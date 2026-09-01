"""Rule-based technical analysis: builds indicator features and a directional signal."""
import numpy as np
import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, MACD, SMAIndicator
from ta.volatility import BollingerBands


# Candles are 15-minute bars (4 per hour). Indicator windows below are scaled
# x4 from their "classic" hourly-chart values so each one still spans the same
# real-world duration (e.g. RSI still looks back ~14 hours, just measured in
# 56 fifteen-minute candles instead of 14 hourly ones).
PERIODS_PER_HOUR = 4


def add_indicator_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Given an OHLCV DataFrame (oldest -> newest), append indicator columns."""
    out = df.copy()
    close = out["close"]
    p = PERIODS_PER_HOUR

    out["rsi14"] = RSIIndicator(close, window=14 * p).rsi()

    macd = MACD(close, window_slow=26 * p, window_fast=12 * p, window_sign=9 * p)
    out["macd"] = macd.macd()
    out["macd_signal"] = macd.macd_signal()
    out["macd_hist"] = macd.macd_diff()

    out["ema9"] = EMAIndicator(close, window=9 * p).ema_indicator()
    out["ema21"] = EMAIndicator(close, window=21 * p).ema_indicator()
    out["sma50"] = SMAIndicator(close, window=50 * p).sma_indicator()

    bb = BollingerBands(close, window=20 * p, window_dev=2)
    out["bb_high"] = bb.bollinger_hband()
    out["bb_low"] = bb.bollinger_lband()
    out["bb_pct"] = bb.bollinger_pband()  # 0 = at lower band, 1 = at upper band

    out["volume_ma20"] = out["volume"].rolling(20 * p).mean()
    out["volume_ratio"] = out["volume"] / out["volume_ma20"]

    # Fraction of each candle's volume that was aggressive (taker) buying,
    # not just derived from price shape like the indicators above -- 0.5 is
    # balanced, >0.5 means buyers were hitting the ask more than sellers hit the bid.
    out["taker_buy_ratio"] = out["taker_buy_base"] / out["volume"]

    # Column names denote the real-world duration they cover, not the raw
    # candle count -- return_1h is still "return over the last hour".
    out["return_1h"] = close.pct_change(1 * p)
    out["return_6h"] = close.pct_change(6 * p)
    out["return_24h"] = close.pct_change(24 * p)

    return out


def recent_volatility(df: pd.DataFrame, window: int = 8) -> float:
    """Rolling std-dev of single-candle (15-min) returns, as a fraction (e.g.
    0.004 = 0.4%). Used to translate a directional score into a plausible
    magnitude for the next 15-min move. window=8 candles = ~2 hours."""
    vol = df["close"].pct_change().rolling(window).std().iloc[-1]
    return float(vol) if pd.notna(vol) else 0.0


def estimate_pct_change(score: float, volatility: float) -> float:
    """Converts a -1..1 directional score into an expected percentage price
    change, scaled by how volatile the asset has actually been recently (so
    a max-confidence signal implies roughly a one-std-dev move, not a fixed
    arbitrary percentage)."""
    return float(score) * volatility


def add_cross_asset_correlation(xrp: pd.DataFrame, other: pd.DataFrame, label: str, window: int = 96) -> pd.DataFrame:
    """Append rolling return-correlation between XRP and another asset (e.g. BTC, ETH),
    plus that asset's own latest 1h return (used as a market-confirmation signal)."""
    out = xrp.copy()
    xrp_ret = out["close"].pct_change()
    other_ret = other["close"].pct_change().reindex(out.index)
    out[f"corr_{label}"] = xrp_ret.rolling(window).corr(other_ret)
    out[f"{label}_return_1h"] = other_ret
    out[f"lead_lag_{label}"] = _score_lead_lag(out["close"], other["close"])
    return out


def compute_lead_lag(xrp_close: pd.Series, other_close: pd.Series, max_lag: int = 8, window: int = 192) -> tuple[int, float]:
    """Finds which lag (1..max_lag candles, i.e. 15min-2h) of `other`'s returns
    best correlates with XRP's concurrent returns over the trailing `window`
    candles. Returns (best_lag, correlation_at_that_lag); (0, 0.0) if nothing
    beats a same-time comparison or there isn't enough data."""
    xrp_ret = xrp_close.pct_change().tail(window)
    other_ret = other_close.pct_change()

    best_lag, best_corr = 0, 0.0
    for lag in range(1, max_lag + 1):
        shifted = other_ret.shift(lag).reindex(xrp_ret.index)
        if shifted.notna().sum() < window // 2:
            continue
        corr = xrp_ret.corr(shifted)
        if pd.notna(corr) and abs(corr) > abs(best_corr):
            best_lag, best_corr = lag, float(corr)
    return best_lag, best_corr


def _score_lead_lag(xrp_close: pd.Series, other_close: pd.Series) -> float:
    """If `other` historically leads XRP by some lag, use the portion of its
    move within that lag window which XRP hasn't caught up to yet as a
    forward-looking signal (e.g. BTC leads XRP by 2 candles and just moved
    +1% over the last 2 candles -> XRP is expected to catch up)."""
    lag, corr = compute_lead_lag(xrp_close, other_close)
    if lag == 0 or abs(corr) < 0.15:
        return 0.0
    catch_up_move = other_close.pct_change(lag).iloc[-1]
    if pd.isna(catch_up_move):
        return 0.0
    sign = 1.0 if corr > 0 else -1.0
    return float(np.clip(sign * catch_up_move * 30, -1, 1))


def _score_rsi(rsi: float) -> float:
    if pd.isna(rsi):
        return 0.0
    if rsi <= 30:
        return 1.0
    if rsi >= 70:
        return -1.0
    return (50 - rsi) / 20.0  # smooth transition, centered at 50


def _score_macd(macd_hist: float) -> float:
    if pd.isna(macd_hist):
        return 0.0
    return float(np.clip(macd_hist * 50, -1, 1))


def _score_ema_cross(ema9: float, ema21: float) -> float:
    if pd.isna(ema9) or pd.isna(ema21) or ema21 == 0:
        return 0.0
    diff_pct = (ema9 - ema21) / ema21
    return float(np.clip(diff_pct * 40, -1, 1))


def _score_bollinger(bb_pct: float) -> float:
    if pd.isna(bb_pct):
        return 0.0
    return float(np.clip((0.5 - bb_pct) * 2, -1, 1))


def _score_volume_confirmation(volume_ratio: float, return_1h: float) -> float:
    if pd.isna(volume_ratio) or pd.isna(return_1h):
        return 0.0
    if volume_ratio <= 1.0:
        return 0.0
    direction = 1.0 if return_1h > 0 else -1.0
    strength = min((volume_ratio - 1.0), 1.0)
    return direction * strength


def _score_market_correlation(corr_btc: float, btc_return_1h: float, corr_eth: float, eth_return_1h: float) -> float:
    """If XRP historically co-moves with BTC/ETH (positive correlation) and that
    asset just moved, treat it as a confirming signal in the same direction
    (a negative correlation flips the expected direction instead)."""
    signals = []
    for corr, ret in ((corr_btc, btc_return_1h), (corr_eth, eth_return_1h)):
        if pd.isna(corr) or pd.isna(ret):
            continue
        signals.append(float(np.clip(corr * ret * 50, -1, 1)))
    if not signals:
        return 0.0
    return sum(signals) / len(signals)


def _score_lead_lag_combined(lead_lag_btc: float, lead_lag_eth: float) -> float:
    values = [v for v in (lead_lag_btc, lead_lag_eth) if pd.notna(v)]
    if not values:
        return 0.0
    return float(np.clip(sum(values) / len(values), -1, 1))


def _score_taker_buy_ratio(ratio: float) -> float:
    if pd.isna(ratio):
        return 0.0
    return float(np.clip((ratio - 0.5) * 4, -1, 1))


WEIGHTS = {
    "rsi": 0.18,
    "macd": 0.18,
    "ema_cross": 0.14,
    "bollinger": 0.11,
    "volume": 0.11,
    "market": 0.09,
    "lead_lag": 0.09,
    "taker_buy": 0.10,
}


def technical_signal(df_with_indicators: pd.DataFrame) -> dict:
    """Combine the latest indicator values into a single directional signal.

    Returns {"direction": "UP"/"DOWN", "confidence": 0..1, "score": -1..1}.
    """
    last = df_with_indicators.iloc[-1]

    scores = {
        "rsi": _score_rsi(last.get("rsi14")),
        "macd": _score_macd(last.get("macd_hist")),
        "ema_cross": _score_ema_cross(last.get("ema9"), last.get("ema21")),
        "bollinger": _score_bollinger(last.get("bb_pct")),
        "volume": _score_volume_confirmation(last.get("volume_ratio"), last.get("return_1h")),
        "market": _score_market_correlation(
            last.get("corr_btc"), last.get("btc_return_1h"),
            last.get("corr_eth"), last.get("eth_return_1h"),
        ),
        "lead_lag": _score_lead_lag_combined(last.get("lead_lag_btc"), last.get("lead_lag_eth")),
        "taker_buy": _score_taker_buy_ratio(last.get("taker_buy_ratio")),
    }
    combined = sum(scores[k] * WEIGHTS[k] for k in WEIGHTS)
    combined = float(np.clip(combined, -1, 1))

    direction = "UP" if combined >= 0 else "DOWN"
    confidence = min(abs(combined), 1.0)
    return {"direction": direction, "confidence": confidence, "score": combined, "components": scores}

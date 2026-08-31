"""Rule-based technical analysis: builds indicator features and a directional signal."""
import numpy as np
import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, MACD, SMAIndicator
from ta.volatility import BollingerBands


def add_indicator_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Given an OHLCV DataFrame (oldest -> newest), append indicator columns."""
    out = df.copy()
    close = out["close"]

    out["rsi14"] = RSIIndicator(close, window=14).rsi()

    macd = MACD(close)
    out["macd"] = macd.macd()
    out["macd_signal"] = macd.macd_signal()
    out["macd_hist"] = macd.macd_diff()

    out["ema9"] = EMAIndicator(close, window=9).ema_indicator()
    out["ema21"] = EMAIndicator(close, window=21).ema_indicator()
    out["sma50"] = SMAIndicator(close, window=50).sma_indicator()

    bb = BollingerBands(close, window=20, window_dev=2)
    out["bb_high"] = bb.bollinger_hband()
    out["bb_low"] = bb.bollinger_lband()
    out["bb_pct"] = bb.bollinger_pband()  # 0 = at lower band, 1 = at upper band

    out["volume_ma20"] = out["volume"].rolling(20).mean()
    out["volume_ratio"] = out["volume"] / out["volume_ma20"]

    out["return_1h"] = close.pct_change(1)
    out["return_6h"] = close.pct_change(6)
    out["return_24h"] = close.pct_change(24)

    return out


def add_cross_asset_correlation(xrp: pd.DataFrame, other: pd.DataFrame, label: str, window: int = 24) -> pd.DataFrame:
    """Append rolling return-correlation between XRP and another asset (e.g. BTC, ETH),
    plus that asset's own latest 1h return (used as a market-confirmation signal)."""
    out = xrp.copy()
    xrp_ret = out["close"].pct_change()
    other_ret = other["close"].pct_change().reindex(out.index)
    out[f"corr_{label}"] = xrp_ret.rolling(window).corr(other_ret)
    out[f"{label}_return_1h"] = other_ret
    return out


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


WEIGHTS = {
    "rsi": 0.22,
    "macd": 0.22,
    "ema_cross": 0.18,
    "bollinger": 0.13,
    "volume": 0.13,
    "market": 0.12,
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
    }
    combined = sum(scores[k] * WEIGHTS[k] for k in WEIGHTS)
    combined = float(np.clip(combined, -1, 1))

    direction = "UP" if combined >= 0 else "DOWN"
    confidence = min(abs(combined), 1.0)
    return {"direction": direction, "confidence": confidence, "score": combined, "components": scores}

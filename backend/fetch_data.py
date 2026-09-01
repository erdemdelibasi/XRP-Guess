"""Binance public market data helpers. No API key required (public endpoints only)."""
import pandas as pd
import requests

# data-api.binance.vision is Binance's public, unauthenticated market-data
# mirror -- it serves the same read-only endpoints as api.binance.com but
# without the regional access blocks that can affect cloud CI IP ranges
# (e.g. GitHub Actions runners) on the main domain.
BASE_URL = "https://data-api.binance.vision"
KLINE_COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_asset_volume", "num_trades",
    "taker_buy_base", "taker_buy_quote", "ignore",
]
NUMERIC_COLUMNS = ["open", "high", "low", "close", "volume", "taker_buy_base"]


def get_klines(symbol: str, interval: str = "15m", limit: int = 500, end_time_ms: int | None = None) -> pd.DataFrame:
    """Fetch OHLCV candles for a symbol. Returns a DataFrame sorted oldest -> newest."""
    params = {"symbol": symbol, "interval": interval, "limit": min(limit, 1000)}
    if end_time_ms is not None:
        params["endTime"] = end_time_ms
    resp = requests.get(f"{BASE_URL}/api/v3/klines", params=params, timeout=15)
    resp.raise_for_status()
    df = pd.DataFrame(resp.json(), columns=KLINE_COLUMNS)
    if df.empty:
        return df.assign(**{c: [] for c in ["open_time", "close_time", "open", "high", "low", "close", "volume", "taker_buy_base"]})[
            ["open_time", "close_time", "open", "high", "low", "close", "volume", "taker_buy_base"]
        ]
    df[NUMERIC_COLUMNS] = df[NUMERIC_COLUMNS].astype(float)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms", utc=True)
    return df[["open_time", "close_time", "open", "high", "low", "close", "volume", "taker_buy_base"]]


def get_klines_history(symbol: str, interval: str = "15m", total: int = 4000) -> pd.DataFrame:
    """Fetches more candles than a single request allows (Binance caps at 1000
    per call) by paging backwards in time. Used for ML training, where a
    richer history improves the model."""
    frames = []
    end_time_ms = None
    remaining = total
    while remaining > 0:
        batch = get_klines(symbol, interval=interval, limit=min(remaining, 1000), end_time_ms=end_time_ms)
        if batch.empty:
            break
        frames.append(batch)
        end_time_ms = int(batch["open_time"].iloc[0].timestamp() * 1000) - 1
        remaining -= len(batch)
        if len(batch) < min(remaining + len(batch), 1000):
            break
    if not frames:
        return pd.DataFrame(columns=["open_time", "close_time", "open", "high", "low", "close", "volume", "taker_buy_base"])
    return (
        pd.concat(frames)
        .drop_duplicates(subset="open_time")
        .sort_values("open_time")
        .reset_index(drop=True)
    )


def get_current_price(symbol: str) -> float:
    resp = requests.get(
        f"{BASE_URL}/api/v3/ticker/price",
        params={"symbol": symbol},
        timeout=15,
    )
    resp.raise_for_status()
    return float(resp.json()["price"])


def get_order_book_imbalance(symbol: str, limit: int = 100) -> float:
    """Point-in-time snapshot only -- Binance doesn't offer a free historical
    order-book archive, so this can only ever be used live (not backtested).
    Returns (bid_volume - ask_volume) / (bid_volume + ask_volume) across the
    top `limit` price levels; >0 means more buy-side depth."""
    resp = requests.get(
        f"{BASE_URL}/api/v3/depth",
        params={"symbol": symbol, "limit": limit},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    bid_volume = sum(float(qty) for _, qty in data["bids"])
    ask_volume = sum(float(qty) for _, qty in data["asks"])
    total = bid_volume + ask_volume
    if total == 0:
        return 0.0
    return (bid_volume - ask_volume) / total

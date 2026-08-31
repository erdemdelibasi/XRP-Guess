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
NUMERIC_COLUMNS = ["open", "high", "low", "close", "volume"]


def get_klines(symbol: str, interval: str = "1h", limit: int = 500) -> pd.DataFrame:
    """Fetch OHLCV candles for a symbol. Returns a DataFrame sorted oldest -> newest."""
    resp = requests.get(
        f"{BASE_URL}/api/v3/klines",
        params={"symbol": symbol, "interval": interval, "limit": limit},
        timeout=15,
    )
    resp.raise_for_status()
    df = pd.DataFrame(resp.json(), columns=KLINE_COLUMNS)
    df[NUMERIC_COLUMNS] = df[NUMERIC_COLUMNS].astype(float)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms", utc=True)
    return df[["open_time", "close_time", "open", "high", "low", "close", "volume"]]


def get_current_price(symbol: str) -> float:
    resp = requests.get(
        f"{BASE_URL}/api/v3/ticker/price",
        params={"symbol": symbol},
        timeout=15,
    )
    resp.raise_for_status()
    return float(resp.json()["price"])

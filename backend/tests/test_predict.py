"""Tests for predict.price_at_target() -- the fix for the 2026-09-03 late-
resolution bug (see CLAUDE.md): a prediction must be scored against the
closed 1-minute candle at its target time, never whatever the live price
happens to be when the resolver runs, and a Binance hiccup must still
resolve the row via the fallback rather than leaving it stuck."""
from datetime import datetime, timezone

import pandas as pd
import predict

TARGET = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)


def test_uses_the_closed_candles_close_price(monkeypatch):
    def fake_get_klines(symbol, interval, limit, end_time_ms=None):
        return pd.DataFrame({"close": [1.2345]})

    monkeypatch.setattr(predict, "get_klines", fake_get_klines)

    assert predict.price_at_target(TARGET, fallback=9.99) == 1.2345


def test_falls_back_to_live_price_when_no_candle_is_returned(monkeypatch):
    def fake_get_klines(symbol, interval, limit, end_time_ms=None):
        return pd.DataFrame({"close": []})  # no 1m candle at that exact time

    monkeypatch.setattr(predict, "get_klines", fake_get_klines)

    assert predict.price_at_target(TARGET, fallback=9.99) == 9.99


def test_falls_back_to_live_price_when_the_fetch_raises(monkeypatch):
    def fake_get_klines(symbol, interval, limit, end_time_ms=None):
        raise ConnectionError("boom")

    monkeypatch.setattr(predict, "get_klines", fake_get_klines)

    assert predict.price_at_target(TARGET, fallback=9.99) == 9.99

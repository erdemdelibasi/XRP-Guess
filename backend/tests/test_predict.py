"""Tests for predict.price_at_target() -- the fix for the 2026-09-03 late-
resolution bug (see CLAUDE.md): a prediction must be scored against the
closed 1-minute candle at its target time, never whatever the live price
happens to be when the resolver runs, and a Binance hiccup must still
resolve the row via the fallback rather than leaving it stuck."""
from datetime import datetime, timedelta, timezone

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


# --- next_quarter_hour: the minimum-horizon guard -------------------------
# Live runs had collapsed to a 5.6-minute median horizon because GitHub
# Actions delays the cron; these pin the guard that stops a "15-minute
# prediction" from being aimed 40 seconds out. See next_quarter_hour().

def test_punctual_run_is_untouched_by_the_guard():
    """The cron fires at :01/:16/:31/:46, ~14 min out -- the guard must be a
    no-op there, or it would silently double every horizon by design."""
    assert predict.next_quarter_hour(datetime(2026, 9, 4, 12, 1, tzinfo=timezone.utc)) == \
        datetime(2026, 9, 4, 12, 15, tzinfo=timezone.utc)
    assert predict.next_quarter_hour(datetime(2026, 9, 4, 12, 46, tzinfo=timezone.utc)) == \
        datetime(2026, 9, 4, 13, 0, tzinfo=timezone.utc)


def test_late_run_skips_a_mark_instead_of_aiming_seconds_ahead():
    """A run landing at :14:51 (this happened live, id=611) used to target
    :15 -- a 9-second horizon scored as a 15-minute forecast."""
    late = datetime(2026, 9, 4, 12, 14, 51, tzinfo=timezone.utc)
    assert predict.next_quarter_hour(late) == datetime(2026, 9, 4, 12, 30, tzinfo=timezone.utc)


def test_horizon_is_never_below_the_minimum():
    """Sweep every second of an hour: no run may ever get a shorter horizon
    than MIN_HORIZON_MINUTES, and none may be pushed past two marks."""
    base = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)
    for offset in range(0, 3600, 7):
        now = base + timedelta(seconds=offset)
        target = predict.next_quarter_hour(now)
        horizon = (target - now).total_seconds() / 60
        assert horizon >= predict.MIN_HORIZON_MINUTES, (now, horizon)
        assert horizon < predict.MIN_HORIZON_MINUTES + 15, (now, horizon)
        assert target.minute % 15 == 0 and target.second == 0


def test_guard_is_configurable_and_off_at_zero():
    """min_horizon_minutes=0 must reproduce the old strict-upward rounding,
    so the guard can be replayed/disabled without touching the rounding."""
    now = datetime(2026, 9, 4, 12, 14, 51, tzinfo=timezone.utc)
    assert predict.next_quarter_hour(now, min_horizon_minutes=0) == \
        datetime(2026, 9, 4, 12, 15, tzinfo=timezone.utc)

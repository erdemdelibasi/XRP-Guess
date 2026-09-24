"""Tests for daily_report.find_run_gaps() -- the outage alarm added after the
2026-09-19..21 GitHub Actions account lock went unreported: 32 hours with no
predictions, portfolio decisions or stop-loss checks, and the report that
would have covered it was blocked by the same lock."""
from datetime import datetime, timedelta, timezone

import daily_report

WINDOW_START = datetime(2026, 9, 20, 15, 0, tzinfo=timezone.utc)  # 18:00 TRT
WINDOW_END = WINDOW_START + timedelta(days=1)


def every(minutes, start, end):
    t, out = start, []
    while t <= end:
        out.append(t)
        t += timedelta(minutes=minutes)
    return out


def test_regular_quarter_hourly_runs_have_no_gaps():
    runs = every(15, WINDOW_START - timedelta(minutes=14), WINDOW_END - timedelta(minutes=14))
    assert daily_report.find_run_gaps(runs, WINDOW_END) == []


def test_a_skipped_run_plus_cron_delay_is_not_an_outage():
    # Measured live: one skipped run + GitHub's cron jitter tops out ~40-49 min.
    runs = [WINDOW_START + timedelta(minutes=m) for m in (1, 16, 65, 80)]
    assert daily_report.find_run_gaps(runs, WINDOW_START + timedelta(minutes=90)) == []


def test_gap_that_began_before_the_window_keeps_its_real_start():
    # The real 2026-09-19..21 outage: last run before the lock, first run after.
    last_before = datetime(2026, 9, 19, 22, 37, tzinfo=timezone.utc)
    resumed = datetime(2026, 9, 21, 7, 30, tzinfo=timezone.utc)
    runs = [last_before, *every(15, resumed, WINDOW_END + timedelta(hours=1))]
    window_end = WINDOW_END + timedelta(hours=1)

    assert daily_report.find_run_gaps(runs, window_end) == [(last_before, resumed)]


def test_runs_that_stopped_and_never_resumed_show_as_a_trailing_gap():
    last = WINDOW_END - timedelta(hours=3)
    runs = every(15, WINDOW_START, last)
    assert daily_report.find_run_gaps(runs, WINDOW_END) == [(last, WINDOW_END)]


def test_no_runs_at_all_is_not_a_crash():
    assert daily_report.find_run_gaps([], WINDOW_END) == []

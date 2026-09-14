"""Supabase client helper. Reads credentials from environment variables only —
never hardcode keys here."""
import os

from postgrest.base_request_builder import MAX_RETRIES, RequestConfig
from supabase import Client, create_client

# Transient-error retry patch -- measured 2026-09-11..09-13: 4 of 973
# quarter-hourly predict.py runs (weekend cron) died on a single Supabase-side
# 504 Gateway Timeout (2 on resolve_due_predictions' read, 2 on its per-row
# `.update(...).eq("id", ...)`) -- see gh run 34771472359/34693805430/
# 34672262672/34669606345. Nothing was corrupted (resolve_due_predictions
# commits row-by-row and the idempotency guard in predict.main() makes a
# re-run of the same quarter-hour a no-op), but each crash turned a 15-minute
# prediction cadence into a 30-minute gap -- exactly the horizon drift
# next_quarter_hour()'s own docstring measures the cost of, and it feeds
# retrain.py's rolling accuracy the same way a missed run always does.
#
# postgrest-py already retries with exponential backoff (send_with_retry /
# get_retry_delay, capped at MAX_RETRIES=3 attempts) but its default
# should_retry() only covers GET/HEAD hitting Cloudflare's 503/520 -- a
# Supabase-side 504 on GET, or anything on PATCH/POST, always falls straight
# through to the caller. We widen should_retry() rather than reimplementing
# the retry loop, so we keep postgrest's own backoff/attempt-count bookkeeping
# as the single source of truth.
#
# Extended to PATCH: every PATCH in this codebase is a point update keyed by
# `.eq("id", ...)` (predictions resolution, trading.py, etc.), so replaying it
# on a lost response is a no-op, not a duplicate.
# Extended to POST *only* when it carries Prefer: resolution=merge-duplicates
# (an upsert -- e.g. retrain.py's model_state write), which is idempotent by
# construction.
# Deliberately NOT extended to plain POST (insert) or DELETE: this project has
# no general dedup guard on trade/prediction inserts beyond the
# (symbol, target_time) uniqueness on `predictions` (see CLAUDE.md's
# idempotency notes and the live duplicate-row incident it describes) --
# blindly retrying an insert whose response merely got lost risks writing a
# real duplicate trade, which is worse than the crash it would fix.
_RETRYABLE_STATUS_CODES = {502, 503, 504, 520}


def _should_retry_transient_gateway_error(self, response, attempt_count: int) -> bool:
    if not self.retry_enabled or attempt_count >= MAX_RETRIES:
        return False
    if response.status_code not in _RETRYABLE_STATUS_CODES:
        return False
    if self.http_method in ("GET", "HEAD", "PATCH"):
        return True
    if self.http_method == "POST":
        return "resolution=merge-duplicates" in self.headers.get("Prefer", "")
    return False


RequestConfig.should_retry = _should_retry_transient_gateway_error


def get_client() -> Client:
    url = os.environ["SUPABASE_URL"]
    # predict.py / retrain.py need INSERT/UPDATE rights, so they must run
    # with the service_role key (kept in GitHub Secrets, never in the frontend).
    key = os.environ["SUPABASE_SERVICE_KEY"]
    return create_client(url, key)

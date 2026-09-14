"""Tests for db._should_retry_transient_gateway_error() -- the fix for the
2026-09-11..09-13 weekend failures (see CLAUDE.md / db.py): four
quarter-hourly predict.py runs died on a single Supabase 504 Gateway Timeout
that postgrest-py's own retry loop doesn't cover by default. These tests
pin the exact policy: retry GET/HEAD/PATCH and upsert-flavored POST on a
transient gateway status, but never a plain insert or a delete -- retrying
those risks writing a real duplicate row instead of just fixing a crash."""
import db


class FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


class FakeRequestConfig:
    """Minimal stand-in with just the attributes should_retry() reads."""

    def __init__(self, http_method, headers=None, retry_enabled=True):
        self.http_method = http_method
        self.headers = headers or {}
        self.retry_enabled = retry_enabled

    should_retry = db._should_retry_transient_gateway_error


def test_retries_get_on_a_transient_gateway_timeout():
    req = FakeRequestConfig("GET")
    assert req.should_retry(FakeResponse(504), attempt_count=0) is True


def test_retries_patch_on_a_transient_gateway_timeout():
    req = FakeRequestConfig("PATCH")
    assert req.should_retry(FakeResponse(504), attempt_count=0) is True


def test_retries_upsert_post_on_a_transient_gateway_timeout():
    req = FakeRequestConfig("POST", headers={"Prefer": "return=representation,resolution=merge-duplicates"})
    assert req.should_retry(FakeResponse(504), attempt_count=0) is True


def test_never_retries_a_plain_insert_post():
    req = FakeRequestConfig("POST", headers={"Prefer": "return=representation"})
    assert req.should_retry(FakeResponse(504), attempt_count=0) is False


def test_never_retries_delete():
    req = FakeRequestConfig("DELETE")
    assert req.should_retry(FakeResponse(504), attempt_count=0) is False


def test_does_not_retry_a_genuine_client_error():
    req = FakeRequestConfig("GET")
    assert req.should_retry(FakeResponse(404), attempt_count=0) is False


def test_stops_once_max_retries_reached():
    req = FakeRequestConfig("GET")
    assert req.should_retry(FakeResponse(504), attempt_count=db.MAX_RETRIES) is False


def test_respects_retry_enabled_false():
    req = FakeRequestConfig("GET", retry_enabled=False)
    assert req.should_retry(FakeResponse(504), attempt_count=0) is False

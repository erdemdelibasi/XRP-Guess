"""Tests for apply_pending_mentions()'s bookkeeping -- the trade-application
half of Kanal Finans TS that stayed in this project after 2026-09-08's split.

The YouTube-facing half (RSS, transcripts, retry backoff, the Claude call) no
longer lives here -- it moved to ../Kanal-Finans-Fetcher, a sibling repo also
serving XAU-Guess (see kanal_finans.py's module docstring for the full why).
Its own tests moved with it. decide_on_mention()/check_stop_loss() themselves
are covered in test_kanal_finans_trading.py, unaffected by this split.
"""
import kanal_finans


class _FakeTable:
    def __init__(self, name, store):
        self.name = name
        self.store = store
        self._filter_null = None
        self._eq = None

    def select(self, _cols):
        return self

    def is_(self, column, value):
        assert value == "null"
        self._filter_null = column
        return self

    def order(self, _col):
        return self

    def update(self, patch):
        self._patch = patch
        return self

    def eq(self, column, value):
        self._eq = (column, value)
        return self

    def execute(self):
        if self._filter_null:
            rows = [r for r in self.store if r.get(self._filter_null) is None]
            return type("R", (), {"data": rows})()
        if self._eq:
            column, value = self._eq
            for row in self.store:
                if row[column] == value:
                    row.update(self._patch)
            return type("R", (), {"data": None})()
        return type("R", (), {"data": self.store})()


class FakeDb:
    """Mimics just enough of the supabase-py chain for kanal_finans_mentions:
    .select().is_(col, "null").order().execute().data and
    .update(patch).eq(col, val).execute()."""
    def __init__(self, rows):
        self.rows = rows

    def table(self, name):
        assert name == "kanal_finans_mentions"
        return _FakeTable(name, self.rows)


def test_get_pending_mentions_only_returns_unapplied_rows():
    rows = [
        {"id": 1, "asset": "XRP", "applied_at": None},
        {"id": 2, "asset": "BTC", "applied_at": "2026-09-08T00:00:00+00:00"},
    ]
    pending = kanal_finans.get_pending_mentions(FakeDb(rows))
    assert [r["id"] for r in pending] == [1]


def test_apply_pending_mentions_stamps_non_xrp_without_trading(monkeypatch):
    """BTC/ETH/KRIPTO have no portfolio to trade, but must still be marked
    applied -- otherwise they are read as "pending" on every run forever."""
    rows = [{"id": 1, "asset": "BTC", "applied_at": None}]
    db = FakeDb(rows)

    def boom(*_a, **_kw):
        raise AssertionError("non-XRP mention must never reach the trading engine")
    monkeypatch.setattr(kanal_finans.kanal_finans_trading, "apply_mention_decision", boom)

    applied = kanal_finans.apply_pending_mentions(db)
    assert applied == 0
    assert rows[0]["applied_at"] is not None


def test_apply_pending_mentions_trades_xrp(monkeypatch):
    rows = [{"id": 1, "asset": "XRP", "action": "BUY", "applied_at": None}]
    db = FakeDb(rows)
    calls = []

    monkeypatch.setattr(kanal_finans, "get_current_price", lambda symbol: 1.42)
    monkeypatch.setattr(kanal_finans.kanal_finans_trading, "apply_mention_decision",
                         lambda db, mention, price: calls.append((mention["id"], price)))

    applied = kanal_finans.apply_pending_mentions(db)
    assert applied == 1
    assert calls == [(1, 1.42)]
    assert rows[0]["applied_at"] is not None


def test_a_failed_trade_is_not_marked_applied(monkeypatch):
    """The retry contract: leaving applied_at NULL is what makes the next run
    try again. Marking it applied here would silently drop a real mention."""
    rows = [{"id": 1, "asset": "XRP", "action": "BUY", "applied_at": None}]
    db = FakeDb(rows)

    def boom(_symbol):
        raise RuntimeError("Binance hiccup")
    monkeypatch.setattr(kanal_finans, "get_current_price", boom)

    applied = kanal_finans.apply_pending_mentions(db)
    assert applied == 0
    assert rows[0]["applied_at"] is None


def test_only_xrp_is_tradeable():
    assert kanal_finans.TRADEABLE_ASSET == "XRP"

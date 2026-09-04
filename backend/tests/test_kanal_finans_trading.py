"""Tests for kanal_finans_trading.decide_on_mention()/check_stop_loss() --
the Kanal Finans TS portfolio's own binary (no confidence score) decision
engine, deliberately separate from trading.compute_rebalance() (see module
docstring: there's no numeric signal to scale a position by here)."""
import kanal_finans_trading as kf


def test_buy_from_flat_cash_spends_everything_and_stores_new_levels():
    decision = kf.decide_on_mention(
        cash=1000.0, xrp=0.0, position_stop_loss=None, position_resistance=None,
        action="BUY", mention_stop_loss=1.30, mention_resistance=1.45, price=1.40,
    )
    assert decision["trade_action"] == "BUY"
    assert decision["usd_amount"] == 1000.0
    assert decision["new_stop_loss"] == 1.30
    assert decision["new_resistance"] == 1.45


def test_buy_ignored_if_already_long():
    decision = kf.decide_on_mention(
        cash=0.0, xrp=500.0, position_stop_loss=1.30, position_resistance=1.45,
        action="BUY", mention_stop_loss=None, mention_resistance=None, price=1.40,
    )
    assert decision["trade_action"] == "HOLD"


def test_level_carries_forward_when_a_mention_gives_no_new_one():
    # Tunc doesn't repeat the level in every video -- a mention with no new
    # stop/resistance must keep whatever level was already being watched.
    decision = kf.decide_on_mention(
        cash=0.0, xrp=500.0, position_stop_loss=1.25, position_resistance=1.50,
        action="HOLD", mention_stop_loss=None, mention_resistance=None, price=1.40,
    )
    assert decision["new_stop_loss"] == 1.25
    assert decision["new_resistance"] == 1.50


def test_sell_clears_stop_loss_but_keeps_resistance_for_display():
    decision = kf.decide_on_mention(
        cash=0.0, xrp=500.0, position_stop_loss=1.25, position_resistance=1.50,
        action="SELL", mention_stop_loss=None, mention_resistance=None, price=1.40,
    )
    assert decision["trade_action"] == "SELL"
    assert decision["xrp_amount"] == 500.0
    # Flat again -- nothing left to protect until the next BUY sets a fresh
    # stop, but the (informational-only) resistance level is retained.
    assert decision["new_stop_loss"] is None
    assert decision["new_resistance"] == 1.50


def test_stop_loss_triggers_at_or_below_the_watched_level():
    decision = kf.check_stop_loss(xrp=500.0, stop_loss_price=1.30, price=1.30)
    assert decision["trade_action"] == "SELL"
    assert decision["xrp_amount"] == 500.0


def test_stop_loss_does_not_trigger_while_flat():
    decision = kf.check_stop_loss(xrp=0.0, stop_loss_price=1.30, price=1.20)
    assert decision["trade_action"] == "HOLD"


def test_stop_loss_does_not_trigger_above_the_watched_level():
    decision = kf.check_stop_loss(xrp=500.0, stop_loss_price=1.30, price=1.31)
    assert decision["trade_action"] == "HOLD"

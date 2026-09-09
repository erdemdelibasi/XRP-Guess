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


# --- plausibility guard: catches extraction-scale errors -------------------
# Live incident, 2026-09-09: mention id=49, the first one extracted from a
# locally-Whisper-transcribed video (official captions were 429'd that day),
# came back stop_loss_price=136 / resistance_price=143 while XRP traded near
# $1.44 -- almost certainly spoken "1.43" transcribed as the bare number
# "143". Every one of the 10 prior mentions with a level was within ~30% of
# spot. Without a guard, check_stop_loss()'s `price <= stop_loss_price` fires
# on the very next check when the level is in the hundreds and price is ~1 --
# which is exactly what happened live (BUY, then an erroneous SELL 11
# minutes later).

def test_scale_error_stop_loss_is_rejected_not_trusted():
    decision = kf.decide_on_mention(
        cash=1000.0, xrp=0.0, position_stop_loss=None, position_resistance=None,
        action="BUY", mention_stop_loss=136, mention_resistance=143, price=1.4410,
    )
    assert decision["trade_action"] == "BUY"          # the BUY itself is real, only the levels are bad
    assert decision["new_stop_loss"] is None           # implausible -> not trusted, not carried as a level
    assert decision["new_resistance"] is None
    assert decision["rejected_stop_loss"] == 136
    assert decision["rejected_resistance"] == 143


def test_scale_error_stop_loss_falls_back_to_previously_watched_level():
    # An implausible NEW level must not clobber a good level already being watched.
    decision = kf.decide_on_mention(
        cash=0.0, xrp=500.0, position_stop_loss=1.25, position_resistance=1.50,
        action="HOLD", mention_stop_loss=136, mention_resistance=None, price=1.44,
    )
    assert decision["new_stop_loss"] == 1.25
    assert decision["rejected_stop_loss"] == 136


def test_plausible_level_is_still_accepted():
    # The guard must not reject real calls -- every historical level was
    # comfortably inside the band.
    decision = kf.decide_on_mention(
        cash=1000.0, xrp=0.0, position_stop_loss=None, position_resistance=None,
        action="BUY", mention_stop_loss=1.30, mention_resistance=1.45, price=1.40,
    )
    assert decision["new_stop_loss"] == 1.30
    assert decision["new_resistance"] == 1.45
    assert decision["rejected_stop_loss"] is None
    assert decision["rejected_resistance"] is None


def test_no_level_given_is_not_reported_as_rejected():
    decision = kf.decide_on_mention(
        cash=0.0, xrp=500.0, position_stop_loss=1.25, position_resistance=1.50,
        action="HOLD", mention_stop_loss=None, mention_resistance=None, price=1.40,
    )
    assert decision["rejected_stop_loss"] is None
    assert decision["rejected_resistance"] is None

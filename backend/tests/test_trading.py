"""Tests for trading.compute_rebalance() -- the confidence-scaled position
sizing + stop-loss engine shared by live trading (maybe_trade) and
backend/backtest.py's historical replay. Pure function, no DB, so these pin
down the invariants CLAUDE.md's trading section documents as measured
(peak_value never drops, cooldown blocks re-entry, the REBALANCE_THRESHOLD
dead zone, the real confidence floor to open a position) so a future change
can't silently break one without a test failing.
"""
import trading


def test_down_signal_sells_down_to_zero_allocation():
    decision = trading.compute_rebalance(
        cash=0.0, xrp=100.0, price=1.0, direction="DOWN", confidence=0.5,
        peak_value=100.0, cooldown_remaining=0,
    )
    assert decision["action"] == "SELL"
    assert decision["xrp_amount"] == 100.0


def test_no_trade_when_drift_is_within_threshold():
    # Sitting exactly on the confidence-implied target allocation -- zero
    # drift, safely inside REBALANCE_THRESHOLD, so no trade should fire
    # (the whole point of the threshold: avoid fee-eroding micro-rebalances).
    confidence = 0.05
    target_fraction = min(confidence / trading.CONFIDENCE_FOR_MAX_ALLOCATION, 1.0) * trading.MAX_ALLOCATION
    value = 1000.0
    price = 1.0
    xrp_value = value * target_fraction

    decision = trading.compute_rebalance(
        cash=value - xrp_value, xrp=xrp_value / price, price=price, direction="UP",
        confidence=confidence, peak_value=value, cooldown_remaining=0,
    )
    assert decision["action"] == "HOLD"


def test_stop_loss_triggers_below_drawdown_and_starts_cooldown():
    peak_value = 1000.0
    xrp = 1000.0  # fully in XRP, no cash
    drawdown_price = (peak_value * (1 - trading.STOP_LOSS_DRAWDOWN) - 0.01) / xrp

    decision = trading.compute_rebalance(
        cash=0.0, xrp=xrp, price=drawdown_price, direction="UP", confidence=0.5,
        peak_value=peak_value, cooldown_remaining=0,
    )
    assert decision["action"] == "SELL"
    assert decision["xrp_amount"] == xrp
    assert decision["reason"] == "Stop-loss tetiklendi"
    assert decision["new_cooldown_remaining"] == trading.STOP_LOSS_COOLDOWN_CANDLES


def test_peak_value_never_decreases_even_after_a_price_drop():
    # Portfolio value (500) has fallen below the stored peak (1000) -- the
    # high-water mark has to stay at the old high, or the drawdown check
    # that reads it is meaningless (see trading.py module docstring).
    decision = trading.compute_rebalance(
        cash=500.0, xrp=0.0, price=1.0, direction="UP", confidence=0.0,
        peak_value=1000.0, cooldown_remaining=0,
    )
    assert decision["new_peak_value"] == 1000.0


def test_cooldown_blocks_new_entries_regardless_of_signal_and_counts_down():
    decision = trading.compute_rebalance(
        cash=1000.0, xrp=0.0, price=1.0, direction="UP", confidence=0.9,
        peak_value=1000.0, cooldown_remaining=5,
    )
    assert decision["action"] == "HOLD"
    assert decision["new_cooldown_remaining"] == 4


def test_min_confidence_to_open_position_is_the_real_trade_boundary():
    # This is exactly the boundary the 2026-09-03 "technical strategy
    # silently frozen" incident hinged on (see CLAUDE.md): calibrated
    # confidence has to clear this threshold before a flat portfolio will
    # ever open a position, and nothing else in the code says so.
    threshold = trading.min_confidence_to_open_position()

    just_below = trading.compute_rebalance(
        cash=1000.0, xrp=0.0, price=1.0, direction="UP", confidence=threshold - 0.001,
        peak_value=1000.0, cooldown_remaining=0,
    )
    just_above = trading.compute_rebalance(
        cash=1000.0, xrp=0.0, price=1.0, direction="UP", confidence=threshold + 0.001,
        peak_value=1000.0, cooldown_remaining=0,
    )
    assert just_below["action"] == "HOLD"
    assert just_above["action"] == "BUY"

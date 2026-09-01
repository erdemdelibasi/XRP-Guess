"""Virtual paper-trading simulator with confidence-scaled position sizing and
a portfolio-level stop-loss. No real money, no Binance API keys, no real
orders -- purely a log of what this strategy would have done.

Position sizing: instead of going 100% in or 100% out on every signal flip,
the target XRP allocation scales with the ensemble's confidence (capped at
MAX_ALLOCATION so a single signal never fully commits the portfolio). A
trade only fires when the current allocation drifts from the target by more
than REBALANCE_THRESHOLD, to avoid fee erosion from constant tiny rebalances.

Stop-loss: if the portfolio's value falls more than STOP_LOSS_DRAWDOWN below
its running all-time peak, it's force-liquidated to cash regardless of the
current signal. peak_value never resets down after a stop-loss -- a real
high-water mark has to stay a real high-water mark, or the drawdown control
is meaningless. One consequence: if price hasn't recovered, re-entering a
position can trip the same stop-loss again quickly -- that's expected
high-water-mark behavior, not a bug.

compute_rebalance() is pure (no DB access) so backtest.py can replay the
exact same sizing/stop-loss logic offline against historical data.
"""
from datetime import datetime, timezone

FEE_RATE = 0.001
MIN_CONFIDENCE_TO_TRADE = 0.02
STARTING_CASH = 1000.0

MAX_ALLOCATION = 0.85                  # a single signal never commits more than 85% of the portfolio to XRP
CONFIDENCE_FOR_MAX_ALLOCATION = 0.25   # confidence level that maps to MAX_ALLOCATION (typical confidences run ~0.05-0.20)
REBALANCE_THRESHOLD = 0.25             # only trade if actual allocation is off target by more than this fraction of portfolio value
                                        # (backtest.py showed 0.10 causes excessive fee-eroding churn -- confidence
                                        # shifts a few points step to step, which crosses a tight threshold constantly)
STOP_LOSS_DRAWDOWN = 0.15              # force to cash if value falls more than this fraction below its running peak


def get_portfolio_state(db) -> dict:
    res = db.table("portfolio_state").select("*").eq("id", 1).single().execute()
    return res.data


def _record_trade(db, side: str, price: float, xrp_amount: float, usd_amount: float,
                   fee_usd: float, cash_after: float, xrp_after: float,
                   prediction_id: int | None, reason: str) -> None:
    db.table("trades").insert({
        "side": side,
        "price": price,
        "xrp_amount": xrp_amount,
        "usd_amount": usd_amount,
        "fee_usd": fee_usd,
        "cash_after": cash_after,
        "xrp_after": xrp_after,
        "triggered_by_prediction_id": prediction_id,
        "reason": reason,
    }).execute()


def _target_allocation(direction: str, confidence: float) -> float:
    """Fraction of total portfolio value that should be in XRP. Long-only --
    a DOWN signal targets 0 (get out of the way), never a short position."""
    if direction == "DOWN":
        return 0.0
    return min(confidence / CONFIDENCE_FOR_MAX_ALLOCATION, 1.0) * MAX_ALLOCATION


def compute_rebalance(cash: float, xrp: float, price: float, direction: str,
                       confidence: float, peak_value: float) -> dict:
    """Pure function, no DB access -- shared by maybe_trade() (live) and
    backtest.py (historical replay).

    Returns {"action": "BUY"/"SELL"/"HOLD", "usd_amount": float,
    "xrp_amount": float, "new_peak_value": float, "reason": str}.
    Only the amount matching `action` is meaningful (usd_amount for BUY,
    xrp_amount for SELL); both are 0 for HOLD.
    """
    value = cash + xrp * price
    peak_value = max(peak_value, value)

    if value <= 0:
        return {"action": "HOLD", "usd_amount": 0.0, "xrp_amount": 0.0, "new_peak_value": peak_value, "reason": ""}

    # Stop-loss takes priority over any signal.
    if xrp > 0 and value <= peak_value * (1 - STOP_LOSS_DRAWDOWN):
        return {
            "action": "SELL", "usd_amount": 0.0, "xrp_amount": xrp,
            "new_peak_value": peak_value, "reason": "Stop-loss tetiklendi",
        }

    if confidence < MIN_CONFIDENCE_TO_TRADE:
        return {"action": "HOLD", "usd_amount": 0.0, "xrp_amount": 0.0, "new_peak_value": peak_value, "reason": ""}

    target_fraction = _target_allocation(direction, confidence)
    target_xrp_value = value * target_fraction
    current_xrp_value = xrp * price
    drift = (current_xrp_value - target_xrp_value) / value

    if drift > REBALANCE_THRESHOLD:
        xrp_to_sell = min(xrp, (current_xrp_value - target_xrp_value) / price)
        return {
            "action": "SELL", "usd_amount": 0.0, "xrp_amount": xrp_to_sell,
            "new_peak_value": peak_value, "reason": "Hedef pozisyona rebalance (azalt)",
        }
    if drift < -REBALANCE_THRESHOLD:
        usd_to_spend = min(cash, target_xrp_value - current_xrp_value)
        if usd_to_spend <= 0:
            return {"action": "HOLD", "usd_amount": 0.0, "xrp_amount": 0.0, "new_peak_value": peak_value, "reason": ""}
        return {
            "action": "BUY", "usd_amount": usd_to_spend, "xrp_amount": 0.0,
            "new_peak_value": peak_value, "reason": "Hedef pozisyona rebalance (artir)",
        }

    return {"action": "HOLD", "usd_amount": 0.0, "xrp_amount": 0.0, "new_peak_value": peak_value, "reason": ""}


def maybe_trade(db, prediction_id: int | None, direction: str, confidence: float, price: float) -> None:
    """Fetches live portfolio state, decides via compute_rebalance(), and
    applies the result (DB writes + trade log)."""
    state = get_portfolio_state(db)
    cash, xrp = float(state["cash_usd"]), float(state["xrp_amount"])
    peak_value = float(state.get("peak_value") or STARTING_CASH)

    decision = compute_rebalance(cash, xrp, price, direction, confidence, peak_value)
    now_iso = datetime.now(timezone.utc).isoformat()

    if decision["action"] == "HOLD":
        if decision["new_peak_value"] != peak_value:
            db.table("portfolio_state").update({
                "peak_value": decision["new_peak_value"], "updated_at": now_iso,
            }).eq("id", 1).execute()
        return

    if decision["action"] == "BUY":
        gross_usd = decision["usd_amount"]
        fee_usd = gross_usd * FEE_RATE
        xrp_bought = (gross_usd - fee_usd) / price
        new_cash = cash - gross_usd
        new_xrp = xrp + xrp_bought

        db.table("portfolio_state").update({
            "cash_usd": new_cash, "xrp_amount": new_xrp, "position": "LONG",
            "peak_value": decision["new_peak_value"], "updated_at": now_iso,
        }).eq("id", 1).execute()
        _record_trade(db, "BUY", price, xrp_bought, gross_usd, fee_usd, new_cash, new_xrp,
                       prediction_id, decision["reason"])
        print(f"TRADE: BUY {xrp_bought:.4f} XRP @ {price:.4f} (fee ${fee_usd:.2f}) -- {decision['reason']}")

    elif decision["action"] == "SELL":
        xrp_to_sell = decision["xrp_amount"]
        gross_usd = xrp_to_sell * price
        fee_usd = gross_usd * FEE_RATE
        new_cash = cash + (gross_usd - fee_usd)
        new_xrp = xrp - xrp_to_sell

        db.table("portfolio_state").update({
            "cash_usd": new_cash, "xrp_amount": new_xrp,
            "position": "LONG" if new_xrp > 0 else "CASH",
            "peak_value": decision["new_peak_value"], "updated_at": now_iso,
        }).eq("id", 1).execute()
        _record_trade(db, "SELL", price, xrp_to_sell, gross_usd, fee_usd, new_cash, new_xrp,
                       prediction_id, decision["reason"])
        print(f"TRADE: SELL {xrp_to_sell:.4f} XRP @ {price:.4f} (fee ${fee_usd:.2f}) -- {decision['reason']}")

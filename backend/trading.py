"""Virtual $1000 paper-trading simulator. No real money, no Binance API keys,
no real orders -- purely a log of what a simple strategy would have done.

Strategy: go fully long (spend all cash on XRP) when the ensemble prediction
flips to UP, go fully to cash (sell all XRP) when it flips to DOWN. Signals
below MIN_CONFIDENCE_TO_TRADE are treated as noise and ignored, and staying
on the same side as the current position never triggers a trade -- otherwise
fees would erode the portfolio every 15 minutes regardless of accuracy.
"""
from datetime import datetime, timezone

# Binance's standard (non-VIP, non-BNB-discounted) spot trading fee is 0.10%
# per order, as of the 2026 published fee schedule.
FEE_RATE = 0.001
MIN_CONFIDENCE_TO_TRADE = 0.02
STARTING_CASH = 1000.0


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


def maybe_trade(db, prediction_id: int | None, direction: str, confidence: float, price: float) -> None:
    """Executes a simulated BUY/SELL if the prediction direction just flipped
    with enough conviction. No-op otherwise."""
    if confidence < MIN_CONFIDENCE_TO_TRADE:
        return

    state = get_portfolio_state(db)
    now_iso = datetime.now(timezone.utc).isoformat()

    if direction == "UP" and state["position"] == "CASH" and state["cash_usd"] > 0:
        gross_usd = float(state["cash_usd"])
        fee_usd = gross_usd * FEE_RATE
        xrp_bought = (gross_usd - fee_usd) / price
        new_cash = 0.0
        new_xrp = float(state["xrp_amount"]) + xrp_bought

        db.table("portfolio_state").update({
            "cash_usd": new_cash, "xrp_amount": new_xrp, "position": "LONG", "updated_at": now_iso,
        }).eq("id", 1).execute()
        _record_trade(db, "BUY", price, xrp_bought, gross_usd, fee_usd, new_cash, new_xrp,
                       prediction_id, "Ensemble tahmini Artis'a dondu")
        print(f"TRADE: BUY {xrp_bought:.4f} XRP @ {price:.4f} (fee ${fee_usd:.2f})")

    elif direction == "DOWN" and state["position"] == "LONG" and state["xrp_amount"] > 0:
        xrp_to_sell = float(state["xrp_amount"])
        gross_usd = xrp_to_sell * price
        fee_usd = gross_usd * FEE_RATE
        new_cash = float(state["cash_usd"]) + (gross_usd - fee_usd)
        new_xrp = 0.0

        db.table("portfolio_state").update({
            "cash_usd": new_cash, "xrp_amount": new_xrp, "position": "CASH", "updated_at": now_iso,
        }).eq("id", 1).execute()
        _record_trade(db, "SELL", price, xrp_to_sell, gross_usd, fee_usd, new_cash, new_xrp,
                       prediction_id, "Ensemble tahmini Azalis'a dondu")
        print(f"TRADE: SELL {xrp_to_sell:.4f} XRP @ {price:.4f} (fee ${fee_usd:.2f})")

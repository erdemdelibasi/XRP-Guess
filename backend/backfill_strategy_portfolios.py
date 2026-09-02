"""One-off (not scheduled): seeds the four single-signal strategy portfolios
(technical/ml/whale/news -- see trading.py's module docstring) with what they
would have done since the very first prediction, instead of starting them
from a blank $1000 today. Run once via the "Backfill Strategy Portfolios"
GitHub Actions workflow (workflow_dispatch), after the strategy_portfolios/
strategy_trades migration has been applied and BEFORE (or shortly after --
this is idempotent) predict.py starts live-trading them.

Replays every predictions row in chronological order, feeding each
strategy's own historical {component}_direction/{component}_confidence and
price_at_prediction through the exact same trading.compute_rebalance() the
live system uses -- so this is "what would have actually happened," not a
separate approximation. Safe to re-run: it always resets from scratch
(deletes that strategy's existing trades, replays the full history again)
rather than incrementally appending, so an accidental double-run just
reproduces the same result instead of duplicating trades.
"""
import sys

from db import get_client
import trading

PAGE_SIZE = 1000
SELECT_COLUMNS = "id,created_at,price_at_prediction,tech_direction,tech_confidence,ml_direction,ml_confidence,whale_direction,whale_confidence,news_direction,news_confidence"


def _fetch_all_predictions(db) -> list[dict]:
    rows = []
    start = 0
    while True:
        page = (
            db.table("predictions")
            .select(SELECT_COLUMNS)
            .order("created_at")
            .range(start, start + PAGE_SIZE - 1)
            .execute()
        )
        rows.extend(page.data)
        if len(page.data) < PAGE_SIZE:
            break
        start += PAGE_SIZE
    return rows


def replay(strategy: str, predictions: list[dict]) -> dict:
    """Pure in-memory replay for one strategy. Returns the final portfolio
    state plus the full list of trades, ready to be written to the DB."""
    cash, xrp, peak_value, cooldown = trading.STARTING_CASH, 0.0, trading.STARTING_CASH, 0
    trades = []

    for row in predictions:
        direction = row.get(f"{strategy}_direction")
        confidence = row.get(f"{strategy}_confidence")
        price = row.get("price_at_prediction")
        if direction is None or confidence is None or price is None:
            continue
        price = float(price)

        decision = trading.compute_rebalance(cash, xrp, price, direction, float(confidence), peak_value, cooldown)
        peak_value = decision["new_peak_value"]
        cooldown = decision["new_cooldown_remaining"]

        if decision["action"] == "BUY":
            gross_usd = decision["usd_amount"]
            fee_usd = gross_usd * trading.FEE_RATE
            xrp += (gross_usd - fee_usd) / price
            cash -= gross_usd
            trades.append({
                "strategy": strategy, "side": "BUY", "price": price,
                "xrp_amount": (gross_usd - fee_usd) / price, "usd_amount": gross_usd, "fee_usd": fee_usd,
                "cash_after": cash, "xrp_after": xrp,
                "triggered_by_prediction_id": row["id"], "reason": decision["reason"],
                "created_at": row["created_at"],
            })
        elif decision["action"] == "SELL":
            xrp_amount = decision["xrp_amount"]
            gross_usd = xrp_amount * price
            fee_usd = gross_usd * trading.FEE_RATE
            cash += gross_usd - fee_usd
            xrp -= xrp_amount
            trades.append({
                "strategy": strategy, "side": "SELL", "price": price,
                "xrp_amount": xrp_amount, "usd_amount": gross_usd, "fee_usd": fee_usd,
                "cash_after": cash, "xrp_after": xrp,
                "triggered_by_prediction_id": row["id"], "reason": decision["reason"],
                "created_at": row["created_at"],
            })

    return {
        "strategy": strategy, "cash_usd": cash, "xrp_amount": xrp,
        "position": "LONG" if xrp > 0 else "CASH",
        "peak_value": peak_value, "stop_loss_cooldown": cooldown,
        "trades": trades,
    }


def apply_replay(db, result: dict) -> None:
    strategy = result["strategy"]
    # Idempotency: wipe this strategy's previous trade log before reinserting
    # the freshly replayed one, so re-running never duplicates trades.
    db.table("strategy_trades").delete().eq("strategy", strategy).execute()
    if result["trades"]:
        db.table("strategy_trades").insert(result["trades"]).execute()
    db.table("strategy_portfolios").update({
        "cash_usd": result["cash_usd"],
        "xrp_amount": result["xrp_amount"],
        "position": result["position"],
        "peak_value": result["peak_value"],
        "stop_loss_cooldown": result["stop_loss_cooldown"],
    }).eq("strategy", strategy).execute()


def main() -> int:
    db = get_client()
    print("Fetching all historical predictions...")
    predictions = _fetch_all_predictions(db)
    print(f"Replaying {len(predictions)} predictions for each strategy...")

    for strategy in trading.STRATEGIES:
        result = replay(strategy, predictions)
        value = result["cash_usd"] + result["xrp_amount"] * (
            predictions[-1]["price_at_prediction"] if predictions else 0
        )
        print(f"{strategy}: {len(result['trades'])} trades, "
              f"cash=${result['cash_usd']:.2f} xrp={result['xrp_amount']:.4f} "
              f"(~${value:.2f} at last known price)")
        apply_replay(db, result)

    print("Backfill complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

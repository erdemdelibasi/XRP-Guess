"""Entry point run once per day by GitHub Actions.

1. Retrains the ML model on the latest ~4000 15-min candles (~41 days) from
   Binance.
2. Recomputes each ensemble component's rolling accuracy from the prediction
   log and updates the ensemble weights (the "self-improvement" loop).
"""
import sys
from datetime import datetime, timedelta, timezone

from db import get_client
import ensemble
from fetch_data import get_klines_history
import ml_model

SYMBOL = "XRPUSDT"
INTERVAL = "15m"
TRAINING_CANDLES = 4000  # ~41 days of 15-min candles
ROLLING_WINDOW_DAYS = 14
MIN_RESOLVED_FOR_REWEIGHT = 20


def _accuracy(values: list[bool]) -> float | None:
    if len(values) < MIN_RESOLVED_FOR_REWEIGHT:
        return None
    return sum(1 for v in values if v) / len(values)


PAGE_SIZE = 1000  # PostgREST caps a single response at ~1000 rows by default


def _fetch_all_since(db, since: str) -> list[dict]:
    rows = []
    start = 0
    while True:
        page = (
            db.table("predictions")
            .select("tech_correct,ml_correct")
            .gte("created_at", since)
            .range(start, start + PAGE_SIZE - 1)
            .execute()
        )
        rows.extend(page.data)
        if len(page.data) < PAGE_SIZE:
            break
        start += PAGE_SIZE
    return rows


def rolling_accuracies(db) -> tuple[float | None, float | None]:
    """Fetches resolved predictions from the trailing window and computes each
    ensemble component's accuracy client-side (avoids relying on the exact
    chaining semantics of the query builder's negation filter)."""
    since = (datetime.now(timezone.utc) - timedelta(days=ROLLING_WINDOW_DAYS)).isoformat()
    all_rows = _fetch_all_since(db, since)
    resolved = [row for row in all_rows if row["tech_correct"] is not None or row["ml_correct"] is not None]
    tech_values = [row["tech_correct"] for row in resolved if row["tech_correct"] is not None]
    ml_values = [row["ml_correct"] for row in resolved if row["ml_correct"] is not None]
    return _accuracy(tech_values), _accuracy(ml_values)


def upsert_weight(db, component: str, weight: float, accuracy: float | None) -> None:
    db.table("model_state").upsert({
        "component": component,
        "weight": weight,
        "rolling_accuracy": accuracy,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }).execute()


def main() -> int:
    db = get_client()

    print("Retraining ML model on latest historical klines...")
    history = get_klines_history(SYMBOL, interval=INTERVAL, total=TRAINING_CANDLES)
    model, metrics = ml_model.train_model(history)
    ml_model.save_model(model)
    print(f"Retrain complete: {metrics}")

    tech_acc, ml_acc = rolling_accuracies(db)
    new_weights = ensemble.recompute_weights(tech_acc, ml_acc)

    upsert_weight(db, "technical", new_weights["technical"], tech_acc)
    upsert_weight(db, "ml", new_weights["ml"], ml_acc)

    print(f"Updated ensemble weights: {new_weights} "
          f"(technical_accuracy={tech_acc}, ml_accuracy={ml_acc})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

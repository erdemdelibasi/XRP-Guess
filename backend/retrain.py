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


SELECT_COLUMNS = ",".join(
    f"{ensemble.COLUMN_PREFIX[c]}_correct,{ensemble.COLUMN_PREFIX[c]}_confidence" for c in ensemble.COMPONENTS
)


def _fetch_all_since(db, since: str) -> list[dict]:
    rows = []
    start = 0
    while True:
        page = (
            db.table("predictions")
            .select(SELECT_COLUMNS)
            .gte("created_at", since)
            .range(start, start + PAGE_SIZE - 1)
            .execute()
        )
        rows.extend(page.data)
        if len(page.data) < PAGE_SIZE:
            break
        start += PAGE_SIZE
    return rows


def rolling_accuracies(db) -> dict[str, float | None]:
    """Fetches resolved predictions from the trailing window and computes each
    ensemble component's accuracy client-side (avoids relying on the exact
    chaining semantics of the query builder's negation filter). A component's
    row only counts if it actually had a non-zero-confidence opinion that
    period -- whale/news mostly stay silent (confidence 0) when nothing
    notable happened, and that abstention shouldn't be scored as a coin flip."""
    since = (datetime.now(timezone.utc) - timedelta(days=ROLLING_WINDOW_DAYS)).isoformat()
    all_rows = _fetch_all_since(db, since)

    accuracies: dict[str, float | None] = {}
    for component in ensemble.COMPONENTS:
        prefix = ensemble.COLUMN_PREFIX[component]
        values = [
            row[f"{prefix}_correct"]
            for row in all_rows
            if row[f"{prefix}_correct"] is not None and (row.get(f"{prefix}_confidence") or 0) > 0
        ]
        accuracies[component] = _accuracy(values)
    return accuracies


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

    accuracies = rolling_accuracies(db)
    new_weights = ensemble.recompute_weights(accuracies)

    for component in ensemble.COMPONENTS:
        upsert_weight(db, component, new_weights[component], accuracies[component])

    print(f"Updated ensemble weights: {new_weights} (accuracies={accuracies})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

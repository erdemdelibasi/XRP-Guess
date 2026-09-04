"""Entry point run once per day by GitHub Actions.

1. Retrains the ML model on the latest ~4000 15-min candles (~41 days) from
   Binance.
2. Refits the technical/ml confidence calibration (see calibration.py) on a
   held-out walk-forward replay of that same history.
3. Recomputes each ensemble component's rolling accuracy from the prediction
   log and updates the ensemble weights (the "self-improvement" loop).
"""
import sys
from datetime import datetime, timedelta, timezone

import calibration
from db import get_client
import ensemble
from fetch_data import get_klines_history
from indicators import add_cross_asset_correlation, add_indicator_columns, technical_signal
import ml_model
import trading

SYMBOL = "XRPUSDT"
BTC_SYMBOL = "BTCUSDT"
ETH_SYMBOL = "ETHUSDT"
INTERVAL = "15m"
TRAINING_CANDLES = 4000  # ~41 days of 15-min candles
ROLLING_WINDOW_DAYS = 14
MIN_RESOLVED_FOR_REWEIGHT = 20

CALIBRATION_TRAIN_FRACTION = 0.7  # mirrors backtest.py's split
CALIBRATION_WARMUP_CANDLES = 50 * 4
CALIBRATION_FEATURE_LIMIT = 400


def _record(values: list[bool]) -> tuple[int, int] | None:
    """(correct_count, total), or None below the minimum sample size. The
    count -- not just the ratio -- is what lets ensemble.combine() tell a real
    edge from a lucky short run (it shrinks P(correct) toward 0.5 by sample
    size), and what gets stored as model_state.sample_size."""
    if len(values) < MIN_RESOLVED_FOR_REWEIGHT:
        return None
    return sum(1 for v in values if v), len(values)


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


def rolling_records(db) -> dict[str, tuple[int, int] | None]:
    """Fetches resolved predictions from the trailing window and computes each
    ensemble component's accuracy client-side (avoids relying on the exact
    chaining semantics of the query builder's negation filter). A component's
    row only counts if it actually had a non-zero-confidence opinion that
    period -- whale/news mostly stay silent (confidence 0) when nothing
    notable happened, and that abstention shouldn't be scored as a coin flip."""
    since = (datetime.now(timezone.utc) - timedelta(days=ROLLING_WINDOW_DAYS)).isoformat()
    all_rows = _fetch_all_since(db, since)

    records: dict[str, tuple[int, int] | None] = {}
    for component in ensemble.COMPONENTS:
        prefix = ensemble.COLUMN_PREFIX[component]
        values = [
            row[f"{prefix}_correct"]
            for row in all_rows
            if row[f"{prefix}_correct"] is not None and (row.get(f"{prefix}_confidence") or 0) > 0
        ]
        records[component] = _record(values)
    return records


def upsert_weight(db, component: str, weight: float, accuracy: float | None,
                   sample_size: int) -> None:
    """`sample_size` is what lets predict.py rebuild the (correct, total)
    record ensemble.combine() pools on -- an accuracy alone can't say how much
    evidence is behind it. Written best-effort: if the column hasn't been
    added yet (see supabase/schema.sql), fall back to writing the rest rather
    than failing the whole retrain."""
    row = {
        "component": component,
        "weight": weight,
        "rolling_accuracy": accuracy,
        "sample_size": sample_size,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        db.table("model_state").upsert(row).execute()
    except Exception as exc:  # noqa: BLE001 -- missing column shouldn't break the run
        print(f"WARNING: upsert with sample_size failed for {component} ({exc}); "
              "retrying without it -- run the model_state migration in supabase/schema.sql.")
        row.pop("sample_size")
        db.table("model_state").upsert(row).execute()


def _trailing_window(df, end_idx: int, limit: int = CALIBRATION_FEATURE_LIMIT):
    start_idx = max(0, end_idx - limit + 1)
    return df.iloc[start_idx:end_idx + 1]


def fit_calibration(xrp_raw, btc_raw, eth_raw) -> dict:
    """Walk-forward replays technical+ML on a held-out tail of recent history
    (same method as backtest.py) to collect (confidence, was_correct) pairs
    per component, then fits an isotonic calibrator per component. Refit
    daily, alongside the model and ensemble weights, so calibration tracks
    the model's current behavior instead of going stale. Uses its own
    train/test split and a throwaway ML model -- separate from the
    production model trained on the full history above -- purely to get an
    out-of-sample replay to calibrate against."""
    n = min(len(xrp_raw), len(btc_raw), len(eth_raw))
    xrp_raw = xrp_raw.iloc[:n].reset_index(drop=True)
    btc_raw = btc_raw.iloc[:n].reset_index(drop=True)
    eth_raw = eth_raw.iloc[:n].reset_index(drop=True)

    split = int(n * CALIBRATION_TRAIN_FRACTION)
    calib_model, _ = ml_model.train_model(xrp_raw.iloc[:split])

    test_start = split + CALIBRATION_WARMUP_CANDLES
    if test_start >= n - 1:
        print("Not enough history for a calibration refit -- skipping.")
        return {}

    records: dict[str, list[tuple[float, bool]]] = {"technical": [], "ml": []}
    for i in range(test_start, n - 1):
        xrp_window = _trailing_window(xrp_raw, i)
        btc_window = _trailing_window(btc_raw, i)
        eth_window = _trailing_window(eth_raw, i)

        feat = add_indicator_columns(xrp_window)
        feat = add_cross_asset_correlation(feat, btc_window, "btc")
        feat = add_cross_asset_correlation(feat, eth_window, "eth")

        tech = technical_signal(feat)
        ml_sig = ml_model.ml_signal(calib_model, feat)

        price = float(xrp_window["close"].iloc[-1])
        next_price = float(xrp_raw["close"].iloc[i + 1])
        actual_direction = "UP" if next_price > price else "DOWN"

        records["technical"].append((tech["confidence"], actual_direction == tech["direction"]))
        records["ml"].append((ml_sig["confidence"], actual_direction == ml_sig["direction"]))

    calibrators = {}
    for component, pairs in records.items():
        confidences = [c for c, _ in pairs]
        corrects = [ok for _, ok in pairs]
        fitted = calibration.fit(confidences, corrects)
        if fitted is not None:
            calibrators[component] = fitted
    return calibrators


def report_calibration_ceilings(calibrators: dict) -> None:
    """Says out loud whether a freshly-fitted calibrator can still produce a
    confidence high enough to open a position at all.

    Calibration caps a component's confidence at whatever accuracy its history
    actually supports, and that cap moves every day as this refit runs. When it
    lands below trading.min_confidence_to_open_position(), that component's
    single-signal portfolio silently stops trading -- no exception, no warning,
    just a flat portfolio that looks like a strategy which never sees an
    opportunity. Exactly that happened to `technical` (ceiling 0.0693 against
    a 0.0735 threshold) and it took a parameter sweep to notice.

    This is a diagnostic only. It deliberately does NOT adjust anything: a
    ceiling below the threshold usually means the signal's measured edge
    doesn't cover round-trip fees, and forcing trades in that state was
    measured (60-day backtest, 2026-09-03) to make every outcome worse.
    """
    threshold = trading.min_confidence_to_open_position()
    print(f"Calibration ceiling check (position opens above confidence {threshold:.4f}):")
    for component, reg in sorted(calibrators.items()):
        # out_of_bounds="clip" means predicting past the fitted domain returns
        # the curve's top value -- i.e. the highest P(correct) it can ever say.
        p_max = float(reg.predict([1.0])[0])
        ceiling = max((p_max - 0.5) * 2, 0.0)
        if ceiling > threshold:
            print(f"  {component}: ceiling {ceiling:.4f} -- can trade")
        else:
            print(f"  WARNING: {component} ceiling {ceiling:.4f} is BELOW {threshold:.4f} -- "
                  f"this strategy cannot open a position until its measured accuracy improves "
                  f"(max P(correct)={p_max:.3f}). Not an error; see trading.min_confidence_to_open_position().")


def _strategy_portfolio_state_as_of(db, strategy: str, cutoff_iso: str) -> tuple[float, float]:
    """Reconstructs a single-signal strategy's (cash_usd, xrp_amount) as of
    `cutoff_iso`, from the most recent strategy_trades row at or before it --
    trading.STARTING_CASH/0 if it hadn't traded yet. Scoped to
    trading.STRATEGIES only (all read strategy_trades the same way), unlike
    daily_report.py's more general version which also has to branch for the
    ensemble's own `trades` table and Kanal Finans TŞ's separate tables."""
    res = (
        db.table("strategy_trades")
        .select("cash_after,xrp_after")
        .eq("strategy", strategy)
        .lte("created_at", cutoff_iso)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    if res.data:
        return float(res.data[0]["cash_after"]), float(res.data[0]["xrp_after"])
    return trading.STARTING_CASH, 0.0


def report_economic_value(db, xrp_history, records: dict, weights: dict) -> None:
    """Says out loud whether a component's rolling directional accuracy --
    the very number influence_weights() just turned into a weight -- actually
    turned into money in that component's own paper portfolio, over the
    identical ROLLING_WINDOW_DAYS window.

    Accuracy and realized (fee-inclusive) return can diverge (see
    daily_report.py's strategy_report docstring: "a strategy can be accurate
    but still lose money to fees"), and nothing previously checked that for
    the specific window the weights are computed from -- the panels and daily
    mail only ever show since-inception or 24h returns, neither of which
    lines up with the 14-day accuracy window.

    Reuses the klines already fetched for today's ML retrain for the "price
    then" lookup instead of an extra Binance call or depending on a nearby
    resolved prediction existing.

    Diagnostic only, same spirit as report_calibration_ceilings -- does not
    adjust any weight or trading decision.
    """
    since = datetime.now(timezone.utc) - timedelta(days=ROLLING_WINDOW_DAYS)
    price_then_rows = xrp_history[xrp_history["open_time"] <= since]
    if price_then_rows.empty:
        print("Economic value check: not enough candle history for the rolling window yet -- skipping.")
        return
    price_then = float(price_then_rows["close"].iloc[-1])
    price_now = float(xrp_history["close"].iloc[-1])

    print(f"Economic value check (accuracy vs realized return, last {ROLLING_WINDOW_DAYS}d, "
          f"XRP {price_then:.4f} -> {price_now:.4f}):")
    for component in trading.STRATEGIES:
        rec = records.get(component)
        if rec is None:
            continue  # not enough resolved predictions yet for an accuracy figure
        accuracy = rec[0] / rec[1]
        cash_then, xrp_then = _strategy_portfolio_state_as_of(db, component, since.isoformat())
        value_then = cash_then + xrp_then * price_then
        live = trading.get_portfolio_state(db, component)
        value_now = float(live["cash_usd"]) + float(live["xrp_amount"]) * price_now
        portfolio_pct = (value_now - value_then) / value_then * 100 if value_then else 0.0

        flag = ""
        if weights.get(component, 0.0) > 0 and portfolio_pct < 0:
            flag = "  <-- accuracy is contributing weight but the SAME-WINDOW portfolio lost money (fee erosion?)"
        print(f"  {component}: accuracy %{accuracy * 100:.1f} ({rec[0]}/{rec[1]}) | "
              f"portfolio {portfolio_pct:+.2f}%{flag}")


def main() -> int:
    db = get_client()

    print("Retraining ML model on latest historical klines...")
    history = get_klines_history(SYMBOL, interval=INTERVAL, total=TRAINING_CANDLES)
    model, metrics = ml_model.train_model(history)
    ml_model.save_model(model)
    print(f"Retrain complete: {metrics}")

    print("Refitting confidence calibration...")
    btc_history = get_klines_history(BTC_SYMBOL, interval=INTERVAL, total=TRAINING_CANDLES)
    eth_history = get_klines_history(ETH_SYMBOL, interval=INTERVAL, total=TRAINING_CANDLES)
    calibrators = fit_calibration(history, btc_history, eth_history)
    if calibrators:
        # Merge onto the existing saved calibrators rather than replacing
        # wholesale -- a component that doesn't have enough well-populated
        # bins today (fit() returns nothing for it) should keep yesterday's
        # calibration instead of silently reverting to "uncalibrated."
        merged = calibration.load()
        merged.update(calibrators)
        calibration.save(merged)
        print(f"Calibration refit complete for: {list(calibrators)}")
        report_calibration_ceilings(merged)

    records = rolling_records(db)
    # These weights are for display only -- ensemble.combine() pools on the
    # (correct, total) records themselves, not on a weight (see its docstring).
    new_weights = ensemble.influence_weights(records)
    report_economic_value(db, history, records, new_weights)

    accuracies = {c: (records[c][0] / records[c][1] if records[c] else None)
                  for c in ensemble.COMPONENTS}
    for component in ensemble.COMPONENTS:
        rec = records[component]
        upsert_weight(db, component, new_weights[component], accuracies[component],
                       rec[1] if rec else 0)

    print(f"Updated ensemble influence: {new_weights} (accuracies={accuracies}, records={records})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

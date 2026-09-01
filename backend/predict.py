"""Entry point run every 15 minutes by GitHub Actions.

Each run predicts the price at the *next* quarter-hour clock mark (e.g. a run
at 18:07 targets 18:15; a run at 18:16 targets 18:30) using both a rule-based
technical signal and an ML model. Over the course of an hour this naturally
produces four checkpoints -- :15, :30, :45, :00 -- each with its own expected
percentage change and target price, from both methods.

No Binance API key is used or required -- only public market data endpoints.

Each run also feeds the final ensemble prediction into trading.py, which
simulates a $1000 paper-trading portfolio (no real money, no real orders).
"""
import sys
from datetime import datetime, timedelta, timezone

from db import get_client
import ensemble
from fetch_data import get_current_price, get_klines, get_klines_history
from indicators import (
    add_cross_asset_correlation,
    add_indicator_columns,
    estimate_pct_change,
    recent_volatility,
    technical_signal,
)
import ml_model
import trading

SYMBOL = "XRPUSDT"
BTC_SYMBOL = "BTCUSDT"
ETH_SYMBOL = "ETHUSDT"
INTERVAL = "15m"
FEATURE_LIMIT = 400
BOOTSTRAP_TRAINING_CANDLES = 4000


def next_quarter_hour(now: datetime) -> datetime:
    """Rounds strictly upward to the next :00/:15/:30/:45 mark."""
    floored = now.replace(second=0, microsecond=0)
    remainder = floored.minute % 15
    step = 15 - remainder if remainder else 15
    return floored + timedelta(minutes=step)


def resolve_due_predictions(db, current_price: float) -> None:
    now_iso = datetime.now(timezone.utc).isoformat()
    due = (
        db.table("predictions")
        .select("*")
        .is_("resolved_at", "null")
        .lte("target_time", now_iso)
        .execute()
    )
    for row in due.data:
        actual_direction = "UP" if current_price > row["price_at_prediction"] else "DOWN"
        correct = actual_direction == row["predicted_direction"]
        tech_correct = row.get("tech_direction") is not None and actual_direction == row["tech_direction"]
        ml_correct = row.get("ml_direction") is not None and actual_direction == row["ml_direction"]

        db.table("predictions").update({
            "resolved_at": now_iso,
            "price_at_resolution": current_price,
            "actual_direction": actual_direction,
            "correct": correct,
            "tech_correct": tech_correct,
            "ml_correct": ml_correct,
        }).eq("id", row["id"]).execute()
        print(f"Resolved prediction {row['id']} (target {row['target_time']}): "
              f"predicted={row['predicted_direction']} actual={actual_direction} correct={correct}")


def get_ensemble_weights(db) -> dict:
    res = db.table("model_state").select("*").execute()
    weights = dict(ensemble.DEFAULT_WEIGHTS)
    for row in res.data:
        if row["component"] in weights and row["weight"] is not None:
            weights[row["component"]] = float(row["weight"])
    return weights


def build_features(symbol: str):
    raw = get_klines(symbol, interval=INTERVAL, limit=FEATURE_LIMIT)
    return add_indicator_columns(raw)


def main() -> int:
    db = get_client()
    now = datetime.now(timezone.utc)

    current_price = get_current_price(SYMBOL)
    resolve_due_predictions(db, current_price)

    xrp = build_features(SYMBOL)
    btc = get_klines(BTC_SYMBOL, interval=INTERVAL, limit=FEATURE_LIMIT)
    eth = get_klines(ETH_SYMBOL, interval=INTERVAL, limit=FEATURE_LIMIT)
    xrp = add_cross_asset_correlation(xrp, btc, "btc")
    xrp = add_cross_asset_correlation(xrp, eth, "eth")

    tech = technical_signal(xrp)

    model = ml_model.load_model()
    model_version = "bootstrap"
    if model is None:
        print("No trained model found yet -- bootstrap-training one now from historical klines.")
        history = get_klines_history(SYMBOL, interval=INTERVAL, total=BOOTSTRAP_TRAINING_CANDLES)
        model, metrics = ml_model.train_model(history)
        ml_model.save_model(model)
        model_version = datetime.now(timezone.utc).isoformat()
        print(f"Bootstrap training complete: {metrics}")

    ml = ml_model.ml_signal(model, xrp)

    weights = get_ensemble_weights(db)
    final = ensemble.combine(tech, ml, weights)

    volatility = recent_volatility(xrp)
    tech_pct = estimate_pct_change(tech["score"], volatility)
    ml_pct = estimate_pct_change(ml["score"], volatility)
    final_pct = estimate_pct_change(final["score"], volatility)

    target_time = next_quarter_hour(now)

    inserted = db.table("predictions").insert({
        "symbol": SYMBOL,
        "target_time": target_time.isoformat(),
        "price_at_prediction": current_price,
        "predicted_direction": final["direction"],
        "confidence": final["confidence"],
        "predicted_pct_change": final_pct,
        "predicted_price": current_price * (1 + final_pct),
        "tech_direction": tech["direction"],
        "tech_confidence": tech["confidence"],
        "tech_pct_change": tech_pct,
        "tech_price": current_price * (1 + tech_pct),
        "ml_direction": ml["direction"],
        "ml_confidence": ml["confidence"],
        "ml_pct_change": ml_pct,
        "ml_price": current_price * (1 + ml_pct),
        "weight_technical": weights["technical"],
        "weight_ml": weights["ml"],
        "model_version": model_version,
    }).execute()

    print(f"New prediction for {target_time.isoformat()} @ {current_price} {SYMBOL}: "
          f"{final['direction']} {final_pct * 100:+.2f}% -> {current_price * (1 + final_pct):.4f} "
          f"[tech={tech['direction']}/{tech_pct * 100:+.2f}%, ml={ml['direction']}/{ml_pct * 100:+.2f}%, "
          f"weights={weights}]")

    prediction_id = inserted.data[0]["id"] if inserted.data else None
    trading.maybe_trade(db, prediction_id, final["direction"], final["confidence"], current_price)
    return 0


if __name__ == "__main__":
    sys.exit(main())

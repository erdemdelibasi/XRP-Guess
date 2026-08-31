"""Entry point run once per hour by GitHub Actions.

1. Resolves any previous unresolved prediction against the current price.
2. Builds a fresh technical + ML signal and logs a new ensemble prediction.

No Binance API key is used or required -- only public market data endpoints.
"""
import sys
from datetime import datetime, timezone

from db import get_client
import ensemble
from fetch_data import get_current_price, get_klines
from indicators import add_cross_asset_correlation, add_indicator_columns, technical_signal
import ml_model

SYMBOL = "XRPUSDT"
BTC_SYMBOL = "BTCUSDT"
ETH_SYMBOL = "ETHUSDT"


def resolve_previous_predictions(db, current_price: float) -> None:
    pending = db.table("predictions").select("*").is_("resolved_at", "null").execute()
    now_iso = datetime.now(timezone.utc).isoformat()

    for row in pending.data:
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
        print(f"Resolved prediction {row['id']}: predicted={row['predicted_direction']} "
              f"actual={actual_direction} correct={correct}")


def get_ensemble_weights(db) -> dict:
    res = db.table("model_state").select("*").execute()
    weights = dict(ensemble.DEFAULT_WEIGHTS)
    for row in res.data:
        if row["component"] in weights and row["weight"] is not None:
            weights[row["component"]] = float(row["weight"])
    return weights


def build_features(symbol: str, limit: int = 300):
    raw = get_klines(symbol, interval="1h", limit=limit)
    return add_indicator_columns(raw)


def main() -> int:
    db = get_client()

    current_price = get_current_price(SYMBOL)
    resolve_previous_predictions(db, current_price)

    xrp = build_features(SYMBOL)
    btc = get_klines(BTC_SYMBOL, interval="1h", limit=300)
    eth = get_klines(ETH_SYMBOL, interval="1h", limit=300)
    xrp = add_cross_asset_correlation(xrp, btc, "btc")
    xrp = add_cross_asset_correlation(xrp, eth, "eth")

    tech = technical_signal(xrp)

    model = ml_model.load_model()
    model_version = "bootstrap"
    if model is None:
        print("No trained model found yet -- bootstrap-training one now from historical klines.")
        history = get_klines(SYMBOL, interval="1h", limit=1000)
        model, metrics = ml_model.train_model(history)
        ml_model.save_model(model)
        model_version = datetime.now(timezone.utc).isoformat()
        print(f"Bootstrap training complete: {metrics}")

    ml = ml_model.ml_signal(model, xrp)

    weights = get_ensemble_weights(db)
    final = ensemble.combine(tech, ml, weights)

    db.table("predictions").insert({
        "symbol": SYMBOL,
        "price_at_prediction": current_price,
        "predicted_direction": final["direction"],
        "confidence": final["confidence"],
        "tech_direction": tech["direction"],
        "tech_confidence": tech["confidence"],
        "ml_direction": ml["direction"],
        "ml_confidence": ml["confidence"],
        "weight_technical": weights["technical"],
        "weight_ml": weights["ml"],
        "model_version": model_version,
    }).execute()

    print(f"New prediction @ {current_price} {SYMBOL}: "
          f"{final['direction']} (confidence={final['confidence']:.2f}) "
          f"[tech={tech['direction']}/{tech['confidence']:.2f}, "
          f"ml={ml['direction']}/{ml['confidence']:.2f}, weights={weights}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())

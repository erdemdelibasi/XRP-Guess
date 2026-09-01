"""Entry point run every 15 minutes by GitHub Actions.

Each run predicts the price at the *next* quarter-hour clock mark (e.g. a run
at 18:07 targets 18:15; a run at 18:16 targets 18:30) using four independent
signal components -- a rule-based technical signal, an ML model, an on-chain
XRPL whale/exchange-flow signal, and a news/regulatory sentiment signal --
combined by ensemble.py. Over the course of an hour this naturally produces
four checkpoints -- :15, :30, :45, :00 -- each with its own expected
percentage change and target price, from every method.

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
import news_signal as news_signal_module
import trading
import whale_signal as whale_signal_module

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
        whale_correct = row.get("whale_direction") is not None and actual_direction == row["whale_direction"]
        news_correct = row.get("news_direction") is not None and actual_direction == row["news_direction"]

        db.table("predictions").update({
            "resolved_at": now_iso,
            "price_at_resolution": current_price,
            "actual_direction": actual_direction,
            "correct": correct,
            "tech_correct": tech_correct,
            "ml_correct": ml_correct,
            "whale_correct": whale_correct,
            "news_correct": news_correct,
        }).eq("id", row["id"]).execute()
        print(f"Resolved prediction {row['id']} (target {row['target_time']}): "
              f"predicted={row['predicted_direction']} actual={actual_direction} correct={correct}")


def get_ensemble_weights(db) -> dict:
    res = db.table("model_state").select("*").execute()
    weights = dict(ensemble.DEFAULT_WEIGHTS)
    for row in res.data:
        if row["component"] in ensemble.COMPONENTS and row["weight"] is not None:
            weights[row["component"]] = float(row["weight"])
    return weights


def safe_signal(fn, *args, label: str) -> dict:
    """Calls a signal-producing function and falls back to a neutral result
    if it raises -- a hiccup in one evidence source must never block the
    others or stop a prediction from being logged."""
    try:
        return fn(*args)
    except Exception as exc:  # noqa: BLE001 -- deliberately broad, see docstring
        print(f"WARNING: {label} signal failed ({exc}); using neutral fallback.")
        return {"direction": "UP", "confidence": 0.0, "score": 0.0}


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
    whale = safe_signal(whale_signal_module.whale_signal, current_price, label="whale")
    news = safe_signal(news_signal_module.news_signal, label="news")

    weights = get_ensemble_weights(db)
    signals = {"technical": tech, "ml": ml, "whale": whale, "news": news}
    final = ensemble.combine(signals, weights)

    volatility = recent_volatility(xrp)
    tech_pct = estimate_pct_change(tech["score"], volatility)
    ml_pct = estimate_pct_change(ml["score"], volatility)
    whale_pct = estimate_pct_change(whale["score"], volatility)
    news_pct = estimate_pct_change(news["score"], volatility)
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
        "whale_direction": whale["direction"],
        "whale_confidence": whale["confidence"],
        "whale_pct_change": whale_pct,
        "whale_price": current_price * (1 + whale_pct),
        "news_direction": news["direction"],
        "news_confidence": news["confidence"],
        "news_pct_change": news_pct,
        "news_price": current_price * (1 + news_pct),
        "weight_technical": weights["technical"],
        "weight_ml": weights["ml"],
        "weight_whale": weights["whale"],
        "weight_news": weights["news"],
        "model_version": model_version,
    }).execute()

    print(f"New prediction for {target_time.isoformat()} @ {current_price} {SYMBOL}: "
          f"{final['direction']} {final_pct * 100:+.2f}% -> {current_price * (1 + final_pct):.4f} "
          f"[tech={tech['direction']}/{tech_pct * 100:+.2f}%, ml={ml['direction']}/{ml_pct * 100:+.2f}%, "
          f"whale={whale['direction']}/{whale['confidence']:.2f}, news={news['direction']}/{news['confidence']:.2f}, "
          f"weights={weights}]")

    prediction_id = inserted.data[0]["id"] if inserted.data else None
    trading.maybe_trade(db, prediction_id, final["direction"], final["confidence"], current_price)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Entry point run every 15 minutes by GitHub Actions.

Each run predicts the price at the *next* quarter-hour clock mark (e.g. a run
at 18:07 targets 18:15; a run at 18:16 targets 18:30) using six independent
signal components -- a rule-based technical signal, an ML model, an on-chain
XRPL whale/exchange-flow signal, a news/regulatory sentiment signal, a live
order-book imbalance signal, and a Claude API judgment call -- combined by
ensemble.py. Over the course of an hour this naturally produces four
checkpoints -- :15, :30, :45, :00 -- each with its own expected percentage
change and target price, from every method.

No Binance API key is used or required -- only public market data endpoints.

Each run also feeds the final ensemble prediction into trading.py, which
simulates a $1000 paper-trading portfolio (no real money, no real orders).
"""
import sys
from datetime import datetime, timedelta, timezone

import calibration
import claude_signal as claude_signal_module
from db import get_client
import ensemble
from fetch_data import get_current_price, get_klines, get_klines_history
import kanal_finans_trading
import momentum_trading
from indicators import (
    add_cross_asset_correlation,
    add_indicator_columns,
    estimate_pct_change,
    recent_volatility,
    technical_signal,
)
import ml_model
import news_signal as news_signal_module
import orderbook_signal as orderbook_signal_module
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


def price_at_target(target_time: datetime, fallback: float) -> float:
    """Close of the 1-minute candle ending exactly at `target_time` -- the
    real market price at the moment a prediction was actually aiming for.

    Resolution used to score against whatever the live price happened to be
    when the resolver ran, which is a different and variable horizon: runs
    land ~1 min after each quarter-hour, so a :15 target was routinely scored
    10-30 minutes late, and when GitHub Actions delays the cron (a confirmed
    2h56m gap on 2026-09-03) a 15-minute prediction got scored against a
    ~3-hour move. That inflates or destroys accuracy at random and feeds
    straight into retrain.py's rolling accuracies and the ensemble weights.

    Falls back to the live price if the candle can't be fetched, so a Binance
    hiccup still resolves the row rather than leaving it stuck unresolved.
    """
    try:
        end_ms = int(target_time.timestamp() * 1000) - 1
        candles = get_klines(SYMBOL, interval="1m", limit=1, end_time_ms=end_ms)
        if not candles.empty:
            return float(candles["close"].iloc[-1])
        print(f"WARNING: no 1m candle at {target_time.isoformat()}; using live price.")
    except Exception as exc:  # noqa: BLE001 -- see docstring
        print(f"WARNING: couldn't fetch price at {target_time.isoformat()} ({exc}); using live price.")
    return fallback


def resolve_due_predictions(db, current_price: float) -> None:
    now_iso = datetime.now(timezone.utc).isoformat()
    due = (
        db.table("predictions")
        .select("*")
        .is_("resolved_at", "null")
        .lte("target_time", now_iso)
        .execute()
    )
    price_cache: dict[str, float] = {}
    for row in due.data:
        target_time = datetime.fromisoformat(row["target_time"])
        if row["target_time"] not in price_cache:
            price_cache[row["target_time"]] = price_at_target(target_time, current_price)
        resolution_price = price_cache[row["target_time"]]

        actual_direction = "UP" if resolution_price > row["price_at_prediction"] else "DOWN"
        correct = actual_direction == row["predicted_direction"]
        tech_correct = row.get("tech_direction") is not None and actual_direction == row["tech_direction"]
        ml_correct = row.get("ml_direction") is not None and actual_direction == row["ml_direction"]
        whale_correct = row.get("whale_direction") is not None and actual_direction == row["whale_direction"]
        news_correct = row.get("news_direction") is not None and actual_direction == row["news_direction"]
        orderbook_correct = row.get("orderbook_direction") is not None and actual_direction == row["orderbook_direction"]
        claude_correct = row.get("claude_direction") is not None and actual_direction == row["claude_direction"]

        db.table("predictions").update({
            "resolved_at": now_iso,
            "price_at_resolution": resolution_price,
            "actual_direction": actual_direction,
            "correct": correct,
            "tech_correct": tech_correct,
            "ml_correct": ml_correct,
            "whale_correct": whale_correct,
            "news_correct": news_correct,
            "orderbook_correct": orderbook_correct,
            "claude_correct": claude_correct,
        }).eq("id", row["id"]).execute()
        print(f"Resolved prediction {row['id']} (target {row['target_time']}): "
              f"predicted={row['predicted_direction']} actual={actual_direction} correct={correct}")


def get_ensemble_state(db) -> tuple[dict, dict]:
    """(display_weights, reliabilities) from model_state.

    `reliabilities` is what ensemble.combine() actually pools on: each
    component's live (correct, resolved) record, rebuilt from the stored
    rolling_accuracy and sample_size. The weights are only carried along to
    be logged on the prediction row and shown in the UI. A component with no
    sample_size yet (column not migrated, or no history) is simply left out,
    and combine() falls back to its cold-start path.
    """
    res = db.table("model_state").select("*").execute()
    weights = dict(ensemble.DEFAULT_WEIGHTS)
    reliabilities: dict[str, tuple[int, int]] = {}
    for row in res.data:
        component = row["component"]
        if component not in ensemble.COMPONENTS:
            continue
        if row.get("weight") is not None:
            weights[component] = float(row["weight"])
        total = row.get("sample_size") or 0
        accuracy = row.get("rolling_accuracy")
        if total > 0 and accuracy is not None:
            reliabilities[component] = (round(float(accuracy) * int(total)), int(total))
    return weights, reliabilities


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
    # A model saved before a FEATURE_COLUMNS change (e.g. a new indicator
    # added) is incompatible and would raise on the ml_signal() call below --
    # treat that exactly like "no model yet" and retrain on the spot rather
    # than crashing the whole run.
    if model is not None and getattr(model, "n_features_in_", None) != len(ml_model.FEATURE_COLUMNS):
        print("Saved model's feature count doesn't match FEATURE_COLUMNS anymore -- treating as stale.")
        model = None
    if model is None:
        print("No compatible trained model found -- bootstrap-training one now from historical klines.")
        history = get_klines_history(SYMBOL, interval=INTERVAL, total=BOOTSTRAP_TRAINING_CANDLES)
        model, metrics = ml_model.train_model(history)
        ml_model.save_model(model)
        model_version = datetime.now(timezone.utc).isoformat()
        print(f"Bootstrap training complete: {metrics}")

    ml = ml_model.ml_signal(model, xrp)

    calibrators = calibration.load()
    tech = calibration.apply(calibrators.get("technical"), tech)
    ml = calibration.apply(calibrators.get("ml"), ml)

    whale = safe_signal(whale_signal_module.whale_signal, current_price, label="whale")
    news = safe_signal(news_signal_module.news_signal, label="news")
    orderbook = safe_signal(orderbook_signal_module.orderbook_signal, label="orderbook")
    # Given tech's/whale's own (pre-calibration) signals as context -- see
    # claude_signal.py for why calibrated confidence isn't used here.
    claude = safe_signal(claude_signal_module.claude_signal, current_price, tech, whale, label="claude")

    weights, reliabilities = get_ensemble_state(db)
    signals = {"technical": tech, "ml": ml, "whale": whale, "news": news, "orderbook": orderbook, "claude": claude}
    final = ensemble.combine(signals, weights, reliabilities)

    volatility = recent_volatility(xrp)
    tech_pct = estimate_pct_change(tech["score"], volatility)
    ml_pct = estimate_pct_change(ml["score"], volatility)
    whale_pct = estimate_pct_change(whale["score"], volatility)
    news_pct = estimate_pct_change(news["score"], volatility)
    orderbook_pct = estimate_pct_change(orderbook["score"], volatility)
    claude_pct = estimate_pct_change(claude["score"], volatility)
    final_pct = estimate_pct_change(final["score"], volatility)

    target_time = next_quarter_hour(now)

    prediction_row = {
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
        "orderbook_direction": orderbook["direction"],
        "orderbook_confidence": orderbook["confidence"],
        "orderbook_pct_change": orderbook_pct,
        "orderbook_price": current_price * (1 + orderbook_pct),
        "claude_direction": claude["direction"],
        "claude_confidence": claude["confidence"],
        "claude_pct_change": claude_pct,
        "claude_price": current_price * (1 + claude_pct),
        "weight_technical": weights["technical"],
        "weight_ml": weights["ml"],
        "weight_whale": weights["whale"],
        "weight_news": weights["news"],
        "weight_orderbook": weights["orderbook"],
        "weight_claude": weights["claude"],
        "model_version": model_version,
        # Same scalar every row (falls out of trading.py's constants, not
        # tuned) -- stored per-row so the frontend can show "does this
        # confidence clear the real trade-opening bar" without duplicating
        # trading.min_confidence_to_open_position()'s math in JS.
        "trade_threshold": trading.min_confidence_to_open_position(),
    }

    try:
        inserted = db.table("predictions").insert(prediction_row).execute()
    except Exception as exc:  # noqa: BLE001 -- see comment below
        print(f"WARNING: insert with trade_threshold failed ({exc}); retrying without it -- "
              "run the trade_threshold migration in supabase/schema.sql.")
        prediction_row.pop("trade_threshold")
        inserted = db.table("predictions").insert(prediction_row).execute()

    print(f"New prediction for {target_time.isoformat()} @ {current_price} {SYMBOL}: "
          f"{final['direction']} {final_pct * 100:+.2f}% -> {current_price * (1 + final_pct):.4f} "
          f"[tech={tech['direction']}/{tech_pct * 100:+.2f}%, ml={ml['direction']}/{ml_pct * 100:+.2f}%, "
          f"whale={whale['direction']}/{whale['confidence']:.2f}, news={news['direction']}/{news['confidence']:.2f}, "
          f"orderbook={orderbook['direction']}/{orderbook['confidence']:.2f}, "
          f"claude={claude['direction']}/{claude['confidence']:.2f}, weights={weights}]")

    prediction_id = inserted.data[0]["id"] if inserted.data else None

    # The ensemble portfolio plus five independent single-signal-only
    # portfolios (technical-only, ml-only, whale-only, news-only, claude-only)
    # -- lets a signal's real paper-trading performance be compared against
    # the blended ensemble instead of only ever being seen mixed together.
    strategy_signals = {"technical": tech, "ml": ml, "whale": whale, "news": news, "claude": claude}
    for strategy_name in ("ensemble", *trading.STRATEGIES):
        signal = final if strategy_name == "ensemble" else strategy_signals[strategy_name]
        try:
            trading.maybe_trade(db, strategy_name, prediction_id, signal["direction"], signal["confidence"], current_price)
        except Exception as exc:  # noqa: BLE001 -- one strategy's DB hiccup must not block the others
            print(f"WARNING: {strategy_name} portfolio update failed ({exc})")

    # Kanal Finans TS's own paper portfolio (see kanal_finans_trading.py) --
    # BUY/SELL decisions come from Tunc Satiroglu's videos (processed
    # separately, locally, see CLAUDE.md), but its stop-loss has to be
    # watched continuously, not just when a new video lands. This only
    # touches Supabase + the price already fetched above, never YouTube, so
    # it's unaffected by the IP-block issue that keeps kanal_finans.py itself
    # off GitHub Actions.
    try:
        kanal_finans_trading.maybe_check_stop_loss(db, current_price)
    except Exception as exc:  # noqa: BLE001
        print(f"WARNING: kanal_finans stop-loss check failed ({exc})")

    # Eighth paper portfolio: trend-following (Donchian breakout + EMA trend
    # filter + trailing/hard stop), NOT one of the compute_rebalance()-based
    # ones above -- see momentum_trading.py's module docstring for why it's
    # a separate engine. Reuses `xrp` (already fetched + indicator-enriched
    # above for the technical signal), no extra API call.
    try:
        momentum_trading.maybe_trade(db, xrp, current_price)
    except Exception as exc:  # noqa: BLE001 -- a hiccup here must not block predictions or the other portfolios
        print(f"WARNING: momentum portfolio update failed ({exc})")

    return 0


if __name__ == "__main__":
    sys.exit(main())

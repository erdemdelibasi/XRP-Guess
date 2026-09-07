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
is meaningless.

Because peak_value stays stale, a small confidence-scaled re-entry right
after a stop-loss almost never lifts the portfolio back above the
still-elevated threshold in one step -- so without a cooldown, the very next
candle re-triggers the same stop-loss, and this repeats. A backtest
diagnostic confirmed this isn't hypothetical: 235 of 236 stop-losses in a
60-day window were followed by a re-entry that got stopped out again within
10 hours, burning ~35% of starting capital in fees alone with no
directional benefit. STOP_LOSS_COOLDOWN_CANDLES blocks new entries for a
fixed window after a stop-loss specifically to break that loop; comparing
it against two alternatives (raising the re-entry confidence bar, resetting
peak_value to the post-liquidation value) in the same backtest showed the
cooldown cut stop-loss count ~14x and fee drag from ~73% to ~24% of capital
-- by far the most effective of the three.

compute_rebalance() is pure (no DB access) so backtest.py can replay the
exact same sizing/stop-loss logic offline against historical data.

Six independent $1000 paper portfolios run side by side: the weighted
ensemble (the original one, `portfolio_state`/`trades`, unchanged since it
already had real trade history before this module supported more than one
strategy) plus five single-signal strategies -- technical-only, ml-only,
whale-only, news-only, claude-only -- each trading purely on its own signal,
in `strategy_portfolios`/`strategy_trades` (keyed by `strategy`). All six run
through the exact same compute_rebalance()/maybe_trade() logic; only which
table gets read/written differs. This lets a single signal's real paper
performance be compared against the blended ensemble instead of only ever
being seen mixed together. orderbook has no strategy of its own here (the
user only asked for the other five); `backfill_strategy_portfolios.py` seeds
each from historical `predictions` rows the first time it's deployed (a
strategy backfills to no trades at all -- correctly -- for every row that
predates its own columns existing, e.g. every historical row for `claude`).
"""
from datetime import datetime, timezone

FEE_RATE = 0.001
MIN_CONFIDENCE_TO_TRADE = 0.02
STARTING_CASH = 1000.0

STRATEGIES = ("technical", "ml", "whale", "news", "claude")  # the five single-signal portfolios; "ensemble" is the original, separate table

MAX_ALLOCATION = 0.85                  # a single signal never commits more than 85% of the portfolio to XRP
CONFIDENCE_FOR_MAX_ALLOCATION = 0.25   # confidence level that maps to MAX_ALLOCATION (typical confidences run ~0.05-0.20)
REBALANCE_THRESHOLD = 0.25             # only trade if actual allocation is off target by more than this fraction of portfolio value
                                        # (backtest.py showed 0.10 causes excessive fee-eroding churn -- confidence
                                        # shifts a few points step to step, which crosses a tight threshold constantly)
STOP_LOSS_DRAWDOWN = 0.15              # force to cash if value falls more than this fraction below its running peak
STOP_LOSS_COOLDOWN_CANDLES = 40        # ~10h at 15-min candles -- no re-entry for this many candles after a stop-loss
                                        # (see module docstring: without this, re-entry gets stopped out again almost every time)


def min_confidence_to_open_position() -> float:
    """Lowest confidence that can actually open a position starting from all
    cash. Not a knob -- it falls out of the three constants above: a flat
    portfolio's drift from target equals the target allocation itself, so a
    BUY needs _target_allocation(...) > REBALANCE_THRESHOLD.

    Worth naming even though nothing needs it to trade, because it is the
    number that decides whether a strategy trades *at all*, and it was
    invisible in the code. Measured 2026-09-03: calibration.py's technical
    calibrator had a ceiling of 0.0693 against this threshold's 0.0735, so
    the technical strategy was frozen -- no error, no log line, just a
    portfolio that quietly stopped trading. retrain.py now compares the two
    every day and says so out loud.
    """
    return REBALANCE_THRESHOLD * CONFIDENCE_FOR_MAX_ALLOCATION / MAX_ALLOCATION


def _state_table(strategy: str) -> str:
    return "portfolio_state" if strategy == "ensemble" else "strategy_portfolios"


def _filter_own_row(query, strategy: str):
    """Applies the "just this strategy's one row" filter to an already-built
    select()/update() query -- portfolio_state (ensemble) is keyed by a fixed
    id=1, strategy_portfolios (the other five) by the `strategy` column.
    (Must be called after select()/update(), not on the bare table() query --
    postgrest-py's table() builder doesn't expose .eq() until then.)"""
    return query.eq("id", 1) if strategy == "ensemble" else query.eq("strategy", strategy)


def get_portfolio_state(db, strategy: str = "ensemble") -> dict:
    query = db.table(_state_table(strategy)).select("*")
    res = _filter_own_row(query, strategy).single().execute()
    return res.data


def _update_state(db, strategy: str, fields: dict) -> None:
    query = db.table(_state_table(strategy)).update(fields)
    _filter_own_row(query, strategy).execute()


def _record_trade(db, strategy: str, side: str, price: float, xrp_amount: float, usd_amount: float,
                   fee_usd: float, cash_after: float, xrp_after: float,
                   prediction_id: int | None, reason: str) -> None:
    row = {
        "side": side,
        "price": price,
        "xrp_amount": xrp_amount,
        "usd_amount": usd_amount,
        "fee_usd": fee_usd,
        "cash_after": cash_after,
        "xrp_after": xrp_after,
        "triggered_by_prediction_id": prediction_id,
        "reason": reason,
    }
    table = "trades"
    if strategy != "ensemble":
        table = "strategy_trades"
        row["strategy"] = strategy
    db.table(table).insert(row).execute()


def _target_allocation(direction: str, confidence: float) -> float:
    """Fraction of total portfolio value that should be in XRP. Long-only --
    a DOWN signal targets 0 (get out of the way), never a short position."""
    if direction == "DOWN":
        return 0.0
    return min(confidence / CONFIDENCE_FOR_MAX_ALLOCATION, 1.0) * MAX_ALLOCATION


def compute_rebalance(cash: float, xrp: float, price: float, direction: str,
                       confidence: float, peak_value: float, cooldown_remaining: int = 0) -> dict:
    """Pure function, no DB access -- shared by maybe_trade() (live) and
    backtest.py (historical replay).

    `cooldown_remaining` is how many candles are left in a post-stop-loss
    re-entry block (0 = free to trade). Returns {"action": "BUY"/"SELL"/"HOLD",
    "usd_amount": float, "xrp_amount": float, "new_peak_value": float,
    "new_cooldown_remaining": int, "reason": str}. Only the amount matching
    `action` is meaningful (usd_amount for BUY, xrp_amount for SELL); both
    are 0 for HOLD.
    """
    value = cash + xrp * price
    peak_value = max(peak_value, value)

    if value <= 0:
        return {"action": "HOLD", "usd_amount": 0.0, "xrp_amount": 0.0,
                "new_peak_value": peak_value, "new_cooldown_remaining": cooldown_remaining, "reason": ""}

    # Stop-loss takes priority over any signal, and starts a cooldown so the
    # very next candle can't immediately re-enter and re-trigger it.
    if xrp > 0 and value <= peak_value * (1 - STOP_LOSS_DRAWDOWN):
        return {
            "action": "SELL", "usd_amount": 0.0, "xrp_amount": xrp,
            "new_peak_value": peak_value, "new_cooldown_remaining": STOP_LOSS_COOLDOWN_CANDLES,
            "reason": "Stop-loss tetiklendi",
        }

    if cooldown_remaining > 0:
        return {"action": "HOLD", "usd_amount": 0.0, "xrp_amount": 0.0,
                "new_peak_value": peak_value, "new_cooldown_remaining": cooldown_remaining - 1, "reason": ""}

    if confidence < MIN_CONFIDENCE_TO_TRADE:
        return {"action": "HOLD", "usd_amount": 0.0, "xrp_amount": 0.0,
                "new_peak_value": peak_value, "new_cooldown_remaining": 0, "reason": ""}

    target_fraction = _target_allocation(direction, confidence)
    target_xrp_value = value * target_fraction
    current_xrp_value = xrp * price
    drift = (current_xrp_value - target_xrp_value) / value

    if drift > REBALANCE_THRESHOLD:
        xrp_to_sell = min(xrp, (current_xrp_value - target_xrp_value) / price)
        # A DOWN signal targets 0 XRP, so this should be a clean full exit --
        # but current_xrp_value is xrp*price and dividing it back by price
        # doesn't round-trip exactly, so min() keeps the slightly-smaller
        # float and leaves dust behind. Live proof: the `ml` portfolio sat at
        # 5.7e-14 XRP, which maybe_trade() then labelled "LONG" (it writes
        # "LONG" if new_xrp > 0), so an all-cash portfolio showed as holding
        # a position on the dashboard. Snapping a float-noise remainder to a
        # full exit fixes it at the source, for backtest.py too.
        if xrp - xrp_to_sell < xrp * 1e-9:
            xrp_to_sell = xrp
        return {
            "action": "SELL", "usd_amount": 0.0, "xrp_amount": xrp_to_sell,
            "new_peak_value": peak_value, "new_cooldown_remaining": 0, "reason": "Hedef pozisyona rebalance (azalt)",
        }
    if drift < -REBALANCE_THRESHOLD:
        usd_to_spend = min(cash, target_xrp_value - current_xrp_value)
        if usd_to_spend <= 0:
            return {"action": "HOLD", "usd_amount": 0.0, "xrp_amount": 0.0,
                    "new_peak_value": peak_value, "new_cooldown_remaining": 0, "reason": ""}
        return {
            "action": "BUY", "usd_amount": usd_to_spend, "xrp_amount": 0.0,
            "new_peak_value": peak_value, "new_cooldown_remaining": 0, "reason": "Hedef pozisyona rebalance (artir)",
        }

    return {"action": "HOLD", "usd_amount": 0.0, "xrp_amount": 0.0,
            "new_peak_value": peak_value, "new_cooldown_remaining": 0, "reason": ""}


def maybe_trade(db, strategy: str, prediction_id: int | None, direction: str, confidence: float, price: float) -> None:
    """Fetches `strategy`'s portfolio state, decides via compute_rebalance(),
    and applies the result (DB writes + trade log). `strategy` is "ensemble"
    (the original portfolio_state/trades tables) or one of trading.STRATEGIES
    (strategy_portfolios/strategy_trades)."""
    state = get_portfolio_state(db, strategy)
    cash, xrp = float(state["cash_usd"]), float(state["xrp_amount"])
    peak_value = float(state.get("peak_value") or STARTING_CASH)
    cooldown_remaining = int(state.get("stop_loss_cooldown") or 0)

    decision = compute_rebalance(cash, xrp, price, direction, confidence, peak_value, cooldown_remaining)
    now_iso = datetime.now(timezone.utc).isoformat()

    if decision["action"] == "HOLD":
        if decision["new_peak_value"] != peak_value or decision["new_cooldown_remaining"] != cooldown_remaining:
            _update_state(db, strategy, {
                "peak_value": decision["new_peak_value"],
                "stop_loss_cooldown": decision["new_cooldown_remaining"],
                "updated_at": now_iso,
            })
        return

    if decision["action"] == "BUY":
        gross_usd = decision["usd_amount"]
        fee_usd = gross_usd * FEE_RATE
        xrp_bought = (gross_usd - fee_usd) / price
        new_cash = cash - gross_usd
        new_xrp = xrp + xrp_bought

        _update_state(db, strategy, {
            "cash_usd": new_cash, "xrp_amount": new_xrp, "position": "LONG",
            "peak_value": decision["new_peak_value"],
            "stop_loss_cooldown": decision["new_cooldown_remaining"],
            "updated_at": now_iso,
        })
        _record_trade(db, strategy, "BUY", price, xrp_bought, gross_usd, fee_usd, new_cash, new_xrp,
                       prediction_id, decision["reason"])
        print(f"TRADE [{strategy}]: BUY {xrp_bought:.4f} XRP @ {price:.4f} (fee ${fee_usd:.2f}) -- {decision['reason']}")

    elif decision["action"] == "SELL":
        xrp_to_sell = decision["xrp_amount"]
        gross_usd = xrp_to_sell * price
        fee_usd = gross_usd * FEE_RATE
        new_cash = cash + (gross_usd - fee_usd)
        new_xrp = xrp - xrp_to_sell

        _update_state(db, strategy, {
            "cash_usd": new_cash, "xrp_amount": new_xrp,
            "position": "LONG" if new_xrp > 0 else "CASH",
            "peak_value": decision["new_peak_value"],
            "stop_loss_cooldown": decision["new_cooldown_remaining"],
            "updated_at": now_iso,
        })
        _record_trade(db, strategy, "SELL", price, xrp_to_sell, gross_usd, fee_usd, new_cash, new_xrp,
                       prediction_id, decision["reason"])
        print(f"TRADE [{strategy}]: SELL {xrp_to_sell:.4f} XRP @ {price:.4f} (fee ${fee_usd:.2f}) -- {decision['reason']}")

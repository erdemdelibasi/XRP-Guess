"""Seventh $1000 paper portfolio: trend-following, not direction-classifying.

Built 2026-09-04 after a concrete failure was measured live: XRP rallied
+6.8% over ~15 hours (2026-09-03) and every one of the six existing
portfolios ended the day in the red. The root cause (see CLAUDE.md) was
architectural, not a bad model -- trading.compute_rebalance() re-evaluates a
confidence-scaled target allocation every 15 minutes from scratch, and
_target_allocation() maps any DOWN call to a hard 0% regardless of how weak
that call was. With technical+ml direction genuinely close to a coin flip at
15-minute resolution (this project's own 20,000-candle backtest measured
exactly 50.0% hit rate), the ensemble's direction flips almost every cycle,
and each flip forces a full exit-then-reenter round trip -- 11 of them in 19
hours on 2026-09-03/04, each paying the 0.1%-per-side fee regardless of
whether that particular flip happened to be right.

This strategy sidesteps that failure mode structurally instead of patching
it: it holds a binary FLAT/LONG state and only exits on an explicit,
multi-candle signal (a real stop or a real trend reversal), never because a
single noisy 15-minute reading disagreed with the last one.

Entry (FLAT -> LONG): price breaks above the highest high of the prior
ENTRY_LOOKBACK_CANDLES candles (a Donchian breakout) AND ema9 > ema21 (the
breakout has trend backing it, not just a noise spike). Commits
trading.MAX_ALLOCATION of cash, same headroom convention as every other
portfolio here.

Exit (LONG -> FLAT), any one of three:
  - trailing stop: price falls TRAILING_STOP_PCT below the peak price seen
    since entry (locks in gains once a move has run)
  - hard stop: price falls HARD_STOP_PCT below the entry price (protects
    against a breakout that fails immediately)
  - trend break: ema9 crosses back below ema21 (the move that justified
    entry is actually over, not just pausing)
Plus a portfolio-level circuit breaker identical in mechanism to
trading.compute_rebalance()'s stop-loss/cooldown (STOP_LOSS_DRAWDOWN from the
running peak, STOP_LOSS_COOLDOWN_CANDLES before re-entry is allowed) -- reused
from trading.py rather than reinvented, since that cooldown's necessity was
already measured there (see trading.py's docstring).

VALIDATION STATUS -- read before trusting any number this portfolio shows:
a 208-day backtest (backend/../ scratch analysis, 2026-09-04, not committed --
see the write-up in conversation/CLAUDE.md if added) found this general
approach beat buy-and-hold in 4/5 parameter variants over the full window,
but a proper train/first-half-only-then-test-second-half split told a more
honest story: EVERY variant lost money on the training half (worse than
buy-and-hold by 5.6-12.2 points, one hit 19.9% drawdown), and the locked
winner's entire test-half outperformance (+31.6 points) came from a single
trade out of 16 (+51.27% on one breakout) -- remove that one trade and the
same period turns into -8.87%. That is not enough evidence, in either
direction, to trust backward-looking numbers here. This portfolio is
deliberately NOT backfilled from historical replay (unlike the other five
single-signal portfolios) so its displayed history is 100% real forward
decisions from whenever this shipped, not a replay of that fragile backtest
dressed up as track record. Judge it by what it does from here, not by what
a backtest said it would have done.

compute_decision() is pure (no DB access), same convention as
trading.compute_rebalance() and kanal_finans_trading.decide_on_mention() --
lets this be unit-tested or backtested later without touching Supabase.
"""
import math
from datetime import datetime, timezone

import pandas as pd

import trading  # reuse FEE_RATE, MAX_ALLOCATION, STOP_LOSS_DRAWDOWN, STOP_LOSS_COOLDOWN_CANDLES

STARTING_CASH = 1000.0

ENTRY_LOOKBACK_CANDLES = 96   # 24h at 15-min candles -- how far back the Donchian breakout looks
TRAILING_STOP_PCT = 0.05      # exit if price falls 5% from the post-entry peak
HARD_STOP_PCT = 0.04          # exit if price falls 4% below entry (catches an immediately-failed breakout)


def compute_decision(cash: float, xrp: float, position: str, entry_price: float | None,
                      peak_since_entry: float | None, peak_value: float, cooldown_remaining: int,
                      price: float, rolling_high: float, ema9: float, ema21: float) -> dict:
    """Pure. Returns the action to take plus every state field that needs
    persisting afterward (new_position/new_entry_price/new_peak_since_entry/
    new_peak_value/new_cooldown), regardless of action."""
    value = cash + xrp * price
    peak_value = max(peak_value, value)

    if value <= 0:
        return {"action": "HOLD", "usd_amount": 0.0, "xrp_amount": 0.0, "reason": "",
                "new_position": position, "new_entry_price": entry_price, "new_peak_since_entry": peak_since_entry,
                "new_peak_value": peak_value, "new_cooldown": cooldown_remaining}

    # Portfolio-level circuit breaker -- identical mechanism to
    # trading.compute_rebalance()'s stop-loss/cooldown, reused rather than
    # reinvented (see trading.py docstring for why the cooldown is load-bearing).
    if xrp > 0 and value <= peak_value * (1 - trading.STOP_LOSS_DRAWDOWN):
        return {
            "action": "SELL", "usd_amount": 0.0, "xrp_amount": xrp, "reason": "Portfoy stop-loss tetiklendi",
            "new_position": "FLAT", "new_entry_price": None, "new_peak_since_entry": None,
            "new_peak_value": peak_value, "new_cooldown": trading.STOP_LOSS_COOLDOWN_CANDLES,
        }

    if cooldown_remaining > 0:
        return {"action": "HOLD", "usd_amount": 0.0, "xrp_amount": 0.0, "reason": "",
                "new_position": position, "new_entry_price": entry_price, "new_peak_since_entry": peak_since_entry,
                "new_peak_value": peak_value, "new_cooldown": cooldown_remaining - 1}

    have_indicators = not (math.isnan(rolling_high) or math.isnan(ema9) or math.isnan(ema21))

    if position == "FLAT":
        if have_indicators and price > rolling_high and ema9 > ema21:
            usd_to_spend = cash * trading.MAX_ALLOCATION
            return {
                "action": "BUY", "usd_amount": usd_to_spend, "xrp_amount": 0.0,
                "reason": f"Donchian kirilimi (>{rolling_high:.4f}) + trend onayi (ema9>ema21)",
                "new_position": "LONG", "new_entry_price": price, "new_peak_since_entry": price,
                "new_peak_value": peak_value, "new_cooldown": 0,
            }
        return {"action": "HOLD", "usd_amount": 0.0, "xrp_amount": 0.0, "reason": "",
                "new_position": "FLAT", "new_entry_price": None, "new_peak_since_entry": None,
                "new_peak_value": peak_value, "new_cooldown": 0}

    # position == "LONG"
    new_peak_since_entry = max(peak_since_entry or price, price)
    trailing_stop_price = new_peak_since_entry * (1 - TRAILING_STOP_PCT)
    hard_stop_price = (entry_price or price) * (1 - HARD_STOP_PCT)

    reason = None
    if price <= hard_stop_price:
        reason = f"Hard stop (giristen %{HARD_STOP_PCT*100:.0f} asagi)"
    elif price <= trailing_stop_price:
        reason = f"Trailing stop (tepeden %{TRAILING_STOP_PCT*100:.0f} asagi)"
    elif have_indicators and ema9 < ema21:
        reason = "Trend kirildi (ema9<ema21)"

    if reason:
        return {
            "action": "SELL", "usd_amount": 0.0, "xrp_amount": xrp, "reason": reason,
            "new_position": "FLAT", "new_entry_price": None, "new_peak_since_entry": None,
            "new_peak_value": peak_value, "new_cooldown": 0,
        }
    return {"action": "HOLD", "usd_amount": 0.0, "xrp_amount": 0.0, "reason": "",
            "new_position": "LONG", "new_entry_price": entry_price, "new_peak_since_entry": new_peak_since_entry,
            "new_peak_value": peak_value, "new_cooldown": 0}


def _rolling_high(xrp_df: pd.DataFrame, lookback: int = ENTRY_LOOKBACK_CANDLES) -> float:
    """Highest high over the `lookback` candles strictly before the most
    recent row -- the last row in predict.py's window is the still-forming
    candle, so it must never count toward its own breakout threshold."""
    highs = xrp_df["high"]
    if len(highs) < lookback + 1:
        return float("nan")
    window = highs.iloc[-(lookback + 1):-1]
    return float(window.max())


def get_portfolio_state(db) -> dict:
    res = db.table("momentum_portfolio").select("*").eq("id", 1).single().execute()
    return res.data


def _record_trade(db, side: str, price: float, xrp_amount: float, usd_amount: float, fee_usd: float,
                   cash_after: float, xrp_after: float, reason: str) -> None:
    db.table("momentum_trades").insert({
        "side": side,
        "price": price,
        "xrp_amount": xrp_amount,
        "usd_amount": usd_amount,
        "fee_usd": fee_usd,
        "cash_after": cash_after,
        "xrp_after": xrp_after,
        "reason": reason,
    }).execute()


def maybe_trade(db, xrp_df: pd.DataFrame, price: float) -> None:
    """Called every 15 minutes from predict.py, right after the six
    compute_rebalance()-based portfolios. `xrp_df` is predict.py's already-
    fetched, already-indicator-enriched XRP window (has ema9/ema21/high) --
    no extra API call needed."""
    state = get_portfolio_state(db)
    cash, xrp = float(state["cash_usd"]), float(state["xrp_amount"])
    position = state["position"]
    entry_price = state.get("entry_price")
    peak_since_entry = state.get("peak_since_entry")
    peak_value = float(state.get("peak_value") or STARTING_CASH)
    cooldown_remaining = int(state.get("stop_loss_cooldown") or 0)

    rolling_high = _rolling_high(xrp_df)
    last = xrp_df.iloc[-1]
    ema9 = float(last.get("ema9", float("nan")))
    ema21 = float(last.get("ema21", float("nan")))

    decision = compute_decision(
        cash, xrp, position, entry_price, peak_since_entry, peak_value, cooldown_remaining,
        price, rolling_high, ema9, ema21,
    )

    now_iso = datetime.now(timezone.utc).isoformat()

    if decision["action"] == "HOLD":
        # Skip the write entirely while flat and nothing moved -- most of
        # this strategy's 15-min cycles are exactly that, no sense writing
        # an identical row every time. While LONG, peak_since_entry climbs
        # with the market and has to be persisted so the next cycle's
        # trailing stop is measured from the real peak, not a stale one.
        changed = (
            decision["new_position"] != position
            or decision["new_peak_since_entry"] != peak_since_entry
            or decision["new_peak_value"] != peak_value
            or decision["new_cooldown"] != cooldown_remaining
        )
        if changed:
            db.table("momentum_portfolio").update({
                "position": decision["new_position"],
                "entry_price": decision["new_entry_price"],
                "peak_since_entry": decision["new_peak_since_entry"],
                "peak_value": decision["new_peak_value"],
                "stop_loss_cooldown": decision["new_cooldown"],
                "updated_at": now_iso,
            }).eq("id", 1).execute()
        return

    if decision["action"] == "BUY":
        gross_usd = decision["usd_amount"]
        fee_usd = gross_usd * trading.FEE_RATE
        xrp_amount = (gross_usd - fee_usd) / price
        new_cash = cash - gross_usd
        new_xrp = xrp + xrp_amount
    else:  # SELL
        xrp_amount = decision["xrp_amount"]
        gross_usd = xrp_amount * price
        fee_usd = gross_usd * trading.FEE_RATE
        new_cash = cash + (gross_usd - fee_usd)
        new_xrp = xrp - xrp_amount

    db.table("momentum_portfolio").update({
        "cash_usd": new_cash, "xrp_amount": new_xrp,
        "position": decision["new_position"],
        "entry_price": decision["new_entry_price"],
        "peak_since_entry": decision["new_peak_since_entry"],
        "peak_value": decision["new_peak_value"],
        "stop_loss_cooldown": decision["new_cooldown"],
        "updated_at": now_iso,
    }).eq("id", 1).execute()
    _record_trade(db, decision["action"], price, xrp_amount, gross_usd, fee_usd, new_cash, new_xrp, decision["reason"])
    print(f"TRADE [momentum]: {decision['action']} {xrp_amount:.4f} XRP @ {price:.4f} (fee ${fee_usd:.2f}) -- {decision['reason']}")

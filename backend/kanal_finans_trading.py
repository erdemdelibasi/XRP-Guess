"""Seventh $1000 paper portfolio: mirrors, as literally as possible, what
Tunc Satiroglu says to do with XRP in his Kanal Finans videos -- buy/sell,
plus a continuously-watched stop-loss at whatever level he most recently
gave (see kanal_finans.py, CLAUDE.md).

Deliberately does NOT use trading.compute_rebalance() -- that engine scales
position size by a numeric confidence score updated every 15 minutes; this
one has no confidence score at all, just a discrete BUY/SELL/HOLD from an
irregular (per-video) event stream, so it needs its own (smaller) decision
function. Position sizing is all-in/all-out (not the other six portfolios'
confidence-scaled partial sizing) -- there's no number to scale by, and the
user explicitly asked for a literal "do what he says" model.

Resistance levels are stored (for display) but never auto-trigger a trade --
live data showed Tunc sometimes calls a resistance break bullish/a buy
signal, not a sell one (e.g. "1.41 direncinin gecilmesi bekleniyor, gecilirse
alim firsati olabilir"), so treating "price reached resistance" as an
automatic take-profit would actively misread him. Only the stop-loss level
is a hard, automatic trigger, checked continuously via predict.py's existing
15-minute cycle (maybe_check_stop_loss) -- not just when a new video lands.
That call touches only Supabase + an already-fetched price, never YouTube, so
it runs fine on GitHub Actions even though kanal_finans.py itself can't
(see CLAUDE.md).
"""
from datetime import datetime, timezone

import trading  # reuse FEE_RATE -- same 0.10% assumption as every other paper portfolio

STARTING_CASH = 1000.0

# A stop-loss/resistance level Tunc gives should sit somewhere near the live
# price -- a near-term technical call is never several times away from spot.
# This band exists to catch extraction-SCALE errors, not to second-guess a
# real call: found live on 2026-09-09, mention id=49 -- the first ever
# extracted from a locally-Whisper-transcribed video instead of YouTube's
# official captions (see ../Kanal-Finans-Fetcher, captions were 429'd that
# day) -- came back stop_loss_price=136 / resistance_price=143 while XRP
# traded near $1.44, almost certainly spoken "1.43" ("bir kirk uc")
# transcribed as the bare number "143". Every one of the 10 prior mentions
# that carried a level was within ~30% of spot; this rejects anything more
# than 3x away on either side, which comfortably covers any real call while
# catching a 10x or 100x slip -- and it matters here specifically because
# check_stop_loss() below fires on `price <= stop_loss_price`, so a
# corrupted level in the hundreds while XRP trades near $1 doesn't just
# mislabel a number, it makes the stop fire on the very next price check.
PLAUSIBLE_LEVEL_RATIO = (1 / 3, 3.0)


def _plausible_level(level: float | None, price: float) -> float | None:
    """None (treated as "not given") if `level` isn't within shouting
    distance of `price` -- see PLAUSIBLE_LEVEL_RATIO above."""
    if not level or price <= 0:
        return None
    ratio = level / price
    return level if PLAUSIBLE_LEVEL_RATIO[0] <= ratio <= PLAUSIBLE_LEVEL_RATIO[1] else None


def decide_on_mention(cash: float, xrp: float, position_stop_loss: float | None, position_resistance: float | None,
                       action: str, mention_stop_loss: float | None, mention_resistance: float | None,
                       price: float) -> dict:
    """Pure. A mention's stop_loss/resistance only replaces the currently
    watched level when it actually gives a new, PLAUSIBLE one (Tunc doesn't
    repeat the level in every video, and an implausible one is treated the
    same as none given) -- otherwise the previously known level carries
    forward. Returns {"trade_action": "BUY"/"SELL"/"HOLD", ...} plus
    "rejected_stop_loss"/"rejected_resistance" (the raw values, or None) so
    a caller can log a loud warning instead of silently dropping them."""
    plausible_stop = _plausible_level(mention_stop_loss, price)
    plausible_resistance = _plausible_level(mention_resistance, price)
    rejected_stop = mention_stop_loss if mention_stop_loss and plausible_stop is None else None
    rejected_resistance = mention_resistance if mention_resistance and plausible_resistance is None else None

    new_stop = plausible_stop if plausible_stop else position_stop_loss
    new_resistance = plausible_resistance if plausible_resistance else position_resistance

    if action == "BUY" and xrp == 0 and cash > 0:
        fee_usd = cash * trading.FEE_RATE
        xrp_bought = (cash - fee_usd) / price
        return {
            "trade_action": "BUY", "usd_amount": cash, "xrp_amount": xrp_bought, "fee_usd": fee_usd,
            "new_stop_loss": new_stop, "new_resistance": new_resistance,
            "rejected_stop_loss": rejected_stop, "rejected_resistance": rejected_resistance,
            "reason": "Tunc Satiroglu: al",
        }

    if action == "SELL" and xrp > 0:
        gross_usd = xrp * price
        fee_usd = gross_usd * trading.FEE_RATE
        return {
            "trade_action": "SELL", "usd_amount": gross_usd, "xrp_amount": xrp, "fee_usd": fee_usd,
            # flat again -- nothing left to protect until the next BUY sets a fresh level
            "new_stop_loss": None, "new_resistance": new_resistance,
            "rejected_stop_loss": rejected_stop, "rejected_resistance": rejected_resistance,
            "reason": "Tunc Satiroglu: sat",
        }

    return {
        "trade_action": "HOLD", "new_stop_loss": new_stop, "new_resistance": new_resistance,
        "rejected_stop_loss": rejected_stop, "rejected_resistance": rejected_resistance, "reason": "",
    }


def check_stop_loss(xrp: float, stop_loss_price: float | None, price: float) -> dict:
    """Pure. Continuous monitoring, independent of new mentions -- only
    fires while actually long and only below a known level."""
    if xrp > 0 and stop_loss_price and price <= stop_loss_price:
        gross_usd = xrp * price
        fee_usd = gross_usd * trading.FEE_RATE
        return {
            "trade_action": "SELL", "usd_amount": gross_usd, "xrp_amount": xrp, "fee_usd": fee_usd,
            "reason": f"Zarar-kes tetiklendi (${stop_loss_price:.4f})",
        }
    return {"trade_action": "HOLD"}


def get_portfolio_state(db) -> dict:
    res = db.table("kanal_finans_portfolio").select("*").eq("id", 1).single().execute()
    return res.data


def _record_trade(db, decision: dict, price: float, cash_after: float, xrp_after: float, mention_id: int | None) -> None:
    db.table("kanal_finans_trades").insert({
        "side": decision["trade_action"],
        "price": price,
        "xrp_amount": decision["xrp_amount"],
        "usd_amount": decision["usd_amount"],
        "fee_usd": decision["fee_usd"],
        "cash_after": cash_after,
        "xrp_after": xrp_after,
        "triggered_by_mention_id": mention_id,
        "reason": decision["reason"],
    }).execute()


def apply_mention_decision(db, mention: dict, price: float) -> None:
    """Called live from kanal_finans.py right after a new XRP mention is
    saved, with the current market price."""
    state = get_portfolio_state(db)
    cash, xrp = float(state["cash_usd"]), float(state["xrp_amount"])
    position_stop_loss = state.get("stop_loss_price")
    position_resistance = state.get("resistance_price")

    decision = decide_on_mention(
        cash, xrp, position_stop_loss, position_resistance,
        mention["action"], mention.get("stop_loss_price"), mention.get("resistance_price"), price,
    )
    if decision["rejected_stop_loss"] is not None:
        print(f"WARNING: kanal_finans mention {mention['id']} stop_loss_price="
              f"{decision['rejected_stop_loss']} is implausible vs price={price:.4f} -- ignored, "
              f"see PLAUSIBLE_LEVEL_RATIO in kanal_finans_trading.py.")
    if decision["rejected_resistance"] is not None:
        print(f"WARNING: kanal_finans mention {mention['id']} resistance_price="
              f"{decision['rejected_resistance']} is implausible vs price={price:.4f} -- ignored, "
              f"see PLAUSIBLE_LEVEL_RATIO in kanal_finans_trading.py.")
    _apply(db, decision, price, cash, xrp, mention["id"])


def maybe_check_stop_loss(db, price: float) -> None:
    """Called every 15 minutes from predict.py -- never touches YouTube, so
    unlike kanal_finans.py itself this runs fine on GitHub Actions."""
    state = get_portfolio_state(db)
    cash, xrp = float(state["cash_usd"]), float(state["xrp_amount"])
    decision = check_stop_loss(xrp, state.get("stop_loss_price"), price)
    if decision["trade_action"] == "HOLD":
        return
    decision["new_stop_loss"] = None
    decision["new_resistance"] = state.get("resistance_price")
    _apply(db, decision, price, cash, xrp, mention_id=None)


def _apply(db, decision: dict, price: float, cash: float, xrp: float, mention_id: int | None) -> None:
    now_iso = datetime.now(timezone.utc).isoformat()

    if decision["trade_action"] == "HOLD":
        db.table("kanal_finans_portfolio").update({
            "stop_loss_price": decision["new_stop_loss"],
            "resistance_price": decision["new_resistance"],
            "updated_at": now_iso,
        }).eq("id", 1).execute()
        return

    if decision["trade_action"] == "BUY":
        new_cash = cash - decision["usd_amount"]
        new_xrp = xrp + decision["xrp_amount"]
        position = "LONG"
    else:  # SELL
        new_cash = cash + (decision["usd_amount"] - decision["fee_usd"])
        new_xrp = xrp - decision["xrp_amount"]
        position = "LONG" if new_xrp > 0 else "CASH"

    db.table("kanal_finans_portfolio").update({
        "cash_usd": new_cash, "xrp_amount": new_xrp, "position": position,
        "stop_loss_price": decision["new_stop_loss"],
        "resistance_price": decision["new_resistance"],
        "updated_at": now_iso,
    }).eq("id", 1).execute()
    _record_trade(db, decision, price, new_cash, new_xrp, mention_id)
    print(f"TRADE [kanal_finans]: {decision['trade_action']} {decision['xrp_amount']:.4f} XRP @ {price:.4f} -- {decision['reason']}")

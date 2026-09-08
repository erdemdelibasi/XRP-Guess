"""Kanal Finans TS (YouTube @KanalFinans, Tunc Satiroglu) as an independent
opinion feed for XRP -- the TRADE-APPLICATION half only.

WHERE THE OTHER HALF WENT, AND WHY
-----------------------------------
Until 2026-09-08 this module also polled the channel's RSS feed, fetched each
video's transcript, and asked Claude to extract mentions -- all of it inline,
then immediately applied any XRP mention to this project's own paper
portfolio. XAU-Guess ran an independent, near-identical copy of that same
pipeline against the SAME YouTube channel, from the SAME machine (same
residential IP), on its own 15-minute schedule.

That meant every pending video's transcript was fetched from YouTube TWICE
per cycle for no reason -- doubling the load against an endpoint that was
already blocking us intermittently. Measured that day: this project had 23
historical successes (the most recent less than 24 hours earlier) while
XAU-Guess had never once succeeded -- so doubling an already-struggling IP's
request volume was making a real problem worse, not a theoretical one.

The fix moved everything that talks to YOUTUBE into ../Kanal-Finans-Fetcher,
a small sibling repo that fetches each transcript ONCE and writes into BOTH
projects' Supabase with each project's own extraction prompt/schema (they
are genuinely different questions -- this project reports XRP/BTC/ETH/KRIPTO
mentions, XAU-Guess reports ALTIN/GUMUS mentions plus macro themes this
project never asked for, so there was never a single shared schema to
converge on).

What is LEFT here is everything that needs THIS project's own dependencies
and cannot be shared: `fetch_data`, `kanal_finans_trading`. This file's whole
job now is: read `kanal_finans_mentions` rows the fetcher wrote that this
project has not traded on yet (`applied_at is null`), and apply the XRP ones.
It makes ZERO requests to YouTube, which is why it can keep running on its
OLD 15-minute schedule while the actual fetch moved to the fetcher's slower,
shared, 30-minute one -- this got MORE responsive to a fresh mention, not
less, because it is no longer waiting behind a possibly-blocked transcript
fetch to do the one thing it actually needs to do quickly.

Everything else about this feature is unchanged and still true: this is one
person's reported opinion, not a forecast this project stands behind, only
XRP mentions ever move money (BTC/ETH/KRIPTO are informational only -- there
is no portfolio for them), stop-loss still fires automatically while
resistance never does (see kanal_finans_trading.py), and a missing level
still means "unchanged", never "cancelled".

migration_kanal_finans_applied_at.sql backfilled every pre-existing mention's
`applied_at` to its own `created_at` -- those 45 rows were already traded on
by the old inline logic, so they must NOT be read as pending on this file's
first run under the new split.
"""
from __future__ import annotations

from datetime import datetime, timezone

import db as db_module
import kanal_finans_trading
from fetch_data import get_current_price

SYMBOL = "XRPUSDT"

# Only XRP maps onto the real paper portfolio -- BTC/ETH/KRIPTO mentions are
# informational, same reason GENEL is in XAU-Guess: reported, not traded.
TRADEABLE_ASSET = "XRP"


def get_pending_mentions(db) -> list[dict]:
    """Mentions Kanal-Finans-Fetcher wrote that this project has not traded
    on yet, oldest first so they apply in the order they were actually said."""
    return (db.table("kanal_finans_mentions")
            .select("*")
            .is_("applied_at", "null")
            .order("published_at")
            .execute().data)


def mark_applied(db, mention_id: int) -> None:
    db.table("kanal_finans_mentions").update(
        {"applied_at": datetime.now(timezone.utc).isoformat()}
    ).eq("id", mention_id).execute()


def apply_pending_mentions(db) -> int:
    """Applies every not-yet-traded XRP mention to the portfolio. Returns how
    many resulted in a real trade (BTC/ETH/KRIPTO mentions are marked applied
    but don't count, same as a HOLD reading counts but moves no money)."""
    mentions = get_pending_mentions(db)
    applied = 0
    for mention in mentions:
        if mention["asset"] != TRADEABLE_ASSET:
            mark_applied(db, mention["id"])
            continue
        try:
            price = get_current_price(SYMBOL)
            kanal_finans_trading.apply_mention_decision(db, mention, price)
            mark_applied(db, mention["id"])
            applied += 1
            print(f"kanal_finans: [{mention['asset']}] {mention['action']} "
                  f"@ {price:,.4f} ({SYMBOL})")
        except Exception as exc:  # noqa: BLE001 -- one mention's hiccup must
            # not stop the rest, and must NOT be marked applied: leaving
            # applied_at null is what makes the next run retry it.
            print(f"WARNING: kanal_finans apply failed for mention "
                  f"{mention['id']} ({exc})")
    return applied


def main() -> int:
    db = db_module.get_client()
    applied = apply_pending_mentions(db)
    if applied:
        print(f"kanal_finans: applied {applied} pending mention(s).")
    else:
        print("kanal_finans: nothing pending.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""One-off (not scheduled): answers "what would $1000 have become" by
replaying every historical XRP kanal_finans_mentions row chronologically
against real historical price, through the exact same
kanal_finans_trading.decide_on_mention()/check_stop_loss() logic the live
system uses -- then seeds kanal_finans_portfolio/kanal_finans_trades with the
result, exactly like backfill_strategy_portfolios.py does for the other five
strategies. Idempotent: always resets from scratch (deletes existing
kanal_finans_trades, replays the full history again), so re-running is safe.

Two steps:
1. Any existing XRP mention row that predates the action/stop_loss_price/
   resistance_price columns (NULL there) gets them filled in from its
   already-stored `summary` text via a small Claude call -- no need to
   re-fetch the transcript, the summary text already carries the concrete
   numbers (e.g. "zarar kes seviyesi 1.37-1.46 civari").
2. Chronological replay: walk 15-minute XRP/USDT candles (fetch_data.py,
   the same public Binance data source used everywhere else in this project)
   from just before the first mention to now. At each candle's close, first
   apply any mention whose published_at has arrived but hasn't been applied
   yet (at that candle's close price), then run the continuous stop-loss
   check -- mirroring predict.py's real 15-minute cadence as closely as
   backtest data allows.
"""
import json
import os
import sys
from datetime import datetime, timezone

import anthropic

from db import get_client
import fetch_data
import kanal_finans_trading
import trading

MODEL = "claude-sonnet-5"

BACKFILL_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["BUY", "SELL", "HOLD"]},
        "stop_loss_price": {"type": "number"},
        "resistance_price": {"type": "number"},
    },
    "required": ["action", "stop_loss_price", "resistance_price"],
    "additionalProperties": False,
}
BACKFILL_SYSTEM_PROMPT = (
    "Sana bir piyasa yorumcusunun XRP hakkinda soylediklerinin Turkce ozeti "
    "verilecek. Bu ozetten onun net bir 'al/pozisyona gir' onerisi mi "
    "(action=BUY), 'sat/pozisyondan cik/kar al' onerisi mi (action=SELL), "
    "yoksa 'tut/bekle/degisiklik yok' mu (action=HOLD) dedigini cikar. Eger "
    "belirtmisse zarar-kes/destek fiyat seviyesini (stop_loss_price) ve "
    "direnc/hedef fiyat seviyesini (resistance_price) sayi olarak ver -- "
    "aralik verilmisse daha temkinli (pozisyonu daha erken kapatan) ucu "
    "kullan. Belirtilmemisse her ikisi icin de 0 kullan."
)


def backfill_action_fields(db) -> None:
    rows = (
        db.table("kanal_finans_mentions")
        .select("id,summary")
        .eq("asset", "XRP")
        .is_("action", "null")
        .execute()
    ).data
    if not rows:
        print("backfill_kanal_finans_portfolio: no XRP mentions need action-field backfill.")
        return

    print(f"backfill_kanal_finans_portfolio: parsing action/stop_loss/resistance for {len(rows)} XRP mention(s)...")
    client = anthropic.Anthropic()
    for row in rows:
        response = client.messages.create(
            model=MODEL,
            max_tokens=256,
            system=BACKFILL_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": row["summary"]}],
            output_config={
                "effort": "low",
                "format": {"type": "json_schema", "schema": BACKFILL_SCHEMA},
            },
        )
        text = next(b.text for b in response.content if b.type == "text")
        parsed = json.loads(text)
        db.table("kanal_finans_mentions").update({
            "action": parsed["action"],
            "stop_loss_price": parsed["stop_loss_price"] or None,
            "resistance_price": parsed["resistance_price"] or None,
        }).eq("id", row["id"]).execute()
        print(f"  id={row['id']}: action={parsed['action']} stop_loss={parsed['stop_loss_price']} resistance={parsed['resistance_price']}")


def _fetch_xrp_mentions(db) -> list[dict]:
    return (
        db.table("kanal_finans_mentions")
        .select("id,published_at,action,stop_loss_price,resistance_price")
        .eq("asset", "XRP")
        .order("published_at")
        .execute()
    ).data


def replay(mentions: list[dict]) -> dict:
    """Pure in-memory replay. Returns final state + full trade list, ready
    to write to the DB -- mirrors backfill_strategy_portfolios.replay()."""
    if not mentions:
        return {"cash_usd": kanal_finans_trading.STARTING_CASH, "xrp_amount": 0.0, "position": "CASH",
                "stop_loss_price": None, "resistance_price": None, "trades": []}

    first_time = datetime.fromisoformat(mentions[0]["published_at"].replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    total_candles = int((now - first_time).total_seconds() / 900) + 10  # +10 candles of headroom
    candles = fetch_data.get_klines_history("XRPUSDT", "15m", total=total_candles)
    candles = candles[candles["close_time"] >= first_time].reset_index(drop=True)

    cash, xrp = kanal_finans_trading.STARTING_CASH, 0.0
    stop_loss, resistance = None, None
    trades = []
    pending = list(mentions)

    for _, candle in candles.iterrows():
        close_time, price = candle["close_time"], float(candle["close"])

        while pending and datetime.fromisoformat(pending[0]["published_at"].replace("Z", "+00:00")) <= close_time:
            mention = pending.pop(0)
            decision = kanal_finans_trading.decide_on_mention(
                cash, xrp, stop_loss, resistance,
                mention["action"], mention.get("stop_loss_price"), mention.get("resistance_price"), price,
            )
            stop_loss, resistance = decision["new_stop_loss"], decision["new_resistance"]
            if decision["trade_action"] == "BUY":
                cash -= decision["usd_amount"]
                xrp += decision["xrp_amount"]
                trades.append(_trade_row(decision, price, cash, xrp, mention["id"], close_time))
            elif decision["trade_action"] == "SELL":
                cash += decision["usd_amount"] - decision["fee_usd"]
                xrp -= decision["xrp_amount"]
                trades.append(_trade_row(decision, price, cash, xrp, mention["id"], close_time))

        sl_decision = kanal_finans_trading.check_stop_loss(xrp, stop_loss, price)
        if sl_decision["trade_action"] == "SELL":
            cash += sl_decision["usd_amount"] - sl_decision["fee_usd"]
            xrp -= sl_decision["xrp_amount"]
            stop_loss = None
            trades.append(_trade_row(sl_decision, price, cash, xrp, None, close_time))

    return {
        "cash_usd": cash, "xrp_amount": xrp, "position": "LONG" if xrp > 0 else "CASH",
        "stop_loss_price": stop_loss, "resistance_price": resistance, "trades": trades,
        "last_price": float(candles.iloc[-1]["close"]) if len(candles) else None,
    }


def _trade_row(decision: dict, price: float, cash_after: float, xrp_after: float, mention_id, created_at) -> dict:
    return {
        "side": decision["trade_action"], "price": price,
        "xrp_amount": decision["xrp_amount"], "usd_amount": decision["usd_amount"], "fee_usd": decision["fee_usd"],
        "cash_after": cash_after, "xrp_after": xrp_after,
        "triggered_by_mention_id": mention_id, "reason": decision["reason"],
        "created_at": created_at.isoformat(),
    }


def apply_replay(db, result: dict) -> None:
    db.table("kanal_finans_trades").delete().neq("id", 0).execute()
    if result["trades"]:
        db.table("kanal_finans_trades").insert(result["trades"]).execute()
    db.table("kanal_finans_portfolio").update({
        "cash_usd": result["cash_usd"], "xrp_amount": result["xrp_amount"], "position": result["position"],
        "stop_loss_price": result["stop_loss_price"], "resistance_price": result["resistance_price"],
    }).eq("id", 1).execute()


def main() -> int:
    db = get_client()
    backfill_action_fields(db)

    mentions = _fetch_xrp_mentions(db)
    print(f"Replaying {len(mentions)} XRP mention(s) chronologically against real 15m candles...")
    result = replay(mentions)

    value = result["cash_usd"] + result["xrp_amount"] * (result.get("last_price") or 0)
    print(f"\n=== Kanal Finans TS takip-portfoyu: gecmis backfill sonucu ===")
    print(f"Islem sayisi: {len(result['trades'])}")
    print(f"Son durum: cash=${result['cash_usd']:.2f} xrp={result['xrp_amount']:.4f} ({result['position']})")
    print(f"Guncel deger (son bilinen fiyatla): ${value:.2f}  (baslangic: ${trading.STARTING_CASH:.2f})")
    for t in result["trades"]:
        print(f"  {t['created_at']}: {t['side']} {t['xrp_amount']:.4f} XRP @ {t['price']:.4f} -- {t['reason']}")

    apply_replay(db, result)
    print("\nBackfill complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

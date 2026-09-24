"""Entry point run once a day (18:10 Turkey time) by GitHub Actions.

Builds a plain-text summary of the last 24h (18:00 TRT -> 18:00 TRT: how
many predictions were made/resolved/correct, which signal component did
best that day, how the virtual portfolio's value and XRP price changed) and
emails it via Gmail SMTP using an App Password. No new tables are needed --
both the portfolio's and the price's value at the start of the window are
reconstructed from the existing `trades` and `predictions` tables.
"""
import os
import smtplib
import sys
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from db import get_client
import ensemble
from fetch_data import get_current_price
import kanal_finans_trading
import momentum_trading
from predict import SYMBOL, get_ensemble_state
import trading

TIMEZONE = timezone(timedelta(hours=3))  # Turkey: fixed UTC+3, no DST
TR_MONTHS = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz",
             "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
COMPONENT_LABELS = {"technical": "Teknik", "ml": "ML", "whale": "Balina", "news": "Haber", "orderbook": "Emir Defteri", "claude": "Claude"}

# Kanal Finans TŞ is the seventh $1000 paper portfolio. It is NOT one of
# trading.STRATEGIES (different tables, different decision engine -- see
# kanal_finans_trading.py), but it *is* directly comparable on the one axis
# the table below reports: what $1000 turned into. It has no prediction
# accuracy to show, since it makes no 15-minute directional calls.
KANAL_FINANS = "kanal_finans"
# The trend-following portfolio (momentum_trading.py) is the eighth, and in
# the same position: own tables, own engine, no 15-minute calls to score.
MOMENTUM = "momentum"
UNSCORED_STRATEGIES = (KANAL_FINANS, MOMENTUM)
REPORT_STRATEGIES = ("ensemble", *trading.STRATEGIES, KANAL_FINANS, MOMENTUM)
STRATEGY_LABELS = {"ensemble": "Ensemble (ana model)", "technical": "Teknik", "ml": "ML", "whale": "Balina",
                   "news": "Haber", "claude": "Claude", KANAL_FINANS: "Kanal Finans TŞ", MOMENTUM: "Trend Takip"}
# Deliberately different wording from the ensemble's English UP/DOWN labels --
# a stance is what Tunç Şatıroğlu said, not a prediction of ours (same
# distinction app.js:KANAL_FINANS_STANCE_LABELS makes in the UI).
STANCE_LABELS = {"UP": "Olumlu", "DOWN": "Olumsuz", "NEUTRAL": "Nötr"}
ACTION_LABELS = {"BUY": "AL", "SELL": "SAT", "HOLD": "TUT"}


def format_tr_date(dt: datetime) -> str:
    return f"{dt.day:02d} {TR_MONTHS[dt.month - 1]} {dt.year}"


def _published_label(published_at: str | None) -> str:
    """"01 Eylül 13:47" in Turkey time, from a stored ISO timestamp."""
    if not published_at:
        return "tarih yok"
    dt = datetime.fromisoformat(published_at).astimezone(TIMEZONE)
    return f"{dt.day:02d} {TR_MONTHS[dt.month - 1]} {dt.strftime('%H:%M')}"


def _level_text(price, label: str) -> str:
    """A level is only ever set when Tunç actually gave one; kanal_finans.py
    already maps its 0 "not mentioned" sentinel to NULL, so falsy means
    genuinely unknown here."""
    return f"{label} ${float(price):.4f}" if price else f"{label} yok"


def window_bounds(now_trt: datetime) -> tuple[datetime, datetime]:
    """Finds the most recent past 18:00 TRT mark as the window end (robust
    to a few minutes of cron delay, and to a manual test run at any time of
    day); the window is the 24h before that."""
    anchor = now_trt.replace(hour=18, minute=0, second=0, microsecond=0)
    if now_trt < anchor:
        anchor -= timedelta(days=1)
    return anchor - timedelta(days=1), anchor


def fetch_predictions_in_window(db, start: datetime, end: datetime) -> list[dict]:
    res = (
        db.table("predictions")
        .select("*")
        .gte("created_at", start.isoformat())
        .lt("created_at", end.isoformat())
        .execute()
    )
    return res.data


def component_accuracy(rows: list[dict], component: str) -> tuple[float | None, int]:
    """Same abstention rule as retrain.py: a row only counts if that
    component actually had a non-zero-confidence opinion."""
    prefix = ensemble.COLUMN_PREFIX[component]
    correct_key, conf_key = f"{prefix}_correct", f"{prefix}_confidence"
    values = [r[correct_key] for r in rows if r.get(correct_key) is not None and (r.get(conf_key) or 0) > 0]
    if not values:
        return None, 0
    return sum(1 for v in values if v) / len(values), len(values)


def _trades_table(strategy: str) -> str:
    """Each portfolio family logs to its own table: the ensemble to `trades`,
    the five single-signal strategies to a shared `strategy_trades` (keyed by
    a `strategy` column), Kanal Finans TŞ and the trend-following portfolio
    to their own `kanal_finans_trades`/`momentum_trades`."""
    if strategy == "ensemble":
        return "trades"
    if strategy == KANAL_FINANS:
        return "kanal_finans_trades"
    if strategy == MOMENTUM:
        return "momentum_trades"
    return "strategy_trades"


def live_portfolio_state(db, strategy: str) -> dict:
    if strategy == KANAL_FINANS:
        return kanal_finans_trading.get_portfolio_state(db)
    if strategy == MOMENTUM:
        return momentum_trading.get_portfolio_state(db)
    return trading.get_portfolio_state(db, strategy)


def portfolio_state_as_of(db, cutoff: datetime, strategy: str = "ensemble") -> tuple[float, float]:
    """Reconstructs (cash_usd, xrp_amount) as of `cutoff` from the most
    recent trade at or before it; STARTING_CASH/0 if there was none yet."""
    table = _trades_table(strategy)
    query = db.table(table).select("cash_after,xrp_after").lte("created_at", cutoff.isoformat())
    if table == "strategy_trades":
        query = query.eq("strategy", strategy)
    res = query.order("created_at", desc=True).limit(1).execute()
    if res.data:
        row = res.data[0]
        return float(row["cash_after"]), float(row["xrp_after"])
    return trading.STARTING_CASH, 0.0


def price_as_of(db, cutoff: datetime) -> float | None:
    """Approximates the XRP/USDT price at `cutoff` using the closest
    already-resolved prediction at or before it. Filters resolved_at
    client-side rather than chaining a `.not_.is_()` query -- postgrest-py's
    negation-filter chaining semantics have been unreliable here before."""
    res = (
        db.table("predictions")
        .select("resolved_at,price_at_resolution")
        .lte("target_time", cutoff.isoformat())
        .order("target_time", desc=True)
        .limit(8)
        .execute()
    )
    for row in res.data:
        if row.get("resolved_at") and row.get("price_at_resolution") is not None:
            return float(row["price_at_resolution"])
    return None


def trade_count_in_window(db, start: datetime, end: datetime, strategy: str = "ensemble") -> tuple[int, int, int]:
    table = _trades_table(strategy)
    query = (
        db.table(table).select("side")
        .gte("created_at", start.isoformat())
        .lt("created_at", end.isoformat())
    )
    if table == "strategy_trades":
        query = query.eq("strategy", strategy)
    res = query.execute()
    buys = sum(1 for r in res.data if r["side"] == "BUY")
    sells = sum(1 for r in res.data if r["side"] == "SELL")
    return len(res.data), buys, sells


def strategy_report(db, strategy: str, start: datetime, end: datetime,
                     price_start: float | None, price_now: float,
                     accuracy: float | None, accuracy_count: int) -> dict:
    """24h portfolio change + cumulative return since the $1000 start +
    today's trade count + today's prediction accuracy, for one strategy --
    lets a strategy's real (fee-inclusive) trading performance be judged
    against its raw prediction accuracy side by side, not just one or the
    other (a strategy can be "accurate" but still lose money to fees, as
    technical/whale have shown in backtests)."""
    cash_start, xrp_start = portfolio_state_as_of(db, start, strategy)
    value_start = cash_start + xrp_start * (price_start if price_start is not None else price_now)

    live_state = live_portfolio_state(db, strategy)
    cash_now, xrp_now = float(live_state["cash_usd"]), float(live_state["xrp_amount"])
    value_now = cash_now + xrp_now * price_now

    trade_total, buys, sells = trade_count_in_window(db, start, end, strategy)

    return {
        "value_start": value_start, "value_now": value_now,
        "xrp_now": xrp_now, "position": live_state["position"],
        "trade_total": trade_total, "buys": buys, "sells": sells,
        "accuracy": accuracy, "accuracy_count": accuracy_count,
    }


def kanal_finans_context(db, start: datetime, end: datetime) -> dict:
    """What Tunç Şatıroğlu said, plus the levels his portfolio is currently
    watching. Two different questions, so two different queries:

    - "what arrived today" is keyed on `created_at` (when *we* processed the
      video), not `published_at` -- a run blocked by YouTube's IP block for a
      day catches up later, and the mail should report it on the day it was
      actually ingested;
    - "what is his standing view on XRP" is the latest XRP mention regardless
      of date, because on a day with no new video (common -- the channel
      doesn't post daily, and the local scheduled task can be skipped while
      the machine is asleep) the *previous* call is still the one the
      portfolio is acting on. Without this the card would just say "nothing
      today" and carry no information at all.
    """
    new_mentions = (
        db.table("kanal_finans_mentions")
        .select("video_id,asset,stance,summary,video_title,published_at")
        .gte("created_at", start.isoformat())
        .lt("created_at", end.isoformat())
        .order("published_at", desc=True)
        .execute()
    ).data

    # Only the newest video's BTC/ETH/KRIPTO lines get printed. A normal day
    # brings one or two videos, but a day that catches up on a backlog (the
    # first run, or after YouTube's IP block clears) can bring a dozen -- and
    # dumping 37 stale one-liners into the mail buries the part that matters.
    # The counts above still report the full ingest.
    newest_video = new_mentions[0]["video_id"] if new_mentions else None
    other_assets = [m for m in new_mentions if m["video_id"] == newest_video and m["asset"] != "XRP"]

    latest_xrp = (
        db.table("kanal_finans_mentions")
        .select("stance,action,summary,video_title,published_at,stop_loss_price,resistance_price")
        .eq("asset", "XRP")
        .order("published_at", desc=True)
        .limit(1)
        .execute()
    ).data

    state = kanal_finans_trading.get_portfolio_state(db)
    return {
        "mention_count": len(new_mentions),
        "other_assets": other_assets,
        "new_videos": len({m["video_id"] for m in new_mentions}),
        "latest_xrp": latest_xrp[0] if latest_xrp else None,
        "stop_loss_price": state.get("stop_loss_price"),
        "resistance_price": state.get("resistance_price"),
        "position": state["position"],
    }


def build_report(db) -> dict:
    start, end = window_bounds(datetime.now(TIMEZONE))
    rows = fetch_predictions_in_window(db, start, end)
    resolved = [r for r in rows if r.get("resolved_at")]
    correct = [r for r in resolved if r.get("correct")]

    components = {c: component_accuracy(rows, c) for c in ensemble.COMPONENTS}

    price_start = price_as_of(db, start)
    price_now = get_current_price(SYMBOL)

    ensemble_accuracy = (len(correct) / len(resolved)) if resolved else None
    strategies = {
        "ensemble": strategy_report(db, "ensemble", start, end, price_start, price_now, ensemble_accuracy, len(resolved)),
    }
    for strategy in trading.STRATEGIES:
        acc, count = components[strategy]
        strategies[strategy] = strategy_report(db, strategy, start, end, price_start, price_now, acc, count)

    # Kanal Finans is a separate subsystem (own tables, own schema migration,
    # and its ingest runs on the user's machine rather than in CI) -- if any
    # of that is missing or hiccups, the rest of the mail must still go out,
    # so the whole section degrades to "absent" instead of failing the run.
    try:
        strategies[KANAL_FINANS] = strategy_report(
            db, KANAL_FINANS, start, end, price_start, price_now, accuracy=None, accuracy_count=0)
        kanal_finans = kanal_finans_context(db, start, end)
    except Exception as exc:  # noqa: BLE001 -- see comment above
        print(f"WARNING: Kanal Finans section skipped ({exc})")
        strategies.pop(KANAL_FINANS, None)
        kanal_finans = None

    # Same fail-soft reasoning: its own tables, and one missing row must not
    # cost the whole mail.
    try:
        strategies[MOMENTUM] = strategy_report(
            db, MOMENTUM, start, end, price_start, price_now, accuracy=None, accuracy_count=0)
    except Exception as exc:  # noqa: BLE001
        print(f"WARNING: momentum row skipped ({exc})")

    weights, _ = get_ensemble_state(db)  # rapor sadece gosterim ağırlıklarını kullanıyor

    return {
        "start": start, "end": end,
        "total_predictions": len(rows), "resolved": len(resolved), "correct": len(correct),
        "components": components,
        "strategies": strategies,
        "kanal_finans": kanal_finans,
        "price_start": price_start, "price_now": price_now,
        "weights": weights,
    }


def render_text(report: dict) -> str:
    lines = [
        "Merhaba Erdem,", "",
        f"Dün {report['start'].strftime('%H:%M')} - bugün {report['end'].strftime('%H:%M')} arasında:", "",
        "TAHMİNLER",
    ]
    if report["resolved"] > 0:
        acc_pct = report["correct"] / report["resolved"] * 100
        lines.append(
            f"- {report['total_predictions']} tahmin yapıldı, {report['resolved']}'i sonuçlandı, "
            f"%{acc_pct:.1f} isabet ({report['correct']}/{report['resolved']} doğru)"
        )
    else:
        lines.append(f"- {report['total_predictions']} tahmin yapıldı, henüz sonuçlanan yok")

    acc_o, count_o = report["components"]["orderbook"]
    orderbook_text = f"%{acc_o * 100:.1f} ({count_o})" if acc_o is not None else "yeterli veri yok"
    lines.append(f"- Emir defteri (kendi portföyü yok): {orderbook_text}")

    present = [s for s in REPORT_STRATEGIES if s in report["strategies"]]
    lines += ["", f"{len(present)} PORTFÖY PERFORMANSI (her biri kendi $1000 ile)"]
    best_name, best_pct = None, float("-inf")
    for strategy in present:
        s = report["strategies"][strategy]
        label = STRATEGY_LABELS[strategy]
        if s["accuracy"] is not None:
            acc_text = f"%{s['accuracy'] * 100:.0f} isabet ({s['accuracy_count']})"
        else:
            # Kanal Finans/Trend Takip make no 15-minute directional calls, so
            # there is nothing to score -- different from "no data yet".
            acc_text = "isabet ölçülmüyor" if strategy in UNSCORED_STRATEGIES else "isabet: veri yok"
        today_pct = (s["value_now"] - s["value_start"]) / s["value_start"] * 100 if s["value_start"] else 0.0
        total_pct = (s["value_now"] - trading.STARTING_CASH) / trading.STARTING_CASH * 100
        lines.append(
            f"- {label}: {acc_text} | ${s['value_now']:.2f} ({today_pct:+.2f}% bugün, {total_pct:+.2f}% toplam) "
            f"| {s['trade_total']} işlem ({s['buys']} AL, {s['sells']} SAT)"
        )
        if today_pct > best_pct:
            best_name, best_pct = label, today_pct
    if best_name is not None:
        lines.append(f"- Bugün en çok kazanan: {best_name} ({best_pct:+.2f}%)")

    lines += ["", "XRP/USDT FİYATI"]
    if report["price_start"] is not None:
        price_change_pct = (report["price_now"] - report["price_start"]) / report["price_start"] * 100
        lines.append(f"- Dün {report['start'].strftime('%H:%M')}: ${report['price_start']:.4f}")
        lines.append(f"- Bugün {report['end'].strftime('%H:%M')}: ${report['price_now']:.4f} ({price_change_pct:+.2f}%)")
    else:
        lines.append(f"- Önceki fiyat verisi yok, güncel: ${report['price_now']:.4f}")

    kf = report.get("kanal_finans")
    if kf is not None:
        lines += ["", "KANAL FİNANS TŞ (Tunç Şatıroğlu'nun söyledikleri -- bizim tahminimiz değil)"]
        lines.append(f"- Bugün işlenen video: {kf['new_videos']} ({kf['mention_count']} kripto bahsi)")
        latest = kf["latest_xrp"]
        if latest is not None:
            published = _published_label(latest.get("published_at"))
            stance = STANCE_LABELS.get(latest["stance"], latest["stance"])
            action = ACTION_LABELS.get(latest.get("action"), "—")
            lines.append(f"- XRP görüşü ({published}): {stance} / {action} -- {latest['summary']}")
            lines.append(f"  Video: {latest.get('video_title') or '-'}")
        else:
            lines.append("- Henüz XRP hakkında bir görüş kaydedilmedi")
        lines.append(f"- İzlenen seviyeler: {_level_text(kf['stop_loss_price'], 'zarar-kes')} | "
                     f"{_level_text(kf['resistance_price'], 'direnç')} (direnç otomatik satış tetiklemez)")
        for m in kf["other_assets"]:
            lines.append(f"- {m['asset']}: {STANCE_LABELS.get(m['stance'], m['stance'])} -- {m['summary']}")

    w = report["weights"]
    lines += ["", "GÜNCEL ENSEMBLE AĞIRLIKLARI"]
    lines.append("- " + " | ".join(f"{COMPONENT_LABELS[c]} %{w[c] * 100:.1f}" for c in ensemble.COMPONENTS))

    lines += ["", "Bu bir yatırım tavsiyesi değildir."]
    return "\n".join(lines)


# Matches frontend/style.css's dark theme so the email feels like the same product.
BG, CARD, CARD_HEAD, BORDER = "#0b0f14", "#131920", "#1b232d", "#232c37"
TEXT, MUTED, UP, DOWN = "#e8edf2", "#8a97a6", "#2ecc71", "#e74c3c"


def _row(label: str, value_html: str) -> str:
    return (
        f'<tr><td style="padding:9px 16px;font-size:13px;color:{MUTED};border-top:1px solid {BORDER};">{label}</td>'
        f'<td style="padding:9px 16px;font-size:13px;text-align:right;border-top:1px solid {BORDER};">{value_html}</td></tr>'
    )


def _card(title: str, rows: list[str]) -> str:
    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="background:{CARD};border-radius:10px;overflow:hidden;margin:0 0 14px;border:1px solid {BORDER};">'
        f'<tr><td colspan="2" style="padding:10px 16px;background:{CARD_HEAD};'
        f'font-size:12px;font-weight:700;letter-spacing:.04em;color:{TEXT};">{title}</td></tr>'
        f'{"".join(rows)}</table>'
    )


def _pct_span(pct: float) -> str:
    color = UP if pct >= 0 else DOWN
    return f'<span style="color:{color};font-weight:600;">{pct:+.2f}%</span>'


def _strategy_table(report: dict) -> str:
    """One row per paper portfolio -- accuracy next to real fee-inclusive
    portfolio return, since they can diverge (an "accurate" strategy can
    still lose money to fees, as backtests have shown for technical/whale).
    Kanal Finans TŞ and Trend Takip are listed here too: they make no
    directional calls so their accuracy cells stay empty, but "what did $1000
    become" is the one axis on which all eight portfolios are directly
    comparable."""
    headers = ("Strateji", "İsabet", "Değer", "Bugün", "Toplam", "İşlem")
    header_html = "".join(
        f'<th style="padding:8px 10px;font-size:11px;color:{MUTED};text-align:left;'
        f'border-bottom:1px solid {BORDER};white-space:nowrap;">{h}</th>'
        for h in headers
    )

    body_rows = []
    best_name, best_pct = None, float("-inf")
    present = [s for s in REPORT_STRATEGIES if s in report["strategies"]]
    for strategy in present:
        s = report["strategies"][strategy]
        label = STRATEGY_LABELS[strategy]
        acc_html = f'%{s["accuracy"] * 100:.0f} <span style="color:{MUTED};">({s["accuracy_count"]})</span>' if s["accuracy"] is not None else f'<span style="color:{MUTED};">—</span>'
        today_pct = (s["value_now"] - s["value_start"]) / s["value_start"] * 100 if s["value_start"] else 0.0
        total_pct = (s["value_now"] - trading.STARTING_CASH) / trading.STARTING_CASH * 100
        if today_pct > best_pct:
            best_name, best_pct = label, today_pct

        cells = [
            label,
            acc_html,
            f"${s['value_now']:.2f}",
            _pct_span(today_pct),
            _pct_span(total_pct),
            f'<span style="color:{MUTED};">{s["trade_total"]}</span>',
        ]
        tds = "".join(
            f'<td style="padding:8px 10px;font-size:12px;border-top:1px solid {BORDER};white-space:nowrap;">{c}</td>'
            for c in cells
        )
        body_rows.append(f"<tr>{tds}</tr>")

    note = f'<tr><td colspan="6" style="padding:6px 10px 10px;font-size:11px;color:{MUTED};">Bugün en çok kazanan: <span style="color:{TEXT};">{best_name}</span> ({_pct_span(best_pct)})</td></tr>' if best_name else ""

    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="background:{CARD};border-radius:10px;overflow:hidden;margin:0 0 14px;border:1px solid {BORDER};">'
        f'<tr><td colspan="6" style="padding:10px 16px;background:{CARD_HEAD};'
        f'font-size:12px;font-weight:700;letter-spacing:.04em;color:{TEXT};">📊 {len(present)} PORTFÖY PERFORMANSI '
        f'<span style="font-weight:400;color:{MUTED};">(her biri kendi $1000 ile)</span></td></tr>'
        f'<tr>{header_html}</tr>{"".join(body_rows)}{note}</table>'
    )


def _kanal_finans_card(kf: dict) -> str:
    """What the speaker said, not what we predict -- so it gets its own card
    with Turkish stance wording rather than being folded into the ensemble's
    UP/DOWN vocabulary (same separation the UI makes)."""
    rows = [_row("Bugün işlenen video", f"{kf['new_videos']} <span style=\"color:{MUTED};\">({kf['mention_count']} kripto bahsi)</span>")]

    latest = kf["latest_xrp"]
    if latest is not None:
        stance = STANCE_LABELS.get(latest["stance"], latest["stance"])
        stance_color = {"UP": UP, "DOWN": DOWN}.get(latest["stance"], MUTED)
        action = ACTION_LABELS.get(latest.get("action"), "—")
        rows.append(_row(
            f"XRP görüşü <span style=\"color:{MUTED};\">({_published_label(latest.get('published_at'))})</span>",
            f'<span style="color:{stance_color};font-weight:600;">{stance}</span> '
            f'<span style="color:{MUTED};">/ {action}</span>',
        ))
        rows.append(
            f'<tr><td colspan="2" style="padding:2px 16px 10px;font-size:12px;color:{TEXT};line-height:1.45;">'
            f'{latest["summary"]}<div style="color:{MUTED};font-size:11px;margin-top:4px;">'
            f'{latest.get("video_title") or ""}</div></td></tr>'
        )
    else:
        rows.append(_row("XRP görüşü", f'<span style="color:{MUTED};">henüz yok</span>'))

    rows.append(_row(
        "İzlenen seviyeler",
        f'{_level_text(kf["stop_loss_price"], "Zarar-kes")} '
        f'<span style="color:{MUTED};">·</span> {_level_text(kf["resistance_price"], "direnç")}',
    ))
    # Only the stop-loss auto-trades; a resistance break is shown but never
    # acted on (see kanal_finans_trading.py -- he sometimes calls it bullish).
    rows.append(
        f'<tr><td colspan="2" style="padding:0 16px 10px;font-size:11px;color:{MUTED};">'
        f'Direnç seviyesi otomatik satış tetiklemez, yalnızca bilgi amaçlıdır.</td></tr>'
    )

    for m in kf["other_assets"]:
        stance = STANCE_LABELS.get(m["stance"], m["stance"])
        stance_color = {"UP": UP, "DOWN": DOWN}.get(m["stance"], MUTED)
        rows.append(_row(
            m["asset"],
            # The summary is a sentence, not a number -- left-align it even
            # though _row()'s value column is right-aligned for figures.
            f'<span style="color:{stance_color};font-weight:600;">{stance}</span>'
            f'<div style="color:{MUTED};font-size:11px;margin-top:3px;text-align:left;line-height:1.4;">{m["summary"]}</div>',
        ))

    title = f'📺 KANAL FİNANS TŞ <span style="font-weight:400;color:{MUTED};">(Tunç Şatıroğlu\'nun görüşü)</span>'
    return _card(title, rows)


def render_html(report: dict) -> str:
    pred_rows = []
    if report["resolved"] > 0:
        acc_pct = report["correct"] / report["resolved"] * 100
        acc_color = UP if acc_pct >= 50 else DOWN
        pred_rows.append(_row("Yapılan / Sonuçlanan", f"{report['total_predictions']} / {report['resolved']}"))
        pred_rows.append(_row(
            "İsabet oranı",
            f'<span style="color:{acc_color};font-weight:600;">%{acc_pct:.1f}</span> '
            f'<span style="color:{MUTED};">({report["correct"]}/{report["resolved"]})</span>',
        ))
    else:
        pred_rows.append(_row("Yapılan", f"{report['total_predictions']} (henüz sonuçlanan yok)"))

    acc_o, count_o = report["components"]["orderbook"]
    orderbook_text = f"%{acc_o * 100:.1f} <span style=\"color:{MUTED};\">({count_o})</span>" if acc_o is not None else f'<span style="color:{MUTED};">veri yok</span>'
    pred_rows.append(_row("Emir defteri (kendi portföyü yok)", orderbook_text))

    if report["price_start"] is not None:
        price_pct = (report["price_now"] - report["price_start"]) / report["price_start"] * 100
        price_rows = [
            _row("Dün 18:00", f"${report['price_start']:.4f}"),
            _row("Bugün 18:00", f"${report['price_now']:.4f} {_pct_span(price_pct)}"),
        ]
    else:
        price_rows = [_row("Güncel", f"${report['price_now']:.4f} <span style=\"color:{MUTED};\">(önceki veri yok)</span>")]

    w = report["weights"]
    # Negatif ağırlık = bileşen ölçülen geçmişinde sürekli yanılmış ve harmanda
    # TERS okunuyor (bkz. ensemble.influence_weights). Etkisi gerçek, yönü
    # söylediğinin tersi -- işareti göstermezsek en çok yanılan bileşen en
    # güvenilir gibi görünür.
    weight_rows = [_row(
        "Dağılım",
        " · ".join(
            f"{COMPONENT_LABELS[c]} %{abs(w[c]) * 100:.1f}" + (" (ters)" if w[c] < 0 else "")
            for c in ensemble.COMPONENTS
        ),
    )]

    kf = report.get("kanal_finans")
    body = (
        _card("📊 TAHMİNLER", pred_rows)
        + _strategy_table(report)
        + (_kanal_finans_card(kf) if kf is not None else "")
        + _card("💹 XRP/USDT FİYATI", price_rows)
        + _card("⚖️ ENSEMBLE AĞIRLIKLARI", weight_rows)
    )

    return f"""\
<!doctype html>
<html><body style="margin:0;padding:0;background:{BG};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{BG};padding:24px 0;">
<tr><td align="center">
<table role="presentation" width="600" cellpadding="0" cellspacing="0"
       style="width:600px;max-width:100%;font-family:-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:{TEXT};">
<tr><td style="padding:0 20px 6px;">
  <div style="font-size:20px;font-weight:700;">XRP-Guess</div>
  <div style="font-size:13px;color:{MUTED};margin-top:2px;">Günlük Özet &mdash; {format_tr_date(report['end'])}</div>
</td></tr>
<tr><td style="padding:14px 20px 4px;font-size:14px;">Merhaba Erdem,</td></tr>
<tr><td style="padding:0 20px 16px;font-size:13px;color:{MUTED};">
  Dün {report['start'].strftime('%H:%M')} &ndash; bugün {report['end'].strftime('%H:%M')} arasında:
</td></tr>
<tr><td style="padding:0 20px;">{body}</td></tr>
<tr><td style="padding:4px 20px 0;font-size:11px;color:{MUTED};">Bu bir yatırım tavsiyesi değildir.</td></tr>
</table>
</td></tr>
</table>
</body></html>
"""


def render_email(report: dict) -> tuple[str, str, str]:
    subject = f"XRP-Guess - Günlük Özet ({format_tr_date(report['end'])})"
    return subject, render_text(report), render_html(report)


def send_email(subject: str, text_body: str, html_body: str) -> None:
    address = os.environ["GMAIL_ADDRESS"]
    app_password = os.environ["GMAIL_APP_PASSWORD"]
    recipient = os.environ.get("REPORT_RECIPIENT", address)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = address
    msg["To"] = recipient
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(address, app_password)
        server.sendmail(address, [recipient], msg.as_string())


def main() -> int:
    db = get_client()
    report = build_report(db)
    subject, text_body, html_body = render_email(report)
    print(text_body)
    send_email(subject, text_body, html_body)
    print("Daily report email sent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

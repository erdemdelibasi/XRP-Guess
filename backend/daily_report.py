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
from predict import SYMBOL, get_ensemble_weights
import trading

TIMEZONE = timezone(timedelta(hours=3))  # Turkey: fixed UTC+3, no DST
TR_MONTHS = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz",
             "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
COMPONENT_LABELS = {"technical": "Teknik", "ml": "ML", "whale": "Balina", "news": "Haber", "orderbook": "Emir Defteri", "claude": "Claude"}
STRATEGY_LABELS = {"ensemble": "Ensemble (ana model)", "technical": "Teknik", "ml": "ML", "whale": "Balina", "news": "Haber", "claude": "Claude"}


def format_tr_date(dt: datetime) -> str:
    return f"{dt.day:02d} {TR_MONTHS[dt.month - 1]} {dt.year}"


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


def portfolio_state_as_of(db, cutoff: datetime, strategy: str = "ensemble") -> tuple[float, float]:
    """Reconstructs (cash_usd, xrp_amount) as of `cutoff` from the most
    recent trade at or before it; STARTING_CASH/0 if there was none yet.
    `strategy`: "ensemble" reads `trades`, any of trading.STRATEGIES reads
    `strategy_trades` filtered to that strategy."""
    table = "trades" if strategy == "ensemble" else "strategy_trades"
    query = db.table(table).select("cash_after,xrp_after").lte("created_at", cutoff.isoformat())
    if strategy != "ensemble":
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
    table = "trades" if strategy == "ensemble" else "strategy_trades"
    query = (
        db.table(table).select("side")
        .gte("created_at", start.isoformat())
        .lt("created_at", end.isoformat())
    )
    if strategy != "ensemble":
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

    live_state = trading.get_portfolio_state(db, strategy)
    cash_now, xrp_now = float(live_state["cash_usd"]), float(live_state["xrp_amount"])
    value_now = cash_now + xrp_now * price_now

    trade_total, buys, sells = trade_count_in_window(db, start, end, strategy)

    return {
        "value_start": value_start, "value_now": value_now,
        "xrp_now": xrp_now, "position": live_state["position"],
        "trade_total": trade_total, "buys": buys, "sells": sells,
        "accuracy": accuracy, "accuracy_count": accuracy_count,
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

    weights = get_ensemble_weights(db)

    return {
        "start": start, "end": end,
        "total_predictions": len(rows), "resolved": len(resolved), "correct": len(correct),
        "components": components,
        "strategies": strategies,
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

    lines += ["", "5 STRATEJİ PERFORMANSI (her biri kendi $1000 ile)"]
    best_name, best_pct = None, float("-inf")
    for strategy in ("ensemble", *trading.STRATEGIES):
        s = report["strategies"][strategy]
        label = STRATEGY_LABELS[strategy]
        acc_text = f"%{s['accuracy'] * 100:.0f} isabet ({s['accuracy_count']})" if s["accuracy"] is not None else "isabet: veri yok"
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
    """5-column comparison table (one row per strategy) -- accuracy next to
    real fee-inclusive portfolio return, since they can diverge (an
    "accurate" strategy can still lose money to fees, as backtests have
    shown for technical/whale)."""
    headers = ("Strateji", "İsabet", "Değer", "Bugün", "Toplam", "İşlem")
    header_html = "".join(
        f'<th style="padding:8px 10px;font-size:11px;color:{MUTED};text-align:left;'
        f'border-bottom:1px solid {BORDER};white-space:nowrap;">{h}</th>'
        for h in headers
    )

    body_rows = []
    best_name, best_pct = None, float("-inf")
    for strategy in ("ensemble", *trading.STRATEGIES):
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
        f'font-size:12px;font-weight:700;letter-spacing:.04em;color:{TEXT};">📊 5 STRATEJİ PERFORMANSI '
        f'<span style="font-weight:400;color:{MUTED};">(her biri kendi $1000 ile)</span></td></tr>'
        f'<tr>{header_html}</tr>{"".join(body_rows)}{note}</table>'
    )


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
    weight_rows = [_row(
        "Dağılım",
        " · ".join(f"{COMPONENT_LABELS[c]} %{w[c] * 100:.1f}" for c in ensemble.COMPONENTS),
    )]

    body = (
        _card("📊 TAHMİNLER", pred_rows)
        + _strategy_table(report)
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
  <div style="font-size:20px;font-weight:700;">XRP Tahmin Paneli</div>
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
    subject = f"XRP Tahmin Paneli - Günlük Özet ({format_tr_date(report['end'])})"
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

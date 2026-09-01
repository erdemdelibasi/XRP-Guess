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
from email.mime.text import MIMEText

from db import get_client
import ensemble
from fetch_data import get_current_price
from predict import SYMBOL, get_ensemble_weights
import trading

TIMEZONE = timezone(timedelta(hours=3))  # Turkey: fixed UTC+3, no DST
TR_MONTHS = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz",
             "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
COMPONENT_LABELS = {"technical": "Teknik", "ml": "ML", "whale": "Balina", "news": "Haber"}
MIN_SAMPLES_FOR_BEST = 5  # below this, a high accuracy is just noise -- don't crown it "best of the day"


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


def portfolio_state_as_of(db, cutoff: datetime) -> tuple[float, float]:
    """Reconstructs (cash_usd, xrp_amount) as of `cutoff` from the most
    recent trade at or before it; STARTING_CASH/0 if there was none yet."""
    res = (
        db.table("trades")
        .select("cash_after,xrp_after")
        .lte("created_at", cutoff.isoformat())
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
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


def trade_count_in_window(db, start: datetime, end: datetime) -> tuple[int, int, int]:
    res = (
        db.table("trades")
        .select("side")
        .gte("created_at", start.isoformat())
        .lt("created_at", end.isoformat())
        .execute()
    )
    buys = sum(1 for r in res.data if r["side"] == "BUY")
    sells = sum(1 for r in res.data if r["side"] == "SELL")
    return len(res.data), buys, sells


def build_report(db) -> dict:
    start, end = window_bounds(datetime.now(TIMEZONE))
    rows = fetch_predictions_in_window(db, start, end)
    resolved = [r for r in rows if r.get("resolved_at")]
    correct = [r for r in resolved if r.get("correct")]

    components = {c: component_accuracy(rows, c) for c in ensemble.COMPONENTS}

    cash_start, xrp_start = portfolio_state_as_of(db, start)
    price_start = price_as_of(db, start)
    price_now = get_current_price(SYMBOL)

    live_state = trading.get_portfolio_state(db)
    cash_now, xrp_now = float(live_state["cash_usd"]), float(live_state["xrp_amount"])
    value_now = cash_now + xrp_now * price_now
    value_start = cash_start + xrp_start * (price_start if price_start is not None else price_now)

    trade_total, buys, sells = trade_count_in_window(db, start, end)
    weights = get_ensemble_weights(db)

    return {
        "start": start, "end": end,
        "total_predictions": len(rows), "resolved": len(resolved), "correct": len(correct),
        "components": components,
        "value_start": value_start, "value_now": value_now,
        "xrp_now": xrp_now, "position": live_state["position"],
        "price_start": price_start, "price_now": price_now,
        "trade_total": trade_total, "buys": buys, "sells": sells,
        "weights": weights,
    }


def render_email(report: dict) -> tuple[str, str]:
    subject = f"XRP Tahmin Paneli - Günlük Özet ({format_tr_date(report['end'])})"

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

    comp_lines = []
    best_name, best_acc = None, -1.0
    for c in ensemble.COMPONENTS:
        acc, count = report["components"][c]
        label = COMPONENT_LABELS[c]
        if acc is None:
            comp_lines.append(f"{label}: yeterli veri yok")
        else:
            comp_lines.append(f"{label} %{acc * 100:.1f} ({count})")
            if count >= MIN_SAMPLES_FOR_BEST and acc > best_acc:
                best_name, best_acc = label, acc
    if best_name is not None:
        lines.append(f"- Bugün en başarılı bileşen: {best_name} (%{best_acc * 100:.1f})")
    lines.append(f"- Bileşenler: {', '.join(comp_lines)}")

    lines += ["", "SANAL PORTFÖY"]
    ret_pct = (report["value_now"] - report["value_start"]) / report["value_start"] * 100 if report["value_start"] else 0.0
    lines.append(f"- Bakiye: ${report['value_start']:.2f} -> ${report['value_now']:.2f} ({ret_pct:+.2f}%)")
    lines.append(f"- Şu an elde: {report['xrp_now']:.2f} XRP ({report['position']} pozisyonda)")
    lines.append(f"- Bugün {report['trade_total']} işlem yapıldı ({report['buys']} AL, {report['sells']} SAT)")

    lines += ["", "XRP/USDT FİYATI"]
    if report["price_start"] is not None:
        price_change_pct = (report["price_now"] - report["price_start"]) / report["price_start"] * 100
        lines.append(f"- Dün {report['start'].strftime('%H:%M')}: ${report['price_start']:.4f}")
        lines.append(f"- Bugün {report['end'].strftime('%H:%M')}: ${report['price_now']:.4f} ({price_change_pct:+.2f}%)")
    else:
        lines.append(f"- Önceki fiyat verisi yok, güncel: ${report['price_now']:.4f}")

    w = report["weights"]
    lines += ["", "GÜNCEL ENSEMBLE AĞIRLIKLARI"]
    lines.append(
        f"- Teknik %{w['technical'] * 100:.1f} | ML %{w['ml'] * 100:.1f} | "
        f"Balina %{w['whale'] * 100:.1f} | Haber %{w['news'] * 100:.1f}"
    )

    lines += ["", "Bu bir yatırım tavsiyesi değildir."]
    return subject, "\n".join(lines)


def send_email(subject: str, body: str) -> None:
    address = os.environ["GMAIL_ADDRESS"]
    app_password = os.environ["GMAIL_APP_PASSWORD"]
    recipient = os.environ.get("REPORT_RECIPIENT", address)

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = address
    msg["To"] = recipient

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(address, app_password)
        server.sendmail(address, [recipient], msg.as_string())


def main() -> int:
    db = get_client()
    report = build_report(db)
    subject, body = render_email(report)
    print(body)
    send_email(subject, body)
    print("Daily report email sent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

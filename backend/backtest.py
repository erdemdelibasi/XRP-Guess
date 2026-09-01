"""One-off historical backtest -- NOT run on a schedule, NOT a live signal.
Run manually (locally or via workflow_dispatch) to sanity-check the
technical+ML ensemble and the paper-trading strategy against real history.
Touches no database.

IMPORTANT LIMITATIONS (also printed in the report, not hidden):
- Only the technical and ML components are backtested. whale/news/orderbook
  are live-only signals with no free historical archive to replay, so this
  approximates the ensemble as technical+ML only (fixed 50/50 between just
  the two) -- something the real live system, with five components, never
  actually runs as-is.
- The ML model is trained ONCE on the first TRAIN_FRACTION of the window and
  evaluated out-of-sample on the rest. Production retrains daily; that
  rolling retraining loop is not replayed here.
- Ensemble weights are fixed at 50/50 rather than the real self-adjusting
  trajectory retrain.py would have produced day by day.
"""
import math
import sys

import ensemble
from fetch_data import get_klines_history
from indicators import add_cross_asset_correlation, add_indicator_columns, technical_signal
import ml_model
import trading

SYMBOL = "XRPUSDT"
BTC_SYMBOL = "BTCUSDT"
ETH_SYMBOL = "ETHUSDT"
INTERVAL = "15m"
BACKTEST_CANDLES = 20000  # ~208 days of 15-min candles -- wide enough to span multiple market regimes
TRAIN_FRACTION = 0.7
FEATURE_LIMIT = 400  # mirrors predict.py's live window -- fixed cost per step, no lookahead
WARMUP_CANDLES = 50 * 4  # ~50h so slow indicators (sma50 etc.) aren't NaN at test start
BACKTEST_WEIGHTS = {"technical": 0.5, "ml": 0.5}
CANDLES_PER_DAY = 96  # 15-min candles


def _trailing_window(df, end_idx: int, limit: int = FEATURE_LIMIT):
    start_idx = max(0, end_idx - limit + 1)
    return df.iloc[start_idx:end_idx + 1]


def _log_loss(prob_up: float, actual_up: float) -> float:
    eps = 1e-9
    p = min(max(prob_up, eps), 1 - eps)
    return -(actual_up * math.log(p) + (1 - actual_up) * math.log(1 - p))


def _reliability_table(records: list[tuple[float, bool]], n_bins: int = 10) -> list[dict]:
    """Buckets predictions by confidence and compares empirical accuracy against
    the accuracy that confidence level itself implies (a well-calibrated
    confidence of c means the predicted side should be right 0.5+c/2 of the
    time, since confidence is |prob_up - 0.5| * 2). This is a reliability
    diagram rendered as a table instead of a plot."""
    bins: list[list[tuple[float, bool]]] = [[] for _ in range(n_bins)]
    for confidence, correct in records:
        idx = min(int(confidence * n_bins), n_bins - 1)
        bins[idx].append((confidence, correct))

    rows = []
    for i, bucket in enumerate(bins):
        if not bucket:
            continue
        avg_confidence = sum(c for c, _ in bucket) / len(bucket)
        rows.append({
            "range": f"%{i * 100 // n_bins}-{(i + 1) * 100 // n_bins}",
            "count": len(bucket),
            "avg_confidence": avg_confidence,
            "implied_accuracy": 0.5 + avg_confidence / 2,
            "empirical_accuracy": sum(1 for _, correct in bucket if correct) / len(bucket),
        })
    return rows


def run_backtest() -> None:
    print(f"Fetching {BACKTEST_CANDLES} candles (~{BACKTEST_CANDLES / CANDLES_PER_DAY:.0f} days) of XRP/BTC/ETH history...")
    xrp_raw = get_klines_history(SYMBOL, interval=INTERVAL, total=BACKTEST_CANDLES)
    btc_raw = get_klines_history(BTC_SYMBOL, interval=INTERVAL, total=BACKTEST_CANDLES)
    eth_raw = get_klines_history(ETH_SYMBOL, interval=INTERVAL, total=BACKTEST_CANDLES)
    n = min(len(xrp_raw), len(btc_raw), len(eth_raw))
    xrp_raw, btc_raw, eth_raw = xrp_raw.iloc[:n].reset_index(drop=True), btc_raw.iloc[:n].reset_index(drop=True), eth_raw.iloc[:n].reset_index(drop=True)

    split = int(n * TRAIN_FRACTION)
    print(f"Training ML model on first {split} candles ({TRAIN_FRACTION * 100:.0f}%)...")
    model, train_metrics = ml_model.train_model(xrp_raw.iloc[:split])
    print(f"Train metrics: {train_metrics}")

    test_start = split + WARMUP_CANDLES
    if test_start >= n - 1:
        raise ValueError("Not enough candles for a warmed-up test window -- increase BACKTEST_CANDLES.")

    cash, xrp, peak_value = trading.STARTING_CASH, 0.0, trading.STARTING_CASH
    cooldown_remaining = 0
    max_drawdown_pct = 0.0
    trade_log = []
    correct_count, resolved_count = 0, 0
    brier_records: list[tuple[float, float]] = []  # (prob_up, actual_up)
    calibration_records: list[tuple[float, bool]] = []  # (confidence, was_correct)

    entry_price = float(xrp_raw["close"].iloc[test_start])
    baseline_xrp = trading.STARTING_CASH * (1 - trading.FEE_RATE) / entry_price

    print(f"Replaying {n - 1 - test_start} steps from candle {test_start} to {n - 2}...")
    for i in range(test_start, n - 1):
        xrp_window = _trailing_window(xrp_raw, i)
        btc_window = _trailing_window(btc_raw, i)
        eth_window = _trailing_window(eth_raw, i)

        feat = add_indicator_columns(xrp_window)
        feat = add_cross_asset_correlation(feat, btc_window, "btc")
        feat = add_cross_asset_correlation(feat, eth_window, "eth")

        tech = technical_signal(feat)
        ml = ml_model.ml_signal(model, feat)
        final = ensemble.combine({"technical": tech, "ml": ml}, BACKTEST_WEIGHTS)

        price = float(xrp_window["close"].iloc[-1])

        decision = trading.compute_rebalance(cash, xrp, price, final["direction"], final["confidence"], peak_value, cooldown_remaining)
        value_before = cash + xrp * price
        peak_value = decision["new_peak_value"]
        cooldown_remaining = decision["new_cooldown_remaining"]
        drawdown = (peak_value - value_before) / peak_value if peak_value > 0 else 0.0
        max_drawdown_pct = max(max_drawdown_pct, drawdown)

        if decision["action"] == "BUY":
            gross = decision["usd_amount"]
            fee = gross * trading.FEE_RATE
            xrp += (gross - fee) / price
            cash -= gross
            trade_log.append(("BUY", decision["reason"]))
        elif decision["action"] == "SELL":
            amt = decision["xrp_amount"]
            gross = amt * price
            fee = gross * trading.FEE_RATE
            cash += gross - fee
            xrp -= amt
            trade_log.append(("SELL", decision["reason"]))

        next_price = float(xrp_raw["close"].iloc[i + 1])
        actual_direction = "UP" if next_price > price else "DOWN"
        resolved_count += 1
        is_correct = actual_direction == final["direction"]
        if is_correct:
            correct_count += 1

        # final["score"] is a weighted average of component scores each already
        # bounded to [-1, 1], so it stays in [-1, 1] -- safe to remap to a 0..1
        # probability of UP without extra clipping.
        prob_up = 0.5 + final["score"] / 2
        actual_up = 1.0 if actual_direction == "UP" else 0.0
        brier_records.append((prob_up, actual_up))
        calibration_records.append((final["confidence"], is_correct))

    final_price = float(xrp_raw["close"].iloc[n - 1])
    final_value = cash + xrp * final_price
    buy_hold_value = baseline_xrp * final_price

    strategy_return_pct = (final_value - trading.STARTING_CASH) / trading.STARTING_CASH * 100
    buy_hold_return_pct = (buy_hold_value - trading.STARTING_CASH) / trading.STARTING_CASH * 100
    accuracy_pct = correct_count / resolved_count * 100 if resolved_count else 0.0
    buy_count = sum(1 for side, _ in trade_log if side == "BUY")
    sell_count = len(trade_log) - buy_count
    stop_loss_count = sum(1 for _, reason in trade_log if reason == "Stop-loss tetiklendi")

    brier = sum((p - o) ** 2 for p, o in brier_records) / len(brier_records)
    log_loss = sum(_log_loss(p, o) for p, o in brier_records) / len(brier_records)
    up_rate = sum(o for _, o in brier_records) / len(brier_records)
    # Climatology baseline: the Brier score you'd get by always predicting the
    # empirical UP rate instead of a per-step opinion -- i.e. the score of a
    # model with zero actual skill. Brier Skill Score compares against this,
    # not against 0.25, since up_rate isn't guaranteed to be exactly 0.5.
    climatology_brier = sum((up_rate - o) ** 2 for _, o in brier_records) / len(brier_records)
    brier_skill_score = 1 - brier / climatology_brier if climatology_brier > 0 else 0.0

    print()
    print("=" * 64)
    print("BACKTEST SONUCU (sadece teknik + ML, 50/50 sabit agirlik)")
    print("=" * 64)
    print(f"Test penceresi: {resolved_count} mum (~{resolved_count / CANDLES_PER_DAY:.1f} gun)")
    print(f"Yon isabet orani: %{accuracy_pct:.1f} ({correct_count}/{resolved_count})")
    print(f"Islem sayisi: {len(trade_log)} ({buy_count} AL, {sell_count} SAT, {stop_loss_count} stop-loss)")
    print(f"Maksimum dusus (tepe noktasindan): %{max_drawdown_pct * 100:.1f}")
    print()
    print(f"Brier score: {brier:.4f} (dusuk=iyi; taban-orani referansi {climatology_brier:.4f})")
    print(f"Log-loss: {log_loss:.4f}")
    print(f"Brier Skill Score (taban-oranina karsi): {brier_skill_score:+.4f} "
          f"(0=taban orani kadar bilgisiz, >0=daha iyi, <0=daha kotu)")
    print()
    print("KALIBRASYON (guven araligina gore, reliability diagram):")
    print(f"{'Aralik':<10}{'N':>6}  {'Ort. guven':>10}  {'Ima edilen isabet':>18}  {'Gercek isabet':>14}")
    for row in _reliability_table(calibration_records):
        print(f"{row['range']:<10}{row['count']:>6}  {row['avg_confidence'] * 100:>9.1f}%  "
              f"{row['implied_accuracy'] * 100:>17.1f}%  {row['empirical_accuracy'] * 100:>13.1f}%")
    print()
    print(f"Strateji (guven-bazli boyutlandirma + stop-loss): "
          f"${trading.STARTING_CASH:.2f} -> ${final_value:.2f} ({strategy_return_pct:+.2f}%)")
    print(f"Al-ve-tut (XRP, tek seferlik alim):               "
          f"${trading.STARTING_CASH:.2f} -> ${buy_hold_value:.2f} ({buy_hold_return_pct:+.2f}%)")
    print(f"Sabit nakit:                                       ${trading.STARTING_CASH:.2f} (degismez)")
    print()
    print("ONEMLI SINIRLAMALAR:")
    print("- Sadece teknik+ML test edildi; balina/haber/emir-defteri canli-only, gecmis arsivi yok.")
    print("- ML modeli bu pencerede TEK SEFER egitildi (production'daki gunluk yeniden-egitim tekrar oynatilmadi).")
    print("- Ensemble agirliklari sabit 50/50 (retrain.py'in gunluk ayarlamasi tekrar oynatilmadi).")
    print("- Bu bir yatirim tavsiyesi degildir; gecmis performans gelecegi garanti etmez.")


def main() -> int:
    run_backtest()
    return 0


if __name__ == "__main__":
    sys.exit(main())

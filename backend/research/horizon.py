"""Exploratory: does ANY horizon give technical+ML an edge that beats fees?

Reuses backtest.py's own walk-forward machinery unchanged -- same
_trailing_window / technical_signal / ml_signal / ensemble.combine /
trading.compute_rebalance path -- only the candle INTERVAL changes, so the
horizon of one step becomes 1h / 4h / 1d instead of 15m. Not committed:
this answers "is there a horizon worth rebuilding the project around",
before touching the live system.
"""
import os, sys, time, math, statistics
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import backtest, ensemble, ml_model, trading
from fetch_data import get_klines_history
from indicators import add_cross_asset_correlation, add_indicator_columns, technical_signal

FEE_ROUNDTRIP = 2 * trading.FEE_RATE

def run(interval, total, cooldown_candles, hours_per_candle):
    t0 = time.time()
    print(f"\n{'='*74}\n### UFUK = {interval}  ({total} mum isteniyor)\n{'='*74}", flush=True)
    xrp = get_klines_history("XRPUSDT", interval=interval, total=total)
    btc = get_klines_history("BTCUSDT", interval=interval, total=total)
    eth = get_klines_history("ETHUSDT", interval=interval, total=total)
    n = min(len(xrp), len(btc), len(eth))
    xrp, btc, eth = (d.iloc[:n].reset_index(drop=True) for d in (xrp, btc, eth))
    span_days = n * hours_per_candle / 24
    print(f"  {n} mum cekildi (~{span_days:.0f} gun)", flush=True)

    train_end = int(n * backtest.TRAIN_FRACTION)
    test_start = max(train_end, backtest.FEATURE_LIMIT + 10)
    if n - test_start < 150:
        print("  YETERSIZ VERI -- atlaniyor"); return None

    model, metrics = ml_model.train_model(xrp.iloc[:train_end].reset_index(drop=True))
    print(f"  ML egitildi: {metrics}", flush=True)

    # Stop-loss cooldown is defined in CANDLES (40 = ~10h at 15m). At 1d that
    # would be a 40-DAY freeze, so scale it to the same wall-clock intent.
    saved = trading.STOP_LOSS_COOLDOWN_CANDLES
    trading.STOP_LOSS_COOLDOWN_CANDLES = cooldown_candles

    path, correct, brier, _ = backtest._compute_signal_path(xrp, btc, eth, model, test_start, n)
    steps = len(path)
    acc = correct / steps
    se = math.sqrt(0.25 / steps)
    z = (acc - 0.5) / se
    moves = [abs(s["next_price"] / s["price"] - 1) for s in path]
    mean_move = statistics.mean(moves)
    breakeven = 0.5 + trading.FEE_RATE / mean_move
    brier_score = statistics.mean((p - a) ** 2 for p, a in brier)

    sim = backtest._simulate_portfolio(path, 0.0)
    bh = (path[-1]["next_price"] / path[0]["price"] - 1) * 100
    trading.STOP_LOSS_COOLDOWN_CANDLES = saved

    print(f"  test adimi: {steps}   ({time.time()-t0:.0f} sn)")
    print(f"  YON ISABETI       : %{100*acc:.1f}   (z={z:+.2f} vs %50, {'ANLAMLI' if abs(z)>1.96 else 'anlamli degil'})")
    print(f"  ort |hareket|     : %{100*mean_move:.3f}")
    print(f"  BASABAS ESIGI     : %{100*breakeven:.1f}  ->  {'GECIYOR' if acc > breakeven else 'GECMIYOR'} (fark {100*(acc-breakeven):+.1f} puan)")
    print(f"  Brier score       : {brier_score:.4f}  (0.25 = yazi-tura)")
    print(f"  portfoy getirisi  : %{sim['return_pct']:+.2f}  ({sim['trade_count']} islem, maks dusus %{sim['max_drawdown_pct']:.1f})")
    print(f"  al-ve-tut         : %{bh:+.2f}   -> strateji {'ONDE' if sim['return_pct']>bh else 'GERIDE'} ({sim['return_pct']-bh:+.1f} puan)")
    return dict(interval=interval, steps=steps, acc=acc, z=z, breakeven=breakeven,
                mean_move=mean_move, ret=sim["return_pct"], bh=bh,
                trades=sim["trade_count"], brier=brier_score, days=span_days)

if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "1d"
    cfg = {"1d": ("1d", 3000, 1, 24.0), "4h": ("4h", 8000, 3, 4.0), "1h": ("1h", 12000, 10, 1.0)}
    r = run(*cfg[which])
    import json; print("\nJSON " + json.dumps(r))

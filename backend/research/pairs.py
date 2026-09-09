"""Pairs trading / statistical arbitrage on XRP vs a correlated asset.

Untested territory in this bench: everything so far (xsec.py, horizon.py) was
a directional or ranking bet -- "will this go up", "will this beat that". A
cointegrated pair is different in kind: you are dollar-neutral (long one leg,
short the other in a hedge-ratio-weighted amount) and you profit when a
historically stable relationship between two assets temporarily stretches and
snaps back, regardless of which way the whole market moves. This is the
textbook Renaissance/LTCM-style stat-arb setup, so it deserves a real,
disciplined test rather than being waved off as "another prediction game in
disguise" -- unlike single-asset direction, it does NOT require knowing where
the market is going, only that XRP/other reverts to its own recent ratio.

Mechanics per pair (XRP vs `OTHER`):
  hedge ratio  beta = rolling OLS slope of log(XRP) on log(OTHER), window `lb`
  spread       s_t  = log(XRP_t) - beta * log(OTHER_t)
  z-score      z_t  = (s_t - rolling_mean(s, w)) / rolling_std(s, w)
  enter short-spread (short XRP / long OTHER*beta) when z > entry
  enter long-spread  (long XRP / short OTHER*beta) when z < -entry
  exit to flat when |z| < exit

Costs: TWO legs, so a round-trip entry+exit charges 4x the one-way fee (not
2x like a single-asset trade). That is the real reason stat-arb needs a much
smaller move per trade to break even in percentage terms per leg, but a much
higher trade frequency to pay for two legs at all.

Same discipline as xsec.py: config picked on TRAIN half only, run once on
TEST half. Requires panel.json (see panel.py / README.md).
"""
import json, math, os, statistics, sys

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
DAY = 86400000
TAKER, MAKER = 0.0010, 0.0002
XRP = "XRPUSDT"
OTHERS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "LTCUSDT", "ADAUSDT", "XLMUSDT"]


def load():
    path = os.path.join(HERE, "panel.json")
    if not os.path.exists(path):
        sys.exit("panel.json yok -- once 'python panel.py' calistir.")
    raw = json.load(open(path))["panel"]
    panel = {s: {int(t): p for t, p in v.items()} for s, v in raw.items()}
    return panel


PANEL = load()


def series(sym, days):
    """Aligned close series for `sym` over `days`, or None if any day missing."""
    out = []
    for d in days:
        p = PANEL.get(sym, {}).get(d)
        if p is None:
            return None
        out.append(p)
    return out


def common_days(sym_a, sym_b):
    return sorted(set(PANEL[sym_a]) & set(PANEL[sym_b]))


def rolling_ols_beta(x, y, i, lb):
    """Slope of y on x over the trailing `lb` points ending at i (inclusive)."""
    xs, ys = x[i - lb + 1:i + 1], y[i - lb + 1:i + 1]
    mx, my = statistics.mean(xs), statistics.mean(ys)
    num = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    den = sum((a - mx) ** 2 for a in xs)
    return num / den if den else 0.0


def backtest(other, lb, w, entry, exit_, dates_idx, log_xrp, log_o, fee):
    """One walk-forward pass. dates_idx is the range of day-indices to trade
    (already offset past the lb+w warmup). Position held is +1 (long spread),
    -1 (short spread), or 0. Returns list of per-day P&L fractions of a
    notional-1 book (i.e. $1 long + $1 short, so a well-behaved return series
    even though the position is nominally 2x gross)."""
    spread = []
    for i in range(len(log_xrp)):
        if i < lb - 1:
            spread.append(None)
            continue
        beta = rolling_ols_beta(log_o, log_xrp, i, lb)
        spread.append(log_xrp[i] - beta * log_o[i])

    pnl = []
    pos = 0
    entry_beta = 0.0
    trades = 0
    for i in dates_idx:
        window = [s for s in spread[i - w:i] if s is not None]
        if len(window) < w // 2 or spread[i] is None:
            pnl.append(0.0)
            continue
        mu, sd = statistics.mean(window), statistics.stdev(window) if len(window) > 1 else 0.0
        z = (spread[i] - mu) / sd if sd > 1e-9 else 0.0

        # one-day forward P&L from holding the CURRENT position through today->tomorrow
        if i + 1 < len(log_xrp) and pos != 0:
            d_xrp = log_xrp[i + 1] - log_xrp[i]
            d_o = log_o[i + 1] - log_o[i]
            # long spread: long XRP, short beta*OTHER (dollar amounts normalized to 1 leg each)
            leg_pnl = pos * (d_xrp - entry_beta * d_o)
        else:
            leg_pnl = 0.0

        day_fee = 0.0
        if pos == 0 and z > entry:
            pos = -1; entry_beta = rolling_ols_beta(log_o, log_xrp, i, lb); trades += 1
            day_fee = 2 * fee                      # two legs, opening
        elif pos == 0 and z < -entry:
            pos = 1; entry_beta = rolling_ols_beta(log_o, log_xrp, i, lb); trades += 1
            day_fee = 2 * fee
        elif pos != 0 and abs(z) < exit_:
            day_fee = 2 * fee                      # two legs, closing
            pos = 0

        pnl.append(leg_pnl - day_fee)
    return pnl, trades


def evaluate(other, lb, w, entry, exit_, lo, hi, fee):
    days = common_days(XRP, other)
    days = [d for d in days if lo <= d <= hi]
    if len(days) < lb + w + 30:
        return None
    x = [math.log(PANEL[XRP][d]) for d in days]
    o = [math.log(PANEL[other][d]) for d in days]
    idx = list(range(lb + w, len(days) - 1))
    pnl, trades = backtest(other, lb, w, entry, exit_, idx, x, o, fee)
    pnl = [p for p in pnl if p != 0.0 or True]  # keep zeros (flat days are real days)
    n = len(pnl)
    if n < 30 or trades < 3:
        return None
    mu, sd = statistics.mean(pnl), statistics.stdev(pnl)
    if sd < 1e-12:
        return None
    ann = mu * 365
    ir = (mu / sd) * math.sqrt(365)
    t = mu / (sd / math.sqrt(n))
    return dict(n=n, trades=trades, ann=ann, ir=ir, t=t)


def dsub(sym_a, sym_b, frac_lo, frac_hi):
    days = common_days(sym_a, sym_b)
    a = days[int(len(days) * frac_lo)]
    b = days[int(len(days) * frac_hi) - 1]
    return a, b


if __name__ == "__main__":
    print("Pairs trading / kointegrasyon: XRP vs korele varlik\n")
    print("Dolar-notr -- piyasa yonunu bilmene GEREK YOK, sadece XRP/OTHER oraninin")
    print("kendi yakin gecmisine geri donmesine bahis oynuyorsun.\n")

    for other in OTHERS:
        days = common_days(XRP, other)
        if len(days) < 400:
            continue
        first, last = days[0], days[-1]
        mid = days[len(days) // 2]

        best = None
        for lb in (30, 60, 90):
            for w in (20, 30, 60):
                for entry in (1.5, 2.0, 2.5):
                    for exit_ in (0.3, 0.5):
                        r = evaluate(other, lb, w, entry, exit_, first, mid, TAKER)
                        if r and (best is None or r["ir"] > best[0]["ir"]):
                            best = (r, lb, w, entry, exit_)
        if best is None:
            print(f"XRP/{other}: egitimde yeterli islem yok, atlandi.")
            continue
        r, lb, w, entry, exit_ = best
        rt = evaluate(other, lb, w, entry, exit_, mid, last, TAKER)
        rm = evaluate(other, lb, w, entry, exit_, mid, last, MAKER)
        rz = evaluate(other, lb, w, entry, exit_, mid, last, 0.0)
        print(f"XRP/{other}:")
        print(f"  egitimde secilen: lb={lb} w={w} giris_z={entry} cikis_z={exit_}  "
              f"(egitim yillik {100*r['ann']:+.1f}% IR={r['ir']:+.2f}, {r['trades']} islem)")
        if rt:
            print(f"  TEST taker %0.10 : yillik {100*rt['ann']:>+7.1f}%  IR={rt['ir']:>+5.2f}  "
                  f"t={rt['t']:>+5.2f}  ({rt['trades']} islem, n={rt['n']} gun)")
        if rm:
            print(f"  TEST maker %0.02 : yillik {100*rm['ann']:>+7.1f}%  IR={rm['ir']:>+5.2f}  t={rm['t']:>+5.2f}")
        if rz:
            print(f"  TEST komisyonsuz : yillik {100*rz['ann']:>+7.1f}%  IR={rz['ir']:>+5.2f}  t={rz['t']:>+5.2f}")
        print()

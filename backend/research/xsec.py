"""Cross-sectional research bench: is there an edge in ranking coins against
each other, rather than calling XRP's direction outright?

Run modes (python xsec.py <mode>):
  ic        rank correlation between past-return rank and forward-return rank,
            measured separately on the train and test halves
  deciles   mean vs median forward return per past-return decile
  book      the portfolio constructions, config picked on train, run on test

The discipline every mode follows is the one the rest of this repo learned the
hard way (REBALANCE_THRESHOLD, CONFIDENCE_FOR_MAX_ALLOCATION,
momentum_trading): a number picked and scored on the same window means
nothing. The window is split in time, the config is chosen on the FIRST half
alone, and that ONE config is then run on the second half. Everything else
printed is for transparency, not for picking after the fact.

Requires panel.json -- build it once with "python panel.py" (~35 min).
See README.md for what this bench has already ruled out.
"""
import json, math, os, statistics, sys

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
DAY = 86400000
TAKER, MAKER = 0.0010, 0.0002

# Established large caps -- a stand-in for "actually liquid". Short-horizon
# cross-sectional effects are classically strongest in exactly the names you
# cannot trade in size, so every result is worth checking on this subset too.
MAJOR = {f"{b}USDT" for b in (
    "BTC", "ETH", "XRP", "BNB", "SOL", "ADA", "DOGE", "TRX", "LINK", "DOT",
    "LTC", "BCH", "AVAX", "ATOM", "ETC", "XLM", "NEAR", "FIL", "APT", "ARB",
    "OP", "INJ", "SUI", "AAVE", "ALGO", "VET", "ICP", "HBAR", "SAND", "MANA",
    "THETA", "AXS", "EGLD", "FTM", "GRT", "RUNE", "CRV", "MKR", "SNX", "COMP",
    "ZEC", "DASH", "QNT", "STX", "IMX", "LDO", "CAKE", "ENS", "GALA", "CHZ",
    "MATIC", "EOS", "LUNA", "FTT", "WAVES")}


def load():
    path = os.path.join(HERE, "panel.json")
    if not os.path.exists(path):
        sys.exit("panel.json yok -- once 'python panel.py' calistir.")
    raw = json.load(open(path))["panel"]
    panel = {s: {int(t): p for t, p in v.items()} for s, v in raw.items()}
    days = sorted({t for v in panel.values() for t in v})
    return panel, days


PANEL, DAYS = load()
MID, FIRST, LAST = DAYS[len(DAYS) // 2], DAYS[0], DAYS[-1]


def price(s, t):
    return PANEL[s].get(t)


def last_price_upto(s, t):
    """Forced exit for a delisted name: the last price it ever printed. Without
    this the dead symbols silently drop out and the backtest flatters itself."""
    ks = [k for k in PANEL[s] if k <= t]
    return PANEL[s][max(ks)] if ks else None


def spearman(xs, ys):
    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r
    rx, ry = rank(xs), rank(ys)
    n = len(xs)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return num / den if den else 0.0


def dates_for(hold, lo, hi):
    return [t for t in DAYS if lo <= t <= hi - hold * DAY][::hold]


def cross_section(lb, hold, t, universe, min_n=20):
    """(symbol, past return, forward return) for every tradable name at `t`."""
    tp, tf = t - lb * DAY, t + hold * DAY
    past = []
    for s in universe:
        if s not in PANEL:
            continue
        p0, p1 = price(s, tp), price(s, t)
        if p0 and p1:
            past.append((s, p1 / p0 - 1))
    rows = []
    for s, r in past:
        pf = price(s, tf) or last_price_upto(s, tf)
        p1 = price(s, t)
        if pf and p1:
            rows.append((s, r, pf / p1 - 1))
    return rows if len(rows) >= min_n else None


def stats(rets, hold):
    n = len(rets)
    if n < 10:
        return None
    py = 365 / hold
    mu, sd = statistics.mean(rets), statistics.stdev(rets)
    return dict(n=n, ann=mu * py, ir=(mu / sd) * math.sqrt(py), t=mu / (sd / math.sqrt(n)))


# --- portfolio constructions -------------------------------------------------
# All are "reversal" oriented (overweight recent losers) because that is the
# sign the IC actually has; flip the weights for the momentum version.

def run_book(lb, hold, dates, universe, fee, kind, k=5, dec=0.30):
    rets, turns, prev = [], [], {}
    for t in dates:
        rows = cross_section(lb, hold, t, universe)
        if not rows:
            continue
        n = len(rows)
        srt = sorted(rows, key=lambda x: x[1])           # ascending: losers first
        fwd = {s: f for s, _, f in rows}
        mkt = statistics.mean(fwd.values())

        if kind == "rank_ls":                            # rank-weighted, dollar-neutral
            pos = {s: -((i - (n - 1) / 2) / ((n - 1) / 2)) for i, (s, _, _) in enumerate(srt)}
            sc = sum(abs(v) for v in pos.values())
            pos = {s: v / sc * 2 for s, v in pos.items()}
            gross = sum(pos[s] * fwd[s] for s in pos)
        elif kind == "rank_lo":                          # rank-weighted, long-only
            pos = {s: max(0.0, -((i - (n - 1) / 2) / ((n - 1) / 2))) for i, (s, _, _) in enumerate(srt)}
            sc = sum(pos.values())
            pos = {s: v / sc for s, v in pos.items()}
            gross = sum(pos[s] * fwd[s] for s in pos) - mkt
        elif kind == "decile_ls":                        # bottom decile long / top short
            m = max(3, int(n * dec))
            lo = [s for s, _, _ in srt[:m]]
            hi = [s for s, _, _ in srt[-m:]]
            pos = {s: 1 / m for s in lo}
            pos.update({s: -1 / m for s in hi})
            gross = statistics.mean(fwd[s] for s in lo) - statistics.mean(fwd[s] for s in hi)
        elif kind == "decile_lo":                        # bottom decile vs equal weight
            m = max(3, int(n * dec))
            lo = [s for s, _, _ in srt[:m]]
            pos = {s: 1 / m for s in lo}
            gross = statistics.mean(fwd[s] for s in lo) - mkt
        elif kind == "bottom_k":                         # naive: buy the k worst
            lo = [s for s, _, _ in srt[:k]]
            pos = {s: 1 / k for s in lo}
            gross = statistics.mean(fwd[s] for s in lo) - mkt
        else:
            raise ValueError(kind)

        turn = sum(abs(pos.get(s, 0) - prev.get(s, 0)) for s in set(pos) | set(prev))
        prev = pos
        rets.append(gross - fee * turn)
        turns.append(turn)
    s = stats(rets, hold)
    if s:
        s["turn"] = statistics.mean(turns)
    return s


# --- modes -------------------------------------------------------------------

def mode_ic():
    print("IC = gecmis siralamasi ile gelecek siralamasi arasindaki korelasyon.")
    print("Negatif IC = son donemin kazananlari geride kaliyor (reversal).\n")
    print(f"{'lookback':>9s} {'tut':>4s} | {'EGITIM IC':>10s} {'t':>6s} | "
          f"{'TEST IC':>9s} {'t':>6s} | {'isaret':>7s}")
    same = tot = 0
    for lb in (3, 5, 7, 10, 14, 21, 30, 60, 90):
        for hold in (3, 5, 7, 14, 30):
            halves = []
            for lo, hi in ((FIRST + lb * DAY, MID), (MID, LAST)):
                ics = []
                for t in dates_for(hold, lo, hi):
                    rows = cross_section(lb, hold, t, PANEL)
                    if rows:
                        ics.append(spearman([r for _, r, _ in rows], [f for _, _, f in rows]))
                halves.append(ics)
            if min(len(h) for h in halves) < 10:
                continue
            vals = [(statistics.mean(h), statistics.mean(h) / (statistics.stdev(h) / math.sqrt(len(h))))
                    for h in halves]
            (a, ta), (b, tb) = vals
            ok = (a < 0) == (b < 0)
            same += ok
            tot += 1
            print(f"{lb:>9d} {hold:>4d} | {a:>+10.4f} {ta:>+6.2f} | {b:>+9.4f} {tb:>+6.2f} | "
                  f"{'AYNI' if ok else 'ters':>7s}")
    print(f"\nIsareti korunan: {same}/{tot} (sans olsa ~%50 beklenir)")


def mode_deciles(lb=7, hold=3):
    print(f"lookback={lb}g tut={hold}g, TEST yarisi.")
    print("ORTALAMA dolar getirisini, MEDYAN siralamayi/IC'yi takip eder --")
    print("ikisi ayrisiyorsa avantaj sagman kuyrukta oturuyor demektir.\n")
    buckets = {i: [] for i in range(10)}
    for t in dates_for(hold, MID, LAST):
        rows = cross_section(lb, hold, t, PANEL, min_n=30)
        if not rows:
            continue
        n = len(rows)
        for i, (s, _, f) in enumerate(sorted(rows, key=lambda x: x[1])):
            buckets[min(9, i * 10 // n)].append(f)
    print(f"{'desil':>22s} {'n':>6s} {'ORTALAMA':>10s} {'MEDYAN':>9s} {'>+%20':>7s}")
    for i in range(10):
        v = buckets[i]
        lab = "1 (en cok DUSEN)" if i == 0 else "10 (en cok YUKSELEN)" if i == 9 else str(i + 1)
        print(f"{lab:>22s} {len(v):>6d} {100*statistics.mean(v):>+9.3f}% "
              f"{100*statistics.median(v):>+8.3f}% {100*sum(1 for r in v if r > 0.20)/len(v):>6.2f}%")
    m = [statistics.mean(buckets[i]) for i in (0, 9)]
    d = [statistics.median(buckets[i]) for i in (0, 9)]
    print(f"\n  ORTALAMA farki (1-10): {100*(m[0]-m[1]):+.3f}%   "
          f"MEDYAN farki: {100*(d[0]-d[1]):+.3f}%")


def mode_book():
    for kind in ("bottom_k", "rank_ls", "rank_lo", "decile_ls", "decile_lo"):
        for uname, uni in (("ALL", set(PANEL)), ("MAJOR", MAJOR & set(PANEL))):
            best = None
            for lb in (3, 5, 7, 10, 14, 21, 30):
                for hold in (3, 5, 7, 14):
                    r = run_book(lb, hold, dates_for(hold, FIRST + lb * DAY, MID), uni, TAKER, kind)
                    if r and (best is None or r["ir"] > best[0]["ir"]):
                        best = (r, lb, hold)
            if not best:
                continue
            r, lb, hold = best
            d = dates_for(hold, MID, LAST)
            print(f"\n### {kind} | evren={uname} ({len(uni)} sembol)")
            print(f"  egitimde secilen: lookback={lb}g tut={hold}g  "
                  f"(egitim yillik {100*r['ann']:+.1f}% IR={r['ir']:+.2f})")
            for fn, fee in (("taker %0.10", TAKER), ("maker %0.02", MAKER), ("komisyonsuz", 0.0)):
                rt = run_book(lb, hold, d, uni, fee, kind)
                print(f"  TEST {fn:<12s}: yillik {100*rt['ann']:>+7.1f}%  "
                      f"IR={rt['ir']:>+5.2f}  t={rt['t']:>+5.2f}")


if __name__ == "__main__":
    import datetime as dt
    print(f"Panel: {len(PANEL)} sembol, {len(DAYS)} gun ({len(DAYS)/365:.1f} yil)")
    print(f"  egitim: {dt.datetime.fromtimestamp(FIRST/1000, dt.UTC):%Y-%m-%d} -> "
          f"{dt.datetime.fromtimestamp(MID/1000, dt.UTC):%Y-%m-%d}")
    print(f"  test  : {dt.datetime.fromtimestamp(MID/1000, dt.UTC):%Y-%m-%d} -> "
          f"{dt.datetime.fromtimestamp(LAST/1000, dt.UTC):%Y-%m-%d}\n")
    mode = sys.argv[1] if len(sys.argv) > 1 else "ic"
    {"ic": mode_ic, "deciles": mode_deciles, "book": mode_book}[mode]()

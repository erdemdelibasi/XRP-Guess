"""Builds a daily close-price panel for a broad USDT universe, cached to disk.

Includes symbols that have SINCE BEEN DELISTED/renamed (MATIC->POL, EOS, LUNA,
FTT, ...). Leaving them out is survivorship bias: the losers vanish from the
sample and every backtest looks better than reality. Their series simply ends,
and the strategy code has to handle that as a forced exit.
"""
import sys, json, time, urllib.request, os
sys.stdout.reconfigure(encoding="utf-8")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "panel.json")
START = 1546300800000  # 2019-01-01

def req(u):
    for attempt in range(4):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent":"M/5"}), timeout=30))
        except Exception as e:
            if attempt == 3: raise
            time.sleep(1.5 * (attempt+1))

# current USDT spot symbols
info = req("https://data-api.binance.vision/api/v3/exchangeInfo")
live = [s["symbol"] for s in info["symbols"]
        if s["quoteAsset"]=="USDT" and s["status"]=="TRADING"
        and not any(x in s["baseAsset"] for x in ("UP","DOWN","BULL","BEAR"))
        and s["baseAsset"] not in ("USDC","BUSD","TUSD","FDUSD","DAI","EUR","GBP","AEUR","USDP")]
# known delisted / renamed -- these are exactly the ones survivorship bias hides
dead = ["MATICUSDT","EOSUSDT","FTTUSDT","LUNAUSDT","WAVESUSDT","SRMUSDT","ANCUSDT",
        "RAYUSDT","SCUSDT","BTGUSDT","OMGUSDT","NANOUSDT","MIRUSDT","TORNUSDT",
        "KEEPUSDT","REPUSDT","BTSUSDT","STRAXUSDT","AGIXUSDT","OCEANUSDT","CVCUSDT"]
syms = sorted(set(live) | set(dead))
print(f"{len(syms)} sembol denenecek ({len(dead)} tanesi bilinen delist/rename)", flush=True)

def history(sym):
    out={}; start=START
    while True:
        u=(f"https://data-api.binance.vision/api/v3/klines?symbol={sym}"
           f"&interval=1d&limit=1000&startTime={start}")
        try: d=req(u)
        except Exception: return out
        if not d: return out
        for c in d: out[int(c[0])]=float(c[4])
        if len(d)<1000: return out
        start=int(d[-1][0])+86400000

panel={}
for i,s in enumerate(syms):
    h=history(s)
    if len(h)>=400: panel[s]=h
    if (i+1)%40==0: print(f"  {i+1}/{len(syms)}  (panelde {len(panel)})", flush=True)
print(f"\nPanel: {len(panel)} sembol")
alldays=sorted({t for v in panel.values() for t in v})
print(f"Gun araligi: {len(alldays)} gun")
json.dump({"panel":{k:{str(t):p for t,p in v.items()} for k,v in panel.items()}}, open(OUT,"w"))
print("Kaydedildi:",OUT)

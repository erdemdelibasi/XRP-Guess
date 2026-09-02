let allPredictions = [];
let currentRangeDays = 7;
let portfolioState = null;
let strategyPortfolios = null;
let tradesByStrategy = {};
let lastLivePrice = null;
// Toggled by clicking any strategy panel's return badge -- applies to all
// panels at once so they stay comparable in the same unit.
let showPnlInDollars = false;
// Portfolio value at the start of the currently-selected date range, per
// strategy, so the return badge can show *that period's* profit/loss
// instead of always the all-time change since the $1000 start. Populated by
// loadRangePnlBoundary(); `days` guards against using a stale cache while a
// new range's fetch is still in flight (falls back to the all-time PORTFOLIO_START
// baseline in the meantime).
const RANGE_PNL_CACHE = { days: null, boundaries: null, price: null };

const SINGLE_SIGNAL_STRATEGIES = ["technical", "ml", "whale", "news", "claude"]; // matches backend trading.STRATEGIES

function supabaseHeaders() {
  return {
    apikey: CONFIG.SUPABASE_ANON_KEY,
    Authorization: `Bearer ${CONFIG.SUPABASE_ANON_KEY}`,
  };
}

async function fetchPredictions(limit = 1000) {
  const url = `${CONFIG.SUPABASE_URL}/rest/v1/predictions?select=*&order=created_at.desc&limit=${limit}`;
  const res = await fetch(url, { headers: supabaseHeaders() });
  if (!res.ok) throw new Error(`Supabase fetch failed: ${res.status}`);
  return res.json();
}

async function fetchPortfolioState() {
  const url = `${CONFIG.SUPABASE_URL}/rest/v1/portfolio_state?select=*&id=eq.1`;
  const res = await fetch(url, { headers: supabaseHeaders() });
  if (!res.ok) throw new Error(`Supabase fetch failed: ${res.status}`);
  const rows = await res.json();
  return rows[0] ?? null;
}

async function fetchStrategyPortfolios() {
  const url = `${CONFIG.SUPABASE_URL}/rest/v1/strategy_portfolios?select=*`;
  const res = await fetch(url, { headers: supabaseHeaders() });
  if (!res.ok) throw new Error(`Supabase fetch failed: ${res.status}`);
  return res.json();
}

async function fetchStrategyTrades(strategy, limit = 10) {
  const url = `${CONFIG.SUPABASE_URL}/rest/v1/strategy_trades?select=*&strategy=eq.${strategy}&order=created_at.desc&limit=${limit}`;
  const res = await fetch(url, { headers: supabaseHeaders() });
  if (!res.ok) throw new Error(`Supabase fetch failed: ${res.status}`);
  return res.json();
}

async function fetchTrades(limit = 10) {
  const url = `${CONFIG.SUPABASE_URL}/rest/v1/trades?select=*&order=created_at.desc&limit=${limit}`;
  const res = await fetch(url, { headers: supabaseHeaders() });
  if (!res.ok) throw new Error(`Supabase fetch failed: ${res.status}`);
  return res.json();
}

// Kanal Finans TS (YouTube @KanalFinans) mentions -- an independent opinion
// feed, not an ensemble component (see backend/kanal_finans.py, CLAUDE.md).
async function fetchKanalFinansMentions(limit = 20) {
  const url = `${CONFIG.SUPABASE_URL}/rest/v1/kanal_finans_mentions?select=*&order=published_at.desc&limit=${limit}`;
  const res = await fetch(url, { headers: supabaseHeaders() });
  if (!res.ok) throw new Error(`Supabase fetch failed: ${res.status}`);
  return res.json();
}

// Holdings as of the last trade at-or-before `boundaryIso` -- the starting
// point for computing that strategy's profit/loss over the selected range.
// No matching row means the strategy hadn't traded yet by then, i.e. it was
// still sitting at its untouched $1000/0 XRP starting state.
async function fetchBoundaryTrade(table, strategyFilter, boundaryIso) {
  const strategyParam = strategyFilter ? `&strategy=eq.${strategyFilter}` : "";
  const url = `${CONFIG.SUPABASE_URL}/rest/v1/${table}?select=cash_after,xrp_after&order=created_at.desc&created_at=lte.${boundaryIso}&limit=1${strategyParam}`;
  const res = await fetch(url, { headers: supabaseHeaders() });
  if (!res.ok) throw new Error(`Supabase fetch failed: ${res.status}`);
  const rows = await res.json();
  return rows[0] ?? null;
}

async function fetchPriceAt(boundaryMs) {
  const url = `https://data-api.binance.vision/api/v3/klines?symbol=XRPUSDT&interval=15m&startTime=${boundaryMs}&limit=1`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Binance klines fetch failed: ${res.status}`);
  const data = await res.json();
  return data.length ? Number(data[0][4]) : null; // close price
}

// Fetches, once per range-button click, the cash/XRP holdings and XRP price
// at the start of the selected range for every strategy -- cached so the
// 3-second live-price tick doesn't refetch this on every render.
async function loadRangePnlBoundary(days) {
  const boundaryMs = Date.now() - days * 24 * 60 * 60 * 1000;
  const boundaryIso = new Date(boundaryMs).toISOString();
  const [price, ensembleTrade, ...strategyTrades] = await Promise.all([
    fetchPriceAt(boundaryMs),
    fetchBoundaryTrade("trades", null, boundaryIso),
    ...SINGLE_SIGNAL_STRATEGIES.map((s) => fetchBoundaryTrade("strategy_trades", s, boundaryIso)),
  ]);
  const toHoldings = (trade) => ({
    cash: trade ? Number(trade.cash_after) : PORTFOLIO_START,
    xrp: trade ? Number(trade.xrp_after) : 0,
  });
  const boundaries = { ensemble: toHoldings(ensembleTrade) };
  SINGLE_SIGNAL_STRATEGIES.forEach((s, i) => { boundaries[s] = toHoldings(strategyTrades[i]); });
  RANGE_PNL_CACHE.days = days;
  RANGE_PNL_CACHE.price = price;
  RANGE_PNL_CACHE.boundaries = boundaries;
}

// Profit/loss since the start of the selected range. Falls back to the
// all-time change since the $1000 baseline for "Tümü" (range=0) and while a
// newly-selected range's boundary data hasn't loaded yet.
function computeRangePnl(key, currentValue) {
  if (currentRangeDays === 0 || RANGE_PNL_CACHE.days !== currentRangeDays || RANGE_PNL_CACHE.price == null) {
    const amount = currentValue - PORTFOLIO_START;
    return { amount, pct: (amount / PORTFOLIO_START) * 100 };
  }
  const boundary = RANGE_PNL_CACHE.boundaries[key];
  const startValue = boundary.cash + boundary.xrp * RANGE_PNL_CACHE.price;
  const amount = currentValue - startValue;
  return { amount, pct: startValue > 0 ? (amount / startValue) * 100 : 0 };
}

function formatPnl(pnl) {
  const value = showPnlInDollars ? pnl.amount : pnl.pct;
  const sign = value >= 0 ? "+" : "-";
  const magnitude = Math.abs(value).toFixed(2);
  return showPnlInDollars ? `${sign}$${magnitude}` : `${sign}${magnitude}%`;
}

function fmtTime(iso) {
  return new Date(iso).toLocaleString("tr-TR", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function fmtPct(pct) {
  if (pct == null) return "-";
  const sign = pct >= 0 ? "+" : "";
  return `${sign}${(pct * 100).toFixed(2)}%`;
}

function fmtPrice(price) {
  if (price == null) return "-";
  return `$${Number(price).toFixed(4)}`;
}

function filterByRange(predictions, days) {
  if (!days) return predictions;
  const since = Date.now() - days * 24 * 60 * 60 * 1000;
  return predictions.filter((p) => new Date(p.created_at).getTime() >= since);
}

function renderWeightsSummary(predictions) {
  const latest = predictions[0];
  const weights = document.getElementById("weights-summary");
  if (latest && latest.weight_technical != null) {
    const labels = { technical: "Teknik", ml: "ML", whale: "Balina", news: "Haber", orderbook: "Emir Defteri", claude: "Claude" };
    const raw = [
      ["technical", latest.weight_technical],
      ["ml", latest.weight_ml],
      ["whale", latest.weight_whale],
      ["news", latest.weight_news],
      ["orderbook", latest.weight_orderbook],
      ["claude", latest.weight_claude],
    ].filter(([, v]) => v != null);
    const pct = roundWeightsTo100(raw);
    const parts = raw.map(([key]) => `${labels[key]}: %${pct[key]}`);
    weights.textContent = `Güncel ağırlıklar — ${parts.join(", ")}`;
  }
}

// Rounds fractional weights to whole percentages that always sum to exactly
// 100 (largest-remainder method) -- rounding each weight independently
// (Math.round) can drift a point or two off 100 and reads as a bug.
function roundWeightsTo100(entries) {
  const withRemainders = entries.map(([label, frac]) => {
    const scaled = frac * 100;
    const floor = Math.floor(scaled);
    return [label, floor, scaled - floor];
  });
  const usedTotal = withRemainders.reduce((sum, [, floor]) => sum + floor, 0);
  const remainder = Math.round(100 - usedTotal);
  const byRemainderDesc = [...withRemainders].sort((a, b) => b[2] - a[2]);
  const pct = Object.fromEntries(withRemainders.map(([label, floor]) => [label, floor]));
  for (let i = 0; i < remainder && i < byRemainderDesc.length; i++) {
    pct[byRemainderDesc[i][0]] += 1;
  }
  return pct;
}

const PORTFOLIO_START = 1000;

// One config entry per strategy panel: which predictions-table columns feed
// its "current prediction" line and its accuracy pie. "ensemble" reuses the
// top-level predicted_direction/confidence/correct columns (the final
// blended call); the other four read that component's own dedicated
// columns (already logged on every prediction row regardless of which
// portfolio ends up using them).
//
// `abstains: true` marks components whose direction field is a meaningless
// placeholder when confidence is 0 (whale/news return a fixed "UP" via
// their NEUTRAL constant when they truly have no opinion -- see
// whale_signal.py/news_signal.py) -- confidence===0 should hide the
// direction there. technical/ml never do this: their direction is always a
// real, already-scored model output even when calibration.py has pushed
// its confidence down to (or to exactly) 0 because the raw signal doesn't
// correlate with real accuracy -- confidence 0 there means "don't trust it
// enough to trade," not "no prediction was made," so their history/current
// line should keep showing the real direction and result.
const STRATEGY_CONFIG = {
  ensemble: { label: "Ensemble (ana model)", dirField: "predicted_direction", confField: "confidence", pctField: "predicted_pct_change", priceField: "predicted_price", correctField: "correct" },
  technical: { label: "Sadece Teknik", dirField: "tech_direction", confField: "tech_confidence", pctField: "tech_pct_change", priceField: "tech_price", correctField: "tech_correct" },
  ml: { label: "Sadece ML", dirField: "ml_direction", confField: "ml_confidence", pctField: "ml_pct_change", priceField: "ml_price", correctField: "ml_correct" },
  whale: { label: "Sadece Balina", dirField: "whale_direction", confField: "whale_confidence", pctField: "whale_pct_change", priceField: "whale_price", correctField: "whale_correct", abstains: true },
  news: { label: "Sadece Haber", dirField: "news_direction", confField: "news_confidence", pctField: "news_pct_change", priceField: "news_price", correctField: "news_correct", abstains: true },
  // claude_signal.py also returns the placeholder direction "UP" (its
  // NEUTRAL constant) when no API key is configured yet or a call fails --
  // same meaningless-direction-at-confidence-0 case as whale/news above.
  claude: { label: "Sadece Claude", dirField: "claude_direction", confField: "claude_confidence", pctField: "claude_pct_change", priceField: "claude_price", correctField: "claude_correct", abstains: true },
};

const strategyPieCharts = {};

// Read the up/down colors from CSS custom properties so the chart palette
// stays in sync with style.css instead of duplicating hex values here.
const rootStyles = getComputedStyle(document.documentElement);
const COLOR_UP = rootStyles.getPropertyValue("--up").trim() || "#34d399";
const COLOR_DOWN = rootStyles.getPropertyValue("--down").trim() || "#fb7185";

function computeStrategyAccuracy(predictions, correctField) {
  const resolved = predictions.filter((p) => p[correctField] != null);
  if (resolved.length === 0) return null;
  const correct = resolved.filter((p) => p[correctField]).length;
  return { correct, total: resolved.length, pct: Math.round((correct / resolved.length) * 100) };
}

// Builds the 6 panel shells once (Chart.js needs its <canvas> to already be
// in the DOM before a chart is created on it) -- re-running this on every
// refresh would destroy/recreate charts and DOM nodes for no reason. Each
// panel carries its own trade-history and prediction-history table so every
// strategy's full picture is visible side by side, with no per-panel
// scrolling and no separate "shared" tables lower on the page.
function buildStrategyPanelsShell() {
  const container = document.getElementById("strategy-panels");
  if (!container || container.childElementCount > 0) return;
  for (const [key, cfg] of Object.entries(STRATEGY_CONFIG)) {
    const panel = document.createElement("div");
    panel.className = "strategy-panel";
    panel.dataset.strategy = key;
    panel.innerHTML = `
      <h3>${cfg.label}</h3>
      <div class="strategy-prediction muted small">-</div>
      <canvas class="strategy-pie" width="100" height="100"></canvas>
      <p class="strategy-acc-summary muted small center">-</p>
      <div class="strategy-portfolio">
        <span class="value">-</span>
        <span class="direction">-</span>
      </div>
      <p class="strategy-cash-xrp muted small">-</p>
      <div class="strategy-history-block" data-block="trades">
        <div class="strategy-block-header">
          <span class="strategy-subhead">İşlem Geçmişi</span>
          <button type="button" class="strategy-toggle" aria-expanded="true" title="Gizle/Göster">▾</button>
        </div>
        <div class="table-wrap">
          <table class="strategy-trades-table">
            <thead><tr><th>Zaman</th><th>İşlem</th><th>Bakiye</th></tr></thead>
            <tbody></tbody>
          </table>
        </div>
      </div>
      <div class="strategy-history-block" data-block="predictions">
        <div class="strategy-block-header">
          <span class="strategy-subhead">Tahmin Geçmişi</span>
          <button type="button" class="strategy-toggle" aria-expanded="true" title="Gizle/Göster">▾</button>
        </div>
        <div class="table-wrap">
          <table class="strategy-history-table">
            <thead><tr><th>Hedef</th><th>Tahmin</th><th>Sonuç</th></tr></thead>
            <tbody></tbody>
          </table>
        </div>
      </div>
    `;
    container.appendChild(panel);
  }
}

// Each history block has its own top-right toggle so the user can collapse
// blocks with little/no data (e.g. a quiet strategy's trade history) to keep
// every panel's row heights aligned -- delegated on the shared container
// since panels are built once and never destroyed.
function setupHistoryToggles() {
  const container = document.getElementById("strategy-panels");
  if (!container) return;
  container.addEventListener("click", (e) => {
    const btn = e.target.closest(".strategy-toggle");
    if (!btn) return;
    const block = btn.closest(".strategy-history-block");
    if (!block) return;
    const collapsed = block.classList.toggle("collapsed");
    btn.textContent = collapsed ? "▸" : "▾";
    btn.setAttribute("aria-expanded", String(!collapsed));
  });
}

// Clicking any panel's return badge flips ALL of them between % and $ at
// once, so the panels stay comparable in the same unit.
function setupPnlToggle() {
  const container = document.getElementById("strategy-panels");
  if (!container) return;
  container.addEventListener("click", (e) => {
    if (!e.target.closest(".strategy-portfolio .direction")) return;
    showPnlInDollars = !showPnlInDollars;
    if (lastLivePrice != null) {
      safeRender(renderStrategyPanels, allPredictions, portfolioState, strategyPortfolios, lastLivePrice, tradesByStrategy);
    }
  });
}

function renderStrategyTradesTable(panel, trades) {
  const tbody = panel.querySelector(".strategy-trades-table tbody");
  if (!trades || trades.length === 0) {
    tbody.innerHTML = '<tr><td colspan="3" class="muted center">Henüz işlem yok</td></tr>';
    return;
  }
  tbody.innerHTML = trades.map((t) => {
    const sideClass = t.side === "BUY" ? "up" : "down";
    const sideText = t.side === "BUY" ? "AL" : "SAT";
    return `
      <tr>
        <td>${fmtTime(t.created_at)}</td>
        <td class="${sideClass}">${sideText}<span class="sub">${Number(t.xrp_amount).toFixed(1)} XRP @ ${fmtPrice(t.price)}</span></td>
        <td>${fmtPrice(t.cash_after)}</td>
      </tr>`;
  }).join("");
}

function renderStrategyHistoryTable(panel, predictions, cfg) {
  const tbody = panel.querySelector(".strategy-history-table tbody");
  const rows = predictions.slice(0, 10);
  if (rows.length === 0) {
    tbody.innerHTML = '<tr><td colspan="3" class="muted center">Henüz veri yok</td></tr>';
    return;
  }
  tbody.innerHTML = rows.map((p) => {
    const dir = p[cfg.dirField];
    const conf = p[cfg.confField];
    if (dir == null || (cfg.abstains && conf === 0)) {
      return `<tr><td>${p.target_time ? fmtTime(p.target_time) : "-"}</td><td class="muted" colspan="2">Sessiz</td></tr>`;
    }
    const correct = p[cfg.correctField];
    const dirClass = dir === "UP" ? "up" : "down";
    const resultClass = correct == null ? "pending" : correct ? "up" : "down";
    const resultText = correct == null ? "Bekliyor" : correct ? "Doğru" : "Yanlış";
    return `
      <tr>
        <td>${p.target_time ? fmtTime(p.target_time) : "-"}</td>
        <td class="${dirClass}">${dir}<span class="sub">${fmtPct(p[cfg.pctField])}</span></td>
        <td class="${resultClass}">${resultText}</td>
      </tr>`;
  }).join("");
}

// Ensemble uses English "UP"/"DOWN" labels for its own predictions -- these
// Turkish labels are deliberately different text (same --up/--down colors,
// see style.css) so a Kanal Finans stance badge never reads as if it were
// our model's own prediction.
const KANAL_FINANS_STANCE_LABELS = { UP: "Olumlu", DOWN: "Olumsuz", NEUTRAL: "Nötr" };
const KANAL_FINANS_STANCE_CLASSES = { UP: "up", DOWN: "down", NEUTRAL: "neutral" };

function renderKanalFinans(mentions) {
  const container = document.getElementById("kanal-finans-feed");
  if (!mentions || mentions.length === 0) {
    container.innerHTML = '<p class="muted small center">Henüz veri yok</p>';
    return;
  }
  container.innerHTML = mentions.map((m) => `
    <div class="kanal-finans-item">
      <div class="kanal-finans-meta">
        <span class="muted small">${fmtTime(m.published_at)}</span>
        <a href="https://youtu.be/${m.video_id}" target="_blank" rel="noopener">${m.video_title || "Video"}</a>
      </div>
      <div class="kanal-finans-body">
        <span class="asset-badge">${m.asset}</span>
        <span class="kanal-finans-summary">${m.summary}</span>
        <span class="stance stance-${KANAL_FINANS_STANCE_CLASSES[m.stance] || "neutral"}">${KANAL_FINANS_STANCE_LABELS[m.stance] || m.stance}</span>
      </div>
    </div>`).join("");
}

function renderStrategyPanels(predictions, ensembleState, strategyStates, livePrice, tradesByStrategy) {
  if (livePrice == null || predictions.length === 0) return;
  buildStrategyPanelsShell();

  const byStrategy = Object.fromEntries((strategyStates ?? []).map((s) => [s.strategy, s]));
  const latest = predictions[0];
  const rangeFiltered = filterByRange(predictions, currentRangeDays);

  for (const [key, cfg] of Object.entries(STRATEGY_CONFIG)) {
    const panel = document.querySelector(`.strategy-panel[data-strategy="${key}"]`);
    if (!panel) continue;
    const state = key === "ensemble" ? ensembleState : byStrategy[key];

    // Latest prediction line
    const dir = latest[cfg.dirField];
    const conf = latest[cfg.confField];
    const predEl = panel.querySelector(".strategy-prediction");
    if (dir == null || conf == null || (cfg.abstains && conf === 0)) {
      predEl.textContent = "Sessiz (sinyal yok)";
    } else {
      const arrow = dir === "UP" ? "▲" : "▼";
      predEl.innerHTML = `<span class="${dir === "UP" ? "up" : "down"}">${arrow} %${Math.round(conf * 100)} güven</span> → ${fmtPrice(latest[cfg.priceField])} (${fmtPct(latest[cfg.pctField])})`;
    }

    // Accuracy pie (respects the same date-range buttons across all panels)
    const acc = computeStrategyAccuracy(rangeFiltered, cfg.correctField);
    const canvas = panel.querySelector(".strategy-pie");
    const pieData = {
      labels: ["Doğru", "Yanlış"],
      datasets: [{
        data: acc ? [acc.correct, acc.total - acc.correct] : [0, 0],
        backgroundColor: [COLOR_UP, COLOR_DOWN],
        borderWidth: 0,
        hoverOffset: 0,
      }],
    };
    if (strategyPieCharts[key]) {
      strategyPieCharts[key].data = pieData;
      strategyPieCharts[key].update();
    } else {
      strategyPieCharts[key] = new Chart(canvas, {
        type: "doughnut",
        data: pieData,
        options: { cutout: "68%", plugins: { legend: { display: false } }, animation: false },
      });
    }
    panel.querySelector(".strategy-acc-summary").textContent = acc ? `${acc.total} tahminden ${acc.correct} doğru (%${acc.pct})` : "Bu aralıkta veri yok";

    // Portfolio value/return -- the badge shows the selected range's
    // profit/loss (see computeRangePnl) and toggles between % and $ on click
    // (see setupPnlToggle).
    if (state) {
      const cash = Number(state.cash_usd);
      const xrp = Number(state.xrp_amount);
      const value = cash + xrp * livePrice;
      const pnl = computeRangePnl(key, value);
      panel.querySelector(".strategy-portfolio .value").textContent = `$${value.toFixed(2)}`;
      const retEl = panel.querySelector(".strategy-portfolio .direction");
      retEl.textContent = formatPnl(pnl);
      retEl.className = `direction ${pnl.amount >= 0 ? "up" : "down"}`;
      retEl.title = "Dolar/yüzde görünümü için tıkla";
      panel.querySelector(".strategy-cash-xrp").textContent = `Nakit $${cash.toFixed(2)} · ${xrp.toFixed(4)} XRP`;
    }

    // Full per-strategy trade & prediction history (not just a mini list)
    renderStrategyTradesTable(panel, tradesByStrategy?.[key]);
    renderStrategyHistoryTable(panel, predictions, cfg);
  }
}

function setupRangeButtons() {
  const container = document.getElementById("range-buttons");
  container.addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-range]");
    if (!btn) return;
    container.querySelectorAll("button").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    currentRangeDays = Number(btn.dataset.range);
    // "Tümü" doesn't need boundary data (falls back to the $1000 baseline in
    // computeRangePnl); the other ranges fetch fresh boundary holdings/price.
    if (currentRangeDays !== 0) {
      try {
        await loadRangePnlBoundary(currentRangeDays);
      } catch (err) {
        console.error("Range PnL boundary fetch failed:", err);
        RANGE_PNL_CACHE.days = null; // render() falls back to the all-time baseline
      }
    }
    if (lastLivePrice != null) {
      safeRender(renderStrategyPanels, allPredictions, portfolioState, strategyPortfolios, lastLivePrice, tradesByStrategy);
    }
  });
}

function safeRender(fn, ...args) {
  try {
    fn(...args);
  } catch (err) {
    console.error(`${fn.name} failed:`, err);
  }
}

function updateClock() {
  document.getElementById("live-clock").textContent = new Date().toLocaleTimeString("tr-TR", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function startClock() {
  updateClock();
  setInterval(updateClock, 1000);
}

const BINANCE_TICKER_URL = "https://data-api.binance.vision/api/v3/ticker/price?symbol=XRPUSDT";

async function updateLivePrice() {
  try {
    const res = await fetch(BINANCE_TICKER_URL);
    if (!res.ok) throw new Error(`Binance fetch failed: ${res.status}`);
    const data = await res.json();
    lastLivePrice = Number(data.price);
    document.getElementById("live-price").textContent = fmtPrice(lastLivePrice);
    safeRender(renderStrategyPanels, allPredictions, portfolioState, strategyPortfolios, lastLivePrice, tradesByStrategy);
  } catch (err) {
    console.error("Live price fetch failed:", err);
  }
}

function startLivePricePolling() {
  updateLivePrice();
  setInterval(updateLivePrice, 3000);
}

async function loadPredictions() {
  try {
    allPredictions = await fetchPredictions();
  } catch (err) {
    document.getElementById("last-updated").textContent = "Veri alınamadı";
    console.error(err);
    return;
  }

  // Rows logged before the target_time/pct-change columns existed are legacy
  // test data -- drop them so the history table only shows real forecasts.
  allPredictions = allPredictions.filter((p) => p.target_time != null);

  if (allPredictions.length === 0) {
    document.getElementById("last-updated").textContent = "Henüz veri yok";
    return;
  }
  document.getElementById("last-updated").textContent = fmtTime(allPredictions[0].created_at);
  safeRender(renderWeightsSummary, allPredictions);

  try {
    portfolioState = await fetchPortfolioState();
  } catch (err) {
    console.error("Portfolio fetch failed:", err);
  }

  try {
    tradesByStrategy.ensemble = await fetchTrades();
  } catch (err) {
    console.error("Trades fetch failed:", err);
  }

  try {
    const [portfolios, ...perStrategyTrades] = await Promise.all([
      fetchStrategyPortfolios(),
      ...SINGLE_SIGNAL_STRATEGIES.map((s) => fetchStrategyTrades(s)),
    ]);
    strategyPortfolios = portfolios;
    SINGLE_SIGNAL_STRATEGIES.forEach((s, i) => { tradesByStrategy[s] = perStrategyTrades[i]; });
  } catch (err) {
    console.error("Strategy portfolios/trades fetch failed:", err);
  }

  try {
    const mentions = await fetchKanalFinansMentions();
    safeRender(renderKanalFinans, mentions);
  } catch (err) {
    console.error("Kanal Finans fetch failed:", err);
  }

  if (lastLivePrice != null) {
    safeRender(renderStrategyPanels, allPredictions, portfolioState, strategyPortfolios, lastLivePrice, tradesByStrategy);
  }
}

// Predictions only change every 15 min, so refreshing every 30s keeps the
// panel feeling live without hammering Supabase for no reason.
const PREDICTIONS_REFRESH_MS = 30000;

async function init() {
  setupRangeButtons();
  setupHistoryToggles();
  setupPnlToggle();
  startClock();
  startLivePricePolling();
  // Kicks off in parallel with loadPredictions() below -- computeRangePnl()
  // falls back to the all-time baseline until this resolves, so there's
  // nothing broken to show meanwhile; re-render once it lands so the default
  // 7-day range's real profit/loss replaces that fallback.
  loadRangePnlBoundary(currentRangeDays)
    .then(() => {
      if (lastLivePrice != null) {
        safeRender(renderStrategyPanels, allPredictions, portfolioState, strategyPortfolios, lastLivePrice, tradesByStrategy);
      }
    })
    .catch((err) => console.error("Range PnL boundary fetch failed:", err));
  await loadPredictions();
  setInterval(loadPredictions, PREDICTIONS_REFRESH_MS);
}

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => navigator.serviceWorker.register("sw.js").catch(() => {}));
}

init();

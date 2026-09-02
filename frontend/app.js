let allPredictions = [];
let pieChart = null;
let currentRangeDays = 7;
let portfolioState = null;
let strategyPortfolios = null;
let tradesByStrategy = {};
let lastLivePrice = null;

const SINGLE_SIGNAL_STRATEGIES = ["technical", "ml", "whale", "news"]; // matches backend trading.STRATEGIES

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

async function fetchStrategyTrades(strategy, limit = 6) {
  const url = `${CONFIG.SUPABASE_URL}/rest/v1/strategy_trades?select=*&strategy=eq.${strategy}&order=created_at.desc&limit=${limit}`;
  const res = await fetch(url, { headers: supabaseHeaders() });
  if (!res.ok) throw new Error(`Supabase fetch failed: ${res.status}`);
  return res.json();
}

async function fetchTrades(limit = 50) {
  const url = `${CONFIG.SUPABASE_URL}/rest/v1/trades?select=*&order=created_at.desc&limit=${limit}`;
  const res = await fetch(url, { headers: supabaseHeaders() });
  if (!res.ok) throw new Error(`Supabase fetch failed: ${res.status}`);
  return res.json();
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

function renderCurrent(latest) {
  document.getElementById("last-updated").textContent = fmtTime(latest.created_at);
  document.getElementById("current-price").textContent = fmtPrice(latest.price_at_prediction);

  const dirEl = document.getElementById("current-direction");
  dirEl.textContent = latest.predicted_direction === "UP" ? "▲ Artış" : "▼ Azalış";
  dirEl.className = `direction ${latest.predicted_direction === "UP" ? "up" : "down"}`;

  document.getElementById("current-target").textContent = latest.target_time ? fmtTime(latest.target_time) : "-";
  document.getElementById("current-predicted-price").textContent =
    `${fmtPrice(latest.predicted_price)} (${fmtPct(latest.predicted_pct_change)})`;

  document.getElementById("current-confidence").textContent = `%${Math.round(latest.confidence * 100)}`;
  document.getElementById("tech-component").textContent =
    `${latest.tech_direction ?? "-"} ${fmtPct(latest.tech_pct_change)} → ${fmtPrice(latest.tech_price)}`;
  document.getElementById("ml-component").textContent =
    `${latest.ml_direction ?? "-"} ${fmtPct(latest.ml_pct_change)} → ${fmtPrice(latest.ml_price)}`;

  const whaleConfident = latest.whale_confidence != null && latest.whale_confidence > 0;
  document.getElementById("whale-component").textContent = whaleConfident
    ? `${latest.whale_direction} ${fmtPct(latest.whale_pct_change)} → ${fmtPrice(latest.whale_price)}`
    : "Sessiz (büyük hareket yok)";

  const newsConfident = latest.news_confidence != null && latest.news_confidence > 0;
  document.getElementById("news-component").textContent = newsConfident
    ? `${latest.news_direction} ${fmtPct(latest.news_pct_change)} → ${fmtPrice(latest.news_price)}`
    : "Sessiz (önemli haber yok)";
}

function filterByRange(predictions, days) {
  if (!days) return predictions;
  const since = Date.now() - days * 24 * 60 * 60 * 1000;
  return predictions.filter((p) => new Date(p.created_at).getTime() >= since);
}

function renderAccuracy(predictions) {
  const resolved = filterByRange(predictions, currentRangeDays).filter((p) => p.resolved_at);
  const correct = resolved.filter((p) => p.correct).length;
  const incorrect = resolved.length - correct;

  const ctx = document.getElementById("accuracy-pie");
  const data = {
    labels: ["Doğru", "Yanlış"],
    datasets: [{ data: [correct, incorrect], backgroundColor: ["#2ecc71", "#e74c3c"] }],
  };
  if (pieChart) {
    pieChart.data = data;
    pieChart.update();
  } else {
    pieChart = new Chart(ctx, {
      type: "pie",
      data,
      options: { plugins: { legend: { labels: { color: "#e8edf2" } } } },
    });
  }

  const summary = document.getElementById("accuracy-summary");
  if (resolved.length === 0) {
    summary.textContent = "Bu aralıkta henüz sonuçlanmış tahmin yok.";
  } else {
    const pct = Math.round((correct / resolved.length) * 100);
    summary.textContent = `${resolved.length} tahminden ${correct} doğru (%${pct})`;
  }

  const latest = predictions[0];
  const weights = document.getElementById("weights-summary");
  if (latest && latest.weight_technical != null) {
    const labels = { technical: "Teknik", ml: "ML", whale: "Balina", news: "Haber", orderbook: "Emir Defteri" };
    const raw = [
      ["technical", latest.weight_technical],
      ["ml", latest.weight_ml],
      ["whale", latest.weight_whale],
      ["news", latest.weight_news],
      ["orderbook", latest.weight_orderbook],
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

function renderHistory(predictions) {
  const tbody = document.querySelector("#history-table tbody");
  tbody.innerHTML = "";
  predictions.slice(0, 50).forEach((p) => {
    const tr = document.createElement("tr");

    const resultClass = p.resolved_at == null ? "pending" : p.correct ? "up" : "down";
    const resultText = p.resolved_at == null ? "Bekliyor" : p.correct ? "Doğru" : "Yanlış";
    const predDirClass = p.predicted_direction === "UP" ? "up" : "down";

    tr.innerHTML = `
      <td>${p.target_time ? fmtTime(p.target_time) : "-"}</td>
      <td class="${predDirClass}">
        <div>${p.predicted_direction} ${fmtPct(p.predicted_pct_change)}</div>
        <div class="sub">${fmtPrice(p.predicted_price)}</div>
      </td>
      <td>
        <div>${p.actual_direction ?? "-"}</div>
        <div class="sub">${fmtPrice(p.price_at_resolution)}</div>
      </td>
      <td class="${resultClass}">${resultText}</td>
    `;
    tbody.appendChild(tr);
  });
}

const PORTFOLIO_START = 1000;

function renderPortfolio(state, livePrice) {
  if (!state || livePrice == null) return;
  const cash = Number(state.cash_usd);
  const xrp = Number(state.xrp_amount);
  const value = cash + xrp * livePrice;
  const returnPct = (value - PORTFOLIO_START) / PORTFOLIO_START;

  document.getElementById("portfolio-value").textContent = `$${value.toFixed(2)}`;
  const retEl = document.getElementById("portfolio-return");
  retEl.textContent = `${returnPct >= 0 ? "+" : ""}${(returnPct * 100).toFixed(2)}%`;
  retEl.className = `direction ${returnPct >= 0 ? "up" : "down"}`;

  document.getElementById("portfolio-cash").textContent = `$${cash.toFixed(2)}`;
  document.getElementById("portfolio-xrp").textContent = `${xrp.toFixed(4)} XRP`;
}

// One config entry per strategy card: which predictions-table columns feed
// its "current prediction" line and its accuracy pie. "ensemble" reuses the
// top-level predicted_direction/confidence/correct columns (the final
// blended call); the other four read that component's own dedicated
// columns (already logged on every prediction row regardless of which
// portfolio ends up using them).
const STRATEGY_CONFIG = {
  ensemble: { label: "Ensemble (ana model)", dirField: "predicted_direction", confField: "confidence", pctField: "predicted_pct_change", priceField: "predicted_price", correctField: "correct" },
  technical: { label: "Sadece Teknik", dirField: "tech_direction", confField: "tech_confidence", pctField: "tech_pct_change", priceField: "tech_price", correctField: "tech_correct" },
  ml: { label: "Sadece ML", dirField: "ml_direction", confField: "ml_confidence", pctField: "ml_pct_change", priceField: "ml_price", correctField: "ml_correct" },
  whale: { label: "Sadece Balina", dirField: "whale_direction", confField: "whale_confidence", pctField: "whale_pct_change", priceField: "whale_price", correctField: "whale_correct" },
  news: { label: "Sadece Haber", dirField: "news_direction", confField: "news_confidence", pctField: "news_pct_change", priceField: "news_price", correctField: "news_correct" },
};

const strategyPieCharts = {};

function computeStrategyAccuracy(predictions, correctField) {
  const resolved = predictions.filter((p) => p[correctField] != null);
  if (resolved.length === 0) return null;
  const correct = resolved.filter((p) => p[correctField]).length;
  return { correct, total: resolved.length, pct: Math.round((correct / resolved.length) * 100) };
}

// Builds the 5 card shells once (Chart.js needs its <canvas> to already be
// in the DOM before a chart is created on it) -- re-running this on every
// refresh would destroy/recreate charts and DOM nodes for no reason.
function buildStrategyCardsShell() {
  const container = document.getElementById("strategy-cards");
  if (!container || container.childElementCount > 0) return;
  for (const [key, cfg] of Object.entries(STRATEGY_CONFIG)) {
    const card = document.createElement("div");
    card.className = "strategy-card";
    card.dataset.strategy = key;
    card.innerHTML = `
      <h3>${cfg.label}</h3>
      <div class="strategy-prediction muted small">-</div>
      <canvas class="strategy-pie" width="100" height="100"></canvas>
      <p class="strategy-acc-summary muted small center">-</p>
      <div class="strategy-portfolio">
        <span class="value">-</span>
        <span class="direction">-</span>
      </div>
      <div class="strategy-trades-mini muted small">-</div>
    `;
    container.appendChild(card);
  }
}

function renderStrategyCards(predictions, ensembleState, strategyStates, livePrice, tradesByStrategy) {
  if (livePrice == null || predictions.length === 0) return;
  buildStrategyCardsShell();

  const byStrategy = Object.fromEntries((strategyStates ?? []).map((s) => [s.strategy, s]));
  const latest = predictions[0];
  const rangeFiltered = filterByRange(predictions, currentRangeDays);

  for (const [key, cfg] of Object.entries(STRATEGY_CONFIG)) {
    const card = document.querySelector(`.strategy-card[data-strategy="${key}"]`);
    if (!card) continue;
    const state = key === "ensemble" ? ensembleState : byStrategy[key];

    // Latest prediction line
    const dir = latest[cfg.dirField];
    const conf = latest[cfg.confField];
    const predEl = card.querySelector(".strategy-prediction");
    if (dir == null || conf == null || conf === 0) {
      predEl.textContent = "Sessiz (sinyal yok)";
    } else {
      const arrow = dir === "UP" ? "▲" : "▼";
      predEl.innerHTML = `<span class="${dir === "UP" ? "up" : "down"}">${arrow} %${Math.round(conf * 100)} güven</span> → ${fmtPrice(latest[cfg.priceField])} (${fmtPct(latest[cfg.pctField])})`;
    }

    // Accuracy pie (respects the same date-range buttons as the main pie)
    const acc = computeStrategyAccuracy(rangeFiltered, cfg.correctField);
    const canvas = card.querySelector(".strategy-pie");
    const pieData = {
      labels: ["Doğru", "Yanlış"],
      datasets: [{ data: acc ? [acc.correct, acc.total - acc.correct] : [0, 0], backgroundColor: ["#2ecc71", "#e74c3c"] }],
    };
    if (strategyPieCharts[key]) {
      strategyPieCharts[key].data = pieData;
      strategyPieCharts[key].update();
    } else {
      strategyPieCharts[key] = new Chart(canvas, {
        type: "pie",
        data: pieData,
        options: { plugins: { legend: { display: false } }, animation: false },
      });
    }
    card.querySelector(".strategy-acc-summary").textContent = acc ? `${acc.total} tahminden ${acc.correct} doğru (%${acc.pct})` : "Bu aralıkta veri yok";

    // Portfolio value/return
    if (state) {
      const cash = Number(state.cash_usd);
      const xrp = Number(state.xrp_amount);
      const value = cash + xrp * livePrice;
      const retPct = ((value - PORTFOLIO_START) / PORTFOLIO_START) * 100;
      card.querySelector(".strategy-portfolio .value").textContent = `$${value.toFixed(2)}`;
      const retEl = card.querySelector(".strategy-portfolio .direction");
      retEl.textContent = `${retPct >= 0 ? "+" : ""}${retPct.toFixed(2)}%`;
      retEl.className = `direction ${retPct >= 0 ? "up" : "down"}`;
    }

    // Last few trades, compact
    const trades = tradesByStrategy?.[key] ?? [];
    const tradesEl = card.querySelector(".strategy-trades-mini");
    tradesEl.innerHTML = trades.length === 0
      ? "Henüz işlem yok"
      : trades.map((t) => {
          const sideText = t.side === "BUY" ? "AL" : "SAT";
          const sideClass = t.side === "BUY" ? "up" : "down";
          return `<div><span class="${sideClass}">${sideText}</span> ${Number(t.xrp_amount).toFixed(1)} XRP @ ${fmtPrice(t.price)} <span class="muted">${fmtTime(t.created_at)}</span></div>`;
        }).join("");
  }
}

function renderTrades(trades) {
  const tbody = document.querySelector("#trades-table tbody");
  tbody.innerHTML = "";
  if (trades.length === 0) {
    tbody.innerHTML = '<tr><td colspan="4" class="muted center">Henüz işlem yok</td></tr>';
    return;
  }
  trades.forEach((t) => {
    const tr = document.createElement("tr");
    const sideClass = t.side === "BUY" ? "up" : "down";
    const sideText = t.side === "BUY" ? "AL" : "SAT";

    tr.innerHTML = `
      <td>${fmtTime(t.created_at)}</td>
      <td class="${sideClass}">
        <div>${sideText}</div>
        <div class="sub">${Number(t.xrp_amount).toFixed(2)} XRP @ ${fmtPrice(t.price)}</div>
      </td>
      <td>${fmtPrice(t.fee_usd)}</td>
      <td>
        <div>${fmtPrice(t.cash_after)}</div>
        <div class="sub">${Number(t.xrp_after).toFixed(2)} XRP</div>
      </td>
    `;
    tbody.appendChild(tr);
  });
}

function setupRangeButtons() {
  const container = document.getElementById("range-buttons");
  container.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-range]");
    if (!btn) return;
    container.querySelectorAll("button").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    currentRangeDays = Number(btn.dataset.range);
    renderAccuracy(allPredictions);
    if (lastLivePrice != null) {
      safeRender(renderStrategyCards, allPredictions, portfolioState, strategyPortfolios, lastLivePrice, tradesByStrategy);
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
    if (portfolioState) safeRender(renderPortfolio, portfolioState, lastLivePrice);
    safeRender(renderStrategyCards, allPredictions, portfolioState, strategyPortfolios, lastLivePrice, tradesByStrategy);
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
  safeRender(renderCurrent, allPredictions[0]);
  safeRender(renderAccuracy, allPredictions);
  safeRender(renderHistory, allPredictions);

  try {
    portfolioState = await fetchPortfolioState();
    if (portfolioState && lastLivePrice != null) {
      safeRender(renderPortfolio, portfolioState, lastLivePrice);
    }
  } catch (err) {
    console.error("Portfolio fetch failed:", err);
  }

  try {
    const trades = await fetchTrades();
    tradesByStrategy.ensemble = trades.slice(0, 6);
    safeRender(renderTrades, trades);
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

  if (lastLivePrice != null) {
    safeRender(renderStrategyCards, allPredictions, portfolioState, strategyPortfolios, lastLivePrice, tradesByStrategy);
  }
}

// Predictions only change every 15 min, so refreshing every 30s keeps the
// panel feeling live without hammering Supabase for no reason.
const PREDICTIONS_REFRESH_MS = 30000;

async function init() {
  setupRangeButtons();
  startClock();
  startLivePricePolling();
  await loadPredictions();
  setInterval(loadPredictions, PREDICTIONS_REFRESH_MS);
}

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => navigator.serviceWorker.register("sw.js").catch(() => {}));
}

init();

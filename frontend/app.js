const AUTH_KEY = "xrp_tahmin_auth_ok";
let allPredictions = [];
let pieChart = null;
let currentRangeDays = 7;
let portfolioState = null;
let lastLivePrice = null;

async function sha256Hex(text) {
  const data = new TextEncoder().encode(text);
  const digest = await crypto.subtle.digest("SHA-256", data);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

function showApp() {
  document.getElementById("password-gate").hidden = true;
  document.getElementById("app").hidden = false;
  window.scrollTo(0, 0);
  init();
}

function setupGate() {
  if (localStorage.getItem(AUTH_KEY) === "1") {
    showApp();
    return;
  }
  const form = document.getElementById("gate-form");
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const value = document.getElementById("gate-password").value;
    const hash = await sha256Hex(value);
    if (hash === CONFIG.PASSWORD_HASH) {
      localStorage.setItem(AUTH_KEY, "1");
      document.getElementById("gate-error").hidden = true;
      showApp();
    } else {
      document.getElementById("gate-error").hidden = false;
    }
  });
}

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
    weights.textContent =
      `Güncel ağırlıklar — Teknik: %${Math.round(latest.weight_technical * 100)}, ` +
      `ML: %${Math.round(latest.weight_ml * 100)}`;
  }
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
    safeRender(renderTrades, trades);
  } catch (err) {
    console.error("Trades fetch failed:", err);
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

setupGate();

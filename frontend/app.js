const AUTH_KEY = "xrp_tahmin_auth_ok";
let allPredictions = [];
let pieChart = null;
let currentRangeDays = 7;

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

async function fetchPredictions(limit = 1000) {
  const url = `${CONFIG.SUPABASE_URL}/rest/v1/predictions?select=*&order=created_at.desc&limit=${limit}`;
  const res = await fetch(url, {
    headers: {
      apikey: CONFIG.SUPABASE_ANON_KEY,
      Authorization: `Bearer ${CONFIG.SUPABASE_ANON_KEY}`,
    },
  });
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

    tr.innerHTML = `
      <td>${p.target_time ? fmtTime(p.target_time) : "-"}</td>
      <td class="${p.predicted_direction === "UP" ? "up" : "down"}">${p.predicted_direction} ${fmtPct(p.predicted_pct_change)}</td>
      <td>${fmtPrice(p.predicted_price)}</td>
      <td>${p.actual_direction ?? "-"}</td>
      <td class="${resultClass}">${resultText}</td>
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

async function init() {
  setupRangeButtons();
  try {
    allPredictions = await fetchPredictions();
  } catch (err) {
    document.getElementById("last-updated").textContent = "Veri alınamadı";
    console.error(err);
    return;
  }

  if (allPredictions.length === 0) {
    document.getElementById("last-updated").textContent = "Henüz veri yok";
    return;
  }
  safeRender(renderCurrent, allPredictions[0]);
  safeRender(renderAccuracy, allPredictions);
  safeRender(renderHistory, allPredictions);
}

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => navigator.serviceWorker.register("sw.js").catch(() => {}));
}

setupGate();

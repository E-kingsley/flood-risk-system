const API_BASE = "https://flood-risk-system-l0tm.onrender.com/api";
const MONTH_NAMES = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

const RISK_META = {
  0: { label: "Low Risk", css: "low", color: "#22c55e" },
  1: { label: "Moderate Risk", css: "moderate", color: "#f59e0b" },
  2: { label: "High Risk", css: "high", color: "#ef4444" },
};

let map;
let geojsonLayer;
let lgaLayersByName = {}; // normalized LGA name -> Leaflet layer
let allLgas = [];
let currentLgaLayer = null; // the layer currently highlighted by a prediction
let snapshotActive = false;

function normalizeName(name) {
  return (name || "").trim().toLowerCase();
}

function showStatus(message, isError = false) {
  const el = document.getElementById("statusMessage");
  el.textContent = message;
  el.classList.remove("hidden", "error");
  if (isError) el.classList.add("error");
}

function hideStatus() {
  document.getElementById("statusMessage").classList.add("hidden");
}

/* ---------- Tabs ---------- */

function bindTabs() {
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));

      btn.classList.add("active");
      document.getElementById(`${btn.dataset.tab}View`).classList.add("active");

      // The map needs a resize nudge whenever its container becomes
      // visible again after being display:none (Leaflet can't measure
      // a hidden container correctly).
      if (btn.dataset.tab === "predict" && map) {
        setTimeout(() => map.invalidateSize(), 50);
      }

      if (btn.dataset.tab === "history") {
        prefillHistoryFromPredict();
      }
    });
  });
}

/* ---------- Results panel collapse ---------- */

function bindResultsToggle() {
  const btn = document.getElementById("resultsToggle");
  const panel = document.getElementById("resultsPanel");

  btn.addEventListener("click", () => {
    const collapsed = panel.classList.toggle("collapsed");
    btn.textContent = collapsed ? "Show results ▸" : "Hide results ▾";
    // Leaflet doesn't know its container resized until told.
    setTimeout(() => map.invalidateSize(), 260);
  });
}

/* ---------- Compare toggle ---------- */

function bindCompareToggle() {
  const checkbox = document.getElementById("compareToggle");
  const fields = document.getElementById("comparePeriodFields");
  const predictBtn = document.getElementById("predictBtn");

  checkbox.addEventListener("change", () => {
    fields.classList.toggle("hidden", !checkbox.checked);
    predictBtn.textContent = checkbox.checked ? "Compare Flood Risk" : "Predict Flood Risk";
  });
}

/* ---------- Alert toast ---------- */

function showAlertToast(result) {
  const select = document.getElementById("alertThreshold");
  if (!select || select.value === "off") return;

  const threshold = parseInt(select.value, 10);
  if (result.predicted_risk_class < threshold) return;

  const meta = RISK_META[result.predicted_risk_class];
  const container = document.getElementById("toastContainer");

  const toast = document.createElement("div");
  toast.className = `toast toast-${meta.css}`;
  toast.innerHTML = `
    <div class="toast-title">⚠ Flood Risk Alert</div>
    <div class="toast-body">${result.lga_name} is forecast <strong>${meta.label}</strong> for ${MONTH_NAMES[result.month - 1]} ${result.year}.</div>
  `;
  container.appendChild(toast);

  requestAnimationFrame(() => toast.classList.add("show"));

  setTimeout(() => {
    toast.classList.remove("show");
    setTimeout(() => toast.remove(), 300);
  }, 6000);
}

/* ---------- Map ---------- */

function initMap() {
  map = L.map("map", { zoomControl: true }).setView([4.9, 6.3], 8);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "&copy; OpenStreetMap contributors",
    maxZoom: 18,
  }).addTo(map);
}

function defaultLgaStyle() {
  return {
    color: "#2dd4bf",
    weight: 1,
    fillColor: "#16324f",
    fillOpacity: 0.5,
  };
}

async function loadGeoJSON() {
  try {
    const res = await fetch("lga_boundaries.geojson");
    if (!res.ok) throw new Error(`Could not load lga_boundaries.geojson (status ${res.status})`);
    const data = await res.json();

    geojsonLayer = L.geoJSON(data, {
      style: defaultLgaStyle,
      onEachFeature: (feature, layer) => {
        const rawName =
          feature.properties.adm2_name ||
          feature.properties.ADM2_EN ||
          feature.properties.admin2Name ||
          feature.properties.shapeName ||
          feature.properties.NAME_2 ||
          feature.properties.name ||
          "";
        const key = normalizeName(rawName);
        lgaLayersByName[key] = layer;
        layer.bindTooltip(rawName);
      },
    }).addTo(map);

    if (geojsonLayer.getBounds().isValid()) {
      map.fitBounds(geojsonLayer.getBounds(), { padding: [20, 20] });
    }
  } catch (err) {
    showStatus(
      "Couldn't load LGA boundaries. Make sure lga_boundaries.geojson is in the same folder as index.html.",
      true
    );
    console.error(err);
  }
}

// Scales fill opacity by model confidence, so a shaky prediction reads
// visually "softer" on the map than a confident one. Confidence is a
// 0-1 top-class probability; opacity is clamped to a 0.3-0.85 range so
// even low-confidence results stay visible against the base map style.
function opacityForConfidence(confidence) {
  const clamped = Math.max(0, Math.min(1, confidence));
  return 0.3 + clamped * 0.55;
}

function highlightLga(lgaName, riskClass, confidence = 1) {
  // Reset the previously highlighted LGA back to the default style.
  if (currentLgaLayer) {
    currentLgaLayer.setStyle(defaultLgaStyle());
  }

  const layer = lgaLayersByName[normalizeName(lgaName)];
  if (!layer) {
    console.warn(`No map shape found for LGA name "${lgaName}" — tooltip name may differ from the API's lga_name.`);
    return;
  }

  const meta = RISK_META[riskClass];
  layer.setStyle({
    color: meta.color,
    weight: 3,
    fillColor: meta.color,
    fillOpacity: opacityForConfidence(confidence),
  });
  layer.bringToFront();

    // flyToBounds gives a real cinematic pan-and-zoom (unlike fitBounds'
  // animate option, which is a weak linear ease) — this is what makes
  // the zoom into the predicted LGA feel deliberate and satisfying.
  map.flyToBounds(layer.getBounds(), {
    padding: [60, 60],
    maxZoom: 11,
    duration: 2.5,
  });

  currentLgaLayer = layer;
}

/* ---------- All-LGA snapshot ---------- */

function clearAllLgaStyles() {
  Object.values(lgaLayersByName).forEach((layer) => layer.setStyle(defaultLgaStyle()));
}

function resetLgaTooltips() {
  Object.entries(lgaLayersByName).forEach(([key, layer]) => {
    const match = allLgas.find((l) => normalizeName(l.lga_name) === key);
    layer.bindTooltip(match ? match.lga_name : key);
  });
}

function renderRegionSnapshot(data) {
  clearAllLgaStyles();
  currentLgaLayer = null;

  data.results.forEach((r) => {
    const layer = lgaLayersByName[normalizeName(r.lga_name)];
    if (!layer) return;

    const meta = RISK_META[r.predicted_risk_class];
    const topProb = Math.max(
      r.probabilities.low_risk,
      r.probabilities.moderate_risk,
      r.probabilities.high_risk
    );
    layer.setStyle({
      color: meta.color,
      weight: 1,
      fillColor: meta.color,
      fillOpacity: opacityForConfidence(topProb),
    });
    layer.bindTooltip(`${r.lga_name}: ${meta.label} (${(topProb * 100).toFixed(0)}%)`);
  });

  if (geojsonLayer && geojsonLayer.getBounds().isValid()) {
    map.fitBounds(geojsonLayer.getBounds(), { padding: [20, 20] });
  }
}

function showSnapshotStatus(message, isError = false) {
  const el = document.getElementById("snapshotStatus");
  el.textContent = message;
  el.classList.remove("hidden", "error");
  if (isError) el.classList.add("error");
}

function hideSnapshotStatus() {
  document.getElementById("snapshotStatus").classList.add("hidden");
}

function exitSnapshotMode() {
  snapshotActive = false;
  const btn = document.getElementById("snapshotBtn");
  btn.textContent = "Show All-LGA Snapshot";
  btn.classList.remove("active");
  clearAllLgaStyles();
  resetLgaTooltips();
}

async function handleSnapshotClick() {
  const btn = document.getElementById("snapshotBtn");

  if (snapshotActive) {
    exitSnapshotMode();
    hideSnapshotStatus();
    return;
  }

  const month = document.getElementById("monthSelect").value;
  const year = document.getElementById("yearSelect").value;
  if (!month || !year) {
    showSnapshotStatus("Pick a month and year first.", true);
    return;
  }

  btn.disabled = true;
  btn.textContent = "Loading snapshot...";
  hideSnapshotStatus();

  try {
    const url = `${API_BASE}/predict-region?month=${month}&year=${year}`;
    const res = await fetch(url);
    const data = await res.json();

    if (!res.ok) {
      const message = data.details || data.error || `Request failed (status ${res.status})`;
      showSnapshotStatus(message, true);
      return;
    }

    renderRegionSnapshot(data);
    snapshotActive = true;
    btn.classList.add("active");
    btn.textContent = "Exit Snapshot Mode";
  } catch (err) {
    showSnapshotStatus("Couldn't reach the backend. Is your Flask server running?", true);
    console.error(err);
  } finally {
    btn.disabled = false;
  }
}

function bindSnapshotButton() {
  document.getElementById("snapshotBtn").addEventListener("click", handleSnapshotClick);
}

/* ---------- Dropdowns ---------- */

async function loadLgas() {
  try {
    const res = await fetch(`${API_BASE}/lgas`);
    if (!res.ok) throw new Error(`API returned status ${res.status}`);
    allLgas = await res.json();

    const states = [...new Set(allLgas.map((l) => l.state_name))].sort();
    const stateSelect = document.getElementById("stateSelect");
    stateSelect.innerHTML =
      '<option value="">Select a state</option>' +
      states.map((s) => `<option value="${s}">${s}</option>`).join("");

    populateHistoryStates(states);
    hideStatus();
  } catch (err) {
    showStatus(
      "Couldn't reach the backend at " + API_BASE + ". Is your Flask server running?",
      true
    );
    console.error(err);
  }
}

function populateLgaDropdown(stateName) {
  const lgaSelect = document.getElementById("lgaSelect");
  const predictBtn = document.getElementById("predictBtn");

  if (!stateName) {
    lgaSelect.innerHTML = '<option value="">Select a state first</option>';
    lgaSelect.disabled = true;
    predictBtn.disabled = true;
    return;
  }

  const lgasInState = allLgas
    .filter((l) => l.state_name === stateName)
    .sort((a, b) => a.lga_name.localeCompare(b.lga_name));

  lgaSelect.innerHTML =
    '<option value="">Select an LGA</option>' +
    lgasInState
      .map((l) => `<option value="${l.lga_id}">${l.lga_name}</option>`)
      .join("");
  lgaSelect.disabled = false;
  predictBtn.disabled = true; // re-enabled once an LGA is actually picked
}

function populateMonthYearDropdownsFor(monthId, yearId) {
  const monthSelect = document.getElementById(monthId);
  monthSelect.innerHTML = MONTH_NAMES
    .map((name, idx) => `<option value="${idx + 1}">${name}</option>`)
    .join("");

  // Historical data covers 2000-2024; months beyond that route through
  // live forecasting (up to ~7 months ahead) on the backend automatically.
  const yearSelect = document.getElementById(yearId);
  const years = [];
  for (let y = 2027; y >= 2000; y--) years.push(y);
  yearSelect.innerHTML = years
    .map((y) => `<option value="${y}">${y}</option>`)
    .join("");
}

function populateMonthYearDropdowns() {
  populateMonthYearDropdownsFor("monthSelect", "yearSelect");
  populateMonthYearDropdownsFor("monthSelect2", "yearSelect2");
}

/* ---------- Predict ---------- */

function setPredictLoading(isLoading) {
  const btn = document.getElementById("predictBtn");
  const isCompare = document.getElementById("compareToggle").checked;
  btn.disabled = isLoading;
  btn.textContent = isLoading
    ? (isCompare ? "Comparing..." : "Predicting...")
    : (isCompare ? "Compare Flood Risk" : "Predict Flood Risk");
}

function hideAllResultViews() {
  document.getElementById("resultTiles").classList.add("hidden");
  document.getElementById("resultDetail").classList.add("hidden");
  document.getElementById("compareResult").classList.add("hidden");
}

function renderResult(result) {
  if (snapshotActive) {
    exitSnapshotMode();
  }

  const meta = RISK_META[result.predicted_risk_class];
  const topProb = Math.max(
    result.probabilities.low_risk,
    result.probabilities.moderate_risk,
    result.probabilities.high_risk
  );

  hideAllResultViews();
  document.getElementById("resultTiles").classList.remove("hidden");
  document.getElementById("resultDetail").classList.remove("hidden");

  document.getElementById("tileRiskValue").textContent = meta.label;
  const riskBadge = document.getElementById("tileRiskBadge");
  riskBadge.textContent = meta.label;
  riskBadge.className = `risk-badge ${meta.css}`;

  document.getElementById("tileConfidence").textContent = `${(topProb * 100).toFixed(1)}%`;

  const modeEl = document.getElementById("tileMode");
  const modeSubEl = document.getElementById("tileModeSub");
  if (result.mode === "forecast") {
    modeEl.textContent = "Forecast";
    modeEl.className = "tile-value mode-value forecast";
    modeSubEl.textContent = "live Open-Meteo forecast";
  } else if (result.mode === "recent") {
    modeEl.textContent = "Recent";
    modeEl.className = "tile-value mode-value recent";
    modeSubEl.textContent = "observed weather (Open-Meteo archive)";
  } else {
    modeEl.textContent = "Historical";
    modeEl.className = "tile-value mode-value historical";
    modeSubEl.textContent = "recorded 2000-2024 data";
  }

  const topFeature = result.top_contributing_features[0];
  document.getElementById("tileTopFactor").textContent = topFeature
    ? topFeature.feature.replace(/_/g, " ")
    : "-";

  document.getElementById("resultLgaName").textContent =
    `${result.lga_name} — ${MONTH_NAMES[result.month - 1]} ${result.year}`;

  document.getElementById("probLow").style.width = `${result.probabilities.low_risk * 100}%`;
  document.getElementById("probLowPct").textContent = `${(result.probabilities.low_risk * 100).toFixed(1)}%`;

  document.getElementById("probModerate").style.width = `${result.probabilities.moderate_risk * 100}%`;
  document.getElementById("probModeratePct").textContent = `${(result.probabilities.moderate_risk * 100).toFixed(1)}%`;

  document.getElementById("probHigh").style.width = `${result.probabilities.high_risk * 100}%`;
  document.getElementById("probHighPct").textContent = `${(result.probabilities.high_risk * 100).toFixed(1)}%`;

  highlightLga(result.lga_name, result.predicted_risk_class, topProb);
  showAlertToast(result);
}

function fillComparePeriod(prefix, result, meta, topProb) {
  document.getElementById(`${prefix}Label`).textContent = `${MONTH_NAMES[result.month - 1]} ${result.year}`;
  const riskEl = document.getElementById(`${prefix}Risk`);
  riskEl.textContent = meta.label;
  riskEl.className = `compare-risk-value ${meta.css}`;
  document.getElementById(`${prefix}Confidence`).textContent = `${(topProb * 100).toFixed(1)}%`;
  document.getElementById(`${prefix}Mode`).textContent =
    result.mode.charAt(0).toUpperCase() + result.mode.slice(1);
}

function renderComparison(a, b) {
  if (snapshotActive) {
    exitSnapshotMode();
  }

  const metaA = RISK_META[a.predicted_risk_class];
  const metaB = RISK_META[b.predicted_risk_class];
  const topA = Math.max(a.probabilities.low_risk, a.probabilities.moderate_risk, a.probabilities.high_risk);
  const topB = Math.max(b.probabilities.low_risk, b.probabilities.moderate_risk, b.probabilities.high_risk);

  hideAllResultViews();
  document.getElementById("compareResult").classList.remove("hidden");

  const deltaClass = b.predicted_risk_class - a.predicted_risk_class;
  const deltaEl = document.getElementById("compareDelta");
  if (deltaClass > 0) {
    deltaEl.textContent = `Risk increased: ${metaA.label} → ${metaB.label}`;
    deltaEl.className = "compare-delta delta-up";
  } else if (deltaClass < 0) {
    deltaEl.textContent = `Risk decreased: ${metaA.label} → ${metaB.label}`;
    deltaEl.className = "compare-delta delta-down";
  } else {
    deltaEl.textContent = `Risk unchanged: ${metaA.label} in both periods`;
    deltaEl.className = "compare-delta delta-flat";
  }

  fillComparePeriod("compareA", a, metaA, topA);
  fillComparePeriod("compareB", b, metaB, topB);

  // Highlight using the second (later) period's result on the map.
  highlightLga(b.lga_name, b.predicted_risk_class, topB);
  showAlertToast(b);
}

async function fetchPrediction(lgaId, month, year) {
  const url = `${API_BASE}/predict?lga_id=${lgaId}&month=${month}&year=${year}`;
  const res = await fetch(url);
  const data = await res.json();
  if (!res.ok) {
    const message = data.details || data.error || `Request failed (status ${res.status})`;
    throw new Error(message);
  }
  return data;
}

async function handlePredictClick() {
  const lgaId = document.getElementById("lgaSelect").value;
  const month = document.getElementById("monthSelect").value;
  const year = document.getElementById("yearSelect").value;

  if (!lgaId || !month || !year) return;

  const isCompare = document.getElementById("compareToggle").checked;

  if (isCompare) {
    const month2 = document.getElementById("monthSelect2").value;
    const year2 = document.getElementById("yearSelect2").value;
    if (!month2 || !year2) return;

    setPredictLoading(true);
    hideStatus();

    try {
      const [dataA, dataB] = await Promise.all([
        fetchPrediction(lgaId, month, year),
        fetchPrediction(lgaId, month2, year2),
      ]);
      renderComparison(dataA, dataB);
    } catch (err) {
      showStatus(err.message, true);
      console.error(err);
    } finally {
      setPredictLoading(false);
    }
    return;
  }

  setPredictLoading(true);
  hideStatus();

  try {
    const data = await fetchPrediction(lgaId, month, year);
    renderResult(data);
  } catch (err) {
    showStatus(err.message, true);
    console.error(err);
  } finally {
    setPredictLoading(false);
  }
}


/* ---------- History & Analytics ---------- */

let historyChart = null;
let historyRecords = [];   // records for the LGA currently shown
let historyLgaName = "";

function populateHistoryStates(states) {
  document.getElementById("histStateSelect").innerHTML =
    '<option value="">Select a state</option>' +
    states.map((s) => `<option value="${s}">${s}</option>`).join("");
}

function populateHistoryLgas(stateName) {
  const lgaSelect = document.getElementById("histLgaSelect");
  const loadBtn = document.getElementById("histLoadBtn");

  if (!stateName) {
    lgaSelect.innerHTML = '<option value="">Select a state first</option>';
    lgaSelect.disabled = true;
    loadBtn.disabled = true;
    return;
  }

  const lgas = allLgas
    .filter((l) => l.state_name === stateName)
    .sort((a, b) => a.lga_name.localeCompare(b.lga_name));

  lgaSelect.innerHTML =
    '<option value="">Select an LGA</option>' +
    lgas.map((l) => `<option value="${l.lga_id}">${l.lga_name}</option>`).join("");
  lgaSelect.disabled = false;
  loadBtn.disabled = true;
}

// If the person already picked an LGA on the Predict tab, start the
// History tab on the same one so they don't have to choose twice.
function prefillHistoryFromPredict() {
  const histState = document.getElementById("histStateSelect");
  const histLga = document.getElementById("histLgaSelect");
  if (histState.value) return; // already chosen here, leave it alone

  const predState = document.getElementById("stateSelect").value;
  const predLga = document.getElementById("lgaSelect").value;
  if (!predState || !predLga) return;

  histState.value = predState;
  populateHistoryLgas(predState);
  histLga.value = predLga;
  document.getElementById("histLoadBtn").disabled = !histLga.value;
}

function showHistStatus(message, isError = false) {
  const el = document.getElementById("histStatus");
  el.textContent = message;
  el.classList.remove("hidden", "error");
  if (isError) el.classList.add("error");
}

function hideHistStatus() {
  document.getElementById("histStatus").classList.add("hidden");
}

function renderHistoryTiles(records) {
  const total = records.length;
  const counts = { 0: 0, 1: 0, 2: 0 };
  const highByMonth = new Array(12).fill(0);

  records.forEach((r) => {
    counts[r.predicted_risk_class] += 1;
    if (r.predicted_risk_class === 2) highByMonth[r.month - 1] += 1;
  });

  const pct = (n) => (total ? `${((n / total) * 100).toFixed(1)}% of months` : "-");

  document.getElementById("histTotal").textContent = total;
  document.getElementById("histHigh").textContent = counts[2];
  document.getElementById("histHighSub").textContent = pct(counts[2]);
  document.getElementById("histModerate").textContent = counts[1];
  document.getElementById("histModerateSub").textContent = pct(counts[1]);

  const maxHigh = Math.max(...highByMonth);
  document.getElementById("histPeak").textContent =
    maxHigh > 0 ? MONTH_NAMES[highByMonth.indexOf(maxHigh)] : "No high-risk months";

  document.getElementById("histTiles").classList.remove("hidden");
}

function renderHistoryChart(lgaName, records) {
  const labels = records.map((r) => `${r.year}-${String(r.month).padStart(2, "0")}`);
  const values = records.map((r) => r.predicted_risk_class);
  const colorFor = (cls) => RISK_META[cls].color;

  document.getElementById("histChartTitle").textContent =
    `${lgaName}: monthly flood risk, 2000 to 2024`;
  document.getElementById("histChartCard").classList.remove("hidden");

  if (historyChart) historyChart.destroy();

  const ctx = document.getElementById("histChart").getContext("2d");
  // Bars sit at height 1/2/3 so every month is visible, including Low months.
  const heights = values.map((v) => v + 1);
  const tierLabels = { 1: "Low", 2: "Moderate", 3: "High" };

  historyChart = new Chart(ctx, {
    type: "bar",
    data: {
      labels,
      datasets: [{
        data: heights,
        backgroundColor: values.map(colorFor),
        borderWidth: 0,
        barPercentage: 1,
        categoryPercentage: 1,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { mode: "nearest", intersect: false, axis: "x" },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            title: (items) => {
              const r = records[items[0].dataIndex];
              return `${MONTH_NAMES[r.month - 1]} ${r.year}`;
            },
            label: (item) => {
              const r = records[item.dataIndex];
              return [
                RISK_META[r.predicted_risk_class].label,
                `Low ${(r.probabilities.low_risk * 100).toFixed(1)}%`,
                `Moderate ${(r.probabilities.moderate_risk * 100).toFixed(1)}%`,
                `High ${(r.probabilities.high_risk * 100).toFixed(1)}%`,
              ];
            },
          },
        },
      },
      scales: {
        y: {
          min: 0,
          max: 3,
          ticks: {
            stepSize: 1,
            color: "#8fa3ad",
            callback: (v) => tierLabels[v] || "",
          },
          grid: { color: "rgba(22, 50, 79, 0.8)" },
        },
        x: {
          ticks: { color: "#8fa3ad", maxTicksLimit: 13, maxRotation: 0 },
          grid: { display: false },
        },
      },
    },
  });
}

function renderHistoryCalendar(lgaName, records) {
  const years = [...new Set(records.map((r) => r.year))].sort((a, b) => a - b);
  const byYearMonth = {};
  records.forEach((r) => {
    byYearMonth[`${r.year}-${r.month}`] = r;
  });

  let html =
    '<div class="cal-row cal-header"><div class="cal-year-label"></div>' +
    MONTH_NAMES.map((m) => `<div class="cal-month-label">${m.slice(0, 3)}</div>`).join("") +
    "</div>";

  years.forEach((year) => {
    html += `<div class="cal-row"><div class="cal-year-label">${year}</div>`;
    for (let m = 1; m <= 12; m++) {
      const rec = byYearMonth[`${year}-${m}`];
      if (rec) {
        const color = RISK_META[rec.predicted_risk_class].color;
        const topProb = Math.max(
          rec.probabilities.low_risk,
          rec.probabilities.moderate_risk,
          rec.probabilities.high_risk
        );
        const title = `${MONTH_NAMES[m - 1]} ${year}: ${RISK_META[rec.predicted_risk_class].label} (${(topProb * 100).toFixed(1)}%)`;
        html += `<div class="cal-cell" style="background:${color}" title="${title}"></div>`;
      } else {
        html += '<div class="cal-cell cal-cell-empty" title="No data"></div>';
      }
    }
    html += "</div>";
  });

  document.getElementById("histCalendarGrid").innerHTML = html;
  document.getElementById("histCalendarTitle").textContent =
    `${lgaName}: seasonal risk calendar, 2000 to 2024`;
  document.getElementById("histCalendarCard").classList.remove("hidden");
}

async function handleHistoryLoad() {
  const lgaId = document.getElementById("histLgaSelect").value;
  if (!lgaId) return;

  const loadBtn = document.getElementById("histLoadBtn");
  loadBtn.disabled = true;
  loadBtn.textContent = "Loading...";
  hideHistStatus();

  try {
    const res = await fetch(`${API_BASE}/history?lga_id=${lgaId}`);
    const data = await res.json();

    if (!res.ok) {
      showHistStatus(data.details || data.error || `Request failed (status ${res.status})`, true);
      return;
    }

    historyRecords = data.records;
    historyLgaName = data.lga_name;

    document.getElementById("histEmpty").classList.add("hidden");
    renderHistoryTiles(historyRecords);
    renderHistoryChart(historyLgaName, historyRecords);
    renderHistoryCalendar(historyLgaName, historyRecords);
    document.getElementById("histCsvBtn").disabled = false;
  } catch (err) {
    showHistStatus("Couldn't reach the backend. Is your Flask server running?", true);
    console.error(err);
  } finally {
    loadBtn.textContent = "Show history";
    loadBtn.disabled = !document.getElementById("histLgaSelect").value;
  }
}

function downloadHistoryCsv() {
  if (!historyRecords.length) return;

  const header = [
    "lga", "year", "month", "risk_class", "risk_label",
    "prob_low", "prob_moderate", "prob_high",
  ];
  const rows = historyRecords.map((r) => [
    `"${historyLgaName.replace(/"/g, '""')}"`,
    r.year,
    r.month,
    r.predicted_risk_class,
    RISK_META[r.predicted_risk_class].label,
    r.probabilities.low_risk,
    r.probabilities.moderate_risk,
    r.probabilities.high_risk,
  ]);

  const csv = [header.join(","), ...rows.map((r) => r.join(","))].join("\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);

  const a = document.createElement("a");
  a.href = url;
  a.download = `${historyLgaName.replace(/\s+/g, "_")}_flood_risk_history.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

/* ---------- Wiring ---------- */

function bindEvents() {
  document.getElementById("stateSelect").addEventListener("change", (e) => {
    populateLgaDropdown(e.target.value);
  });

  document.getElementById("lgaSelect").addEventListener("change", (e) => {
    document.getElementById("predictBtn").disabled = !e.target.value;
  });

  document.getElementById("predictBtn").addEventListener("click", handlePredictClick);

  document.getElementById("histStateSelect").addEventListener("change", (e) => {
    populateHistoryLgas(e.target.value);
  });
  document.getElementById("histLgaSelect").addEventListener("change", (e) => {
    document.getElementById("histLoadBtn").disabled = !e.target.value;
  });
  document.getElementById("histLoadBtn").addEventListener("click", handleHistoryLoad);
  document.getElementById("histCsvBtn").addEventListener("click", downloadHistoryCsv);

  bindResultsToggle();
  bindCompareToggle();
  bindSnapshotButton();
  bindTabs();
}

async function init() {
  initMap();
  populateMonthYearDropdowns();
  await loadGeoJSON();
  await loadLgas();
  bindEvents();

  map.invalidateSize();
  setTimeout(() => map.invalidateSize(), 200);
  window.addEventListener("resize", () => map.invalidateSize());
}

init();
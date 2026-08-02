"use strict";

let token = "";
let selected = null;
let pollTimer = null;

function escapeHtml(value) {
  const node = document.createElement("span");
  node.textContent = value == null ? "" : String(value);
  return node.innerHTML;
}

async function api(path, options = {}) {
  const headers = { Authorization: `Bearer ${token}`, ...(options.headers || {}) };
  const response = await fetch(path, { ...options, headers });
  const data = await response.json().catch(() => ({ error: `HTTP ${response.status}` }));
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

function stateBadge(state) {
  const good = state === "succeeded";
  const bad = state === "failed" || state === "cancelled";
  return `<span class="badge ${good ? "good" : bad ? "bad" : ""}">${escapeHtml(state)}</span>`;
}

async function loadMeta() {
  const meta = await api("/api/v1/meta");
  document.getElementById("meta").textContent = `${meta.agents.length} Agenten · Evaluatoren: ${meta.evaluators.join(", ")}`;
}

async function loadExperiments() {
  const experiments = await api("/api/v1/experiments");
  const container = document.getElementById("experiments");
  container.innerHTML = experiments.length ? experiments.map((item) => `
    <article class="card" data-id="${escapeHtml(item.experiment_id)}">
      <h3>${escapeHtml(item.name)}</h3>
      <div class="row"><span>${escapeHtml(item.experiment_id)}</span>${stateBadge(item.state)}</div>
      <div class="row"><span>Fortschritt</span><span>${item.progress?.done || 0}/${item.progress?.total || 0}</span></div>
    </article>`).join("") : "<p>Noch keine Experimente.</p>";
  container.querySelectorAll(".card").forEach((card) => card.addEventListener("click", () => selectExperiment(card.dataset.id)));
}

function summaryCards(summary) {
  return (summary?.groups || []).map((row) => `
    <article><strong>${escapeHtml(row.agent)} · ${escapeHtml(row.variant)}</strong>
      ${row.passed}/${row.completed} bestanden · ${row.errors} Fehler<br>
      Median ${row.median_duration_ms == null ? "n/a" : `${(row.median_duration_ms / 1000).toFixed(1)} s`} · p95 ${row.p95_duration_ms == null ? "n/a" : `${(row.p95_duration_ms / 1000).toFixed(1)} s`}
    </article>`).join("");
}

function trialRows(trials) {
  return trials.map((trial) => {
    const usage = trial.measurement?.usage || {};
    return `<tr>
      <td>${trial.sequence}</td><td>${escapeHtml(trial.source_agent_id)}</td><td>${escapeHtml(trial.variant_id)}</td>
      <td>${stateBadge(trial.state)}</td><td>${trial.evaluation ? (trial.evaluation.passed ? "ja" : "nein") : "–"}</td>
      <td>${trial.measurement?.duration_ms == null ? "–" : `${(trial.measurement.duration_ms / 1000).toFixed(1)} s`}</td>
      <td>${usage.input_tokens ?? "?"} / ${usage.output_tokens ?? "?"}</td>
      <td>${trial.result_artifact ? `<button class="result-link secondary" data-trial="${escapeHtml(trial.trial_id)}">anzeigen</button>` : escapeHtml(trial.error || "–")}</td>
    </tr>`;
  }).join("");
}

async function selectExperiment(id) {
  selected = id;
  const value = await api(`/api/v1/experiments/${id}`);
  document.getElementById("detail-panel").classList.remove("hidden");
  document.getElementById("detail-title").textContent = `${value.name} · ${value.experiment_id}`;
  document.getElementById("detail-state").innerHTML = `${stateBadge(value.state)} ${value.error ? `· ${escapeHtml(value.error)}` : ""}`;
  const done = value.progress?.done || 0, total = value.progress?.total || 0;
  document.getElementById("progress-fill").style.width = `${total ? 100 * done / total : 0}%`;
  document.getElementById("summary").innerHTML = summaryCards(value.summary);
  document.getElementById("trials").innerHTML = trialRows(value.trials || []);
  document.querySelectorAll(".result-link").forEach((button) => button.addEventListener("click", () => showResult(button.dataset.trial)));
  if (["running", "planned"].includes(value.state)) startPolling(); else stopPolling();
}

async function showResult(trialId) {
  const value = await api(`/api/v1/experiments/${selected}/results/${trialId}`);
  const output = document.getElementById("result");
  output.textContent = JSON.stringify(value, null, 2);
  output.classList.remove("hidden");
}

function startPolling() {
  if (pollTimer) return;
  pollTimer = setInterval(async () => { if (selected) { await selectExperiment(selected); await loadExperiments(); } }, 2000);
}
function stopPolling() { if (pollTimer) clearInterval(pollTimer); pollTimer = null; }

async function action(name) {
  if (!selected) return;
  await api(`/api/v1/experiments/${selected}/${name}`, { method: "POST" });
  await selectExperiment(selected);
}

async function initialize() {
  const fragment = new URLSearchParams(location.hash.slice(1));
  token = fragment.get("token") || "";
  history.replaceState(null, "", location.pathname);
  if (!token) throw new Error("Bearer-Token fehlt; Oberfläche über die Start-URL öffnen.");
  await api("/api/v1/health");
  const connection = document.getElementById("connection");
  connection.textContent = "verbunden"; connection.className = "badge good";
  await Promise.all([loadMeta(), loadExperiments()]);
}

document.getElementById("create-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = document.getElementById("message");
  try {
    const result = await api("/api/v1/experiments", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ manifest_path: document.getElementById("manifest-path").value }),
    });
    message.textContent = `Angelegt: ${result.experiment_id}`; message.className = "message";
    await loadExperiments(); await selectExperiment(result.experiment_id);
  } catch (error) { message.textContent = error.message; message.className = "message error"; }
});
document.getElementById("refresh").addEventListener("click", loadExperiments);
document.getElementById("run").addEventListener("click", () => action("run"));
document.getElementById("resume").addEventListener("click", () => action("resume"));
document.getElementById("cancel").addEventListener("click", () => action("cancel"));

initialize().catch((error) => {
  const connection = document.getElementById("connection");
  connection.textContent = error.message; connection.className = "badge bad";
});

"use strict";

const connection = document.querySelector("#connection");
const terminalOutput = document.querySelector("#terminal-output");
const statusOutput = document.querySelector("#status-output");
const sessionState = document.querySelector("#session-state");
const terminalInput = document.querySelector("#terminal-input");
const sendInput = document.querySelector("#send-input");
const inputForm = document.querySelector("#terminal-input-form");
const openLink = document.querySelector("#open-link");
const cancelSession = document.querySelector("#cancel-session");
const agentCards = document.querySelector("#agent-cards");
const dashboardOutput = document.querySelector("#dashboard-output");
const refreshDashboard = document.querySelector("#refresh-dashboard");

let bearerToken = "";
let activeSessionId = "";
let outputOffset = 0;
let pollTimer = null;
let latestLoginUrl = "";

function readToken() {
  const fragment = new URLSearchParams(window.location.hash.slice(1));
  bearerToken = fragment.get("token") || "";
  if (bearerToken) {
    history.replaceState(null, "", window.location.pathname);
  }
}

async function api(path, options = {}) {
  if (!bearerToken) {
    throw new Error("Kein API-Token in der Start-URL gefunden.");
  }
  const headers = new Headers(options.headers || {});
  headers.set("Authorization", `Bearer ${bearerToken}`);
  if (options.body) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(path, {...options, headers, cache: "no-store"});
  const payload = await response.json().catch(() => ({error: `HTTP ${response.status}`}));
  if (!response.ok) {
    throw new Error(payload.error || `HTTP ${response.status}`);
  }
  return payload;
}

function setConnection(message, kind = "ok") {
  connection.textContent = message;
  connection.dataset.kind = kind;
}

function setSessionControls(running) {
  terminalInput.disabled = !running;
  sendInput.disabled = !running;
  cancelSession.disabled = !running;
  document.querySelectorAll("[data-login-worker], [data-login-agent]").forEach((button) => {
    button.disabled = running;
  });
}

function extractLoginUrl(text) {
  const matches = text.match(/https:\/\/[^\s<>"']+/g) || [];
  const candidate = matches.find((value) => {
    try {
      const url = new URL(value.replace(/[),.;]+$/, ""));
      return url.protocol === "https:";
    } catch (_error) {
      return false;
    }
  });
  if (candidate) {
    latestLoginUrl = candidate.replace(/[),.;]+$/, "");
    openLink.disabled = false;
  }
}

function appendOutput(text, reset) {
  if (reset) {
    terminalOutput.textContent = "";
  }
  if (text) {
    terminalOutput.textContent += text;
    extractLoginUrl(terminalOutput.textContent);
    terminalOutput.scrollTop = terminalOutput.scrollHeight;
  }
}

async function pollSession() {
  if (!activeSessionId) return;
  try {
    const payload = await api(
      `/api/v1/login-sessions/${encodeURIComponent(activeSessionId)}?offset=${outputOffset}`,
    );
    appendOutput(payload.output, payload.offset_reset);
    outputOffset = payload.next_offset;
    sessionState.textContent = `${payload.worker}: ${payload.state}` +
      (payload.exit_code === null ? "" : ` (Exit ${payload.exit_code})`);
    const running = payload.state === "starting" || payload.state === "running";
    setSessionControls(running);
    if (running) {
      pollTimer = window.setTimeout(pollSession, 700);
    } else {
      activeSessionId = "";
      pollTimer = null;
    }
  } catch (error) {
    setConnection(`Session-Abfrage fehlgeschlagen: ${error.message}`, "error");
    setSessionControls(false);
  }
}

async function startLogin(targetType, targetId) {
  if (pollTimer) window.clearTimeout(pollTimer);
  activeSessionId = "";
  outputOffset = 0;
  latestLoginUrl = "";
  openLink.disabled = true;
  terminalOutput.textContent = "";
  sessionState.textContent = `${targetType} ${targetId}: Login wird gestartet …`;
  setSessionControls(true);
  try {
    const payload = await api("/api/v1/login-sessions", {
      method: "POST",
      body: JSON.stringify({[targetType]: targetId}),
    });
    activeSessionId = payload.id;
    appendOutput(payload.output, true);
    outputOffset = payload.next_offset;
    await pollSession();
  } catch (error) {
    sessionState.textContent = `Login konnte nicht gestartet werden: ${error.message}`;
    setSessionControls(false);
  }
}

async function checkStatus(worker) {
  statusOutput.textContent = `${worker}: Status wird geprüft …`;
  try {
    const payload = await api(`/api/v1/workers/${worker}/status`);
    statusOutput.textContent = [payload.output, payload.error].filter(Boolean).join("\n");
  } catch (error) {
    statusOutput.textContent = `Statusfehler: ${error.message}`;
  }
}

async function checkAgentStatus(agentId) {
  statusOutput.textContent = `${agentId}: Status wird geprüft …`;
  try {
    const payload = await api(`/api/v1/agents/${encodeURIComponent(agentId)}/status`);
    statusOutput.textContent = [payload.output, payload.error].filter(Boolean).join("\n");
  } catch (error) {
    statusOutput.textContent = `Statusfehler: ${error.message}`;
  }
}

async function loadDashboard() {
  dashboardOutput.textContent = "Control Plane wird gelesen …";
  try {
    const payload = await api("/api/v1/control-summary");
    dashboardOutput.textContent = JSON.stringify(payload, null, 2);
  } catch (error) {
    dashboardOutput.textContent = `Dashboardfehler: ${error.message}`;
  }
}

function renderAgents(agents) {
  agentCards.textContent = "";
  if (!agents.length) {
    const placeholder = document.createElement("article");
    placeholder.className = "worker-card placeholder-card";
    placeholder.textContent = "Keine Codex-/Claude-Agenten registriert.";
    agentCards.append(placeholder);
    return;
  }
  for (const agent of agents) {
    const card = document.createElement("article");
    card.className = "worker-card";
    const content = document.createElement("div");
    const tag = document.createElement("p");
    tag.className = "tag";
    tag.textContent = `${agent.engine} · isoliertes Credential`;
    const heading = document.createElement("h2");
    heading.textContent = agent.agent_id;
    const role = document.createElement("p");
    role.textContent = agent.role;
    content.append(tag, heading, role);
    const actions = document.createElement("div");
    actions.className = "actions";
    const status = document.createElement("button");
    status.className = "secondary";
    status.textContent = "Status prüfen";
    status.dataset.statusAgent = agent.agent_id;
    status.addEventListener("click", () => checkAgentStatus(agent.agent_id));
    const login = document.createElement("button");
    login.textContent = `${agent.agent_id} anmelden`;
    login.dataset.loginAgent = agent.agent_id;
    login.addEventListener("click", () => startLogin("agent", agent.agent_id));
    actions.append(status, login);
    card.append(content, actions);
    agentCards.append(card);
  }
}

document.querySelectorAll("[data-login-worker]").forEach((button) => {
  button.addEventListener("click", () => startLogin("worker", button.dataset.loginWorker));
});

document.querySelectorAll("[data-status-worker]").forEach((button) => {
  button.addEventListener("click", () => checkStatus(button.dataset.statusWorker));
});

inputForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!activeSessionId) return;
  const text = terminalInput.value;
  try {
    await api(`/api/v1/login-sessions/${encodeURIComponent(activeSessionId)}/input`, {
      method: "POST",
      body: JSON.stringify({text}),
    });
    terminalInput.value = "";
    terminalInput.focus();
  } catch (error) {
    setConnection(`Terminal-Eingabe fehlgeschlagen: ${error.message}`, "error");
  }
});

openLink.addEventListener("click", () => {
  if (latestLoginUrl) {
    window.open(latestLoginUrl, "_blank", "noopener,noreferrer");
  }
});

cancelSession.addEventListener("click", async () => {
  if (!activeSessionId) return;
  try {
    await api(`/api/v1/login-sessions/${encodeURIComponent(activeSessionId)}`, {
      method: "DELETE",
    });
    sessionState.textContent = "Abbruch angefordert …";
  } catch (error) {
    setConnection(`Abbruch fehlgeschlagen: ${error.message}`, "error");
  }
});

refreshDashboard.addEventListener("click", loadDashboard);

async function initialize() {
  readToken();
  if (!bearerToken) {
    setConnection(
      "Kein Zugriffstoken vorhanden. Öffne exakt die von ./hermesctl login-ui ausgegebene URL.",
      "error",
    );
    return;
  }
  try {
    const health = await api("/api/v1/health");
    const agents = await api("/api/v1/agents");
    renderAgents(agents.agents);
    await loadDashboard();
    setConnection(
      `Lokale API verbunden · Worker: ${health.workers.join(", ")} · Agenten: ${health.agents.length}`,
    );
  } catch (error) {
    setConnection(`API-Verbindung fehlgeschlagen: ${error.message}`, "error");
  }
}

initialize();

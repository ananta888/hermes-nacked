#!/usr/bin/env node
/** Narrow Unix-socket broker for one isolated coding CLI. */

import fs from "node:fs";
import net from "node:net";
import path from "node:path";
import { spawn } from "node:child_process";

const kind = String(process.env.WORKER_KIND || "").trim().toLowerCase();
const socketPath = process.env.WORKER_SOCKET || "/worker-socket/worker.sock";
const workerHome = process.env.WORKER_HOME || "/home/worker";
const workspace = process.env.WORKER_WORKSPACE || "/workspace";
const maxTimeout = Number.parseInt(process.env.WORKER_TIMEOUT_MAX || "1800", 10);
const maxPromptBytes = 64 * 1024;
const maxRequestBytes = 96 * 1024;
const maxOutputBytes = 4 * 1024 * 1024;

const specs = {
  codex: {
    binary: "codex",
    version: ["--version"],
    authStatus: ["login", "status"],
    run(prompt, model) {
      const args = [
        "--ask-for-approval", "never",
        "exec",
        "--ephemeral",
        "--sandbox", "workspace-write",
        "--skip-git-repo-check",
        "--ignore-user-config",
        "--ignore-rules",
      ];
      if (model) args.push("--model", model);
      args.push(prompt);
      return args;
    },
  },
  claude: {
    binary: "claude",
    version: ["--version"],
    authStatus: ["auth", "status"],
    run(prompt, model) {
      const args = [
        "-p",
        "--output-format", "json",
        "--permission-mode", "auto",
        "--safe-mode",
        "--no-session-persistence",
        "--max-turns", "30",
      ];
      if (model) args.push("--model", model);
      args.push(prompt);
      return args;
    },
  },
  opencode: {
    binary: "opencode",
    version: ["--version"],
    authStatus: ["auth", "list"],
    run(prompt, model) {
      const args = [
        "run", "--pure", "--format", "json", "--auto", "--dir", workspace,
      ];
      if (model) args.push("--model", model);
      args.push(prompt);
      return args;
    },
  },
};

if (!Object.hasOwn(specs, kind)) {
  throw new Error(`unsupported WORKER_KIND: ${kind || "(empty)"}`);
}

const spec = specs[kind];
let activeChild = null;

function readModel() {
  const modelPath = path.join(workerHome, ".worker-model");
  try {
    const model = fs.readFileSync(modelPath, "utf8").trim();
    if (!model) return "";
    if (model.length > 256 || model.includes("\n") || !/^[A-Za-z0-9_.:/@+\-]+$/.test(model)) {
      throw new Error("configured model contains unsupported characters");
    }
    return model;
  } catch (error) {
    if (error && error.code === "ENOENT") return "";
    throw error;
  }
}

function appendLimited(state, chunk) {
  const bytes = Buffer.isBuffer(chunk) ? chunk : Buffer.from(String(chunk));
  if (state.size >= maxOutputBytes) {
    state.truncated = true;
    return;
  }
  const remaining = maxOutputBytes - state.size;
  state.parts.push(bytes.subarray(0, remaining));
  state.size += Math.min(bytes.length, remaining);
  if (bytes.length > remaining) state.truncated = true;
}

function runProcess(args, timeoutSeconds) {
  return new Promise((resolve) => {
    const stdout = { parts: [], size: 0, truncated: false };
    const stderr = { parts: [], size: 0, truncated: false };
    const startedAt = Date.now();
    let timedOut = false;
    let settled = false;

    const child = spawn(spec.binary, args, {
      cwd: workspace,
      env: {
        ...process.env,
        HOME: workerHome,
        TERM: "dumb",
        NO_COLOR: "1",
      },
      stdio: ["ignore", "pipe", "pipe"],
    });
    activeChild = child;
    child.stdout.on("data", (chunk) => appendLimited(stdout, chunk));
    child.stderr.on("data", (chunk) => appendLimited(stderr, chunk));

    const timeout = setTimeout(() => {
      timedOut = true;
      child.kill("SIGTERM");
      setTimeout(() => child.kill("SIGKILL"), 5000).unref();
    }, timeoutSeconds * 1000);

    const finish = (exitCode, signal, spawnError = "") => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      if (activeChild === child) activeChild = null;
      const output = Buffer.concat(stdout.parts).toString("utf8").trim();
      const errorOutput = Buffer.concat(stderr.parts).toString("utf8").trim();
      resolve({
        exit_code: Number.isInteger(exitCode) ? exitCode : 1,
        signal: signal || null,
        timed_out: timedOut,
        duration_ms: Date.now() - startedAt,
        output,
        error: spawnError || errorOutput,
        truncated: stdout.truncated || stderr.truncated,
      });
    };

    child.on("error", (error) => finish(1, null, String(error.message || error)));
    child.on("close", (code, signal) => finish(code, signal));
  });
}

async function handleRequest(request) {
  if (!request || typeof request !== "object" || Array.isArray(request)) {
    return { ok: false, error: "request must be an object" };
  }
  if (activeChild) return { ok: false, error: `${kind} worker is busy` };

  const operation = String(request.operation || "");
  const model = readModel();
  if (operation === "status") {
    const version = await runProcess(spec.version, 30);
    const auth = await runProcess(spec.authStatus, 30);
    return {
      ok: true,
      worker: kind,
      model: model || null,
      workspace,
      version,
      auth: {
        exit_code: auth.exit_code,
        output: auth.output,
        error: auth.error,
      },
    };
  }

  if (operation !== "run") return { ok: false, error: "unsupported operation" };
  const prompt = typeof request.prompt === "string" ? request.prompt.trim() : "";
  if (!prompt) return { ok: false, error: "prompt must be a non-empty string" };
  if (Buffer.byteLength(prompt, "utf8") > maxPromptBytes) {
    return { ok: false, error: "prompt exceeds 64 KiB" };
  }
  const requestedTimeout = Number.parseInt(String(request.timeout_seconds || "900"), 10);
  const timeoutSeconds = Math.min(Math.max(requestedTimeout || 900, 30), maxTimeout);
  const result = await runProcess(spec.run(prompt, model), timeoutSeconds);
  return {
    ok: result.exit_code === 0 && !result.timed_out,
    worker: kind,
    model: model || null,
    ...result,
  };
}

fs.mkdirSync(path.dirname(socketPath), { recursive: true });
try {
  const stat = fs.lstatSync(socketPath);
  if (!stat.isSocket()) throw new Error(`${socketPath} exists and is not a socket`);
  fs.unlinkSync(socketPath);
} catch (error) {
  if (!error || error.code !== "ENOENT") {
    if (!String(error.message || error).includes("exists and is not a socket")) throw error;
    throw error;
  }
}

const server = net.createServer((socket) => {
  let requestData = Buffer.alloc(0);
  let handled = false;
  socket.setTimeout((maxTimeout + 30) * 1000);

  socket.on("data", async (chunk) => {
    if (handled) return;
    requestData = Buffer.concat([requestData, chunk]);
    if (requestData.length > maxRequestBytes) {
      handled = true;
      socket.end(`${JSON.stringify({ ok: false, error: "request too large" })}\n`);
      return;
    }
    const newline = requestData.indexOf(10);
    if (newline < 0) return;
    handled = true;
    try {
      const request = JSON.parse(requestData.subarray(0, newline).toString("utf8"));
      const response = await handleRequest(request);
      socket.end(`${JSON.stringify(response)}\n`);
    } catch (error) {
      socket.end(`${JSON.stringify({ ok: false, error: String(error.message || error) })}\n`);
    }
  });

  socket.on("timeout", () => socket.destroy());
});

server.listen(socketPath, () => {
  fs.chmodSync(socketPath, 0o660);
  process.stdout.write(`${kind} worker listening on ${socketPath}\n`);
});

function shutdown() {
  if (activeChild) activeChild.kill("SIGTERM");
  server.close(() => process.exit(0));
  setTimeout(() => process.exit(1), 5000).unref();
}

process.on("SIGTERM", shutdown);
process.on("SIGINT", shutdown);

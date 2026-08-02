#!/usr/bin/env node
/** Narrow Unix-socket broker for one policy-controlled coding CLI. */

import fs from "node:fs";
import net from "node:net";
import path from "node:path";
import { spawn } from "node:child_process";

const kind = String(process.env.WORKER_KIND || "").trim().toLowerCase();
const socketPath = process.env.WORKER_SOCKET || "/worker-socket/worker.sock";
const workerHome = process.env.WORKER_HOME || "/home/worker";
const workspace = process.env.WORKER_WORKSPACE || "/workspace";
const controlFile = process.env.WORKER_CONTROL_FILE || "/worker-control/capabilities";
const contextRoot = process.env.WORKER_CONTEXT || "/worker-context";
const maxTimeout = Number.parseInt(process.env.WORKER_TIMEOUT_MAX || "1800", 10);
const maxPromptBytes = 64 * 1024;
const maxRequestBytes = 96 * 1024;
const maxOutputBytes = 4 * 1024 * 1024;
const maxInstructionBytes = 128 * 1024;
const maxWorkspaceEntries = 250000;
const workers = new Set(["codex", "claude", "opencode"]);
const features = new Set(["tools", "commandline", "skills", "agents-md", "claude-md"]);
const featureAliases = new Map([
  ["tool", "tools"],
  ["tool-use", "tools"],
  ["tooluse", "tools"],
  ["shell", "commandline"],
  ["terminal", "commandline"],
  ["agents", "agents-md"],
  ["agents.md", "agents-md"],
  ["agentsmd", "agents-md"],
  ["claude", "claude-md"],
  ["claude.md", "claude-md"],
  ["claudemd", "claude-md"],
]);

if (!workers.has(kind)) {
  throw new Error(`unsupported WORKER_KIND: ${kind || "(empty)"}`);
}

function normalizeFeature(value) {
  const normalized = String(value).trim().toLowerCase().replaceAll("_", "-");
  return featureAliases.get(normalized) || normalized;
}

function readPolicy() {
  const raw = fs.readFileSync(controlFile, "utf8");
  const enabled = new Set(
    raw.replaceAll(",", " ").split(/\s+/u).map(normalizeFeature).filter(Boolean),
  );
  const unknown = [...enabled].filter((item) => !features.has(item)).sort();
  if (unknown.length) throw new Error(`unknown worker features: ${unknown.join(", ")}`);
  if (enabled.has("commandline") && !enabled.has("tools")) {
    throw new Error("worker commandline requires tools; policy is invalid");
  }
  return Object.freeze({
    features: [...enabled].sort(),
    tools: enabled.has("tools"),
    commandline: enabled.has("commandline"),
    skills: enabled.has("skills"),
    agents_md: enabled.has("agents-md"),
    claude_md: enabled.has("claude-md"),
  });
}

function readContextFile(filename) {
  const target = path.join(contextRoot, filename);
  const stat = fs.lstatSync(target);
  if (!stat.isFile() || stat.isSymbolicLink()) {
    throw new Error(`protected context is not a regular file: ${filename}`);
  }
  return fs.readFileSync(target, "utf8").trim();
}

function readApprovedSkills() {
  const skillsRoot = path.join(contextRoot, "skills");
  const blocks = [];
  const entries = fs.readdirSync(skillsRoot, { withFileTypes: true })
    .filter((entry) => entry.isDirectory() && !entry.isSymbolicLink())
    .sort((left, right) => left.name.localeCompare(right.name));
  for (const entry of entries) {
    const skillPath = path.join(skillsRoot, entry.name, "SKILL.md");
    let stat;
    try {
      stat = fs.lstatSync(skillPath);
    } catch (error) {
      if (error && error.code === "ENOENT") continue;
      throw error;
    }
    if (!stat.isFile() || stat.isSymbolicLink()) {
      throw new Error(`approved skill is not a regular file: ${entry.name}/SKILL.md`);
    }
    blocks.push({ name: entry.name, content: fs.readFileSync(skillPath, "utf8").trim() });
  }
  return blocks;
}

function buildInstructions(policy) {
  const blocks = [];
  if (policy.agents_md) {
    blocks.push(`<agents-md>\n${readContextFile("AGENTS.md")}\n</agents-md>`);
  }
  if (policy.claude_md) {
    blocks.push(`<claude-md>\n${readContextFile("CLAUDE.md")}\n</claude-md>`);
  }
  if (policy.skills) {
    const skillBlocks = readApprovedSkills();
    if (skillBlocks.length) {
      blocks.push(
        "<approved-worker-skills>\n" +
        "Use these SKILL.md instructions only when their descriptions match the delegated task.\n\n" +
        skillBlocks.map((skill) => (
          `<skill name=${JSON.stringify(skill.name)}>\n${skill.content}\n</skill>`
        )).join("\n\n") +
        "\n</approved-worker-skills>",
      );
    }
  }
  const result = blocks.join("\n\n");
  if (Buffer.byteLength(result, "utf8") > maxInstructionBytes) {
    throw new Error("enabled worker context exceeds 128 KiB");
  }
  return result;
}

function isRecognizedSkillPath(relativePath) {
  const normalized = `/${relativePath.split(path.sep).join("/")}`;
  if (!normalized.endsWith("/SKILL.md")) return false;
  return ["/.agents/skills/", "/.claude/skills/", "/.codex/skills/", "/.opencode/skills/"]
    .some((marker) => normalized.includes(marker));
}

function workspaceCustomizationViolations(policy) {
  const violations = [];
  const pending = [workspace];
  let visited = 0;
  while (pending.length) {
    const current = pending.pop();
    const entries = fs.readdirSync(current, { withFileTypes: true });
    for (const entry of entries) {
      visited += 1;
      if (visited > maxWorkspaceEntries) {
        throw new Error("workspace customization scan exceeds 250000 entries");
      }
      const absolute = path.join(current, entry.name);
      const relative = path.relative(workspace, absolute);
      if (entry.isDirectory() && !entry.isSymbolicLink()) pending.push(absolute);
      if (!entry.isFile() || entry.isSymbolicLink()) continue;
      if (!policy.agents_md && ["AGENTS.md", "AGENTS.override.md"].includes(entry.name)) {
        violations.push({ feature: "agents-md", path: relative });
      }
      if (!policy.claude_md && entry.name === "CLAUDE.md") {
        violations.push({ feature: "claude-md", path: relative });
      }
      const inClaudeRules = relative.split(path.sep).includes(".claude") &&
        relative.split(path.sep).includes("rules") && entry.name.endsWith(".md");
      if (!policy.claude_md && inClaudeRules) {
        violations.push({ feature: "claude-md", path: relative });
      }
      if (!policy.skills && isRecognizedSkillPath(relative)) {
        violations.push({ feature: "skills", path: relative });
      }
      if (violations.length >= 20) return violations;
    }
  }
  return violations;
}

function createInstructionFile(instructions) {
  if (!instructions) return { path: "", cleanup() {} };
  const temporaryDirectory = fs.mkdtempSync("/tmp/hermes-worker-context-");
  const instructionPath = path.join(temporaryDirectory, "instructions.md");
  fs.writeFileSync(instructionPath, `${instructions}\n`, { encoding: "utf8", mode: 0o600 });
  return {
    path: instructionPath,
    cleanup() {
      fs.rmSync(temporaryDirectory, { recursive: true, force: true });
    },
  };
}

function codexInvocation(prompt, model, policy, instructions) {
  const args = [
    "--ask-for-approval", "never",
    "--strict-config",
    "--disable", "apps",
    "--disable", "browser_use",
    "--disable", "browser_use_external",
    "--disable", "browser_use_full_cdp_access",
    "--disable", "code_mode_host",
    "--disable", "computer_use",
    "--disable", "goals",
    "--disable", "hooks",
    "--disable", "image_generation",
    "--disable", "multi_agent",
    "--disable", "plugins",
    "--disable", "remote_plugin",
    policy.tools ? "--enable" : "--disable", "shell_tool",
    "-c", "project_doc_max_bytes=0",
    "-c", 'web_search="disabled"',
  ];
  if (instructions) args.push("-c", `developer_instructions=${JSON.stringify(instructions)}`);
  if (model) args.push("--model", model);
  args.push(
    "exec",
    "--ephemeral",
    "--sandbox", policy.commandline ? "workspace-write" : "read-only",
    "--skip-git-repo-check",
    "--ignore-user-config",
    "--ignore-rules",
    prompt,
  );
  return { args, env: {}, cleanup() {} };
}

function claudeInvocation(prompt, model, policy, instructions) {
  const instructionFile = createInstructionFile(instructions);
  const allowedTools = policy.tools
    ? ["Read", "Glob", "Grep", "Edit", "Write", ...(policy.commandline ? ["Bash"] : [])]
    : [];
  const args = [
    "-p",
    "--output-format", "json",
    "--permission-mode", "auto",
    "--safe-mode",
    "--strict-mcp-config",
    "--mcp-config", '{"mcpServers":{}}',
    "--disable-slash-commands",
    "--tools", allowedTools.join(","),
    "--no-session-persistence",
    "--max-turns", "30",
  ];
  if (instructionFile.path) args.push("--append-system-prompt-file", instructionFile.path);
  if (model) args.push("--model", model);
  args.push(prompt);
  return { args, env: {}, cleanup: instructionFile.cleanup };
}

function opencodeInvocation(prompt, model, policy, instructions) {
  const instructionFile = createInstructionFile(instructions);
  const permission = {
    "*": "deny",
    external_directory: "deny",
    doom_loop: "deny",
    task: "deny",
    skill: "deny",
    lsp: "deny",
    question: "deny",
    webfetch: "deny",
    websearch: "deny",
    read: policy.tools ? "allow" : "deny",
    glob: policy.tools ? "allow" : "deny",
    grep: policy.tools ? "allow" : "deny",
    edit: policy.tools ? "allow" : "deny",
    bash: policy.commandline ? "allow" : "deny",
  };
  const config = {
    autoupdate: false,
    share: "disabled",
    permission,
    instructions: instructionFile.path ? [instructionFile.path] : [],
  };
  const args = ["run", "--pure", "--format", "json", "--auto", "--dir", workspace];
  if (model) args.push("--model", model);
  args.push(prompt);
  return {
    args,
    env: { OPENCODE_CONFIG_CONTENT: JSON.stringify(config) },
    cleanup: instructionFile.cleanup,
  };
}

const specs = {
  codex: {
    binary: "codex",
    version: ["--version"],
    authStatus: ["login", "status"],
    invocation: codexInvocation,
  },
  claude: {
    binary: "claude",
    version: ["--version"],
    authStatus: ["auth", "status"],
    invocation: claudeInvocation,
  },
  opencode: {
    binary: "opencode",
    version: ["--version"],
    authStatus: ["auth", "list"],
    invocation: opencodeInvocation,
  },
};

const spec = specs[kind];
let activeChild = null;

function enforcementSummary(policy) {
  if (kind === "codex") {
    return {
      tools: policy.tools ? "shell_tool in read-only sandbox" : "shell_tool disabled",
      commandline: policy.commandline ? "workspace-write sandbox" : "no workspace writes",
      skills: policy.skills ? "approved SKILL.md bodies injected" : "skills absent",
      custom_context: "automatic project instructions disabled; protected context injected explicitly",
    };
  }
  if (kind === "claude") {
    return {
      tools: policy.tools ? "Read,Glob,Grep,Edit,Write" : "all built-in tools disabled",
      commandline: policy.commandline ? "Bash enabled" : "Bash absent",
      skills: policy.skills ? "approved SKILL.md bodies injected" : "skills absent",
      custom_context: "safe mode; protected context injected explicitly",
    };
  }
  return {
    tools: policy.tools ? "read,glob,grep,edit allowed" : "file tools denied",
    commandline: policy.commandline ? "bash allowed" : "bash denied",
    skills: policy.skills ? "approved SKILL.md bodies injected; native Skill denied" : "skills absent",
    custom_context: "project config/external skills disabled; explicit permission deny-list",
  };
}

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

function runProcess(args, timeoutSeconds, environment = {}, cleanup = () => {}) {
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
        ...environment,
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
      try {
        cleanup();
      } catch (error) {
        if (!spawnError) spawnError = `temporary context cleanup failed: ${error.message || error}`;
      }
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
  const policy = readPolicy();
  if (operation === "status") {
    const version = await runProcess(spec.version, 30);
    const auth = await runProcess(spec.authStatus, 30);
    return {
      ok: true,
      worker: kind,
      model: model || null,
      workspace,
      policy,
      enforcement: enforcementSummary(policy),
      protected_context: contextRoot,
      workspace_customization_guard: "fail-closed",
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
  const violations = workspaceCustomizationViolations(policy);
  if (violations.length) {
    return {
      ok: false,
      worker: kind,
      policy,
      error: "workspace contains customization sources whose worker feature is disabled",
      violations,
    };
  }
  const requestedTimeout = Number.parseInt(String(request.timeout_seconds || "900"), 10);
  const timeoutSeconds = Math.min(Math.max(requestedTimeout || 900, 30), maxTimeout);
  const instructions = buildInstructions(policy);
  const invocation = spec.invocation(prompt, model, policy, instructions);
  const result = await runProcess(
    invocation.args,
    timeoutSeconds,
    invocation.env,
    invocation.cleanup,
  );
  return {
    ok: result.exit_code === 0 && !result.timed_out,
    worker: kind,
    model: model || null,
    policy,
    enforcement: enforcementSummary(policy),
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
      socket.end(`${JSON.stringify({ ok: false, worker: kind, error: String(error.message || error) })}\n`);
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

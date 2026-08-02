---
name: agents-mcp
description: Orchestrate registered Hermes agent instances, asynchronous jobs, immutable handoff artifacts, and applied teams through the scoped agents MCP server. Use when the user explicitly asks to delegate to named agents or coordinate a team and the skills and agents-mcp capabilities are active.
---

# Registered Agents MCP

Use only tools whose names start with `mcp__agents__`. Credentials, login,
Docker, rights mutation, arbitrary files, and team definition changes are not
part of this server.

## Choose an operation

- Discover with `mcp__agents__list`; inspect one agent with
  `mcp__agents__status` before delegation.
- Use `mcp__agents__run` for one synchronous bounded task.
- Use `mcp__agents__job_submit` for asynchronous work, then poll only its job
  id with `mcp__agents__job_status`. Cancel only on explicit request with
  `mcp__agents__job_cancel`.
- Read a checksum-verified text/JSON/patch handoff up to 1 MiB with
  `mcp__agents__artifact_get`.
- Inspect applied teams with `mcp__agents__team_list` and
  `mcp__agents__team_status`.

Pass the full goal, constraints, inputs, acceptance checks, and desired output
in each prompt. Jobs may depend on earlier job ids and immutable artifact ids;
agents never share a writable workspace implicitly. Do not submit the same
task both synchronously and asynchronously or through a direct skill.

Agent rights are enforced on the next task. If rights are insufficient, state
the smallest operator change and its risk; do not broaden another agent.
Codex is `[special]`: inspect, edit, commandline, and network form one explicit
bundle because its inner read-only sandbox cannot run here. Prefer Claude or
OpenCode for a native file/Bash split.

Never request or relay login codes. If a socket or authentication is missing,
tell the operator to start the agent and use the local subscription login UI.

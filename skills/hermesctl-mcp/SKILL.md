---
name: hermesctl-mcp
description: Inspect, explain, and change access for Hermes, Codex CLI, Claude Code, or OpenCode through scoped local MCP tools and their common access vocabulary. Use when the user explicitly asks Hermes to explain, enable, disable, or reset a target's tool-use, commandline, skills, AGENTS.md, or CLAUDE.md rights and the skills and hermesctl-mcp capabilities are active.
---

# Hermesctl MCP

Use only the tools whose names start with `mcp__hermesctl__`. The MCP server
accepts capability-management operations only; it is not a general command
executor.

## Workflow

1. Call `mcp__hermesctl__access_status` with exactly one target before a
   policy change.
2. Call `mcp__hermesctl__access_explain` when a mapping is marked special,
   controlled, or unclear.
3. Explain the least set of rights needed and its risk.
4. Obtain an explicit user request before calling an enable, disable, or reset
   tool.
5. Call the matching status tool again. Hermes changes apply only to a newly
   started Hermes session; worker changes apply to that worker's next task.

## Tools

- `mcp__hermesctl__access_status`: inspect one target using common terms.
- `mcp__hermesctl__access_explain`: show mappings and safe alternatives.
- `mcp__hermesctl__access_enable`: enable common features for one target.
- `mcp__hermesctl__access_disable`: disable common features for one target.
- `mcp__hermesctl__access_reset`: reset only that target.

Targets are `hermes`, `codex`, `claude`, and `opencode`. Their shared feature
names are `tool-use`, `commandline`, `skills`, `agents-md`, and `claude-md`.
Use these five tools by default. The following nine tools are retained for
compatibility and Hermes-only advanced capabilities:

- `mcp__hermesctl__status`: inspect effective policy.
- `mcp__hermesctl__list_capabilities`: list switches and dependencies.
- `mcp__hermesctl__enable`: enable one or more named capabilities.
- `mcp__hermesctl__disable`: disable one or more named capabilities.
- `mcp__hermesctl__reset`: return to the zero-capability state.
- `mcp__hermesctl__worker_rights`: inspect one worker's separate policy.
- `mcp__hermesctl__worker_enable`: enable named features for one worker.
- `mcp__hermesctl__worker_disable`: disable named features for one worker.
- `mcp__hermesctl__worker_reset`: return one worker to model-only operation.

Valid capabilities are `files`, `commandline`, `skills`, `web`, `code`,
`planning`, `shell-network`, `orchestrator`, `claude-md`, `hermesctl-direct`, and
`hermesctl-mcp`, plus `codex-direct`, `codex-mcp`, `claude-direct`,
`claude-mcp`, `opencode-direct`, and `opencode-mcp`.

For Hermes itself, `agents-md` and `AGENTS.md` alias `orchestrator`; the
independent `claude-md` capability loads the protected root CLAUDE.md.

Worker features are `tools`, `commandline`, `skills`, `agents-md`, and
`claude-md`; `commandline` requires `tools`. Always pass exactly one of
`codex`, `claude`, or `opencode` and never broaden another worker's profile.

Treat `[special]` and `[controlled]` from `access_explain` as actionable
warnings. Codex `tool-use` is its shell tool inside a read-only sandbox; offer
model-only, inspection-only, or Claude/OpenCode as the returned alternatives.
Worker `skills` injects reviewed SKILL.md bodies and leaves native dynamic
skill discovery disabled.

Never use unrelated MCP servers, edit the capability file directly, or imply
that the current session's tool list changed. If these MCP tools are absent,
stop and tell the operator to enable `skills hermesctl-mcp` for the next
session.

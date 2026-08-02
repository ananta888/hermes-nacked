---
name: hermesctl-mcp
description: Inspect and change the capability policy of this Hermes Naked installation through its scoped local MCP server. Use when the user explicitly asks Hermes to explain, enable, disable, or reset its own permissions and the skills and hermesctl-mcp capabilities are active.
---

# Hermesctl MCP

Use only the tools whose names start with `mcp__hermesctl__`. The MCP server
accepts capability-management operations only; it is not a general command
executor.

## Workflow

1. Call `mcp__hermesctl__status` before making a change.
2. Use `mcp__hermesctl__list_capabilities` when capability names or
   dependencies are unclear.
3. Explain the least set of rights needed and its risk.
4. Obtain an explicit user request before calling an enable, disable, or reset
   tool.
5. Call `mcp__hermesctl__status` again and report that changes apply only to a
   newly started Hermes session.

## Tools

- `mcp__hermesctl__status`: inspect effective policy.
- `mcp__hermesctl__list_capabilities`: list switches and dependencies.
- `mcp__hermesctl__enable`: enable one or more named capabilities.
- `mcp__hermesctl__disable`: disable one or more named capabilities.
- `mcp__hermesctl__reset`: return to the zero-capability state.

Valid capabilities are `files`, `commandline`, `skills`, `web`, `code`,
`planning`, `shell-network`, `orchestrator`, `hermesctl-direct`, and
`hermesctl-mcp`, plus `codex-direct`, `codex-mcp`, `claude-direct`,
`claude-mcp`, `opencode-direct`, and `opencode-mcp`.

Never use unrelated MCP servers, edit the capability file directly, or imply
that the current session's tool list changed. If these MCP tools are absent,
stop and tell the operator to enable `skills hermesctl-mcp` for the next
session.

---
name: hermesctl-direct
description: Inspect and change the capability policy of this Hermes Naked installation through the restricted hermesctl CLI. Use when the user explicitly asks Hermes to explain, enable, disable, or reset its own permissions and the skills, commandline, and hermesctl-direct capabilities are active.
---

# Hermesctl Direct

Use only the mounted `hermesctl` command. It can manage capability state but
cannot start containers, authenticate, install skills, or access Docker
administration from the command sandbox.

## Workflow

1. Run `hermesctl status` before making a change.
2. Explain the least set of rights needed and its risk.
3. Obtain an explicit user request before `enable`, `disable`, or `reset`.
4. Run exactly one of the allowed commands.
5. Verify with `hermesctl status` and report that the new policy applies to the
   next Hermes session, not the current tool snapshot.

## Commands

```bash
hermesctl status
hermesctl capabilities
hermesctl enable files
hermesctl disable web
hermesctl reset
```

Valid capabilities are `files`, `commandline`, `skills`, `web`, `code`,
`planning`, `shell-network`, `orchestrator`, `hermesctl-direct`, and
`hermesctl-mcp`, plus `codex-direct`, `codex-mcp`, `claude-direct`,
`claude-mcp`, `opencode-direct`, and `opencode-mcp`.

Respect dependencies: `shell-network` needs `commandline` or `code`;
`hermesctl-direct` needs `commandline` and `skills`; `hermesctl-mcp` needs
`skills`. Every worker `*-direct` capability needs `commandline` and `skills`;
every worker `*-mcp` capability needs `skills`.

Never edit the capability file directly, invoke Docker, use host paths, or
claim that the running session gained tools. If `hermesctl` is unavailable,
stop and tell the operator which prerequisite capability is missing.

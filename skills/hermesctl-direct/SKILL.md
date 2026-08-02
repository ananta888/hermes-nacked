---
name: hermesctl-direct
description: Inspect and change the Hermes capability policy or one isolated coding worker's separate feature policy through the restricted hermesctl CLI. Use when the user explicitly asks Hermes to explain, enable, disable, or reset Hermes or worker permissions and the skills, commandline, and hermesctl-direct capabilities are active.
---

# Hermesctl Direct

Use only the mounted `hermesctl` command. It can manage Hermes capability
state and the separate worker feature files, but cannot start containers,
authenticate, install skills, or access Docker administration from the command
sandbox.

## Workflow

1. Run `hermesctl status` before a Hermes policy change, or
   `hermesctl worker <worker> rights` before a worker policy change.
2. Explain the least set of rights needed and its risk.
3. Obtain an explicit user request before `enable`, `disable`, or `reset`.
4. Run exactly one of the allowed commands.
5. Verify with the matching status command. Hermes policy applies to the next
   Hermes session; worker policy applies to that worker's next task.

## Commands

```bash
hermesctl status
hermesctl capabilities
hermesctl enable files
hermesctl disable web
hermesctl reset
hermesctl worker codex rights
hermesctl worker codex capabilities
hermesctl worker codex enable tools agents-md
hermesctl worker codex disable agents-md
hermesctl worker codex reset
```

Valid capabilities are `files`, `commandline`, `skills`, `web`, `code`,
`planning`, `shell-network`, `orchestrator`, `claude-md`, `hermesctl-direct`, and
`hermesctl-mcp`, plus `codex-direct`, `codex-mcp`, `claude-direct`,
`claude-mcp`, `opencode-direct`, and `opencode-mcp`.

Respect dependencies: `shell-network` needs `commandline` or `code`;
`hermesctl-direct` needs `commandline` and `skills`; `hermesctl-mcp` needs
`skills`. Every worker `*-direct` capability needs `commandline` and `skills`;
every worker `*-mcp` capability needs `skills`.

For Hermes itself, `agents-md` and `AGENTS.md` alias `orchestrator`; the
independent `claude-md` capability loads the protected root CLAUDE.md.

Worker features are `tools`, `commandline`, `skills`, `agents-md`, and
`claude-md`. They are independent for Codex, Claude, and OpenCode;
`commandline` requires `tools`. Never enable a feature for a different worker
as a convenience. The feature files are not Hermes session capabilities.

Never edit the capability file directly, invoke Docker, use host paths, or
claim that the running session gained tools. If `hermesctl` is unavailable,
stop and tell the operator which prerequisite capability is missing.

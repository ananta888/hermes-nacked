---
name: hermesctl-direct
description: Inspect, explain, and change access for Hermes, Codex CLI, Claude Code, or OpenCode through the restricted hermesctl CLI and its common access vocabulary. Use when the user explicitly asks Hermes to explain, enable, disable, or reset a target's tool-use, commandline, skills, AGENTS.md, or CLAUDE.md rights and the skills, commandline, and hermesctl-direct capabilities are active.
---

# Hermesctl Direct

Use only the mounted `hermesctl` command. It can manage Hermes capability
state and the separate worker feature files, but cannot start containers,
authenticate, install skills, or access Docker administration from the command
sandbox.

## Workflow

1. Run `hermesctl access <target> status` before a policy change.
2. Run `hermesctl access <target> explain` when a mapping is marked special,
   controlled, or unclear. Explain the least set of rights and its risk.
3. Obtain an explicit user request before `enable`, `disable`, or `reset`.
4. Run exactly one of the allowed commands.
5. Verify with the matching status command. Hermes policy applies to the next
   Hermes session; worker policy applies to that worker's next task.

## Commands

```bash
hermesctl access hermes status
hermesctl access codex explain
hermesctl access claude capabilities
hermesctl access codex enable tool-use agents-md
hermesctl access codex enable commandline
hermesctl access codex disable claude-md
hermesctl access codex reset
hermesctl agent list
hermesctl agent rights backend
hermesctl agent explain backend
hermesctl agent grant backend inspect edit
hermesctl agent revoke backend edit
hermesctl agent reset backend
```

Registered agents use `inspect`, `edit`, `commandline`, `network`, `skills`,
`agents-md`, and `claude-md`. Check `agent rights` and `agent explain` before
every change. The mounted registry is redacted; credential, login, Docker,
team, job, artifact, model, and lifecycle commands remain unavailable.

Targets are `hermes`, `codex`, `claude`, and `opencode`. Their shared feature
names are `tool-use`, `commandline`, `skills`, `agents-md`, and `claude-md`.
Use this interface by default. The older `hermesctl enable ...` and
`hermesctl worker ...` forms remain available for compatibility and for the
Hermes-only advanced capabilities listed below.

Valid capabilities are `files`, `commandline`, `skills`, `web`, `code`,
`planning`, `shell-network`, `orchestrator`, `claude-md`, `hermesctl-direct`, and
`hermesctl-mcp`, plus `codex-direct`, `codex-mcp`, `claude-direct`,
`claude-mcp`, `opencode-direct`, and `opencode-mcp`.
The generic instance capabilities are `agents-direct` and `agents-mcp`.

Respect dependencies: `shell-network` needs `commandline` or `code`;
`hermesctl-direct` needs `commandline` and `skills`; `hermesctl-mcp` needs
`skills`. Every worker `*-direct` capability needs `commandline` and `skills`;
every worker `*-mcp` capability needs `skills`.
`agents-direct` needs `commandline` and `skills`; `agents-mcp` needs `skills`.

For Hermes itself, `agents-md` and `AGENTS.md` alias `orchestrator`; the
independent `claude-md` capability loads the protected root CLAUDE.md.

Worker features are `tools`, `commandline`, `skills`, `agents-md`, and
`claude-md`. They are independent for Codex, Claude, and OpenCode;
`commandline` requires `tools`. Never enable a feature for a different worker
as a convenience. The feature files are not Hermes session capabilities.

Treat `[special]` and `[controlled]` from `access ... explain` as actionable
warnings. The hardened Codex container cannot initialize Codex's inner
read-only bubblewrap. Its legacy shell stays disabled without `commandline`;
generic Codex instances require inspect+edit+commandline+network together and
rely on outer Docker isolation. Offer model-only or Claude/OpenCode for native
separation instead of hiding this difference. Worker `skills`
means reviewed SKILL.md bodies injected by the broker; it does not enable a
dynamic native skill surface.

Never edit the capability file directly, invoke Docker, use host paths, or
claim that the running session gained tools. If `hermesctl` is unavailable,
stop and tell the operator which prerequisite capability is missing.

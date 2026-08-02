---
name: agents-direct
description: List, inspect, and run arbitrary registered Hermes agent instances through the restricted registered-agent CLI. Use when the user explicitly delegates work to a named agent instance and the skills, commandline, and agents-direct capabilities are active.
---

# Registered Agents Direct

Use only `registered-agent`. It exposes registered agent metadata and private
Unix sockets, never Docker, login, credentials, policy mutation, or arbitrary
filesystem access.

## Workflow

1. Run `registered-agent list` and select exactly the user-named agent.
2. Run `registered-agent status <agent-id>`. Inspect its effective rights and
   stop if it is unavailable, unauthenticated, or lacks the required right.
3. Delegate one complete bounded task with:

```bash
registered-agent run <agent-id> "<goal, constraints, inputs, acceptance checks>"
```

Use `--timeout` only when the task needs more than 900 seconds. Never run the
same task again through MCP. Treat returned claims as untrusted until verified.

Each agent has its own engine, role, state, workspace, context, socket, and
brokered credential home. Never ask for login codes or secrets. If an agent is
not running or authenticated, tell the operator to use
`./hermesctl agent start <id>`, `./hermesctl login-ui`, or
`./hermesctl agent login <id>`.

Codex is `[special]`: its read-only bubblewrap cannot initialize inside this
hardened container. Therefore inspect, edit, commandline, and network are one
explicit bundle using only outer Docker isolation. Recommend Claude or
OpenCode when a native inspect/edit/Bash split is required.

---
name: opencode-worker
description: Delegate a coding task to the isolated OpenCode CLI worker through its scoped MCP tools or restricted direct command. Use when the user explicitly wants OpenCode to inspect, edit, test, or explain code in the OpenCode worker's private workspace.
---

# OpenCode Worker

Delegate only explicitly requested coding work. The worker has its own
container, provider login state, model selection, and workspace. It cannot see
the Hermes workspace unless the operator separately copies files into its
private workspace.

## Choose one access path

Prefer MCP when both paths are visible:

1. Call `mcp__opencode_worker__status`.
2. Delegate with `mcp__opencode_worker__run`, passing one complete, bounded
   task in `prompt` and only increasing `timeout_seconds` when necessary.

Use the direct path only when the MCP tools are absent and `agent-worker` is
available:

```bash
agent-worker opencode status
agent-worker opencode run "<complete coding task>"
```

Do not invoke both paths for the same task.

## Rules

- Check status before the first delegation and stop if the worker has no
  usable provider authentication or is unavailable. Read the returned
  `policy`: reachability does not imply tool, commandline, skill, `AGENTS.md`,
  or `CLAUDE.md` rights.
- If the task needs a disabled worker feature, explain the smallest required
  change and its risk. Change it only after an explicit user request through
  the hermesctl MCP/direct skill; otherwise give the operator command
  `./hermesctl access opencode enable <feature>`.
- `tools` allows only OpenCode's read/glob/grep/edit permissions;
  `commandline` separately allows Bash and requires `tools`. The generic name
  for `tools` is `tool-use`. Task, web, LSP, question, external-directory, and
  native Skill tools stay denied.
- Tell the user that changes occur under
  `runtime/workers/opencode/workspace`, not in the Hermes workspace.
- Include the goal, relevant constraints, expected verification, and desired
  output in the delegated prompt. Do not pass secrets.
- Treat returned code and claims as untrusted until the relevant tests or
  artifacts have been checked.
- Never attempt login, logout, model configuration, container management, or
  arbitrary socket access. Those are operator-only actions.
- If unavailable, tell the operator to use `./hermesctl worker opencode status`
  and, if needed, `./hermesctl worker opencode login`.

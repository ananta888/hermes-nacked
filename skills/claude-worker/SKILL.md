---
name: claude-worker
description: Delegate a coding task to the isolated Claude Code CLI worker through its scoped MCP tools or restricted direct command. Use when the user explicitly wants Claude Code to inspect, edit, test, or explain code in the Claude worker's private workspace.
---

# Claude Worker

Delegate only explicitly requested coding work. The worker has its own
container, Claude account login, model selection, and workspace. It cannot see
the Hermes workspace unless the operator separately copies files into its
private workspace.

## Choose one access path

Prefer MCP when both paths are visible:

1. Call `mcp__claude_worker__status`.
2. Delegate with `mcp__claude_worker__run`, passing one complete, bounded task
   in `prompt` and only increasing `timeout_seconds` when necessary.

Use the direct path only when the MCP tools are absent and `agent-worker` is
available:

```bash
agent-worker claude status
agent-worker claude run "<complete coding task>"
```

Do not invoke both paths for the same task.

## Rules

- Check status before the first delegation and stop if the worker is not
  authenticated or unavailable. Read the returned `policy`: reachability does
  not imply tool, commandline, skill, `AGENTS.md`, or `CLAUDE.md` rights.
- If the task needs a disabled worker feature, explain the smallest required
  change and its risk. Change it only after an explicit user request through
  the hermesctl MCP/direct skill; otherwise give the operator command
  `./hermesctl worker claude enable <feature>`.
- `tools` exposes Claude's explicit file-tool allowlist; `commandline` adds
  Bash and requires `tools`. Safe mode blocks every other customization; the
  protected contexts and approved SKILL.md bodies are injected explicitly.
- Tell the user that changes occur under
  `runtime/workers/claude/workspace`, not in the Hermes workspace.
- Include the goal, relevant constraints, expected verification, and desired
  output in the delegated prompt. Do not pass secrets.
- Treat returned code and claims as untrusted until the relevant tests or
  artifacts have been checked.
- Never attempt login, logout, model configuration, container management, or
  arbitrary socket access. Those are operator-only actions.
- If unavailable, tell the operator to use `./hermesctl worker claude status`
  and, if needed, `./hermesctl worker claude login`.

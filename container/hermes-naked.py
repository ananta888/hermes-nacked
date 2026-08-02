#!/opt/hermes/.venv/bin/python
"""Policy-enforcing entry point for a deny-by-default Hermes chat."""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import sys

sys.path.insert(0, "/usr/local/lib")

from hermes_naked_policy import PolicyError, parse_capabilities


BLOCKED_FLAGS = {
    "-s",
    "--skills",
    "-t",
    "--toolsets",
    "--ignore-user-config",
    "--yolo",
}

HERMESCTL_MCP_TOOLS = {
    "mcp__hermesctl__status",
    "mcp__hermesctl__list_capabilities",
    "mcp__hermesctl__enable",
    "mcp__hermesctl__disable",
    "mcp__hermesctl__reset",
    "mcp__hermesctl__worker_rights",
    "mcp__hermesctl__worker_enable",
    "mcp__hermesctl__worker_disable",
    "mcp__hermesctl__worker_reset",
}
WORKER_MCP_TOOLS = {
    worker: {
        f"mcp__{worker}_worker__status",
        f"mcp__{worker}_worker__run",
    }
    for worker in ("codex", "claude", "opencode")
}


def _fail(message: str, code: int = 64) -> "NoReturn":
    print(f"hermes-naked: {message}", file=sys.stderr)
    raise SystemExit(code)


def _reject_policy_bypasses(args: list[str]) -> None:
    for arg in args:
        flag = arg.split("=", 1)[0]
        attached_short_override = (
            (arg.startswith("-s") or arg.startswith("-t"))
            and not arg.startswith("--")
            and len(arg) > 2
        )
        if flag in BLOCKED_FLAGS or attached_short_override:
            _fail(f"{flag} is controlled by hermesctl and cannot be overridden")


def _install_context_policy(policy) -> None:
    """Inject only the exact protected context files selected by policy."""
    if not policy.load_context_files:
        return

    from agent import prompt_builder

    selected: list[tuple[str, Path]] = []
    context_root = Path("/policy-context")
    if policy.load_orchestrator:
        selected.append(("AGENTS.md", context_root / "AGENTS.md"))
    if policy.load_claude_context:
        selected.append(("CLAUDE.md", context_root / "CLAUDE.md"))

    def guarded_context_prompt(
        cwd=None,
        skip_soul=False,
        context_length=None,
        allow_install_tree_fallback=False,
    ):
        del cwd, skip_soul, context_length, allow_install_tree_fallback
        sections: list[str] = []
        for label, source in selected:
            try:
                metadata = source.lstat()
                if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                    _fail(f"protected {label} context is not a regular file")
                raw = source.read_bytes()
            except OSError as exc:
                _fail(f"protected {label} context is unavailable: {exc}")
            if len(raw) > 64 * 1024:
                _fail(f"protected {label} context exceeds 64 KiB")
            try:
                content = raw.decode("utf-8", errors="strict").strip()
            except UnicodeDecodeError:
                _fail(f"protected {label} context is not valid UTF-8")
            sections.append(f"## {label}\n\n{content}")
        return (
            "# Project Context\n\n"
            "The following operator-protected context files have been "
            "explicitly enabled and should be followed:\n\n"
            + "\n\n".join(sections)
        )

    prompt_builder.build_context_files_prompt = guarded_context_prompt


def _install_remote_mount_policy(policy) -> None:
    """Prevent implicit state/credential/cache mounts into command sandboxes."""
    from tools import credential_files

    credential_files.get_credential_file_mounts = lambda: []
    credential_files.get_cache_directory_mounts = lambda: []
    if policy.mount_skills:
        original_skills_mount = credential_files.get_skills_directory_mount

        def sandbox_user_skills_mount(*args, **kwargs):
            return original_skills_mount(
                container_base="/tmp/hermes-home/.hermes"
            )

        credential_files.get_skills_directory_mount = sandbox_user_skills_mount
    else:
        credential_files.get_skills_directory_mount = lambda *args, **kwargs: []


def _apply_terminal_policy_env(policy) -> None:
    """Set the final nested-sandbox mounts after Hermes config bridging."""
    sandbox_env = {"HOME": "/tmp/hermes-home"}
    volumes: list[str] = []
    if policy.direct_control:
        host_root = os.environ.get("HERMES_HOST_ROOT", "").strip()
        if not host_root:
            _fail("HERMES_HOST_ROOT is required for hermesctl-direct")
        volumes = [
            f"{host_root}/.hermes-capabilities:/control/.hermes-capabilities:rw",
            f"{host_root}/hermesctl:/usr/local/bin/hermesctl:ro",
            (
                f"{host_root}/container/policy.py:"
                "/usr/local/lib/hermesctl_policy.py:ro"
            ),
            (
                f"{host_root}/container/worker_policy.py:"
                "/usr/local/lib/hermes_worker_policy.py:ro"
            ),
            (
                f"{host_root}/runtime/control/workers:"
                "/control/workers:rw"
            ),
        ]
        sandbox_env.update(
            {
                "HERMESCTL_ROOT": "/control",
                "HERMESCTL_CAPABILITIES_FILE": "/control/.hermes-capabilities",
                "HERMESCTL_POLICY_DIR": "/usr/local/lib",
                "HERMESCTL_WORKER_CONTROL_DIR": "/control/workers",
                "HERMESCTL_CONTROL_ONLY": "1",
            }
        )

    if policy.direct_workers:
        host_root = os.environ.get("HERMES_HOST_ROOT", "").strip()
        if not host_root:
            _fail("HERMES_HOST_ROOT is required for direct worker access")
        volumes.extend(
            [
                (
                    f"{host_root}/container/workerctl.py:"
                    "/usr/local/bin/agent-worker:ro"
                ),
                (
                    f"{host_root}/container/worker_rpc.py:"
                    "/usr/local/lib/worker_rpc.py:ro"
                ),
            ]
        )
        for worker in policy.direct_workers:
            volumes.append(
                f"{host_root}/runtime/workers/{worker}/socket:"
                f"/worker-sockets/{worker}:ro"
            )

    os.environ["TERMINAL_DOCKER_VOLUMES"] = json.dumps(volumes)
    os.environ["TERMINAL_DOCKER_ENV"] = json.dumps(sandbox_env)
    os.environ["TERMINAL_DOCKER_NETWORK"] = (
        "true" if policy.sandbox_network else "false"
    )


def _install_extension_policy(policy) -> None:
    """Allow only policy-owned local MCP servers and keep extensions blocked."""
    if not policy.enable_mcp:
        return

    # MCP requires safe mode off. Preserve the other safe-mode effects with
    # explicit chokepoints so no configured plugin or shell hook comes alive.
    from hermes_cli import plugins

    plugins.discover_plugins = lambda *args, **kwargs: []

    try:
        from agent import shell_hooks

        shell_hooks.register_from_config = lambda *args, **kwargs: None
    except Exception:
        pass

    from tools import mcp_tool

    exact_servers = {}
    if "hermesctl-mcp" in policy.capabilities:
        exact_servers["hermesctl"] = {
            "command": "/usr/local/bin/hermesctl-mcp",
            "args": [],
            "timeout": 15,
            "connect_timeout": 15,
            "tools": {
                "include": [
                    "status",
                    "list_capabilities",
                    "enable",
                    "disable",
                    "reset",
                    "worker_rights",
                    "worker_enable",
                    "worker_disable",
                    "worker_reset",
                ],
                "resources": False,
                "prompts": False,
            },
        }
    for worker in policy.mcp_workers:
        exact_servers[f"{worker}_worker"] = {
            "command": "/usr/local/bin/worker-mcp",
            "args": [worker],
            "timeout": 1815,
            "connect_timeout": 15,
            "tools": {
                "include": ["status", "run"],
                "resources": False,
                "prompts": False,
            },
        }
    mcp_tool._load_mcp_config = lambda: exact_servers

    # Hermes normally checks config.yaml before spawning its discovery thread.
    # This policy-owned server is injected above instead of trusting mutable
    # user config, so make that cheap startup gate match the exact policy.
    from hermes_cli import mcp_startup

    mcp_startup._has_configured_mcp_servers = lambda: True


def _install_tool_policy(toolsets: tuple[str, ...]) -> tuple[str, ...]:
    """Pin every tool-schema rebuild to the operator-approved toolsets."""
    from toolsets import create_custom_toolset, resolve_toolset

    create_custom_toolset(
        "naked-none",
        "Deny-by-default sentinel containing no model-facing tools",
        tools=[],
        includes=[],
    )
    effective = toolsets or ("naked-none",)
    def resolve_allowed_names() -> set[str]:
        names: set[str] = set()
        for toolset in effective:
            names.update(resolve_toolset(toolset))
        return names

    allowed_names = resolve_allowed_names()
    if "hermesctl" in effective:
        # Report the exact policy surface even before the stdio server has
        # connected; runtime visibility still requires successful discovery.
        allowed_names.update(HERMESCTL_MCP_TOOLS)
    for worker, tools in WORKER_MCP_TOOLS.items():
        if f"{worker}_worker" in effective:
            allowed_names.update(tools)

    import model_tools

    original = model_tools.get_tool_definitions

    def guarded_get_tool_definitions(
        enabled_toolsets=None,
        disabled_toolsets=None,
        quiet_mode=False,
        skip_tool_search_assembly=False,
    ):
        definitions = original(
            enabled_toolsets=list(effective),
            disabled_toolsets=None,
            quiet_mode=quiet_mode,
            # Keep the explicit policy schemas. Hermes' lazy Tool Search
            # assembly may otherwise replace MCP tools with a generic bridge;
            # filtering that bridge would hide the allowed tools, while
            # allowing it would create a second dynamic dispatch surface.
            skip_tool_search_assembly=True,
        )
        # Re-resolve after dynamic MCP registration, then filter as defense in
        # depth for future Hermes changes or unrelated registry overlays.
        current_allowed_names = resolve_allowed_names()
        return [
            item
            for item in definitions
            if item.get("function", {}).get("name") in current_allowed_names
        ]

    model_tools.get_tool_definitions = guarded_get_tool_definitions
    return tuple(sorted(allowed_names))


def _inject_provider_args(args: list[str]) -> list[str]:
    result = list(args)
    provider = os.environ.get("HERMES_NAKED_PROVIDER", "").strip()
    model = os.environ.get("HERMES_NAKED_MODEL", "").strip()
    has_provider = any(a == "--provider" or a.startswith("--provider=") for a in result)
    has_model = any(a in {"-m", "--model"} or a.startswith("--model=") for a in result)
    if provider and not has_provider:
        result[0:0] = ["--provider", provider]
    if model and not has_model:
        result[0:0] = ["--model", model]
    return result


def _probe_sandbox(policy) -> None:
    if not policy.needs_sandbox:
        _fail("sandbox probe requires files, commandline, or code")

    from tools.environments.docker import DockerEnvironment

    configured_volumes = json.loads(os.environ.get("TERMINAL_DOCKER_VOLUMES", "[]"))
    configured_env = json.loads(os.environ.get("TERMINAL_DOCKER_ENV", "{}"))
    environment = DockerEnvironment(
        image=os.environ.get("TERMINAL_DOCKER_IMAGE", "python:3.13-slim-bookworm"),
        cwd="/workspace",
        timeout=30,
        cpu=1,
        memory=256,
        persistent_filesystem=False,
        task_id="hermes-naked-policy-probe",
        volumes=configured_volumes,
        forward_env=[],
        env=configured_env,
        network=policy.sandbox_network,
        host_cwd=os.environ.get("TERMINAL_CWD", ""),
        auto_mount_cwd=True,
        run_as_host_user=True,
        persist_across_processes=False,
    )
    try:
        control_check = (
            "test -x /usr/local/bin/hermesctl; "
            "hermesctl status >/tmp/hermesctl-status; "
            "grep -q '^Capabilities:' /tmp/hermesctl-status; "
            "hermesctl worker codex rights >/tmp/codex-rights; "
            "grep -q '^Worker rights:' /tmp/codex-rights; "
            "printf 'hermesctl=mounted\\n'; "
            if policy.direct_control
            else "test ! -e /control/.hermes-capabilities; printf 'hermesctl=absent\\n'; "
        )
        worker_checks = ""
        if policy.direct_workers:
            worker_checks += "test -x /usr/local/bin/agent-worker; "
            for worker in policy.direct_workers:
                worker_checks += (
                    f"test -S /worker-sockets/{worker}/worker.sock; "
                    f"if touch /worker-sockets/{worker}/.write-test 2>/dev/null; "
                    f"then exit 1; fi; "
                    f"agent-worker {worker} status >/tmp/{worker}-status; "
                )
            worker_checks += (
                "printf 'workers=" + ",".join(policy.direct_workers) + "\\n'; "
            )
        else:
            worker_checks = (
                "test ! -e /usr/local/bin/agent-worker; printf 'workers=absent\\n'; "
            )

        result = environment.execute(
            "set -eu; test -d /workspace; "
            "test -f /workspace/.gitkeep; "
            "printf 'uid=%s\\n' \"$(id -u)\"; "
            "if test -d \"$HOME/.hermes/skills\"; then printf 'skills=mounted\\n'; "
            "else printf 'skills=absent\\n'; fi; "
            + control_check
            + worker_checks
        )
        network_mode = environment._container_network_mode(environment._container_id or "")
        expected_network = "bridge" if policy.sandbox_network else "none"
        output = result.get("output", "")
        if result.get("returncode") != 0:
            _fail(f"sandbox command failed: {output.strip()}", 1)
        expected_skill_line = "skills=mounted" if policy.mount_skills else "skills=absent"
        if expected_skill_line not in output:
            _fail(f"sandbox skill-mount mismatch: {output.strip()}", 1)
        expected_control_line = (
            "hermesctl=mounted" if policy.direct_control else "hermesctl=absent"
        )
        if expected_control_line not in output:
            _fail(f"sandbox hermesctl-mount mismatch: {output.strip()}", 1)
        if network_mode != expected_network:
            _fail(
                f"sandbox network mismatch: expected {expected_network}, got {network_mode}",
                1,
            )
        print(
            json.dumps(
                {
                    "ok": True,
                    "network_mode": network_mode,
                    "skills_mounted": policy.mount_skills,
                    "hermesctl_mounted": policy.direct_control,
                    "direct_workers": policy.direct_workers,
                    "worker_socket_mounts_read_only": bool(policy.direct_workers),
                    "workspace_mounted": True,
                    "run_as_uid": os.getuid(),
                    "command_output": output.strip().splitlines(),
                },
                indent=2,
            )
        )
    finally:
        environment.cleanup(force_remove=True)


def _probe_mcp(policy) -> None:
    if not policy.enable_mcp:
        _fail("MCP probe requires an MCP capability")

    from tools.mcp_tool import discover_mcp_tools, shutdown_mcp_servers
    import model_tools

    expected: set[str] = set()
    if "hermesctl-mcp" in policy.capabilities:
        expected.update(HERMESCTL_MCP_TOOLS)
    for worker in policy.mcp_workers:
        expected.update(WORKER_MCP_TOOLS[worker])
    try:
        discovered = set(discover_mcp_tools())
        schemas = model_tools.get_tool_definitions(
            enabled_toolsets=list(policy.toolsets), quiet_mode=True
        )
        visible = {
            item.get("function", {}).get("name", "")
            for item in schemas
            if item.get("function", {}).get("name", "") in expected
        }
        if discovered != expected or visible != expected:
            from toolsets import resolve_toolset, validate_toolset
            from tools.registry import registry
            from tools.mcp_tool import get_mcp_status

            aliases = {
                name: validate_toolset(name) for name in policy.toolsets
            }
            resolved = {
                name: resolve_toolset(name) for name in policy.toolsets
            }
            _fail(
                "MCP tool mismatch: "
                f"discovered={sorted(discovered)}, visible={sorted(visible)}, "
                f"toolsets={policy.toolsets}, "
                f"aliases={aliases}, resolved={resolved}, "
                f"registered={registry.get_registered_toolset_names()}, "
                f"status={get_mcp_status()}",
                1,
            )
        checked_status: list[str] = []
        status_tools: list[tuple[str, dict[str, str]]] = []
        if "hermesctl-mcp" in policy.capabilities:
            status_tools.append(("mcp__hermesctl__status", {}))
            status_tools.extend(
                ("mcp__hermesctl__worker_rights", {"worker": worker})
                for worker in ("codex", "claude", "opencode")
            )
        status_tools.extend(
            (f"mcp__{worker}_worker__status", {})
            for worker in policy.mcp_workers
        )
        for tool_name, arguments in status_tools:
            status_result = model_tools.handle_function_call(
                tool_name,
                arguments,
                task_id="hermes-naked-mcp-probe",
                user_task="policy verification",
                enabled_tools=sorted(visible),
                enabled_toolsets=list(policy.toolsets),
            )
            if not status_result or "error calling" in status_result.lower():
                _fail(f"MCP status call failed for {tool_name}: {status_result}", 1)
            checked_status.append(tool_name)
        print(
            json.dumps(
                {
                    "ok": True,
                    "tools": sorted(visible),
                    "status_calls": checked_status,
                },
                indent=2,
            )
        )
    finally:
        shutdown_mcp_servers()


def _probe_context(policy) -> None:
    if not policy.load_context_files:
        _fail("context probe requires orchestrator or claude-md")

    from agent.prompt_builder import build_context_files_prompt

    workspace = os.environ.get("TERMINAL_CWD", "")
    prompt = build_context_files_prompt(cwd=workspace, skip_soul=True)
    agents_loaded = "Hermes-Orchestrator" in prompt
    claude_loaded = "Hermes CLAUDE.md Context" in prompt
    if agents_loaded != policy.load_orchestrator:
        _fail("AGENTS.md context selection mismatch", 1)
    if claude_loaded != policy.load_claude_context:
        _fail("CLAUDE.md context selection mismatch", 1)
    print(
        json.dumps(
            {
                "ok": True,
                "workspace": workspace,
                "agents_md_loaded": agents_loaded,
                "claude_md_loaded": claude_loaded,
            },
            indent=2,
        )
    )


def main() -> None:
    # Set these before importing Hermes discovery/runtime modules.
    os.environ["HERMES_DISABLE_LAZY_INSTALLS"] = "1"

    try:
        policy = parse_capabilities(os.environ.get("HERMES_CAPABILITIES"))
    except PolicyError as exc:
        _fail(str(exc))

    os.environ["HERMES_SAFE_MODE"] = "0" if policy.enable_mcp else "1"
    os.environ["HERMES_IGNORE_RULES"] = "0" if policy.load_context_files else "1"
    _install_context_policy(policy)
    _install_extension_policy(policy)
    _install_remote_mount_policy(policy)
    _apply_terminal_policy_env(policy)
    allowed_tools = _install_tool_policy(policy.toolsets)

    args = sys.argv[1:]
    if args == ["--policy-report"]:
        print(
            json.dumps(
                {
                    "capabilities": policy.capabilities,
                    "toolsets": policy.toolsets,
                    "allowed_tools": allowed_tools,
                    "context_files": tuple(
                        name
                        for name, enabled in (
                            ("AGENTS.md", policy.load_orchestrator),
                            ("CLAUDE.md", policy.load_claude_context),
                        )
                        if enabled
                    ),
                    "orchestrator": policy.load_orchestrator,
                    "claude_context": policy.load_claude_context,
                    "persistent_memory": False,
                    "plugins": False,
                    "mcp": policy.enable_mcp,
                    "preloaded_skills": False,
                    "skill_mount": policy.mount_skills,
                    "control_mount": policy.mount_control,
                    "direct_control": policy.direct_control,
                    "direct_workers": policy.direct_workers,
                    "mcp_workers": policy.mcp_workers,
                    "workers": policy.workers,
                    "credential_mounts": False,
                    "cache_mounts": False,
                    "sandbox_required": policy.needs_sandbox,
                    "sandbox_network": policy.sandbox_network,
                    "workspace": os.environ.get("TERMINAL_CWD", ""),
                },
                indent=2,
            )
        )
        return
    if args == ["--sandbox-probe"]:
        _probe_sandbox(policy)
        return
    if args == ["--mcp-probe"]:
        _probe_mcp(policy)
        return
    if args in (["--context-probe"], ["--orchestrator-probe"]):
        _probe_context(policy)
        return

    _reject_policy_bypasses(args)
    if args and args[0] == "chat":
        args = args[1:]
    args = _inject_provider_args(args)

    # Importing the classic CLI bridges config.yaml terminal values into the
    # environment. Re-apply the operator policy afterwards so mutable user
    # config can never add mounts, forwarded variables, or network access.
    import cli as _classic_cli  # noqa: F401

    _apply_terminal_policy_env(policy)

    # The empty custom toolset is valid in Hermes and remains empty even if the
    # upstream default platform toolset grows in a future release.
    sys.argv = ["hermes", "chat", "--toolsets", ",".join(policy.toolsets or ("naked-none",)), *args]

    from hermes_cli.main import main as hermes_main

    hermes_main()


if __name__ == "__main__":
    main()

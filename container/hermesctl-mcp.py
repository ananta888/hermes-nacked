#!/opt/hermes/.venv/bin/python
"""Narrow stdio MCP adapter for hermesctl capability management."""

from __future__ import annotations

import os
import subprocess
from typing import Any

from mcp.server.fastmcp import FastMCP


HERMESCTL = "/usr/local/bin/hermesctl"
CONTROL_ENV = {
    "HERMESCTL_ROOT": "/control",
    "HERMESCTL_CAPABILITIES_FILE": "/control/.hermes-capabilities",
    "HERMESCTL_POLICY_DIR": "/usr/local/lib",
    "HERMESCTL_CONTROL_ONLY": "1",
}

mcp = FastMCP(
    "hermesctl",
    instructions=(
        "Manage only the capability policy of this Hermes Naked instance. "
        "Policy changes affect newly started sessions, never the current tool snapshot."
    ),
)


def _run(*args: str) -> dict[str, Any]:
    env = os.environ.copy()
    env.update(CONTROL_ENV)
    try:
        result = subprocess.run(
            [HERMESCTL, *args],
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "exit_code": 1, "error": str(exc)}
    payload: dict[str, Any] = {
        "ok": result.returncode == 0,
        "exit_code": result.returncode,
    }
    if result.stdout.strip():
        payload["output"] = result.stdout.strip()
    if result.stderr.strip():
        payload["error"] = result.stderr.strip()
    if result.returncode != 0:
        policy_dir = CONTROL_ENV["HERMESCTL_POLICY_DIR"]
        payload["diagnostic"] = {
            "policy_dir_exists": os.path.isdir(policy_dir),
            "policy_file_exists": os.path.isfile(
                os.path.join(policy_dir, "hermesctl_policy.py")
            ),
            "control_file_exists": os.path.isfile(
                CONTROL_ENV["HERMESCTL_CAPABILITIES_FILE"]
            ),
        }
    return payload


def _normalize_capabilities(capabilities: list[str]) -> list[str]:
    if not isinstance(capabilities, list) or not capabilities:
        raise ValueError("capabilities must be a non-empty list")
    result: list[str] = []
    for capability in capabilities:
        if not isinstance(capability, str) or not capability.strip():
            raise ValueError("each capability must be a non-empty string")
        result.append(capability.strip())
    return result


@mcp.tool()
def status() -> dict[str, Any]:
    """Show this installation's effective capabilities and isolation state."""
    return _run("status")


@mcp.tool()
def list_capabilities() -> dict[str, Any]:
    """List valid capability switches, aliases, and dependency rules."""
    return _run("capabilities")


@mcp.tool()
def enable(capabilities: list[str]) -> dict[str, Any]:
    """Enable explicitly requested capabilities for newly started sessions."""
    return _run("enable", *_normalize_capabilities(capabilities))


@mcp.tool()
def disable(capabilities: list[str]) -> dict[str, Any]:
    """Disable explicitly requested capabilities for newly started sessions."""
    return _run("disable", *_normalize_capabilities(capabilities))


@mcp.tool()
def reset() -> dict[str, Any]:
    """Remove every model-facing capability for newly started sessions."""
    return _run("reset")


if __name__ == "__main__":
    mcp.run(transport="stdio")

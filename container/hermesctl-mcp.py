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
    "HERMESCTL_WORKER_CONTROL_DIR": "/control/workers",
    "HERMESCTL_CONTROL_ONLY": "1",
}

mcp = FastMCP(
    "hermesctl",
    instructions=(
        "Manage only the Hermes capability policy and the separate feature "
        "policies of its three isolated coding workers. Hermes policy changes "
        "affect new sessions; worker policy changes affect the next worker task."
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


def _normalize_worker(worker: str) -> str:
    if not isinstance(worker, str):
        raise ValueError("worker must be a string")
    normalized = worker.strip().lower()
    if normalized not in {"codex", "claude", "opencode"}:
        raise ValueError("worker must be codex, claude, or opencode")
    return normalized


def _normalize_worker_features(features: list[str]) -> list[str]:
    if not isinstance(features, list) or not features:
        raise ValueError("features must be a non-empty list")
    result: list[str] = []
    for feature in features:
        if not isinstance(feature, str) or not feature.strip():
            raise ValueError("each worker feature must be a non-empty string")
        result.append(feature.strip())
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


@mcp.tool()
def worker_rights(worker: str) -> dict[str, Any]:
    """Show one worker's separately enforced feature policy."""
    return _run("worker", _normalize_worker(worker), "rights")


@mcp.tool()
def worker_enable(worker: str, features: list[str]) -> dict[str, Any]:
    """Enable explicitly requested features for one worker's next task."""
    return _run(
        "worker",
        _normalize_worker(worker),
        "enable",
        *_normalize_worker_features(features),
    )


@mcp.tool()
def worker_disable(worker: str, features: list[str]) -> dict[str, Any]:
    """Disable explicitly requested features for one worker's next task."""
    return _run(
        "worker",
        _normalize_worker(worker),
        "disable",
        *_normalize_worker_features(features),
    )


@mcp.tool()
def worker_reset(worker: str) -> dict[str, Any]:
    """Return exactly one coding worker to model-only operation."""
    return _run("worker", _normalize_worker(worker), "reset")


if __name__ == "__main__":
    mcp.run(transport="stdio")

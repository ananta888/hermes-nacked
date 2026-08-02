#!/opt/hermes/.venv/bin/python
"""Scoped stdio MCP adapter for one isolated coding worker."""

from __future__ import annotations

import sys
from typing import Any

from mcp.server.fastmcp import FastMCP

sys.path.insert(0, "/usr/local/lib")

from worker_rpc import WORKERS, call_worker


if len(sys.argv) != 2 or sys.argv[1] not in WORKERS:
    raise SystemExit("usage: worker-mcp {codex|claude|opencode}")

WORKER = sys.argv[1]
mcp = FastMCP(
    f"{WORKER}_worker",
    instructions=(
        f"Delegate tasks only to the isolated {WORKER} coding worker. "
        "The worker has its own state and workspace; authentication is operator-only."
    ),
)


@mcp.tool()
def status() -> dict[str, Any]:
    """Show this worker's CLI version, configured model, and login status."""
    try:
        return call_worker(WORKER, "status", timeout_seconds=60)
    except Exception as exc:
        return {"ok": False, "worker": WORKER, "error": str(exc)}


@mcp.tool()
def run(prompt: str, timeout_seconds: int = 900) -> dict[str, Any]:
    """Run one task in this worker's private workspace."""
    try:
        return call_worker(
            WORKER,
            "run",
            prompt=prompt,
            timeout_seconds=timeout_seconds,
        )
    except Exception as exc:
        return {"ok": False, "worker": WORKER, "error": str(exc)}


if __name__ == "__main__":
    mcp.run(transport="stdio")

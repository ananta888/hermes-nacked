#!/opt/hermes/.venv/bin/python
"""Scoped MCP for registered agents, persistent jobs, teams, and artifacts."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, List

from mcp.server.fastmcp import FastMCP

sys.path.insert(0, "/usr/local/lib")

from control_plane.agents import AgentRegistry
from control_plane.artifacts import ArtifactRepository
from control_plane.credentials import CredentialBroker
from control_plane.domain import ControlPlaneError
from control_plane.jobs import JobRepository, JobService
from control_plane.storage import AtomicStore
from worker_rpc import call_agent


ROOT = Path(os.environ.get("HERMES_AGENT_RUNTIME_ROOT", "/agent-runtime"))
SOCKET_ROOT = Path(os.environ.get("HERMES_AGENT_SOCKET_ROOT", "/agent-sockets"))
os.environ["HERMES_AGENT_SOCKET_ROOT"] = str(SOCKET_ROOT)
STORE = AtomicStore(ROOT)
CREDENTIALS = CredentialBroker(ROOT, STORE)
AGENTS = AgentRegistry(ROOT, STORE, CREDENTIALS)
ARTIFACTS = ArtifactRepository(ROOT, STORE)
JOBS = JobRepository(ROOT, STORE)
EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="hermes-agent-job")


def _socket_required(agent_id: str) -> None:
    AGENTS.get(agent_id)
    if AGENTS.is_operator_only(agent_id):
        raise ControlPlaneError("agent is reserved for an operator evaluation trial")
    if not (SOCKET_ROOT / agent_id / "worker.sock").is_socket():
        raise ControlPlaneError(
            f"agent {agent_id} is not running; the operator must start it first"
        )


JOB_SERVICE = JobService(AGENTS, JOBS, ARTIFACTS, _socket_required, call_agent)
mcp = FastMCP(
    "agents",
    instructions=(
        "Use only explicitly registered, rights-scoped agent instances. Credentials and "
        "login operations are operator-only and are never exposed by this server."
    ),
)


def _public_agent(agent_id: str) -> dict[str, Any]:
    record = AGENTS.get(agent_id)
    if AGENTS.is_operator_only(record.agent_id):
        raise ControlPlaneError("agent is reserved for an operator evaluation trial")
    return {
        "agent_id": record.agent_id,
        "engine": record.engine,
        "role": record.role,
        "rights": AGENTS.rights(record.agent_id),
        "socket_ready": (SOCKET_ROOT / record.agent_id / "worker.sock").is_socket(),
        "credentials_exposed": False,
    }


@mcp.tool()
def list() -> dict[str, Any]:
    """List registered agent ids, roles, engines, effective rights, and readiness."""
    try:
        return {
            "ok": True,
            "agents": [
                _public_agent(item.agent_id)
                for item in AGENTS.list(include_operator_only=False)
            ],
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
def status(agent_id: str) -> dict[str, Any]:
    """Show one agent's sanitized metadata and live CLI status."""
    try:
        public = _public_agent(agent_id)
        if not public["socket_ready"]:
            return {"ok": False, **public, "error": "operator must start this agent"}
        return {**public, "runtime": call_agent(agent_id, "status", timeout_seconds=60)}
    except Exception as exc:
        return {"ok": False, "agent_id": agent_id, "error": str(exc)}


@mcp.tool()
def run(agent_id: str, prompt: str, timeout_seconds: int = 900) -> dict[str, Any]:
    """Run one synchronous task in exactly one already-started private agent workspace."""
    try:
        _socket_required(agent_id)
        return call_agent(
            agent_id, "run", prompt=prompt, timeout_seconds=timeout_seconds
        )
    except Exception as exc:
        return {"ok": False, "agent_id": agent_id, "error": str(exc)}


@mcp.tool()
def job_submit(
    agent_id: str,
    prompt: str,
    timeout_seconds: int = 900,
    dependencies: List[str] | None = None,
    input_artifacts: List[str] | None = None,
) -> dict[str, Any]:
    """Queue an asynchronous job for an already-started agent and return its id."""
    try:
        _socket_required(agent_id)
        job = JOBS.create(
            agent_id,
            prompt,
            timeout_seconds=timeout_seconds,
            dependencies=tuple(dependencies or ()),
            input_artifacts=tuple(input_artifacts or ()),
        )
        EXECUTOR.submit(JOB_SERVICE.run, job["job_id"])
        return {"ok": True, **job}
    except Exception as exc:
        return {"ok": False, "agent_id": agent_id, "error": str(exc)}


@mcp.tool()
def job_status(job_id: str) -> dict[str, Any]:
    """Return the persisted status and result artifact id of one job."""
    try:
        return {"ok": True, **JOBS.get(job_id)}
    except Exception as exc:
        return {"ok": False, "job_id": job_id, "error": str(exc)}


@mcp.tool()
def job_cancel(job_id: str) -> dict[str, Any]:
    """Cancel one queued or running job through its narrow worker RPC."""
    try:
        return {"ok": True, **JOB_SERVICE.cancel(job_id)}
    except Exception as exc:
        return {"ok": False, "job_id": job_id, "error": str(exc)}


@mcp.tool()
def artifact_get(artifact_id: str) -> dict[str, Any]:
    """Read one checksum-verified text, JSON, or patch artifact up to 1 MiB."""
    try:
        return {"ok": True, **ARTIFACTS.model_view(artifact_id)}
    except Exception as exc:
        return {"ok": False, "artifact_id": artifact_id, "error": str(exc)}


def _team_path(team_name: str) -> Path:
    normalized = str(team_name).strip().lower().replace("_", "-")
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,62}", normalized):
        raise ControlPlaneError("invalid team name")
    return ROOT / "runtime" / "teams" / normalized / "team.json"


@mcp.tool()
def team_list() -> dict[str, Any]:
    """List applied team names and their member ids without credential metadata."""
    try:
        teams = []
        team_root = ROOT / "runtime" / "teams"
        if team_root.is_dir():
            for path in sorted(team_root.glob("*/team.json")):
                value = json.loads(path.read_text(encoding="utf-8"))
                teams.append(
                    {
                        "name": value["name"],
                        "orchestrator": value["orchestrator"],
                        "agents": [item["id"] for item in value["agents"]],
                        "workflow_steps": len(value.get("workflow", [])),
                    }
                )
        return {"ok": True, "teams": teams}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
def team_status(team_name: str) -> dict[str, Any]:
    """Show one applied team's sanitized structure and current agent readiness."""
    try:
        value = json.loads(_team_path(team_name).read_text(encoding="utf-8"))
        return {
            "ok": True,
            "name": value["name"],
            "orchestrator": value["orchestrator"],
            "agents": [_public_agent(item["id"]) for item in value["agents"]],
            "workflow": [
                {"id": item["id"], "agent": item["agent"], "needs": item["needs"]}
                for item in value.get("workflow", [])
            ],
        }
    except Exception as exc:
        return {"ok": False, "team": team_name, "error": str(exc)}


if __name__ == "__main__":
    mcp.run(transport="stdio")

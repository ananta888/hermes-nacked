"""Declarative team convergence and DAG execution service."""

from __future__ import annotations

from pathlib import Path
import secrets
from typing import Any

from .agents import AgentRegistry
from .credentials import CredentialBroker
from .domain import ControlPlaneError, normalize_identifier, utc_now, validate_rights
from .jobs import JobRepository, JobService
from .manifests import topological_steps
from .runtime import DockerAgentRuntime
from .storage import AtomicStore


class TeamService:
    def __init__(
        self,
        root: Path,
        store: AtomicStore,
        agents: AgentRegistry,
        credentials: CredentialBroker,
        runtime: DockerAgentRuntime,
        jobs: JobRepository,
        job_service: JobService,
    ):
        self.root = root.resolve()
        self.store = store
        self.agents = agents
        self.credentials = credentials
        self.runtime = runtime
        self.jobs = jobs
        self.job_service = job_service
        self.team_root = self.root / "runtime" / "teams"
        self.run_root = self.root / "runtime" / "runs"

    def plan(self, manifest: dict[str, Any]) -> list[dict[str, Any]]:
        existing = {record.agent_id: record for record in self.agents.list()}
        credential_owners: dict[str, str] = {}
        plan: list[dict[str, Any]] = []
        for spec in manifest["agents"]:
            validate_rights(spec["engine"], spec["access"])
            previous_owner = credential_owners.get(spec["credential"])
            if previous_owner and previous_owner != spec["id"]:
                raise ControlPlaneError(
                    f"team agents {previous_owner} and {spec['id']} share credential "
                    f"{spec['credential']}; team manifests require isolated credentials"
                )
            credential_owners[spec["credential"]] = spec["id"]
            try:
                credential = self.credentials.get(spec["credential"])
            except ControlPlaneError:
                credential = None
            if credential is not None and credential.engine != spec["engine"]:
                raise ControlPlaneError(
                    f"credential {credential.credential_id} belongs to {credential.engine}, "
                    f"not {spec['engine']}"
                )
            outside_consumers = [
                agent_id
                for agent_id in self.agents.agents_using_credential(spec["credential"])
                if agent_id != spec["id"]
            ]
            if outside_consumers:
                raise ControlPlaneError(
                    f"credential {spec['credential']} is already assigned to "
                    f"{', '.join(outside_consumers)}"
                )
            if spec["id"] not in existing:
                plan.append(
                    {
                        "action": "create",
                        "agent": spec["id"],
                        "engine": spec["engine"],
                        "credential": spec["credential"],
                    }
                )
            else:
                record = existing[spec["id"]]
                if record.engine != spec["engine"]:
                    raise ControlPlaneError(
                        f"agent {record.agent_id} already uses {record.engine}; engine changes require a new id"
                    )
                plan.append({"action": "converge", "agent": spec["id"]})
            plan.append(
                {
                    "action": "set-rights",
                    "agent": spec["id"],
                    "rights": spec["access"],
                }
            )
        return plan

    def apply(self, manifest: dict[str, Any]) -> dict[str, Any]:
        plan = self.plan(manifest)
        for spec in manifest["agents"]:
            self.credentials.create(spec["credential"], spec["engine"])
            try:
                record = self.agents.get(spec["id"])
            except ControlPlaneError:
                record = self.agents.create(
                    spec["id"],
                    spec["engine"],
                    spec["credential"],
                    role=spec["role"],
                )
            if record.credential_id != spec["credential"]:
                self.agents.assign_credential(spec["id"], spec["credential"])
            self.agents.set_role(spec["id"], spec["role"])
            self.agents.set_rights(spec["id"], tuple(spec["access"]))
            self.agents.set_model(spec["id"], spec["model"])
        team_directory = self.store.ensure_directory(
            self.team_root / manifest["name"], 0o700
        )
        stored = {**manifest, "applied_at": utc_now()}
        self.store.write_json(team_directory / "team.json", stored)
        return {"ok": True, "team": manifest["name"], "plan": plan}

    def get(self, team_name: str) -> dict[str, Any]:
        normalized = normalize_identifier(team_name, label="team name")
        return self.store.read_json(self.team_root / normalized / "team.json")

    def list(self) -> list[dict[str, Any]]:
        if not self.team_root.exists():
            return []
        result = []
        for directory in sorted(self.team_root.iterdir()):
            path = directory / "team.json"
            if directory.is_dir() and not directory.is_symlink() and path.is_file():
                result.append(self.store.read_json(path))
        return result

    def status(self, team_name: str) -> dict[str, Any]:
        team = self.get(team_name)
        return {
            "name": team["name"],
            "orchestrator": team["orchestrator"],
            "agents": [self.agents.public_status(item["id"]) for item in team["agents"]],
            "workflow_steps": len(team["workflow"]),
            "applied_at": team.get("applied_at"),
        }

    def run(
        self,
        team_name: str,
        *,
        approve_all: bool = False,
        approved_steps: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        team = self.get(team_name)
        run_id = f"run-{secrets.token_hex(8)}"
        step_jobs: dict[str, str] = {}
        run = {
            "run_id": run_id,
            "team": team["name"],
            "state": "running",
            "created_at": utc_now(),
            "finished_at": None,
            "steps": {},
        }
        self.store.write_json(self.run_root / f"{run_id}.json", run)
        for step in topological_steps(team["workflow"]):
            dependencies = tuple(step_jobs[dependency] for dependency in step["needs"])
            approved = approve_all or step["id"] in approved_steps or not step["approval"]
            job = self.jobs.create(
                step["agent"],
                step["prompt"],
                timeout_seconds=step["timeout_seconds"],
                dependencies=dependencies,
                input_artifacts=tuple(step["input_artifacts"]),
                approval_required=step["approval"],
                approved=approved,
                labels={"team": team["name"], "run": run_id, "step": step["id"]},
            )
            step_jobs[step["id"]] = job["job_id"]
            run["steps"][step["id"]] = job["job_id"]
            self.store.write_json(self.run_root / f"{run_id}.json", run)
            result = self.job_service.run(job["job_id"])
            if result["state"] != "succeeded":
                run["state"] = "blocked" if result["state"] == "blocked" else "failed"
                run["finished_at"] = utc_now()
                self.store.write_json(self.run_root / f"{run_id}.json", run)
                return run
        run["state"] = "succeeded"
        run["finished_at"] = utc_now()
        self.store.write_json(self.run_root / f"{run_id}.json", run)
        return run

    def reset(self, team_name: str, *, stop: bool = True) -> dict[str, Any]:
        team = self.get(team_name)
        stopped: list[str] = []
        for spec in team["agents"]:
            self.agents.set_rights(spec["id"], ())
            if stop:
                try:
                    self.runtime.stop(spec["id"])
                    stopped.append(spec["id"])
                except ControlPlaneError:
                    # Rights still fail closed even when no container exists.
                    pass
        return {
            "ok": True,
            "team": team["name"],
            "rights_reset": [spec["id"] for spec in team["agents"]],
            "stopped": stopped,
        }

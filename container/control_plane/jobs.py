"""Persistent jobs and dependency-aware result handoffs."""

from __future__ import annotations

import json
from pathlib import Path
import secrets
from typing import Any, Callable

from .agents import AgentRegistry
from .artifacts import ArtifactRepository
from .domain import ControlPlaneError, normalize_identifier, utc_now
from .storage import AtomicStore


TERMINAL_STATES = {"succeeded", "failed", "cancelled", "blocked"}


class JobRepository:
    def __init__(self, root: Path, store: AtomicStore):
        self.root = root.resolve()
        self.store = store
        self.job_root = self.root / "runtime" / "jobs"

    def create(
        self,
        agent_id: str,
        prompt: str,
        *,
        timeout_seconds: int = 900,
        dependencies: tuple[str, ...] = (),
        input_artifacts: tuple[str, ...] = (),
        approval_required: bool = False,
        approved: bool = False,
        labels: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        normalized_agent = normalize_identifier(agent_id, label="agent id")
        clean_prompt = str(prompt).strip()
        if not clean_prompt:
            raise ControlPlaneError("job prompt must not be empty")
        if len(clean_prompt.encode("utf-8")) > 64 * 1024:
            raise ControlPlaneError("job prompt exceeds 64 KiB")
        bounded_timeout = min(max(int(timeout_seconds), 30), 1800)
        job_id = f"job-{secrets.token_hex(8)}"
        now = utc_now()
        value = {
            "job_id": job_id,
            "agent_id": normalized_agent,
            "prompt": clean_prompt,
            "timeout_seconds": bounded_timeout,
            "dependencies": list(dependencies),
            "input_artifacts": list(input_artifacts),
            "approval_required": bool(approval_required),
            "approved": bool(approved),
            "labels": dict(labels or {}),
            "state": "queued",
            "created_at": now,
            "updated_at": now,
            "started_at": None,
            "finished_at": None,
            "result_artifact": None,
            "error": None,
        }
        self.store.write_json(self.job_root / f"{job_id}.json", value)
        return value

    def get(self, job_id: str) -> dict[str, Any]:
        normalized = normalize_identifier(job_id, label="job id")
        return self.store.read_json(self.job_root / f"{normalized}.json")

    def list(self) -> list[dict[str, Any]]:
        if not self.job_root.exists():
            return []
        return [self.store.read_json(path) for path in sorted(self.job_root.glob("job-*.json"))]

    def update(self, job_id: str, **changes: Any) -> dict[str, Any]:
        normalized = normalize_identifier(job_id, label="job id")

        def operation() -> dict[str, Any]:
            current = self.get(normalized)
            current.update(changes)
            current["updated_at"] = utc_now()
            self.store.write_json(self.job_root / f"{normalized}.json", current)
            return current

        return self.store.locked(f"{normalized}", operation)


class JobService:
    """Execute jobs through injected runtime and RPC adapters."""

    def __init__(
        self,
        agents: AgentRegistry,
        jobs: JobRepository,
        artifacts: ArtifactRepository,
        start_agent: Callable[[str], None],
        call_agent: Callable[..., dict[str, Any]],
    ):
        self.agents = agents
        self.jobs = jobs
        self.artifacts = artifacts
        self.start_agent = start_agent
        self.call_agent = call_agent

    def run(self, job_id: str) -> dict[str, Any]:
        job = self.jobs.get(job_id)
        if job["state"] in TERMINAL_STATES:
            return job
        if job["approval_required"] and not job["approved"]:
            return self.jobs.update(
                job_id,
                state="blocked",
                finished_at=utc_now(),
                error="operator approval is required",
            )
        dependency_results: list[dict[str, Any]] = []
        for dependency_id in job["dependencies"]:
            dependency = self.jobs.get(dependency_id)
            if dependency["state"] != "succeeded":
                return self.jobs.update(
                    job_id,
                    state="blocked",
                    finished_at=utc_now(),
                    error=f"dependency {dependency_id} is {dependency['state']}",
                )
            dependency_results.append(dependency)

        self.agents.get(job["agent_id"])
        prompt = self._compose_prompt(job, dependency_results)
        self.jobs.update(job_id, state="running", started_at=utc_now(), error=None)
        try:
            self.start_agent(job["agent_id"])
            result = self.call_agent(
                job["agent_id"],
                "run",
                prompt=prompt,
                timeout_seconds=int(job["timeout_seconds"]),
            )
            current = self.jobs.get(job_id)
            if current["state"] == "cancelled":
                return current
            artifact_id = f"{job_id}-result"
            self.artifacts.put_bytes(
                artifact_id,
                (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
                media_type="application/json",
                producer=job["agent_id"],
            )
            state = "succeeded" if result.get("ok") else "failed"
            return self.jobs.update(
                job_id,
                state=state,
                finished_at=utc_now(),
                result_artifact=artifact_id,
                error=None if result.get("ok") else str(result.get("error") or "agent failed"),
            )
        except Exception as exc:
            current = self.jobs.get(job_id)
            if current["state"] == "cancelled":
                return current
            return self.jobs.update(
                job_id,
                state="failed",
                finished_at=utc_now(),
                error=str(exc),
            )

    def cancel(self, job_id: str) -> dict[str, Any]:
        job = self.jobs.get(job_id)
        if job["state"] in TERMINAL_STATES:
            return job
        if job["state"] == "running":
            try:
                self.call_agent(job["agent_id"], "cancel", timeout_seconds=30)
            except Exception as exc:
                return self.jobs.update(job_id, error=f"cancel request failed: {exc}")
        return self.jobs.update(
            job_id,
            state="cancelled",
            finished_at=utc_now(),
            error=None,
        )

    def approve(self, job_id: str) -> dict[str, Any]:
        job = self.jobs.get(job_id)
        if job["state"] == "blocked" and job.get("error") == "operator approval is required":
            return self.jobs.update(
                job_id, approved=True, state="queued", finished_at=None, error=None
            )
        return self.jobs.update(job_id, approved=True)

    def _compose_prompt(
        self, job: dict[str, Any], dependencies: list[dict[str, Any]]
    ) -> str:
        sections = [job["prompt"]]
        handoffs: list[str] = []
        for dependency in dependencies:
            artifact_id = dependency.get("result_artifact")
            if artifact_id:
                view = self.artifacts.model_view(artifact_id)
                handoffs.append(f"## Dependency {dependency['job_id']}\n{view['content']}")
        for artifact_id in job["input_artifacts"]:
            view = self.artifacts.model_view(artifact_id)
            handoffs.append(f"## Input artifact {artifact_id}\n{view['content']}")
        if handoffs:
            sections.append(
                "Use these immutable handoff artifacts as input. Do not assume a shared workspace.\n\n"
                + "\n\n".join(handoffs)
            )
        result = "\n\n".join(sections)
        if len(result.encode("utf-8")) > 256 * 1024:
            raise ControlPlaneError("composed job prompt exceeds 256 KiB")
        return result

"""Application service composing snapshots, jobs, agents, telemetry, and scoring."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import math
from pathlib import Path
import statistics
import subprocess
from typing import Any, Iterator

from ..agents import AgentRegistry
from ..artifacts import ArtifactRepository
from ..domain import ControlPlaneError, utc_now, validate_rights
from ..jobs import JobRepository, JobService
from ..storage import AtomicStore
from .domain import TRIAL_TERMINAL_STATES
from .environment import DockerTrialEnvironment
from .evaluators import EvaluatorRegistry
from .manifests import load_evaluation_manifest, trial_plan
from .repository import EvaluationRepository
from .snapshots import SnapshotRepository
from .telemetry import TelemetryNormalizer


class EvaluationService:
    def __init__(
        self,
        root: Path,
        store: AtomicStore,
        agents: AgentRegistry,
        artifacts: ArtifactRepository,
        jobs: JobRepository,
        job_service: JobService,
        repository: EvaluationRepository,
        snapshots: SnapshotRepository,
        environment: DockerTrialEnvironment,
        evaluators: EvaluatorRegistry,
        telemetry: TelemetryNormalizer,
    ):
        self.root = root.resolve()
        self.store = store
        self.agents = agents
        self.artifacts = artifacts
        self.jobs = jobs
        self.job_service = job_service
        self.repository = repository
        self.snapshots = snapshots
        self.environment = environment
        self.evaluators = evaluators
        self.telemetry = telemetry

    def plan_manifest(self, path: Path) -> dict[str, Any]:
        specification = load_evaluation_manifest(path)
        return self.plan(specification)

    def plan(self, specification: dict[str, Any]) -> dict[str, Any]:
        targets = []
        for target in specification["targets"]:
            record = self.agents.get(target["agent"])
            for variant in specification["variants"]:
                validate_rights(record.engine, tuple(variant["rights"]))
            targets.append(
                {
                    "agent": record.agent_id,
                    "engine": record.engine,
                    "model": self.agents.model(record.agent_id),
                    "billing_mode": target["billing_mode"],
                    "credential": "shared only through an isolated broker mount",
                }
            )
        evaluator = self.evaluators.get(specification["evaluator"]["type"])
        evaluator.evaluate("", specification["evaluator"]["config"])
        trials = trial_plan(specification)
        return {
            "ok": True,
            "dry_run": True,
            "name": specification["name"],
            "targets": targets,
            "variants": [
                {"id": item["id"], "rights": item["rights"]}
                for item in specification["variants"]
            ],
            "trials": len(trials),
            "order": specification["execution"]["order"],
            "seed": specification["execution"]["seed"],
            "isolation": "temporary agent/workspace/context per trial",
            "canonical_context_mutated": False,
        }

    def create_manifest(self, path: Path) -> dict[str, Any]:
        specification = load_evaluation_manifest(path)
        self.plan(specification)
        experiment_id = self.repository.allocate_id()
        prompt_artifact = f"{experiment_id}-prompt"
        self.artifacts.put_bytes(
            prompt_artifact,
            (specification["scenario"]["prompt"] + "\n").encode("utf-8"),
            media_type="text/markdown",
            producer="evaluation-snapshot",
        )
        workspace_artifact = self.snapshots.workspace_snapshot(
            experiment_id, specification["scenario"]["workspace_source"]
        )
        stored_variants = []
        for variant in specification["variants"]:
            context_artifact = self.snapshots.context_snapshot(
                experiment_id, variant["id"], variant["context_sources"]
            )
            workspace_overlay_artifact = (
                self.snapshots.directory_snapshot(
                    f"{experiment_id}-workspace-{variant['id']}",
                    variant["workspace_overlay_source"],
                    media_type="application/vnd.hermes.workspace-overlay+tar",
                )
                if variant.get("workspace_overlay_source")
                else None
            )
            stored_variants.append(
                {
                    "id": variant["id"],
                    "label": variant["label"],
                    "rights": variant["rights"],
                    "context_artifact": context_artifact,
                    "workspace_overlay_artifact": workspace_overlay_artifact,
                }
            )
        stored_specification = {
            "version": 1,
            "name": specification["name"],
            "targets": specification["targets"],
            "scenario": {
                "prompt_artifact": prompt_artifact,
                "workspace_artifact": workspace_artifact,
            },
            "variants": stored_variants,
            "execution": specification["execution"],
            "evaluator": specification["evaluator"],
            "metrics": specification["metrics"],
        }
        snapshot_artifacts = [
            artifact_id
            for artifact_id in [
                prompt_artifact,
                workspace_artifact,
                *[item["context_artifact"] for item in stored_variants],
                *[item["workspace_overlay_artifact"] for item in stored_variants],
            ]
            if artifact_id
        ]
        provenance = {
            "manifest_source": specification.get("source"),
            "git_commit": self._git_commit(),
            "snapshot_artifacts": snapshot_artifacts,
            "snapshot_digests": {
                artifact_id: self.artifacts.metadata(artifact_id)["sha256"]
                for artifact_id in snapshot_artifacts
            },
            "target_snapshots": [
                {
                    "agent": target["agent"],
                    "engine": self.agents.get(target["agent"]).engine,
                    "model": self.agents.model(target["agent"]),
                    "source_rights": list(self.agents.rights(target["agent"])),
                }
                for target in specification["targets"]
            ],
        }
        value = self.repository.create(
            name=specification["name"],
            specification=stored_specification,
            trials=trial_plan(specification),
            provenance=provenance,
            experiment_id=experiment_id,
        )
        return self.public_view(value, include_trials=False)

    def list(self) -> list[dict[str, Any]]:
        return self.repository.list()

    def status(self, experiment_id: str, *, include_trials: bool = True) -> dict[str, Any]:
        return self.public_view(self.repository.get(experiment_id), include_trials=include_trials)

    @staticmethod
    def public_view(value: dict[str, Any], *, include_trials: bool) -> dict[str, Any]:
        result = {
            key: value.get(key)
            for key in (
                "experiment_id",
                "name",
                "state",
                "cancel_requested",
                "created_at",
                "updated_at",
                "started_at",
                "finished_at",
                "current_trial_id",
                "progress",
                "summary",
                "error",
                "provenance",
                "specification",
            )
        }
        if include_trials:
            result["trials"] = value.get("trials", [])
        return result

    @contextmanager
    def _execution_lock(self, experiment_id: str) -> Iterator[None]:
        lock_root = self.store.ensure_directory(self.root / "runtime" / "locks")
        path = lock_root / f"evaluation-run-{experiment_id}.lock"
        with path.open("a+", encoding="utf-8") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ControlPlaneError(f"evaluation is already running: {experiment_id}") from exc
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def run(self, experiment_id: str) -> dict[str, Any]:
        with self._execution_lock(experiment_id):
            self._recover_stale_trial(experiment_id)
            experiment = self.repository.get(experiment_id)
            if experiment["state"] == "succeeded":
                return self.public_view(experiment, include_trials=True)
            if experiment["cancel_requested"]:
                experiment = self.repository.update(
                    experiment_id,
                    state="cancelled",
                    finished_at=utc_now(),
                    current_trial_id=None,
                )
                return self.public_view(experiment, include_trials=True)
            self.repository.update(
                experiment_id,
                state="running",
                started_at=experiment.get("started_at") or utc_now(),
                finished_at=None,
                error=None,
            )
            try:
                for trial in self.repository.get(experiment_id)["trials"]:
                    if trial["state"] in TRIAL_TERMINAL_STATES:
                        continue
                    if self.repository.get(experiment_id)["cancel_requested"]:
                        break
                    self._run_trial(experiment_id, trial["trial_id"])
                current = self.repository.get(experiment_id)
                cancelled = current["cancel_requested"]
                summary = self._summary(current)
                final = self.repository.update(
                    experiment_id,
                    state="cancelled" if cancelled else "succeeded",
                    finished_at=utc_now(),
                    current_trial_id=None,
                    summary=summary,
                )
                return self.public_view(final, include_trials=True)
            except Exception as exc:
                failed = self.repository.update(
                    experiment_id,
                    state="failed",
                    finished_at=utc_now(),
                    current_trial_id=None,
                    error=str(exc),
                    summary=self._summary(self.repository.get(experiment_id)),
                )
                return self.public_view(failed, include_trials=True)

    def _run_trial(self, experiment_id: str, trial_id: str) -> None:
        experiment = self.repository.get(experiment_id)
        trial = next(item for item in experiment["trials"] if item["trial_id"] == trial_id)
        source = self.agents.get(trial["source_agent_id"])

        def operation() -> None:
            if self.repository.get(experiment_id)["cancel_requested"]:
                self.repository.update_trial(
                    experiment_id,
                    trial_id,
                    state="cancelled",
                    finished_at=utc_now(),
                    error="cancelled before credential lease",
                )
                current = self.repository.get(experiment_id)
                done = sum(
                    1
                    for item in current["trials"]
                    if item["state"] in TRIAL_TERMINAL_STATES
                )
                self.repository.update(
                    experiment_id,
                    progress={"done": done, "total": len(current["trials"])},
                )
                return
            self._run_trial_with_credential(experiment_id, trial_id)

        self.store.locked(f"evaluation-credential-{source.credential_id}", operation)

    def _run_trial_with_credential(self, experiment_id: str, trial_id: str) -> None:
        experiment = self.repository.get(experiment_id)
        trial = next(item for item in experiment["trials"] if item["trial_id"] == trial_id)
        specification = experiment["specification"]
        variant = next(item for item in specification["variants"] if item["id"] == trial["variant_id"])
        source = self.agents.get(trial["source_agent_id"])
        attempt = int(trial.get("attempt") or 0) + 1
        ephemeral_id = self.environment.provision(
            experiment_id=experiment_id,
            trial_id=trial_id,
            source_agent_id=source.agent_id,
            rights=tuple(variant["rights"]),
            workspace_artifact=specification["scenario"]["workspace_artifact"],
            workspace_overlay_artifact=variant["workspace_overlay_artifact"],
            context_artifact=variant["context_artifact"],
        )
        self.repository.update_trial(
            experiment_id,
            trial_id,
            state="running",
            attempt=attempt,
            ephemeral_agent_id=ephemeral_id,
            started_at=utc_now(),
            finished_at=None,
            error=None,
        )
        self.repository.update(experiment_id, current_trial_id=trial_id)
        cleanup_error: Exception | None = None
        try:
            prompt = self.artifacts.model_view(
                specification["scenario"]["prompt_artifact"]
            )["content"].strip()
            job = self.jobs.create(
                ephemeral_id,
                prompt,
                timeout_seconds=specification["execution"]["timeout_seconds"],
                labels={
                    "evaluation": experiment_id,
                    "trial": trial_id,
                    "target": source.agent_id,
                    "variant": variant["id"],
                },
            )
            self.repository.update_trial(experiment_id, trial_id, job_id=job["job_id"])
            job_result = self.job_service.run(job["job_id"])
            raw_artifact = job_result.get("result_artifact")
            if not raw_artifact:
                raise ControlPlaneError(job_result.get("error") or "trial produced no result artifact")
            raw = json.loads(self.artifacts.model_view(raw_artifact)["content"])
            normalized = self.telemetry.normalize(source.engine, raw, trial["billing_mode"])
            outcome = self.evaluators.get(specification["evaluator"]["type"]).evaluate(
                normalized.final_text,
                specification["evaluator"]["config"],
            )
            result_artifact = f"{experiment_id}-{trial_id}-attempt-{attempt}"
            self.artifacts.put_bytes(
                result_artifact,
                (
                    json.dumps(
                        {"normalized": normalized.to_dict(), "evaluation": outcome.to_dict()},
                        ensure_ascii=False,
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n"
                ).encode("utf-8"),
                media_type="application/json",
                producer=ephemeral_id,
            )
            measurement = {
                "engine": source.engine,
                "cli_version": raw.get("cli_version"),
                "model": self.agents.model(source.agent_id),
                "duration_ms": raw.get("duration_ms"),
                "timed_out": bool(raw.get("timed_out", False)),
                "truncated": bool(raw.get("truncated", False)),
                "usage": normalized.usage.to_dict(),
                "reported_cost_usd": normalized.reported_cost_usd,
                "billing_mode": normalized.billing_mode,
                "parse_error": normalized.parse_error,
                "policy": raw.get("policy"),
                "enforcement": raw.get("enforcement"),
            }
            state = "succeeded" if job_result["state"] == "succeeded" else "failed"
            self.repository.update_trial(
                experiment_id,
                trial_id,
                state=state,
                raw_result_artifact=raw_artifact,
                result_artifact=result_artifact,
                measurement=measurement,
                evaluation=outcome.to_dict(),
                finished_at=utc_now(),
                error=job_result.get("error"),
            )
        except Exception as exc:
            self.repository.update_trial(
                experiment_id,
                trial_id,
                state="cancelled"
                if self.repository.get(experiment_id)["cancel_requested"]
                else "failed",
                finished_at=utc_now(),
                error=str(exc),
            )
        finally:
            try:
                self.environment.cleanup(ephemeral_id)
            except Exception as exc:
                cleanup_error = exc
            if cleanup_error:
                self.repository.update_trial(
                    experiment_id,
                    trial_id,
                    state="failed",
                    finished_at=utc_now(),
                    error=str(cleanup_error),
                )
            else:
                self.repository.update_trial(
                    experiment_id, trial_id, ephemeral_agent_id=None
                )
            current = self.repository.get(experiment_id)
            done = sum(
                1 for item in current["trials"] if item["state"] in TRIAL_TERMINAL_STATES
            )
            self.repository.update(
                experiment_id,
                current_trial_id=None,
                progress={"done": done, "total": len(current["trials"])},
            )
            if cleanup_error:
                raise cleanup_error

    def cancel(self, experiment_id: str) -> dict[str, Any]:
        experiment = self.repository.update(experiment_id, cancel_requested=True)
        current_trial = experiment.get("current_trial_id")
        if current_trial:
            trial = next(
                item for item in experiment["trials"] if item["trial_id"] == current_trial
            )
            if trial.get("job_id"):
                self.job_service.cancel(trial["job_id"])
        return self.status(experiment_id)

    def resume(self, experiment_id: str) -> dict[str, Any]:
        experiment = self.repository.get(experiment_id)
        if experiment["state"] == "succeeded":
            return self.public_view(experiment, include_trials=True)
        self.repository.update(
            experiment_id,
            cancel_requested=False,
            state="planned",
            finished_at=None,
            error=None,
        )
        return self.run(experiment_id)

    def _recover_stale_trial(self, experiment_id: str) -> None:
        experiment = self.repository.get(experiment_id)
        for trial in experiment["trials"]:
            if trial["state"] != "running":
                continue
            ephemeral_id = trial.get("ephemeral_agent_id")
            if ephemeral_id:
                self.environment.cleanup(ephemeral_id)
            self.repository.update_trial(
                experiment_id,
                trial["trial_id"],
                state="queued",
                ephemeral_agent_id=None,
                job_id=None,
                started_at=None,
                finished_at=None,
                error="recovered after interrupted runner",
            )

    def export(self, experiment_id: str, destination: Path) -> Path:
        experiment = self.repository.get(experiment_id)
        portable = self.public_view(experiment, include_trials=True)
        for trial in portable["trials"]:
            result_artifact = trial.get("result_artifact")
            if result_artifact:
                trial["result"] = json.loads(
                    self.artifacts.model_view(result_artifact)["content"]
                )
        resolved = destination.resolve()
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(
            json.dumps(portable, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return resolved

    @staticmethod
    def _percentile(values: list[float], percentile: float) -> float | None:
        if not values:
            return None
        ordered = sorted(values)
        index = max(0, math.ceil(percentile * len(ordered)) - 1)
        return ordered[index]

    @staticmethod
    def _wilson_interval(successes: int, total: int) -> list[float] | None:
        if total <= 0:
            return None
        z = 1.959963984540054
        proportion = successes / total
        denominator = 1 + z * z / total
        centre = (proportion + z * z / (2 * total)) / denominator
        margin = (
            z
            * math.sqrt(
                proportion * (1 - proportion) / total + z * z / (4 * total * total)
            )
            / denominator
        )
        return [max(0.0, centre - margin), min(1.0, centre + margin)]

    def _summary(self, experiment: dict[str, Any]) -> dict[str, Any]:
        groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for trial in experiment["trials"]:
            groups.setdefault((trial["source_agent_id"], trial["variant_id"]), []).append(trial)
        rows = []
        for (agent_id, variant_id), trials in sorted(groups.items()):
            measured = [trial for trial in trials if not trial.get("warmup", False)]
            warmups = [trial for trial in trials if trial.get("warmup", False)]
            completed = [trial for trial in measured if trial["state"] == "succeeded"]
            passed = [trial for trial in completed if trial.get("evaluation", {}).get("passed")]
            durations = [
                float(trial["measurement"]["duration_ms"])
                for trial in completed
                if trial.get("measurement", {}).get("duration_ms") is not None
            ]
            scores = [
                float(trial["evaluation"]["score"])
                for trial in completed
                if trial.get("evaluation", {}).get("score") is not None
            ]
            def usage_values(field: str) -> list[float]:
                return [
                    float(trial["measurement"]["usage"][field])
                    for trial in completed
                    if trial.get("measurement", {}).get("usage", {}).get(field) is not None
                ]

            input_tokens = usage_values("input_tokens")
            output_tokens = usage_values("output_tokens")
            reasoning_tokens = usage_values("reasoning_tokens")
            cache_read_tokens = usage_values("cache_read_tokens")
            cache_write_tokens = usage_values("cache_write_tokens")
            costs = [
                float(trial["measurement"]["reported_cost_usd"])
                for trial in completed
                if trial.get("measurement", {}).get("reported_cost_usd") is not None
            ]
            rows.append(
                {
                    "agent": agent_id,
                    "variant": variant_id,
                    "trials": len(measured),
                    "warmups": len(warmups),
                    "completed": len(completed),
                    "errors": len(measured) - len(completed),
                    "passed": len(passed),
                    "pass_rate": len(passed) / len(completed) if completed else None,
                    "pass_rate_wilson_95": self._wilson_interval(
                        len(passed), len(completed)
                    ),
                    "median_score": statistics.median(scores) if scores else None,
                    "median_duration_ms": statistics.median(durations) if durations else None,
                    "p95_duration_ms": self._percentile(durations, 0.95),
                    "median_input_tokens": statistics.median(input_tokens)
                    if input_tokens
                    else None,
                    "median_output_tokens": statistics.median(output_tokens)
                    if output_tokens
                    else None,
                    "median_reasoning_tokens": statistics.median(reasoning_tokens)
                    if reasoning_tokens
                    else None,
                    "median_cache_read_tokens": statistics.median(cache_read_tokens)
                    if cache_read_tokens
                    else None,
                    "median_cache_write_tokens": statistics.median(cache_write_tokens)
                    if cache_write_tokens
                    else None,
                    "reported_cost_total_usd": sum(costs) if costs else None,
                    "reported_cost_mean_usd": statistics.mean(costs) if costs else None,
                }
            )
        return {"groups": rows}

    def _git_commit(self) -> str | None:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=self.root,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        return result.stdout.strip() if result.returncode == 0 else None

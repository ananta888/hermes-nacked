"""Atomic persistence for experiment plans, trials, and summaries."""

from __future__ import annotations

from pathlib import Path
import secrets
from typing import Any, Callable

from ..domain import ControlPlaneError, normalize_identifier, utc_now
from ..storage import AtomicStore


class EvaluationRepository:
    def __init__(self, root: Path, store: AtomicStore):
        self.root = root.resolve()
        self.store = store
        self.evaluation_root = self.root / "runtime" / "evaluations"

    def _path(self, experiment_id: str) -> Path:
        normalized = normalize_identifier(experiment_id, label="experiment id")
        return self.evaluation_root / f"{normalized}.json"

    @staticmethod
    def allocate_id() -> str:
        return f"eval-{secrets.token_hex(8)}"

    def create(
        self,
        *,
        name: str,
        specification: dict[str, Any],
        trials: list[dict[str, Any]],
        provenance: dict[str, Any],
        experiment_id: str | None = None,
    ) -> dict[str, Any]:
        experiment_id = experiment_id or self.allocate_id()
        now = utc_now()
        normalized_trials = [
            {
                **trial,
                "state": "queued",
                "attempt": 0,
                "ephemeral_agent_id": None,
                "job_id": None,
                "raw_result_artifact": None,
                "started_at": None,
                "finished_at": None,
                "measurement": None,
                "evaluation": None,
                "error": None,
            }
            for trial in trials
        ]
        value = {
            "experiment_id": experiment_id,
            "name": name,
            "state": "planned",
            "cancel_requested": False,
            "created_at": now,
            "updated_at": now,
            "started_at": None,
            "finished_at": None,
            "current_trial_id": None,
            "specification": specification,
            "provenance": provenance,
            "progress": {"done": 0, "total": len(normalized_trials)},
            "summary": None,
            "trials": normalized_trials,
            "error": None,
        }
        self.store.write_json(self._path(experiment_id), value)
        return value

    def get(self, experiment_id: str) -> dict[str, Any]:
        return self.store.read_json(self._path(experiment_id))

    def list(self) -> list[dict[str, Any]]:
        if not self.evaluation_root.exists():
            return []
        result = []
        for path in sorted(self.evaluation_root.glob("eval-*.json")):
            if path.is_file() and not path.is_symlink():
                value = self.store.read_json(path)
                result.append(
                    {
                        key: value.get(key)
                        for key in (
                            "experiment_id",
                            "name",
                            "state",
                            "created_at",
                            "updated_at",
                            "progress",
                            "summary",
                        )
                    }
                )
        return result

    def mutate(
        self, experiment_id: str, operation: Callable[[dict[str, Any]], None]
    ) -> dict[str, Any]:
        normalized = normalize_identifier(experiment_id, label="experiment id")

        def locked_operation() -> dict[str, Any]:
            value = self.get(normalized)
            operation(value)
            value["updated_at"] = utc_now()
            self.store.write_json(self._path(normalized), value)
            return value

        return self.store.locked(f"evaluation-{normalized}", locked_operation)

    def update(self, experiment_id: str, **changes: Any) -> dict[str, Any]:
        def operation(value: dict[str, Any]) -> None:
            value.update(changes)

        return self.mutate(experiment_id, operation)

    def update_trial(
        self, experiment_id: str, trial_id: str, **changes: Any
    ) -> dict[str, Any]:
        def operation(value: dict[str, Any]) -> None:
            for trial in value["trials"]:
                if trial["trial_id"] == trial_id:
                    trial.update(changes)
                    return
            raise ControlPlaneError(f"unknown trial id: {trial_id}")

        return self.mutate(experiment_id, operation)

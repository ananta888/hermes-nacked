"""Narrow interfaces used by the evaluation application service."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from .domain import EvaluationOutcome, NormalizedResult


class ResultNormalizer(Protocol):
    def normalize(self, engine: str, response: dict[str, Any], billing_mode: str) -> NormalizedResult:
        """Convert one engine response into the stable evaluation envelope."""


class Evaluator(Protocol):
    def evaluate(self, final_text: str, configuration: dict[str, Any]) -> EvaluationOutcome:
        """Score a model result without knowing how it was executed."""


class TrialEnvironment(Protocol):
    def provision(
        self,
        *,
        experiment_id: str,
        trial_id: str,
        source_agent_id: str,
        rights: tuple[str, ...],
        workspace_artifact: str | None,
        workspace_overlay_artifact: str | None,
        context_artifact: str | None,
    ) -> str:
        """Create and return an isolated temporary agent id."""

    def cleanup(self, agent_id: str) -> None:
        """Stop and remove one temporary agent without deleting its credential."""


class ExperimentExporter(Protocol):
    def export(self, experiment: dict[str, Any], destination: Path) -> Path:
        """Write a portable experiment result."""

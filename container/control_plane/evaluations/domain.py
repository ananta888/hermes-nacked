"""Evaluation domain values without storage or runtime dependencies."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


EXPERIMENT_TERMINAL_STATES = {"succeeded", "failed", "cancelled"}
TRIAL_TERMINAL_STATES = {"succeeded", "failed", "cancelled"}


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class NormalizedResult:
    final_text: str
    usage: TokenUsage
    reported_cost_usd: float | None
    billing_mode: str
    parse_error: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "final_text": self.final_text,
            "usage": self.usage.to_dict(),
            "reported_cost_usd": self.reported_cost_usd,
            "billing_mode": self.billing_mode,
            "parse_error": self.parse_error,
        }


@dataclass(frozen=True)
class EvaluationOutcome:
    passed: bool
    score: float
    observed: Any
    details: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

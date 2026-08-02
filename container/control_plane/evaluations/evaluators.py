"""Open evaluator registry with small built-in scoring strategies."""

from __future__ import annotations

import json
import re
from typing import Any

from ..domain import ControlPlaneError
from .domain import EvaluationOutcome
from .ports import Evaluator


def _bounded_score(value: float) -> float:
    return min(max(float(value), 0.0), 1.0)


def _json_object(text: str) -> dict[str, Any]:
    candidates = [text.strip()]
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL | re.IGNORECASE)
    if fenced:
        candidates.insert(0, fenced.group(1))
    first = text.find("{")
    last = text.rfind("}")
    if 0 <= first < last:
        candidates.append(text[first : last + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("no JSON object found")


class ExactJsonEvaluator:
    def evaluate(self, final_text: str, configuration: dict[str, Any]) -> EvaluationOutcome:
        expected = configuration.get("expected")
        if not isinstance(expected, dict):
            raise ControlPlaneError("exact-json evaluator requires an expected object")
        try:
            observed = _json_object(final_text)
        except ValueError as exc:
            return EvaluationOutcome(False, 0.0, None, {"error": str(exc)})
        matched = sum(1 for key, value in expected.items() if observed.get(key) == value)
        score = matched / len(expected) if expected else 1.0
        return EvaluationOutcome(observed == expected, score, observed, {"expected": expected})


class ContainsEvaluator:
    def evaluate(self, final_text: str, configuration: dict[str, Any]) -> EvaluationOutcome:
        required = configuration.get("required")
        if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
            raise ControlPlaneError("contains evaluator requires a string list named required")
        case_sensitive = bool(configuration.get("case_sensitive", False))
        haystack = final_text if case_sensitive else final_text.casefold()
        matches = {
            item: (item if case_sensitive else item.casefold()) in haystack for item in required
        }
        score = sum(matches.values()) / len(matches) if matches else 1.0
        return EvaluationOutcome(all(matches.values()), score, matches, {})


class RegexFieldsEvaluator:
    def evaluate(self, final_text: str, configuration: dict[str, Any]) -> EvaluationOutcome:
        fields = configuration.get("fields")
        expected = configuration.get("expected")
        if not isinstance(fields, dict) or not all(
            isinstance(key, str) and isinstance(pattern, str) for key, pattern in fields.items()
        ):
            raise ControlPlaneError("regex-fields evaluator requires a fields mapping")
        if not isinstance(expected, dict):
            raise ControlPlaneError("regex-fields evaluator requires an expected object")
        observed: dict[str, Any] = {}
        errors: dict[str, str] = {}
        for key, pattern in fields.items():
            try:
                match = re.search(pattern, final_text, re.IGNORECASE | re.MULTILINE)
            except re.error as exc:
                raise ControlPlaneError(f"invalid evaluator regex for {key}: {exc}") from exc
            if not match:
                errors[key] = "not found"
                continue
            value: Any = match.group(1) if match.groups() else match.group(0)
            if isinstance(expected.get(key), int):
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    errors[key] = "matched value is not an integer"
                    continue
            elif isinstance(expected.get(key), float):
                try:
                    value = float(value)
                except (TypeError, ValueError):
                    errors[key] = "matched value is not numeric"
                    continue
            observed[key] = value
        matched = sum(1 for key, value in expected.items() if observed.get(key) == value)
        score = matched / len(expected) if expected else 1.0
        return EvaluationOutcome(
            observed == expected,
            _bounded_score(score),
            observed,
            {"expected": expected, "errors": errors},
        )


class EvaluatorRegistry:
    """OCP extension point: new evaluators register without changing the service."""

    def __init__(self) -> None:
        self._evaluators: dict[str, Evaluator] = {}
        self.register("exact-json", ExactJsonEvaluator())
        self.register("contains", ContainsEvaluator())
        self.register("regex-fields", RegexFieldsEvaluator())

    def register(self, name: str, evaluator: Evaluator) -> None:
        normalized = str(name).strip().lower()
        if not normalized:
            raise ControlPlaneError("evaluator name must not be empty")
        self._evaluators[normalized] = evaluator

    def get(self, name: str) -> Evaluator:
        normalized = str(name).strip().lower()
        try:
            return self._evaluators[normalized]
        except KeyError as exc:
            raise ControlPlaneError(
                f"unknown evaluator: {name}; available: {', '.join(sorted(self._evaluators))}"
            ) from exc

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._evaluators))

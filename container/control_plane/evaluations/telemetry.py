"""Provider adapters that normalize CLI output for comparisons."""

from __future__ import annotations

import json
from typing import Any, Callable, Iterable

from ..domain import ControlPlaneError, normalize_engine
from .domain import NormalizedResult, TokenUsage


def _json_lines(output: str) -> Iterable[dict[str, Any]]:
    for line in output.splitlines():
        try:
            value = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(value, dict):
            yield value


def _integer(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class TelemetryNormalizer:
    """Stable result envelope backed by one replaceable parser per engine."""

    def __init__(self) -> None:
        self._parsers: dict[str, Callable[[str, str], NormalizedResult]] = {
            "claude": self._claude,
            "codex": self._codex,
            "opencode": self._opencode,
        }

    def register(
        self, engine: str, parser: Callable[[str, str], NormalizedResult]
    ) -> None:
        self._parsers[normalize_engine(engine)] = parser

    def normalize(
        self, engine: str, response: dict[str, Any], billing_mode: str
    ) -> NormalizedResult:
        normalized_engine = normalize_engine(engine)
        output = response.get("output", "")
        if not isinstance(output, str):
            output = str(output)
        try:
            return self._parsers[normalized_engine](output, billing_mode)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            return NormalizedResult(output, TokenUsage(), None, billing_mode, True)

    @staticmethod
    def _claude(output: str, billing_mode: str) -> NormalizedResult:
        value = json.loads(output)
        if not isinstance(value, dict):
            raise ValueError("Claude output is not an object")
        usage = value.get("usage") if isinstance(value.get("usage"), dict) else {}
        return NormalizedResult(
            str(value.get("result") or ""),
            TokenUsage(
                input_tokens=_integer(usage.get("input_tokens")),
                output_tokens=_integer(usage.get("output_tokens")),
                cache_read_tokens=_integer(usage.get("cache_read_input_tokens")),
                cache_write_tokens=_integer(usage.get("cache_creation_input_tokens")),
            ),
            _number(value.get("total_cost_usd")) if billing_mode == "api" else None,
            billing_mode,
            False,
        )

    @staticmethod
    def _codex(output: str, billing_mode: str) -> NormalizedResult:
        final_text: str | None = None
        usage: dict[str, Any] = {}
        for event in _json_lines(output):
            item = event.get("item") if isinstance(event.get("item"), dict) else {}
            if event.get("type") == "item.completed" and item.get("type") == "agent_message":
                final_text = str(item.get("text") or "")
            if event.get("type") == "turn.completed" and isinstance(event.get("usage"), dict):
                usage = event["usage"]
        return NormalizedResult(
            final_text or "",
            TokenUsage(
                input_tokens=_integer(usage.get("input_tokens")),
                output_tokens=_integer(usage.get("output_tokens")),
                reasoning_tokens=_integer(usage.get("reasoning_output_tokens")),
                cache_read_tokens=_integer(usage.get("cached_input_tokens")),
            ),
            None,
            billing_mode,
            final_text is None,
        )

    @staticmethod
    def _opencode(output: str, billing_mode: str) -> NormalizedResult:
        final_parts: list[str] = []
        usage: dict[str, Any] = {}
        cost: float | None = None
        for event in _json_lines(output):
            part = event.get("part") if isinstance(event.get("part"), dict) else {}
            if event.get("type") == "text" and part.get("text"):
                final_parts.append(str(part["text"]))
            if event.get("type") == "step_finish":
                if isinstance(part.get("tokens"), dict):
                    usage = part["tokens"]
                cost = _number(part.get("cost"))
        return NormalizedResult(
            "".join(final_parts),
            TokenUsage(
                input_tokens=_integer(usage.get("input")),
                output_tokens=_integer(usage.get("output")),
                reasoning_tokens=_integer(usage.get("reasoning")),
                cache_read_tokens=_integer(usage.get("cache", {}).get("read"))
                if isinstance(usage.get("cache"), dict)
                else None,
                cache_write_tokens=_integer(usage.get("cache", {}).get("write"))
                if isinstance(usage.get("cache"), dict)
                else None,
            ),
            cost if billing_mode == "api" else None,
            billing_mode,
            not final_parts,
        )

"""Strict versioned evaluation manifests with path-safe normalization."""

from __future__ import annotations

import json
from pathlib import Path
import random
from typing import Any

from ..domain import ControlPlaneError, expand_rights, normalize_identifier


METRICS = {
    "correctness",
    "latency",
    "tokens",
    "reported-cost",
}
BILLING_MODES = {"subscription", "api", "local", "unknown"}


def _exact_fields(value: dict[str, Any], allowed: set[str], label: str) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise ControlPlaneError(
            f"unknown {label} fields: {', '.join(sorted(str(item) for item in unknown))}"
        )


def _load_yaml_or_json(raw: str) -> Any:
    try:
        import yaml  # type: ignore

        return yaml.safe_load(raw)
    except ImportError:
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ControlPlaneError(
                "standard YAML needs PyYAML; otherwise use JSON syntax (valid YAML)"
            ) from exc
    except Exception as exc:
        raise ControlPlaneError(f"invalid evaluation YAML: {exc}") from exc


def _source_path(base: Path, value: Any, *, label: str, directory: bool = False) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ControlPlaneError(f"{label} must be a non-empty path")
    resolved = (base / value).resolve() if not Path(value).is_absolute() else Path(value).resolve()
    valid = resolved.is_dir() if directory else resolved.is_file()
    if not valid or resolved.is_symlink():
        kind = "directory" if directory else "regular file"
        raise ControlPlaneError(f"{label} is not a non-symlink {kind}: {resolved}")
    return str(resolved)


def _skill_source_path(base: Path, value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ControlPlaneError(f"{label} must be a non-empty path")
    resolved = (base / value).resolve() if not Path(value).is_absolute() else Path(value).resolve()
    if resolved.is_symlink() or not (resolved.is_file() or resolved.is_dir()):
        raise ControlPlaneError(f"{label} must be a non-symlink SKILL.md or skill directory")
    return str(resolved)


def load_evaluation_manifest(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    try:
        raw = resolved.read_text(encoding="utf-8")
    except OSError as exc:
        raise ControlPlaneError(f"cannot read evaluation manifest: {exc}") from exc
    value = _load_yaml_or_json(raw)
    if not isinstance(value, dict):
        raise ControlPlaneError("evaluation manifest must be a mapping")
    return normalize_evaluation_manifest(value, base=resolved.parent, source=str(resolved))


def normalize_evaluation_manifest(
    value: dict[str, Any], *, base: Path, source: str | None = None
) -> dict[str, Any]:
    _exact_fields(
        value,
        {"version", "name", "targets", "scenario", "variants", "execution", "evaluator", "metrics"},
        "evaluation",
    )
    if int(value.get("version", 0)) != 1:
        raise ControlPlaneError("evaluation manifest version must be 1")
    name = normalize_identifier(value.get("name", ""), label="evaluation name")

    raw_targets = value.get("targets")
    if not isinstance(raw_targets, list) or not raw_targets:
        raise ControlPlaneError("evaluation targets must be a non-empty list")
    targets: list[dict[str, Any]] = []
    seen_targets: set[str] = set()
    for index, item in enumerate(raw_targets):
        if isinstance(item, str):
            item = {"agent": item}
        if not isinstance(item, dict):
            raise ControlPlaneError(f"evaluation target {index} must be a mapping")
        _exact_fields(item, {"agent", "label", "billing_mode"}, f"target {index}")
        agent_id = normalize_identifier(item.get("agent", ""), label="agent id")
        if agent_id in seen_targets:
            raise ControlPlaneError(f"duplicate evaluation target: {agent_id}")
        seen_targets.add(agent_id)
        billing_mode = str(item.get("billing_mode") or "unknown").strip().lower()
        if billing_mode not in BILLING_MODES:
            raise ControlPlaneError(
                f"target {agent_id} billing_mode must be one of {', '.join(sorted(BILLING_MODES))}"
            )
        targets.append(
            {
                "agent": agent_id,
                "label": str(item.get("label") or agent_id).strip() or agent_id,
                "billing_mode": billing_mode,
            }
        )

    raw_scenario = value.get("scenario")
    if not isinstance(raw_scenario, dict):
        raise ControlPlaneError("evaluation scenario must be a mapping")
    _exact_fields(raw_scenario, {"prompt", "prompt_file", "workspace"}, "scenario")
    prompt = raw_scenario.get("prompt")
    prompt_file = raw_scenario.get("prompt_file")
    if bool(prompt) == bool(prompt_file):
        raise ControlPlaneError("scenario needs exactly one of prompt or prompt_file")
    if prompt_file:
        prompt_path = _source_path(base, prompt_file, label="scenario prompt_file")
        try:
            prompt = Path(prompt_path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ControlPlaneError(f"cannot read scenario prompt_file: {exc}") from exc
    if not isinstance(prompt, str) or not prompt.strip():
        raise ControlPlaneError("scenario prompt must be non-empty UTF-8 text")
    if len(prompt.encode("utf-8")) > 64 * 1024:
        raise ControlPlaneError("scenario prompt exceeds 64 KiB")
    workspace = raw_scenario.get("workspace")
    scenario = {
        "prompt": prompt.strip(),
        "workspace_source": _source_path(base, workspace, label="scenario workspace", directory=True)
        if workspace
        else None,
    }

    raw_variants = value.get("variants")
    if not isinstance(raw_variants, list) or not raw_variants:
        raise ControlPlaneError("evaluation variants must be a non-empty list")
    variants: list[dict[str, Any]] = []
    seen_variants: set[str] = set()
    for index, item in enumerate(raw_variants):
        if not isinstance(item, dict):
            raise ControlPlaneError(f"evaluation variant {index} must be a mapping")
        _exact_fields(
            item,
            {"id", "label", "rights", "context", "workspace_overlay"},
            f"variant {index}",
        )
        variant_id = normalize_identifier(item.get("id", ""), label="variant id")
        if variant_id in seen_variants:
            raise ControlPlaneError(f"duplicate evaluation variant: {variant_id}")
        seen_variants.add(variant_id)
        raw_rights = item.get("rights", [])
        if not isinstance(raw_rights, list) or not all(isinstance(right, str) for right in raw_rights):
            raise ControlPlaneError(f"variant {variant_id} rights must be a string list")
        rights = list(expand_rights(raw_rights))
        raw_context = item.get("context", {})
        if not isinstance(raw_context, dict):
            raise ControlPlaneError(f"variant {variant_id} context must be a mapping")
        _exact_fields(raw_context, {"agents_md", "claude_md", "skills"}, f"variant {variant_id} context")
        skills = raw_context.get("skills", [])
        if not isinstance(skills, list):
            raise ControlPlaneError(f"variant {variant_id} context.skills must be a list")
        context = {
            "agents_md": _source_path(base, raw_context["agents_md"], label=f"variant {variant_id} agents_md")
            if raw_context.get("agents_md")
            else None,
            "claude_md": _source_path(base, raw_context["claude_md"], label=f"variant {variant_id} claude_md")
            if raw_context.get("claude_md")
            else None,
            "skills": [
                _skill_source_path(base, skill, label=f"variant {variant_id} skill")
                for skill in skills
            ],
        }
        if context["agents_md"] and "agents-md" not in rights:
            raise ControlPlaneError(f"variant {variant_id} provides AGENTS.md without agents-md right")
        if context["claude_md"] and "claude-md" not in rights:
            raise ControlPlaneError(f"variant {variant_id} provides CLAUDE.md without claude-md right")
        if context["skills"] and "skills" not in rights:
            raise ControlPlaneError(f"variant {variant_id} provides skills without skills right")
        variants.append(
            {
                "id": variant_id,
                "label": str(item.get("label") or variant_id).strip() or variant_id,
                "rights": rights,
                "context_sources": context,
                "workspace_overlay_source": _source_path(
                    base,
                    item["workspace_overlay"],
                    label=f"variant {variant_id} workspace_overlay",
                    directory=True,
                )
                if item.get("workspace_overlay")
                else None,
            }
        )

    raw_execution = value.get("execution", {})
    if not isinstance(raw_execution, dict):
        raise ControlPlaneError("evaluation execution must be a mapping")
    _exact_fields(
        raw_execution,
        {
            "repetitions",
            "warmup_repetitions",
            "timeout_seconds",
            "order",
            "seed",
            "max_parallel_per_credential",
        },
        "execution",
    )
    repetitions = int(raw_execution.get("repetitions", 1))
    if not 1 <= repetitions <= 100:
        raise ControlPlaneError("execution repetitions must be between 1 and 100")
    warmups = int(raw_execution.get("warmup_repetitions", 0))
    if not 0 <= warmups <= 10:
        raise ControlPlaneError("execution warmup_repetitions must be between 0 and 10")
    timeout = min(max(int(raw_execution.get("timeout_seconds", 900)), 30), 1800)
    order = str(raw_execution.get("order") or "randomized").strip().lower()
    if order not in {"randomized", "sequential"}:
        raise ControlPlaneError("execution order must be randomized or sequential")
    seed = int(raw_execution.get("seed", 0))
    max_parallel = int(raw_execution.get("max_parallel_per_credential", 1))
    if max_parallel != 1:
        raise ControlPlaneError(
            "evaluation manifest v1 requires max_parallel_per_credential: 1 "
            "to prevent shared CLI-token refresh races"
        )
    execution = {
        "repetitions": repetitions,
        "warmup_repetitions": warmups,
        "timeout_seconds": timeout,
        "order": order,
        "seed": seed,
        "max_parallel_per_credential": max_parallel,
    }

    raw_evaluator = value.get("evaluator")
    if not isinstance(raw_evaluator, dict):
        raise ControlPlaneError("evaluation evaluator must be a mapping")
    _exact_fields(raw_evaluator, {"type", "config"}, "evaluator")
    evaluator_type = str(raw_evaluator.get("type") or "").strip().lower()
    if not evaluator_type:
        raise ControlPlaneError("evaluation evaluator.type must not be empty")
    evaluator_config = raw_evaluator.get("config", {})
    if not isinstance(evaluator_config, dict):
        raise ControlPlaneError("evaluation evaluator.config must be a mapping")

    raw_metrics = value.get("metrics", sorted(METRICS))
    if not isinstance(raw_metrics, list) or not all(isinstance(metric, str) for metric in raw_metrics):
        raise ControlPlaneError("evaluation metrics must be a string list")
    metrics = sorted(set(raw_metrics))
    unknown_metrics = set(metrics) - METRICS
    if unknown_metrics:
        raise ControlPlaneError(f"unknown evaluation metrics: {', '.join(sorted(unknown_metrics))}")

    return {
        "version": 1,
        "name": name,
        "source": source,
        "targets": targets,
        "scenario": scenario,
        "variants": variants,
        "execution": execution,
        "evaluator": {"type": evaluator_type, "config": evaluator_config},
        "metrics": metrics,
    }


def trial_plan(specification: dict[str, Any]) -> list[dict[str, Any]]:
    trials: list[dict[str, Any]] = []
    ordinal = 0
    for target in specification["targets"]:
        for variant in specification["variants"]:
            for repetition in range(1, specification["execution"]["warmup_repetitions"] + 1):
                ordinal += 1
                trials.append(
                    {
                        "trial_id": f"trial-{ordinal:06d}",
                        "source_agent_id": target["agent"],
                        "target_label": target["label"],
                        "billing_mode": target["billing_mode"],
                        "variant_id": variant["id"],
                        "variant_label": variant["label"],
                        "repetition": repetition,
                        "warmup": True,
                    }
                )
            for repetition in range(1, specification["execution"]["repetitions"] + 1):
                ordinal += 1
                trials.append(
                    {
                        "trial_id": f"trial-{ordinal:06d}",
                        "source_agent_id": target["agent"],
                        "target_label": target["label"],
                        "billing_mode": target["billing_mode"],
                        "variant_id": variant["id"],
                        "variant_label": variant["label"],
                        "repetition": repetition,
                        "warmup": False,
                    }
                )
    if specification["execution"]["order"] == "randomized":
        random.Random(specification["execution"]["seed"]).shuffle(trials)
    for index, trial in enumerate(trials, 1):
        trial["sequence"] = index
    return trials

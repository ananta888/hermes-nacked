"""Declarative team manifest loading and fail-closed schema normalization."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .domain import (
    ControlPlaneError,
    expand_rights,
    normalize_engine,
    normalize_identifier,
)


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ControlPlaneError(f"cannot read team manifest: {exc}") from exc
    try:
        import yaml  # type: ignore

        value = yaml.safe_load(raw)
    except ImportError:
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ControlPlaneError(
                "standard YAML needs PyYAML; otherwise use JSON syntax (valid YAML)"
            ) from exc
    except Exception as exc:
        raise ControlPlaneError(f"invalid team YAML: {exc}") from exc
    if not isinstance(value, dict):
        raise ControlPlaneError("team manifest must be a mapping")
    return normalize_team_manifest(value)


def _exact_fields(value: dict[str, Any], allowed: set[str], label: str) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise ControlPlaneError(f"unknown {label} fields: {', '.join(sorted(unknown))}")


def normalize_team_manifest(value: dict[str, Any]) -> dict[str, Any]:
    _exact_fields(
        value,
        {"version", "name", "orchestrator", "agents", "workflow", "approvals"},
        "team",
    )
    if int(value.get("version", 0)) != 1:
        raise ControlPlaneError("team manifest version must be 1")
    name = normalize_identifier(value.get("name", ""), label="team name")
    orchestrator = str(value.get("orchestrator", "hermes")).strip() or "hermes"
    raw_agents = value.get("agents")
    if not isinstance(raw_agents, list) or not raw_agents:
        raise ControlPlaneError("team agents must be a non-empty list")
    agents: list[dict[str, Any]] = []
    seen_agents: set[str] = set()
    for index, item in enumerate(raw_agents):
        if not isinstance(item, dict):
            raise ControlPlaneError(f"team agent {index} must be a mapping")
        _exact_fields(
            item,
            {"id", "engine", "role", "credential", "model", "access"},
            f"agent {index}",
        )
        agent_id = normalize_identifier(item.get("id", ""), label="agent id")
        if agent_id in seen_agents:
            raise ControlPlaneError(f"duplicate team agent: {agent_id}")
        seen_agents.add(agent_id)
        engine = normalize_engine(item.get("engine", ""))
        access = item.get("access", [])
        if not isinstance(access, list) or not all(isinstance(right, str) for right in access):
            raise ControlPlaneError(f"agent {agent_id} access must be a string list")
        credential_id = normalize_identifier(
            item.get("credential") or agent_id, label="credential id"
        )
        model = item.get("model")
        if model is not None and not isinstance(model, str):
            raise ControlPlaneError(f"agent {agent_id} model must be a string")
        agents.append(
            {
                "id": agent_id,
                "engine": engine,
                "role": str(item.get("role") or "worker"),
                "credential": credential_id,
                "model": model,
                "access": list(expand_rights(access)),
            }
        )

    raw_workflow = value.get("workflow", [])
    if not isinstance(raw_workflow, list):
        raise ControlPlaneError("team workflow must be a list")
    workflow: list[dict[str, Any]] = []
    seen_steps: set[str] = set()
    for index, item in enumerate(raw_workflow):
        if not isinstance(item, dict):
            raise ControlPlaneError(f"workflow step {index} must be a mapping")
        _exact_fields(
            item,
            {
                "id", "agent", "prompt", "needs", "timeout_seconds",
                "input_artifacts", "approval",
            },
            f"workflow step {index}",
        )
        step_id = normalize_identifier(item.get("id", ""), label="workflow step id")
        if step_id in seen_steps:
            raise ControlPlaneError(f"duplicate workflow step: {step_id}")
        seen_steps.add(step_id)
        agent_id = normalize_identifier(item.get("agent", ""), label="agent id")
        if agent_id not in seen_agents:
            raise ControlPlaneError(f"workflow step {step_id} references unknown agent {agent_id}")
        prompt = str(item.get("prompt") or "").strip()
        if not prompt:
            raise ControlPlaneError(f"workflow step {step_id} prompt must not be empty")
        needs = item.get("needs", [])
        inputs = item.get("input_artifacts", [])
        if not isinstance(needs, list) or not all(isinstance(part, str) for part in needs):
            raise ControlPlaneError(f"workflow step {step_id} needs must be a string list")
        if not isinstance(inputs, list) or not all(isinstance(part, str) for part in inputs):
            raise ControlPlaneError(
                f"workflow step {step_id} input_artifacts must be a string list"
            )
        workflow.append(
            {
                "id": step_id,
                "agent": agent_id,
                "prompt": prompt,
                "needs": [normalize_identifier(part, label="workflow dependency") for part in needs],
                "timeout_seconds": min(max(int(item.get("timeout_seconds", 900)), 30), 1800),
                "input_artifacts": list(inputs),
                "approval": bool(item.get("approval", False)),
            }
        )
    for step in workflow:
        unknown = set(step["needs"]) - seen_steps
        if unknown:
            raise ControlPlaneError(
                f"workflow step {step['id']} has unknown dependencies: {', '.join(sorted(unknown))}"
            )
        if step["id"] in step["needs"]:
            raise ControlPlaneError(f"workflow step {step['id']} depends on itself")
    _topological_order(workflow)

    approvals = value.get("approvals", {})
    if not isinstance(approvals, dict):
        raise ControlPlaneError("team approvals must be a mapping")
    return {
        "version": 1,
        "name": name,
        "orchestrator": orchestrator,
        "agents": agents,
        "workflow": workflow,
        "approvals": approvals,
    }


def _topological_order(workflow: list[dict[str, Any]]) -> list[str]:
    remaining = {step["id"]: set(step["needs"]) for step in workflow}
    ordered: list[str] = []
    while remaining:
        ready = sorted(step_id for step_id, needs in remaining.items() if not needs)
        if not ready:
            raise ControlPlaneError("workflow dependency graph contains a cycle")
        for step_id in ready:
            ordered.append(step_id)
            remaining.pop(step_id)
        for needs in remaining.values():
            needs.difference_update(ready)
    return ordered


def topological_steps(workflow: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {step["id"]: step for step in workflow}
    return [by_id[step_id] for step_id in _topological_order(workflow)]

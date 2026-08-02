"""Fail-closed feature policy for isolated coding workers."""

from __future__ import annotations

from dataclasses import dataclass


WORKERS = ("codex", "claude", "opencode")
FEATURES = frozenset(
    {"tools", "commandline", "skills", "agents-md", "claude-md"}
)
ALIASES = {
    "tool": "tools",
    "tool-use": "tools",
    "tooluse": "tools",
    "shell": "commandline",
    "terminal": "commandline",
    "agents": "agents-md",
    "agents.md": "agents-md",
    "agentsmd": "agents-md",
    "claude": "claude-md",
    "claude.md": "claude-md",
    "claudemd": "claude-md",
}


class WorkerPolicyError(ValueError):
    """The requested worker policy is invalid and must fail closed."""


@dataclass(frozen=True)
class WorkerPolicy:
    features: tuple[str, ...]
    tools: bool
    commandline: bool
    skills: bool
    agents_md: bool
    claude_md: bool


def normalize_worker(value: str) -> str:
    worker = value.strip().lower()
    if worker not in WORKERS:
        raise WorkerPolicyError(
            f"unknown worker: {worker or 'missing'} "
            "(expected codex, claude, or opencode)"
        )
    return worker


def normalize_worker_feature(value: str) -> str:
    normalized = value.strip().lower().replace("_", "-")
    return ALIASES.get(normalized, normalized)


def parse_worker_features(raw: str | None) -> WorkerPolicy:
    values: set[str] = set()
    for item in (raw or "").replace(",", " ").split():
        normalized = normalize_worker_feature(item)
        if normalized:
            values.add(normalized)

    unknown = values - FEATURES
    if unknown:
        raise WorkerPolicyError(
            "unknown worker features: " + ", ".join(sorted(unknown))
        )
    if "commandline" in values and "tools" not in values:
        raise WorkerPolicyError(
            "worker commandline requires tools (generic: tool-use); "
            "enable both explicitly"
        )

    features = tuple(sorted(values))
    return WorkerPolicy(
        features=features,
        tools="tools" in values,
        commandline="commandline" in values,
        skills="skills" in values,
        agents_md="agents-md" in values,
        claude_md="claude-md" in values,
    )

"""Validated control-plane domain objects with no infrastructure dependencies."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import re
from typing import Any, Iterable


ENGINES = ("codex", "claude", "opencode")
RIGHTS = (
    "inspect",
    "edit",
    "commandline",
    "network",
    "skills",
    "agents-md",
    "claude-md",
)
IDENTIFIER = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
ARTIFACT_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
RIGHT_ALIASES = {
    "read": ("inspect",),
    "read-only": ("inspect",),
    "write": ("edit",),
    "shell": ("commandline",),
    "terminal": ("commandline",),
    "skill": ("skills",),
    "agents": ("agents-md",),
    "agents.md": ("agents-md",),
    "agentsmd": ("agents-md",),
    "claude": ("claude-md",),
    "claude.md": ("claude-md",),
    "claudemd": ("claude-md",),
    # Compatibility vocabulary. Unlike the legacy worker switch, the generic
    # instance API makes read and write independently visible.
    "tool": ("inspect", "edit"),
    "tools": ("inspect", "edit"),
    "tool-use": ("inspect", "edit"),
    "tooluse": ("inspect", "edit"),
}


class ControlPlaneError(RuntimeError):
    """Expected fail-closed error suitable for an operator-facing message."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_identifier(value: str, *, label: str = "identifier") -> str:
    if not isinstance(value, str):
        raise ControlPlaneError(f"{label} must be a string")
    normalized = value.strip().lower().replace("_", "-")
    if not IDENTIFIER.fullmatch(normalized):
        raise ControlPlaneError(
            f"invalid {label}: {value!r}; use 1-63 lowercase letters, digits, or hyphens"
        )
    return normalized


def normalize_artifact_id(value: str) -> str:
    if not isinstance(value, str):
        raise ControlPlaneError("artifact id must be a string")
    normalized = value.strip().lower().replace(" ", "-")
    if not ARTIFACT_IDENTIFIER.fullmatch(normalized) or ".." in normalized:
        raise ControlPlaneError(
            "invalid artifact id; use 1-128 lowercase letters, digits, dots, hyphens, or underscores"
        )
    return normalized


def normalize_engine(value: str) -> str:
    normalized = str(value).strip().lower().replace("_", "-")
    aliases = {
        "codex-cli": "codex",
        "claude-cli": "claude",
        "claude-code": "claude",
        "opencode-cli": "opencode",
        "open-code": "opencode",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in ENGINES:
        raise ControlPlaneError("engine must be codex, claude, or opencode")
    return normalized


def expand_rights(values: Iterable[str]) -> tuple[str, ...]:
    expanded: set[str] = set()
    for value in values:
        normalized = str(value).strip().lower().replace("_", "-")
        aliases = RIGHT_ALIASES.get(normalized, (normalized,))
        for item in aliases:
            if item not in RIGHTS:
                raise ControlPlaneError(f"unknown agent right: {value}")
            expanded.add(item)
    return tuple(sorted(expanded))


def validate_rights(engine: str, values: Iterable[str]) -> tuple[str, ...]:
    normalized_engine = normalize_engine(engine)
    rights = set(expand_rights(values))
    if "edit" in rights and "inspect" not in rights:
        raise ControlPlaneError("edit requires inspect; grant both explicitly")
    if "commandline" in rights and not {"inspect", "network"}.issubset(rights):
        raise ControlPlaneError(
            "commandline requires inspect and network; the CLI process shares provider egress"
        )
    codex_bundle = {"inspect", "edit", "commandline"}
    if normalized_engine == "codex" and rights.intersection(codex_bundle):
        if not codex_bundle.issubset(rights) or "network" not in rights:
            raise ControlPlaneError(
                "Codex requires inspect, edit, commandline, and network together: "
                "its read-only bubblewrap sandbox cannot initialize inside the hardened "
                "worker, so only the outer Docker isolation can safely provide its shell"
            )
    return tuple(sorted(rights))


@dataclass(frozen=True)
class AgentRecord:
    agent_id: str
    engine: str
    role: str
    credential_id: str
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "AgentRecord":
        return cls(
            agent_id=normalize_identifier(value["agent_id"], label="agent id"),
            engine=normalize_engine(value["engine"]),
            role=str(value.get("role") or "worker").strip() or "worker",
            credential_id=normalize_identifier(
                value["credential_id"], label="credential id"
            ),
            created_at=str(value["created_at"]),
            updated_at=str(value["updated_at"]),
        )


@dataclass(frozen=True)
class CredentialRecord:
    credential_id: str
    engine: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "CredentialRecord":
        return cls(
            credential_id=normalize_identifier(
                value["credential_id"], label="credential id"
            ),
            engine=normalize_engine(value["engine"]),
            created_at=str(value["created_at"]),
        )

"""Fail-closed capability policy shared by the launcher and its tests."""

from __future__ import annotations

from dataclasses import dataclass


CAPABILITY_TOOLSETS: dict[str, tuple[str, ...]] = {
    "files": ("file",),
    "commandline": ("terminal",),
    "skills": ("skills",),
    "web": ("web",),
    "code": ("code_execution",),
    "planning": ("todo", "clarify"),
    # The raw MCP server name becomes a registry-backed toolset after the
    # narrowly scoped stdio server has been discovered.
    "hermesctl-mcp": ("hermesctl",),
    "codex-mcp": ("codex_worker",),
    "claude-mcp": ("claude_worker",),
    "opencode-mcp": ("opencode_worker",),
    # These capabilities alter context/mount policy but add no toolset.
    "hermesctl-direct": (),
    "codex-direct": (),
    "claude-direct": (),
    "opencode-direct": (),
    "orchestrator": (),
}

MODIFIERS = {"shell-network"}
ALIASES = {
    "tools": "files",
    "file": "files",
    "shell": "commandline",
    "terminal": "commandline",
}
VALID_CAPABILITIES = frozenset(CAPABILITY_TOOLSETS) | MODIFIERS
SANDBOX_CAPABILITIES = frozenset({"files", "commandline", "code"})
WORKERS = ("codex", "claude", "opencode")


class PolicyError(ValueError):
    """The requested policy is invalid and must not be weakened implicitly."""


@dataclass(frozen=True)
class Policy:
    capabilities: tuple[str, ...]
    toolsets: tuple[str, ...]
    needs_sandbox: bool
    sandbox_network: bool
    mount_skills: bool
    mount_control: bool
    direct_control: bool
    enable_mcp: bool
    load_orchestrator: bool
    direct_workers: tuple[str, ...]
    mcp_workers: tuple[str, ...]
    workers: tuple[str, ...]


def normalize_capability(value: str) -> str:
    normalized = value.strip().lower().replace("_", "-")
    return ALIASES.get(normalized, normalized)


def parse_capabilities(raw: str | None) -> Policy:
    values: set[str] = set()
    for item in (raw or "").replace(",", " ").split():
        normalized = normalize_capability(item)
        if normalized:
            values.add(normalized)

    unknown = values - VALID_CAPABILITIES
    if unknown:
        raise PolicyError(
            "unknown capabilities: " + ", ".join(sorted(unknown))
        )

    if "shell-network" in values and not values.intersection(
        {"commandline", "code"}
    ):
        raise PolicyError(
            "shell-network requires commandline or code; enable it separately first"
        )

    if "hermesctl-direct" in values and not {
        "commandline",
        "skills",
    }.issubset(values):
        raise PolicyError(
            "hermesctl-direct requires both commandline and skills"
        )

    if "hermesctl-mcp" in values and "skills" not in values:
        raise PolicyError("hermesctl-mcp requires skills")

    for worker in WORKERS:
        direct = f"{worker}-direct"
        mcp = f"{worker}-mcp"
        if direct in values and not {"commandline", "skills"}.issubset(values):
            raise PolicyError(f"{direct} requires both commandline and skills")
        if mcp in values and "skills" not in values:
            raise PolicyError(f"{mcp} requires skills")

    toolsets: list[str] = []
    for capability in sorted(values):
        for toolset in CAPABILITY_TOOLSETS.get(capability, ()):
            if toolset not in toolsets:
                toolsets.append(toolset)

    direct_workers = tuple(
        worker for worker in WORKERS if f"{worker}-direct" in values
    )
    mcp_workers = tuple(
        worker for worker in WORKERS if f"{worker}-mcp" in values
    )
    workers = tuple(
        worker for worker in WORKERS if worker in {*direct_workers, *mcp_workers}
    )

    return Policy(
        capabilities=tuple(sorted(values)),
        toolsets=tuple(toolsets),
        needs_sandbox=bool(values.intersection(SANDBOX_CAPABILITIES)),
        sandbox_network="shell-network" in values,
        mount_skills="skills" in values,
        mount_control=bool(
            values.intersection({"hermesctl-direct", "hermesctl-mcp"})
        ),
        direct_control="hermesctl-direct" in values,
        enable_mcp=("hermesctl-mcp" in values or bool(mcp_workers)),
        load_orchestrator="orchestrator" in values,
        direct_workers=direct_workers,
        mcp_workers=mcp_workers,
        workers=workers,
    )

"""Disposable Docker-agent environment for one isolated evaluation trial."""

from __future__ import annotations

import hashlib

from ..agents import AgentRegistry
from ..domain import ControlPlaneError, validate_rights
from ..runtime import DockerAgentRuntime
from .snapshots import SnapshotRepository


class DockerTrialEnvironment:
    def __init__(
        self,
        agents: AgentRegistry,
        runtime: DockerAgentRuntime,
        snapshots: SnapshotRepository,
    ):
        self.agents = agents
        self.runtime = runtime
        self.snapshots = snapshots

    @staticmethod
    def ephemeral_id(experiment_id: str, trial_id: str) -> str:
        digest = hashlib.sha256(f"{experiment_id}:{trial_id}".encode("utf-8")).hexdigest()[:20]
        return f"trial-{digest}"

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
        source = self.agents.get(source_agent_id)
        validated_rights = validate_rights(source.engine, rights)
        ephemeral_id = self.ephemeral_id(experiment_id, trial_id)
        try:
            self.agents.get(ephemeral_id)
        except ControlPlaneError:
            pass
        else:
            raise ControlPlaneError(
                f"stale evaluation agent exists: {ephemeral_id}; resume cleanup first"
            )
        record = self.agents.create(
            ephemeral_id,
            source.engine,
            source.credential_id,
            role=f"evaluation trial for {source.agent_id}",
            allow_shared_credential=True,
        )
        try:
            self.agents.mark_operator_only(record.agent_id)
            self.agents.set_model(record.agent_id, self.agents.model(source.agent_id))
            self.snapshots.restore(
                workspace_artifact,
                self.agents.instance_path(record.agent_id) / "workspace",
            )
            self.snapshots.restore(
                workspace_overlay_artifact,
                self.agents.instance_path(record.agent_id) / "workspace",
                replace=False,
            )
            self.snapshots.restore(
                context_artifact,
                self.agents.instance_path(record.agent_id) / "context",
            )
            self.agents.set_rights(record.agent_id, validated_rights)
            return record.agent_id
        except Exception:
            self.agents.set_rights(record.agent_id, ())
            self.agents.delete(record.agent_id)
            raise

    def cleanup(self, agent_id: str) -> None:
        try:
            self.agents.get(agent_id)
        except ControlPlaneError:
            return
        self.agents.set_rights(agent_id, ())
        try:
            self.runtime.stop(agent_id)
        except ControlPlaneError as exc:
            raise ControlPlaneError(
                f"temporary agent {agent_id} was fail-closed but could not be removed: {exc}"
            ) from exc
        self.agents.delete(agent_id)

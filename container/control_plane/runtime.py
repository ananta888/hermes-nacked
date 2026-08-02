"""Docker Compose adapter for one isolated agent instance."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
from typing import Any

from .agents import AgentRegistry
from .credentials import CredentialBroker
from .domain import AgentRecord, ControlPlaneError


class DockerAgentRuntime:
    def __init__(self, root: Path, agents: AgentRegistry, credentials: CredentialBroker):
        self.root = root.resolve()
        self.agents = agents
        self.credentials = credentials
        self.compose_file = self.root / "compose.agent.yaml"

    def environment(self, record: AgentRecord) -> dict[str, str]:
        instance = self.agents.instance_path(record.agent_id)
        credential_home = self.credentials.home_path(record.credential_id)
        versions = {
            "codex": os.environ.get("CODEX_CLI_VERSION", "0.146.0"),
            "claude": os.environ.get("CLAUDE_CLI_VERSION", "2.1.220"),
            "opencode": os.environ.get("OPENCODE_CLI_VERSION", "1.18.11"),
        }
        return {
            **os.environ,
            "HERMES_HOST_ROOT": str(self.root),
            "HERMES_UID": str(os.getuid()),
            "HERMES_GID": str(os.getgid()),
            "AGENT_ID": record.agent_id,
            "AGENT_ENGINE": record.engine,
            "AGENT_CLI_VERSION": versions[record.engine],
            "AGENT_STATE": str(instance / "state"),
            "AGENT_WORKSPACE": str(instance / "workspace"),
            "AGENT_CONTEXT": str(instance / "context"),
            "AGENT_SOCKET": str(self.agents.socket_path(record.agent_id)),
            # Workers need only their rights file. The redacted registry is the
            # canonical model-facing policy mount; private broker metadata stays out.
            "AGENT_CONTROL": str(self.agents.public_path(record.agent_id)),
            "AGENT_CREDENTIAL_HOME": str(credential_home),
            "COMPOSE_IGNORE_ORPHANS": "true",
        }

    def compose_command(self, record: AgentRecord) -> list[str]:
        if not self.compose_file.is_file():
            raise ControlPlaneError("compose.agent.yaml is unavailable")
        return [
            "docker",
            "compose",
            "--project-directory",
            str(self.root),
            "--project-name",
            f"hermes-agent-{record.agent_id}",
            "-f",
            str(self.compose_file),
        ]

    def _run(
        self,
        record: AgentRecord,
        arguments: list[str],
        *,
        timeout: int | None = None,
        capture: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        self.credentials.assert_compatible(record.credential_id, record.engine)
        command = [*self.compose_command(record), *arguments]
        try:
            result = subprocess.run(
                command,
                cwd=self.root,
                env=self.environment(record),
                stdin=None if not capture else subprocess.DEVNULL,
                capture_output=capture,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ControlPlaneError(f"Docker agent command failed: {exc}") from exc
        if result.returncode != 0:
            details = (result.stderr or result.stdout or "").strip()
            raise ControlPlaneError(
                f"Docker agent command exited {result.returncode}: {details}"
            )
        return result

    def build(self, agent_id: str) -> None:
        record = self.agents.get(agent_id)
        self._run(record, ["build", "agent-worker"], timeout=1800)

    def start(self, agent_id: str) -> None:
        record = self.agents.get(agent_id)
        self._run(
            record,
            ["up", "-d", "--wait", "--wait-timeout", "60", "agent-worker"],
            timeout=120,
        )

    def stop(self, agent_id: str) -> None:
        record = self.agents.get(agent_id)
        self._run(record, ["down", "--remove-orphans"], timeout=120)

    def login(self, agent_id: str) -> None:
        record = self.agents.get(agent_id)
        prefix = ["run", "--rm"]
        if record.engine == "codex":
            command = [
                *prefix,
                "--entrypoint",
                "codex",
                "agent-worker",
                "-c",
                'cli_auth_credentials_store="file"',
                "login",
                "--device-auth",
            ]
        elif record.engine == "claude":
            command = [
                *prefix,
                "--entrypoint",
                "claude",
                "agent-worker",
                "auth",
                "login",
                "--claudeai",
            ]
        else:
            command = [
                *prefix,
                "--entrypoint",
                "opencode",
                "agent-worker",
                "auth",
                "login",
            ]
        self._run(record, command)

    def logout(self, agent_id: str) -> None:
        record = self.agents.get(agent_id)
        prefix = ["run", "--rm"]
        if record.engine == "codex":
            command = [*prefix, "--entrypoint", "codex", "agent-worker", "logout"]
        elif record.engine == "claude":
            command = [
                *prefix,
                "--entrypoint",
                "claude",
                "agent-worker",
                "auth",
                "logout",
            ]
        else:
            command = [
                *prefix,
                "--entrypoint",
                "opencode",
                "agent-worker",
                "auth",
                "logout",
            ]
        self._run(record, command)

    def clone_workspace(self, agent_id: str, source: str, branch: str | None = None) -> None:
        record = self.agents.get(agent_id)
        workspace = self.agents.instance_path(record.agent_id) / "workspace"
        entries = [path for path in workspace.iterdir() if path.name != ".gitkeep"]
        if entries:
            raise ControlPlaneError("agent workspace must be empty before clone")
        (workspace / ".gitkeep").unlink(missing_ok=True)
        command = ["git", "clone"]
        if branch:
            command.extend(["--branch", branch])
        command.extend([source, str(workspace)])
        try:
            result = subprocess.run(
                command,
                cwd=self.root,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except OSError as exc:
            raise ControlPlaneError(f"git clone failed: {exc}") from exc
        if result.returncode != 0:
            raise ControlPlaneError(f"git clone exited {result.returncode}")

    def git_patch(self, agent_id: str) -> bytes:
        record = self.agents.get(agent_id)
        workspace = self.agents.instance_path(record.agent_id) / "workspace"
        result = subprocess.run(
            ["git", "-C", str(workspace), "diff", "--binary", "--no-ext-diff"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise ControlPlaneError(
                "cannot export patch: " + result.stderr.decode("utf-8", errors="replace").strip()
            )
        return result.stdout

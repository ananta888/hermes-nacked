"""Agent registry and per-instance policy/state management."""

from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import shutil

from .credentials import CredentialBroker
from .domain import (
    AgentRecord,
    ControlPlaneError,
    expand_rights,
    normalize_engine,
    normalize_identifier,
    utc_now,
    validate_rights,
)
from .storage import AtomicStore


class AgentRegistry:
    def __init__(self, root: Path, store: AtomicStore, credentials: CredentialBroker):
        self.root = root.resolve()
        self.store = store
        self.credentials = credentials
        self.control_root = self.root / "runtime" / "control" / "agents"
        self.public_root = self.root / "runtime" / "registry" / "agents"
        self.instance_root = self.root / "runtime" / "agents"
        self.socket_root = self.root / "runtime" / "sockets" / "agents"

    def control_path(self, agent_id: str) -> Path:
        return self.control_root / normalize_identifier(agent_id, label="agent id")

    def instance_path(self, agent_id: str) -> Path:
        return self.instance_root / normalize_identifier(agent_id, label="agent id")

    def socket_path(self, agent_id: str) -> Path:
        return self.socket_root / normalize_identifier(agent_id, label="agent id")

    def public_path(self, agent_id: str) -> Path:
        return self.public_root / normalize_identifier(agent_id, label="agent id")

    def _write_record(self, record: AgentRecord) -> None:
        self.store.write_json(
            self.control_path(record.agent_id) / "agent.json", record.to_dict()
        )
        public = {
            **record.to_dict(),
            "credential_id": "redacted",
            "credentials_exposed": False,
        }
        self.store.write_json(self.public_path(record.agent_id) / "agent.json", public)

    def create(
        self,
        agent_id: str,
        engine: str,
        credential_id: str,
        *,
        role: str = "worker",
        allow_shared_credential: bool = False,
    ) -> AgentRecord:
        normalized_id = normalize_identifier(agent_id, label="agent id")
        normalized_engine = normalize_engine(engine)
        normalized_credential = normalize_identifier(
            credential_id, label="credential id"
        )
        normalized_role = str(role).strip() or "worker"
        if len(normalized_role) > 200 or any(ord(char) < 32 for char in normalized_role):
            raise ControlPlaneError("role must be printable and at most 200 characters")
        self.credentials.assert_compatible(normalized_credential, normalized_engine)

        def operation() -> AgentRecord:
            record_path = self.control_path(normalized_id) / "agent.json"
            if record_path.exists():
                raise ControlPlaneError(f"agent already exists: {normalized_id}")
            consumers = self.agents_using_credential(normalized_credential)
            if consumers and not allow_shared_credential:
                raise ControlPlaneError(
                    f"credential {normalized_credential} is already assigned to "
                    f"{', '.join(consumers)}; use an isolated credential or explicitly allow sharing"
                )
            now = utc_now()
            record = AgentRecord(
                normalized_id,
                normalized_engine,
                normalized_role,
                normalized_credential,
                now,
                now,
            )
            control = self.store.ensure_directory(self.control_path(normalized_id), 0o700)
            public = self.store.ensure_directory(self.public_path(normalized_id), 0o700)
            instance = self.store.ensure_directory(self.instance_path(normalized_id), 0o700)
            for path, mode in (
                (instance / "state", 0o700),
                (instance / "workspace", 0o750),
                (self.socket_path(normalized_id), 0o700),
            ):
                self.store.ensure_directory(path, mode)
            (instance / "workspace" / ".gitkeep").touch(exist_ok=True)
            os.chmod(instance / "workspace" / ".gitkeep", 0o600)
            self._seed_context(instance / "context", normalized_engine)
            self.store.write_text(control / "capabilities", "")
            self.store.write_text(public / "capabilities", "")
            self._write_record(record)
            return record

        return self.store.locked("agents", operation)

    def _seed_context(self, destination: Path, engine: str) -> None:
        source = self.root / "worker-context" / engine
        if not source.is_dir():
            raise ControlPlaneError(f"worker context template is unavailable: {engine}")
        for current_root, directories, files in os.walk(source, followlinks=False):
            for name in [*directories, *files]:
                candidate = Path(current_root) / name
                if candidate.is_symlink():
                    raise ControlPlaneError(
                        f"worker context template contains a symlink: {candidate.relative_to(source)}"
                    )
        if destination.exists():
            raise ControlPlaneError(f"agent context already exists: {destination}")
        shutil.copytree(source, destination, symlinks=False)
        for current_root, directories, files in os.walk(destination):
            os.chmod(current_root, 0o750)
            for directory in directories:
                os.chmod(Path(current_root) / directory, 0o750)
            for filename in files:
                os.chmod(Path(current_root) / filename, 0o640)

    def get(self, agent_id: str) -> AgentRecord:
        normalized = normalize_identifier(agent_id, label="agent id")
        value = self.store.read_json(self.control_path(normalized) / "agent.json")
        return AgentRecord.from_dict(value)

    def is_operator_only(self, agent_id: str) -> bool:
        normalized = normalize_identifier(agent_id, label="agent id")
        return (self.control_path(normalized) / ".operator-only").is_file() or (
            self.public_path(normalized) / ".operator-only"
        ).is_file()

    def mark_operator_only(self, agent_id: str) -> None:
        record = self.get(agent_id)
        self.store.write_text(
            self.control_path(record.agent_id) / ".operator-only", "evaluation-trial\n"
        )
        self.store.write_text(
            self.public_path(record.agent_id) / ".operator-only", "evaluation-trial\n"
        )

    def list(self, *, include_operator_only: bool = True) -> list[AgentRecord]:
        if not self.control_root.exists():
            return []
        records: list[AgentRecord] = []
        for directory in sorted(self.control_root.iterdir()):
            if not directory.is_dir() or directory.is_symlink():
                continue
            record_path = directory / "agent.json"
            if record_path.is_file():
                record = AgentRecord.from_dict(self.store.read_json(record_path))
                if include_operator_only or not self.is_operator_only(record.agent_id):
                    records.append(record)
        return records

    def agents_using_credential(self, credential_id: str) -> list[str]:
        normalized = normalize_identifier(credential_id, label="credential id")
        return sorted(
            record.agent_id for record in self.list() if record.credential_id == normalized
        )

    def rights(self, agent_id: str) -> tuple[str, ...]:
        record = self.get(agent_id)
        path = self.control_path(record.agent_id) / "capabilities"
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ControlPlaneError(f"agent rights are unavailable: {exc}") from exc
        return validate_rights(record.engine, raw.replace(",", " ").split())

    def set_rights(self, agent_id: str, values: tuple[str, ...]) -> tuple[str, ...]:
        record = self.get(agent_id)
        rights = validate_rights(record.engine, values)
        content = "".join(f"{right}\n" for right in rights)
        self.store.write_text(self.control_path(record.agent_id) / "capabilities", content)
        self.store.write_text(self.public_path(record.agent_id) / "capabilities", content)
        return rights

    def change_rights(
        self, agent_id: str, action: str, requested: tuple[str, ...]
    ) -> tuple[str, ...]:
        current = set(self.rights(agent_id))
        requested_set = set(expand_rights(requested))
        if action == "grant":
            result = current | requested_set
        elif action == "revoke":
            result = current - requested_set
        else:
            raise ControlPlaneError(f"unsupported rights action: {action}")
        return self.set_rights(agent_id, tuple(result))

    def set_model(self, agent_id: str, model: str | None) -> str | None:
        record = self.get(agent_id)
        value = "" if model is None else str(model).strip()
        if value and (len(value) > 256 or not all(c.isalnum() or c in "_.:/@+-" for c in value)):
            raise ControlPlaneError("model id contains unsupported characters")
        self.store.write_text(self.instance_path(record.agent_id) / "state" / ".worker-model", value + ("\n" if value else ""))
        return value or None

    def model(self, agent_id: str) -> str | None:
        record = self.get(agent_id)
        path = self.instance_path(record.agent_id) / "state" / ".worker-model"
        try:
            return path.read_text(encoding="utf-8").strip() or None
        except FileNotFoundError:
            return None

    def assign_credential(
        self, agent_id: str, credential_id: str, *, allow_shared: bool = False
    ) -> AgentRecord:
        record = self.get(agent_id)
        credential = self.credentials.assert_compatible(credential_id, record.engine)
        consumers = [
            item for item in self.agents_using_credential(credential.credential_id)
            if item != record.agent_id
        ]
        if consumers and not allow_shared:
            raise ControlPlaneError(
                f"credential {credential.credential_id} is assigned to {', '.join(consumers)}"
            )
        updated = replace(
            record,
            credential_id=credential.credential_id,
            updated_at=utc_now(),
        )
        self._write_record(updated)
        return updated

    def set_role(self, agent_id: str, role: str) -> AgentRecord:
        record = self.get(agent_id)
        normalized_role = str(role).strip() or "worker"
        if len(normalized_role) > 200 or any(ord(char) < 32 for char in normalized_role):
            raise ControlPlaneError("role must be printable and at most 200 characters")
        updated = replace(record, role=normalized_role, updated_at=utc_now())
        self._write_record(updated)
        return updated

    def public_status(self, agent_id: str) -> dict:
        record = self.get(agent_id)
        return {
            **record.to_dict(),
            "rights": self.rights(record.agent_id),
            "model": self.model(record.agent_id),
            "state": str(self.instance_path(record.agent_id) / "state"),
            "workspace": str(self.instance_path(record.agent_id) / "workspace"),
            "context": str(self.instance_path(record.agent_id) / "context"),
            "socket_ready": (self.socket_path(record.agent_id) / "worker.sock").is_socket(),
            "credential": {
                "credential_id": record.credential_id,
                "engine": record.engine,
                "secret_contents_exposed": False,
            },
        }

    def delete(self, agent_id: str) -> AgentRecord:
        record = self.get(agent_id)

        def operation() -> AgentRecord:
            # Every target derives from the validated id; caller paths are never accepted.
            for target in (
                self.control_path(record.agent_id),
                self.public_path(record.agent_id),
                self.instance_path(record.agent_id),
                self.socket_path(record.agent_id),
            ):
                if target.exists():
                    shutil.rmtree(target)
            return record

        return self.store.locked("agents", operation)

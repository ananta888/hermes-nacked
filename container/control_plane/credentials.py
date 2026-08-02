"""Credential broker metadata and isolated CLI-home lifecycle."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
from typing import Iterable

from .domain import (
    ControlPlaneError,
    CredentialRecord,
    normalize_engine,
    normalize_identifier,
    utc_now,
)
from .storage import AtomicStore


class CredentialBroker:
    """Broker credential homes without reading or returning their secret files."""

    def __init__(self, root: Path, store: AtomicStore):
        self.root = root.resolve()
        self.store = store
        self.metadata_root = self.root / "runtime" / "control" / "credentials"
        self.secret_root = self.root / "runtime" / "credentials"

    def _metadata_path(self, credential_id: str) -> Path:
        return self.metadata_root / f"{credential_id}.json"

    def home_path(self, credential_id: str) -> Path:
        normalized = normalize_identifier(credential_id, label="credential id")
        return self.secret_root / normalized / "home"

    def create(self, credential_id: str, engine: str) -> CredentialRecord:
        normalized_id = normalize_identifier(credential_id, label="credential id")
        normalized_engine = normalize_engine(engine)

        def operation() -> CredentialRecord:
            if self._metadata_path(normalized_id).exists():
                existing = self.get(normalized_id)
                if existing.engine != normalized_engine:
                    raise ControlPlaneError(
                        f"credential {normalized_id} already belongs to {existing.engine}"
                    )
                return existing
            created = CredentialRecord(normalized_id, normalized_engine, utc_now())
            home = self.store.ensure_directory(self.home_path(normalized_id), 0o700)
            self._initialize_home(home, normalized_engine)
            self.store.write_json(self._metadata_path(normalized_id), created.to_dict())
            return created

        return self.store.locked("credentials", operation)

    def _initialize_home(self, home: Path, engine: str) -> None:
        paths = {
            "codex": (".codex",),
            "claude": (".claude",),
            "opencode": (
                ".local/share/opencode",
                ".config/opencode",
                ".cache/opencode",
            ),
        }[engine]
        for relative in paths:
            current = home / relative
            current.mkdir(parents=True, exist_ok=True)
        for current_root, directories, _files in os.walk(home):
            os.chmod(current_root, 0o700)
            for directory in directories:
                os.chmod(Path(current_root) / directory, 0o700)

    def get(self, credential_id: str) -> CredentialRecord:
        normalized = normalize_identifier(credential_id, label="credential id")
        return CredentialRecord.from_dict(self.store.read_json(self._metadata_path(normalized)))

    def list(self) -> list[CredentialRecord]:
        if not self.metadata_root.exists():
            return []
        records: list[CredentialRecord] = []
        for path in sorted(self.metadata_root.glob("*.json")):
            records.append(CredentialRecord.from_dict(self.store.read_json(path)))
        return records

    def assert_compatible(self, credential_id: str, engine: str) -> CredentialRecord:
        record = self.get(credential_id)
        normalized_engine = normalize_engine(engine)
        if record.engine != normalized_engine:
            raise ControlPlaneError(
                f"credential {record.credential_id} is for {record.engine}, not {normalized_engine}"
            )
        home = self.home_path(record.credential_id)
        if not home.is_dir() or home.is_symlink():
            raise ControlPlaneError(f"credential home is unavailable: {record.credential_id}")
        return record

    def delete(self, credential_id: str, *, used_by: Iterable[str] = ()) -> None:
        normalized = normalize_identifier(credential_id, label="credential id")
        consumers = tuple(sorted(set(used_by)))
        if consumers:
            raise ControlPlaneError(
                f"credential {normalized} is still assigned to: {', '.join(consumers)}"
            )

        def operation() -> None:
            self.get(normalized)
            secret_directory = self.secret_root / normalized
            if secret_directory.exists():
                shutil.rmtree(secret_directory)
            self._metadata_path(normalized).unlink()

        self.store.locked("credentials", operation)

    def public_status(self, credential_id: str, *, used_by: Iterable[str] = ()) -> dict:
        record = self.get(credential_id)
        return {
            **record.to_dict(),
            "assigned_agents": tuple(sorted(set(used_by))),
            "home_present": self.home_path(record.credential_id).is_dir(),
            "secret_contents_exposed": False,
        }

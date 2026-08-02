"""Content-addressed team handoff artifacts without shared writable workspaces."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
from typing import Any

from .domain import ControlPlaneError, normalize_artifact_id, utc_now
from .storage import AtomicStore


MAX_ARTIFACT_BYTES = 32 * 1024 * 1024
MAX_MODEL_ARTIFACT_BYTES = 1024 * 1024


class ArtifactRepository:
    def __init__(self, root: Path, store: AtomicStore):
        self.root = root.resolve()
        self.store = store
        self.artifact_root = self.root / "runtime" / "artifacts"

    def _directory(self, artifact_id: str) -> Path:
        return self.artifact_root / normalize_artifact_id(artifact_id)

    def put_bytes(
        self,
        artifact_id: str,
        content: bytes,
        *,
        media_type: str = "application/octet-stream",
        producer: str = "operator",
        overwrite: bool = False,
    ) -> dict[str, Any]:
        normalized = normalize_artifact_id(artifact_id)
        if len(content) > MAX_ARTIFACT_BYTES:
            raise ControlPlaneError("artifact exceeds 32 MiB")

        def operation() -> dict[str, Any]:
            directory = self._directory(normalized)
            metadata_path = directory / "metadata.json"
            if metadata_path.exists() and not overwrite:
                raise ControlPlaneError(f"artifact already exists: {normalized}")
            self.store.ensure_directory(directory, 0o700)
            digest = hashlib.sha256(content).hexdigest()
            metadata = {
                "artifact_id": normalized,
                "created_at": utc_now(),
                "media_type": str(media_type),
                "producer": str(producer),
                "size": len(content),
                "sha256": digest,
            }
            temporary_source = directory / ".content.pending"
            temporary_source.write_bytes(content)
            temporary_source.chmod(0o600)
            temporary_source.replace(directory / "content")
            self.store.write_json(metadata_path, metadata)
            return metadata

        return self.store.locked(f"artifact-{normalized}", operation)

    def put_file(
        self, artifact_id: str, source: Path, *, media_type: str, producer: str
    ) -> dict[str, Any]:
        resolved = source.resolve()
        if not resolved.is_file() or resolved.is_symlink():
            raise ControlPlaneError(f"artifact source is not a regular file: {source}")
        if resolved.stat().st_size > MAX_ARTIFACT_BYTES:
            raise ControlPlaneError("artifact exceeds 32 MiB")
        return self.put_bytes(
            artifact_id,
            resolved.read_bytes(),
            media_type=media_type,
            producer=producer,
        )

    def metadata(self, artifact_id: str) -> dict[str, Any]:
        normalized = normalize_artifact_id(artifact_id)
        return self.store.read_json(self._directory(normalized) / "metadata.json")

    def list(self) -> list[dict[str, Any]]:
        if not self.artifact_root.exists():
            return []
        result = []
        for directory in sorted(self.artifact_root.iterdir()):
            if directory.is_dir() and not directory.is_symlink():
                metadata = directory / "metadata.json"
                if metadata.is_file():
                    result.append(self.store.read_json(metadata))
        return result

    def read_bytes(self, artifact_id: str, *, model_safe: bool = False) -> bytes:
        metadata = self.metadata(artifact_id)
        limit = MAX_MODEL_ARTIFACT_BYTES if model_safe else MAX_ARTIFACT_BYTES
        if int(metadata["size"]) > limit:
            raise ControlPlaneError(
                f"artifact exceeds {'1 MiB model' if model_safe else '32 MiB'} read limit"
            )
        content_path = self._directory(metadata["artifact_id"]) / "content"
        if not content_path.is_file() or content_path.is_symlink():
            raise ControlPlaneError("artifact content is unavailable")
        content = content_path.read_bytes()
        if hashlib.sha256(content).hexdigest() != metadata["sha256"]:
            raise ControlPlaneError("artifact checksum mismatch")
        return content

    def export(self, artifact_id: str, destination: Path) -> dict[str, Any]:
        metadata = self.metadata(artifact_id)
        destination = destination.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.read_bytes(artifact_id))
        return metadata

    def model_view(self, artifact_id: str) -> dict[str, Any]:
        metadata = self.metadata(artifact_id)
        content = self.read_bytes(artifact_id, model_safe=True)
        media_type = str(metadata.get("media_type", ""))
        if not (
            media_type.startswith("text/")
            or media_type in {"application/json", "application/x-git-patch"}
        ):
            raise ControlPlaneError("model artifact access is limited to text, JSON, and patches")
        return {**metadata, "content": content.decode("utf-8", errors="replace")}

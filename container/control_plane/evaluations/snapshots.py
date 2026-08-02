"""Immutable, size-bounded workspace and context snapshots."""

from __future__ import annotations

from io import BytesIO
import os
from pathlib import Path, PurePosixPath
import shutil
import tarfile
from typing import Any, Iterable

from ..artifacts import ArtifactRepository, MAX_ARTIFACT_BYTES
from ..domain import ControlPlaneError, normalize_identifier
from ..storage import AtomicStore


MAX_SNAPSHOT_FILES = 10_000


def _safe_source_files(source: Path) -> list[Path]:
    if not source.is_dir() or source.is_symlink():
        raise ControlPlaneError(f"snapshot source is not a non-symlink directory: {source}")
    files: list[Path] = []
    total = 0
    for current, directories, filenames in os.walk(source, followlinks=False):
        current_path = Path(current)
        for name in [*directories, *filenames]:
            candidate = current_path / name
            if candidate.is_symlink():
                raise ControlPlaneError(
                    f"snapshot source contains a symlink: {candidate.relative_to(source)}"
                )
        for filename in filenames:
            candidate = current_path / filename
            if not candidate.is_file():
                raise ControlPlaneError(
                    f"snapshot source contains a non-regular file: {candidate.relative_to(source)}"
                )
            total += candidate.stat().st_size
            if total > MAX_ARTIFACT_BYTES:
                raise ControlPlaneError("snapshot source exceeds 32 MiB")
            files.append(candidate)
            if len(files) > MAX_SNAPSHOT_FILES:
                raise ControlPlaneError("snapshot source exceeds 10000 files")
    return sorted(files)


class SnapshotRepository:
    def __init__(self, root: Path, store: AtomicStore, artifacts: ArtifactRepository):
        self.root = root.resolve()
        self.store = store
        self.artifacts = artifacts

    @staticmethod
    def _write_member(archive: tarfile.TarFile, source: Path, archive_name: str) -> None:
        content = source.read_bytes()
        info = tarfile.TarInfo(archive_name)
        info.size = len(content)
        info.mode = 0o755 if os.access(source, os.X_OK) else 0o644
        info.mtime = 0
        info.uid = 0
        info.gid = 0
        info.uname = ""
        info.gname = ""
        archive.addfile(info, BytesIO(content))

    def workspace_snapshot(self, experiment_id: str, source: str | None) -> str | None:
        if not source:
            return None
        return self.directory_snapshot(
            f"{experiment_id}-workspace",
            source,
            media_type="application/vnd.hermes.workspace+tar",
        )

    def directory_snapshot(
        self, artifact_id: str, source: str, *, media_type: str
    ) -> str:
        source_path = Path(source).resolve()
        buffer = BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            for file_path in _safe_source_files(source_path):
                relative = file_path.relative_to(source_path).as_posix()
                self._write_member(archive, file_path, relative)
        self.artifacts.put_bytes(
            artifact_id,
            buffer.getvalue(),
            media_type=media_type,
            producer="evaluation-snapshot",
        )
        return artifact_id

    def context_snapshot(
        self, experiment_id: str, variant_id: str, sources: dict[str, Any]
    ) -> str | None:
        entries: list[tuple[Path, str]] = []
        for field, destination in (("agents_md", "AGENTS.md"), ("claude_md", "CLAUDE.md")):
            if sources.get(field):
                entries.append((Path(sources[field]).resolve(), destination))
        for raw_skill in sources.get("skills", []):
            skill_source = Path(raw_skill).resolve()
            if skill_source.is_file() and not skill_source.is_symlink():
                if skill_source.name != "SKILL.md":
                    raise ControlPlaneError(f"skill file must be named SKILL.md: {skill_source}")
                skill_name = normalize_identifier(skill_source.parent.name, label="skill name")
                entries.append((skill_source, f"skills/{skill_name}/SKILL.md"))
            elif skill_source.is_dir() and not skill_source.is_symlink():
                if not (skill_source / "SKILL.md").is_file():
                    raise ControlPlaneError(f"skill directory has no SKILL.md: {skill_source}")
                skill_name = normalize_identifier(skill_source.name, label="skill name")
                for file_path in _safe_source_files(skill_source):
                    relative = file_path.relative_to(skill_source).as_posix()
                    entries.append((file_path, f"skills/{skill_name}/{relative}"))
            else:
                raise ControlPlaneError(f"skill source is unavailable: {skill_source}")
        if not entries:
            return None
        buffer = BytesIO()
        total = 0
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            for source, destination in sorted(entries, key=lambda item: item[1]):
                if not source.is_file() or source.is_symlink():
                    raise ControlPlaneError(f"context source is not a regular file: {source}")
                total += source.stat().st_size
                if total > MAX_ARTIFACT_BYTES:
                    raise ControlPlaneError("context snapshot exceeds 32 MiB")
                self._write_member(archive, source, destination)
        artifact_id = f"{experiment_id}-context-{variant_id}"
        self.artifacts.put_bytes(
            artifact_id,
            buffer.getvalue(),
            media_type="application/vnd.hermes.context+tar",
            producer="evaluation-snapshot",
        )
        return artifact_id

    def restore(
        self, artifact_id: str | None, destination: Path, *, replace: bool = True
    ) -> None:
        destination = destination.resolve()
        if destination != self.root and self.root not in destination.parents:
            raise ControlPlaneError("snapshot destination escapes repository root")
        if replace and destination.exists():
            shutil.rmtree(destination)
        self.store.ensure_directory(destination, 0o750)
        if not artifact_id:
            return
        content = self.artifacts.read_bytes(artifact_id)
        total = 0
        count = 0
        with tarfile.open(fileobj=BytesIO(content), mode="r:") as archive:
            for member in archive.getmembers():
                count += 1
                if count > MAX_SNAPSHOT_FILES:
                    raise ControlPlaneError("snapshot archive exceeds 10000 members")
                name = PurePosixPath(member.name)
                if name.is_absolute() or ".." in name.parts or not name.parts:
                    raise ControlPlaneError("snapshot archive contains an unsafe path")
                if not member.isfile():
                    raise ControlPlaneError("snapshot archive contains a non-regular entry")
                total += member.size
                if total > MAX_ARTIFACT_BYTES:
                    raise ControlPlaneError("snapshot archive exceeds 32 MiB")
                target = destination.joinpath(*name.parts)
                self.store.ensure_directory(target.parent, 0o750)
                source = archive.extractfile(member)
                if source is None:
                    raise ControlPlaneError("snapshot archive entry is unreadable")
                target.write_bytes(source.read())
                os.chmod(target, member.mode & 0o755 or 0o640)

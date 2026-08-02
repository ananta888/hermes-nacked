"""Small atomic storage adapter used by all control-plane repositories."""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Callable, TypeVar

from .domain import ControlPlaneError


T = TypeVar("T")


class AtomicStore:
    """Persist UTF-8/JSON data atomically beneath one explicitly scoped root."""

    def __init__(self, root: Path):
        self.root = root.resolve()

    def ensure_directory(self, path: Path, mode: int = 0o700) -> Path:
        resolved = path.resolve()
        if resolved != self.root and self.root not in resolved.parents:
            raise ControlPlaneError(f"path escapes control-plane root: {path}")
        resolved.mkdir(parents=True, exist_ok=True)
        os.chmod(resolved, mode)
        return resolved

    def read_json(self, path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ControlPlaneError(f"record not found: {path.name}") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise ControlPlaneError(f"invalid record {path}: {exc}") from exc
        if not isinstance(value, dict):
            raise ControlPlaneError(f"record is not an object: {path}")
        return value

    def write_json(self, path: Path, value: dict[str, Any], mode: int = 0o600) -> None:
        payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        self.write_text(path, payload, mode=mode)

    def write_text(self, path: Path, value: str, mode: int = 0o600) -> None:
        parent = self.ensure_directory(path.parent)
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(value)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, mode)
            os.replace(temporary, path)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def locked(self, name: str, operation: Callable[[], T]) -> T:
        lock_root = self.ensure_directory(self.root / "runtime" / "locks")
        lock_path = lock_root / f"{name}.lock"
        with lock_path.open("a+", encoding="utf-8") as handle:
            os.chmod(lock_path, 0o600)
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                return operation()
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

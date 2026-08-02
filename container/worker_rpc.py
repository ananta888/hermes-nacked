"""Unix-socket client shared by the worker CLI and MCP adapters."""

from __future__ import annotations

import json
import os
import socket
import re
from typing import Any


WORKERS = frozenset({"codex", "claude", "opencode"})
MAX_RESPONSE_BYTES = 5 * 1024 * 1024
AGENT_ID = re.compile(r"^[a-z][a-z0-9-]{0,62}$")


def socket_path(worker: str) -> str:
    normalized = worker.strip().lower()
    if normalized not in WORKERS:
        raise ValueError(f"unknown worker: {worker}")
    explicit = os.environ.get(f"HERMES_{normalized.upper()}_WORKER_SOCKET", "")
    if explicit:
        return explicit
    return f"/worker-sockets/{normalized}/worker.sock"


def call_worker(
    worker: str,
    operation: str,
    *,
    prompt: str | None = None,
    timeout_seconds: int = 900,
) -> dict[str, Any]:
    normalized = worker.strip().lower()
    path = socket_path(normalized)
    return _call_socket(
        path,
        operation,
        prompt=prompt,
        timeout_seconds=timeout_seconds,
    )


def agent_socket_path(agent_id: str) -> str:
    normalized = str(agent_id).strip().lower().replace("_", "-")
    if not AGENT_ID.fullmatch(normalized):
        raise ValueError(f"invalid agent id: {agent_id}")
    root = os.environ.get("HERMES_AGENT_SOCKET_ROOT", "/agent-sockets").rstrip("/")
    return f"{root}/{normalized}/worker.sock"


def call_agent(
    agent_id: str,
    operation: str,
    *,
    prompt: str | None = None,
    timeout_seconds: int = 900,
) -> dict[str, Any]:
    return _call_socket(
        agent_socket_path(agent_id),
        operation,
        prompt=prompt,
        timeout_seconds=timeout_seconds,
    )


def _call_socket(
    path: str,
    operation: str,
    *,
    prompt: str | None,
    timeout_seconds: int,
) -> dict[str, Any]:
    if operation not in {"status", "run", "cancel"}:
        raise ValueError(f"unsupported operation: {operation}")
    if operation == "run" and (not isinstance(prompt, str) or not prompt.strip()):
        raise ValueError("prompt must be a non-empty string")
    bounded_timeout = min(max(int(timeout_seconds), 30), 1800)
    request: dict[str, Any] = {
        "operation": operation,
        "timeout_seconds": bounded_timeout,
    }
    if operation == "run" and prompt is not None:
        request["prompt"] = prompt

    payload = (json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8")
    chunks: list[bytes] = []
    size = 0
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(5)
        client.connect(path)
        client.settimeout(bounded_timeout + 15)
        client.sendall(payload)
        while True:
            chunk = client.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > MAX_RESPONSE_BYTES:
                raise RuntimeError("worker response exceeds 5 MiB")
            if b"\n" in chunk:
                break

    raw = b"".join(chunks).split(b"\n", 1)[0]
    if not raw:
        raise RuntimeError("worker returned an empty response")
    response = json.loads(raw.decode("utf-8"))
    if not isinstance(response, dict):
        raise RuntimeError("worker returned a non-object response")
    return response

#!/usr/bin/env python3
"""Loopback-only operator API/UI for isolated worker subscription logins."""

from __future__ import annotations

import argparse
import codecs
import fcntl
import hmac
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import pty
import re
import secrets
import signal
import struct
import subprocess
import termios
import threading
import time
from typing import Any
from urllib.parse import parse_qs, urlsplit
import webbrowser


ALLOWED_WORKERS = ("codex", "claude")
MAX_BODY_BYTES = 8 * 1024
MAX_INPUT_CHARS = 4096
MAX_OUTPUT_CHARS = 1024 * 1024
FINISHED_SESSION_TTL_SECONDS = 15 * 60
ANSI_ESCAPE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")


class LoginUiError(RuntimeError):
    """Expected, user-facing login UI error."""


class SessionNotFound(LoginUiError):
    """The requested login session does not exist."""


class SessionConflict(LoginUiError):
    """The requested operation conflicts with a running session."""


def normalize_worker(value: str) -> str:
    if not isinstance(value, str):
        raise LoginUiError("worker must be a string")
    worker = value.strip().lower().replace("_", "-")
    aliases = {
        "codex-cli": "codex",
        "claude-cli": "claude",
        "claude-code": "claude",
    }
    worker = aliases.get(worker, worker)
    if worker not in ALLOWED_WORKERS:
        raise LoginUiError("worker must be codex or claude")
    return worker


def login_arguments(worker: str) -> list[str]:
    """Return the exact subscription-login command; no caller args allowed."""
    worker = normalize_worker(worker)
    if worker == "codex":
        # hermesctl deliberately maps the empty Codex login argument list to
        # `codex login --device-auth`, which works in its headless container.
        return ["worker", "codex", "login"]
    # Pin the Claude.ai subscription path instead of Console/API billing.
    return ["worker", "claude", "login", "--claudeai"]


def _clean_terminal_text(value: str) -> str:
    return ANSI_ESCAPE.sub("", value).replace("\r\n", "\n").replace("\r", "\n")


class LoginSession:
    def __init__(self, session_id: str, worker: str, command: list[str], cwd: Path):
        self.session_id = session_id
        self.worker = worker
        self.command = tuple(command)
        self.cwd = cwd
        self.created_at = time.time()
        self.finished_at: float | None = None
        self.state = "starting"
        self.exit_code: int | None = None
        self._cancel_requested = False
        self._buffer = ""
        self._base_offset = 0
        self._lock = threading.RLock()
        self._master_fd: int | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._reader_thread: threading.Thread | None = None

    def start(self) -> None:
        master_fd, slave_fd = pty.openpty()
        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, struct.pack("HHHH", 32, 120, 0, 0))
        environment = os.environ.copy()
        environment.update(
            {
                "COMPOSE_ANSI": "never",
                "COMPOSE_PROGRESS": "plain",
                "NO_COLOR": "1",
                "TERM": "dumb",
            }
        )
        try:
            process = subprocess.Popen(
                self.command,
                cwd=self.cwd,
                env=environment,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                close_fds=True,
                start_new_session=True,
            )
        except Exception:
            os.close(master_fd)
            os.close(slave_fd)
            raise
        finally:
            try:
                os.close(slave_fd)
            except OSError:
                pass

        with self._lock:
            self._master_fd = master_fd
            self._process = process
            self.state = "running"
        self._reader_thread = threading.Thread(
            target=self._read_output,
            name=f"login-session-{self.session_id}",
            daemon=True,
        )
        self._reader_thread.start()

    def _append_output(self, text: str) -> None:
        if not text:
            return
        with self._lock:
            self._buffer += text
            overflow = len(self._buffer) - MAX_OUTPUT_CHARS
            if overflow > 0:
                self._buffer = self._buffer[overflow:]
                self._base_offset += overflow

    def _read_output(self) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        try:
            while True:
                with self._lock:
                    master_fd = self._master_fd
                if master_fd is None:
                    break
                try:
                    chunk = os.read(master_fd, 8192)
                except OSError:
                    break
                if not chunk:
                    break
                self._append_output(decoder.decode(chunk))
            self._append_output(decoder.decode(b"", final=True))
        finally:
            with self._lock:
                process = self._process
            exit_code = process.wait() if process is not None else 1
            with self._lock:
                self.exit_code = exit_code
                self.finished_at = time.time()
                self.state = (
                    "cancelled"
                    if self._cancel_requested
                    else "succeeded" if exit_code == 0 else "failed"
                )
                master_fd = self._master_fd
                self._master_fd = None
            if master_fd is not None:
                try:
                    os.close(master_fd)
                except OSError:
                    pass

    def send_input(self, text: str) -> None:
        if not isinstance(text, str):
            raise LoginUiError("text must be a string")
        if len(text) > MAX_INPUT_CHARS:
            raise LoginUiError(f"input exceeds {MAX_INPUT_CHARS} characters")
        if "\x00" in text or any(ord(char) < 32 and char != "\t" for char in text):
            raise LoginUiError("input contains unsupported control characters")
        with self._lock:
            if self.state != "running" or self._master_fd is None:
                raise SessionConflict("login session is not running")
            master_fd = self._master_fd
        try:
            os.write(master_fd, (text + "\n").encode("utf-8"))
        except OSError as exc:
            raise SessionConflict(f"login terminal is unavailable: {exc}") from exc

    def cancel(self) -> None:
        with self._lock:
            process = self._process
            if self.state not in {"starting", "running"} or process is None:
                return
            self._cancel_requested = True
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        terminator = threading.Thread(
            target=self._terminate_after_grace,
            name=f"login-session-cancel-{self.session_id}",
            daemon=True,
        )
        terminator.start()

    def _terminate_after_grace(self) -> None:
        time.sleep(3)
        self.force_kill()

    def force_kill(self) -> None:
        with self._lock:
            process = self._process
            running = self.state in {"starting", "running"}
        if process is not None and running:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def snapshot(self, requested_offset: int = 0) -> dict[str, Any]:
        if requested_offset < 0:
            raise LoginUiError("offset must be zero or greater")
        with self._lock:
            reset = requested_offset < self._base_offset
            effective_offset = self._base_offset if reset else requested_offset
            relative = max(0, effective_offset - self._base_offset)
            output = self._buffer[relative:]
            next_offset = self._base_offset + len(self._buffer)
            return {
                "id": self.session_id,
                "worker": self.worker,
                "state": self.state,
                "exit_code": self.exit_code,
                "created_at": self.created_at,
                "finished_at": self.finished_at,
                "output": _clean_terminal_text(output),
                "next_offset": next_offset,
                "offset_reset": reset,
            }


class LoginSessionManager:
    def __init__(self, root: Path, hermesctl: Path | None = None):
        self.root = root.resolve()
        self.hermesctl = (hermesctl or self.root / "hermesctl").resolve()
        self._sessions: dict[str, LoginSession] = {}
        self._lock = threading.RLock()

    def _remove_expired(self) -> None:
        cutoff = time.time() - FINISHED_SESSION_TTL_SECONDS
        expired = [
            session_id
            for session_id, session in self._sessions.items()
            if session.finished_at is not None and session.finished_at < cutoff
        ]
        for session_id in expired:
            self._sessions.pop(session_id, None)

    def create(self, worker_value: str) -> LoginSession:
        worker = normalize_worker(worker_value)
        with self._lock:
            self._remove_expired()
            if any(
                session.worker == worker and session.state in {"starting", "running"}
                for session in self._sessions.values()
            ):
                raise SessionConflict(f"a {worker} login session is already running")
            session_id = secrets.token_urlsafe(18)
            command = [str(self.hermesctl), *login_arguments(worker)]
            session = LoginSession(session_id, worker, command, self.root)
            self._sessions[session_id] = session
        try:
            session.start()
        except Exception:
            with self._lock:
                self._sessions.pop(session_id, None)
            raise
        return session

    def get(self, session_id: str) -> LoginSession:
        with self._lock:
            self._remove_expired()
            session = self._sessions.get(session_id)
        if session is None:
            raise SessionNotFound("login session not found")
        return session

    def worker_status(self, worker_value: str) -> dict[str, Any]:
        worker = normalize_worker(worker_value)
        environment = os.environ.copy()
        environment.update(
            {
                "COMPOSE_ANSI": "never",
                "COMPOSE_PROGRESS": "plain",
                "NO_COLOR": "1",
                "TERM": "dumb",
            }
        )
        try:
            result = subprocess.run(
                [str(self.hermesctl), "worker", worker, "status"],
                cwd=self.root,
                env=environment,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"ok": False, "worker": worker, "error": str(exc)}
        return {
            "ok": result.returncode == 0,
            "worker": worker,
            "exit_code": result.returncode,
            "output": _clean_terminal_text(result.stdout),
            "error": _clean_terminal_text(result.stderr),
        }

    def shutdown(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
        for session in sessions:
            session.cancel()
        for session in sessions:
            thread = session._reader_thread
            if thread is not None:
                thread.join(timeout=5)
                if thread.is_alive():
                    session.force_kill()
                    thread.join(timeout=2)


class LoginUiHttpServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        manager: LoginSessionManager,
        token: str,
        static_root: Path,
    ):
        super().__init__(server_address, LoginUiHandler)
        self.manager = manager
        self.auth_token = token
        self.static_root = static_root.resolve()
        self.origin = f"http://127.0.0.1:{self.server_address[1]}"


class LoginUiHandler(BaseHTTPRequestHandler):
    server: LoginUiHttpServer

    def log_message(self, format_string: str, *args: Any) -> None:
        print(f"login-ui: {self.address_string()} - {format_string % args}")

    def _security_headers(self, content_type: str, length: int) -> None:
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self'; base-uri 'none'; "
            "frame-ancestors 'none'; form-action 'none'",
        )

    def _send_json(self, status: HTTPStatus | int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._security_headers("application/json; charset=utf-8", len(body))
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, status: HTTPStatus | int, message: str) -> None:
        self._send_json(status, {"ok": False, "error": message})

    def _authenticated(self) -> bool:
        authorization = self.headers.get("Authorization", "")
        expected = f"Bearer {self.server.auth_token}"
        if not hmac.compare_digest(authorization, expected):
            self._send_error_json(HTTPStatus.UNAUTHORIZED, "valid bearer token required")
            return False
        return True

    def _trusted_origin(self) -> bool:
        origin = self.headers.get("Origin")
        if origin and origin != self.server.origin:
            self._send_error_json(HTTPStatus.FORBIDDEN, "cross-origin request rejected")
            return False
        return True

    def _read_json(self) -> dict[str, Any]:
        length_text = self.headers.get("Content-Length", "0")
        try:
            length = int(length_text)
        except ValueError as exc:
            raise LoginUiError("invalid Content-Length") from exc
        if length < 0 or length > MAX_BODY_BYTES:
            raise LoginUiError(f"request body exceeds {MAX_BODY_BYTES} bytes")
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise LoginUiError("request body must be valid JSON") from exc
        if not isinstance(payload, dict):
            raise LoginUiError("request body must be a JSON object")
        return payload

    def _serve_static(self, path: str) -> None:
        files = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/index.html": ("index.html", "text/html; charset=utf-8"),
            "/app.js": ("app.js", "text/javascript; charset=utf-8"),
            "/style.css": ("style.css", "text/css; charset=utf-8"),
        }
        entry = files.get(path)
        if entry is None:
            self._send_error_json(HTTPStatus.NOT_FOUND, "not found")
            return
        filename, content_type = entry
        try:
            body = (self.server.static_root / filename).read_bytes()
        except OSError as exc:
            self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
            return
        self.send_response(HTTPStatus.OK)
        self._security_headers(content_type, len(body))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        path = parsed.path
        if not path.startswith("/api/"):
            self._serve_static(path)
            return
        if not self._authenticated():
            return
        parts = [part for part in path.split("/") if part]
        try:
            if parts == ["api", "v1", "health"]:
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "service": "hermes-subscription-login",
                        "workers": list(ALLOWED_WORKERS),
                        "operator_only": True,
                    },
                )
                return
            if len(parts) == 5 and parts[:3] == ["api", "v1", "workers"] and parts[4] == "status":
                self._send_json(HTTPStatus.OK, self.server.manager.worker_status(parts[3]))
                return
            if len(parts) == 4 and parts[:3] == ["api", "v1", "login-sessions"]:
                offset_values = parse_qs(parsed.query).get("offset", ["0"])
                try:
                    offset = int(offset_values[0])
                except ValueError as exc:
                    raise LoginUiError("offset must be an integer") from exc
                snapshot = self.server.manager.get(parts[3]).snapshot(offset)
                self._send_json(HTTPStatus.OK, {"ok": True, **snapshot})
                return
            self._send_error_json(HTTPStatus.NOT_FOUND, "not found")
        except SessionNotFound as exc:
            self._send_error_json(HTTPStatus.NOT_FOUND, str(exc))
        except LoginUiError as exc:
            self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))

    def do_POST(self) -> None:
        if not self._authenticated() or not self._trusted_origin():
            return
        parts = [part for part in urlsplit(self.path).path.split("/") if part]
        try:
            payload = self._read_json()
            if parts == ["api", "v1", "login-sessions"]:
                if set(payload) != {"worker"}:
                    raise LoginUiError("only the worker field is accepted")
                session = self.server.manager.create(payload["worker"])
                self._send_json(
                    HTTPStatus.CREATED,
                    {"ok": True, **session.snapshot()},
                )
                return
            if len(parts) == 5 and parts[:3] == ["api", "v1", "login-sessions"] and parts[4] == "input":
                if set(payload) != {"text"}:
                    raise LoginUiError("only the text field is accepted")
                session = self.server.manager.get(parts[3])
                session.send_input(payload["text"])
                self._send_json(HTTPStatus.OK, {"ok": True, **session.snapshot()})
                return
            self._send_error_json(HTTPStatus.NOT_FOUND, "not found")
        except SessionNotFound as exc:
            self._send_error_json(HTTPStatus.NOT_FOUND, str(exc))
        except SessionConflict as exc:
            self._send_error_json(HTTPStatus.CONFLICT, str(exc))
        except LoginUiError as exc:
            self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
        except OSError as exc:
            self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def do_DELETE(self) -> None:
        if not self._authenticated() or not self._trusted_origin():
            return
        parts = [part for part in urlsplit(self.path).path.split("/") if part]
        try:
            if len(parts) == 4 and parts[:3] == ["api", "v1", "login-sessions"]:
                session = self.server.manager.get(parts[3])
                session.cancel()
                self._send_json(HTTPStatus.OK, {"ok": True, **session.snapshot()})
                return
            self._send_error_json(HTTPStatus.NOT_FOUND, "not found")
        except SessionNotFound as exc:
            self._send_error_json(HTTPStatus.NOT_FOUND, str(exc))


def create_server(
    root: Path,
    port: int,
    token: str,
    hermesctl: Path | None = None,
    static_root: Path | None = None,
) -> LoginUiHttpServer:
    root = root.resolve()
    manager = LoginSessionManager(root, hermesctl=hermesctl)
    assets = static_root or root / "login-ui"
    return LoginUiHttpServer(("127.0.0.1", port), manager, token, assets)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Serve the local operator-only subscription login API and UI."
    )
    parser.add_argument("--root", type=Path, required=True, help=argparse.SUPPRESS)
    parser.add_argument("--port", type=int, default=8765, help="loopback TCP port (default: 8765)")
    parser.add_argument(
        "--no-browser", action="store_true", help="print the URL without opening a browser"
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not 0 <= args.port <= 65535:
        raise SystemExit("login-ui: --port must be between 0 and 65535")
    root = args.root.resolve()
    hermesctl = root / "hermesctl"
    static_root = root / "login-ui"
    if not hermesctl.is_file() or not os.access(hermesctl, os.X_OK):
        raise SystemExit(f"login-ui: hermesctl is unavailable: {hermesctl}")
    if not all((static_root / name).is_file() for name in ("index.html", "app.js", "style.css")):
        raise SystemExit(f"login-ui: static assets are incomplete: {static_root}")

    token = secrets.token_urlsafe(32)
    server = create_server(root, args.port, token)
    url = f"{server.origin}/#token={token}"
    print("Hermes subscription login UI (operator-only, loopback-only)")
    print(f"URL: {url}")
    print(f"API bearer token: {token}")
    print("Stop with Ctrl-C. Active login processes are cancelled on shutdown.", flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print("\nStopping login UI...", flush=True)
    finally:
        server.shutdown()
        server.manager.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

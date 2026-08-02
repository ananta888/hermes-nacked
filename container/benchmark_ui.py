#!/usr/bin/env python3
"""Loopback-only, bearer-protected operator UI for persistent evaluations."""

from __future__ import annotations

import argparse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
from pathlib import Path
import secrets
import subprocess
import sys
from typing import Any
from urllib.parse import unquote, urlparse
import webbrowser


MAX_BODY_BYTES = 64 * 1024


class BenchmarkHttpServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, root: Path, services: Any, port: int, token: str, assets: Path):
        super().__init__(("127.0.0.1", port), BenchmarkHandler)
        self.root = root.resolve()
        self.services = services
        self.auth_token = token
        self.assets = assets.resolve()
        self.origin = f"http://127.0.0.1:{self.server_address[1]}"

    def spawn(self, experiment_id: str, action: str) -> None:
        wrapper = self.root / "container" / "control_cli.py"
        subprocess.Popen(
            [
                sys.executable,
                str(wrapper),
                "--root",
                str(self.root),
                "benchmark",
                action,
                experiment_id,
            ],
            cwd=self.root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )


class BenchmarkHandler(BaseHTTPRequestHandler):
    server: BenchmarkHttpServer

    def log_message(self, format: str, *args: object) -> None:
        print(f"benchmark-ui: {self.address_string()} - {format % args}", file=sys.stderr)

    def _security_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self' data:; base-uri 'none'; "
            "form-action 'none'; frame-ancestors 'none'",
        )

    def _json(self, status: HTTPStatus, value: Any) -> None:
        payload = (json.dumps(value, ensure_ascii=False) + "\n").encode("utf-8")
        self.send_response(status)
        self._security_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _error(self, status: HTTPStatus, message: str) -> None:
        self._json(status, {"ok": False, "error": message})

    def _authorized(self) -> bool:
        supplied = self.headers.get("Authorization", "")
        expected = f"Bearer {self.server.auth_token}"
        if not hmac.compare_digest(supplied, expected):
            self._error(HTTPStatus.UNAUTHORIZED, "valid bearer token required")
            return False
        origin = self.headers.get("Origin")
        if origin and origin != self.server.origin:
            self._error(HTTPStatus.FORBIDDEN, "foreign browser origin rejected")
            return False
        return True

    def _body(self) -> dict[str, Any] | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._error(HTTPStatus.BAD_REQUEST, "invalid content length")
            return None
        if not 1 <= length <= MAX_BODY_BYTES:
            self._error(HTTPStatus.BAD_REQUEST, "JSON body must be between 1 byte and 64 KiB")
            return None
        try:
            value = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._error(HTTPStatus.BAD_REQUEST, "invalid UTF-8 JSON body")
            return None
        if not isinstance(value, dict):
            self._error(HTTPStatus.BAD_REQUEST, "JSON body must be an object")
            return None
        return value

    def _static(self, request_path: str) -> None:
        relative = "index.html" if request_path in {"", "/"} else unquote(request_path.lstrip("/"))
        if relative not in {"index.html", "app.js", "style.css"}:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        path = self.server.assets / relative
        try:
            content = path.read_bytes()
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content_types = {
            "index.html": "text/html; charset=utf-8",
            "app.js": "text/javascript; charset=utf-8",
            "style.css": "text/css; charset=utf-8",
        }
        self.send_response(HTTPStatus.OK)
        self._security_headers()
        self.send_header("Content-Type", content_types[relative])
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if not path.startswith("/api/"):
            self._static(path)
            return
        if not self._authorized():
            return
        try:
            if path == "/api/v1/health":
                self._json(HTTPStatus.OK, {"ok": True})
            elif path == "/api/v1/meta":
                agents = [
                    {
                        "agent_id": item.agent_id,
                        "engine": item.engine,
                        "role": item.role,
                        "model": self.server.services.agents.model(item.agent_id),
                    }
                    for item in self.server.services.agents.list(
                        include_operator_only=False
                    )
                ]
                self._json(
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "agents": agents,
                        "evaluators": self.server.services.evaluations.evaluators.names(),
                    },
                )
            elif path == "/api/v1/experiments":
                self._json(HTTPStatus.OK, self.server.services.evaluations.list())
            elif path.startswith("/api/v1/experiments/"):
                parts = path.strip("/").split("/")
                if len(parts) == 4:
                    self._json(
                        HTTPStatus.OK,
                        self.server.services.evaluations.status(parts[3]),
                    )
                elif len(parts) == 6 and parts[4] == "results":
                    experiment = self.server.services.evaluations.status(parts[3])
                    trial = next(
                        (item for item in experiment["trials"] if item["trial_id"] == parts[5]),
                        None,
                    )
                    if not trial or not trial.get("result_artifact"):
                        self._error(HTTPStatus.NOT_FOUND, "trial result is unavailable")
                        return
                    content = self.server.services.artifacts.model_view(
                        trial["result_artifact"]
                    )["content"]
                    self._json(HTTPStatus.OK, json.loads(content))
                else:
                    self._error(HTTPStatus.NOT_FOUND, "unknown endpoint")
            else:
                self._error(HTTPStatus.NOT_FOUND, "unknown endpoint")
        except Exception as exc:  # operator API must return controlled JSON errors
            self._error(HTTPStatus.BAD_REQUEST, str(exc))

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if not self._authorized():
            return
        try:
            if path == "/api/v1/experiments":
                body = self._body()
                if body is None:
                    return
                if set(body) != {"manifest_path"} or not isinstance(body["manifest_path"], str):
                    self._error(HTTPStatus.BAD_REQUEST, "expected exactly {manifest_path}")
                    return
                result = self.server.services.evaluations.create_manifest(
                    Path(body["manifest_path"])
                )
                self._json(HTTPStatus.CREATED, result)
                return
            parts = path.strip("/").split("/")
            if len(parts) != 5 or parts[:3] != ["api", "v1", "experiments"]:
                self._error(HTTPStatus.NOT_FOUND, "unknown endpoint")
                return
            experiment_id, action = parts[3], parts[4]
            if action == "run":
                self.server.spawn(experiment_id, "_run")
                self._json(HTTPStatus.ACCEPTED, {"ok": True, "runner": "started"})
            elif action == "resume":
                self.server.spawn(experiment_id, "_resume")
                self._json(HTTPStatus.ACCEPTED, {"ok": True, "runner": "resuming"})
            elif action == "cancel":
                self._json(
                    HTTPStatus.OK,
                    self.server.services.evaluations.cancel(experiment_id),
                )
            else:
                self._error(HTTPStatus.NOT_FOUND, "unknown action")
        except Exception as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))


def run_server(root: Path, *, port: int = 8840, open_browser: bool = True) -> None:
    if not 0 <= int(port) <= 65535:
        raise ValueError("port must be between 0 and 65535")
    container_root = root.resolve() / "container"
    if str(container_root) not in sys.path:
        sys.path.insert(0, str(container_root))
    from control_plane.cli import Services

    services = Services(root)
    token = secrets.token_urlsafe(32)
    assets = root.resolve() / "benchmark-ui"
    server = BenchmarkHttpServer(root, services, int(port), token, assets)
    url = f"{server.origin}/#token={token}"
    print(f"Benchmark UI: {url}")
    print(f"API bearer token: {token}")
    print("Bound to loopback only; Ctrl-C stops the dashboard, not running experiment processes.")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8840)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    run_server(args.root, port=args.port, open_browser=not args.no_browser)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

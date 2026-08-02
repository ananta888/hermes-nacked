from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen


REPOSITORY = Path(__file__).resolve().parents[1]
MODULE_PATH = REPOSITORY / "container" / "login_ui.py"
SPEC = importlib.util.spec_from_file_location("hermes_login_ui", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
login_ui = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(login_ui)


FAKE_HERMESCTL = """#!/usr/bin/env python3
import sys

print("ARGS=" + " ".join(sys.argv[1:]), flush=True)
worker = sys.argv[2] if len(sys.argv) > 2 else "missing"
action = sys.argv[3] if len(sys.argv) > 3 else "missing"
if action == "status":
    print(f"STATUS={worker}", flush=True)
elif action == "login":
    print(f"https://example.test/{worker}/login", flush=True)
    print("INPUT=" + sys.stdin.readline().strip(), flush=True)
else:
    raise SystemExit(64)
"""


class LoginUiTestBase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.fake_ctl = self.root / "hermesctl"
        self.fake_ctl.write_text(FAKE_HERMESCTL, encoding="utf-8")
        self.fake_ctl.chmod(0o755)

    def tearDown(self):
        self.temporary.cleanup()

    def wait_for_state(self, session, states: set[str], timeout: float = 5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            snapshot = session.snapshot()
            if snapshot["state"] in states:
                return snapshot
            time.sleep(0.02)
        self.fail(f"session did not reach {states}: {session.snapshot()}")

    def wait_for_output(self, session, text: str, timeout: float = 5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            snapshot = session.snapshot()
            if text in snapshot["output"]:
                return snapshot
            time.sleep(0.02)
        self.fail(f"session output did not contain {text!r}: {session.snapshot()}")


class LoginSessionTests(LoginUiTestBase):
    def test_login_commands_are_fixed_to_subscription_flows(self):
        self.assertEqual(
            login_ui.login_arguments("codex-cli"),
            ["worker", "codex", "login"],
        )
        self.assertEqual(
            login_ui.login_arguments("claude-code"),
            ["worker", "claude", "login", "--claudeai"],
        )
        with self.assertRaises(login_ui.LoginUiError):
            login_ui.login_arguments("opencode")

    def test_pty_session_streams_output_and_accepts_bounded_input(self):
        manager = login_ui.LoginSessionManager(self.root, self.fake_ctl)
        try:
            session = manager.create("claude")
            self.wait_for_output(session, "https://example.test/claude/login")
            session.send_input("browser-return-code")
            snapshot = self.wait_for_state(session, {"succeeded", "failed"})
            self.assertEqual(snapshot["state"], "succeeded")
            self.assertIn("ARGS=worker claude login --claudeai", snapshot["output"])
            self.assertIn("INPUT=browser-return-code", snapshot["output"])
        finally:
            manager.shutdown()

    def test_second_session_for_same_worker_is_rejected(self):
        manager = login_ui.LoginSessionManager(self.root, self.fake_ctl)
        try:
            session = manager.create("codex")
            self.wait_for_output(session, "https://example.test/codex/login")
            with self.assertRaises(login_ui.SessionConflict):
                manager.create("codex")
            session.cancel()
            snapshot = self.wait_for_state(session, {"cancelled", "failed"})
            self.assertEqual(snapshot["state"], "cancelled")
        finally:
            manager.shutdown()

    def test_worker_status_is_read_only_and_worker_is_validated(self):
        manager = login_ui.LoginSessionManager(self.root, self.fake_ctl)
        status = manager.worker_status("codex")
        self.assertTrue(status["ok"])
        self.assertIn("ARGS=worker codex status", status["output"])
        with self.assertRaises(login_ui.LoginUiError):
            manager.worker_status("hermes")


class LoginHttpApiTests(LoginUiTestBase):
    def setUp(self):
        super().setUp()
        self.token = "test-bearer-token"
        self.server = login_ui.create_server(
            self.root,
            0,
            self.token,
            hermesctl=self.fake_ctl,
            static_root=REPOSITORY / "login-ui",
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = self.server.origin

    def tearDown(self):
        self.server.shutdown()
        self.server.manager.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        super().tearDown()

    def request(self, path: str, method: str = "GET", payload=None, token=True, origin=None):
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {self.token}"
        if origin is not None:
            headers["Origin"] = origin
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(self.base_url + path, data=data, headers=headers, method=method)
        with urlopen(request, timeout=5) as response:
            return response.status, response.headers, response.read()

    def test_static_ui_is_available_but_api_requires_token(self):
        status, headers, body = self.request("/", token=False)
        self.assertEqual(status, 200)
        self.assertIn(b"Abo-Anmeldung", body)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])

        with self.assertRaises(HTTPError) as caught:
            self.request("/api/v1/health", token=False)
        self.assertEqual(caught.exception.code, 401)

        status, _, body = self.request("/api/v1/health")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["workers"], ["codex", "claude"])

    def test_api_runs_fixed_login_and_relays_input(self):
        status, _, body = self.request(
            "/api/v1/login-sessions",
            method="POST",
            payload={"worker": "codex"},
            origin=self.server.origin,
        )
        self.assertEqual(status, 201)
        session_id = json.loads(body)["id"]
        session = self.server.manager.get(session_id)
        self.wait_for_output(session, "https://example.test/codex/login")

        status, _, _ = self.request(
            f"/api/v1/login-sessions/{session_id}/input",
            method="POST",
            payload={"text": "device-complete"},
            origin=self.server.origin,
        )
        self.assertEqual(status, 200)
        snapshot = self.wait_for_state(session, {"succeeded", "failed"})
        self.assertEqual(snapshot["state"], "succeeded")
        self.assertIn("ARGS=worker codex login", snapshot["output"])
        self.assertNotIn("--claudeai", snapshot["output"])

    def test_api_rejects_cross_origin_and_unknown_fields(self):
        with self.assertRaises(HTTPError) as caught:
            self.request(
                "/api/v1/login-sessions",
                method="POST",
                payload={"worker": "codex"},
                origin="https://attacker.example",
            )
        self.assertEqual(caught.exception.code, 403)

        with self.assertRaises(HTTPError) as caught:
            self.request(
                "/api/v1/login-sessions",
                method="POST",
                payload={"worker": "codex", "args": ["--with-api-key"]},
                origin=self.server.origin,
            )
        self.assertEqual(caught.exception.code, 400)

        with self.assertRaises(HTTPError) as caught:
            self.request(
                "/api/v1/login-sessions",
                method="POST",
                payload={"worker": "opencode"},
                origin=self.server.origin,
            )
        self.assertEqual(caught.exception.code, 400)


if __name__ == "__main__":
    unittest.main()

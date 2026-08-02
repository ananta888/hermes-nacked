from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


REPOSITORY = Path(__file__).resolve().parents[1]
HERMESCTL = REPOSITORY / "hermesctl"


class GenericAccessCliTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "config").mkdir()
        (self.root / "config" / "config.yaml").write_text("{}\n", encoding="utf-8")
        self.environment = os.environ.copy()
        self.environment.update(
            {
                "HERMESCTL_ROOT": str(self.root),
                "HERMESCTL_POLICY_DIR": str(REPOSITORY / "container"),
            }
        )

    def tearDown(self):
        self.temporary.cleanup()

    def run_ctl(self, *arguments: str, expected: int = 0):
        result = subprocess.run(
            [str(HERMESCTL), *arguments],
            env=self.environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            result.returncode,
            expected,
            msg=f"stdout={result.stdout!r}\nstderr={result.stderr!r}",
        )
        return result

    def read_lines(self, path: Path) -> list[str]:
        if not path.exists():
            return []
        return path.read_text(encoding="utf-8").splitlines()

    def worker_profile(self, worker: str) -> Path:
        return self.root / "runtime" / "control" / "workers" / worker / "capabilities"

    def test_explain_marks_codex_special_and_accepts_cli_alias(self):
        result = self.run_ctl("access", "codex-cli", "explain")
        self.assertIn("Access target: codex", result.stdout)
        self.assertIn("[special]    tool-use", result.stdout)
        self.assertIn("[alternative]", result.stdout)

    def test_common_features_map_to_hermes_capabilities(self):
        self.run_ctl(
            "access",
            "hermes",
            "enable",
            "tool-use",
            "skills",
            "AGENTS.md",
        )
        self.assertEqual(
            self.read_lines(self.root / ".hermes-capabilities"),
            ["files", "orchestrator", "skills"],
        )
        status = self.run_ctl("access", "hermes", "status")
        self.assertIn(
            "Common access:     tool-use skills agents-md", status.stdout
        )

    def test_common_features_are_scoped_to_one_worker(self):
        self.run_ctl(
            "access",
            "claude-code",
            "enable",
            "tool-use",
            "skills",
            "agents-md",
        )
        self.assertEqual(
            self.read_lines(self.worker_profile("claude")),
            ["agents-md", "skills", "tools"],
        )
        self.assertEqual(self.read_lines(self.worker_profile("codex")), [])
        self.assertEqual(self.read_lines(self.worker_profile("opencode")), [])
        status = self.run_ctl("access", "claude", "status")
        self.assertIn(
            "Common access:     tool-use skills agents-md", status.stdout
        )

    def test_worker_commandline_dependency_is_explicit_and_atomic(self):
        failed = self.run_ctl(
            "access", "codex", "enable", "commandline", expected=64
        )
        self.assertIn("generic: tool-use", failed.stderr)
        self.assertEqual(self.read_lines(self.worker_profile("codex")), [])

        self.run_ctl(
            "access", "codex", "enable", "tool-use", "commandline"
        )
        failed = self.run_ctl(
            "access", "codex", "disable", "tool-use", expected=64
        )
        self.assertIn("generic: tool-use", failed.stderr)
        self.assertEqual(
            self.read_lines(self.worker_profile("codex")),
            ["commandline", "tools"],
        )

    def test_reset_affects_exactly_one_target(self):
        self.run_ctl("access", "hermes", "enable", "skills")
        self.run_ctl("access", "claude", "enable", "tool-use")
        self.run_ctl("access", "claude", "reset")
        self.assertEqual(
            self.read_lines(self.root / ".hermes-capabilities"), ["skills"]
        )
        self.assertEqual(self.read_lines(self.worker_profile("claude")), [])

    def test_unknown_generic_feature_fails_without_mutation(self):
        result = self.run_ctl(
            "access", "opencode", "enable", "network", expected=64
        )
        self.assertIn("unknown generic access feature", result.stderr)
        self.assertEqual(self.read_lines(self.worker_profile("opencode")), [])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "container"))

from worker_policy import (  # noqa: E402
    WorkerPolicyError,
    normalize_worker,
    parse_worker_features,
)


class WorkerPolicyTests(unittest.TestCase):
    def test_empty_is_model_only(self):
        policy = parse_worker_features("")
        self.assertEqual(policy.features, ())
        self.assertFalse(policy.tools)
        self.assertFalse(policy.commandline)
        self.assertFalse(policy.skills)
        self.assertFalse(policy.agents_md)
        self.assertFalse(policy.claude_md)

    def test_every_feature_is_independent_except_commandline(self):
        policy = parse_worker_features("tools skills agents-md claude-md")
        self.assertEqual(
            policy.features,
            ("agents-md", "claude-md", "skills", "tools"),
        )
        self.assertTrue(policy.tools)
        self.assertFalse(policy.commandline)
        self.assertTrue(policy.skills)
        self.assertTrue(policy.agents_md)
        self.assertTrue(policy.claude_md)

    def test_commandline_requires_tools(self):
        with self.assertRaises(WorkerPolicyError):
            parse_worker_features("commandline")
        policy = parse_worker_features("tools commandline")
        self.assertTrue(policy.tools)
        self.assertTrue(policy.commandline)

    def test_aliases_are_normalized(self):
        policy = parse_worker_features(
            "tool-use terminal AGENTS.md CLAUDE.md"
        )
        self.assertEqual(
            policy.features,
            ("agents-md", "claude-md", "commandline", "tools"),
        )

    def test_unknown_feature_fails_closed(self):
        with self.assertRaises(WorkerPolicyError):
            parse_worker_features("tools network")

    def test_worker_names_are_exact(self):
        self.assertEqual(normalize_worker(" Codex "), "codex")
        with self.assertRaises(WorkerPolicyError):
            normalize_worker("hermes")


if __name__ == "__main__":
    unittest.main()

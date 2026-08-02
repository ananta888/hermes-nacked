from __future__ import annotations

import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "container"))

from policy import PolicyError, parse_capabilities


class PolicyTests(unittest.TestCase):
    def test_empty_is_really_empty(self):
        policy = parse_capabilities("")
        self.assertEqual(policy.capabilities, ())
        self.assertEqual(policy.toolsets, ())
        self.assertFalse(policy.needs_sandbox)
        self.assertFalse(policy.mount_skills)
        self.assertFalse(policy.mount_control)
        self.assertFalse(policy.direct_control)
        self.assertFalse(policy.enable_mcp)
        self.assertFalse(policy.load_orchestrator)
        self.assertFalse(policy.load_claude_context)
        self.assertFalse(policy.load_context_files)
        self.assertEqual(policy.direct_workers, ())
        self.assertEqual(policy.mcp_workers, ())
        self.assertEqual(policy.workers, ())
        self.assertFalse(policy.generic_agents_direct)
        self.assertFalse(policy.generic_agents_mcp)

    def test_independent_capabilities(self):
        policy = parse_capabilities("skills, files")
        self.assertEqual(policy.capabilities, ("files", "skills"))
        self.assertEqual(policy.toolsets, ("file", "skills"))
        self.assertTrue(policy.needs_sandbox)
        self.assertTrue(policy.mount_skills)
        self.assertFalse(policy.sandbox_network)

    def test_aliases_are_normalized(self):
        policy = parse_capabilities("tools shell AGENTS.md")
        self.assertEqual(
            policy.capabilities,
            ("commandline", "files", "orchestrator"),
        )

    def test_unknown_capability_fails_closed(self):
        with self.assertRaises(PolicyError):
            parse_capabilities("files godmode")

    def test_network_is_a_separate_permission(self):
        with self.assertRaises(PolicyError):
            parse_capabilities("shell-network")
        policy = parse_capabilities("commandline shell-network")
        self.assertTrue(policy.sandbox_network)

    def test_direct_control_requires_shell_and_skills(self):
        with self.assertRaises(PolicyError):
            parse_capabilities("hermesctl-direct")
        with self.assertRaises(PolicyError):
            parse_capabilities("commandline hermesctl-direct")
        policy = parse_capabilities(
            "commandline skills hermesctl-direct orchestrator"
        )
        self.assertTrue(policy.direct_control)
        self.assertTrue(policy.mount_control)
        self.assertTrue(policy.load_orchestrator)
        self.assertFalse(policy.enable_mcp)

    def test_mcp_control_requires_skills(self):
        with self.assertRaises(PolicyError):
            parse_capabilities("hermesctl-mcp")
        policy = parse_capabilities("skills hermesctl-mcp")
        self.assertEqual(policy.toolsets, ("hermesctl", "skills"))
        self.assertTrue(policy.enable_mcp)
        self.assertTrue(policy.mount_control)
        self.assertFalse(policy.needs_sandbox)

    def test_orchestrator_is_context_only(self):
        policy = parse_capabilities("orchestrator")
        self.assertEqual(policy.toolsets, ())
        self.assertTrue(policy.load_orchestrator)
        self.assertFalse(policy.load_claude_context)
        self.assertTrue(policy.load_context_files)
        self.assertFalse(policy.needs_sandbox)

    def test_claude_context_is_independent_and_context_only(self):
        policy = parse_capabilities("claude-md")
        self.assertEqual(policy.capabilities, ("claude-md",))
        self.assertEqual(policy.toolsets, ())
        self.assertFalse(policy.load_orchestrator)
        self.assertTrue(policy.load_claude_context)
        self.assertTrue(policy.load_context_files)
        self.assertFalse(policy.needs_sandbox)

        combined = parse_capabilities("agents-md claude-md")
        self.assertTrue(combined.load_orchestrator)
        self.assertTrue(combined.load_claude_context)

    def test_worker_direct_requires_shell_and_skills(self):
        for capability in ("codex-direct", "claude-direct", "opencode-direct"):
            with self.subTest(capability=capability):
                with self.assertRaises(PolicyError):
                    parse_capabilities(capability)
                policy = parse_capabilities(
                    f"commandline skills {capability}"
                )
                self.assertEqual(policy.direct_workers, (capability.removesuffix("-direct"),))
                self.assertFalse(policy.enable_mcp)
                self.assertFalse(policy.sandbox_network)

    def test_worker_mcp_requires_only_skills(self):
        for capability in ("codex-mcp", "claude-mcp", "opencode-mcp"):
            worker = capability.removesuffix("-mcp")
            with self.subTest(capability=capability):
                with self.assertRaises(PolicyError):
                    parse_capabilities(capability)
                policy = parse_capabilities(f"skills {capability}")
                self.assertEqual(policy.mcp_workers, (worker,))
                self.assertEqual(policy.workers, (worker,))
                self.assertEqual(policy.toolsets, (f"{worker}_worker", "skills"))
                self.assertTrue(policy.enable_mcp)
                self.assertFalse(policy.needs_sandbox)

    def test_workers_are_independent_and_deduplicated(self):
        policy = parse_capabilities(
            "skills commandline codex-direct codex-mcp claude-mcp"
        )
        self.assertEqual(policy.direct_workers, ("codex",))
        self.assertEqual(policy.mcp_workers, ("codex", "claude"))
        self.assertEqual(policy.workers, ("codex", "claude"))

    def test_generic_agent_surfaces_have_explicit_dependencies(self):
        with self.assertRaises(PolicyError):
            parse_capabilities("agents-direct")
        with self.assertRaises(PolicyError):
            parse_capabilities("skills agents-direct")
        direct = parse_capabilities("skills commandline agents-direct")
        self.assertTrue(direct.generic_agents_direct)
        self.assertFalse(direct.generic_agents_mcp)
        self.assertFalse(direct.enable_mcp)

        with self.assertRaises(PolicyError):
            parse_capabilities("agents-mcp")
        mcp = parse_capabilities("skills agents-mcp")
        self.assertTrue(mcp.generic_agents_mcp)
        self.assertEqual(mcp.toolsets, ("agents", "skills"))
        self.assertTrue(mcp.enable_mcp)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest


REPOSITORY = Path(__file__).resolve().parents[1]
CONTAINER = REPOSITORY / "container"
import sys

sys.path.insert(0, str(CONTAINER))

from control_plane.agents import AgentRegistry
from control_plane.artifacts import ArtifactRepository
from control_plane.credentials import CredentialBroker
from control_plane.domain import ControlPlaneError, validate_rights
from control_plane.jobs import JobRepository, JobService
from control_plane.manifests import load_manifest, normalize_team_manifest
from control_plane.runtime import DockerAgentRuntime
from control_plane.storage import AtomicStore
from control_plane.teams import TeamService


class ControlPlaneTestBase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        shutil.copytree(REPOSITORY / "worker-context", self.root / "worker-context")
        self.store = AtomicStore(self.root)
        self.credentials = CredentialBroker(self.root, self.store)
        self.agents = AgentRegistry(self.root, self.store, self.credentials)
        self.artifacts = ArtifactRepository(self.root, self.store)
        self.jobs = JobRepository(self.root, self.store)

    def tearDown(self):
        self.temporary.cleanup()

    def create_agent(self, agent_id="reviewer", engine="claude"):
        self.credentials.create(agent_id, engine)
        return self.agents.create(agent_id, engine, agent_id)


class CredentialAndAgentTests(ControlPlaneTestBase):
    def test_agent_is_created_zero_rights_with_separate_secret_and_public_registry(self):
        record = self.create_agent()
        self.assertEqual(self.agents.rights(record.agent_id), ())
        private = json.loads(
            (self.root / "runtime/control/agents/reviewer/agent.json").read_text()
        )
        public = json.loads(
            (self.root / "runtime/registry/agents/reviewer/agent.json").read_text()
        )
        self.assertEqual(private["credential_id"], "reviewer")
        self.assertEqual(public["credential_id"], "redacted")
        self.assertFalse(public["credentials_exposed"])
        home = self.credentials.home_path("reviewer")
        self.assertTrue((home / ".claude").is_dir())
        self.assertEqual(home.stat().st_mode & 0o777, 0o700)

    def test_credential_sharing_requires_explicit_override(self):
        self.credentials.create("shared", "claude")
        self.agents.create("first", "claude", "shared")
        with self.assertRaisesRegex(ControlPlaneError, "already assigned"):
            self.agents.create("second", "claude", "shared")
        record = self.agents.create(
            "second", "claude", "shared", allow_shared_credential=True
        )
        self.assertEqual(record.credential_id, "shared")

    def test_granular_dependencies_are_atomic(self):
        self.create_agent()
        with self.assertRaisesRegex(ControlPlaneError, "edit requires inspect"):
            self.agents.change_rights("reviewer", "grant", ("edit",))
        self.assertEqual(self.agents.rights("reviewer"), ())
        rights = self.agents.change_rights(
            "reviewer", "grant", ("inspect", "edit")
        )
        self.assertEqual(rights, ("edit", "inspect"))

    def test_codex_requires_the_explicit_special_bundle(self):
        with self.assertRaisesRegex(ControlPlaneError, "Codex requires"):
            validate_rights("codex", ("inspect", "network"))
        self.assertEqual(
            validate_rights(
                "codex", ("inspect", "edit", "commandline", "network")
            ),
            ("commandline", "edit", "inspect", "network"),
        )

    def test_credential_delete_refuses_assigned_home(self):
        self.create_agent()
        with self.assertRaisesRegex(ControlPlaneError, "still assigned"):
            self.credentials.delete("reviewer", used_by=["reviewer"])
        self.assertTrue(self.credentials.home_path("reviewer").exists())

    def test_agent_delete_removes_only_instance_and_redacted_registry(self):
        self.create_agent()
        other = self.create_agent("other", "opencode")
        removed = self.agents.delete("reviewer")
        self.assertEqual(removed.agent_id, "reviewer")
        self.assertFalse((self.root / "runtime/agents/reviewer").exists())
        self.assertFalse((self.root / "runtime/control/agents/reviewer").exists())
        self.assertFalse((self.root / "runtime/registry/agents/reviewer").exists())
        self.assertTrue(self.agents.instance_path(other.agent_id).exists())
        self.assertTrue(self.credentials.home_path("reviewer").exists())

    def test_operator_only_agents_are_hidden_from_model_facing_lists(self):
        self.create_agent()
        self.agents.mark_operator_only("reviewer")
        self.assertEqual([item.agent_id for item in self.agents.list()], ["reviewer"])
        self.assertEqual(self.agents.list(include_operator_only=False), [])
        self.assertTrue(
            (self.root / "runtime/registry/agents/reviewer/.operator-only").is_file()
        )


class ArtifactAndJobTests(ControlPlaneTestBase):
    def test_artifact_is_checksum_verified_and_model_limited_to_text(self):
        metadata = self.artifacts.put_bytes(
            "handoff", b"review me\n", media_type="text/plain", producer="builder"
        )
        self.assertEqual(self.artifacts.model_view("handoff")["content"], "review me\n")
        content = self.root / "runtime/artifacts/handoff/content"
        content.write_bytes(b"changed")
        with self.assertRaisesRegex(ControlPlaneError, "checksum"):
            self.artifacts.read_bytes("handoff")
        self.assertEqual(metadata["size"], 10)

    def test_job_persists_result_as_immutable_artifact(self):
        self.create_agent()
        calls = []

        def start(agent_id):
            calls.append(("start", agent_id))

        def call(agent_id, operation, **kwargs):
            calls.append((operation, agent_id))
            return {"ok": True, "output": "done"}

        service = JobService(self.agents, self.jobs, self.artifacts, start, call)
        job = self.jobs.create("reviewer", "Review the patch")
        result = service.run(job["job_id"])
        self.assertEqual(result["state"], "succeeded")
        self.assertEqual(calls, [("start", "reviewer"), ("run", "reviewer")])
        artifact = self.artifacts.model_view(result["result_artifact"])
        self.assertIn('"output": "done"', artifact["content"])

    def test_job_dependency_failure_blocks_downstream_call(self):
        self.create_agent()
        service = JobService(
            self.agents,
            self.jobs,
            self.artifacts,
            lambda _agent: None,
            lambda *_args, **_kwargs: self.fail("downstream worker must not run"),
        )
        failed = self.jobs.create("reviewer", "first")
        self.jobs.update(failed["job_id"], state="failed")
        downstream = self.jobs.create(
            "reviewer", "second", dependencies=(failed["job_id"],)
        )
        result = service.run(downstream["job_id"])
        self.assertEqual(result["state"], "blocked")


class TeamTests(ControlPlaneTestBase):
    def manifest(self):
        return normalize_team_manifest(
            {
                "version": 1,
                "name": "dev-team",
                "orchestrator": "hermes",
                "agents": [
                    {
                        "id": "builder",
                        "engine": "opencode",
                        "role": "implementation",
                        "access": ["inspect", "edit", "agents-md"],
                    },
                    {
                        "id": "reviewer",
                        "engine": "claude",
                        "role": "review",
                        "access": ["inspect"],
                    },
                ],
                "workflow": [
                    {"id": "build", "agent": "builder", "prompt": "build"},
                    {
                        "id": "review",
                        "agent": "reviewer",
                        "prompt": "review",
                        "needs": ["build"],
                    },
                ],
            }
        )

    def test_team_apply_converges_isolated_agents_and_exact_rights(self):
        runtime = DockerAgentRuntime(self.root, self.agents, self.credentials)
        job_service = JobService(
            self.agents, self.jobs, self.artifacts, lambda _id: None, lambda *_a, **_k: {}
        )
        teams = TeamService(
            self.root,
            self.store,
            self.agents,
            self.credentials,
            runtime,
            self.jobs,
            job_service,
        )
        manifest = self.manifest()
        plan = teams.plan(manifest)
        self.assertEqual([item["action"] for item in plan].count("create"), 2)
        teams.apply(manifest)
        self.assertEqual(self.agents.rights("builder"), ("agents-md", "edit", "inspect"))
        self.assertEqual(self.agents.rights("reviewer"), ("inspect",))
        self.assertNotEqual(
            self.agents.get("builder").credential_id,
            self.agents.get("reviewer").credential_id,
        )

    def test_manifest_rejects_cycle_and_unknown_fields(self):
        value = {
            "version": 1,
            "name": "cycle",
            "agents": [{"id": "one", "engine": "claude"}],
            "workflow": [
                {"id": "a", "agent": "one", "prompt": "a", "needs": ["b"]},
                {"id": "b", "agent": "one", "prompt": "b", "needs": ["a"]},
            ],
        }
        with self.assertRaisesRegex(ControlPlaneError, "cycle"):
            normalize_team_manifest(value)
        value["unexpected"] = True
        with self.assertRaisesRegex(ControlPlaneError, "unknown team fields"):
            normalize_team_manifest(value)


if __name__ == "__main__":
    unittest.main()

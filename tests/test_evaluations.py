from __future__ import annotations

import http.client
import json
from pathlib import Path
import shutil
import tempfile
import threading
import unittest


REPOSITORY = Path(__file__).resolve().parents[1]
CONTAINER = REPOSITORY / "container"
import sys

sys.path.insert(0, str(CONTAINER))

from benchmark_ui import BenchmarkHttpServer
from control_plane.agents import AgentRegistry
from control_plane.artifacts import ArtifactRepository
from control_plane.credentials import CredentialBroker
from control_plane.domain import ControlPlaneError
from control_plane.evaluations.environment import DockerTrialEnvironment
from control_plane.evaluations.evaluators import EvaluatorRegistry
from control_plane.evaluations.manifests import load_evaluation_manifest
from control_plane.evaluations.repository import EvaluationRepository
from control_plane.evaluations.service import EvaluationService
from control_plane.evaluations.snapshots import SnapshotRepository
from control_plane.evaluations.telemetry import TelemetryNormalizer
from control_plane.jobs import JobRepository
from control_plane.storage import AtomicStore


class FakeJobService:
    def __init__(self, jobs: JobRepository, artifacts: ArtifactRepository):
        self.jobs = jobs
        self.artifacts = artifacts

    def run(self, job_id: str):
        job = self.jobs.get(job_id)
        result = {
            "ok": True,
            "engine": "claude",
            "duration_ms": 1250,
            "timed_out": False,
            "truncated": False,
            "policy": {"inspect": True},
            "enforcement": {"inspect": "Read,Glob,Grep"},
            "output": json.dumps(
                {
                    "result": "expected phrase",
                    "usage": {"input_tokens": 10, "output_tokens": 3},
                    "total_cost_usd": 1.25,
                }
            ),
        }
        artifact_id = f"{job_id}-result"
        self.artifacts.put_bytes(
            artifact_id,
            (json.dumps(result) + "\n").encode(),
            media_type="application/json",
            producer=job["agent_id"],
        )
        return self.jobs.update(
            job_id,
            state="succeeded",
            result_artifact=artifact_id,
        )

    def cancel(self, job_id: str):
        return self.jobs.update(job_id, state="cancelled")


class FakeTrialEnvironment:
    def __init__(self, agents: AgentRegistry):
        self.agents = agents
        self.created: list[str] = []

    def provision(self, **values):
        source = self.agents.get(values["source_agent_id"])
        agent_id = f"trial-{len(self.created) + 1}"
        self.agents.create(
            agent_id,
            source.engine,
            source.credential_id,
            allow_shared_credential=True,
        )
        self.agents.mark_operator_only(agent_id)
        self.agents.set_rights(agent_id, values["rights"])
        self.created.append(agent_id)
        return agent_id

    def cleanup(self, agent_id: str):
        self.agents.set_rights(agent_id, ())
        self.agents.delete(agent_id)


class EvaluationTestBase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        shutil.copytree(REPOSITORY / "worker-context", self.root / "worker-context")
        self.store = AtomicStore(self.root)
        self.credentials = CredentialBroker(self.root, self.store)
        self.credentials.create("source", "claude")
        self.agents = AgentRegistry(self.root, self.store, self.credentials)
        self.agents.create("source", "claude", "source")
        self.artifacts = ArtifactRepository(self.root, self.store)
        self.jobs = JobRepository(self.root, self.store)
        self.repository = EvaluationRepository(self.root, self.store)
        self.snapshots = SnapshotRepository(self.root, self.store, self.artifacts)
        self.environment = FakeTrialEnvironment(self.agents)
        self.service = EvaluationService(
            self.root,
            self.store,
            self.agents,
            self.artifacts,
            self.jobs,
            FakeJobService(self.jobs, self.artifacts),
            self.repository,
            self.snapshots,
            self.environment,
            EvaluatorRegistry(),
            TelemetryNormalizer(),
        )

    def tearDown(self):
        self.temporary.cleanup()

    def manifest(self, **changes):
        value = {
            "version": 1,
            "name": "generic-check",
            "targets": [{"agent": "source", "billing_mode": "subscription"}],
            "scenario": {"prompt": "Return the expected phrase."},
            "variants": [{"id": "read-only", "rights": ["inspect"]}],
            "execution": {"repetitions": 2, "order": "randomized", "seed": 9},
            "evaluator": {
                "type": "contains",
                "config": {"required": ["expected phrase"]},
            },
        }
        value.update(changes)
        path = self.root / "evaluation.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path


class ManifestAndTelemetryTests(EvaluationTestBase):
    def test_manifest_is_strict_and_trial_order_is_reproducible(self):
        first = load_evaluation_manifest(self.manifest())
        second = load_evaluation_manifest(self.manifest())
        self.assertEqual(first, second)
        broken = json.loads(self.manifest().read_text())
        broken["unexpected"] = True
        path = self.root / "broken.json"
        path.write_text(json.dumps(broken))
        with self.assertRaisesRegex(ControlPlaneError, "unknown evaluation fields"):
            load_evaluation_manifest(path)

    def test_subscription_cost_is_not_misrepresented(self):
        response = {
            "output": json.dumps(
                {
                    "result": "done",
                    "usage": {"input_tokens": 4, "output_tokens": 2},
                    "total_cost_usd": 9.99,
                }
            )
        }
        subscription = TelemetryNormalizer().normalize("claude", response, "subscription")
        api = TelemetryNormalizer().normalize("claude", response, "api")
        self.assertIsNone(subscription.reported_cost_usd)
        self.assertEqual(api.reported_cost_usd, 9.99)

    def test_snapshot_restore_rejects_symlink_sources(self):
        source = self.root / "source-dir"
        source.mkdir()
        (source / "safe.txt").write_text("safe")
        (source / "link").symlink_to(source / "safe.txt")
        with self.assertRaisesRegex(ControlPlaneError, "symlink"):
            self.snapshots.workspace_snapshot("eval-test", str(source))


class EvaluationServiceTests(EvaluationTestBase):
    def test_experiment_uses_ephemeral_agents_and_preserves_source_context(self):
        source_context = self.agents.instance_path("source") / "context" / "CLAUDE.md"
        before = source_context.read_bytes()
        created = self.service.create_manifest(self.manifest())
        result = self.service.run(created["experiment_id"])
        self.assertEqual(result["state"], "succeeded")
        self.assertEqual(result["progress"], {"done": 2, "total": 2})
        self.assertEqual(result["summary"]["groups"][0]["pass_rate"], 1.0)
        self.assertEqual(source_context.read_bytes(), before)
        self.assertEqual([item.agent_id for item in self.agents.list()], ["source"])
        self.assertEqual(
            [item.agent_id for item in self.agents.list(include_operator_only=False)],
            ["source"],
        )
        for trial in result["trials"]:
            self.assertEqual(trial["measurement"]["billing_mode"], "subscription")
            self.assertIsNone(trial["measurement"]["reported_cost_usd"])
            self.assertTrue(trial["evaluation"]["passed"])

    def test_export_includes_normalized_result(self):
        created = self.service.create_manifest(self.manifest())
        self.service.run(created["experiment_id"])
        destination = self.root / "export.json"
        self.service.export(created["experiment_id"], destination)
        value = json.loads(destination.read_text())
        self.assertEqual(
            value["trials"][0]["result"]["normalized"]["final_text"],
            "expected phrase",
        )


class BenchmarkUiTests(EvaluationTestBase):
    class Services:
        pass

    def test_ui_is_loopback_bearer_protected_and_rejects_foreign_origin(self):
        services = self.Services()
        services.agents = self.agents
        services.artifacts = self.artifacts
        services.evaluations = self.service
        server = BenchmarkHttpServer(
            self.root,
            services,
            0,
            "test-token",
            REPOSITORY / "benchmark-ui",
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1])
            connection.request("GET", "/")
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertIn("Content-Security-Policy", response.headers)
            response.read()
            connection.request("GET", "/api/v1/health")
            response = connection.getresponse()
            self.assertEqual(response.status, 401)
            response.read()
            connection.request(
                "GET",
                "/api/v1/health",
                headers={
                    "Authorization": "Bearer test-token",
                    "Origin": "https://evil.invalid",
                },
            )
            response = connection.getresponse()
            self.assertEqual(response.status, 403)
            response.read()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()

"""Thin CLI composition root for control-plane services."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

from .agents import AgentRegistry
from .artifacts import ArtifactRepository
from .credentials import CredentialBroker
from .domain import ControlPlaneError, ENGINES, RIGHTS
from .jobs import JobRepository, JobService
from .manifests import load_manifest
from .runtime import DockerAgentRuntime
from .storage import AtomicStore
from .teams import TeamService
from .evaluations.environment import DockerTrialEnvironment
from .evaluations.evaluators import EvaluatorRegistry
from .evaluations.repository import EvaluationRepository
from .evaluations.service import EvaluationService
from .evaluations.snapshots import SnapshotRepository
from .evaluations.telemetry import TelemetryNormalizer


class Services:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.store = AtomicStore(self.root)
        self.credentials = CredentialBroker(self.root, self.store)
        self.agents = AgentRegistry(self.root, self.store, self.credentials)
        self.artifacts = ArtifactRepository(self.root, self.store)
        self.jobs = JobRepository(self.root, self.store)
        self.runtime = DockerAgentRuntime(self.root, self.agents, self.credentials)
        socket_root = self.root / "runtime" / "sockets" / "agents"
        os.environ["HERMES_AGENT_SOCKET_ROOT"] = str(socket_root)
        container_root = self.root / "container"
        if str(container_root) not in sys.path:
            sys.path.insert(0, str(container_root))
        from worker_rpc import call_agent

        self.call_agent = call_agent
        self.job_service = JobService(
            self.agents,
            self.jobs,
            self.artifacts,
            self.runtime.start,
            self.call_agent,
        )
        self.teams = TeamService(
            self.root,
            self.store,
            self.agents,
            self.credentials,
            self.runtime,
            self.jobs,
            self.job_service,
        )
        self.evaluation_repository = EvaluationRepository(self.root, self.store)
        self.evaluation_snapshots = SnapshotRepository(
            self.root, self.store, self.artifacts
        )
        self.trial_environment = DockerTrialEnvironment(
            self.agents, self.runtime, self.evaluation_snapshots
        )
        self.evaluations = EvaluationService(
            self.root,
            self.store,
            self.agents,
            self.artifacts,
            self.jobs,
            self.job_service,
            self.evaluation_repository,
            self.evaluation_snapshots,
            self.trial_environment,
            EvaluatorRegistry(),
            TelemetryNormalizer(),
        )


def _print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _agent_explanation(engine: str) -> dict[str, Any]:
    common = {
        "inspect": "read/search surface",
        "edit": "workspace edits; requires inspect",
        "commandline": "shell; requires inspect and network",
        "network": "explicit acknowledgement of shell/provider egress coupling",
        "skills": "inject only reviewed instance SKILL.md bodies",
        "agents-md": "inject the protected instance AGENTS.md",
        "claude-md": "inject the protected instance CLAUDE.md",
    }
    if engine == "codex":
        adapter = {
            "classification": "special",
            "constraint": (
                "inspect, edit, commandline, and network are one explicit bundle; "
                "Codex read-only bubblewrap cannot initialize in the cap_drop:ALL worker, "
                "so the shell is enabled only with outer Docker isolation and full workspace access"
            ),
            "alternative": "Use Claude or OpenCode for a native inspect/edit/Bash split.",
        }
    else:
        adapter = {
            "classification": "native",
            "constraint": "inspect and edit use built-in file tools; Bash remains separately gated",
            "alternative": "Leave commandline and network disabled for file-only operation.",
        }
    return {"engine": engine, "rights": common, "adapter": adapter}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hermes-control")
    parser.add_argument("--root", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)

    agent = commands.add_parser("agent")
    agent_commands = agent.add_subparsers(dest="action", required=True)
    create = agent_commands.add_parser("create")
    create.add_argument("agent_id")
    create.add_argument("--engine", required=True, choices=ENGINES)
    create.add_argument("--credential")
    create.add_argument("--role", default="worker")
    create.add_argument("--share-credential", action="store_true")
    agent_commands.add_parser("list")
    for action in ("status", "explain", "rights", "reset", "build", "login", "logout", "start", "stop"):
        sub = agent_commands.add_parser(action)
        sub.add_argument("agent_id")
    delete = agent_commands.add_parser("delete")
    delete.add_argument("agent_id")
    delete.add_argument("--yes", action="store_true")
    delete.add_argument("--delete-credential", action="store_true")
    for action in ("grant", "revoke"):
        sub = agent_commands.add_parser(action)
        sub.add_argument("agent_id")
        sub.add_argument("rights", nargs="+")
    model = agent_commands.add_parser("model")
    model.add_argument("agent_id")
    model.add_argument("model", nargs="?")
    credential = agent_commands.add_parser("credential")
    credential.add_argument("agent_id")
    credential.add_argument("credential_id")
    credential.add_argument("--share", action="store_true")
    run = agent_commands.add_parser("run")
    run.add_argument("agent_id")
    run.add_argument("prompt", nargs="+")
    run.add_argument("--timeout", type=int, default=900)
    clone = agent_commands.add_parser("clone")
    clone.add_argument("agent_id")
    clone.add_argument("source")
    clone.add_argument("--branch")
    patch = agent_commands.add_parser("patch")
    patch.add_argument("agent_id")
    patch.add_argument("artifact_id")

    credential_parser = commands.add_parser("credential")
    credential_commands = credential_parser.add_subparsers(dest="action", required=True)
    credential_create = credential_commands.add_parser("create")
    credential_create.add_argument("credential_id")
    credential_create.add_argument("--engine", required=True, choices=ENGINES)
    credential_commands.add_parser("list")
    credential_status = credential_commands.add_parser("status")
    credential_status.add_argument("credential_id")
    credential_delete = credential_commands.add_parser("delete")
    credential_delete.add_argument("credential_id")
    credential_delete.add_argument("--yes", action="store_true")

    artifact = commands.add_parser("artifact")
    artifact_commands = artifact.add_subparsers(dest="action", required=True)
    artifact_commands.add_parser("list")
    artifact_show = artifact_commands.add_parser("show")
    artifact_show.add_argument("artifact_id")
    artifact_put = artifact_commands.add_parser("put")
    artifact_put.add_argument("artifact_id")
    artifact_put.add_argument("source", type=Path)
    artifact_put.add_argument("--media-type")
    artifact_get = artifact_commands.add_parser("get")
    artifact_get.add_argument("artifact_id")
    artifact_get.add_argument("destination", type=Path)

    job = commands.add_parser("job")
    job_commands = job.add_subparsers(dest="action", required=True)
    job_commands.add_parser("list")
    job_status = job_commands.add_parser("status")
    job_status.add_argument("job_id")
    job_cancel = job_commands.add_parser("cancel")
    job_cancel.add_argument("job_id")
    job_approve = job_commands.add_parser("approve")
    job_approve.add_argument("job_id")
    job_submit = job_commands.add_parser("submit")
    job_submit.add_argument("agent_id")
    job_submit.add_argument("prompt", nargs="+")
    job_submit.add_argument("--timeout", type=int, default=900)
    job_submit.add_argument("--depends", action="append", default=[])
    job_submit.add_argument("--artifact", action="append", default=[])
    job_submit.add_argument("--approval", action="store_true")
    job_submit.add_argument("--approved", action="store_true")
    job_submit.add_argument("--wait", action="store_true")
    job_run = job_commands.add_parser("_run")
    job_run.add_argument("job_id")

    team = commands.add_parser("team")
    team_commands = team.add_subparsers(dest="action", required=True)
    team_commands.add_parser("list")
    team_apply = team_commands.add_parser("apply")
    team_apply.add_argument("manifest", type=Path)
    team_apply.add_argument("--explain", action="store_true")
    team_status = team_commands.add_parser("status")
    team_status.add_argument("team_name")
    team_run = team_commands.add_parser("run")
    team_run.add_argument("team_name")
    team_run.add_argument("--approve-all", action="store_true")
    team_run.add_argument("--approve", action="append", default=[])
    team_reset = team_commands.add_parser("reset")
    team_reset.add_argument("team_name")
    team_reset.add_argument("--keep-running", action="store_true")
    team_commands.add_parser("example")

    benchmark = commands.add_parser("benchmark")
    benchmark_commands = benchmark.add_subparsers(dest="action", required=True)
    for action in ("validate", "plan", "create"):
        sub = benchmark_commands.add_parser(action)
        sub.add_argument("manifest", type=Path)
    benchmark_commands.add_parser("list")
    benchmark_status = benchmark_commands.add_parser("status")
    benchmark_status.add_argument("experiment_id")
    for action in ("run", "resume"):
        sub = benchmark_commands.add_parser(action)
        sub.add_argument("experiment_id")
        sub.add_argument("--wait", action="store_true")
    benchmark_cancel = benchmark_commands.add_parser("cancel")
    benchmark_cancel.add_argument("experiment_id")
    benchmark_export = benchmark_commands.add_parser("export")
    benchmark_export.add_argument("experiment_id")
    benchmark_export.add_argument("destination", type=Path)
    benchmark_commands.add_parser("example")
    benchmark_serve = benchmark_commands.add_parser("serve")
    benchmark_serve.add_argument("--port", type=int, default=8840)
    benchmark_serve.add_argument("--no-browser", action="store_true")
    for action in ("_run", "_resume"):
        sub = benchmark_commands.add_parser(action)
        sub.add_argument("experiment_id")
    return parser


def _assert_model_visible_agent(services: Services, agent_id: str) -> None:
    if (
        os.environ.get("HERMESCTL_CONTROL_ONLY") == "1"
        and services.agents.is_operator_only(agent_id)
    ):
        raise ControlPlaneError("agent is reserved for an operator evaluation trial")


def _agent_command(services: Services, args: argparse.Namespace) -> None:
    action = args.action
    if action == "create":
        credential_id = args.credential or args.agent_id
        services.credentials.create(credential_id, args.engine)
        record = services.agents.create(
            args.agent_id,
            args.engine,
            credential_id,
            role=args.role,
            allow_shared_credential=args.share_credential,
        )
        _print(services.agents.public_status(record.agent_id))
    elif action == "list":
        include_operator_only = os.environ.get("HERMESCTL_CONTROL_ONLY") != "1"
        _print([
            services.agents.public_status(item.agent_id)
            for item in services.agents.list(
                include_operator_only=include_operator_only
            )
        ])
    elif action == "status":
        _assert_model_visible_agent(services, args.agent_id)
        status = services.agents.public_status(args.agent_id)
        if status["socket_ready"]:
            try:
                status["runtime"] = services.call_agent(
                    args.agent_id, "status", timeout_seconds=60
                )
            except Exception as exc:
                status["runtime"] = {"ok": False, "error": str(exc)}
        _print(status)
    elif action == "explain":
        _assert_model_visible_agent(services, args.agent_id)
        record = services.agents.get(args.agent_id)
        _print({"agent_id": record.agent_id, **_agent_explanation(record.engine)})
    elif action == "rights":
        _assert_model_visible_agent(services, args.agent_id)
        record = services.agents.get(args.agent_id)
        _print(
            {
                "agent_id": record.agent_id,
                "rights": services.agents.rights(record.agent_id),
                **_agent_explanation(record.engine),
            }
        )
    elif action in {"grant", "revoke"}:
        _assert_model_visible_agent(services, args.agent_id)
        rights = services.agents.change_rights(args.agent_id, action, tuple(args.rights))
        _print({"agent_id": args.agent_id, "rights": rights})
    elif action == "reset":
        _assert_model_visible_agent(services, args.agent_id)
        _print({"agent_id": args.agent_id, "rights": services.agents.set_rights(args.agent_id, ())})
    elif action == "model":
        if args.model is None:
            _print({"agent_id": args.agent_id, "model": services.agents.model(args.agent_id)})
        else:
            value = None if args.model == "none" else args.model
            _print({"agent_id": args.agent_id, "model": services.agents.set_model(args.agent_id, value)})
    elif action == "credential":
        record = services.agents.assign_credential(
            args.agent_id, args.credential_id, allow_shared=args.share
        )
        _print(record.to_dict())
    elif action in {"build", "start", "stop", "login", "logout"}:
        getattr(services.runtime, action)(args.agent_id)
        _print({"ok": True, "agent_id": args.agent_id, "action": action})
    elif action == "delete":
        if not args.yes:
            raise ControlPlaneError("agent delete requires --yes")
        services.runtime.stop(args.agent_id)
        record = services.agents.delete(args.agent_id)
        credential_deleted = False
        if args.delete_credential:
            services.credentials.delete(
                record.credential_id,
                used_by=services.agents.agents_using_credential(record.credential_id),
            )
            credential_deleted = True
        _print(
            {
                "ok": True,
                "deleted_agent": record.agent_id,
                "deleted_credential": record.credential_id if credential_deleted else None,
                "logical_recovery": False,
                "secure_erasure": False,
            }
        )
    elif action == "run":
        services.runtime.start(args.agent_id)
        _print(
            services.call_agent(
                args.agent_id,
                "run",
                prompt=" ".join(args.prompt),
                timeout_seconds=args.timeout,
            )
        )
    elif action == "clone":
        services.runtime.clone_workspace(args.agent_id, args.source, args.branch)
        _print({"ok": True, "agent_id": args.agent_id, "workspace": str(services.agents.instance_path(args.agent_id) / 'workspace')})
    elif action == "patch":
        content = services.runtime.git_patch(args.agent_id)
        metadata = services.artifacts.put_bytes(
            args.artifact_id,
            content,
            media_type="application/x-git-patch",
            producer=args.agent_id,
        )
        _print(metadata)


def _credential_command(services: Services, args: argparse.Namespace) -> None:
    if args.action == "create":
        _print(services.credentials.create(args.credential_id, args.engine).to_dict())
    elif args.action == "list":
        _print([
            services.credentials.public_status(
                item.credential_id,
                used_by=services.agents.agents_using_credential(item.credential_id),
            )
            for item in services.credentials.list()
        ])
    elif args.action == "status":
        _print(
            services.credentials.public_status(
                args.credential_id,
                used_by=services.agents.agents_using_credential(args.credential_id),
            )
        )
    elif args.action == "delete":
        if not args.yes:
            raise ControlPlaneError("credential delete requires --yes")
        services.credentials.delete(
            args.credential_id,
            used_by=services.agents.agents_using_credential(args.credential_id),
        )
        _print(
            {
                "ok": True,
                "deleted": args.credential_id,
                "logical_recovery": False,
                "secure_erasure": False,
            }
        )


def _artifact_command(services: Services, args: argparse.Namespace) -> None:
    if args.action == "list":
        _print(services.artifacts.list())
    elif args.action == "show":
        _print(services.artifacts.metadata(args.artifact_id))
    elif args.action == "put":
        media_type = args.media_type or mimetypes.guess_type(args.source.name)[0] or "application/octet-stream"
        _print(
            services.artifacts.put_file(
                args.artifact_id, args.source, media_type=media_type, producer="operator"
            )
        )
    elif args.action == "get":
        metadata = services.artifacts.export(args.artifact_id, args.destination)
        _print({**metadata, "destination": str(args.destination.resolve())})


def _spawn_job_runner(root: Path, job_id: str) -> None:
    wrapper = root / "container" / "control_cli.py"
    subprocess.Popen(
        [sys.executable, str(wrapper), "--root", str(root), "job", "_run", job_id],
        cwd=root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )


def _job_command(services: Services, args: argparse.Namespace) -> None:
    if args.action == "list":
        _print(services.jobs.list())
    elif args.action == "status":
        _print(services.jobs.get(args.job_id))
    elif args.action == "cancel":
        _print(services.job_service.cancel(args.job_id))
    elif args.action == "approve":
        job = services.job_service.approve(args.job_id)
        if job["state"] not in {"succeeded", "failed", "cancelled", "blocked"}:
            _spawn_job_runner(services.root, job["job_id"])
        _print(job)
    elif args.action == "_run":
        _print(services.job_service.run(args.job_id))
    elif args.action == "submit":
        services.agents.get(args.agent_id)
        job = services.jobs.create(
            args.agent_id,
            " ".join(args.prompt),
            timeout_seconds=args.timeout,
            dependencies=tuple(args.depends),
            input_artifacts=tuple(args.artifact),
            approval_required=args.approval,
            approved=args.approved,
        )
        if args.wait:
            _print(services.job_service.run(job["job_id"]))
        elif args.approval and not args.approved:
            _print(job)
        else:
            _spawn_job_runner(services.root, job["job_id"])
            _print(job)


def _team_example() -> dict[str, Any]:
    return {
        "version": 1,
        "name": "software-team",
        "orchestrator": "hermes",
        "agents": [
            {
                "id": "builder",
                "engine": "codex",
                "role": "Implementierung",
                "credential": "builder",
                "model": None,
                "access": ["inspect", "edit", "commandline", "network", "skills", "agents-md"],
            },
            {
                "id": "reviewer",
                "engine": "claude",
                "role": "Read-only Review",
                "credential": "reviewer",
                "model": None,
                "access": ["inspect", "agents-md"],
            },
        ],
        "workflow": [
            {"id": "build", "agent": "builder", "prompt": "Implementiere die Aufgabe.", "needs": []},
            {"id": "review", "agent": "reviewer", "prompt": "Prüfe das Ergebnis.", "needs": ["build"]},
        ],
        "approvals": {"principle": "least-privilege"},
    }


def _team_command(services: Services, args: argparse.Namespace) -> None:
    if args.action == "list":
        _print(services.teams.list())
    elif args.action == "example":
        _print(_team_example())
    elif args.action == "apply":
        manifest = load_manifest(args.manifest)
        if args.explain:
            _print({"team": manifest["name"], "dry_run": True, "plan": services.teams.plan(manifest)})
        else:
            _print(services.teams.apply(manifest))
    elif args.action == "status":
        _print(services.teams.status(args.team_name))
    elif args.action == "run":
        _print(
            services.teams.run(
                args.team_name,
                approve_all=args.approve_all,
                approved_steps=tuple(args.approve),
            )
        )
    elif args.action == "reset":
        _print(services.teams.reset(args.team_name, stop=not args.keep_running))


def _spawn_evaluation_runner(root: Path, experiment_id: str, action: str) -> None:
    wrapper = root / "container" / "control_cli.py"
    subprocess.Popen(
        [
            sys.executable,
            str(wrapper),
            "--root",
            str(root),
            "benchmark",
            action,
            experiment_id,
        ],
        cwd=root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )


def _benchmark_example() -> dict[str, Any]:
    return {
        "version": 1,
        "name": "agent-comparison",
        "targets": [
            {"agent": "claude-test", "billing_mode": "subscription"},
            {"agent": "opencode-test", "billing_mode": "unknown"},
        ],
        "scenario": {"prompt_file": "prompt.md", "workspace": "workspace"},
        "variants": [
            {"id": "baseline", "rights": ["inspect"]},
            {
                "id": "with-context",
                "rights": ["inspect", "skills", "claude-md"],
                "context": {
                    "claude_md": "contexts/CLAUDE.md",
                    "skills": ["contexts/skills/example"],
                },
            },
        ],
        "execution": {
            "repetitions": 4,
            "warmup_repetitions": 1,
            "timeout_seconds": 180,
            "order": "randomized",
            "seed": 42,
            "max_parallel_per_credential": 1,
        },
        "evaluator": {
            "type": "contains",
            "config": {"required": ["expected phrase"]},
        },
        "metrics": ["correctness", "latency", "tokens", "reported-cost"],
    }


def _benchmark_command(services: Services, args: argparse.Namespace) -> None:
    action = args.action
    if action in {"validate", "plan"}:
        _print(services.evaluations.plan_manifest(args.manifest))
    elif action == "create":
        _print(services.evaluations.create_manifest(args.manifest))
    elif action == "list":
        _print(services.evaluations.list())
    elif action == "status":
        _print(services.evaluations.status(args.experiment_id))
    elif action in {"run", "resume"}:
        if args.wait:
            result = (
                services.evaluations.run(args.experiment_id)
                if action == "run"
                else services.evaluations.resume(args.experiment_id)
            )
            _print(result)
        else:
            _spawn_evaluation_runner(
                services.root,
                args.experiment_id,
                "_run" if action == "run" else "_resume",
            )
            _print(
                {
                    "ok": True,
                    "experiment_id": args.experiment_id,
                    "runner": "started",
                    "action": action,
                }
            )
    elif action == "_run":
        _print(services.evaluations.run(args.experiment_id))
    elif action == "_resume":
        _print(services.evaluations.resume(args.experiment_id))
    elif action == "cancel":
        _print(services.evaluations.cancel(args.experiment_id))
    elif action == "export":
        destination = services.evaluations.export(
            args.experiment_id, args.destination
        )
        _print({"ok": True, "destination": str(destination)})
    elif action == "example":
        _print(_benchmark_example())
    elif action == "serve":
        from benchmark_ui import run_server

        run_server(
            services.root,
            port=args.port,
            open_browser=not args.no_browser,
        )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        services = Services(args.root)
        if args.command == "agent":
            _agent_command(services, args)
        elif args.command == "credential":
            _credential_command(services, args)
        elif args.command == "artifact":
            _artifact_command(services, args)
        elif args.command == "job":
            _job_command(services, args)
        elif args.command == "team":
            _team_command(services, args)
        elif args.command == "benchmark":
            _benchmark_command(services, args)
        return 0
    except ControlPlaneError as exc:
        print(f"hermes-control: {exc}", file=sys.stderr)
        return 64
    except KeyboardInterrupt:
        print("hermes-control: interrupted", file=sys.stderr)
        return 130

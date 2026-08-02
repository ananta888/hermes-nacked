#!/usr/bin/env python3
"""Restricted direct client for arbitrary registered agent instances."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys

sys.path.insert(0, "/usr/local/lib")

from worker_rpc import call_agent


AGENT_ID = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
CONTROL_ROOT = Path(os.environ.get("HERMES_AGENT_CONTROL_ROOT", "/agent-control"))


def records() -> list[dict]:
    result = []
    if not CONTROL_ROOT.is_dir():
        return result
    for directory in sorted(CONTROL_ROOT.iterdir()):
        if (directory / ".operator-only").is_file():
            continue
        path = directory / "agent.json"
        rights_path = directory / "capabilities"
        if not AGENT_ID.fullmatch(directory.name) or not path.is_file() or path.is_symlink():
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            rights = rights_path.read_text(encoding="utf-8").split()
            result.append(
                {
                    "agent_id": directory.name,
                    "engine": value["engine"],
                    "role": value.get("role", "worker"),
                    "rights": sorted(rights),
                    "socket_ready": Path(
                        os.environ.get("HERMES_AGENT_SOCKET_ROOT", "/agent-sockets")
                    ).joinpath(directory.name, "worker.sock").is_socket(),
                }
            )
        except (OSError, json.JSONDecodeError, KeyError):
            continue
    return result


def require_agent(agent_id: str) -> dict:
    normalized = agent_id.strip().lower().replace("_", "-")
    for record in records():
        if record["agent_id"] == normalized:
            return record
    raise ValueError(f"unknown registered agent: {agent_id}")


def main() -> int:
    parser = argparse.ArgumentParser(prog="registered-agent")
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("list")
    status = actions.add_parser("status")
    status.add_argument("agent_id")
    run = actions.add_parser("run")
    run.add_argument("agent_id")
    run.add_argument("prompt", nargs="+")
    run.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()
    try:
        if args.action == "list":
            result = {"ok": True, "agents": records()}
        else:
            record = require_agent(args.agent_id)
            if args.action == "status":
                runtime = call_agent(record["agent_id"], "status", timeout_seconds=60)
                result = {"ok": bool(runtime.get("ok")), **record, "runtime": runtime}
            else:
                result = call_agent(
                    record["agent_id"],
                    "run",
                    prompt=" ".join(args.prompt),
                    timeout_seconds=args.timeout,
                )
    except Exception as exc:
        result = {"ok": False, "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())

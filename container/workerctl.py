#!/usr/bin/env python3
"""Restricted direct client for the isolated coding workers."""

from __future__ import annotations

import argparse
import json
import sys

sys.path.insert(0, "/usr/local/lib")

from worker_rpc import WORKERS, call_worker


def main() -> None:
    parser = argparse.ArgumentParser(prog="agent-worker")
    parser.add_argument("worker", choices=sorted(WORKERS))
    subparsers = parser.add_subparsers(dest="operation", required=True)
    subparsers.add_parser("status", help="Show CLI version, model, and login status")
    run_parser = subparsers.add_parser("run", help="Delegate one task")
    run_parser.add_argument("prompt", nargs="+")
    run_parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()

    try:
        if args.operation == "status":
            result = call_worker(args.worker, "status", timeout_seconds=60)
        else:
            result = call_worker(
                args.worker,
                "run",
                prompt=" ".join(args.prompt),
                timeout_seconds=args.timeout,
            )
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        raise SystemExit(1) from exc

    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()

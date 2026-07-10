from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from e2e_agent import cli as legacy_cli
from e2e_agent.workflow import WorkflowRuntime


def _build_v2_run_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="e2e-agent run")
    parser.add_argument("--app", required=True)
    parser.add_argument("--workflow", default="p0-web-regression")
    parser.add_argument("--env", default="local")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--inputs-json", default=None)
    return parser


def _load_inputs(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("--inputs-json must contain a JSON object")
    return payload


def _runtime_summary(result: dict[str, Any]) -> dict[str, Any]:
    manifest = result.get("artifact_manifest") or {}
    gates = result.get("gates") or {}
    pending_gates = [
        gate_id
        for gate_id, gate in gates.items()
        if str((gate or {}).get("status") or "") == "pending"
    ]
    artifacts = result.get("artifacts") or {}
    report = artifacts.get("test_report") or {}
    metadata = result.get("metadata") or {}
    return {
        "run_id": result.get("run_id"),
        "app_id": result.get("app_id"),
        "domain_id": result.get("domain_id"),
        "workflow_id": result.get("workflow_id"),
        "env": result.get("env"),
        "status": manifest.get("status") or report.get("status") or ("pending" if pending_gates else "completed"),
        "pending_gates": pending_gates,
        "artifact_names": sorted(artifacts),
        "artifact_count": len(manifest.get("artifacts") or []),
        "artifacts_dir": metadata.get("artifacts_dir"),
        "artifact_manifest": metadata.get("artifact_manifest_path"),
        "errors": result.get("errors") or [],
    }


def _runtime_exit_code(summary: dict[str, Any]) -> int:
    return 1 if str(summary.get("status")) in {"failed", "error"} else 0


def run_v2(argv: list[str]) -> int:
    args = _build_v2_run_parser().parse_args(argv)
    runtime = WorkflowRuntime()
    result = asyncio.run(
        runtime.run(
            app_path=Path(args.app),
            workflow=args.workflow,
            env=args.env,
            run_id=args.run_id,
            inputs=_load_inputs(args.inputs_json),
        )
    )
    summary = _runtime_summary(result)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return _runtime_exit_code(summary)


def main(argv: list[str] | None = None) -> int:
    actual = list(sys.argv[1:] if argv is None else argv)
    if actual and actual[0] == "run" and "--app" in actual[1:]:
        try:
            return run_v2(actual[1:])
        except Exception as exc:
            print(str(exc), file=sys.stderr)
            return 1
    return legacy_cli.main(actual)


if __name__ == "__main__":
    raise SystemExit(main())

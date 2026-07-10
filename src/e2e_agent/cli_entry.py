from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from e2e_agent import cli as legacy_cli
from e2e_agent.data import DataProviderRegistry
from e2e_agent.plugins import PluginManager
from e2e_agent.workflow import WorkflowRuntime
from e2e_agent.workflow.gates import decide_gate, load_gate_checkpoint


def _build_v2_run_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="e2e-agent run")
    parser.add_argument("--app", required=True)
    parser.add_argument("--workflow", default="p0-web-regression")
    parser.add_argument("--env", default="local")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--inputs-json", default=None)
    return parser


def _build_v2_gate_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="e2e-agent gate-v2")
    subparsers = parser.add_subparsers(dest="gate_command", required=True)
    for command in ("status", "resume"):
        command_parser = subparsers.add_parser(command)
        command_parser.add_argument("run_id")
        command_parser.add_argument("--checkpoint-dir", default=None)
    for command in ("approve", "reject"):
        command_parser = subparsers.add_parser(command)
        command_parser.add_argument("run_id")
        command_parser.add_argument("--operator", default="manual")
        command_parser.add_argument("--note", default=f"{command}d via v2 CLI")
        command_parser.add_argument("--checkpoint-dir", default=None)
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


def _checkpoint_dir(runtime: WorkflowRuntime, explicit: str | None) -> Path:
    return Path(explicit) if explicit else runtime.repo_root / ".local" / "e2e-agent" / "gate-checkpoints"


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


def gate_v2(argv: list[str]) -> int:
    args = _build_v2_gate_parser().parse_args(argv)
    runtime = WorkflowRuntime()
    directory = _checkpoint_dir(runtime, args.checkpoint_dir)
    if args.gate_command == "status":
        path, payload = load_gate_checkpoint(args.run_id, directory)
        state = payload.get("state") or {}
        gate_id = str(payload.get("pending_gate") or "")
        gate = (state.get("gates") or {}).get(gate_id) or {}
        response = {
            "run_id": args.run_id,
            "checkpoint": str(path),
            "checkpoint_status": payload.get("status"),
            "pending_gate": gate_id,
            "gate": gate,
            "decision": payload.get("decision"),
            "updated_at": payload.get("updated_at"),
        }
        print(json.dumps(response, ensure_ascii=False, indent=2))
        return 0
    if args.gate_command in {"approve", "reject"}:
        status = "approved" if args.gate_command == "approve" else "rejected"
        payload = decide_gate(
            args.run_id,
            directory,
            status=status,
            operator=args.operator,
            note=args.note,
        )
        print(
            json.dumps(
                {
                    "run_id": args.run_id,
                    "pending_gate": payload.get("pending_gate"),
                    "status": payload.get("status"),
                    "decision": payload.get("decision"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    result = asyncio.run(runtime.resume(run_id=args.run_id, checkpoint_dir=directory))
    summary = _runtime_summary(result)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return _runtime_exit_code(summary)


def list_plugins() -> int:
    root = Path(__file__).resolve().parents[2]
    manifests = PluginManager(root / "plugins").list()
    payload = [
        {
            "id": item.id,
            "version": item.version,
            "kind": item.kind,
            "implementation": item.implementation_id,
            "runtime": item.runtime_type,
            "path": str(item.path),
        }
        for item in manifests
    ]
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def list_data_providers() -> int:
    print(json.dumps(DataProviderRegistry().list_names(), ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    actual = list(sys.argv[1:] if argv is None else argv)
    try:
        if actual and actual[0] == "run" and "--app" in actual[1:]:
            return run_v2(actual[1:])
        if actual and actual[0] == "gate-v2":
            return gate_v2(actual[1:])
        if actual == ["plugins"] or actual == ["plugins", "--json"]:
            return list_plugins()
        if actual == ["data-providers"] or actual == ["data-providers", "--json"]:
            return list_data_providers()
        return legacy_cli.main(actual)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from e2e_agent import cli as legacy_cli
from e2e_agent.config.yaml_loader import load_yaml_file
from e2e_agent.data import DataProviderRegistry
from e2e_agent.plugins import PluginManager
from e2e_agent.workflow import WorkflowRuntime
from e2e_agent.workflow.gates import decide_gate, load_gate_checkpoint


_TOP_LEVEL_HELP = """usage: e2e-agent <command> [options]

Commands:
  run              Run a v2 App Pack workflow or a legacy product-input workflow
  gate             Inspect, decide, or resume a v1/v2 gate checkpoint
  doctor           Inspect local dependencies and repository inputs
  domains          List Domain Packs
  workflows        List Workflow definitions
  runners          List Runner manifests
  plugins          List production or explicitly selected plugins
  data-providers   List test data providers
  plugin create    Scaffold a plugin package
  validate         Validate App, Domain, or Workflow configuration
  reports serve    Serve generated reports
  healing apply    Apply an approved legacy healing event
  acceptance       Run the repository acceptance matrix

Compatibility:
  gate-v2          Deprecated alias for `gate` v2 checkpoint operations
"""


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _resolve_repo_paths(values: list[str] | None) -> list[Path]:
    root = _repo_root()
    result: list[Path] = []
    for value in values or []:
        path = Path(value)
        result.append(path if path.is_absolute() else root / path)
    return result


def _build_v2_run_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="e2e-agent run")
    parser.add_argument("--app", required=True)
    parser.add_argument("--workflow", default="p0-web-regression")
    parser.add_argument("--env", default="local")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--inputs-json", default=None)
    parser.add_argument(
        "--plugin-dir",
        action="append",
        default=[],
        help="Additional plugin discovery directory; may be repeated.",
    )
    return parser


def _build_v2_gate_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="e2e-agent gate")
    subparsers = parser.add_subparsers(dest="gate_command", required=True)
    for command in ("status", "resume"):
        command_parser = subparsers.add_parser(command)
        command_parser.add_argument("run_id")
        command_parser.add_argument("--checkpoint-dir", default=None)
    for command in ("approve", "reject"):
        command_parser = subparsers.add_parser(command)
        command_parser.add_argument("run_id")
        command_parser.add_argument("--operator", default="manual")
        command_parser.add_argument("--note", default=f"{command}d via CLI")
        command_parser.add_argument("--checkpoint-dir", default=None)
    return parser


def _build_plugins_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="e2e-agent plugins")
    parser.add_argument("--path", action="append", default=[], help="Plugin discovery directory; may be repeated.")
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser


def _build_plugin_create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="e2e-agent plugin create")
    parser.add_argument("plugin_id")
    parser.add_argument("--runtime", choices=["python", "node"], default="python")
    parser.add_argument("--root", default=None)
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


def _option_value(argv: list[str], option: str) -> str | None:
    try:
        index = argv.index(option)
    except ValueError:
        return None
    return argv[index + 1] if index + 1 < len(argv) else None


def _v2_checkpoint_exists(argv: list[str]) -> bool:
    if len(argv) < 2:
        return False
    run_id = argv[1]
    explicit = _option_value(argv, "--checkpoint-dir")
    directory = Path(explicit) if explicit else _repo_root() / ".local" / "e2e-agent" / "gate-checkpoints"
    return (directory / f"{run_id}.v2.json").exists()


def _use_v2_gate(argv: list[str]) -> bool:
    if not argv:
        return False
    command = argv[0]
    if command == "summary" or "--gate" in argv:
        return False
    if command in {"approve", "reject"}:
        return True
    if "--checkpoint-dir" in argv:
        return True
    return command in {"status", "resume"} and _v2_checkpoint_exists(argv)


def run_v2(argv: list[str]) -> int:
    args = _build_v2_run_parser().parse_args(argv)
    runtime = WorkflowRuntime(plugin_roots=_resolve_repo_paths(args.plugin_dir))
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


def gate(argv: list[str]) -> int:
    if _use_v2_gate(argv):
        return gate_v2(argv)
    return legacy_cli.main(["gate", *argv])


def list_plugins(argv: list[str]) -> int:
    args = _build_plugins_parser().parse_args(argv)
    roots = _resolve_repo_paths(args.path) if args.path else [_repo_root() / "plugins"]
    manifests = PluginManager(roots).list()
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


def list_runners() -> int:
    payload = [load_yaml_file(path) for path in sorted((_repo_root() / "runners").glob("*.yaml"))]
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def list_data_providers() -> int:
    print(json.dumps(DataProviderRegistry().list_names(), ensure_ascii=False, indent=2))
    return 0


def create_plugin(argv: list[str]) -> int:
    args = _build_plugin_create_parser().parse_args(argv)
    plugin_id = str(args.plugin_id)
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", plugin_id):
        raise ValueError("plugin id must match [a-z0-9][a-z0-9-]*")
    plugins_root = Path(args.root) if args.root else _repo_root() / "plugins"
    plugin_root = plugins_root / plugin_id
    if plugin_root.exists():
        raise FileExistsError(f"Plugin already exists: {plugin_root}")
    plugin_root.mkdir(parents=True)
    entry = "plugin.py" if args.runtime == "python" else "plugin.js"
    manifest = f'''id: {plugin_id}
version: "0.1.0"
kind: node
description: "Generated plugin {plugin_id}."
runtime:
  type: {args.runtime}
  entry: {entry}
  timeout_seconds: 300
contracts:
  input: []
  output: []
capabilities: []
'''
    (plugin_root / "plugin.yaml").write_text(manifest, encoding="utf-8")
    if args.runtime == "python":
        source = '''from __future__ import annotations
import json
import sys

payload = json.loads(sys.stdin.read() or "{}")
print(json.dumps({"status": "success", "outputs": {}, "metrics": {}, "warnings": []}))
'''
    else:
        source = '''let data = "";
process.stdin.on("data", chunk => data += chunk);
process.stdin.on("end", () => console.log(JSON.stringify({status: "success", outputs: {}, metrics: {}, warnings: []})));
'''
    (plugin_root / entry).write_text(source, encoding="utf-8")
    print(json.dumps({"id": plugin_id, "path": str(plugin_root), "runtime": args.runtime}, ensure_ascii=False, indent=2))
    return 0


def run_acceptance() -> int:
    completed = subprocess.run(
        [sys.executable, str(_repo_root() / "tools" / "acceptance_matrix.py")],
        cwd=str(_repo_root()),
        check=False,
    )
    return int(completed.returncode)


def main(argv: list[str] | None = None) -> int:
    actual = list(sys.argv[1:] if argv is None else argv)
    if not actual or actual in (["-h"], ["--help"]):
        print(_TOP_LEVEL_HELP)
        return 0
    try:
        if actual[0] == "run" and "--app" in actual[1:]:
            return run_v2(actual[1:])
        if actual[0] == "gate":
            return gate(actual[1:])
        if actual[0] == "gate-v2":
            print("Deprecated: use `e2e-agent gate ...` instead of `gate-v2`.", file=sys.stderr)
            return gate_v2(actual[1:])
        if actual[0] == "plugins":
            return list_plugins(actual[1:])
        if actual == ["runners"] or actual == ["runners", "--json"]:
            return list_runners()
        if actual == ["data-providers"] or actual == ["data-providers", "--json"]:
            return list_data_providers()
        if len(actual) >= 2 and actual[:2] == ["plugin", "create"]:
            return create_plugin(actual[2:])
        if actual == ["acceptance"]:
            return run_acceptance()
        return legacy_cli.main(actual)
    except SystemExit as exc:
        return int(exc.code or 0)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

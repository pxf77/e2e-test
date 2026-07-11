from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from e2e_agent import cli as legacy_cli
from e2e_agent.data import DataProviderRegistry
from e2e_agent.plugins import PluginManager
from e2e_agent.workflow import WorkflowRuntime
from e2e_agent.workflow.gates import decide_gate, gate_checkpoint_path, load_gate_checkpoint

_REPO_ROOT = Path(__file__).resolve().parents[3]

_ROOT_HELP = """usage: e2e-agent <command> [options]

Commands:
  run               Run an App Pack workflow or legacy product-input workflow
  gate              Inspect, decide, or resume v1/v2 gates
  gate-v2           Deprecated alias for `gate`
  doctor            Inspect local environment
  domains            List Domain Packs
  workflows          List Workflow definitions
  runners            List execution runners
  plugins            List discovered plugins
  data-providers     List test-data providers
  plugin create      Scaffold a plugin
  acceptance         Run the release acceptance matrix
  validate           Validate app, domain, or workflow configuration
  reports            Serve generated reports
  healing            Apply a reviewed healing event

Use `e2e-agent <command> --help` for command-specific options.
"""


def _build_v2_run_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="e2e-agent run")
    parser.add_argument("--app", required=True)
    parser.add_argument("--workflow", default="p0-web-regression")
    parser.add_argument("--env", default="local")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--inputs-json", default=None)
    return parser


def _build_gate_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="e2e-agent gate")
    subparsers = parser.add_subparsers(dest="gate_command", required=True)
    for command in ("status", "summary", "resume"):
        command_parser = subparsers.add_parser(command)
        command_parser.add_argument("run_id")
        command_parser.add_argument("--checkpoint-dir", default=None)
        if command == "resume":
            command_parser.add_argument("--thread-id", default=None)
    for command in ("approve", "reject"):
        command_parser = subparsers.add_parser(command)
        command_parser.add_argument("run_id")
        command_parser.add_argument("--gate", dest="gate_name", choices=["r1", "r2", "r3", "r4"], default=None)
        command_parser.add_argument("--operator", default="manual")
        command_parser.add_argument("--note", default=f"{command}d via CLI")
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


def _v2_checkpoint_dir(explicit: str | None) -> Path:
    return Path(explicit) if explicit else _REPO_ROOT / ".local" / "e2e-agent" / "gate-checkpoints"


def _is_v2_checkpoint(run_id: str, explicit_dir: str | None) -> bool:
    return gate_checkpoint_path(run_id, _v2_checkpoint_dir(explicit_dir)).exists()


def _v2_gate(args: argparse.Namespace) -> int:
    runtime = WorkflowRuntime(repo_root=_REPO_ROOT)
    directory = _v2_checkpoint_dir(args.checkpoint_dir)
    if args.gate_command in {"status", "summary"}:
        path, payload = load_gate_checkpoint(args.run_id, directory)
        state = payload.get("state") or {}
        gate_id = str(payload.get("pending_gate") or "")
        gate = (state.get("gates") or {}).get(gate_id) or {}
        response = {
            "version": "v2",
            "run_id": args.run_id,
            "checkpoint": str(path),
            "checkpoint_status": payload.get("status"),
            "pending_gate": gate_id,
            "gate": gate,
            "decision": payload.get("decision"),
            "updated_at": payload.get("updated_at"),
        }
        if args.gate_command == "summary":
            response = {
                "version": "v2",
                "run_id": args.run_id,
                "pending_gate": gate_id,
                "checkpoint_status": payload.get("status"),
                "gate_status": gate.get("status"),
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
                    "version": "v2",
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


def _v1_gate(args: argparse.Namespace) -> int:
    if args.checkpoint_dir:
        raise ValueError("--checkpoint-dir is supported for v2 gates; set E2E_AGENT_GATE_CHECKPOINT_DIR for v1")
    if args.gate_command == "status":
        print(json.dumps(legacy_cli.gate_status(args.run_id), ensure_ascii=False, indent=2))
        return 0
    if args.gate_command == "summary":
        print(json.dumps(legacy_cli.gate_summary(args.run_id), ensure_ascii=False, indent=2))
        return 0
    if args.gate_command == "approve":
        if not args.gate_name:
            raise ValueError("v1 gate approve requires --gate r1|r2|r3|r4")
        print(legacy_cli.gate_approve(args.run_id, args.gate_name, args.operator, args.note))
        return 0
    if args.gate_command == "reject":
        if not args.gate_name:
            raise ValueError("v1 gate reject requires --gate r1|r2|r3|r4")
        print(legacy_cli.gate_reject(args.run_id, args.gate_name, args.operator, args.note))
        return 0
    print(legacy_cli.gate_resume(args.run_id, args.thread_id))
    return 0


def gate(argv: list[str], *, force_v2: bool = False) -> int:
    args = _build_gate_parser().parse_args(argv)
    use_v2 = force_v2 or _is_v2_checkpoint(args.run_id, args.checkpoint_dir)
    return _v2_gate(args) if use_v2 else _v1_gate(args)


def _plugin_manager(paths: list[str] | None = None) -> PluginManager:
    roots = [Path(path) for path in paths or []]
    if not roots:
        roots = [_REPO_ROOT / "plugins"]
    return PluginManager(roots)


def list_plugins(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="e2e-agent plugins")
    parser.add_argument("--path", action="append", default=[])
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    manifests = _plugin_manager(args.path).list()
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
    if args.as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for item in payload:
            print(f"{item['id']}@{item['version']} ({item['runtime']})")
    return 0


def list_data_providers(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="e2e-agent data-providers")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    providers = DataProviderRegistry().list_names()
    print(json.dumps(providers, ensure_ascii=False, indent=2) if args.as_json else "\n".join(providers))
    return 0


def list_runners(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="e2e-agent runners")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    payload = []
    runners_root = _REPO_ROOT / "runners"
    if runners_root.exists():
        from e2e_agent.config.yaml_loader import load_yaml_file

        for path in sorted(runners_root.glob("*.yaml")):
            manifest = load_yaml_file(path)
            payload.append({"id": manifest.get("id"), "version": manifest.get("version"), "path": str(path)})
    if args.as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for item in payload:
            print(f"{item['id']}@{item['version']}")
    return 0


def plugin_create(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="e2e-agent plugin create")
    parser.add_argument("plugin_id")
    parser.add_argument("--runtime", choices=["python", "node"], default="python")
    parser.add_argument("--output-dir", default="plugins")
    args = parser.parse_args(argv)
    target = Path(args.output_dir) / args.plugin_id
    if target.exists():
        raise FileExistsError(f"Plugin directory already exists: {target}")
    target.mkdir(parents=True)
    entry = "plugin.py" if args.runtime == "python" else "plugin.js"
    manifest = f'''id: {args.plugin_id}\nversion: "0.1.0"\nkind: node\ndescription: "{args.plugin_id} plugin"\nruntime:\n  type: {args.runtime}\n  entry: {entry}\ncontracts:\n  input: []\n  output: []\n'''
    (target / "plugin.yaml").write_text(manifest, encoding="utf-8")
    if args.runtime == "python":
        body = '''from __future__ import annotations\n\nimport json\nimport sys\n\n\ndef main() -> int:\n    payload = json.load(sys.stdin)\n    json.dump({"status": "success", "outputs": {}, "metrics": {"received": bool(payload)}}, sys.stdout)\n    return 0\n\n\nif __name__ == "__main__":\n    raise SystemExit(main())\n'''
    else:
        body = '''let input = "";\nprocess.stdin.on("data", chunk => input += chunk);\nprocess.stdin.on("end", () => {\n  const payload = input ? JSON.parse(input) : {};\n  process.stdout.write(JSON.stringify({status: "success", outputs: {}, metrics: {received: !!payload}}));\n});\n'''
    (target / entry).write_text(body, encoding="utf-8")
    print(str(target))
    return 0


def acceptance() -> int:
    completed = subprocess.run([sys.executable, str(_REPO_ROOT / "tools" / "acceptance_matrix.py")], cwd=_REPO_ROOT, check=False)
    return int(completed.returncode)


def main(argv: list[str] | None = None) -> int:
    actual = list(sys.argv[1:] if argv is None else argv)
    try:
        if not actual or actual[0] in {"-h", "--help"}:
            print(_ROOT_HELP)
            return 0
        if actual[0] == "run" and "--app" in actual[1:]:
            return run_v2(actual[1:])
        if actual[0] == "gate":
            return gate(actual[1:])
        if actual[0] == "gate-v2":
            print("Deprecated: use `e2e-agent gate ...`", file=sys.stderr)
            return gate(actual[1:], force_v2=True)
        if actual[0] == "plugins":
            return list_plugins(actual[1:])
        if actual[0] == "data-providers":
            return list_data_providers(actual[1:])
        if actual[0] == "runners":
            return list_runners(actual[1:])
        if actual[:2] == ["plugin", "create"]:
            return plugin_create(actual[2:])
        if actual == ["acceptance"]:
            return acceptance()
        return legacy_cli.main(actual)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

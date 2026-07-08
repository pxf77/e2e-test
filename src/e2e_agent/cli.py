from __future__ import annotations

import asyncio
import argparse
import functools
import http.server
import json
import os
import shutil
import socketserver
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from e2e_agent.core.healing_apply import apply_healing_event
from e2e_agent.graph.gates import get_gate_checkpoint_path
from e2e_agent.graph.graph import build_graph

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODEL_KEY_ENV_NAMES = ("OPENAI_API_KEY", "GEMINI_API_KEY", "DEEPSEEK_API_KEY")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _load_checkpoint(run_id: str) -> tuple[Path, dict[str, Any]]:
    checkpoint_path = get_gate_checkpoint_path(run_id)
    if not checkpoint_path.exists():
        raise FileNotFoundError(f"Gate checkpoint not found: {checkpoint_path}")
    return checkpoint_path, json.loads(checkpoint_path.read_text(encoding="utf-8"))


def _write_checkpoint(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def gate_approve(run_id: str, gate_name: str, operator: str, note: str) -> str:
    checkpoint_path, payload = _load_checkpoint(run_id)
    state = payload.get("state", {})
    state[f"{gate_name}_gate"] = {
        "status": "approved",
        "operator": operator,
        "timestamp": _utc_now(),
        "note": note,
    }
    payload["pending_gate"] = gate_name
    payload["updated_at"] = _utc_now()
    payload["state"] = state
    _write_checkpoint(checkpoint_path, payload)
    return f"Approved {gate_name} for {run_id}"


def gate_reject(run_id: str, gate_name: str, operator: str, note: str) -> str:
    checkpoint_path, payload = _load_checkpoint(run_id)
    state = payload.get("state", {})
    state[f"{gate_name}_gate"] = {
        "status": "rejected",
        "operator": operator,
        "timestamp": _utc_now(),
        "note": note,
    }
    payload["pending_gate"] = gate_name
    payload["updated_at"] = _utc_now()
    payload["state"] = state
    _write_checkpoint(checkpoint_path, payload)
    return f"Rejected {gate_name} for {run_id}"


def gate_resume(run_id: str, thread_id: str | None) -> str:
    checkpoint_path, payload = _load_checkpoint(run_id)
    state = payload.get("state", {})
    app = build_graph(":memory:")
    result = asyncio.run(
        app.ainvoke(
            state,
            config={"configurable": {"thread_id": thread_id or run_id}},
        )
    )
    payload["state"] = result
    payload["updated_at"] = _utc_now()
    payload["last_resume_thread_id"] = thread_id or run_id
    _write_checkpoint(checkpoint_path, payload)
    return f"Resumed {run_id}"


def gate_status(run_id: str) -> dict[str, Any]:
    _, payload = _load_checkpoint(run_id)
    state = payload.get("state", {}) if isinstance(payload.get("state"), dict) else {}
    gates = {
        gate_name: state.get(f"{gate_name}_gate", {"status": "pending"})
        for gate_name in ("r1", "r2", "r3", "r4")
    }
    return {
        "run_id": run_id,
        "pending_gate": payload.get("pending_gate"),
        "updated_at": payload.get("updated_at"),
        "gates": gates,
    }


def gate_summary(run_id: str) -> dict[str, Any]:
    status = gate_status(run_id)
    counts = {"approved": 0, "pending": 0, "rejected": 0}
    for gate in status["gates"].values():
        gate_status_value = str((gate or {}).get("status") or "pending")
        if gate_status_value not in counts:
            gate_status_value = "pending"
        counts[gate_status_value] += 1
    blocking_pending = [
        gate_name
        for gate_name in ("r1", "r2", "r3")
        if str((status["gates"].get(gate_name) or {}).get("status") or "pending") == "pending"
    ]
    return {
        "run_id": run_id,
        "pending_gate": status["pending_gate"],
        "counts": counts,
        "blocking_pending_gates": blocking_pending,
    }


def _display_path(path: Path) -> str:
    try:
        return path.relative_to(_REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


def _safe_read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def list_product_inputs() -> list[dict[str, str]]:
    products_root = _REPO_ROOT / "products"
    if not products_root.exists():
        return []
    result: list[dict[str, str]] = []
    for path in sorted(products_root.glob("**/product-input.json")):
        parts = set(path.parts)
        if ".assets" in path.name or any(part.endswith(".assets") for part in parts):
            continue
        payload = _safe_read_json(path)
        product_id = str(payload.get("product_id") or path.parent.parent.name)
        product_name = str(payload.get("product_name") or product_id)
        result.append(
            {
                "product_id": product_id,
                "product_name": product_name,
                "path": _display_path(path),
            }
        )
    return result


def doctor_status() -> dict[str, Any]:
    python_ok = sys.version_info >= (3, 12)
    node_path = shutil.which("node")
    npm_path = shutil.which("npm")
    uv_path = shutil.which("uv")
    package_lock = _REPO_ROOT / "package-lock.json"
    pyproject = _REPO_ROOT / "pyproject.toml"
    return {
        "repo_root": str(_REPO_ROOT),
        "checks": {
            "python": {
                "ok": python_ok,
                "version": ".".join(str(part) for part in sys.version_info[:3]),
                "executable": sys.executable,
                "required": ">=3.12",
            },
            "uv": {"ok": bool(uv_path), "path": uv_path},
            "node": {"ok": bool(node_path), "path": node_path},
            "npm": {"ok": bool(npm_path), "path": npm_path},
            "pyproject": {"ok": pyproject.exists(), "path": str(pyproject)},
            "package_lock": {"ok": package_lock.exists(), "path": str(package_lock)},
        },
        "model_keys": {name: bool(os.environ.get(name)) for name in _MODEL_KEY_ENV_NAMES},
        "product_inputs": list_product_inputs(),
    }


def _print_doctor_text(status: dict[str, Any]) -> None:
    checks = status["checks"]
    print("E2E Agent environment")
    print(f"repo_root: {status['repo_root']}")
    for name, check in checks.items():
        marker = "OK" if check.get("ok") else "MISSING"
        detail = check.get("version") or check.get("path") or ""
        if name == "python" and not check.get("ok"):
            detail = f"{detail} (required {check.get('required')})"
        print(f"[{marker}] {name}: {detail}")
    print("model keys:")
    for name, present in status["model_keys"].items():
        print(f"[{'OK' if present else 'MISSING'}] {name}")
    print("product inputs:")
    for index, item in enumerate(status["product_inputs"], start=1):
        print(f"{index}. {item['path']} ({item['product_id']})")


def run_full_workflow(product_input: Path, extra_env: dict[str, str] | None = None) -> int:
    resolved_input = product_input if product_input.is_absolute() else _REPO_ROOT / product_input
    if not resolved_input.exists():
        raise FileNotFoundError(f"Product input not found: {resolved_input}")
    local_root = _REPO_ROOT / ".local" / "e2e-agent"
    gate_dir = local_root / "gate-checkpoints"
    log_dir = local_root / "logs"
    gate_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.setdefault("E2E_AGENT_GATE_CHECKPOINT_DIR", str(gate_dir))
    env.setdefault("AGENT3_HEADLESS", "1")
    env.setdefault("PLAYWRIGHT_HTML_OPEN", "never")
    if extra_env:
        env.update(extra_env)
    command = [
        sys.executable,
        str(_REPO_ROOT / "tools" / "run_full_workflow.py"),
        "--product-input",
        str(resolved_input),
    ]
    completed = subprocess.run(command, cwd=_REPO_ROOT, env=env, check=False)
    return int(completed.returncode)


def _serve_static_reports(root: Path, host: str, port: int) -> None:
    if not root.exists():
        raise FileNotFoundError(f"Static root not found: {root}")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    with socketserver.TCPServer((host, port), handler) as httpd:
        print(f"Serving reports from {root}")
        print(f"Open http://{host}:{port}/ and browse to *.assets/runs/<run_id>/report.html")
        httpd.serve_forever()


def _find_run_dir(run_id: str) -> Path:
    if Path(run_id).name != run_id or any(token in run_id for token in ("/", "\\")):
        raise ValueError(f"Unsafe run id: {run_id}")
    matches = sorted((_REPO_ROOT / "products").glob(f"**/runs/{run_id}"))
    if not matches:
        raise FileNotFoundError(f"Run dir not found: {run_id}")
    if len(matches) > 1:
        raise ValueError(f"Multiple run dirs found for {run_id}: {matches}")
    return matches[0]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="e2e-agent")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor_parser = subparsers.add_parser("doctor")
    doctor_parser.add_argument("--json", action="store_true", dest="as_json")

    products_parser = subparsers.add_parser("products")
    products_parser.add_argument("--json", action="store_true", dest="as_json")

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--product-input", required=True)

    reports_parser = subparsers.add_parser("reports")
    reports_subparsers = reports_parser.add_subparsers(dest="reports_command", required=True)
    serve_parser = reports_subparsers.add_parser("serve")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8080)

    gate_parser = subparsers.add_parser("gate")
    gate_subparsers = gate_parser.add_subparsers(dest="gate_command", required=True)

    for command_name in ("approve", "reject"):
        cmd_parser = gate_subparsers.add_parser(command_name)
        cmd_parser.add_argument("run_id")
        cmd_parser.add_argument("--gate", dest="gate_name", choices=["r1", "r2", "r3", "r4"], required=True)
        cmd_parser.add_argument("--operator", default="manual")
        cmd_parser.add_argument("--note", default=f"{command_name}d via CLI")

    resume_parser = gate_subparsers.add_parser("resume")
    resume_parser.add_argument("run_id")
    resume_parser.add_argument("--thread-id", default=None)

    status_parser = gate_subparsers.add_parser("status")
    status_parser.add_argument("run_id")

    summary_parser = gate_subparsers.add_parser("summary")
    summary_parser.add_argument("run_id")

    healing_parser = subparsers.add_parser("healing")
    healing_subparsers = healing_parser.add_subparsers(dest="healing_command", required=True)
    apply_parser = healing_subparsers.add_parser("apply")
    apply_parser.add_argument("--run-id", required=True)
    apply_parser.add_argument("--event-id", required=True)
    apply_parser.add_argument("--evidence-file", required=True)
    apply_parser.add_argument("--operator", default="manual")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "doctor":
            status = doctor_status()
            if args.as_json:
                print(json.dumps(status, ensure_ascii=False))
            else:
                _print_doctor_text(status)
        elif args.command == "products":
            products = list_product_inputs()
            if args.as_json:
                print(json.dumps(products, ensure_ascii=False))
            else:
                if not products:
                    print("No product-input.json found under products/")
                for index, item in enumerate(products, start=1):
                    print(f"{index}. {item['path']} ({item['product_id']} - {item['product_name']})")
        elif args.command == "run":
            return run_full_workflow(Path(args.product_input))
        elif args.command == "reports":
            if args.reports_command == "serve":
                _serve_static_reports(_REPO_ROOT / "products", args.host, args.port)
            else:
                parser.error(f"Unsupported reports command: {args.reports_command}")
                return 2
        elif args.command == "gate":
            if args.gate_command == "approve":
                print(gate_approve(args.run_id, args.gate_name, args.operator, args.note))
            elif args.gate_command == "reject":
                print(gate_reject(args.run_id, args.gate_name, args.operator, args.note))
            elif args.gate_command == "resume":
                print(gate_resume(args.run_id, args.thread_id))
            elif args.gate_command == "status":
                print(json.dumps(gate_status(args.run_id), ensure_ascii=False))
            elif args.gate_command == "summary":
                print(json.dumps(gate_summary(args.run_id), ensure_ascii=False))
            else:
                parser.error(f"Unsupported gate command: {args.gate_command}")
                return 2
        elif args.command == "healing":
            if args.healing_command == "apply":
                run_dir = _find_run_dir(args.run_id)
                result = apply_healing_event(
                    run_dir,
                    args.event_id,
                    evidence_file=args.evidence_file,
                    operator=args.operator,
                )
                print(json.dumps(result, ensure_ascii=False))
            else:
                parser.error(f"Unsupported healing command: {args.healing_command}")
                return 2
        return 0
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .state import WorkflowRuntimeState


VALID_GATE_STATUSES = {"pending", "approved", "rejected"}


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _optional_gate_requires_review(state: WorkflowRuntimeState, node_spec: dict[str, Any]) -> bool:
    artifacts = state.get("artifacts") or {}
    for input_name in node_spec.get("inputs") or []:
        name = str(input_name)
        if "conflict" in name.lower() and artifacts.get(name):
            return True
    metadata = state.get("metadata") or {}
    forced = metadata.get("force_human_gates") or []
    return str(node_spec.get("id")) in {str(item) for item in forced}


def evaluate_gate(state: WorkflowRuntimeState, node_spec: dict[str, Any]) -> dict[str, Any]:
    gate_id = str(node_spec["id"])
    gates = state.get("gates") or {}
    existing = gates.get(gate_id) or {}
    existing_status = str(existing.get("status") or "")
    if existing_status in VALID_GATE_STATUSES:
        return dict(existing)

    policy = str(node_spec.get("policy") or "human_required")
    if policy == "report_only":
        status = "approved"
        note = "Report-only gate auto-approved"
    elif policy == "human_optional" and not _optional_gate_requires_review(state, node_spec):
        status = "approved"
        note = "Optional gate auto-approved because no blocking evidence was found"
    else:
        status = "pending"
        note = "Awaiting human review"

    return {
        "status": status,
        "operator": "workflow-runtime" if status == "approved" else "",
        "timestamp": _utc_now(),
        "note": note,
        "policy": policy,
    }


def gate_checkpoint_path(run_id: str, checkpoint_dir: str | Path) -> Path:
    return Path(checkpoint_dir) / f"{run_id}.v2.json"


def persist_pending_gate(state: WorkflowRuntimeState, gate_id: str) -> Path | None:
    gate = (state.get("gates") or {}).get(gate_id) or {}
    if gate.get("status") != "pending":
        return None
    metadata = state.get("metadata") or {}
    checkpoint_dir = metadata.get("gate_checkpoint_dir")
    if not checkpoint_dir:
        return None
    target_dir = Path(str(checkpoint_dir))
    target_dir.mkdir(parents=True, exist_ok=True)
    path = gate_checkpoint_path(str(state.get("run_id") or "run"), target_dir)
    payload = {
        "run_id": state.get("run_id"),
        "app_id": state.get("app_id"),
        "workflow_id": state.get("workflow_id"),
        "pending_gate": gate_id,
        "decision": None,
        "status": "pending",
        "updated_at": _utc_now(),
        "state": state,
    }
    _write_checkpoint(path, payload)
    return path


def load_gate_checkpoint(run_id: str, checkpoint_dir: str | Path) -> tuple[Path, dict[str, Any]]:
    path = gate_checkpoint_path(run_id, checkpoint_dir)
    if not path.exists():
        raise FileNotFoundError(f"v2 gate checkpoint not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("state"), dict):
        raise ValueError(f"Invalid v2 gate checkpoint: {path}")
    return path, payload


def decide_gate(
    run_id: str,
    checkpoint_dir: str | Path,
    *,
    status: str,
    operator: str,
    note: str,
) -> dict[str, Any]:
    if status not in {"approved", "rejected"}:
        raise ValueError(f"Gate decision must be approved or rejected, got: {status}")
    path, payload = load_gate_checkpoint(run_id, checkpoint_dir)
    gate_id = str(payload.get("pending_gate") or "")
    if not gate_id:
        raise ValueError(f"Checkpoint has no pending gate: {path}")
    state = dict(payload["state"])
    gates = dict(state.get("gates") or {})
    existing = dict(gates.get(gate_id) or {})
    if str(existing.get("status") or "pending") not in VALID_GATE_STATUSES:
        raise ValueError(f"Invalid current gate status for {gate_id}: {existing}")
    decision = {
        **existing,
        "status": status,
        "operator": operator,
        "timestamp": _utc_now(),
        "note": note,
    }
    gates[gate_id] = decision
    state["gates"] = gates
    payload.update(
        {
            "decision": decision,
            "status": "decided",
            "updated_at": _utc_now(),
            "state": state,
        }
    )
    _write_checkpoint(path, payload)
    return payload


def complete_gate_checkpoint(
    path: Path,
    payload: dict[str, Any],
    *,
    state: dict[str, Any],
    next_status: str,
) -> None:
    payload.update(
        {
            "status": next_status,
            "updated_at": _utc_now(),
            "state": state,
        }
    )
    _write_checkpoint(path, payload)


def gate_route(gate_id: str):
    def _route(state: WorkflowRuntimeState) -> str:
        gate = (state.get("gates") or {}).get(gate_id) or {}
        return str(gate.get("status") or "pending")

    _route.__name__ = f"route_{gate_id}"
    return _route


def _write_checkpoint(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    temporary.replace(path)

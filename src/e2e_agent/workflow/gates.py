from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .state import WorkflowRuntimeState


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
    if existing_status in {"approved", "rejected", "pending"}:
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
    path = target_dir / f"{state.get('run_id', 'run')}.v2.json"
    path.write_text(
        json.dumps(
            {
                "run_id": state.get("run_id"),
                "workflow_id": state.get("workflow_id"),
                "pending_gate": gate_id,
                "updated_at": _utc_now(),
                "state": state,
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    return path


def gate_route(gate_id: str):
    def _route(state: WorkflowRuntimeState) -> str:
        gate = (state.get("gates") or {}).get(gate_id) or {}
        return str(gate.get("status") or "pending")

    _route.__name__ = f"route_{gate_id}"
    return _route

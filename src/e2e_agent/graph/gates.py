"""Gate routing functions for LangGraph conditional edges."""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from e2e_agent.graph.state import E2EAgentState

_ROOT_DIR = Path(__file__).resolve().parent.parent.parent.parent
_GATE_CHECKPOINT_DIR = _ROOT_DIR / "reg" / ".artifacts" / "gate-checkpoints"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _resolve_gate_checkpoint_dir(state: E2EAgentState | None = None) -> Path:
    if state and state.get("gate_checkpoint_dir"):
        return Path(str(state["gate_checkpoint_dir"]))
    if os.environ.get("E2E_AGENT_GATE_CHECKPOINT_DIR"):
        return Path(os.environ["E2E_AGENT_GATE_CHECKPOINT_DIR"])
    return _GATE_CHECKPOINT_DIR


def get_gate_checkpoint_path(run_id: str, checkpoint_dir: str | Path | None = None) -> Path:
    base_dir = Path(checkpoint_dir) if checkpoint_dir else _resolve_gate_checkpoint_dir()
    return base_dir / f"{run_id}.json"


def persist_gate_checkpoint(state: E2EAgentState, gate_name: str) -> Path:
    checkpoint_dir = _resolve_gate_checkpoint_dir(state)
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    run_id = str(state.get("run_id") or "run-unknown")
    checkpoint_path = get_gate_checkpoint_path(run_id, checkpoint_dir)
    checkpoint_path.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "pending_gate": gate_name,
                "updated_at": _utc_now(),
                "state": state,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return checkpoint_path


def route_gate(gate_name: str) -> Callable[[E2EAgentState], str]:
    """Returns a routing function for the given gate (r1/r2/r3/r4).

    Routing outcomes:
        "approved"  → proceed to next agent node
        "rejected"  → loop back to previous agent for revision
        "pending"   → interrupt graph and wait for human review
    """
    key = f"{gate_name}_gate"

    def _route(state: E2EAgentState) -> str:
        gate = state.get(key, {})
        return gate.get("status", "pending")

    _route.__name__ = f"route_{gate_name}"
    return _route


def make_gate_node(gate_name: str) -> Callable[[E2EAgentState], dict]:
    """Returns a LangGraph node function that initialises gate state to 'pending'.

    In a real deployment this node would:
    1. Persist the current state to the checkpoint store.
    2. Notify the gate operator (e.g. send a Slack message).
    3. Return immediately — the graph is then interrupted (END) until the
       operator calls the CLI `gate approve` command which resumes execution.
    """
    key = f"{gate_name}_gate"

    def _gate_node(state: E2EAgentState) -> dict:
        existing = state.get(key, {})
        # Persist pending gates so an external reviewer can inspect/resume them.
        if existing.get("status") not in ("approved", "rejected"):
            gate_state = {
                key: {
                    "status": "pending",
                    "operator": "",
                    "timestamp": "",
                    "note": f"{gate_name.upper()} Gate — awaiting human review",
                }
            }
            persist_gate_checkpoint({**state, **gate_state}, gate_name)
            return gate_state
        return {}

    _gate_node.__name__ = f"{gate_name}_gate_node"
    return _gate_node


# Pre-built gate nodes and routers for all 4 gates
r1_gate_node = make_gate_node("r1")
r2_gate_node = make_gate_node("r2")
r3_gate_node = make_gate_node("r3")
r4_gate_node = make_gate_node("r4")

route_r1 = route_gate("r1")
route_r2 = route_gate("r2")
route_r3 = route_gate("r3")
route_r4 = route_gate("r4")

"""Smoke tests for LangGraph StateGraph — verifies graph compiles without errors."""
from __future__ import annotations

import json

import pytest


def test_graph_compiles():
    """graph.compile() must succeed with in-memory checkpointer."""
    from e2e_agent.legacy.graph.graph import build_graph
    app = build_graph(":memory:")
    assert app is not None


def test_graph_has_nodes():
    """Graph must contain all 8 expected nodes."""
    from e2e_agent.legacy.graph.graph import build_graph
    app = build_graph(":memory:")
    expected_nodes = {
        "tc_merge", "r1_gate",
        "path_extract", "r2_gate",
        "explore", "r3_gate",
        "exec_healing", "r4_gate",
    }
    actual_nodes = set(app.get_graph().nodes.keys()) - {"__start__", "__end__"}
    assert actual_nodes == expected_nodes


@pytest.mark.asyncio
async def test_graph_invoke_returns_pending_at_r1(tmp_path):
    """Invoking the graph with a minimal state should reach R1 Gate (pending)."""
    from e2e_agent.legacy.graph.graph import build_graph

    app = build_graph(":memory:")
    initial_state = {
        "product_id": "test-product",
        "prd_path": "sources/test/prd.md",
        "manual_cases_path": None,
        "entry_url": None,
        "merged_cases": [],
        "conflicts": [],
        "regression_flow": {},
        "regression_paths": [],
        "page_functions": [],
        "scenarios": [],
        "assertion_results": [],
        "reports": [],
        "healing_events": [],
        "r1_gate": {"status": "pending", "operator": "", "timestamp": "", "note": ""},
        "r2_gate": {"status": "pending", "operator": "", "timestamp": "", "note": ""},
        "r3_gate": {"status": "pending", "operator": "", "timestamp": "", "note": ""},
        "r4_gate": {"status": "pending", "operator": "", "timestamp": "", "note": ""},
        "run_id": "test-run-001",
        "error": None,
        "artifact_root_dir": str(tmp_path),
        "gate_checkpoint_dir": str(tmp_path / "gate-checkpoints"),
    }

    config = {"configurable": {"thread_id": "test-thread-001"}}
    result = await app.ainvoke(initial_state, config=config)

    # After tc_merge (stub returns []) → r1_gate sets status pending → graph ends
    assert result is not None
    assert result["r1_gate"]["status"] == "pending"


@pytest.mark.asyncio
async def test_graph_preserves_agent4_report_metadata_fields(tmp_path):
    """Agent4 report controls must survive StateGraph state filtering."""
    from e2e_agent.legacy.graph.graph import build_graph

    app = build_graph(":memory:")
    initial_state = {
        "product_id": "test-product",
        "prd_path": "sources/test/prd.md",
        "manual_cases_path": None,
        "entry_url": None,
        "merged_cases": [],
        "conflicts": [],
        "regression_flow": {},
        "regression_paths": [],
        "page_functions": [],
        "scenarios": [],
        "assertion_results": [],
        "reports": [],
        "healing_events": [],
        "r1_gate": {"status": "pending", "operator": "", "timestamp": "", "note": ""},
        "r2_gate": {"status": "pending", "operator": "", "timestamp": "", "note": ""},
        "r3_gate": {"status": "pending", "operator": "", "timestamp": "", "note": ""},
        "r4_gate": {"status": "pending", "operator": "", "timestamp": "", "note": ""},
        "run_id": "test-run-agent4-report-fields",
        "error": None,
        "artifact_root_dir": str(tmp_path),
        "gate_checkpoint_dir": str(tmp_path / "gate-checkpoints"),
        "run_dir": str(tmp_path / "agent4-report"),
        "html_report": str(tmp_path / "agent4-report" / "report.html"),
    }

    result = await app.ainvoke(
        initial_state,
        config={"configurable": {"thread_id": "test-thread-agent4-report-fields"}},
    )

    assert result["run_dir"] == str(tmp_path / "agent4-report")
    assert result["html_report"] == str(tmp_path / "agent4-report" / "report.html")


def test_pending_gate_persists_checkpoint(tmp_path):
    """A pending gate should persist a checkpoint for human review."""
    from e2e_agent.legacy.graph.gates import r1_gate_node

    state = {
        "run_id": "run-pending-001",
        "gate_checkpoint_dir": str(tmp_path),
        "r1_gate": {"status": "pending", "operator": "", "timestamp": "", "note": ""},
    }

    result = r1_gate_node(state)

    checkpoint_path = tmp_path / "run-pending-001.json"
    assert checkpoint_path.exists()
    payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert payload["pending_gate"] == "r1"
    assert payload["state"]["r1_gate"]["status"] == "pending"
    assert result["r1_gate"]["status"] == "pending"


@pytest.mark.parametrize(
    ("route_name", "state_key"),
    [
        ("route_r1", "r1_gate"),
        ("route_r2", "r2_gate"),
        ("route_r3", "r3_gate"),
        ("route_r4", "r4_gate"),
    ],
)
@pytest.mark.parametrize("status", ["approved", "rejected", "pending"])
def test_gate_routers_support_three_statuses(route_name, state_key, status):
    """Each gate router must preserve approved/rejected/pending decisions."""
    import e2e_agent.legacy.graph.gates as gates

    route = getattr(gates, route_name)

    assert route({state_key: {"status": status}}) == status

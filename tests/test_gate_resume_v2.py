from __future__ import annotations

import json
from pathlib import Path

import pytest

from e2e_agent.workflow import WorkflowRuntime
from e2e_agent.workflow.gates import decide_gate, load_gate_checkpoint

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.asyncio
async def test_approve_and_resume_runs_from_next_node(tmp_path: Path) -> None:
    runtime = WorkflowRuntime(repo_root=ROOT)
    run_id = "gate-approve-resume"
    result = await runtime.run(
        app_path=Path("apps/demo-ecommerce/app.yaml"),
        workflow="p0-web-regression",
        run_id=run_id,
        metadata={"gate_checkpoint_dir": str(tmp_path)},
    )

    assert result["gates"]["r2_path_review"]["status"] == "pending"
    _, checkpoint = load_gate_checkpoint(run_id, tmp_path)
    assert checkpoint["pending_gate"] == "r2_path_review"

    decide_gate(
        run_id,
        tmp_path,
        status="approved",
        operator="tester",
        note="paths reviewed",
    )
    resumed = await runtime.resume(run_id=run_id, checkpoint_dir=tmp_path)

    assert resumed["gates"]["r2_path_review"]["status"] == "approved"
    assert resumed["gates"]["r3_contract_review"]["status"] == "approved"
    assert "execution_result" in resumed["artifacts"]
    assert "test_report" in resumed["artifacts"]
    assert resumed["metadata"]["gate_history"][-1]["gate_id"] == "r2_path_review"
    _, completed = load_gate_checkpoint(run_id, tmp_path)
    assert completed["status"] == "completed"


@pytest.mark.asyncio
async def test_reject_and_resume_returns_to_revision_then_pending(tmp_path: Path) -> None:
    runtime = WorkflowRuntime(repo_root=ROOT)
    run_id = "gate-reject-resume"
    await runtime.run(
        app_path=Path("apps/demo-ecommerce/app.yaml"),
        workflow="p0-web-regression",
        run_id=run_id,
        metadata={"gate_checkpoint_dir": str(tmp_path)},
    )

    decide_gate(
        run_id,
        tmp_path,
        status="rejected",
        operator="reviewer",
        note="revise paths",
    )
    resumed = await runtime.resume(run_id=run_id, checkpoint_dir=tmp_path)

    assert resumed["gates"]["r2_path_review"]["status"] == "pending"
    assert [item["node_id"] for item in resumed["node_trace"]].count("path_extract") == 2
    _, checkpoint = load_gate_checkpoint(run_id, tmp_path)
    assert checkpoint["pending_gate"] == "r2_path_review"
    assert checkpoint["status"] == "pending"
    assert checkpoint["decision"] is None


def test_gate_checkpoint_decision_is_persisted(tmp_path: Path) -> None:
    state = {
        "run_id": "decision-only",
        "workflow_id": "p0-web-regression",
        "gates": {"r2": {"status": "pending", "policy": "human_required"}},
    }
    path = tmp_path / "decision-only.v2.json"
    path.write_text(
        json.dumps(
            {
                "run_id": "decision-only",
                "workflow_id": "p0-web-regression",
                "pending_gate": "r2",
                "status": "pending",
                "decision": None,
                "state": state,
            }
        ),
        encoding="utf-8",
    )

    payload = decide_gate(
        "decision-only",
        tmp_path,
        status="approved",
        operator="qa",
        note="ok",
    )

    assert payload["status"] == "decided"
    assert payload["state"]["gates"]["r2"]["status"] == "approved"

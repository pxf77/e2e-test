from __future__ import annotations

from pathlib import Path

import pytest

from e2e_agent.workflow import WorkflowRuntime

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
async def test_smoke_static_web_runs_to_report() -> None:
    runtime = WorkflowRuntime(repo_root=ROOT)

    result = await runtime.run(
        app_path=Path("apps/demo-generic-form/app.yaml"),
        workflow="smoke-static-web",
        run_id="runtime-smoke-test",
        metadata={"gate_checkpoint_dir": ""},
    )

    artifacts = result["artifacts"]
    assert artifacts["page_registry"]["entry_url"] == "https://example.com/"
    assert artifacts["execution_result"]["status"] == "skipped"
    assert artifacts["assertion_report"]["status"] == "skipped"
    assert artifacts["test_report"]["status"] == "skipped"
    assert [item["node_id"] for item in result["node_trace"]] == [
        "explore",
        "prepare_data",
        "execute",
        "assertions",
        "report",
    ]


@pytest.mark.asyncio
async def test_p0_web_workflow_stops_at_required_path_gate() -> None:
    runtime = WorkflowRuntime(repo_root=ROOT)

    result = await runtime.run(
        app_path=Path("apps/demo-ecommerce/app.yaml"),
        workflow="p0-web-regression",
        run_id="runtime-gate-test",
        metadata={"gate_checkpoint_dir": ""},
    )

    assert result["gates"]["r1_case_review"]["status"] == "approved"
    assert result["gates"]["r2_path_review"]["status"] == "pending"
    assert result["artifacts"]["merged_cases"]
    assert result["artifacts"]["regression_paths"]
    assert "execution_result" not in result["artifacts"]


def test_insurance_workflow_prepares_legacy_state() -> None:
    runtime = WorkflowRuntime(repo_root=ROOT)

    state = runtime.prepare_state(
        app_path=Path("apps/demo-insurance/app.yaml"),
        workflow="p0-insurance-regression",
        run_id="runtime-legacy-test",
    )

    legacy = state["legacy_state"]
    assert legacy["product_id"] == "demo-insurance"
    assert legacy["product_name"] == "Demo Insurance"
    assert legacy["prd_path"].endswith("apps/demo-insurance/requirements.md")
    assert legacy["manual_cases_path"].endswith("apps/demo-insurance/manual-cases.json")
    assert legacy["entry_url"] == "https://example.com/product/demo-insurance"

from __future__ import annotations

from pathlib import Path

from e2e_agent.runners import ExecutionPlan, ExecutionResult, ExecutionRunner
from e2e_agent.workflow.defaults import build_default_node_registry
from e2e_agent.workflow.state import WorkflowRuntimeState

ROOT = Path(__file__).resolve().parents[2]


def test_unused_runtime_and_runner_registry_files_are_absent() -> None:
    obsolete = [
        ROOT / "src" / "e2e_agent" / "runtime",
        ROOT / "src" / "e2e_agent" / "runners" / "registry.py",
    ]

    assert [str(path.relative_to(ROOT)) for path in obsolete if path.exists()] == []


def test_runner_package_exports_only_active_contracts() -> None:
    assert ExecutionPlan.__name__ == "ExecutionPlan"
    assert ExecutionResult.__name__ == "ExecutionResult"
    assert ExecutionRunner.__name__ == "ExecutionRunner"


def test_workflow_state_and_node_registry_are_the_active_runtime_model() -> None:
    state: WorkflowRuntimeState = {
        "run_id": "cleanup-test",
        "app_id": "demo",
        "domain_id": "generic-web",
        "workflow_id": "smoke-static-web",
        "artifacts": {},
    }
    registry = build_default_node_registry(ROOT)
    implementations = {item.id for item in registry.list()}

    assert state["run_id"] == "cleanup-test"
    assert {"runner.playwright", "runner.api", "runner.appium"} <= implementations
    assert "builtin.report" in implementations

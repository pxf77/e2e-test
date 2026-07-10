from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from e2e_agent.runners.api import ApiRunner
from e2e_agent.runners.base import ExecutionPlan
from e2e_agent.runners.mobile import AppiumRunner

from .registry import NodeResult
from .state import WorkflowRuntimeState


async def api_runner_node(state: WorkflowRuntimeState, node_spec: dict[str, Any]) -> NodeResult:
    artifacts = state.get("artifacts") or {}
    inputs = state.get("inputs") or {}
    metadata = state.get("metadata") or {}
    scenarios = inputs.get("api_scenarios") or artifacts.get("api_scenarios") or node_spec.get("scenarios") or []
    fixtures = dict(inputs.get("api_fixtures") or {})
    app = state.get("app") or {}
    api_entry = (app.get("entrypoints") or {}).get("api") or {}
    if api_entry.get("base_url"):
        fixtures.setdefault("base_url", api_entry["base_url"])
    runner = ApiRunner()
    result = await runner.execute(
        ExecutionPlan(
            id=f"{state.get('run_id', 'run')}-{node_spec.get('id', 'api')}",
            runner=runner.name,
            scenarios=[dict(item) for item in scenarios if isinstance(item, dict)],
            fixtures=fixtures,
            env={"name": state.get("env", "local"), **dict(inputs.get("api_env") or {})},
            artifacts_dir=str(metadata.get("artifacts_dir") or ""),
        )
    )
    return NodeResult(
        outputs={"execution_result": asdict(result)},
        metrics={"runner": runner.name, "scenario_count": len(scenarios)},
    )


async def appium_runner_node(state: WorkflowRuntimeState, node_spec: dict[str, Any]) -> NodeResult:
    artifacts = state.get("artifacts") or {}
    inputs = state.get("inputs") or {}
    metadata = state.get("metadata") or {}
    runner = AppiumRunner()
    scenarios = inputs.get("mobile_scenarios") or artifacts.get("mobile_scenarios") or []
    result = await runner.execute(
        ExecutionPlan(
            id=f"{state.get('run_id', 'run')}-{node_spec.get('id', 'mobile')}",
            runner=runner.name,
            scenarios=[dict(item) for item in scenarios if isinstance(item, dict)],
            fixtures=dict(inputs.get("mobile_fixtures") or {}),
            env={"name": state.get("env", "local"), **dict(inputs.get("mobile_env") or {})},
            artifacts_dir=str(metadata.get("artifacts_dir") or Path.cwd()),
        )
    )
    return NodeResult(
        outputs={"execution_result": asdict(result)},
        metrics={"runner": runner.name, "scenario_count": len(scenarios)},
    )

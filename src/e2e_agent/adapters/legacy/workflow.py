from __future__ import annotations

from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urljoin

from e2e_agent.agents.agent1_tc_merge.node import tc_merge_node
from e2e_agent.agents.agent2_path_extract.node import path_extract_node
from e2e_agent.agents.agent3_explore.node import explore_node
from e2e_agent.agents.agent4_exec.node import exec_healing_node
from e2e_agent.workflow.registry import NodeRegistry, NodeResult
from e2e_agent.workflow.state import WorkflowRuntimeState

from .domain_path import build_domain_regression_artifacts

LegacyNode = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


def _resolve_app_path(app_root: Path, value: str | None) -> str | None:
    if not value:
        return None
    path = Path(value)
    return str(path if path.is_absolute() else app_root / path)


def build_legacy_state(
    *,
    app: dict[str, Any],
    app_root: Path,
    run_id: str,
    artifact_root: Path,
) -> dict[str, Any]:
    requirements = app.get("requirements") or {}
    web = (app.get("entrypoints") or {}).get("web") or {}
    base_url = str(web.get("base_url") or "")
    start_url = str(web.get("start_url") or "")
    entry_url = urljoin(base_url.rstrip("/") + "/", start_url.lstrip("/")) if base_url else start_url
    return {
        "product_id": str(app.get("id") or "app"),
        "product_name": str(app.get("name") or app.get("id") or "app"),
        "prd_path": _resolve_app_path(app_root, requirements.get("prd")) or "",
        "manual_cases_path": _resolve_app_path(app_root, requirements.get("manual_cases")),
        "entry_url": entry_url or None,
        "run_id": run_id,
        "artifact_root_dir": str(artifact_root),
        "artifact_fingerprints": [],
        "error": None,
    }


def _prepare_legacy_state(state: WorkflowRuntimeState) -> dict[str, Any]:
    legacy_state = dict(state.get("legacy_state") or {})
    legacy_state["domain"] = dict(state.get("domain") or {})
    for gate_name, gate in (state.get("gates") or {}).items():
        if gate_name in {"r1_gate", "r2_gate", "r3_gate", "r4_gate"}:
            legacy_state[gate_name] = dict(gate)
    return legacy_state


def _node_result(
    legacy_state: dict[str, Any],
    node_spec: dict[str, Any],
    *,
    warnings: list[str] | None = None,
) -> NodeResult:
    output_names = [str(item) for item in node_spec.get("outputs") or []]
    outputs = {name: legacy_state[name] for name in output_names if name in legacy_state}
    return NodeResult(
        outputs=outputs,
        state_updates={"legacy_state": legacy_state},
        warnings=list(warnings or []),
        metrics={"legacy_output_count": len(outputs)},
    )


def _legacy_handler(node: LegacyNode):
    async def _handler(state: WorkflowRuntimeState, node_spec: dict[str, Any]) -> NodeResult:
        legacy_state = _prepare_legacy_state(state)
        result = await node(legacy_state)
        legacy_state.update(result)
        return _node_result(legacy_state, node_spec)

    return _handler


async def legacy_path_extract_handler(
    state: WorkflowRuntimeState,
    node_spec: dict[str, Any],
) -> NodeResult:
    """Run Agent2 deterministic planning with the active Domain Pack.

    The v1 graph still calls ``path_extract_node`` unchanged. Only the v2
    legacy adapter bypasses the insurance-specific skill entry and invokes the
    deterministic planner under serialized Domain Pack overrides.
    """
    legacy_state = _prepare_legacy_state(state)
    domain = dict(state.get("domain") or {})
    if (domain.get("ontology") or {}).get("page_types"):
        regression_flow, regression_paths, governance_summary, warnings = await build_domain_regression_artifacts(
            legacy_state,
            domain,
        )
        legacy_state.update(
            {
                "regression_flow": regression_flow,
                "regression_paths": regression_paths,
                "governance_summary": governance_summary,
                "error": "; ".join(dict.fromkeys(warnings)) if warnings else None,
            }
        )
        return _node_result(legacy_state, node_spec, warnings=warnings)

    result = await path_extract_node(legacy_state)
    legacy_state.update(result)
    return _node_result(legacy_state, node_spec)


def register_legacy_nodes(registry: NodeRegistry) -> None:
    registry.register("legacy.agent1_tc_merge", _legacy_handler(tc_merge_node), kind="agent")
    registry.register("legacy.agent2_path_extract", legacy_path_extract_handler, kind="agent")
    registry.register("legacy.agent3_explore", _legacy_handler(explore_node), kind="agent")
    registry.register("legacy.agent4_exec", _legacy_handler(exec_healing_node), kind="agent")

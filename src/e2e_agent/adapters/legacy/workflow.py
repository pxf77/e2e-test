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


def _legacy_handler(node: LegacyNode):
    async def _handler(state: WorkflowRuntimeState, node_spec: dict[str, Any]) -> NodeResult:
        legacy_state = dict(state.get("legacy_state") or {})
        for gate_name, gate in (state.get("gates") or {}).items():
            if gate_name in {"r1_gate", "r2_gate", "r3_gate", "r4_gate"}:
                legacy_state[gate_name] = dict(gate)
        result = await node(legacy_state)
        legacy_state.update(result)
        output_names = [str(item) for item in node_spec.get("outputs") or []]
        outputs = {name: legacy_state[name] for name in output_names if name in legacy_state}
        return NodeResult(
            outputs=outputs,
            state_updates={"legacy_state": legacy_state},
            metrics={"legacy_output_count": len(outputs)},
        )

    return _handler


def register_legacy_nodes(registry: NodeRegistry) -> None:
    registry.register("legacy.agent1_tc_merge", _legacy_handler(tc_merge_node), kind="agent")
    registry.register("legacy.agent2_path_extract", _legacy_handler(path_extract_node), kind="agent")
    registry.register("legacy.agent3_explore", _legacy_handler(explore_node), kind="agent")
    registry.register("legacy.agent4_exec", _legacy_handler(exec_healing_node), kind="agent")

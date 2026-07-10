from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from e2e_agent.core.domain_path_planner import build_domain_path_nodes, resolve_business_intent
from e2e_agent.runners.base import ExecutionPlan
from e2e_agent.runners.playwright.runner import PlaywrightRunner

from .registry import NodeResult
from .state import WorkflowRuntimeState


def _app_root(state: WorkflowRuntimeState) -> Path:
    metadata = state.get("metadata") or {}
    return Path(str(metadata.get("app_root") or "."))


def _resolve_app_path(state: WorkflowRuntimeState, value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else _app_root(state) / path


def _entry_url(state: WorkflowRuntimeState) -> str:
    app = state.get("app") or {}
    web = (app.get("entrypoints") or {}).get("web") or {}
    base_url = str(web.get("base_url") or "")
    start_url = str(web.get("start_url") or "")
    return urljoin(base_url.rstrip("/") + "/", start_url.lstrip("/")) if base_url else start_url


def case_merge_node(state: WorkflowRuntimeState, node_spec: dict[str, Any]) -> NodeResult:
    app = state.get("app") or {}
    manual_cases_ref = (app.get("requirements") or {}).get("manual_cases")
    manual_cases_path = _resolve_app_path(state, str(manual_cases_ref) if manual_cases_ref else None)
    cases: list[dict[str, Any]] = []
    warnings: list[str] = []
    if manual_cases_path and manual_cases_path.exists():
        payload = json.loads(manual_cases_path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            cases = [dict(item) for item in payload if isinstance(item, dict)]
        elif isinstance(payload, dict):
            raw_cases = payload.get("cases") or payload.get("test_cases") or []
            cases = [dict(item) for item in raw_cases if isinstance(item, dict)]
        else:
            warnings.append(f"Unsupported manual case payload: {manual_cases_path}")
    elif manual_cases_path:
        warnings.append(f"Manual case file not found: {manual_cases_path}")

    normalized: list[dict[str, Any]] = []
    for index, case in enumerate(cases, start=1):
        case_id = str(case.get("case_id") or case.get("id") or f"CASE-{index:03d}")
        normalized.append({**case, "case_id": case_id})
    return NodeResult(
        outputs={"merged_cases": normalized, "merge_conflicts": []},
        warnings=warnings,
        metrics={"merged_case_count": len(normalized)},
    )


def path_extract_node(state: WorkflowRuntimeState, node_spec: dict[str, Any]) -> NodeResult:
    artifacts = state.get("artifacts") or {}
    domain = state.get("domain") or {}
    ontology = domain.get("ontology") or {}
    cases = artifacts.get("merged_cases") or []
    paths: list[dict[str, Any]] = []
    flow_nodes: dict[str, dict[str, Any]] = {}
    flow_edges: dict[tuple[str, str], dict[str, str]] = {}

    for index, case in enumerate(cases, start=1):
        domain_nodes = build_domain_path_nodes(case, ontology)
        if domain_nodes:
            path_nodes = domain_nodes
        else:
            steps = case.get("steps") or []
            path_nodes = [
                {
                    "id": f"step-{step_index:02d}",
                    "page_type": "unknown",
                    "name": str(step),
                    "node_type": "action",
                    "url_pattern": None,
                    "optional": False,
                    "action": str(step),
                }
                for step_index, step in enumerate(steps, start=1)
            ]

        for node in path_nodes:
            flow_nodes.setdefault(str(node["id"]), dict(node))
        for left, right in zip(path_nodes, path_nodes[1:]):
            key = (str(left["id"]), str(right["id"]))
            flow_edges.setdefault(key, {"from": key[0], "to": key[1]})

        intent = resolve_business_intent(case, ontology)
        optional_nodes = [str(node["id"]) for node in path_nodes if node.get("optional")]
        paths.append(
            {
                "path_id": f"PATH-{index:03d}",
                "case_ids": [str(case.get("case_id") or f"CASE-{index:03d}")],
                "title": str(case.get("title") or case.get("name") or f"Regression path {index}"),
                "business_intent": intent,
                "nodes": path_nodes,
                "optional_nodes": optional_nodes,
                "execution_policy": {
                    "name": "domain_ontology_path" if domain_nodes else "case_step_path",
                    "skip_absent_optional_nodes": bool(optional_nodes),
                },
            }
        )

    regression_flow = {
        "domain_id": state.get("domain_id"),
        "entry_url": _entry_url(state),
        "flow_version": "domain-ontology-v2",
        "nodes": list(flow_nodes.values()),
        "edges": list(flow_edges.values()),
    }
    return NodeResult(
        outputs={"regression_flow": regression_flow, "regression_paths": paths},
        metrics={
            "path_count": len(paths),
            "ontology_page_type_count": len(ontology.get("page_types") or {}),
        },
    )


def explore_static_node(state: WorkflowRuntimeState, node_spec: dict[str, Any]) -> NodeResult:
    entry_url = _entry_url(state)
    ontology = ((state.get("domain") or {}).get("ontology") or {})
    page_types = ontology.get("page_types") or {}
    first_page_type = next(iter(page_types), "landing")
    page_registry = {
        "pages": [
            {
                "page_key": "entry",
                "page_type": first_page_type,
                "url": entry_url,
                "source": "static_app_pack",
            }
        ]
        if entry_url
        else [],
        "entry_url": entry_url,
    }
    return NodeResult(outputs={"page_registry": page_registry})


def explore_node(state: WorkflowRuntimeState, node_spec: dict[str, Any]) -> NodeResult:
    result = explore_static_node(state, node_spec)
    result.outputs["page_models"] = []
    return result


async def playwright_runner_node(state: WorkflowRuntimeState, node_spec: dict[str, Any]) -> NodeResult:
    artifacts = state.get("artifacts") or {}
    metadata = state.get("metadata") or {}
    runner = PlaywrightRunner(repo_root=Path(str(metadata.get("repo_root") or Path.cwd())))
    plan = ExecutionPlan(
        id=f"{state.get('run_id', 'run')}-{node_spec.get('id', 'execute')}",
        runner=runner.name,
        scenarios=list(artifacts.get("scenarios") or artifacts.get("regression_paths") or []),
        fixtures=dict((state.get("inputs") or {}).get("runner_fixtures") or {}),
        env={"name": state.get("env", "local")},
        artifacts_dir=str(metadata.get("artifacts_dir") or ""),
    )
    execution_result = await runner.execute(plan)
    return NodeResult(
        outputs={"execution_result": asdict(execution_result)},
        metrics={"runner_artifact_count": len(execution_result.artifacts)},
    )


def report_node(state: WorkflowRuntimeState, node_spec: dict[str, Any]) -> NodeResult:
    artifacts = state.get("artifacts") or {}
    execution = artifacts.get("execution_result") or {}
    summary = execution.get("summary") or {}
    status = execution.get("status") or "unknown"
    report = {
        "run_id": state.get("run_id"),
        "workflow_id": state.get("workflow_id"),
        "app_id": state.get("app_id"),
        "domain_id": state.get("domain_id"),
        "status": status,
        "summary": {
            "passed": int(summary.get("passed") or 0),
            "failed": int(summary.get("failed") or 0),
            "skipped": int(summary.get("skipped") or 0),
        },
        "failures": execution.get("failures") or [],
        "runner_artifacts": execution.get("artifacts") or [],
    }
    return NodeResult(outputs={"test_report": report, "healing_events": []})

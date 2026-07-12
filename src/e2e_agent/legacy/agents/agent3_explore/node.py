"""explore_agent: static-first contract compilation + browser probe fallback.

LangGraph node responsible for:
- compiling static element-set contracts for Agent4
- validating environment readiness for browser exploration fallback
- delegating artifact generation to `mpt-ins-ts-gen`
- falling back to the shared local runtime only when the skill entry is unavailable

Reads:  state.regression_paths, state.entry_url
Writes: state.page_functions, state.scenarios, state.assertion_results, state.error
Gate:   R3 (human review of coverage report)
"""
from __future__ import annotations

import importlib.util
import json
import os
from fnmatch import fnmatch
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from e2e_agent.legacy.agents.agent3_explore.element_set import load_element_set_for_product
from e2e_agent.legacy.agents.agent3_explore.static_contract import build_static_explore_artifacts
from e2e_agent.artifacts.fingerprint import append_artifact_fingerprint
from e2e_agent.artifacts.paths import (
    agent_artifact_dir,
    agent_artifact_path,
    product_artifact_dir,
    write_agent_json_artifact,
)
from e2e_agent.legacy.browser.runner import PlaywrightTSRunner
from e2e_agent.core.page_exploration import (
    _action_trace_from_path_results,
    _build_exploration_contract,
    _main_flow_progress_from_results,
    run_live_exploration,
)
from e2e_agent.core.element_set_generation import materialise_element_set_from_page_registry
from e2e_agent.core.knowledge_agent3_hints import load_knowledge_agent3_hints
from e2e_agent.core.runtime_context import prepare_runtime_context
from e2e_agent.legacy.skills.loader import SkillPackageLoader
from e2e_agent.core.script_generation import (
    build_ts_gen_bundle,
    finalize_script_generation_result,
    materialise_ts_gen_outputs,
    validate_script_bundle,
)
from e2e_agent.core.static_contract_builder import build_static_agent4_contract
from e2e_agent.core.static_product_package import load_static_product_package

if TYPE_CHECKING:
    from e2e_agent.legacy.graph.state import E2EAgentState

def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").exists():
            return parent
    return Path(__file__).resolve().parents[5]


_ROOT_DIR = _repo_root()
_PRODUCT_DETAIL_ROUTE_ALIASES = (
    "/product/detail",
    "/product/index",
    "/media.html",
)
_HEALTH_NOTICE_ROUTE_ALIASES = (
    "/insure/health-notice",
    "/product/healthInform",
)
_SUITABILITY_ROUTE_ALIASES = (
    "/product/to-insure",
    "/product/adapt",
    "/product/adapt/result",
)
_RISK_CONTROL_ROUTE_ALIASES = (
    "/risk-control",
    "/authentication",
    "/authentication/detail",
    "/authentication/result",
)
_PAYMENT_ROUTE_ALIASES = (
    "/payment",
    "/pay",
    "/pay/",
)
_POLICY_RESULT_ROUTE_ALIASES = (
    "/result",
    "/pay/success",
    "/order/detail",
)


def _state_product_source_dir(root_dir: Path, state: "E2EAgentState") -> Path | None:
    raw = str(state.get("product_source_dir") or "").strip()
    if not raw:
        return None
    source_dir = Path(raw)
    return source_dir if source_dir.is_absolute() else root_dir / source_dir


def _knowledge_product_ids(root_dir: Path, state: "E2EAgentState") -> list[str]:
    product_id = str(state.get("product_id") or "product").strip() or "product"
    candidates = [product_id]
    source_dir = _state_product_source_dir(root_dir, state)
    if source_dir:
        try:
            source_key = source_dir.resolve().relative_to((root_dir / "products").resolve()).as_posix()
        except ValueError:
            source_key = ""
        if source_key:
            candidates.append(source_key)
    result: list[str] = []
    for item in candidates:
        if item and item not in result:
            result.append(item)
    return result


def _load_agent3_knowledge_hints(root_dir: Path, state: "E2EAgentState") -> dict[str, Any]:
    fallback: dict[str, Any] | None = None
    for knowledge_product_id in _knowledge_product_ids(root_dir, state):
        hints = load_knowledge_agent3_hints(root_dir, knowledge_product_id)
        if fallback is None:
            fallback = hints
        if hints.get("available"):
            return hints
    return fallback or load_knowledge_agent3_hints(root_dir, str(state.get("product_id") or "product"))


def _state_product_artifact_dir(root_dir: Path, state: "E2EAgentState") -> Path:
    product_id = str(state.get("product_id") or "product")
    return product_artifact_dir(
        root_dir,
        product_id,
        product_dir=state.get("product_artifact_dir"),
        source_paths=[state.get("prd_path"), state.get("manual_cases_path"), state.get("product_source_dir")],
    )


def _playwright_python_available() -> bool:
    return importlib.util.find_spec("playwright.async_api") is not None


def _live_exploration_disabled() -> bool:
    return os.environ.get("AGENT3_DISABLE_LIVE", "").lower() in {"1", "true", "yes"}


def _environment_warnings() -> list[str]:
    warnings: list[str] = []
    if not _playwright_python_available():
        warnings.append("Playwright Python package not available for live browser exploration")
    runner = PlaywrightTSRunner(_ROOT_DIR)
    if not runner.check_node_available():
        warnings.append("Node.js / npx not available for Playwright TS compatibility mode")
    return warnings


def _agent3_mode(state: "E2EAgentState") -> str:
    product_config = state.get("product_config", {}) if isinstance(state.get("product_config"), dict) else {}
    return str(state.get("agent3_mode") or product_config.get("agent3_mode") or "static-first")


def _should_try_static_first(state: "E2EAgentState") -> bool:
    mode = _agent3_mode(state)
    return mode == "static-first" and bool(state.get("regression_paths"))


def _should_keep_static_artifacts(static_artifacts: dict[str, Any]) -> bool:
    page_registry = static_artifacts.get("page_registry", {}) or {}
    static_contract = page_registry.get("static_contract", {}) or {}
    return bool(
        static_contract.get("is_usable")
        or static_contract.get("has_executable_paths")
        or int(static_contract.get("targeted_probe_request_count") or 0) > 0
        or page_registry.get("page_content_records")
    )


def _static_contract_is_complete(static_artifacts: dict[str, Any]) -> bool:
    page_registry = static_artifacts.get("page_registry", {}) or {}
    exploration_contract = page_registry.get("exploration_contract", {}) or {}
    if "is_complete" in exploration_contract or "blocked_count" in exploration_contract:
        return bool(exploration_contract.get("is_complete")) and int(exploration_contract.get("blocked_count") or 0) == 0
    summary = exploration_contract.get("summary", {}) if isinstance(exploration_contract, dict) else {}
    if isinstance(summary, dict):
        total = int(summary.get("total_paths") or 0)
        completed = int(summary.get("completed_path_count") or 0)
        blocked = int(summary.get("blocked_path_count") or 0)
        return total > 0 and completed == total and blocked == 0
    return False


def _supports_live_probe(config: dict[str, Any]) -> bool:
    supports = config.get("supports")
    return isinstance(supports, dict) and bool(supports.get("live_probe"))


def _state_supports_live_probe(state: "E2EAgentState") -> bool:
    product_config = state.get("product_config", {}) if isinstance(state.get("product_config"), dict) else {}
    return _supports_live_probe(product_config)


def _should_keep_static_artifacts_for_state(static_artifacts: dict[str, Any], state: "E2EAgentState") -> bool:
    if _static_contract_is_complete(static_artifacts):
        return True
    if _state_supports_live_probe(state):
        return False
    return _should_keep_static_artifacts(static_artifacts)


def _fallback_explore_artifacts(state: "E2EAgentState") -> dict[str, Any]:
    pages: list[dict[str, Any]] = []
    for node in state.get("regression_flow", {}).get("nodes", []):
        node_type = str(node.get("type", "form"))
        if node_type in {"start", "end", "branch"}:
            continue
        node_id = str(node.get("node_id") or "")
        pages.append(
            {
                "page_key": node_id.removeprefix("NODE-") or "page",
                "url": state.get("entry_url"),
                "source_url": state.get("entry_url"),
                "title": str(node.get("page_name") or node_id),
                "field_count": 0,
                "action_count": 0,
                "fields": [],
                "actions": [],
                "primary_actions": [],
                "candidate_links": [],
            }
        )
    path_exploration_results: list[dict[str, Any]] = []
    for path_item in state.get("regression_paths", []) or []:
        required_nodes = [
            str(node_id)
            for node_id in path_item.get("nodes", []) or []
            if str(node_id) not in {"NODE-start", "NODE-end", "NODE-branch"}
        ]
        target_node = required_nodes[-1] if required_nodes else None
        path_exploration_results.append(
            {
                "path_id": path_item.get("path_id"),
                "case_ids": list(path_item.get("case_ids", []) or []),
                "path_status": "blocked",
                "target_node": target_node,
                "blocked_node": target_node,
                "blocked_reason": "Live browser exploration failed before Agent3 could complete the planned path",
                "evidence_source": "agent3-fallback",
                "node_progress": [
                    {
                        "node_id": node_id,
                        "status": "blocked" if node_id == target_node else "pending",
                        "matched": False,
                    }
                    for node_id in required_nodes
                ],
                "completion_rule": {
                    "source": "agent2.nodes",
                    "target_node": target_node,
                    "required_nodes": required_nodes,
                    "matched_nodes": [],
                    "missing_nodes": required_nodes,
                    "is_complete": False,
                },
                "action_chain": [],
            }
        )
    exploration_contract = _build_exploration_contract(path_exploration_results)
    return {
        "page_registry": {
            "product_id": state.get("product_id"),
            "entry_url": state.get("entry_url"),
            "platform": "h5" if state.get("entry_url") and "/m/" in str(state.get("entry_url")) else "pc",
            "generated_by": "explore_agent.fallback",
            "pages": pages,
            "primary_actions": [],
            "path_exploration_results": path_exploration_results,
            "exploration_contract": exploration_contract,
        },
        "explore_trace": {
            "product_id": state.get("product_id"),
            "visited_urls": [state.get("entry_url")] if state.get("entry_url") else [],
            "discovered_page_count": len(pages),
            "exploration_contract": exploration_contract,
            "warnings": ["Live browser exploration unavailable; built fallback page registry from regression_flow"],
        },
    }


def _url_pattern_matches(page_url: str, url_pattern: str) -> bool:
    """Match planned route fragments against explored absolute URLs."""
    if not page_url or not url_pattern:
        return False

    parsed = urlparse(page_url)
    page_path = parsed.path or page_url
    candidates = {page_url, page_path}
    if parsed.query:
        candidates.add(f"{page_path}?{parsed.query}")

    if any(fnmatch(candidate, url_pattern) for candidate in candidates):
        return True
    if url_pattern.startswith("/") and page_path.endswith(url_pattern):
        return True
    if url_pattern == "/product/detail":
        return any(page_path.endswith(alias) for alias in _PRODUCT_DETAIL_ROUTE_ALIASES)
    if url_pattern == "/insure/health-notice":
        return any(page_path.endswith(alias) for alias in _HEALTH_NOTICE_ROUTE_ALIASES)
    if url_pattern == "/product/to-insure":
        return any(page_path.endswith(alias) for alias in _SUITABILITY_ROUTE_ALIASES)
    if url_pattern == "/risk-control":
        return any(page_path.endswith(alias) for alias in _RISK_CONTROL_ROUTE_ALIASES)
    if url_pattern == "/payment":
        return any(page_path.endswith(alias) for alias in _PAYMENT_ROUTE_ALIASES)
    if url_pattern == "/result":
        return any(page_path.endswith(alias) for alias in _POLICY_RESULT_ROUTE_ALIASES)
    return False


def _node_matches_explored_route(page_url: str, node_id: str) -> bool:
    page_path = urlparse(page_url).path or page_url
    if node_id == "NODE-suitability":
        return any(page_path.endswith(alias) for alias in _SUITABILITY_ROUTE_ALIASES)
    if node_id == "NODE-risk-control":
        return any(page_path.endswith(alias) for alias in _RISK_CONTROL_ROUTE_ALIASES)
    if node_id == "NODE-health-notice":
        return any(page_path.endswith(alias) for alias in _HEALTH_NOTICE_ROUTE_ALIASES)
    if node_id == "NODE-product-detail":
        return any(page_path.endswith(alias) for alias in _PRODUCT_DETAIL_ROUTE_ALIASES)
    if node_id == "NODE-payment":
        return any(page_path.endswith(alias) for alias in _PAYMENT_ROUTE_ALIASES)
    if node_id == "NODE-policy-result":
        return any(page_path.endswith(alias) for alias in _POLICY_RESULT_ROUTE_ALIASES)
    return False


def _annotate_page_registry_with_governance(
    page_registry: dict[str, Any],
    state: "E2EAgentState",
) -> tuple[dict[str, Any], list[str]]:
    governance_summary = state.get("governance_summary", {}) or {}
    governance_paths = list(governance_summary.get("paths", []) or [])
    page_key_entries = [
        item
        for path_item in governance_paths
        for item in path_item.get("page_keys", [])
        if isinstance(item, dict)
    ]
    warnings: list[str] = []
    if not page_key_entries:
        page_registry["governance_source"] = "state.governance_summary"
        page_registry["planned_page_key_count"] = 0
        for page in page_registry.get("pages", []):
            page["planned_page_keys"] = []
            page["planned_state_keys"] = []
        return page_registry, warnings
    for page in page_registry.get("pages", []):
        page_url = str(page.get("url") or "")
        matched = [
            item
            for item in page_key_entries
            if item.get("url_pattern") and _url_pattern_matches(page_url, str(item.get("url_pattern")))
        ]
        if not matched:
            matched = [
                item
                for item in page_key_entries
                if item.get("node_id") and _node_matches_explored_route(page_url, str(item.get("node_id")))
            ]
        if matched:
            page["planned_page_keys"] = list(
                dict.fromkeys(item.get("page_key") for item in matched if item.get("page_key"))
            )
            page["planned_state_keys"] = sorted(
                {
                    key
                    for item in matched
                    for key in item.get("allowed_state_keys", [])
                }
            )
        else:
            page["planned_page_keys"] = []
            page["planned_state_keys"] = []
            warnings.append(f"No planned page-key matched explored page: {page_url or page.get('page_key')}")
    page_registry["governance_source"] = "state.governance_summary"
    page_registry["planned_page_key_count"] = len(page_key_entries)
    return page_registry, warnings


def _materialise_explore_fallback(root_dir: Path, state: "E2EAgentState", artifacts: dict[str, Any]) -> None:
    product_id = str(state.get("product_id") or "product")
    artifact_product_dir = _state_product_artifact_dir(root_dir, state)
    platform = "h5" if state.get("entry_url") and "/m/" in str(state.get("entry_url")) else "pc"
    output_dir = agent_artifact_dir(root_dir, product_id, "agent3", product_dir=artifact_product_dir) / "ts-gen" / platform
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "page-registry.json").write_text(
        json.dumps(artifacts["page_registry"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "explore-trace.json").write_text(
        json.dumps(artifacts["explore_trace"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    agent3_dir = agent_artifact_dir(root_dir, product_id, "agent3", product_dir=artifact_product_dir)
    agent_explore_dir = agent3_dir / "explore"
    agent_explore_dir.mkdir(parents=True, exist_ok=True)
    path_exploration_results = list(artifacts["page_registry"].get("path_exploration_results", []) or [])
    for filename, payload in {
        "path-exploration-results.json": path_exploration_results,
        "exploration-contract.json": artifacts["page_registry"].get("exploration_contract", {}),
        "action-trace.json": _action_trace_from_path_results(path_exploration_results),
        "main-flow-progress.json": _main_flow_progress_from_results(path_exploration_results),
        "page-content-records.json": artifacts["page_registry"].get("page_content_records", []),
        "page-elements.json": [],
        "targeted-probe-plan.json": artifacts["page_registry"].get("targeted_probe_plan", {}),
        "missing-report.json": artifacts["page_registry"].get("missing_report", {}),
        "explore-trace.json": artifacts["explore_trace"],
    }.items():
        (agent_explore_dir / filename).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    for filename, payload in {
        "page-registry.json": artifacts["page_registry"],
        "explore-trace.json": artifacts["explore_trace"],
    }.items():
        write_agent_json_artifact(
            root_dir=root_dir,
            product_id=product_id,
            agent_name="agent3",
            relative_path=filename,
            payload=payload,
            product_dir=artifact_product_dir,
        )


def _static_first_contract(
    root_dir: Path,
    state: "E2EAgentState",
) -> dict[str, Any] | None:
    if _agent3_mode(state) != "static-first":
        return None
    product_id = str(state.get("product_id") or "product")
    try:
        package = load_static_product_package(
            root_dir,
            product_id,
            product_source_dir=_state_product_source_dir(root_dir, state),
        )
    except FileNotFoundError:
        return None
    if package.agent3_mode != "static-first":
        return None
    knowledge_hints = _load_agent3_knowledge_hints(root_dir, state)
    static_contract = build_static_agent4_contract(package, state, knowledge_hints=knowledge_hints)
    if _supports_live_probe(package.config) and not _static_contract_is_complete(static_contract):
        return None
    return static_contract


def _materialise_agent3_outputs(
    *,
    root_dir: Path,
    product_id: str,
    product_dir: str | Path | None,
    runtime_context: dict[str, Any],
    live_artifacts: dict[str, Any],
    result: dict[str, Any],
) -> None:
    for filename, payload in {
        "page-registry.json": live_artifacts.get("page_registry", {}),
        "explore-trace.json": live_artifacts.get("explore_trace", {}),
        "runtime-context.json": runtime_context,
        "page-functions.json": result.get("page_functions", []),
        "scenarios.json": result.get("scenarios", []),
        "script-plan.json": result.get("script_plan", {}),
        "script-bundle.json": result.get("script_bundle", {}),
        "script-validation.json": result.get("script_validation", {}),
        "assertion-results.json": result.get("assertion_results", []),
        "assertion-template-summary.json": result.get("assertion_template_summary", {}),
    }.items():
        write_agent_json_artifact(
            root_dir=root_dir,
            product_id=product_id,
            agent_name="agent3",
            relative_path=filename,
            payload=payload,
            product_dir=product_dir,
        )


def _attach_assertion_template_summary(
    live_artifacts: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    summary = result.get("assertion_template_summary", {})
    if not isinstance(summary, dict) or not summary:
        return live_artifacts
    page_registry = dict(live_artifacts.get("page_registry", {}) or {})
    page_registry["assertion_template_summary"] = summary
    return {**live_artifacts, "page_registry": page_registry}


def _is_nonblocking_agent3_warning(message: str) -> bool:
    text = message.lower()
    return (
        "reused completed agent3 chain" in text
        or "self-healing retrying" in text
        or "continuing self-healing" in text
        or "no planned page-key matched explored page" in text
        or "static-first contract incomplete; falling back to live probe" in text
    )


def _agent3_error_from_warnings(
    warnings: list[str],
    page_registry: dict[str, Any],
    result: dict[str, Any],
) -> str | None:
    unique_warnings = [str(item) for item in dict.fromkeys(warnings) if str(item)]
    script_validation = result.get("script_validation", {}) or {}
    if script_validation.get("status") == "failed":
        errors = "; ".join(str(item) for item in script_validation.get("errors", []) or [])
        return f"Agent3 script precheck failed: {errors}".rstrip(": ")

    exploration_contract = page_registry.get("exploration_contract", {}) or {}
    has_contract = bool(exploration_contract)
    blocked_count = int(exploration_contract.get("blocked_count") or 0)
    is_complete = exploration_contract.get("is_complete")
    if has_contract and (is_complete is False or blocked_count > 0):
        return "; ".join(unique_warnings) or "Agent3 exploration contract incomplete"

    blocking_warnings: list[str] = []
    for message in unique_warnings:
        if _is_nonblocking_agent3_warning(message):
            continue
        lower = message.lower()
        if any(
            token in lower
            for token in ("failed", "blocked", "timed out", "not available", "entry_url is required")
        ):
            blocking_warnings.append(message)
    return "; ".join(blocking_warnings) if blocking_warnings else None


def _finalize_explore_result(
    *,
    state: "E2EAgentState",
    root_dir: Path,
    runtime_context: dict[str, Any],
    live_artifacts: dict[str, Any],
    result: dict[str, Any],
    warnings: list[str],
) -> dict[str, Any]:
    product_id = str(state.get("product_id") or "product")
    run_id = str(state.get("run_id") or "run-unknown")
    artifact_product_dir = _state_product_artifact_dir(root_dir, state)
    live_artifacts = _attach_assertion_template_summary(live_artifacts, result)
    _materialise_agent3_outputs(
        root_dir=root_dir,
        product_id=product_id,
        product_dir=artifact_product_dir,
        runtime_context=runtime_context,
        live_artifacts=live_artifacts,
        result=result,
    )
    existing_fingerprints = list(state.get("artifact_fingerprints", []) or [])
    new_fingerprints = [
        append_artifact_fingerprint(
            root_dir=root_dir,
            product_id=product_id,
            run_id=run_id,
            artifact_path=agent_artifact_path(
                product_id,
                "agent3",
                "page-registry.json",
                root_dir=root_dir,
                product_dir=artifact_product_dir,
            ),
            artifact_type="page-registry",
            payload=live_artifacts.get("page_registry", {}),
            producer="explore_agent",
            product_dir=artifact_product_dir,
        ),
        append_artifact_fingerprint(
            root_dir=root_dir,
            product_id=product_id,
            run_id=run_id,
            artifact_path=agent_artifact_path(
                product_id,
                "agent3",
                "explore-trace.json",
                root_dir=root_dir,
                product_dir=artifact_product_dir,
            ),
            artifact_type="explore-trace",
            payload=live_artifacts.get("explore_trace", {}),
            producer="explore_agent",
            product_dir=artifact_product_dir,
        ),
        append_artifact_fingerprint(
            root_dir=root_dir,
            product_id=product_id,
            run_id=run_id,
            artifact_path=agent_artifact_path(
                product_id,
                "agent3",
                "page-functions.json",
                root_dir=root_dir,
                product_dir=artifact_product_dir,
            ),
            artifact_type="page-functions",
            payload=result.get("page_functions", []),
            producer="explore_agent",
            product_dir=artifact_product_dir,
        ),
        append_artifact_fingerprint(
            root_dir=root_dir,
            product_id=product_id,
            run_id=run_id,
            artifact_path=agent_artifact_path(
                product_id,
                "agent3",
                "scenarios.json",
                root_dir=root_dir,
                product_dir=artifact_product_dir,
            ),
            artifact_type="scenarios",
            payload=result.get("scenarios", []),
            producer="explore_agent",
            product_dir=artifact_product_dir,
        ),
        append_artifact_fingerprint(
            root_dir=root_dir,
            product_id=product_id,
            run_id=run_id,
            artifact_path=agent_artifact_path(
                product_id,
                "agent3",
                "script-plan.json",
                root_dir=root_dir,
                product_dir=artifact_product_dir,
            ),
            artifact_type="script-plan",
            payload=result.get("script_plan", {}),
            producer="explore_agent",
            product_dir=artifact_product_dir,
        ),
        append_artifact_fingerprint(
            root_dir=root_dir,
            product_id=product_id,
            run_id=run_id,
            artifact_path=agent_artifact_path(
                product_id,
                "agent3",
                "assertion-results.json",
                root_dir=root_dir,
                product_dir=artifact_product_dir,
            ),
            artifact_type="assertion-results",
            payload=result.get("assertion_results", []),
            producer="explore_agent",
            product_dir=artifact_product_dir,
        ),
    ]

    return {
        "page_registry": live_artifacts.get("page_registry", {}),
        "explore_trace": live_artifacts.get("explore_trace", {}),
        "runtime_context": runtime_context,
        "page_functions": result.get("page_functions", []),
        "scenarios": result.get("scenarios", []),
        "script_plan": result.get("script_plan", {}),
        "script_bundle": result.get("script_bundle", {}),
        "script_validation": result.get("script_validation", {}),
        "assertion_results": result.get("assertion_results", []),
        "assertion_template_summary": result.get("assertion_template_summary", {}),
        "product_artifact_dir": str(artifact_product_dir),
        "artifact_fingerprints": existing_fingerprints + new_fingerprints,
        "error": _agent3_error_from_warnings(warnings, live_artifacts.get("page_registry", {}), result),
    }


async def _explore_node_impl(state: "E2EAgentState") -> dict:
    """Run live browser exploration first, then invoke ts-gen."""
    warnings: list[str] = []
    root_dir = Path(str(state.get("artifact_root_dir") or _ROOT_DIR))
    artifact_product_dir = _state_product_artifact_dir(root_dir, state)
    source_product_dir = _state_product_source_dir(root_dir, state)
    product_id = str(state.get("product_id") or "product")
    knowledge_hints = _load_agent3_knowledge_hints(root_dir, state)
    runtime_context = prepare_runtime_context(
        root_dir=root_dir,
        product_id=product_id,
        run_id=str(state.get("run_id") or "run-unknown"),
        entry_url=state.get("entry_url"),
        product_dir=artifact_product_dir,
    )

    static_contract = _static_first_contract(root_dir, state)
    if static_contract is not None:
        warnings.extend(str(item) for item in static_contract.get("warnings", []))
        live_artifacts = {
            "page_registry": static_contract.get("page_registry", {}),
            "explore_trace": static_contract.get("explore_trace", {}),
        }
        _materialise_explore_fallback(root_dir, state, live_artifacts)
        tsgen_state = {
            **state,
            "runtime_context": runtime_context,
            "page_registry": live_artifacts.get("page_registry", {}),
            "explore_trace": live_artifacts.get("explore_trace", {}),
            "product_artifact_dir": str(artifact_product_dir),
        }
        result = build_ts_gen_bundle(
            tsgen_state,
            root_dir=root_dir,
            materialise=False,
            generated_by="agent3.static-first",
        )
        warnings.extend(str(item) for item in result.get("warnings", []))
        result["scenarios"] = static_contract.get("scenarios", result.get("scenarios", []))
        if result.get("page_functions") or result.get("scenarios"):
            materialise_ts_gen_outputs(
                tsgen_state,
                result.get("page_functions", []),
                result.get("scenarios", []),
                root_dir=root_dir,
                generated_by="agent3.static-first",
            )
        script_validation = validate_script_bundle(
            finalize_script_generation_result(
                result,
                tsgen_state,
                root_dir=root_dir,
                generated_by="agent3.static-first",
            ).get("script_bundle", {}),
            PlaywrightTSRunner(root_dir),
        )
        result = finalize_script_generation_result(
            result,
            tsgen_state,
            root_dir=root_dir,
            generated_by="agent3.static-first",
            validation=script_validation,
            materialise=True,
        )
        if script_validation.get("status") == "failed":
            warnings.append(
                "Agent3 script precheck failed: "
                + "; ".join(str(item) for item in script_validation.get("errors", []) or [])
            )
        return _finalize_explore_result(
            state=state,
            root_dir=root_dir,
            runtime_context=runtime_context,
            live_artifacts=live_artifacts,
            result=result,
            warnings=warnings,
        )

    loader = SkillPackageLoader()
    manifest = None
    try:
        manifest = loader.load_skill("mpt-ins-ts-gen")
        if manifest.requires_node:
            warnings.extend(_environment_warnings())
    except (FileNotFoundError, ValueError) as exc:
        warnings.append(str(exc))

    live_artifacts: dict[str, Any]
    used_static_contract = False
    element_set_summary: dict[str, Any] = {}
    if _should_try_static_first(state) and not _state_supports_live_probe(state):
        try:
            element_set_payload = load_element_set_for_product(
                root_dir=root_dir,
                product_id=str(state.get("product_id") or "product"),
                product_source_dir=source_product_dir,
            )
            if element_set_payload.get("warning"):
                warnings.append(str(element_set_payload["warning"]))
            static_artifacts = build_static_explore_artifacts(
                product_id=product_id,
                entry_url=state.get("entry_url"),
                regression_paths=list(state.get("regression_paths", []) or []),
                element_set=dict(element_set_payload.get("element_set", {}) or {}),
                knowledge_hints=knowledge_hints,
            )
            static_artifacts.setdefault("explore_trace", {})["element_set_source"] = element_set_payload.get("source")
            static_artifacts.setdefault("explore_trace", {})["element_set_path"] = element_set_payload.get("path")
            static_contract = (
                static_artifacts.get("page_registry", {})
                .get("static_contract", {})
            )
            if _should_keep_static_artifacts_for_state(static_artifacts, state):
                live_artifacts = static_artifacts
                element_set_summary = dict(static_artifacts.get("element_set_summary", {}) or {})
                element_set_summary["source"] = element_set_payload.get("source")
                element_set_summary["path"] = element_set_payload.get("path")
                used_static_contract = True
            else:
                missing_nodes = static_contract.get("missing_nodes", []) or []
                warnings.append(
                    f"Static-first contract incomplete; falling back to live probe. missing_nodes={len(missing_nodes)}"
                )
        except Exception as exc:
            warnings.append(f"Static-first contract failed: {exc}")

    if (
        not used_static_contract
        and not _live_exploration_disabled()
        and _playwright_python_available()
        and state.get("entry_url")
    ):
        try:
            live_artifacts = await run_live_exploration(
                product_id=str(state.get("product_id") or "product"),
                entry_url=state.get("entry_url"),
                root_dir=root_dir,
                runtime_context=runtime_context,
                regression_paths=list(state.get("regression_paths", []) or []),
                regression_flow=dict(state.get("regression_flow", {}) or {}),
                mock_data=dict(state.get("mock_data", {}) or {}),
                product_dir=artifact_product_dir,
                materialise=True,
            )
            warnings.extend(str(item) for item in live_artifacts.get("warnings", []))
        except Exception as exc:
            warnings.append(f"Live browser exploration failed: {exc}")
            live_artifacts = _fallback_explore_artifacts(state)
            _materialise_explore_fallback(root_dir, state, live_artifacts)
    else:
        if not used_static_contract:
            warnings.extend(_environment_warnings())
            live_artifacts = _fallback_explore_artifacts(state)
            warnings.extend(str(item) for item in live_artifacts["explore_trace"].get("warnings", []))
            _materialise_explore_fallback(root_dir, state, live_artifacts)

    annotated_registry, governance_warnings = _annotate_page_registry_with_governance(
        live_artifacts.get("page_registry", {}),
        state,
    )
    live_artifacts["page_registry"] = annotated_registry
    warnings.extend(governance_warnings)
    _materialise_explore_fallback(root_dir, state, live_artifacts)
    if not used_static_contract:
        try:
            element_set_summary = materialise_element_set_from_page_registry(
                root_dir=root_dir,
                product_id=str(state.get("product_id") or "product"),
                page_registry=live_artifacts.get("page_registry", {}),
                product_dir=artifact_product_dir,
            )
        except Exception as exc:
            warnings.append(f"Element-set generation failed: {exc}")

    tsgen_state = {
        **state,
        "runtime_context": runtime_context,
        "page_registry": live_artifacts.get("page_registry", {}),
        "explore_trace": live_artifacts.get("explore_trace", {}),
    }
    result: dict[str, Any] | None = None
    if manifest and manifest.entry_script:
        try:
            result = loader.run_entry(
                "mpt-ins-ts-gen",
                {
                    "product_id": tsgen_state.get("product_id"),
                    "entry_url": tsgen_state.get("entry_url"),
                    "regression_flow": tsgen_state.get("regression_flow", {}),
                    "regression_paths": tsgen_state.get("regression_paths", []),
                    "page_registry": tsgen_state.get("page_registry", {}),
                    "explore_trace": tsgen_state.get("explore_trace", {}),
                    "mock_data": tsgen_state.get("mock_data", {}),
                    "materialise": True,
                    "generated_by": "mpt-ins-ts-gen",
                    "root_dir": str(root_dir),
                    "product_artifact_dir": str(artifact_product_dir),
                    "product_source_dir": str(source_product_dir) if source_product_dir else None,
                },
            )
            warnings.extend(str(item) for item in result.get("warnings", []))
        except (RuntimeError, ValueError, FileNotFoundError) as exc:
            warnings.append(str(exc))

    if result is None:
        result = build_ts_gen_bundle(
            tsgen_state,
            root_dir=root_dir,
            materialise=True,
            generated_by="mpt-ins-ts-gen",
        )
        warnings.extend(str(item) for item in result.get("warnings", []))

    script_validation = validate_script_bundle(
        result.get("script_bundle", {}),
        PlaywrightTSRunner(root_dir),
    )
    result = finalize_script_generation_result(
        result,
        tsgen_state,
        root_dir=root_dir,
        generated_by="mpt-ins-ts-gen",
        validation=script_validation,
        materialise=True,
    )
    if script_validation.get("status") == "failed":
        warnings.append(
            "Agent3 script precheck failed: "
            + "; ".join(str(item) for item in script_validation.get("errors", []) or [])
        )

    product_id = str(state.get("product_id") or "product")
    run_id = str(state.get("run_id") or "run-unknown")
    artifact_product_dir = _state_product_artifact_dir(root_dir, state)
    live_artifacts = _attach_assertion_template_summary(live_artifacts, result)
    _materialise_agent3_outputs(
        root_dir=root_dir,
        product_id=product_id,
        product_dir=artifact_product_dir,
        runtime_context=runtime_context,
        live_artifacts=live_artifacts,
        result=result,
    )
    existing_fingerprints = list(state.get("artifact_fingerprints", []) or [])
    new_fingerprints = [
        append_artifact_fingerprint(
            root_dir=root_dir,
            product_id=product_id,
            run_id=run_id,
            artifact_path=agent_artifact_path(
                product_id,
                "agent3",
                "page-registry.json",
                root_dir=root_dir,
                product_dir=artifact_product_dir,
            ),
            artifact_type="page-registry",
            payload=live_artifacts.get("page_registry", {}),
            producer="explore_agent",
            product_dir=artifact_product_dir,
        ),
        append_artifact_fingerprint(
            root_dir=root_dir,
            product_id=product_id,
            run_id=run_id,
            artifact_path=agent_artifact_path(
                product_id,
                "agent3",
                "explore-trace.json",
                root_dir=root_dir,
                product_dir=artifact_product_dir,
            ),
            artifact_type="explore-trace",
            payload=live_artifacts.get("explore_trace", {}),
            producer="explore_agent",
            product_dir=artifact_product_dir,
        ),
        append_artifact_fingerprint(
            root_dir=root_dir,
            product_id=product_id,
            run_id=run_id,
            artifact_path=agent_artifact_path(
                product_id,
                "agent3",
                "page-functions.json",
                root_dir=root_dir,
                product_dir=artifact_product_dir,
            ),
            artifact_type="page-functions",
            payload=result.get("page_functions", []),
            producer="explore_agent",
            product_dir=artifact_product_dir,
        ),
        append_artifact_fingerprint(
            root_dir=root_dir,
            product_id=product_id,
            run_id=run_id,
            artifact_path=agent_artifact_path(
                product_id,
                "agent3",
                "scenarios.json",
                root_dir=root_dir,
                product_dir=artifact_product_dir,
            ),
            artifact_type="scenarios",
            payload=result.get("scenarios", []),
            producer="explore_agent",
            product_dir=artifact_product_dir,
        ),
        append_artifact_fingerprint(
            root_dir=root_dir,
            product_id=product_id,
            run_id=run_id,
            artifact_path=agent_artifact_path(
                product_id,
                "agent3",
                "script-plan.json",
                root_dir=root_dir,
                product_dir=artifact_product_dir,
            ),
            artifact_type="script-plan",
            payload=result.get("script_plan", {}),
            producer="explore_agent",
            product_dir=artifact_product_dir,
        ),
        append_artifact_fingerprint(
            root_dir=root_dir,
            product_id=product_id,
            run_id=run_id,
            artifact_path=agent_artifact_path(
                product_id,
                "agent3",
                "assertion-results.json",
                root_dir=root_dir,
                product_dir=artifact_product_dir,
            ),
            artifact_type="assertion-results",
            payload=result.get("assertion_results", []),
            producer="explore_agent",
            product_dir=artifact_product_dir,
        ),
    ]

    return {
        "page_registry": live_artifacts.get("page_registry", {}),
        "explore_trace": live_artifacts.get("explore_trace", {}),
        "runtime_context": runtime_context,
        "page_functions": result.get("page_functions", []),
        "scenarios": result.get("scenarios", []),
        "script_plan": result.get("script_plan", {}),
        "script_bundle": result.get("script_bundle", {}),
        "script_validation": result.get("script_validation", {}),
        "assertion_results": result.get("assertion_results", []),
        "assertion_template_summary": result.get("assertion_template_summary", {}),
        "element_set": element_set_summary,
        "product_artifact_dir": str(artifact_product_dir),
        "artifact_fingerprints": existing_fingerprints + new_fingerprints,
        "error": _agent3_error_from_warnings(warnings, live_artifacts.get("page_registry", {}), result),
    }


async def explore_node(state: "E2EAgentState") -> dict:
    try:
        return await _explore_node_impl(state)
    except Exception as exc:
        return {"error": f"explore failed: {exc}"}

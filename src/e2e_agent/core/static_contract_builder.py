"""Compile static product packages into the current Agent4 contract."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from e2e_agent.core.knowledge_agent3_hints import (
    enrich_targeted_probe_plan,
    knowledge_assist_summary,
)
from e2e_agent.core.page_exploration import _build_exploration_contract
from e2e_agent.core.script_generation import build_scenarios, platform_from_entry_url
from e2e_agent.core.static_product_package import StaticProductPackage

_SELECTOR_OVERRIDE_PRIORITY = [
    {
        "rank": 1,
        "source": "targeted_probe_verified",
        "description": "Runtime targeted probe produced a verified selector.",
    },
    {
        "rank": 2,
        "source": "product_static_override",
        "description": "Product package provides a product-specific selector override.",
    },
    {
        "rank": 3,
        "source": "static_element_set",
        "description": "Static element-set selector is verified from the compiled page model.",
    },
    {
        "rank": 4,
        "source": "legacy_ts_selector",
        "description": "Legacy Playwright helper selector is retained as a fallback candidate.",
    },
    {
        "rank": 5,
        "source": "text_locator",
        "description": "Text or label locator is used only when no stronger selector is available.",
    },
]


def _business_nodes(path_item: Mapping[str, Any]) -> list[str]:
    return [
        str(node_id)
        for node_id in path_item.get("nodes", []) or []
        if str(node_id) not in {"NODE-start", "NODE-end", "NODE-branch"}
    ]


def _first_locator(item: Mapping[str, Any]) -> tuple[str | None, str | None]:
    for locator in item.get("locators", []) or []:
        if not isinstance(locator, Mapping):
            continue
        value = str(locator.get("value") or "").strip()
        if not value:
            continue
        by = str(locator.get("by") or "selector")
        if by in {"selector", "css"}:
            return value, None
        if by in {"text", "label_text"}:
            return None, value
    return None, None


def _field_record(field: Mapping[str, Any]) -> dict[str, Any]:
    selector, text = _first_locator(field)
    field_key = str(field.get("field_key") or field.get("name") or "field")
    selected_locator = (
        {"by": "selector", "value": selector}
        if selector
        else ({"by": "text", "value": text} if text else {})
    )
    locator_status = (
        "verified_static"
        if selected_locator.get("by") == "selector"
        else ("candidate_only" if selected_locator else "missing")
    )
    return {
        "field_key": field_key,
        "selector": selector,
        "text_selector": f"text={text}" if text else None,
        "tag": str(field.get("tag") or "input"),
        "type": str(field.get("type") or "text"),
        "required": bool(field.get("required", False)),
        "source": "static-product-package",
        "locators": [dict(item) for item in field.get("locators", []) or [] if isinstance(item, Mapping)],
        "selected_locator": selected_locator,
        "locator_status": locator_status,
        "value_strategy": field.get("value_strategy") or "test-data-profile",
        "raw": {
            "tag": str(field.get("tag") or "input"),
            "type": str(field.get("type") or "text"),
            "label": field.get("label") or field_key,
        },
    }


def _action_record(action: Mapping[str, Any]) -> dict[str, Any]:
    selector, text_locator = _first_locator(action)
    text = str(action.get("text") or text_locator or action.get("action_key") or "")
    selected_locator = (
        {"by": "selector", "value": selector}
        if selector
        else ({"by": "text", "value": text_locator} if text_locator else {})
    )
    return {
        "action_key": action.get("action_key"),
        "selector": selector,
        "text_selector": f"text={text_locator}" if text_locator else None,
        "text": text,
        "tag": str(action.get("tag") or "button"),
        "required": bool(action.get("required", False)),
        "source": "static-product-package",
        "locators": [dict(item) for item in action.get("locators", []) or [] if isinstance(item, Mapping)],
        "selected_locator": selected_locator,
        "locator_status": "verified_static" if selected_locator else "missing",
    }


def _planned_page_catalog(regression_paths: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    catalog_by_node: dict[str, dict[str, Any]] = {}
    for path_item in regression_paths:
        path_id = str(path_item.get("path_id") or "")
        for page_key in path_item.get("page_keys", []) or []:
            if not isinstance(page_key, Mapping):
                continue
            node_id = str(page_key.get("node_id") or "")
            if not node_id:
                continue
            item = catalog_by_node.setdefault(
                node_id,
                {
                    "planned_page_id": f"PP-{len(catalog_by_node) + 1:03d}",
                    "node_id": node_id,
                    "page_key": page_key.get("page_key"),
                    "url_pattern": page_key.get("url_pattern"),
                    "state": page_key.get("state", {}),
                    "path_ids": [],
                },
            )
            if path_id and path_id not in item["path_ids"]:
                item["path_ids"].append(path_id)
    return list(catalog_by_node.values())


def _source_path_ids(node_id: str, regression_paths: list[Mapping[str, Any]]) -> list[str]:
    return [
        str(path_item.get("path_id"))
        for path_item in regression_paths
        if node_id in _business_nodes(path_item) and path_item.get("path_id")
    ]


def _probe_owner(kind: str) -> str:
    if kind in {"field", "action"}:
        return "ai-testing"
    return "ai-fullstack"


def _probe_request_for_missing_page_model(
    *,
    path_id: str,
    sequence: int,
    node_id: str,
    planned_by_node: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    planned = dict(planned_by_node.get(node_id, {}) or {})
    return {
        "probe_id": f"TP-{path_id}-{sequence:03d}",
        "path_id": path_id,
        "kind": "page_model",
        "node_id": node_id,
        "page_key": planned.get("page_key"),
        "reason": "missing_page_model",
        "owner": _probe_owner("page_model"),
        "probe_scope": "current_or_reachable_node",
        "probe_strategy": "discover_page_model_by_agent2_node",
        "url_patterns": [str(planned["url_pattern"])] if planned.get("url_pattern") else [],
        "acceptance_criteria": [
            "page_model_id=mapped",
            "match_contract.entry_signals_present",
            "field_map_or_selector_map_present",
            "static_product_package_updated",
        ],
    }


def _probe_request_for_required_field(
    *,
    path_id: str,
    sequence: int,
    record: Mapping[str, Any],
    field: Mapping[str, Any],
) -> dict[str, Any] | None:
    if not field.get("required") or field.get("locator_status") == "verified_static":
        return None
    return {
        "probe_id": f"TP-{path_id}-{sequence:03d}",
        "path_id": path_id,
        "kind": "field",
        "node_id": (record.get("matched_node_ids", []) or [None])[0],
        "page_model_id": record.get("page_model_id"),
        "page_content_record_id": record.get("page_content_record_id"),
        "field_key": field.get("field_key"),
        "reason": "required_field_needs_verified_locator",
        "owner": _probe_owner("field"),
        "current_locator_status": field.get("locator_status") or "missing",
        "candidate_locators": [dict(item) for item in field.get("locators", []) or []],
        "probe_scope": "current_node_only",
        "probe_strategy": "resolve_field_by_label_context",
        "acceptance_criteria": [
            "locator_status=verified_static",
            "selected_locator.by=selector",
            "mock_status=mapped",
        ],
    }


def _probe_request_for_required_action(
    *,
    path_id: str,
    sequence: int,
    record: Mapping[str, Any],
    action: Mapping[str, Any],
) -> dict[str, Any] | None:
    if not action.get("required") or action.get("locator_status") == "verified_static":
        return None
    return {
        "probe_id": f"TP-{path_id}-{sequence:03d}",
        "path_id": path_id,
        "kind": "action",
        "node_id": (record.get("matched_node_ids", []) or [None])[0],
        "page_model_id": record.get("page_model_id"),
        "page_content_record_id": record.get("page_content_record_id"),
        "action_key": action.get("action_key"),
        "reason": "required_action_needs_clickable_locator",
        "owner": _probe_owner("action"),
        "current_locator_status": action.get("locator_status") or "missing",
        "candidate_locators": [dict(item) for item in action.get("locators", []) or []],
        "probe_scope": "current_node_only",
        "probe_strategy": "resolve_action_by_text_role_or_selector",
        "acceptance_criteria": [
            "locator_status=verified_static",
            "selected_locator.by=selector_or_text",
            "click_strategy=supported",
        ],
    }


def _targeted_probe_plan_for_path(
    *,
    path_id: str,
    missing_nodes: list[str],
    matched_nodes: list[str],
    record_by_node: Mapping[str, Mapping[str, Any]],
    planned_by_node: Mapping[str, Mapping[str, Any]],
    knowledge_hints: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    requests: list[dict[str, Any]] = []
    for node_id in missing_nodes:
        requests.append(
            _probe_request_for_missing_page_model(
                path_id=path_id,
                sequence=len(requests) + 1,
                node_id=node_id,
                planned_by_node=planned_by_node,
            )
        )
    for node_id in matched_nodes:
        record = dict(record_by_node.get(node_id, {}) or {})
        for field in record.get("field_map", []) or []:
            request = _probe_request_for_required_field(
                path_id=path_id,
                sequence=len(requests) + 1,
                record=record,
                field=field,
            )
            if request:
                requests.append(request)
        for action in (record.get("selector_map", {}) or {}).get("actions", []) or []:
            request = _probe_request_for_required_action(
                path_id=path_id,
                sequence=len(requests) + 1,
                record=record,
                action=action,
            )
            if request:
                requests.append(request)
    plan = {
        "source": "agent3.static-product-package",
        "version": "1.0",
        "status": "required" if requests else "not_required",
        "requests": requests,
        "summary": _targeted_probe_summary(requests),
    }
    return enrich_targeted_probe_plan(plan, knowledge_hints) if knowledge_hints else plan


def _targeted_probe_summary(requests: list[Mapping[str, Any]]) -> dict[str, int]:
    return {
        "request_count": len(requests),
        "field_request_count": len([item for item in requests if item.get("kind") == "field"]),
        "action_request_count": len([item for item in requests if item.get("kind") == "action"]),
        "page_model_request_count": len([item for item in requests if item.get("kind") == "page_model"]),
        "transition_action_request_count": len(
            [item for item in requests if item.get("kind") == "transition_action"]
        ),
    }


def _aggregate_targeted_probe_plan(path_results: list[Mapping[str, Any]]) -> dict[str, Any]:
    requests = [
        dict(request)
        for path in path_results
        for request in ((path.get("targeted_probe_plan", {}) or {}).get("requests", []) or [])
    ]
    return {
        "source": "agent3.static-product-package",
        "version": "1.0",
        "status": "required" if requests else "not_required",
        "requests": requests,
        "summary": _targeted_probe_summary(requests),
    }


def _missing_report_item(request: Mapping[str, Any]) -> dict[str, Any]:
    kind = str(request.get("kind") or "unknown")
    return {
        "kind": kind,
        "owner": request.get("owner") or _probe_owner(kind),
        "probe_id": request.get("probe_id"),
        "path_id": request.get("path_id"),
        "node_id": request.get("node_id"),
        "page_model_id": request.get("page_model_id"),
        "page_content_record_id": request.get("page_content_record_id"),
        "field_key": request.get("field_key"),
        "action_key": request.get("action_key"),
        "page_key": request.get("page_key"),
        "reason": request.get("reason"),
        "probe_scope": request.get("probe_scope"),
        "probe_strategy": request.get("probe_strategy"),
        "acceptance_criteria": list(request.get("acceptance_criteria", []) or []),
        "knowledge_hint_status": request.get("knowledge_hint_status"),
        "knowledge_evidence": request.get("knowledge_evidence"),
    }


def _build_static_missing_report(targeted_probe_plan: Mapping[str, Any]) -> dict[str, Any]:
    requests = [dict(item) for item in targeted_probe_plan.get("requests", []) or []]
    items = [_missing_report_item(request) for request in requests]
    required_fields = [item for item in items if item.get("kind") == "field"]
    required_actions = [item for item in items if item.get("kind") == "action"]
    page_models = [item for item in items if item.get("kind") == "page_model"]
    transition_actions = [item for item in items if item.get("kind") == "transition_action"]
    return {
        "source": "agent3.static-product-package",
        "version": "1.0",
        "selector_override_priority": [dict(item) for item in _SELECTOR_OVERRIDE_PRIORITY],
        "required_fields": required_fields,
        "required_actions": required_actions,
        "page_models": page_models,
        "transition_actions": transition_actions,
        "summary": {
            "missing_count": len(items),
            "required_field_count": len(required_fields),
            "required_action_count": len(required_actions),
            "page_model_count": len(page_models),
            "transition_action_count": len(transition_actions),
        },
    }


def _page_content_records(
    package: StaticProductPackage,
    regression_paths: list[Mapping[str, Any]],
    planned_catalog: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    model_by_node = package.page_model_by_node()
    referenced_nodes = {
        node_id
        for path_item in regression_paths
        for node_id in _business_nodes(path_item)
    }
    catalog_by_node = {item["node_id"]: item for item in planned_catalog}
    records: list[dict[str, Any]] = []
    for sequence, node_id in enumerate(sorted(referenced_nodes), start=1):
        model = model_by_node.get(node_id)
        if not model:
            continue
        fields = [_field_record(field) for field in model.get("fields", []) or [] if isinstance(field, Mapping)]
        actions = [_action_record(action) for action in model.get("actions", []) or [] if isinstance(action, Mapping)]
        url_patterns = list(model.get("url_patterns", []) or [])
        actual_url = url_patterns[0] if url_patterns else package.entry_url
        catalog_item = catalog_by_node.get(node_id, {})
        record_id = f"PCR-STATIC-{sequence:03d}"
        records.append(
            {
                "page_content_record_id": record_id,
                "page_model_id": model.get("page_model_id"),
                "actual_url": actual_url,
                "actual_page_key": catalog_item.get("page_key") or model.get("page_key_pattern"),
                "title": model.get("title") or str(node_id).removeprefix("NODE-").replace("-", " ").title(),
                "dom_signature": f"static:{model.get('page_model_id') or node_id}:v1",
                "body_text_excerpt": " ".join(model.get("match_contract", {}).get("entry_signals", []) or []),
                "field_count": len(fields),
                "action_count": len(actions),
                "field_map": fields,
                "selector_map": {
                    "page_key": catalog_item.get("page_key") or model.get("page_key_pattern"),
                    "url": actual_url,
                    "fields": fields,
                    "actions": actions,
                },
                "matched_planned_page_ids": [catalog_item.get("planned_page_id")] if catalog_item else [],
                "matched_node_ids": [node_id],
                "source_path_ids": _source_path_ids(node_id, regression_paths),
            }
        )
    return records


def _path_exploration_results(
    package: StaticProductPackage,
    state: Mapping[str, Any],
    planned_catalog: list[dict[str, Any]],
    page_content_records: list[dict[str, Any]],
    knowledge_hints: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    record_by_node = {
        node_id: record
        for record in page_content_records
        for node_id in record.get("matched_node_ids", []) or []
    }
    planned_by_node = {item["node_id"]: item for item in planned_catalog}
    bindings = package.binding_by_path_id()
    results: list[dict[str, Any]] = []
    for path_item in state.get("regression_paths", []) or []:
        path_id = str(path_item.get("path_id") or "")
        required_nodes = _business_nodes(path_item)
        matched_nodes = [node_id for node_id in required_nodes if node_id in record_by_node]
        missing_nodes = [node_id for node_id in required_nodes if node_id not in record_by_node]
        binding = bindings.get(path_id, {})
        target_node = str(binding.get("expected_target_node") or (required_nodes[-1] if required_nodes else ""))
        is_complete = not missing_nodes
        status = "explored" if is_complete else ("partial" if matched_nodes else "blocked")
        blocked_node = missing_nodes[0] if missing_nodes else None
        targeted_probe_plan = _targeted_probe_plan_for_path(
            path_id=path_id,
            missing_nodes=missing_nodes,
            matched_nodes=matched_nodes,
            record_by_node=record_by_node,
            planned_by_node=planned_by_node,
            knowledge_hints=knowledge_hints,
        )
        contract_complete = is_complete and targeted_probe_plan["summary"]["request_count"] == 0
        results.append(
            {
                "path_id": path_id,
                "case_ids": list(path_item.get("case_ids", []) or []),
                "path_status": status if contract_complete else "blocked",
                "target_node": target_node or None,
                "reached_node": matched_nodes[-1] if matched_nodes else None,
                "blocked_node": blocked_node,
                "blocked_reason": (
                    None
                    if contract_complete
                    else (
                        f"Static package missing page model for {blocked_node}"
                        if blocked_node
                        else "Static package requires targeted probe"
                    )
                ),
                "evidence_source": "agent3-static-first",
                "planned_page_refs": [
                    planned_by_node[node_id]["planned_page_id"]
                    for node_id in required_nodes
                    if node_id in planned_by_node
                ],
                "page_content_refs": [
                    record_by_node[node_id]["page_content_record_id"]
                    for node_id in matched_nodes
                ],
                "node_progress": [
                    {
                        "node_id": node_id,
                        "status": "matched" if node_id in matched_nodes else "blocked",
                        "matched": node_id in matched_nodes,
                    }
                    for node_id in required_nodes
                ],
                "completion_rule": {
                    "source": "agent3.static-contract",
                    "target_node": target_node or None,
                    "required_nodes": required_nodes,
                    "matched_nodes": matched_nodes,
                    "missing_nodes": missing_nodes,
                    "is_complete": contract_complete,
                },
                "action_chain": [],
                "node_execution_trace": [],
                "targeted_probe_plan": targeted_probe_plan,
            }
        )
    return results


def _pages_from_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "page_key": str(record.get("actual_page_key") or record.get("page_model_id") or record["page_content_record_id"]),
            "url": record.get("actual_url"),
            "source_url": record.get("actual_url"),
            "title": record.get("title"),
            "field_count": int(record.get("field_count") or 0),
            "action_count": int(record.get("action_count") or 0),
            "fields": list(record.get("field_map", []) or []),
            "actions": list((record.get("selector_map", {}) or {}).get("actions", []) or []),
            "primary_actions": list((record.get("selector_map", {}) or {}).get("actions", []) or [])[:1],
            "candidate_links": [],
        }
        for record in records
    ]


def _apply_static_test_data(
    scenarios: list[dict[str, Any]],
    package: StaticProductPackage,
) -> list[dict[str, Any]]:
    bindings = package.binding_by_path_id()
    updated: list[dict[str, Any]] = []
    for scenario in scenarios:
        binding = bindings.get(str(scenario.get("path_id") or ""), {})
        profile_id = str(binding.get("test_data_profile_id") or "")
        profile = package.test_data_profiles.get(profile_id, {})
        values = dict(profile.get("values", {}) or {})
        next_scenario = dict(scenario)
        if profile_id:
            next_scenario["mock_data_profile_id"] = profile_id
        if values:
            next_scenario["mock_data"] = {**dict(scenario.get("mock_data", {}) or {}), **values}
        updated.append(next_scenario)
    return updated


def build_static_agent4_contract(
    package: StaticProductPackage,
    state: Mapping[str, Any],
    *,
    knowledge_hints: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile static package assets into page_registry and scenarios."""
    entry_url = str(state.get("entry_url") or package.entry_url or "")
    regression_paths = list(state.get("regression_paths", []) or [])
    planned_catalog = _planned_page_catalog(regression_paths)
    page_content_records = _page_content_records(package, regression_paths, planned_catalog)
    path_results = _path_exploration_results(
        package,
        state,
        planned_catalog,
        page_content_records,
        knowledge_hints=knowledge_hints,
    )
    exploration_contract = _build_exploration_contract(path_results)
    targeted_probe_plan = _aggregate_targeted_probe_plan(path_results)
    if knowledge_hints:
        targeted_probe_plan = enrich_targeted_probe_plan(targeted_probe_plan, knowledge_hints)
    missing_report = _build_static_missing_report(targeted_probe_plan)
    knowledge_assist = knowledge_assist_summary(knowledge_hints)
    pages = _pages_from_records(page_content_records)
    page_registry = {
        "product_id": package.product_id,
        "entry_url": entry_url,
        "platform": str(package.config.get("platform") or platform_from_entry_url(entry_url)),
        "generated_by": "explore_agent.static-first",
        "planned_flow_version": package.config.get("default_flow_id"),
        "planned_page_catalog": planned_catalog,
        "page_content_records": page_content_records,
        "pages": pages,
        "primary_actions": [
            action
            for page in pages
            for action in page.get("primary_actions", []) or []
        ][:5],
        "path_exploration_results": path_results,
        "exploration_contract": exploration_contract,
        "targeted_probe_plan": targeted_probe_plan,
        "missing_report": missing_report,
        "selector_override_priority": [dict(item) for item in _SELECTOR_OVERRIDE_PRIORITY],
        "static_contract": {
            "source": "agent3.static-product-package",
            "requires_targeted_probe": targeted_probe_plan["summary"]["request_count"] > 0,
            "targeted_probe_request_count": targeted_probe_plan["summary"]["request_count"],
            "missing_report": missing_report,
            "selector_override_priority": [dict(item) for item in _SELECTOR_OVERRIDE_PRIORITY],
            "knowledge_assist": knowledge_assist,
        },
    }
    contract_state = {
        **dict(state),
        "entry_url": entry_url,
        "page_registry": page_registry,
    }
    scenarios = _apply_static_test_data(build_scenarios(contract_state), package)
    explore_trace = {
        "product_id": package.product_id,
        "visited_urls": [],
        "discovered_page_count": len(pages),
        "planned_page_count": len(planned_catalog),
        "page_content_record_count": len(page_content_records),
        "exploration_contract": exploration_contract,
        "static_contract": page_registry["static_contract"],
        "targeted_probe_plan": targeted_probe_plan,
        "missing_report": missing_report,
        "knowledge_hints": knowledge_assist,
        "path_exploration_summary": {
            "total": len(path_results),
            "explored": sum(1 for item in path_results if item.get("path_status") == "explored"),
            "partial": sum(1 for item in path_results if item.get("path_status") == "partial"),
            "blocked": sum(1 for item in path_results if item.get("path_status") == "blocked"),
        },
        "warnings": [],
        "source": "agent3.static-first",
    }
    return {
        "page_registry": page_registry,
        "explore_trace": explore_trace,
        "path_exploration_results": path_results,
        "scenarios": scenarios,
        "warnings": [],
    }

"""Deterministic knowledge artifact generation for insurance products."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from e2e_agent.core.knowledge_base import normalise_workflow_cases


def _dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _text_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _safe_int(value: Any, default: int = 0) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _feature_fields(features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    for index, feature in enumerate(features, start=1):
        name = str(feature.get("name") or f"Feature {index}").strip()
        fields.append(
            {
                "field_id": f"FIELD-{index:03d}",
                "name": name,
                "source_feature_id": str(feature.get("feature_id") or f"FEAT-{index:03d}"),
                "priority": str(feature.get("priority") or "P1"),
                "rules": _text_list(feature.get("acceptance_criteria")),
            }
        )
    return fields


def _flow_pages(application_flow: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    seen: set[str] = set()
    for step in application_flow:
        page = str(step.get("page") or "Unknown").strip()
        if not page or page in seen:
            continue
        seen.add(page)
        pages.append(
            {
                "page_id": f"PAGE-{len(pages) + 1:03d}",
                "name": page,
                "actions": [
                    str(item.get("action"))
                    for item in application_flow
                    if str(item.get("page") or "").strip() == page and item.get("action")
                ],
            }
        )
    return pages


def _normalise_page_probe(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or not value:
        return {}
    fields = _dict_list(value.get("fields"))
    actions = _dict_list(value.get("actions"))
    primary_actions = _dict_list(value.get("primary_actions"))
    return {
        **value,
        "status": str(value.get("status") or "completed"),
        "mode": str(value.get("mode") or "page-probe"),
        "url": str(value.get("url") or value.get("entry_url") or ""),
        "title": str(value.get("title") or "Entry Page"),
        "body_text_excerpt": str(value.get("body_text_excerpt") or "")[:1200],
        "fields": fields,
        "actions": actions,
        "primary_actions": primary_actions,
        "field_count": _safe_int(value.get("field_count"), len(fields)),
        "action_count": _safe_int(value.get("action_count"), len(actions)),
        "primary_action_count": _safe_int(value.get("primary_action_count"), len(primary_actions)),
    }


def _page_probe_record(page_probe: dict[str, Any]) -> dict[str, Any] | None:
    if not page_probe:
        return None
    return {
        "page_id": "PAGE-PROBE-001",
        "name": str(page_probe.get("title") or "Entry Page"),
        "url": str(page_probe.get("url") or page_probe.get("entry_url") or ""),
        "source": "page-probe",
        "field_count": _safe_int(page_probe.get("field_count"), 0),
        "action_count": _safe_int(page_probe.get("action_count"), 0),
        "body_text_excerpt": str(page_probe.get("body_text_excerpt") or ""),
        "primary_actions": list(page_probe.get("primary_actions", []) or []),
    }


def _page_probe_fields(page_probe: dict[str, Any], *, start_index: int) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    for offset, field in enumerate(_dict_list(page_probe.get("fields")), start=0):
        name = str(
            field.get("label")
            or field.get("placeholder")
            or field.get("name")
            or field.get("id")
            or field.get("selector")
            or f"Observed field {offset + 1}"
        ).strip()
        fields.append(
            {
                "field_id": f"FIELD-PROBE-{start_index + offset:03d}",
                "name": name,
                "source": "page-probe",
                "page_url": str(page_probe.get("url") or ""),
                "selector": str(field.get("selector") or ""),
                "tag": str(field.get("tag") or ""),
                "type": str(field.get("type") or ""),
                "priority": "observed",
                "required": bool(field.get("required")),
                "rules": [],
            }
        )
    return fields


def _infer_workflow_cases(
    *,
    product_id: str,
    features: list[dict[str, Any]],
    application_flow: list[dict[str, Any]],
) -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    flow_steps = [
        str(step.get("action") or step.get("page") or "").strip()
        for step in application_flow
        if str(step.get("action") or step.get("page") or "").strip()
    ]
    for index, feature in enumerate(features, start=1):
        criteria = _text_list(feature.get("acceptance_criteria"))
        title = str(feature.get("name") or f"Feature {index}").strip()
        cases.append(
            {
                "case_id": f"KLG-FEAT-{index:03d}",
                "title": title,
                "priority": str(feature.get("priority") or "P1"),
                "steps": flow_steps[:4] or [title],
                "assertions": criteria[:4] or [f"{title} completes successfully"],
                "source": "knowledge.inferred_from_prd_analysis",
            }
        )
    return normalise_workflow_cases(
        {
            "schema_version": "1.0",
            "product_id": product_id,
            "source": "knowledge.inferred_from_prd_analysis",
            "cases": cases,
        }
    )


def _render_list(lines: list[str]) -> str:
    return "\n".join(f"- {line}" for line in lines) if lines else "- None"


def _render_knowledge_markdown(artifacts: dict[str, Any]) -> str:
    knowledge_base = artifacts["knowledge_base"]
    workflow_cases = artifacts["workflow_cases"]["cases"]
    fields = artifacts["field_catalog"]["fields"]
    page_probe = artifacts.get("page_probe", {})
    lines = [
        f"# {knowledge_base['product_name']} Knowledge Base",
        "",
        "## Summary",
        f"- Product ID: {knowledge_base['product_id']}",
        f"- Generation mode: {knowledge_base['generation_mode']}",
        f"- Feature count: {knowledge_base['source_summary']['feature_count']}",
        f"- Workflow case count: {len(workflow_cases)}",
        f"- Page probe status: {artifacts.get('exploration', {}).get('status', 'not_requested')}",
        "",
        "## Workflow Cases",
    ]
    for case in workflow_cases:
        lines.append(f"- {case['case_id']} {case['title']} ({case['priority']})")
    if page_probe:
        lines.extend(["", "## Page Probe"])
        lines.append(f"- URL: {page_probe.get('url') or page_probe.get('entry_url') or ''}")
        lines.append(f"- Title: {page_probe.get('title') or ''}")
        lines.append(f"- Fields: {page_probe.get('field_count', 0)}")
        lines.append(f"- Actions: {page_probe.get('action_count', 0)}")
    lines.extend(["", "## Field Catalog"])
    for field in fields:
        lines.append(f"- {field['field_id']} {field['name']} ({field.get('priority', 'observed')})")
    return "\n".join(lines).rstrip() + "\n"


def _render_main_workflow(artifacts: dict[str, Any]) -> str:
    pages = artifacts["ui_ontology"]["pages"]
    return "# Main Workflow\n\n" + _render_list(
        [
            f"{page['page_id']} {page['name']}: {', '.join(page.get('actions', [])) or 'observe page'}"
            for page in pages
        ]
    ) + "\n"


def _render_freedom_test(artifacts: dict[str, Any]) -> str:
    cases = artifacts["workflow_cases"]["cases"]
    return "# Freedom Test Ideas\n\n" + _render_list(
        [f"{case['case_id']}: vary data around {case['title']}" for case in cases]
    ) + "\n"


def _render_log(artifacts: dict[str, Any]) -> str:
    warnings = artifacts.get("warnings", [])
    exploration = artifacts.get("exploration", {})
    lines = [
        "# Knowledge Generation Log",
        "",
        f"- Exploration status: {exploration.get('status', 'not_requested')}",
        f"- Warning count: {len(warnings)}",
    ]
    lines.extend(f"- Warning: {warning}" for warning in warnings)
    return "\n".join(lines).rstrip() + "\n"


def build_knowledge_artifacts(payload: dict[str, Any]) -> dict[str, Any]:
    product_id = str(payload.get("product_id") or "product").strip() or "product"
    product_name = str(payload.get("product_name") or product_id).strip()
    prd_analysis = payload.get("prd_analysis") if isinstance(payload.get("prd_analysis"), dict) else {}
    features = _dict_list(prd_analysis.get("features"))
    application_flow = _dict_list(prd_analysis.get("application_flow"))
    warnings: list[str] = []

    explicit_workflow_cases = payload.get("workflow_cases")
    if explicit_workflow_cases:
        workflow_cases = normalise_workflow_cases(explicit_workflow_cases)
        workflow_cases["product_id"] = product_id
    else:
        workflow_cases = _infer_workflow_cases(
            product_id=product_id,
            features=features,
            application_flow=application_flow,
        )

    entry_url = str(payload.get("entry_url") or "").strip()
    exploration_mode = str(payload.get("exploration_mode") or "materials-only").strip()
    page_probe = _normalise_page_probe(payload.get("page_probe"))
    page_probe_error = str(payload.get("page_probe_error") or "").strip()
    if page_probe:
        warnings.extend(str(item) for item in page_probe.get("warnings", []) or [] if str(item).strip())
        exploration = {
            "status": str(page_probe.get("status") or "completed"),
            "mode": "page-probe",
            "entry_url": entry_url or str(page_probe.get("entry_url") or ""),
            "url": str(page_probe.get("url") or ""),
            "screenshot_path": str(page_probe.get("screenshot_path") or ""),
        }
    elif page_probe_error:
        warnings.append(f"Page probe failed: {page_probe_error}")
        exploration = {
            "status": "failed",
            "mode": "page-probe",
            "entry_url": entry_url,
            "error": page_probe_error,
        }
    elif exploration_mode == "page-probe" and entry_url:
        warnings.append("Page probe was requested but no page_probe snapshot was supplied")
        exploration = {
            "status": "pending_browser_exploration",
            "mode": "page-probe",
            "entry_url": entry_url,
        }
    elif entry_url or exploration_mode == "mcp":
        warnings.append("Browser/MCP exploration is not executed by the phase-one knowledge generator")
        exploration = {
            "status": "pending_browser_exploration",
            "mode": exploration_mode,
            "entry_url": entry_url,
        }
    else:
        exploration = {"status": "not_requested", "mode": "materials-only", "entry_url": ""}

    knowledge_base = {
        "schema_version": "1.0",
        "product_id": product_id,
        "product_name": product_name,
        "generation_mode": "page-probe" if page_probe else "materials-only",
        "source_summary": {
            "feature_count": len(features),
            "application_flow_step_count": len(application_flow),
            "workflow_case_count": len(workflow_cases.get("cases", [])),
            "page_probe_count": 1 if page_probe else 0,
            "page_probe_field_count": _safe_int(page_probe.get("field_count"), 0) if page_probe else 0,
            "page_probe_action_count": _safe_int(page_probe.get("action_count"), 0) if page_probe else 0,
        },
        "business_terms": [
            {
                "term": str(feature.get("name") or f"Feature {index}"),
                "source_feature_id": str(feature.get("feature_id") or f"FEAT-{index:03d}"),
            }
            for index, feature in enumerate(features, start=1)
        ],
    }
    ui_ontology = {
        "schema_version": "1.0",
        "product_id": product_id,
        "pages": _flow_pages(application_flow),
    }
    probe_page = _page_probe_record(page_probe)
    if probe_page:
        ui_ontology["pages"].append(probe_page)
    field_catalog = {
        "schema_version": "1.0",
        "product_id": product_id,
        "fields": _feature_fields(features),
    }
    field_catalog["fields"].extend(_page_probe_fields(page_probe, start_index=len(field_catalog["fields"]) + 1))

    artifacts: dict[str, Any] = {
        "knowledge_base": knowledge_base,
        "ui_ontology": ui_ontology,
        "field_catalog": field_catalog,
        "workflow_cases": workflow_cases,
        "page_probe": page_probe,
        "warnings": warnings,
        "exploration": exploration,
    }
    artifacts["markdown"] = {
        "knowledge.md": _render_knowledge_markdown(artifacts),
        "main_workflow.md": _render_main_workflow(artifacts),
        "freedom_test.md": _render_freedom_test(artifacts),
        "log.md": _render_log(artifacts),
    }
    return artifacts


def materialise_knowledge_artifacts(
    root_dir: Path,
    product_id: str,
    artifacts: dict[str, Any],
) -> dict[str, str]:
    knowledge_root = root_dir / "knowledge" / product_id
    knowledge_root.mkdir(parents=True, exist_ok=True)
    outputs = {
        "knowledge_base": knowledge_root / "knowledge-base.json",
        "ui_ontology": knowledge_root / "ui-ontology.json",
        "field_catalog": knowledge_root / "field-catalog.json",
        "workflow_cases": knowledge_root / "workflow-cases.json",
    }
    for key, path in outputs.items():
        path.write_text(
            json.dumps(artifacts[key], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    mcp_outputs: dict[str, Path] = {}
    page_probe = artifacts.get("page_probe") if isinstance(artifacts.get("page_probe"), dict) else {}
    exploration = artifacts.get("exploration") if isinstance(artifacts.get("exploration"), dict) else {}
    if page_probe or str(exploration.get("mode") or "") == "page-probe":
        mcp_dir = knowledge_root / "mcp"
        mcp_dir.mkdir(parents=True, exist_ok=True)
        fields = _dict_list(page_probe.get("fields")) if page_probe else []
        actions = _dict_list(page_probe.get("actions")) if page_probe else []
        snapshot_count = 1 if page_probe else 0
        field_count = _safe_int(page_probe.get("field_count"), len(fields)) if page_probe else 0
        action_count = _safe_int(page_probe.get("action_count"), len(actions)) if page_probe else 0

        if page_probe:
            page_snapshots_path = mcp_dir / "page-snapshots.json"
            page_snapshots_path.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "product_id": product_id,
                        "snapshots": [page_probe],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            mcp_outputs["mcp_page_snapshots"] = page_snapshots_path

        evidence = {
            "schema_version": "1.0",
            "product_id": product_id,
            "mode": str(exploration.get("mode") or "page-probe"),
            "status": exploration.get("status"),
            "entry_url": exploration.get("entry_url"),
            "snapshot_count": snapshot_count,
            "field_count": field_count,
            "action_count": action_count,
            "screenshot_path": page_probe.get("screenshot_path") if page_probe else "",
        }
        if exploration.get("error"):
            evidence["error"] = str(exploration.get("error"))
        exploration_evidence_path = mcp_dir / "exploration-evidence.json"
        exploration_evidence_path.write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        mcp_outputs["mcp_exploration_evidence"] = exploration_evidence_path

    markdown_outputs: dict[str, Path] = {}
    for filename, text in artifacts["markdown"].items():
        path = knowledge_root / filename
        path.write_text(str(text), encoding="utf-8")
        markdown_outputs[filename.removesuffix(".md")] = path

    return {
        **{key: str(path) for key, path in outputs.items()},
        **{key: str(path) for key, path in mcp_outputs.items()},
        **{key: str(path) for key, path in markdown_outputs.items()},
    }

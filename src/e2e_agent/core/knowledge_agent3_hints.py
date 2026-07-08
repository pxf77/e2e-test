"""Normalize optional product knowledge artifacts into Agent3 exploration hints."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping


_NODE_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "NODE-product-detail",
        (
            "product detail",
            "product/detail",
            "product/index",
            "media.html",
            "产品详情",
            "保障责任",
            "投保须知",
            "立即投保",
            "我要投保",
        ),
    ),
    (
        "NODE-premium-calculation",
        ("premium calculation", "premium", "quote", "保费", "试算", "费率"),
    ),
    (
        "NODE-suitability",
        ("suitability", "适当性", "风险承受", "保险需求", "问卷"),
    ),
    (
        "NODE-health-notice",
        ("health notice", "health-notice", "健康告知", "如实告知"),
    ),
    (
        "NODE-insure-form",
        ("insure form", "product/insure", "投保信息", "投保人", "被保险人", "证件有效期"),
    ),
    (
        "NODE-underwriting",
        ("underwriting", "核保", "承保", "标准核保"),
    ),
    (
        "NODE-risk-control",
        ("risk-control", "authentication", "认证", "风控", "智能认证", "人脸识别"),
    ),
    (
        "NODE-payment",
        ("payment", "/pay", "支付", "付款", "银行签约", "首期保费", "微信", "支付宝"),
    ),
    (
        "NODE-policy-result",
        ("policy result", "pay/success", "result", "出单", "保单", "承保成功", "电子保单"),
    ),
    (
        "NODE-policy-service",
        ("policy service", "保全", "续期", "回访", "发票"),
    ),
    (
        "NODE-surrender",
        ("surrender", "退保", "解除合同"),
    ),
)

_ACTION_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("action.buy_now", ("buy now", "立即投保", "我要投保", "投保")),
    ("action.quote", ("premium", "quote", "保费", "试算")),
    ("action.submit", ("submit", "提交", "提交投保", "提交投保单")),
    ("action.pay", ("pay", "payment", "支付", "付款", "立即支付", "确认支付")),
    ("action.next", ("next", "continue", "下一步", "继续", "确认")),
    ("action.agree_all", ("agree", "同意", "已阅读", "确认无以上问题")),
)


def _empty_payload(product_id: str, root: Path, warnings: list[str] | None = None) -> dict[str, Any]:
    warning_list = list(warnings or [])
    return {
        "source": "knowledge.agent3-hints",
        "version": "1.0",
        "product_id": product_id,
        "knowledge_root": str(root),
        "available": False,
        "summary": {
            "page_hint_count": 0,
            "observed_page_hint_count": 0,
            "document_page_hint_count": 0,
            "field_hint_count": 0,
            "action_hint_count": 0,
            "warning_count": len(warning_list),
        },
        "pages": [],
        "field_hints": [],
        "mcp_exploration_evidence": {},
        "warnings": warning_list,
    }


def _resolve_knowledge_root(root_dir: str | Path, product_id: str) -> tuple[str, Path, list[str]]:
    product = str(product_id or "product").strip() or "product"
    base = (Path(root_dir) / "knowledge").resolve()
    if Path(product).is_absolute():
        return product, base, [f"invalid knowledge product id: {product}"]
    parts = [part for part in re.split(r"[\\/]+", product) if part]
    if not parts or any(part in {".", ".."} for part in parts):
        return product, base, [f"invalid knowledge product id: {product}"]
    root = base.joinpath(*parts).resolve()
    try:
        root.relative_to(base)
    except ValueError:
        return product, base, [f"invalid knowledge product id: {product}"]
    return product, root, []


def _read_json_object(path: Path, warnings: list[str]) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        warnings.append(f"{path.name} is invalid JSON: {exc.msg}")
        return {}
    if not isinstance(value, dict):
        warnings.append(f"{path.name} must contain a JSON object")
        return {}
    return value


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _unique_strings(values: list[Any], *, limit: int | None = None) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        key = _normalise_text(text)
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
        if limit is not None and len(result) >= limit:
            break
    return result


def _normalise_text(value: str) -> str:
    lowered = value.strip().lower()
    return re.sub(r"\s+", "", lowered)


def _slug(value: str, *, fallback: str) -> str:
    text = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", value.strip().lower()).strip("-")
    ascii_text = re.sub(r"[^0-9a-z-]+", "", text).strip("-")
    return ascii_text or fallback


def _text_from(*values: Any) -> str:
    parts: list[str] = []
    for value in values:
        if isinstance(value, Mapping):
            parts.extend(str(item or "") for item in value.values())
        elif isinstance(value, list):
            parts.extend(str(item or "") for item in value)
        else:
            parts.append(str(value or ""))
    return " ".join(part for part in parts if part)


def _infer_node_id(*values: Any) -> str:
    text_parts = [_normalise_text(_text_from(value)) for value in values]
    if not any(text_parts):
        return ""
    weights = [4, 3, 3, 2, 1]
    best_node = ""
    best_score = 0
    for node_id, hints in _NODE_HINTS:
        score = 0
        for index, part in enumerate(text_parts):
            if not part:
                continue
            weight = weights[index] if index < len(weights) else 1
            score += sum(weight for hint in hints if _normalise_text(hint) in part)
        if score > best_score:
            best_node = node_id
            best_score = score
    return best_node


def _infer_action_key(action: Mapping[str, Any]) -> str:
    haystack = _normalise_text(_text_from(action.get("text"), action.get("selector"), action.get("href")))
    for action_key, hints in _ACTION_HINTS:
        if any(_normalise_text(hint) in haystack for hint in hints):
            return action_key
    slug = _slug(str(action.get("text") or action.get("selector") or "action"), fallback="custom")
    return f"action.{slug.replace('-', '_')}"


def _locator_list(*, selector: Any = None, text: Any = None, label: Any = None) -> list[dict[str, str]]:
    locators: list[dict[str, str]] = []
    selector_text = str(selector or "").strip()
    if selector_text:
        locators.append({"by": "selector", "value": selector_text})
    text_value = str(text or "").strip()
    if text_value:
        locators.append({"by": "text", "value": text_value})
    label_value = str(label or "").strip()
    if label_value and label_value != text_value:
        locators.append({"by": "label_text", "value": label_value})
    return locators


def _compact_mcp_evidence(evidence_payload: Mapping[str, Any]) -> dict[str, Any]:
    if not evidence_payload:
        return {}
    return {
        "schema_version": evidence_payload.get("schema_version"),
        "product_id": evidence_payload.get("product_id"),
        "mode": evidence_payload.get("mode"),
        "status": evidence_payload.get("status"),
        "entry_url": evidence_payload.get("entry_url"),
        "snapshot_count": evidence_payload.get("snapshot_count"),
        "field_count": evidence_payload.get("field_count"),
        "action_count": evidence_payload.get("action_count"),
        "screenshot_path": evidence_payload.get("screenshot_path"),
    }


def _action_hint(action: Mapping[str, Any], *, source: str, evidence_status: str) -> dict[str, Any]:
    text = str(action.get("text") or "").strip()
    selector = str(action.get("selector") or "").strip()
    return {
        "action_key": _infer_action_key(action),
        "text": text,
        "selector": selector,
        "tag": action.get("tag"),
        "href": action.get("href"),
        "visible": bool(action.get("visible", True)),
        "locators": _locator_list(selector=selector, text=text),
        "source": source,
        "evidence_status": evidence_status,
        "confidence": 0.85 if selector else 0.65,
    }


def _field_hint(field: Mapping[str, Any], *, node_id: str, source: str, evidence_status: str) -> dict[str, Any]:
    label = str(field.get("label") or field.get("name") or field.get("placeholder") or "").strip()
    selector = str(field.get("selector") or "").strip()
    field_id = str(field.get("field_id") or field.get("field_key") or field.get("name") or "").strip()
    return {
        "field_id": field_id,
        "field_key": str(field.get("field_key") or field_id or _slug(label, fallback="field")).strip(),
        "name": str(field.get("name") or label or field_id).strip(),
        "node_id": node_id,
        "priority": field.get("priority"),
        "rules": _unique_strings(_as_list(field.get("rules")), limit=8),
        "required": bool(field.get("required", False)),
        "locators": _locator_list(selector=selector, label=label),
        "source": source,
        "evidence_status": evidence_status,
        "confidence": 0.8 if selector else 0.45,
    }


def _ui_page_hints(ui_ontology: Mapping[str, Any]) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    for index, page in enumerate(ui_ontology.get("pages", []) or [], start=1):
        if not isinstance(page, Mapping):
            continue
        actions = _unique_strings(_as_list(page.get("actions")), limit=8)
        name = str(page.get("name") or page.get("title") or "").strip()
        node_id = str(page.get("node_id") or "").strip() or _infer_node_id(
            page.get("url"),
            name,
            actions,
        )
        pages.append(
            {
                "hint_id": f"KAH-UI-{index:03d}",
                "page_id": page.get("page_id") or f"PAGE-{index:03d}",
                "page_name": name,
                "node_id": node_id,
                "actual_url": page.get("url"),
                "title": page.get("title") or name,
                "source": "ui-ontology",
                "evidence_status": "document-inferred",
                "confidence": 0.55 if node_id else 0.35,
                "entry_signals": _unique_strings([name, *actions], limit=5),
                "text_hints": _unique_strings([name, *actions], limit=8),
                "url_patterns": _unique_strings(_as_list(page.get("url_patterns")), limit=5),
                "observed_fields": [],
                "observed_actions": [_action_hint({"text": action}, source="ui-ontology", evidence_status="document-inferred") for action in actions],
            }
        )
    return pages


def _snapshot_hints(
    snapshot_payload: Mapping[str, Any],
    *,
    mcp_evidence: Mapping[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    pages: list[dict[str, Any]] = []
    field_hints: list[dict[str, Any]] = []
    evidence_summary = dict(mcp_evidence or {})
    snapshots = snapshot_payload.get("snapshots")
    if snapshots is None and snapshot_payload:
        snapshots = [snapshot_payload]
    for index, snapshot in enumerate(snapshots or [], start=1):
        if not isinstance(snapshot, Mapping):
            continue
        if str(snapshot.get("status") or "completed") not in {"completed", "success"}:
            continue
        actions = [
            _action_hint(action, source="mcp-page-snapshot", evidence_status="observed")
            for action in snapshot.get("actions", []) or []
            if isinstance(action, Mapping)
        ]
        primary_actions = [
            _action_hint(action, source="mcp-page-snapshot", evidence_status="observed")
            for action in snapshot.get("primary_actions", []) or []
            if isinstance(action, Mapping)
        ]
        action_texts = [item.get("text") for item in [*primary_actions, *actions]]
        node_id = _infer_node_id(
            snapshot.get("url"),
            snapshot.get("entry_url"),
            snapshot.get("title"),
            snapshot.get("body_text_excerpt"),
            action_texts,
        )
        observed_fields = []
        for field_index, field in enumerate(snapshot.get("fields", []) or [], start=1):
            if not isinstance(field, Mapping):
                continue
            field_hint = _field_hint(
                field,
                node_id=node_id,
                source="mcp-page-snapshot",
                evidence_status="observed",
            )
            if not field_hint["field_id"]:
                field_hint["field_id"] = f"MCP-FIELD-{index:03d}-{field_index:03d}"
            observed_fields.append(field_hint)
            field_hints.append(field_hint)
        pages.append(
            {
                "hint_id": f"KAH-MCP-{index:03d}",
                "page_id": snapshot.get("page_id") or f"MCP-PAGE-{index:03d}",
                "page_name": snapshot.get("page_name") or snapshot.get("title") or "",
                "node_id": node_id,
                "actual_url": snapshot.get("url") or snapshot.get("entry_url"),
                "title": snapshot.get("title"),
                "dom_signature": snapshot.get("dom_signature"),
                "screenshot_path": snapshot.get("screenshot_path") or evidence_summary.get("screenshot_path"),
                "mcp_evidence": evidence_summary,
                "source": "mcp-page-snapshot",
                "evidence_status": "observed",
                "confidence": 0.8 if node_id else 0.55,
                "entry_signals": _unique_strings(
                    [
                        snapshot.get("title"),
                        *action_texts,
                    ],
                    limit=5,
                ),
                "text_hints": _unique_strings(
                    [
                        snapshot.get("title"),
                        snapshot.get("body_text_excerpt"),
                        *action_texts,
                    ],
                    limit=8,
                ),
                "url_patterns": [],
                "observed_fields": observed_fields,
                "observed_actions": primary_actions or actions,
            }
        )
    return pages, field_hints


def _catalog_field_hints(field_catalog: Mapping[str, Any]) -> list[dict[str, Any]]:
    hints: list[dict[str, Any]] = []
    for field in field_catalog.get("fields", []) or []:
        if not isinstance(field, Mapping):
            continue
        node_id = str(field.get("node_id") or "").strip() or _infer_node_id(
            field.get("name"),
            field.get("source_feature_id"),
            field.get("rules"),
        )
        hints.append(
            _field_hint(
                field,
                node_id=node_id,
                source="field-catalog",
                evidence_status="document-inferred",
            )
        )
    return hints


def _summary(pages: list[Mapping[str, Any]], field_hints: list[Mapping[str, Any]], warnings: list[str]) -> dict[str, int]:
    observed_pages = [item for item in pages if item.get("evidence_status") == "observed"]
    action_count = sum(len(item.get("observed_actions", []) or []) for item in pages)
    return {
        "page_hint_count": len(pages),
        "observed_page_hint_count": len(observed_pages),
        "document_page_hint_count": len(pages) - len(observed_pages),
        "field_hint_count": len(field_hints),
        "action_hint_count": action_count,
        "warning_count": len(warnings),
    }


def load_knowledge_agent3_hints(root_dir: str | Path, product_id: str) -> dict[str, Any]:
    """Load optional knowledge artifacts as non-authoritative Agent3 hints."""
    product, root, product_warnings = _resolve_knowledge_root(root_dir, product_id)
    if product_warnings:
        return _empty_payload(product, root, product_warnings)
    if not root.exists():
        return _empty_payload(product, root, [f"knowledge root not found at {root}"])

    warnings: list[str] = []
    ui_ontology = _read_json_object(root / "ui-ontology.json", warnings)
    field_catalog = _read_json_object(root / "field-catalog.json", warnings)
    snapshot_payload = _read_json_object(root / "mcp" / "page-snapshots.json", warnings)
    evidence_payload = _read_json_object(root / "mcp" / "exploration-evidence.json", warnings)
    if evidence_payload and evidence_payload.get("status") not in {None, "completed", "success"}:
        warnings.append(f"MCP exploration evidence status is {evidence_payload.get('status')}")
    mcp_evidence = _compact_mcp_evidence(evidence_payload)

    pages = _ui_page_hints(ui_ontology)
    snapshot_pages, snapshot_fields = _snapshot_hints(snapshot_payload, mcp_evidence=mcp_evidence)
    pages.extend(snapshot_pages)
    field_hints = [*_catalog_field_hints(field_catalog), *snapshot_fields]
    available = bool(pages or field_hints)
    payload = _empty_payload(product, root, warnings)
    payload.update(
        {
            "available": available,
            "summary": _summary(pages, field_hints, warnings),
            "pages": pages,
            "field_hints": field_hints,
            "mcp_exploration_evidence": mcp_evidence,
            "warnings": warnings,
        }
    )
    return payload


def knowledge_assist_summary(knowledge_hints: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return a small trace-safe summary for Agent3 static contracts."""
    hints = dict(knowledge_hints or {})
    observed_page_hints = [
        {
            "hint_id": item.get("hint_id"),
            "node_id": item.get("node_id"),
            "page_id": item.get("page_id"),
            "page_name": item.get("page_name"),
            "actual_url": item.get("actual_url"),
            "title": item.get("title"),
            "dom_signature": item.get("dom_signature"),
            "screenshot_path": item.get("screenshot_path"),
            "mcp_evidence": dict(item.get("mcp_evidence", {}) or {}),
            "source": item.get("source"),
            "evidence_status": item.get("evidence_status"),
            "confidence": item.get("confidence"),
            "observed_field_count": len(item.get("observed_fields", []) or []),
            "observed_action_count": len(item.get("observed_actions", []) or []),
        }
        for item in hints.get("pages", []) or []
        if isinstance(item, Mapping) and item.get("evidence_status") == "observed"
    ][:3]
    return {
        "source": hints.get("source") or "knowledge.agent3-hints",
        "version": hints.get("version") or "1.0",
        "product_id": hints.get("product_id"),
        "knowledge_root": hints.get("knowledge_root"),
        "available": bool(hints.get("available")),
        "summary": dict(hints.get("summary", {}) or {}),
        "mcp_exploration_evidence": dict(hints.get("mcp_exploration_evidence", {}) or {}),
        "observed_page_hints": observed_page_hints,
        "warnings": list(hints.get("warnings", []) or []),
    }


def _page_evidence_for_node(knowledge_hints: Mapping[str, Any], node_id: str) -> list[dict[str, Any]]:
    candidates = [
        dict(item)
        for item in knowledge_hints.get("pages", []) or []
        if isinstance(item, Mapping) and str(item.get("node_id") or "") == node_id
    ]
    candidates.sort(key=lambda item: (item.get("evidence_status") != "observed", -float(item.get("confidence") or 0)))
    return candidates[:3]


def _field_evidence_for_node(knowledge_hints: Mapping[str, Any], node_id: str) -> list[dict[str, Any]]:
    candidates = [
        dict(item)
        for item in knowledge_hints.get("field_hints", []) or []
        if isinstance(item, Mapping) and str(item.get("node_id") or "") == node_id
    ]
    candidates.sort(key=lambda item: (item.get("evidence_status") != "observed", -float(item.get("confidence") or 0)))
    return candidates[:5]


def _compact_page_hint(page: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "hint_id": page.get("hint_id"),
        "node_id": page.get("node_id"),
        "page_id": page.get("page_id"),
        "page_name": page.get("page_name"),
        "actual_url": page.get("actual_url"),
        "title": page.get("title"),
        "dom_signature": page.get("dom_signature"),
        "screenshot_path": page.get("screenshot_path"),
        "mcp_evidence": dict(page.get("mcp_evidence", {}) or {}),
        "source": page.get("source"),
        "evidence_status": page.get("evidence_status"),
        "confidence": page.get("confidence"),
        "entry_signals": list(page.get("entry_signals", []) or [])[:5],
        "observed_field_count": len(page.get("observed_fields", []) or []),
        "observed_action_count": len(page.get("observed_actions", []) or []),
    }


def _compact_action(action: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "action_key": action.get("action_key"),
        "text": action.get("text"),
        "selector": action.get("selector"),
        "locators": [dict(item) for item in action.get("locators", []) or [] if isinstance(item, Mapping)][:3],
        "source": action.get("source"),
        "evidence_status": action.get("evidence_status"),
        "confidence": action.get("confidence"),
    }


def _compact_field_hint(field: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "field_id": field.get("field_id"),
        "field_key": field.get("field_key"),
        "name": field.get("name"),
        "priority": field.get("priority"),
        "rules": list(field.get("rules", []) or [])[:5],
        "locators": [dict(item) for item in field.get("locators", []) or [] if isinstance(item, Mapping)][:3],
        "source": field.get("source"),
        "evidence_status": field.get("evidence_status"),
        "confidence": field.get("confidence"),
    }


def knowledge_evidence_for_request(
    request: Mapping[str, Any],
    knowledge_hints: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Return non-authoritative knowledge evidence for a targeted probe request."""
    hints = dict(knowledge_hints or {})
    if not hints.get("available"):
        return None
    node_id = str(request.get("node_id") or "")
    if not node_id:
        return None
    page_hints = _page_evidence_for_node(hints, node_id)
    field_hints = _field_evidence_for_node(hints, node_id)
    if not page_hints and not field_hints:
        return None
    candidate_actions = [
        _compact_action(action)
        for page in page_hints
        for action in page.get("observed_actions", []) or []
        if isinstance(action, Mapping)
    ][:5]
    candidate_fields = [_compact_field_hint(item) for item in field_hints][:5]
    return {
        "source": "knowledge.agent3-hints",
        "node_id": node_id,
        "evidence_status": "observed" if any(item.get("evidence_status") == "observed" for item in page_hints) else "document-inferred",
        "page_hints": [_compact_page_hint(item) for item in page_hints],
        "field_hints": candidate_fields,
        "candidate_actions": candidate_actions,
        "usage_policy": "hint_only_requires_targeted_probe_verification",
    }


def enrich_targeted_probe_plan(
    targeted_probe_plan: Mapping[str, Any],
    knowledge_hints: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Attach knowledge evidence to probe requests without changing required counts."""
    requests: list[dict[str, Any]] = []
    evidence_count = 0
    for request in targeted_probe_plan.get("requests", []) or []:
        next_request = dict(request)
        evidence = knowledge_evidence_for_request(next_request, knowledge_hints)
        if evidence:
            next_request["knowledge_evidence"] = evidence
            next_request["knowledge_hint_status"] = "available"
            evidence_count += 1
        requests.append(next_request)
    summary = dict(targeted_probe_plan.get("summary", {}) or {})
    summary["knowledge_evidence_request_count"] = evidence_count
    result = dict(targeted_probe_plan)
    result["requests"] = requests
    result["summary"] = summary
    result["knowledge_assist"] = knowledge_assist_summary(knowledge_hints)
    return result

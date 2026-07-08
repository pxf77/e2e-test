"""Generate static Agent3 element-set assets from live exploration output."""
from __future__ import annotations

import json
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping


_DOMAIN_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("beneficiary", ("受益", "beneficiary")),
    ("insured", ("被保", "被保险", "insured", "_20")),
    ("applicant", ("投保", "applicant", "_10")),
    ("tax", ("税收", "纳税", "tax")),
)

_FIELD_TYPE_HINTS: tuple[tuple[str, tuple[str, ...], str, str], ...] = (
    ("mobile", ("moblie", "mobile", "phone", "tel", "手机号", "手机"), "string", "mobile_cn"),
    ("id_no", ("cardnumber", "idcard", "cert", "证件", "身份证"), "string", "id_card"),
    ("name", ("name", "姓名"), "string", "person_name"),
    ("birthdate", ("birth", "birthday", "出生", "生日"), "date", "birthdate"),
    ("gender", ("gender", "sex", "性别"), "enum", "default_first_option"),
    ("height", ("height", "身高"), "number", "height_cm"),
    ("weight", ("weight", "体重"), "number", "weight_kg"),
    ("email", ("email", "邮箱"), "string", "email"),
    ("address", ("address", "地址"), "string", "address_cn"),
    ("income", ("income", "收入"), "number", "income_wan"),
    ("captcha", ("captcha", "verify", "验证码"), "string", "captcha"),
)

_ACTION_HINTS: tuple[tuple[str, tuple[str, ...], str, bool], ...] = (
    ("action.buy_now", ("立即投保", "我要投保", "去投保"), "primary", True),
    ("action.submit", ("提交投保单", "提交核保", "提交"), "submit", True),
    ("action.pay", ("去支付", "立即支付", "确认支付", "付款"), "payment", True),
    ("action.agree_all", ("已阅读并同意", "确认无以上问题", "同意"), "agreement", False),
    ("action.next", ("下一步", "继续", "确认"), "forward", True),
)

_CHOICE_OPTION_RE = re.compile(r"^\s*[A-ZＡ-Ｚ]\s*[\.．、]")

_NODE_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("NODE-insure-form", ("投保人信息", "被保险人信息", "提交投保单", "/product/insure")),
    ("NODE-health-notice", ("健康告知", "问卷", "确认无以上问题")),
    ("NODE-suitability", ("适当性", "风险承受", "保险需求")),
    ("NODE-product-detail", ("产品详情", "保障责任", "立即投保", "/product/detail")),
    ("NODE-payment", ("支付", "付款", "银行卡", "签约")),
    ("NODE-policy-result", ("保单", "出单", "成功", "结果")),
)


def _slug(value: str, *, fallback: str) -> str:
    text = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", value).strip("-").lower()
    if not text:
        return fallback
    ascii_text = re.sub(r"[^0-9a-z-]+", "", text).strip("-")
    return ascii_text or fallback


def _node_slug(node_id: str | None, page_key: str | None, index: int) -> str:
    if node_id:
        return _slug(str(node_id).removeprefix("NODE-"), fallback=f"page-{index:03d}")
    if page_key:
        normalized = str(page_key).replace("apps-cps-product-", "")
        return _slug(normalized, fallback=f"page-{index:03d}")
    return f"page-{index:03d}"


def _first_text(*values: object) -> str:
    return " ".join(str(value or "") for value in values if value is not None)


def _infer_node_id(record: Mapping[str, Any]) -> str:
    matched = [str(item) for item in record.get("matched_node_ids", []) or [] if item]
    if matched:
        return matched[0]
    haystack = _first_text(
        record.get("actual_url"),
        record.get("actual_page_key"),
        record.get("title"),
        record.get("body_text_excerpt"),
    )
    for node_id, hints in _NODE_HINTS:
        if any(hint in haystack for hint in hints):
            return node_id
    return ""


def _field_text(field: Mapping[str, Any]) -> str:
    raw = field.get("raw", {}) or {}
    return _first_text(
        field.get("field_key"),
        field.get("name"),
        field.get("label"),
        raw.get("name"),
        raw.get("id"),
        raw.get("label"),
        raw.get("placeholder"),
        raw.get("type"),
    )


def _infer_field_domain(text: str) -> str:
    lowered = text.lower()
    for domain, hints in _DOMAIN_HINTS:
        if any(hint.lower() in lowered or hint in text for hint in hints):
            return domain
    return "form"


def _infer_field_type(text: str) -> tuple[str, str, str]:
    lowered = text.lower()
    for field_type, hints, value_type, mock_strategy in _FIELD_TYPE_HINTS:
        if any(hint.lower() in lowered or hint in text for hint in hints):
            return field_type, value_type, mock_strategy
    fallback = _slug(text, fallback="field").replace("-", "_")
    return fallback, "string", "mock"


def _field_aliases(field: Mapping[str, Any]) -> list[str]:
    raw = field.get("raw", {}) or {}
    aliases = [
        str(value).strip()
        for value in (
            field.get("field_key"),
            field.get("name"),
            field.get("label"),
            raw.get("name"),
            raw.get("id"),
            raw.get("label"),
            raw.get("placeholder"),
        )
        if str(value or "").strip()
    ]
    return list(dict.fromkeys(aliases))


def _infer_field_descriptor(field: Mapping[str, Any], seen: set[str]) -> dict[str, Any]:
    text = _field_text(field)
    domain = _infer_field_domain(text)
    field_type, value_type, mock_strategy = _infer_field_type(text)
    base_key = f"{domain}.{field_type}"
    field_key = base_key
    suffix = 2
    while field_key in seen:
        field_key = f"{base_key}_{suffix}"
        suffix += 1
    seen.add(field_key)
    raw = field.get("raw", {}) or {}
    return {
        "field_key": field_key,
        "domain": domain,
        "aliases": _field_aliases(field),
        "value_type": value_type,
        "mock_strategy": mock_strategy,
        "required": bool(field.get("required") or raw.get("required")),
        "selector": field.get("selector") or raw.get("selector"),
        "raw": raw or dict(field),
    }


def _is_hidden_field(field: Mapping[str, Any]) -> bool:
    raw = field.get("raw", {}) or {}
    field_type = str(raw.get("type") or field.get("type") or "").lower()
    if field_type in {"hidden", "button", "submit", "reset", "radio", "checkbox"}:
        return True
    if raw.get("disabled") or field.get("disabled"):
        return True
    selector = str(field.get("selector") or raw.get("selector") or "")
    return selector.startswith("#__")


def _is_business_action(descriptor: Mapping[str, Any]) -> bool:
    return str(descriptor.get("action_type") or "") != "custom"


def _is_document_action_text(text: str) -> bool:
    return text.startswith("\u300a") and any(token in text for token in ("声明", "确认书", "提示书", "责任免除", "条款"))


def _infer_action_descriptor(action: Mapping[str, Any]) -> dict[str, Any]:
    text = _first_text(action.get("text"), action.get("selector"), action.get("href"))
    visible_text = str(action.get("text") or "").strip()
    if _is_document_action_text(visible_text):
        return {
            "action_key": "action.document_link",
            "aliases": [visible_text],
            "action_type": "custom",
            "required": False,
            "selector": action.get("selector"),
            "text": action.get("text"),
            "tag": action.get("tag"),
            "href": action.get("href"),
        }
    if _CHOICE_OPTION_RE.search(visible_text):
        return {
            "action_key": "action.answer_option",
            "aliases": [visible_text],
            "action_type": "choice",
            "required": False,
            "selector": action.get("selector"),
            "text": action.get("text"),
            "tag": action.get("tag"),
            "href": action.get("href"),
        }
    for action_key, hints, action_type, required in _ACTION_HINTS:
        if any(hint in text for hint in hints):
            return {
                "action_key": action_key,
                "aliases": [str(action.get("text") or "").strip()] if action.get("text") else [],
                "action_type": action_type,
                "required": required,
                "selector": action.get("selector"),
                "text": action.get("text"),
                "tag": action.get("tag"),
                "href": action.get("href"),
            }
    slug = _slug(str(action.get("text") or action.get("selector") or "action"), fallback="custom")
    return {
        "action_key": f"action.{slug.replace('-', '_')}",
        "aliases": [str(action.get("text") or "").strip()] if action.get("text") else [],
        "action_type": "custom",
        "required": False,
        "selector": action.get("selector"),
        "text": action.get("text"),
        "tag": action.get("tag"),
        "href": action.get("href"),
    }


def _records_from_registry(page_registry: Mapping[str, Any]) -> list[dict[str, Any]]:
    records = [dict(item) for item in page_registry.get("page_content_records", []) or []]
    if records:
        return records
    fallback_records: list[dict[str, Any]] = []
    for index, page in enumerate(page_registry.get("pages", []) or [], start=1):
        fallback_records.append(
            {
                "page_content_record_id": f"PCR-PAGE-{index:03d}",
                "actual_url": page.get("url"),
                "actual_page_key": page.get("page_key"),
                "title": page.get("title"),
                "dom_signature": page.get("dom_signature"),
                "body_text_excerpt": page.get("body_text_excerpt"),
                "matched_node_ids": page.get("matched_node_ids", []),
                "source_path_ids": [page.get("path_id")] if page.get("path_id") else [],
                "field_map": [
                    {
                        "field_key": field.get("name") or field.get("id") or field.get("placeholder") or f"field-{field.get('index')}",
                        "selector": field.get("selector"),
                        "required": field.get("required"),
                        "raw": field,
                    }
                    for field in page.get("fields", []) or []
                ],
                "selector_map": {"actions": list(page.get("actions", []) or [])},
            }
        )
    return fallback_records


def _semantic_field_entry(descriptor: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "field_key": descriptor.get("field_key"),
        "domain": descriptor.get("domain"),
        "aliases": list(descriptor.get("aliases", []) or []),
        "value_type": descriptor.get("value_type"),
        "required_default": bool(descriptor.get("required")),
        "mock_strategy": descriptor.get("mock_strategy"),
        "source": "agent3.live-exploration",
    }


def _semantic_action_entry(descriptor: Mapping[str, Any]) -> dict[str, Any]:
    aliases = list(descriptor.get("aliases", []) or [])
    return {
        "action_key": descriptor.get("action_key"),
        "aliases": aliases,
        "action_type": descriptor.get("action_type"),
        "source": "agent3.live-exploration",
    }


def _page_model_field(descriptor: Mapping[str, Any]) -> dict[str, Any]:
    locators = []
    if descriptor.get("selector"):
        locators.append({"by": "selector", "value": descriptor.get("selector")})
    for alias in descriptor.get("aliases", []) or []:
        if alias and alias != descriptor.get("selector") and re.search(r"[\u4e00-\u9fff]", str(alias)):
            locators.append({"by": "label_text", "value": alias})
            break
    return {
        "field_key": descriptor.get("field_key"),
        "locators": locators,
        "required": bool(descriptor.get("required")),
        "value_type": descriptor.get("value_type"),
        "mock_strategy": descriptor.get("mock_strategy"),
        "source": "agent3.live-exploration",
        "raw": descriptor.get("raw", {}),
    }


def _page_model_action(descriptor: Mapping[str, Any]) -> dict[str, Any]:
    locators = []
    if descriptor.get("selector"):
        locators.append({"by": "selector", "value": descriptor.get("selector")})
    if descriptor.get("text"):
        locators.append({"by": "text", "value": descriptor.get("text")})
    return {
        "action_key": descriptor.get("action_key"),
        "locators": locators,
        "required": bool(descriptor.get("required")),
        "text": descriptor.get("text"),
        "action_type": descriptor.get("action_type"),
        "source": "agent3.live-exploration",
        "raw": {
            "selector": descriptor.get("selector"),
            "tag": descriptor.get("tag"),
            "href": descriptor.get("href"),
        },
    }


def _text_hints(record: Mapping[str, Any]) -> list[str]:
    text = str(record.get("body_text_excerpt") or "")
    candidates = [
        "产品详情",
        "立即投保",
        "投保人信息",
        "被保险人信息",
        "健康告知",
        "适当性",
        "提交投保单",
        "支付",
        "保单",
        "成功",
    ]
    return [item for item in candidates if item in text][:8]


def build_element_set_bundle(page_registry: Mapping[str, Any]) -> dict[str, Any]:
    """Build product automation assets from an Agent3 page registry."""
    product_id = str(page_registry.get("product_id") or "product")
    entry_url = page_registry.get("entry_url")
    platform = page_registry.get("platform") or ("h5" if "/m/" in str(entry_url or "") else "pc")
    records = _records_from_registry(page_registry)
    semantic_fields: dict[str, dict[str, Any]] = {}
    semantic_actions: dict[str, dict[str, Any]] = {}
    page_models: list[dict[str, Any]] = []
    probe_records: list[dict[str, Any]] = []

    for index, record in enumerate(records, start=1):
        node_id = _infer_node_id(record)
        slug = _node_slug(node_id, str(record.get("actual_page_key") or ""), index)
        page_model_id = f"PM-{slug}"
        seen_fields: set[str] = set()
        field_descriptors = [
            _infer_field_descriptor(field, seen_fields)
            for field in record.get("field_map", []) or []
            if field.get("selector") or (field.get("raw", {}) or {}).get("selector")
            if not _is_hidden_field(field)
        ]
        action_descriptors = [
            _infer_action_descriptor(action)
            for action in (record.get("selector_map", {}) or {}).get("actions", []) or []
            if action.get("selector") or action.get("text")
        ]

        for descriptor in field_descriptors:
            field_key = str(descriptor.get("field_key") or "")
            if field_key and field_key not in semantic_fields:
                semantic_fields[field_key] = _semantic_field_entry(descriptor)
        for descriptor in action_descriptors:
            action_key = str(descriptor.get("action_key") or "")
            if action_key and _is_business_action(descriptor) and action_key not in semantic_actions:
                semantic_actions[action_key] = _semantic_action_entry(descriptor)

        page_model = {
            "version": "1.0",
            "page_model_id": page_model_id,
            "node_id": node_id,
            "actual_page_key": record.get("actual_page_key"),
            "actual_url_sample": record.get("actual_url"),
            "title": record.get("title"),
            "source": "agent3.live-exploration",
            "match_contract": {
                "text_hints": _text_hints(record),
                "entry_signals": _text_hints(record)[:3],
                "exit_signals": [
                    str(item.get("text"))
                    for item in action_descriptors
                    if item.get("text")
                ][:5],
                "min_confidence": 0.75,
            },
            "fields": [_page_model_field(item) for item in field_descriptors],
            "actions": [_page_model_action(item) for item in action_descriptors if _is_business_action(item)],
            "probe_policy": {
                "allow_probe": True,
                "probe_fields_when_missing": True,
                "probe_actions_when_missing": True,
            },
            "source_record": {
                "page_content_record_id": record.get("page_content_record_id"),
                "dom_signature": record.get("dom_signature"),
                "source_path_ids": list(record.get("source_path_ids", []) or []),
            },
        }
        page_models.append({"filename": f"{slug}.json", "content": page_model})
        probe_records.append(
            {
                "page_model_id": page_model_id,
                "actual_url": record.get("actual_url"),
                "dom_signature": record.get("dom_signature"),
                "page_match_confidence": 1.0 if node_id else 0.5,
                "discovered_fields": [
                    {
                        "field_key": item.get("field_key"),
                        "selector": item.get("selector"),
                        "confidence": 0.9,
                        "source": "live-exploration",
                    }
                    for item in field_descriptors
                    if item.get("selector")
                ],
                "discovered_actions": [
                    {
                        "action_key": item.get("action_key"),
                        "selector": item.get("selector"),
                        "text": item.get("text"),
                        "confidence": 0.9,
                        "source": "live-exploration",
                    }
                    for item in action_descriptors
                    if _is_business_action(item)
                    if item.get("selector")
                ],
                "captured_at": datetime.now(UTC).isoformat(),
            }
        )

    product_config = {
        "product_id": product_id,
        "platform": platform,
        "entry_url": entry_url,
        "agent3_mode": "static-first",
        "default_flow_id": "main-purchase",
        "probe_policy_id": "default-probe-policy",
        "supports": {
            "live_probe": True,
            "full_discovery": False,
            "external_payment_handoff": True,
        },
        "artifact_policy": {
            "freeze_probe_result": False,
            "write_back_cache": True,
            "require_manual_review_before_adapter": True,
        },
        "generated_by": "agent3.element-set-generation",
    }
    field_semantic = {
        "version": "1.0",
        "generated_by": "agent3.element-set-generation",
        "fields": list(semantic_fields.values()),
    }
    action_semantic = {
        "version": "1.0",
        "generated_by": "agent3.element-set-generation",
        "actions": list(semantic_actions.values()),
    }
    probe_cache = {
        "version": "1.0",
        "generated_by": "agent3.element-set-generation",
        "records": probe_records,
    }
    index = {
        "version": "1.0",
        "product_id": product_id,
        "entry_url": entry_url,
        "generated_by": "agent3.element-set-generation",
        "source_registry_generated_by": page_registry.get("generated_by"),
        "page_model_count": len(page_models),
        "field_count": len(semantic_fields),
        "action_count": len(semantic_actions),
        "page_models": [
            {
                "page_model_id": item["content"].get("page_model_id"),
                "filename": item["filename"],
                "node_id": item["content"].get("node_id"),
                "actual_url_sample": item["content"].get("actual_url_sample"),
                "field_count": len(item["content"].get("fields", []) or []),
                "action_count": len(item["content"].get("actions", []) or []),
            }
            for item in page_models
        ],
    }
    return {
        "product_config": product_config,
        "field_semantic": field_semantic,
        "action_semantic": action_semantic,
        "page_models": page_models,
        "probe_cache": probe_cache,
        "index": index,
    }


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _single_file_element_set(bundle: Mapping[str, Any]) -> dict[str, Any]:
    page_models = {
        Path(str(item["filename"])).stem: item["content"]
        for item in bundle.get("page_models", []) or []
    }
    by_node: dict[str, str] = {}
    by_field_key: dict[str, list[dict[str, str]]] = {}
    by_action_key: dict[str, list[dict[str, str]]] = {}
    for slug, model in page_models.items():
        model_ref = f"#/page_models/{slug}"
        node_id = str(model.get("node_id") or "")
        if node_id:
            by_node[node_id] = model_ref
        for field in model.get("fields", []) or []:
            field_key = str(field.get("field_key") or "")
            if field_key:
                by_field_key.setdefault(field_key, []).append(
                    {"page_model_id": str(model.get("page_model_id") or ""), "ref": model_ref}
                )
        for action in model.get("actions", []) or []:
            action_key = str(action.get("action_key") or "")
            if action_key:
                by_action_key.setdefault(action_key, []).append(
                    {"page_model_id": str(model.get("page_model_id") or ""), "ref": model_ref}
                )

    index = dict(bundle.get("index", {}) or {})
    return {
        "version": "1.0",
        "generated_at": datetime.now(UTC).isoformat(),
        "owner": "Agent3 / explore_agent",
        "purpose": "Single-file product element set generated from Agent3 live exploration.",
        "product_config": bundle.get("product_config", {}),
        "summary": {
            "page_model_count": index.get("page_model_count", len(page_models)),
            "field_semantic_count": len((bundle.get("field_semantic", {}) or {}).get("fields", []) or []),
            "action_semantic_count": len((bundle.get("action_semantic", {}) or {}).get("actions", []) or []),
            "probe_record_count": len((bundle.get("probe_cache", {}) or {}).get("records", []) or []),
        },
        "index": index,
        "page_models": page_models,
        "semantic_library": {
            "fields": (bundle.get("field_semantic", {}) or {}).get("fields", []),
            "actions": (bundle.get("action_semantic", {}) or {}).get("actions", []),
        },
        "probe_cache": bundle.get("probe_cache", {}),
        "quick_lookup": {
            "by_node": dict(sorted(by_node.items())),
            "by_field_key": dict(sorted(by_field_key.items())),
            "by_action_key": dict(sorted(by_action_key.items())),
        },
    }


def _cleanup_split_element_set_outputs(automation_dir: Path) -> None:
    for target in (
        automation_dir / "product.config.json",
        automation_dir / "element-set.index.json",
        automation_dir / "semantic-library",
        automation_dir / "page-models",
        automation_dir / "probe",
    ):
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()


def materialise_element_set_from_page_registry(
    *,
    root_dir: Path,
    product_id: str,
    page_registry: Mapping[str, Any],
    product_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Write generated element-set assets as one JSON file under the product artifact dir."""
    bundle = build_element_set_bundle({**dict(page_registry), "product_id": product_id})
    if not bundle["page_models"]:
        return {
            **bundle["index"],
            "generated_files": [],
        }
    from e2e_agent.artifacts.paths import product_artifact_dir

    automation_dir = product_artifact_dir(root_dir, product_id, product_dir=product_dir) / "automation"
    _cleanup_split_element_set_outputs(automation_dir)
    element_set_path = automation_dir / "element-set.json"
    _write_json(element_set_path, _single_file_element_set(bundle))

    return {
        **bundle["index"],
        "generated_files": [str(element_set_path)],
    }

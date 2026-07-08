"""Assertion template catalog loading and matching."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import yaml


_DEFAULT_KEYWORDS: dict[str, dict[str, tuple[str, ...]]] = {
    "price_premium": {
        "route": ("product", "premium", "quote", "plan"),
        "assertion": ("保费", "价格", "金额", "premium", "price"),
    },
    "underwriting_result": {
        "route": ("underwriting", "result", "auth"),
        "assertion": ("核保", "承保", "拒保", "除外", "加费", "underwriting"),
    },
    "order_status": {
        "route": ("payment", "order", "pay", "policy"),
        "assertion": ("支付", "订单", "出单", "保单", "落单", "order", "payment"),
    },
}

_DEFAULT_PRIORITY = {
    "order_status": 30,
    "underwriting_result": 20,
    "price_premium": 10,
}


def _default_catalog() -> dict[str, Any]:
    return {
        "version": "builtin",
        "source": "builtin",
        "templates": {
            key: {
                "name": key,
                "required": True,
                "match": {
                    "route_keywords": list(value["route"]),
                    "assertion_keywords": list(value["assertion"]),
                },
            }
            for key, value in _DEFAULT_KEYWORDS.items()
        },
    }


def _resolve_catalog_path(
    path: str | Path | None,
    *,
    root_dir: Path | None = None,
) -> Path | None:
    if path:
        candidate = Path(str(path))
        if not candidate.is_absolute() and root_dir is not None:
            candidate = root_dir / candidate
        return candidate
    if root_dir is None:
        root_dir = Path.cwd()
    candidate = root_dir / "config" / "assertion-templates.yaml"
    return candidate if candidate.exists() else None


def load_assertion_template_catalog(
    path: str | Path | None = None,
    *,
    root_dir: Path | None = None,
) -> dict[str, Any]:
    """Load assertion templates from YAML with a conservative built-in fallback."""
    resolved = _resolve_catalog_path(path, root_dir=root_dir)
    if resolved is None or not resolved.exists():
        return _default_catalog()

    payload = yaml.safe_load(resolved.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        payload = {}
    templates = payload.get("templates")
    if not isinstance(templates, dict) or not templates:
        return _default_catalog()
    return {
        "version": str(payload.get("version") or "unknown"),
        "source": str(resolved),
        "templates": {str(key): value for key, value in templates.items() if isinstance(value, dict)},
    }


def _strings(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        texts: list[str] = []
        for key, item in value.items():
            texts.append(str(key))
            texts.extend(_strings(item))
        return texts
    if isinstance(value, list | tuple | set):
        texts: list[str] = []
        for item in value:
            texts.extend(_strings(item))
        return texts
    return [str(value)]


def _path_conditions(path_item: Mapping[str, Any]) -> dict[str, str]:
    conditions = path_item.get("conditions", {})
    if not isinstance(conditions, Mapping):
        return {}
    return {str(key): str(value) for key, value in conditions.items()}


def _keyword_list(template_type: str, template: Mapping[str, Any], key: str) -> list[str]:
    match = template.get("match", {})
    if isinstance(match, Mapping) and key in match:
        values = match.get(key, [])
        if isinstance(values, str):
            return [values]
        if isinstance(values, list):
            configured = [str(item) for item in values if str(item).strip()]
            if configured:
                return configured
    default_key = "route" if key == "route_keywords" else "assertion"
    return list(_DEFAULT_KEYWORDS.get(template_type, {}).get(default_key, ()))


def _contains_keyword(text: str, keyword: str) -> bool:
    return keyword.lower() in text.lower()


def _score_template(
    template_type: str,
    template: Mapping[str, Any],
    *,
    route_text: str,
    assertion_text: str,
    conditions: Mapping[str, str],
) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    for keyword in _keyword_list(template_type, template, "route_keywords"):
        if _contains_keyword(route_text, keyword):
            score += 3
            reasons.append(f"route keyword '{keyword}'")
    for keyword in _keyword_list(template_type, template, "assertion_keywords"):
        if _contains_keyword(assertion_text, keyword):
            score += 5
            reasons.append(f"assertion keyword '{keyword}'")

    match = template.get("match", {})
    if isinstance(match, Mapping):
        condition_keys = [str(item) for item in match.get("condition_keys", []) or []]
        condition_values = [str(item) for item in match.get("condition_values", []) or []]
        for key in condition_keys:
            if key in conditions:
                score += 2
                reasons.append(f"condition key '{key}'")
        condition_text = " ".join(conditions.values())
        for value in condition_values:
            if _contains_keyword(condition_text, value):
                score += 2
                reasons.append(f"condition value '{value}'")
    return score, reasons


def match_assertion_template(
    path_item: Mapping[str, Any],
    catalog: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Choose the strongest assertion template for a regression path."""
    catalog = catalog or _default_catalog()
    templates = catalog.get("templates", {})
    if not isinstance(templates, Mapping) or not templates:
        templates = _default_catalog()["templates"]

    route_text = " ".join(
        _strings(path_item.get("nodes"))
        + _strings(path_item.get("path_id"))
        + _strings(path_item.get("page_keys"))
        + _strings(path_item.get("target_node"))
    )
    assertion_text = " ".join(
        _strings(path_item.get("assertions"))
        + _strings(path_item.get("rules"))
        + _strings(path_item.get("business_goal"))
        + _strings(path_item.get("title"))
    )
    conditions = _path_conditions(path_item)

    best: tuple[int, int, str, list[str]] | None = None
    for template_type, template in templates.items():
        if template_type == "custom" or not isinstance(template, Mapping):
            continue
        score, reasons = _score_template(
            str(template_type),
            template,
            route_text=route_text,
            assertion_text=assertion_text,
            conditions=conditions,
        )
        priority = _DEFAULT_PRIORITY.get(str(template_type), 0)
        candidate = (score, priority, str(template_type), reasons)
        if score > 0 and (best is None or candidate[:2] > best[:2]):
            best = candidate

    variables: dict[str, Any] = {
        "path_id": str(path_item.get("path_id") or ""),
        **conditions,
    }
    source = str(catalog.get("source") or "builtin")
    if best is None:
        return {
            "template_type": "custom",
            "template_source": source,
            "match_reason": "no configured assertion template matched this path",
            "variables": variables,
            "assertion_strength": "weak",
            "weak": True,
            "justification": "No price, underwriting, or order-state template rule matched; keep as custom for human review.",
            "missing_template_reason": "no_template_match",
        }

    _, _, template_type, reasons = best
    return {
        "template_type": template_type,
        "template_source": source,
        "match_reason": "matched " + ", ".join(reasons),
        "variables": variables,
        "assertion_strength": "strong",
        "weak": False,
        "justification": None,
        "missing_template_reason": None,
    }


def summarize_assertion_template_coverage(
    assertion_results: list[Mapping[str, Any]],
) -> dict[str, Any]:
    total = len(assertion_results)
    missing = [
        item
        for item in assertion_results
        if item.get("missing_template_reason") or item.get("template_type") == "custom"
    ]
    weak = [
        item
        for item in assertion_results
        if item.get("weak") or item.get("assertion_strength") == "weak"
    ]
    used: dict[str, int] = {}
    for item in assertion_results:
        template_type = str(item.get("template_type") or "unknown")
        used[template_type] = used.get(template_type, 0) + 1
    matched_count = total - len(missing)
    return {
        "total_assertion_count": total,
        "template_matched_count": matched_count,
        "template_coverage_rate": (matched_count / total) if total else 0,
        "missing_template_count": len(missing),
        "weak_assertion_count": len(weak),
        "custom_assertion_count": used.get("custom", 0),
        "templates_used": used,
        "missing_templates": [
            {
                "assertion_id": item.get("assertion_id"),
                "case_id": item.get("case_id"),
                "path_id": (item.get("expected_value") or {}).get("path_id")
                if isinstance(item.get("expected_value"), Mapping)
                else None,
                "reason": item.get("missing_template_reason") or "custom_template",
                "justification": item.get("justification"),
            }
            for item in missing
        ],
    }

"""Static element-set access for Agent3."""
from __future__ import annotations

import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any


STATIC_ELEMENT_SET_PATH = "builtin:agent3-generic-static-element-set"


def _action(action_key: str, text: str, selector: str | None = None, *, required: bool = True) -> dict[str, Any]:
    locators = [{"by": "selector", "value": selector}] if selector else [{"by": "text", "value": text}]
    return {
        "action_key": action_key,
        "text": text,
        "locators": locators,
        "required": required,
    }


def _field(
    field_key: str,
    label: str,
    selector: str,
    *,
    value_type: str = "string",
    control_type: str | None = None,
    required: bool = True,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "field_key": field_key,
        "label": label,
        "value_type": value_type,
        "required": required,
        "mock_strategy": "mock",
        "locators": [{"by": "selector", "value": selector}, {"by": "label_text", "value": label}],
    }
    if control_type:
        item["control_type"] = control_type
    return item


def _model(
    slug: str,
    node_id: str,
    *,
    entry_signals: list[str],
    url_patterns: list[str],
    actions: list[dict[str, Any]] | None = None,
    fields: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "page_model_id": f"PM-{slug}",
        "node_id": node_id,
        "page_key_pattern": slug,
        "match_contract": {
            "entry_signals": entry_signals,
            "url_patterns": url_patterns,
        },
        "fields": fields or [],
        "actions": actions or [],
    }


def _builtin_static_element_set() -> dict[str, Any]:
    page_models = {
        "product-detail": _model(
            "product-detail",
            "NODE-product-detail",
            entry_signals=["Product Detail"],
            url_patterns=["/product/detail"],
            actions=[
                _action("action.buy_now", "Start Application", "#submit-by"),
                _action("action.agree_all", "Agree and Continue", required=False),
            ],
        ),
        "premium-calculation": _model(
            "premium-calculation",
            "NODE-premium-calculation",
            entry_signals=["Premium Calculation"],
            url_patterns=["/premium"],
            actions=[_action("action.agree_all", "Agree and Continue")],
        ),
        "suitability": _model(
            "suitability",
            "NODE-suitability",
            entry_signals=["Suitability Questionnaire"],
            url_patterns=["/questionnaire"],
            actions=[
                _action("action.submit", "Submit", "button.js-adapt-question-btn"),
                _action("action.buy_now", "Continue Application"),
            ],
        ),
        "health-notice": _model(
            "health-notice",
            "NODE-health-notice",
            entry_signals=["Health Notice"],
            url_patterns=["/health-notice"],
            actions=[_action("action.next", "Continue")],
        ),
        "insure-form": _model(
            "insure-form",
            "NODE-insure-form",
            entry_signals=["Application Form"],
            url_patterns=["/product/insure"],
            fields=[
                _field("insure_form.applicantname", "Applicant Name", 'input[name^="applicantName"]'),
                _field("insure_form.applicantidno", "Applicant ID Number", 'input[name^="applicantIdNo"]'),
                _field("insure_form.applicantphone", "Applicant Phone", 'input[name^="applicantPhone"]'),
                _field("insure_form.insuredname", "Insured Name", 'input[name^="insuredName"]'),
                _field("insure_form.insuredidno", "Insured ID Number", 'input[name^="insuredIdNo"]'),
                _field("insure_form.insuredphone", "Insured Phone", 'input[name^="insuredPhone"]'),
            ],
            actions=[
                _action("action.agree_all", "Agree"),
                _action("action.submit", "Submit Order", "#submit-order"),
            ],
        ),
        "underwriting-callback": _model(
            "underwriting-callback",
            "NODE-underwriting-callback",
            entry_signals=["Underwriting Callback"],
            url_patterns=["/underwriting/callback"],
        ),
        "risk-control-check": _model(
            "risk-control-check",
            "NODE-risk-control",
            entry_signals=["Risk Control"],
            url_patterns=["/risk-control"],
            actions=[_action("action.next", "Continue", "#risk-next")],
        ),
        "payment": _model(
            "payment",
            "NODE-payment",
            entry_signals=["Payment"],
            url_patterns=["/pay"],
            actions=[_action("action.pay", "Pay Now", "#pay-now")],
        ),
        "payment-signing": _model(
            "payment-signing",
            "NODE-payment-signing",
            entry_signals=["Payment Signing"],
            url_patterns=["/pay/signing"],
            actions=[_action("action.next", "Confirm", "#signing-confirm")],
        ),
        "policy-result": _model(
            "policy-result",
            "NODE-policy-result",
            entry_signals=["Policy Result"],
            url_patterns=["/policy/result"],
        ),
    }
    by_node = {model["node_id"]: f"#/page_models/{slug}" for slug, model in page_models.items()}
    return {
        "layout": {"runtime_dependency": "builtin generic element set"},
        "summary": {
            "source": "agent3-builtin-generic",
            "embedded_legacy_ts_count": 0,
            "page_model_count": len(page_models),
        },
        "quick_lookup": {"by_node": by_node},
        "page_models": page_models,
        "field_semantics": {},
        "action_semantics": {},
        "probe_cache": {},
    }


@lru_cache(maxsize=1)
def load_static_element_set() -> dict[str, Any]:
    """Load the embedded generic Agent3 static element set."""
    return _builtin_static_element_set()


def _merge_element_sets(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if key in {"page_models", "field_semantics", "action_semantics", "probe_cache"} and isinstance(value, dict):
            merged.setdefault(key, {})
            merged[key].update(deepcopy(value))
            continue
        if key == "quick_lookup" and isinstance(value, dict):
            merged.setdefault("quick_lookup", {})
            for lookup_key, lookup_value in value.items():
                if isinstance(lookup_value, dict):
                    merged["quick_lookup"].setdefault(lookup_key, {})
                    merged["quick_lookup"][lookup_key].update(deepcopy(lookup_value))
                else:
                    merged["quick_lookup"][lookup_key] = deepcopy(lookup_value)
            continue
        merged[key] = deepcopy(value)
    return merged


def load_element_set_for_product(
    root_dir: str | Path,
    product_id: str,
    *,
    product_source_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Load the product element set first, then fall back to the built-in asset."""
    root_path = Path(root_dir)
    source_dir = Path(product_source_dir) if product_source_dir else root_path / "products" / product_id
    if not source_dir.is_absolute():
        source_dir = root_path / source_dir
    product_path = source_dir / "automation" / "element-set.json"
    if product_path.exists():
        try:
            product_element_set = json.loads(product_path.read_text(encoding="utf-8-sig"))
            return {
                "element_set": _merge_element_sets(load_static_element_set(), product_element_set),
                "source": "product-automation",
                "path": str(product_path),
                "warning": None,
            }
        except json.JSONDecodeError as exc:
            return {
                "element_set": load_static_element_set(),
                "source": "agent3-builtin",
                "path": STATIC_ELEMENT_SET_PATH,
                "warning": f"Product element-set parse failed: {product_path}: {exc}",
            }
    return {
        "element_set": load_static_element_set(),
        "source": "agent3-builtin",
        "path": STATIC_ELEMENT_SET_PATH,
        "warning": None,
    }

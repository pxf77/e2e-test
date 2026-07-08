"""Compile Agent3 static element-set assets into the Agent4-compatible contract."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Mapping

from e2e_agent.core.knowledge_agent3_hints import (
    enrich_targeted_probe_plan,
    knowledge_assist_summary,
)


_IGNORED_PATH_NODES = {"NODE-start", "NODE-end", "NODE-branch"}
_NODE_MODEL_ALIASES = {
    "NODE-underwriting": "NODE-underwriting-callback",
}
_INSURE_FORM_CORE_REQUIRED_FIELD_KEYS = {
    "agreement.confirm",
    "applicant.address",
    "applicant.annual_income",
    "applicant.card_valid_end",
    "applicant.card_valid_start",
    "applicant.email",
    "applicant.height",
    "applicant.id_no",
    "applicant.name",
    "applicant.occupation",
    "applicant.phone",
    "applicant.region",
    "applicant.weight",
    "beneficiary.ratio",
    "beneficiary.relation",
    "beneficiary.type",
    "insure_form.applicantemail",
    "insure_form.applicantidno",
    "insure_form.applicantname",
    "insure_form.applicantphone",
    "insure_form.cardtype",
    "insure_form.cardvalidend",
    "insure_form.cardvalidstart",
    "insure_form.cardvalidtype",
    "insure_form.insuredidno",
    "insure_form.insuredidtype",
    "insure_form.insuredname",
    "insure_form.insuredphone",
    "insured.address",
    "insured.birthdate",
    "insured.card_valid_end",
    "insured.card_valid_start",
    "insured.gender",
    "insured.height",
    "insured.id_no",
    "insured.name",
    "insured.occupation",
    "insured.phone",
    "insured.region",
    "insured.weight",
    "policy.start_date",
}
_INSURE_FORM_REQUIRED_EXCLUDED_TEXT = (
    "sameaddress",
    "expectcurrentpagevalidation",
    "expectedvalidationtext",
    "是否同",
)
_INSURE_FORM_SEMANTIC_SELECTOR_ALIASES = {
    "applicant.email": ("applicantEmail", "email", "电子邮箱"),
}
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


def _selector_locator(selector: str) -> dict[str, str]:
    return {"by": "selector", "value": selector}


def _label_locator(label: str) -> dict[str, str]:
    return {"by": "label_text", "value": label}


def _supplemental_field(
    field_key: str,
    *,
    selector: str,
    label: str,
    value_type: str = "string",
    control_type: str | None = None,
) -> dict[str, Any]:
    field: dict[str, Any] = {
        "field_key": field_key,
        "label": label,
        "value_type": value_type,
        "required": True,
        "mock_strategy": "mock",
        "locators": [_selector_locator(selector), _label_locator(label)],
    }
    if control_type:
        field["control_type"] = control_type
    return field


def _insure_form_supplemental_fields() -> list[dict[str, Any]]:
    """Contract fields observed on the current PC insure form framework."""
    return [
        _supplemental_field(
            "policy.start_date",
            selector='input[name^="insuranceDate"]',
            label="起保日期",
            value_type="date",
            control_type="date_picker",
        ),
        _supplemental_field("applicant.name", selector='input[name^="cName_10"]', label="投保人姓名"),
        _supplemental_field("applicant.id_no", selector='input[name^="cardNumber_10"]', label="投保人证件号码"),
        _supplemental_field(
            "applicant.card_valid_start",
            selector='input[name^="cardPeriod_10"]',
            label="投保人证件有效期开始",
            value_type="date",
            control_type="date_picker",
        ),
        _supplemental_field(
            "applicant.card_valid_end",
            selector='input[name^="cardPeriodEnd_10"]',
            label="投保人证件有效期结束",
            value_type="date",
            control_type="date_picker",
        ),
        _supplemental_field(
            "applicant.region",
            selector='div[name^="provCityText_10"].province, div[name^="provCityText_10"]',
            label="投保人居住省市",
            control_type="region_picker",
        ),
        _supplemental_field("applicant.address", selector='input[name^="contactAddress_10"]', label="投保人联系地址"),
        _supplemental_field(
            "applicant.occupation",
            selector='div[name^="jobText_10"].job1, div[name^="jobText_10"]',
            label="投保人职业",
            control_type="occupation_picker",
        ),
        _supplemental_field(
            "applicant.annual_income",
            selector='input[name^="yearlyIncome_10"]',
            label="投保人年收入",
            value_type="number",
        ),
        _supplemental_field("applicant.height", selector='input[name^="height_10"]', label="投保人身高", value_type="number"),
        _supplemental_field("applicant.weight", selector='input[name^="weight_10"]', label="投保人体重", value_type="number"),
        _supplemental_field(
            "applicant.phone",
            selector='input[name^="moblie_10"], input[name^="mobile_10"]',
            label="投保人手机号码",
        ),
        _supplemental_field("applicant.email", selector='input[name^="email_10"]', label="投保人电子邮箱"),
        _supplemental_field("insured.name", selector='input[name^="cName_20"]', label="被保险人姓名"),
        _supplemental_field("insured.id_no", selector='input[name^="cardNumber_20"]', label="被保险人证件号码"),
        _supplemental_field(
            "insured.card_valid_start",
            selector='input[name^="cardPeriod_20"]',
            label="被保险人证件有效期开始",
            value_type="date",
            control_type="date_picker",
        ),
        _supplemental_field(
            "insured.card_valid_end",
            selector='input[name^="cardPeriodEnd_20"]',
            label="被保险人证件有效期结束",
            value_type="date",
            control_type="date_picker",
        ),
        _supplemental_field(
            "insured.region",
            selector='div[name^="provCityText_20"].province, div[name^="provCityText_20"]',
            label="被保险人居住省市",
            control_type="region_picker",
        ),
        _supplemental_field("insured.address", selector='input[name^="contactAddress_20"]', label="被保险人联系地址"),
        _supplemental_field(
            "insured.occupation",
            selector='div[name^="jobText_20"].job1, div[name^="jobText_20"]',
            label="被保险人职业",
            control_type="occupation_picker",
        ),
        _supplemental_field("insured.height", selector='input[name^="height_20"]', label="被保险人身高", value_type="number"),
        _supplemental_field("insured.weight", selector='input[name^="weight_20"]', label="被保险人体重", value_type="number"),
        _supplemental_field(
            "insured.phone",
            selector='input[name^="moblie_20"], input[name^="mobile_20"]',
            label="被保险人手机号码",
        ),
        _supplemental_field(
            "agreement.confirm",
            selector='input[type="checkbox"]',
            label="本人充分阅读、理解并同意",
            value_type="boolean",
            control_type="agreement_checkbox",
        ),
    ]


def _business_nodes(path_item: Mapping[str, Any]) -> list[str]:
    return [
        str(node_id)
        for node_id in path_item.get("nodes", []) or []
        if str(node_id) not in _IGNORED_PATH_NODES
    ]


def _model_slug_from_ref(ref: object) -> str:
    text = str(ref or "")
    marker = "#/page_models/"
    if marker not in text:
        return ""
    return text.split(marker, 1)[1].split("/", 1)[0]


def _model_for_node(element_set: Mapping[str, Any], node_id: str) -> tuple[str, dict[str, Any] | None]:
    ref = ((element_set.get("quick_lookup", {}) or {}).get("by_node", {}) or {}).get(node_id)
    if not ref and node_id in _NODE_MODEL_ALIASES:
        ref = ((element_set.get("quick_lookup", {}) or {}).get("by_node", {}) or {}).get(_NODE_MODEL_ALIASES[node_id])
    slug = _model_slug_from_ref(ref)
    if not slug:
        return "", None
    model = ((element_set.get("page_models", {}) or {}).get(slug)) or None
    return slug, dict(model) if isinstance(model, dict) else None


def _css_attr_value(value: object) -> str:
    return str(value or "").replace("\\", "\\\\").replace('"', '\\"')


def _selector_from_param(value: object) -> str:
    escaped = _css_attr_value(value)
    return (
        f'input:not([type="hidden"])[name*="{escaped}" i], '
        f'textarea[name*="{escaped}" i], select[name*="{escaped}" i], '
        f'input:not([type="hidden"])[id*="{escaped}" i], '
        f'textarea[id*="{escaped}" i], select[id*="{escaped}" i], '
        f'input:not([type="hidden"])[placeholder*="{escaped}" i], '
        f'textarea[placeholder*="{escaped}" i], '
        f'input:not([type="hidden"])[aria-label*="{escaped}" i], '
        f'textarea[aria-label*="{escaped}" i], select[aria-label*="{escaped}" i]'
    )


def _param_locator_as_selector(locator: Mapping[str, Any]) -> dict[str, Any] | None:
    if locator.get("by") != "param" or not locator.get("value"):
        return None
    return {
        "by": "selector",
        "value": _selector_from_param(locator.get("value")),
        "source_by": "param",
        "source_value": locator.get("value"),
    }


def _semantic_selector_for_field(field: Mapping[str, Any], *, node_id: str) -> dict[str, Any] | None:
    if node_id != "NODE-insure-form":
        return None
    field_key = str(field.get("field_key") or "")
    aliases = _INSURE_FORM_SEMANTIC_SELECTOR_ALIASES.get(field_key)
    if not aliases:
        return None
    selector = ", ".join(_selector_from_param(alias) for alias in aliases)
    return {
        "by": "selector",
        "value": selector,
        "source_by": "semantic",
        "source_value": field_key,
    }


def _preferred_locator(
    locators: list[Mapping[str, Any]],
    *,
    allow_text: bool,
    allow_param_selector: bool = False,
) -> dict[str, Any]:
    for locator in locators:
        if locator.get("by") == "selector" and locator.get("value"):
            return {"by": "selector", "value": locator.get("value")}
    if allow_param_selector and locators:
        selector = _param_locator_as_selector(locators[0])
        if selector:
            return selector
    if allow_text:
        for locator in locators:
            by = str(locator.get("by") or "")
            if by in {"text", "label_text"} or by.startswith("role:"):
                return {"by": by, "value": locator.get("value")}
    if allow_param_selector:
        for locator in locators:
            selector = _param_locator_as_selector(locator)
            if selector:
                return selector
    return dict(locators[0]) if locators else {}


def _locator_kinds(action: Mapping[str, Any]) -> set[str]:
    return {
        str(locator.get("by") or "")
        for locator in action.get("locators", []) or []
        if isinstance(locator, Mapping)
    }


def _is_executable_action(action: Mapping[str, Any]) -> bool:
    if action.get("selector"):
        return True
    kinds = _locator_kinds(action)
    if kinds & {"function", "param", "role:heading"}:
        return False
    return bool(str(action.get("text") or "").strip())


def _action_priority(action: Mapping[str, Any]) -> tuple[int, int, str]:
    action_key = str(action.get("action_key") or "")
    text = str(action.get("text") or "")
    kinds = _locator_kinds(action)
    key_rank = {
        "action.submit": 0,
        "action.buy_now": 1,
        "action.next": 2,
        "action.pay": 3,
        "action.identity_auth": 4,
        "action.agree_all": 5,
    }.get(action_key, 50)
    text_lower = text.lower()
    text_rank = 0 if any(
        token in text_lower
        for token in (
            "提交",
            "下一步",
            "继续",
            "立即投保",
            "去完成",
            "支付",
            "submit",
            "continue",
            "start application",
            "pay",
        )
    ) else 10
    locator_penalty = 0 if action.get("selector") else 1
    if kinds & {"role:tab", "has_text"}:
        locator_penalty += 5
    return (key_rank + text_rank, locator_penalty, text)


def _element_text(item: Mapping[str, Any]) -> str:
    locator_text = " ".join(
        str(locator.get("value") or "")
        for locator in item.get("locators", []) or []
        if isinstance(locator, Mapping)
    )
    return " ".join(
        str(item.get(key) or "")
        for key in ("field_key", "action_key", "label", "text", "value_type", "mock_strategy")
    ).lower() + " " + locator_text.lower()


def _locator_status(preferred: Mapping[str, Any], locators: list[Mapping[str, Any]]) -> str:
    if preferred.get("by") == "selector" and preferred.get("value"):
        return "verified_static"
    if preferred.get("value") or locators:
        return "candidate_only"
    return "missing"


def _control_type_for_field(field: Mapping[str, Any]) -> str:
    text = _element_text(field)
    value_type = str(field.get("value_type") or "").lower()
    if "occupation" in text or "职业" in text:
        return "occupation_picker"
    if any(token in text for token in ("region", "province", "city", "area", "地区", "省", "市")):
        return "region_picker"
    if value_type == "date" or any(token in text for token in ("birthdate", "date", "生日", "出生日期", "起保日")):
        return "date_picker"
    if "agreement" in text or "协议" in text or "已阅读" in text:
        return "agreement_checkbox"
    if value_type == "enum" or any(token in text for token in ("relation", "relationship", "关系", "类型")):
        return "select"
    if value_type == "boolean":
        return "checkbox"
    if value_type == "number":
        return "input_number"
    return "input_text"


def _fill_strategy_for_control(control_type: str) -> str:
    return {
        "input_text": "fill_text",
        "input_number": "fill_number",
        "select": "select_by_text_or_value",
        "date_picker": "date_picker_select_or_fill",
        "occupation_picker": "occupation_search_and_select",
        "region_picker": "region_cascade_select",
        "agreement_checkbox": "check_agreement",
        "checkbox": "check",
    }.get(control_type, "unsupported")


def _field_strategy_status(control_type: str) -> str:
    return "supported" if _fill_strategy_for_control(control_type) != "unsupported" else "unsupported"


def _click_strategy_for_action(action: Mapping[str, Any]) -> str:
    action_key = str(action.get("action_key") or "")
    if action_key == "action.answer_questionnaire":
        return "business_questionnaire_rule"
    if action_key == "action.answer_health_notice":
        return "health_notice_no_issue"
    if action.get("selector"):
        return "click_by_selector"
    if action.get("text"):
        return "click_by_text"
    return "unsupported"


def _is_confirmation_prompt_action(action: Mapping[str, Any]) -> bool:
    text = str(action.get("text") or "")
    return any(token in text for token in ("确认进入投保流程",))


def _confirmation_cta_actions(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    ctas: list[dict[str, Any]] = []
    for action in (record.get("selector_map", {}) or {}).get("actions", []) or []:
        action_key = str(action.get("action_key") or "")
        text = str(action.get("text") or "")
        text_lower = text.lower()
        if action_key != "action.agree_all" and not any(
            token in text
            for token in ("已阅读并同意", "已阅读并确认", "同意并继续", "进入下一步")
        ) and not any(token in text_lower for token in ("agree", "continue")):
            continue
        if _is_executable_action(action):
            ctas.append(dict(action))
    ctas.sort(key=_action_priority)
    return ctas


def _transition_action_for_record(record: Mapping[str, Any]) -> dict[str, Any] | None:
    actions = [
        dict(action)
        for action in (record.get("selector_map", {}) or {}).get("actions", []) or []
        if action.get("required") and _is_executable_action(action)
    ]
    if not actions:
        return None
    actions.sort(key=_action_priority)
    selected = actions[0]
    if _is_confirmation_prompt_action(selected):
        ctas = _confirmation_cta_actions(record)
        if ctas:
            return {**ctas[0], "required": True}
    return selected



def _supporting_overlay_actions(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    overlays: list[dict[str, Any]] = []
    for action in (record.get("selector_map", {}) or {}).get("actions", []) or []:
        action_key = str(action.get("action_key") or "")
        text = str(action.get("text") or "")
        text_lower = text.lower()
        if action_key != "action.agree_all" and not any(token in text for token in ("同意", "已阅读", "阅读并确认")) and not any(
            token in text_lower for token in ("agree", "accept")
        ):
            continue
        if _is_executable_action(action):
            overlays.append(dict(action))
    overlays.sort(key=_action_priority)
    return overlays[:1]


def _questionnaire_post_submit_actions(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    followups: list[dict[str, Any]] = []
    for action in (record.get("selector_map", {}) or {}).get("actions", []) or []:
        text = str(action.get("text") or "")
        text_lower = text.lower()
        if not any(token in text for token in ("继续投保", "继续", "确认继续")) and not any(
            token in text_lower for token in ("continue application", "continue")
        ):
            continue
        if _is_executable_action(action):
            followups.append(dict(action))
    followups.sort(key=_action_priority)
    return followups[:1]


def _should_answer_questionnaire_before_action(action: Mapping[str, Any]) -> bool:
    return str(action.get("action_key") or "") in {"action.submit", "action.next"}


def _is_questionnaire_record(record: Mapping[str, Any]) -> bool:
    node_id = str(record.get("node_id") or "")
    page_model_id = str(record.get("page_model_id") or "")
    signals = " ".join(
        str(signal)
        for key in ("entry_signals", "exit_signals", "text_hints")
        for signal in (record.get("match_contract", {}) or {}).get(key, []) or []
    )
    text = f"{node_id} {page_model_id} {signals}"
    return any(token in text for token in ("questionnaire", "suitability", "问卷", "适当性", "风险测评"))


def _is_health_notice_record(record: Mapping[str, Any]) -> bool:
    node_id = str(record.get("node_id") or "")
    page_model_id = str(record.get("page_model_id") or "")
    signals = " ".join(
        str(signal)
        for key in ("entry_signals", "exit_signals", "text_hints")
        for signal in (record.get("match_contract", {}) or {}).get(key, []) or []
    )
    text = f"{node_id} {page_model_id} {signals}"
    return any(token in text for token in ("health-notice", "健康告知", "确认无以上问题"))


def _is_auto_transition_record(record: Mapping[str, Any]) -> bool:
    node_id = str(record.get("node_id") or "")
    page_model_id = str(record.get("page_model_id") or "")
    signals = " ".join(
        str(signal)
        for key in ("entry_signals", "exit_signals", "text_hints")
        for signal in (record.get("match_contract", {}) or {}).get(key, []) or []
    )
    text = f"{node_id} {page_model_id} {signals}".lower()
    return any(token in text for token in ("callback", "回调", "核保中", "处理中"))


def _auto_wait_action(
    *,
    path_id: str,
    node_id: str,
    next_node_id: str,
    step_index: int,
    skip_if_absent: bool = False,
) -> dict[str, Any]:
    return {
        "action_key": "action.auto_wait_for_next_node",
        "text": "auto_wait_for_next_node",
        "selector": None,
        "tag": "auto-wait",
        "source": "agent3.static-contract",
        "source_url": None,
        "required": True,
        "locators": [],
        "path_id": path_id,
        "script_step_id": f"{path_id}-STEP-{step_index:03d}",
        "step_index": step_index,
        "planned_from_node_id": node_id,
        "planned_to_node_id": next_node_id,
        "expected_next_node_id": next_node_id,
        "click_strategy": "auto_wait_for_next_node",
        "skip_if_absent": skip_if_absent,
    }


def _questionnaire_answer_action(
    *,
    path_id: str,
    node_id: str,
    next_node_id: str,
    step_index: int,
) -> dict[str, Any]:
    return {
        "action_key": "action.answer_questionnaire",
        "text": "business_questionnaire_rule",
        "selector": None,
        "tag": "questionnaire",
        "source": "agent3.static-contract",
        "source_url": None,
        "required": True,
        "locators": [],
        "path_id": path_id,
        "script_step_id": f"{path_id}-STEP-{step_index:03d}",
        "step_index": step_index,
        "planned_from_node_id": node_id,
        "planned_to_node_id": next_node_id,
        "expected_next_node_id": node_id,
        "answer_strategy": "business_questionnaire_rule",
    }


def _health_notice_answer_action(
    *,
    path_id: str,
    node_id: str,
    next_node_id: str,
    step_index: int,
) -> dict[str, Any]:
    return {
        "action_key": "action.answer_health_notice",
        "text": "确认无以上问题",
        "selector": None,
        "tag": "health-notice",
        "source": "agent3.static-contract",
        "source_url": None,
        "required": True,
        "locators": [{"by": "text", "value": "确认无以上问题"}],
        "path_id": path_id,
        "script_step_id": f"{path_id}-STEP-{step_index:03d}",
        "step_index": step_index,
        "planned_from_node_id": node_id,
        "planned_to_node_id": next_node_id,
        "expected_next_node_id": node_id,
        "answer_strategy": "health_notice_no_issue",
    }


def _compile_action_chain(
    *,
    path_id: str,
    required_nodes: list[str],
    records_by_node: Mapping[str, Mapping[str, Any]],
    optional_nodes: set[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    chain: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    optional_nodes = optional_nodes or set()
    for index, node_id in enumerate(required_nodes[:-1]):
        next_node_id = required_nodes[index + 1]
        record = records_by_node.get(node_id)
        if not record:
            continue
        selected = _transition_action_for_record(record)
        if selected is None:
            if _is_auto_transition_record(record):
                chain.append(
                    _auto_wait_action(
                        path_id=path_id,
                        node_id=node_id,
                        next_node_id=next_node_id,
                        step_index=len(chain) + 1,
                        skip_if_absent=node_id in optional_nodes,
                    )
                )
                continue
            if node_id not in optional_nodes:
                missing.append(
                    {
                        "path_id": path_id,
                        "node_id": node_id,
                        "next_node_id": next_node_id,
                        "reason": "missing_executable_transition_action",
                    }
                )
            continue
        if _is_questionnaire_record(record):
            if _should_answer_questionnaire_before_action(selected):
                followups = (
                    _questionnaire_post_submit_actions(record)
                    if selected.get("action_key") == "action.submit"
                    else []
                )
                candidates = [
                    _questionnaire_answer_action(
                        path_id=path_id,
                        node_id=node_id,
                        next_node_id=next_node_id,
                        step_index=len(chain) + 1,
                    ),
                    selected,
                    *followups,
                ]
            else:
                candidates = [selected]
        elif _is_health_notice_record(record):
            candidates = [
                _health_notice_answer_action(
                    path_id=path_id,
                    node_id=node_id,
                    next_node_id=next_node_id,
                    step_index=len(chain) + 1,
                ),
                selected,
            ]
        else:
            overlays = _supporting_overlay_actions(record)
            candidates = [*overlays, selected] if selected.get("action_key") == "action.submit" else [selected, *overlays]
        for action in candidates:
            dedupe_key = (
                node_id,
                str(action.get("action_key") or ""),
                str(action.get("selector") or action.get("text") or ""),
            )
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            chain.append(
                {
                    **action,
                    "path_id": path_id,
                    "script_step_id": action.get("script_step_id") or f"{path_id}-STEP-{len(chain) + 1:03d}",
                    "step_index": len(chain) + 1,
                    "planned_from_node_id": node_id,
                    "planned_to_node_id": next_node_id,
                    "expected_next_node_id": action.get("expected_next_node_id") or next_node_id,
                    "skip_if_absent": bool(action.get("skip_if_absent") or node_id in optional_nodes),
                }
            )
    return chain, missing


def _should_promote_required_field(
    field: Mapping[str, Any],
    *,
    node_id: str,
    preferred: Mapping[str, Any],
) -> bool:
    if node_id != "NODE-insure-form":
        return False
    field_key = str(field.get("field_key") or "")
    if field_key not in _INSURE_FORM_CORE_REQUIRED_FIELD_KEYS:
        return False
    text = _element_text(field)
    if any(token in text for token in _INSURE_FORM_REQUIRED_EXCLUDED_TEXT):
        return False
    return preferred.get("by") == "selector" and bool(preferred.get("value"))


def _field_record(field: Mapping[str, Any], *, node_id: str = "") -> dict[str, Any]:
    locators = [dict(item) for item in field.get("locators", []) or []]
    preferred = _preferred_locator(locators, allow_text=True, allow_param_selector=True)
    if preferred.get("by") != "selector":
        semantic_selector = _semantic_selector_for_field(field, node_id=node_id)
        if semantic_selector:
            preferred = semantic_selector
    control_type = str(field.get("control_type") or _control_type_for_field(field))
    fill_strategy = _fill_strategy_for_control(control_type)
    locator_status = _locator_status(preferred, locators)
    mock_strategy = field.get("mock_strategy") or "mock"
    required = bool(field.get("required")) or _should_promote_required_field(
        field,
        node_id=node_id,
        preferred=preferred,
    )
    return {
        "field_key": field.get("field_key"),
        "selector": preferred.get("value") if preferred.get("by") == "selector" else None,
        "source": "static-model",
        "required": required,
        "value_strategy": mock_strategy,
        "control_type": control_type,
        "fill_strategy": fill_strategy,
        "strategy_status": _field_strategy_status(control_type),
        "locator_status": locator_status,
        "selected_locator": dict(preferred) if preferred else {},
        "mock_key": field.get("field_key"),
        "mock_status": "mapped" if field.get("field_key") and mock_strategy else "missing",
        "locators": locators,
        "raw": {
            "tag": "input",
            "type": field.get("value_type") or "string",
            "label": field.get("label") or field.get("field_key"),
            "preferred_locator": preferred,
        },
    }


def _action_record(action: Mapping[str, Any], actual_url: str | None) -> dict[str, Any]:
    locators = [dict(item) for item in action.get("locators", []) or []]
    preferred = _preferred_locator(locators, allow_text=True)
    text = action.get("text")
    if not text and preferred.get("by") != "selector":
        text = preferred.get("value")
    selector = preferred.get("value") if preferred.get("by") == "selector" else None
    locator_status = _locator_status(preferred, locators)
    click_strategy = "click_by_selector" if selector else ("click_by_text" if text else "unsupported")
    return {
        "action_key": action.get("action_key"),
        "text": text,
        "selector": selector,
        "tag": "button",
        "source": "static-model",
        "source_url": actual_url,
        "required": bool(action.get("required")),
        "control_type": "button",
        "click_strategy": click_strategy,
        "strategy_status": "supported" if click_strategy != "unsupported" else "unsupported",
        "locator_status": locator_status,
        "selected_locator": dict(preferred) if preferred else {},
        "locators": locators,
    }


def _page_record(
    *,
    sequence: int,
    slug: str,
    model: Mapping[str, Any],
    entry_url: str | None,
) -> dict[str, Any]:
    node_id = str(model.get("node_id") or "")
    actual_url = model.get("actual_url_sample") or (entry_url if node_id == "NODE-product-detail" else None)
    raw_fields = [dict(field) for field in model.get("fields", []) or []]
    if node_id == "NODE-insure-form":
        raw_fields.extend(_insure_form_supplemental_fields())
    fields = [_field_record(field, node_id=node_id) for field in raw_fields]
    actions = [_action_record(action, str(actual_url) if actual_url else None) for action in model.get("actions", []) or []]
    return {
        "page_content_record_id": f"PCR-STATIC-{sequence:03d}",
        "page_model_id": model.get("page_model_id") or f"PM-{slug}",
        "node_id": node_id,
        "actual_url": actual_url,
        "actual_page_key": model.get("page_key_pattern") or f"PK-{slug}",
        "title": slug.replace("-", " ").title(),
        "dom_signature": f"static:{model.get('page_model_id') or slug}:v1",
        "match_contract": dict(model.get("match_contract", {}) or {}),
        "field_count": len(fields),
        "action_count": len(actions),
        "field_map": fields,
        "selector_map": {
            "page_key": model.get("page_key_pattern") or f"PK-{slug}",
            "url": actual_url,
            "fields": fields,
            "actions": actions,
        },
        "matched_planned_page_ids": [],
        "matched_node_ids": [node_id] if node_id else [],
        "source_path_ids": [],
        "source": "agent3.static-element-set",
    }


def _field_resolution_item(record: Mapping[str, Any], field: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "node_id": record.get("node_id"),
        "page_model_id": record.get("page_model_id"),
        "page_content_record_id": record.get("page_content_record_id"),
        "field_key": field.get("field_key"),
        "required": bool(field.get("required")),
        "locator_status": field.get("locator_status") or "missing",
        "selected_locator": dict(field.get("selected_locator", {}) or {}),
        "locator_candidates": [dict(item) for item in field.get("locators", []) or []],
        "control_type": field.get("control_type"),
        "fill_strategy": field.get("fill_strategy"),
        "mock_key": field.get("mock_key") or field.get("field_key"),
        "mock_strategy": field.get("value_strategy"),
        "mock_status": field.get("mock_status") or "missing",
        "proof": (
            "static selector verified by element set"
            if field.get("locator_status") == "verified_static"
            else "targeted probe required before Agent4 execution"
        ),
    }


def _build_field_resolution_plan(records: list[Mapping[str, Any]]) -> dict[str, Any]:
    fields = [
        _field_resolution_item(record, field)
        for record in records
        for field in record.get("field_map", []) or []
    ]
    required = [item for item in fields if item.get("required")]
    verified_required = [
        item for item in required if item.get("locator_status") == "verified_static"
    ]
    return {
        "source": "agent3.static-contract",
        "version": "1.0",
        "fields": fields,
        "summary": {
            "field_count": len(fields),
            "required_field_count": len(required),
            "verified_required_field_count": len(verified_required),
            "probe_required_field_count": len(required) - len(verified_required),
        },
    }


def _component_field_strategy(record: Mapping[str, Any], field: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "node_id": record.get("node_id"),
        "page_model_id": record.get("page_model_id"),
        "field_key": field.get("field_key"),
        "required": bool(field.get("required")),
        "control_type": field.get("control_type"),
        "fill_strategy": field.get("fill_strategy"),
        "strategy_status": field.get("strategy_status") or "unsupported",
        "locator_status": field.get("locator_status") or "missing",
    }


def _component_action_strategy(record: Mapping[str, Any], action: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "node_id": record.get("node_id"),
        "page_model_id": record.get("page_model_id"),
        "action_key": action.get("action_key"),
        "text": action.get("text"),
        "required": bool(action.get("required")),
        "control_type": action.get("control_type") or "button",
        "click_strategy": action.get("click_strategy") or _click_strategy_for_action(action),
        "strategy_status": action.get("strategy_status") or (
            "supported" if _click_strategy_for_action(action) != "unsupported" else "unsupported"
        ),
        "locator_status": action.get("locator_status") or "missing",
    }


def _build_component_strategy(records: list[Mapping[str, Any]]) -> dict[str, Any]:
    field_strategies = [
        _component_field_strategy(record, field)
        for record in records
        for field in record.get("field_map", []) or []
    ]
    action_strategies = [
        _component_action_strategy(record, action)
        for record in records
        for action in (record.get("selector_map", {}) or {}).get("actions", []) or []
    ]
    unsupported_required_fields = [
        item
        for item in field_strategies
        if item.get("required") and item.get("strategy_status") != "supported"
    ]
    unsupported_required_actions = [
        item
        for item in action_strategies
        if item.get("required") and item.get("strategy_status") != "supported"
    ]
    return {
        "source": "agent3.static-contract",
        "version": "1.0",
        "field_strategies": field_strategies,
        "action_strategies": action_strategies,
        "summary": {
            "field_strategy_count": len(field_strategies),
            "action_strategy_count": len(action_strategies),
            "unsupported_required_component_count": len(unsupported_required_fields) + len(unsupported_required_actions),
        },
    }


def _gate(name: str, proofs: list[Mapping[str, Any]]) -> dict[str, Any]:
    failed = [item for item in proofs if item.get("status") != "passed"]
    return {
        "gate": name,
        "status": "passed" if not failed else "failed",
        "passed_count": len(proofs) - len(failed),
        "failed_count": len(failed),
    }


def _build_path_validation_report(
    *,
    path_id: str,
    required_nodes: list[str],
    optional_nodes: set[str],
    records_by_node: Mapping[str, Mapping[str, Any]],
    node_progress: list[Mapping[str, Any]],
    action_chain: list[Mapping[str, Any]],
) -> dict[str, Any]:
    active_nodes = [node_id for node_id in required_nodes if node_id not in optional_nodes or node_id in records_by_node]
    progress_by_node = {str(item.get("node_id") or ""): dict(item) for item in node_progress}
    page_proofs = []
    for node_id in required_nodes:
        progress = progress_by_node.get(node_id, {})
        optional_missing = progress.get("status") == "optional_missing"
        record = records_by_node.get(node_id)
        passed = bool(record) or optional_missing
        page_proofs.append(
            {
                "node_id": node_id,
                "page_model_id": record.get("page_model_id") if record else None,
                "status": "passed" if passed else "failed",
                "strategy": "static_page_model_match" if record else "optional_skip" if optional_missing else "missing_page_model",
                "entry_signals": list((record.get("match_contract", {}) or {}).get("entry_signals", []) or []) if record else [],
                "url_patterns": list((record.get("match_contract", {}) or {}).get("url_patterns", []) or []) if record else [],
            }
        )

    field_proofs = []
    component_proofs = []
    mock_proofs = []
    for node_id in active_nodes:
        record = records_by_node.get(node_id)
        if not record:
            continue
        for field in record.get("field_map", []) or []:
            if not field.get("required"):
                continue
            locator_passed = field.get("locator_status") == "verified_static"
            strategy_passed = field.get("strategy_status") == "supported"
            mock_passed = bool(field.get("field_key") and field.get("value_strategy"))
            field_proofs.append(
                {
                    "node_id": node_id,
                    "field_key": field.get("field_key"),
                    "status": "passed" if locator_passed else "failed",
                    "locator_status": field.get("locator_status"),
                    "selected_locator": dict(field.get("selected_locator", {}) or {}),
                }
            )
            component_proofs.append(
                {
                    "node_id": node_id,
                    "field_key": field.get("field_key"),
                    "status": "passed" if strategy_passed else "failed",
                    "control_type": field.get("control_type"),
                    "fill_strategy": field.get("fill_strategy"),
                }
            )
            mock_proofs.append(
                {
                    "node_id": node_id,
                    "field_key": field.get("field_key"),
                    "status": "passed" if mock_passed else "failed",
                    "mock_key": field.get("mock_key") or field.get("field_key"),
                    "mock_strategy": field.get("value_strategy"),
                }
            )

    action_proofs = []
    transition_proofs = []
    for action in action_chain:
        click_strategy = action.get("click_strategy") or _click_strategy_for_action(action)
        clickable = click_strategy != "unsupported"
        action_proofs.append(
            {
                "script_step_id": action.get("script_step_id"),
                "action_key": action.get("action_key"),
                "text": action.get("text"),
                "planned_from_node_id": action.get("planned_from_node_id"),
                "planned_to_node_id": action.get("planned_to_node_id"),
                "status": "passed" if clickable else "failed",
                "click_strategy": click_strategy,
                "selector": action.get("selector"),
            }
        )
        has_transition = bool(action.get("planned_from_node_id") and action.get("planned_to_node_id"))
        transition_proofs.append(
            {
                "script_step_id": action.get("script_step_id"),
                "action_key": action.get("action_key"),
                "planned_from_node_id": action.get("planned_from_node_id"),
                "planned_to_node_id": action.get("planned_to_node_id"),
                "expected_next_node_id": action.get("expected_next_node_id") or action.get("planned_to_node_id"),
                "status": "passed" if has_transition else "failed",
                "strategy": "agent4_click_then_match_expected_node",
            }
        )

    proofs = {
        "page_recognition": page_proofs,
        "required_field_location": field_proofs,
        "action_clickability": action_proofs,
        "component_strategy_coverage": component_proofs,
        "mock_data_mapping": mock_proofs,
        "transition_reachability": transition_proofs,
    }
    gates = [_gate(name, items) for name, items in proofs.items()]
    failed_gates = [item for item in gates if item.get("status") != "passed"]
    return {
        "source": "agent3.static-contract",
        "path_id": path_id,
        "status": "passed" if not failed_gates else "failed",
        "agent4_ready": not failed_gates,
        "gates": gates,
        "proofs": proofs,
        "summary": {
            "gate_count": len(gates),
            "failed_gate_count": len(failed_gates),
            "required_node_count": len(required_nodes),
            "required_field_count": len(field_proofs),
            "action_count": len(action_proofs),
        },
    }


def _aggregate_validation_report(path_results: list[Mapping[str, Any]]) -> dict[str, Any]:
    reports = [dict(item.get("validation_report", {}) or {}) for item in path_results]
    failed = [item for item in reports if item.get("status") != "passed"]
    return {
        "source": "agent3.static-contract",
        "status": "passed" if not failed else "failed",
        "agent4_ready": not failed,
        "path_reports": reports,
        "summary": {
            "path_count": len(reports),
            "ready_path_count": len(reports) - len(failed),
            "blocked_path_count": len(failed),
        },
    }


def _targeted_probe_request_for_field(
    *,
    path_id: str,
    sequence: int,
    record: Mapping[str, Any],
    field: Mapping[str, Any],
) -> dict[str, Any] | None:
    if not field.get("required") or field.get("locator_status") == "verified_static":
        return None
    match_contract = dict(record.get("match_contract", {}) or {})
    return {
        "probe_id": f"TP-{path_id}-{sequence:03d}",
        "path_id": path_id,
        "kind": "field",
        "node_id": record.get("node_id"),
        "page_model_id": record.get("page_model_id"),
        "page_content_record_id": record.get("page_content_record_id"),
        "field_key": field.get("field_key"),
        "reason": "required_field_needs_verified_locator",
        "current_locator_status": field.get("locator_status") or "missing",
        "candidate_locators": [dict(item) for item in field.get("locators", []) or []],
        "probe_scope": "current_node_only",
        "probe_strategy": "resolve_field_by_label_context",
        "entry_signals": list(match_contract.get("entry_signals", []) or []),
        "url_patterns": list(match_contract.get("url_patterns", []) or []),
        "control_type": field.get("control_type"),
        "fill_strategy": field.get("fill_strategy"),
        "mock_key": field.get("mock_key") or field.get("field_key"),
        "mock_strategy": field.get("value_strategy"),
        "acceptance_criteria": [
            "locator_status=verified_static",
            "selected_locator.by=selector",
            "mock_status=mapped",
            "component_strategy.status=supported",
        ],
    }


def _targeted_probe_request_for_action(
    *,
    path_id: str,
    sequence: int,
    record: Mapping[str, Any],
    action: Mapping[str, Any],
) -> dict[str, Any] | None:
    if not action.get("required") or _is_executable_action(action):
        return None
    match_contract = dict(record.get("match_contract", {}) or {})
    return {
        "probe_id": f"TP-{path_id}-{sequence:03d}",
        "path_id": path_id,
        "kind": "action",
        "node_id": record.get("node_id"),
        "page_model_id": record.get("page_model_id"),
        "page_content_record_id": record.get("page_content_record_id"),
        "action_key": action.get("action_key"),
        "reason": "required_action_needs_clickable_locator",
        "current_locator_status": action.get("locator_status") or "missing",
        "candidate_locators": [dict(item) for item in action.get("locators", []) or []],
        "probe_scope": "current_node_only",
        "probe_strategy": "resolve_action_by_text_role_or_selector",
        "entry_signals": list(match_contract.get("entry_signals", []) or []),
        "url_patterns": list(match_contract.get("url_patterns", []) or []),
        "click_strategy": action.get("click_strategy"),
        "acceptance_criteria": [
            "locator_status=verified_static",
            "selected_locator.by=selector",
            "click_strategy=supported",
            "expected_next_node_id=mapped",
        ],
    }


def _page_key_for_node(path_item: Mapping[str, Any], node_id: str) -> dict[str, Any]:
    for item in path_item.get("page_keys", []) or []:
        if isinstance(item, Mapping) and item.get("node_id") == node_id:
            return dict(item)
    return {}


def _targeted_probe_request_for_missing_page_model(
    *,
    path_id: str,
    sequence: int,
    node_id: str,
    path_item: Mapping[str, Any],
) -> dict[str, Any]:
    page_key = _page_key_for_node(path_item, node_id)
    return {
        "probe_id": f"TP-{path_id}-{sequence:03d}",
        "path_id": path_id,
        "kind": "page_model",
        "node_id": node_id,
        "page_key": page_key.get("page_key"),
        "reason": "missing_page_model",
        "probe_scope": "current_or_reachable_node",
        "probe_strategy": "discover_page_model_by_agent2_node",
        "url_patterns": [str(page_key["url_pattern"])] if page_key.get("url_pattern") else [],
        "allowed_state_keys": list(page_key.get("allowed_state_keys", []) or []),
        "acceptance_criteria": [
            "page_model_id=mapped",
            "match_contract.entry_signals_present",
            "field_map_or_selector_map_present",
            "static_element_set_updated",
        ],
    }


def _targeted_probe_request_for_missing_transition_action(
    *,
    path_id: str,
    sequence: int,
    transition: Mapping[str, Any],
    path_item: Mapping[str, Any],
) -> dict[str, Any]:
    from_node_id = str(transition.get("node_id") or "")
    to_node_id = str(transition.get("next_node_id") or "")
    page_key = _page_key_for_node(path_item, from_node_id)
    return {
        "probe_id": f"TP-{path_id}-{sequence:03d}",
        "path_id": path_id,
        "kind": "transition_action",
        "node_id": from_node_id,
        "next_node_id": to_node_id,
        "page_key": page_key.get("page_key"),
        "reason": "missing_executable_transition_action",
        "probe_scope": "current_node_only",
        "probe_strategy": "discover_transition_action_to_next_node",
        "url_patterns": [str(page_key["url_pattern"])] if page_key.get("url_pattern") else [],
        "acceptance_criteria": [
            "action_key=mapped",
            "selected_locator.by=selector_or_text",
            "click_strategy=supported",
            "expected_next_node_id=mapped",
        ],
    }


def _build_targeted_probe_plan(
    path_id: str,
    records: list[Mapping[str, Any]],
    *,
    missing_nodes: list[str] | None = None,
    missing_transition_actions: list[Mapping[str, Any]] | None = None,
    path_item: Mapping[str, Any] | None = None,
    knowledge_hints: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    requests: list[dict[str, Any]] = []
    path_item = path_item or {}
    for node_id in missing_nodes or []:
        requests.append(
            _targeted_probe_request_for_missing_page_model(
                path_id=path_id,
                sequence=len(requests) + 1,
                node_id=node_id,
                path_item=path_item,
            )
        )
    for record in records:
        for field in record.get("field_map", []) or []:
            request = _targeted_probe_request_for_field(
                path_id=path_id,
                sequence=len(requests) + 1,
                record=record,
                field=field,
            )
            if request:
                requests.append(request)
        has_transition_action = _transition_action_for_record(record) is not None or _is_auto_transition_record(record)
        for action in (record.get("selector_map", {}) or {}).get("actions", []) or []:
            if has_transition_action:
                continue
            request = _targeted_probe_request_for_action(
                path_id=path_id,
                sequence=len(requests) + 1,
                record=record,
                action=action,
            )
            if request:
                requests.append(request)
    for transition in missing_transition_actions or []:
        requests.append(
            _targeted_probe_request_for_missing_transition_action(
                path_id=path_id,
                sequence=len(requests) + 1,
                transition=transition,
                path_item=path_item,
            )
        )
    plan = {
        "source": "agent3.static-contract",
        "version": "1.0",
        "status": "required" if requests else "not_required",
        "requests": requests,
        "summary": {
            "request_count": len(requests),
            "field_request_count": len([item for item in requests if item.get("kind") == "field"]),
            "action_request_count": len([item for item in requests if item.get("kind") == "action"]),
            "page_model_request_count": len([item for item in requests if item.get("kind") == "page_model"]),
            "transition_action_request_count": len(
                [item for item in requests if item.get("kind") == "transition_action"]
            ),
        },
    }
    return enrich_targeted_probe_plan(plan, knowledge_hints) if knowledge_hints else plan


def _aggregate_targeted_probe_plan(path_results: list[Mapping[str, Any]]) -> dict[str, Any]:
    requests = [
        dict(request)
        for path in path_results
        for request in ((path.get("targeted_probe_plan", {}) or {}).get("requests", []) or [])
    ]
    return {
        "source": "agent3.static-contract",
        "version": "1.0",
        "status": "required" if requests else "not_required",
        "requests": requests,
        "summary": {
            "request_count": len(requests),
            "field_request_count": len([item for item in requests if item.get("kind") == "field"]),
            "action_request_count": len([item for item in requests if item.get("kind") == "action"]),
            "page_model_request_count": len([item for item in requests if item.get("kind") == "page_model"]),
            "transition_action_request_count": len(
                [item for item in requests if item.get("kind") == "transition_action"]
            ),
        },
    }


def _probe_owner(kind: str) -> str:
    if kind in {"field", "action"}:
        return "ai-testing"
    return "ai-fullstack"


def _missing_report_item(request: Mapping[str, Any]) -> dict[str, Any]:
    kind = str(request.get("kind") or "unknown")
    return {
        "kind": kind,
        "owner": _probe_owner(kind),
        "probe_id": request.get("probe_id"),
        "path_id": request.get("path_id"),
        "node_id": request.get("node_id"),
        "next_node_id": request.get("next_node_id"),
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
        "source": "agent3.static-contract",
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


def _missing_required_elements(records: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    missing: list[dict[str, Any]] = []
    for record in records:
        page_model_id = str(record.get("page_model_id") or "")
        for field in record.get("field_map", []) or []:
            if field.get("required") and field.get("locator_status") != "verified_static":
                missing.append(
                    {
                        "kind": "field",
                        "node_id": record.get("node_id"),
                        "page_model_id": page_model_id,
                        "page_content_record_id": record.get("page_content_record_id"),
                        "field_key": field.get("field_key"),
                        "reason": "required_field_needs_targeted_probe",
                        "owner": "ai-testing",
                        "probe_scope": "current_node_only",
                        "probe_strategy": "resolve_field_by_label_context",
                    }
                )
        for action in (record.get("selector_map", {}) or {}).get("actions", []) or []:
            if action.get("required") and not (action.get("selector") or action.get("text")):
                missing.append(
                    {
                        "kind": "action",
                        "node_id": record.get("node_id"),
                        "page_model_id": page_model_id,
                        "page_content_record_id": record.get("page_content_record_id"),
                        "action_key": action.get("action_key"),
                        "reason": "missing_required_action_locator",
                        "owner": "ai-testing",
                        "probe_scope": "current_node_only",
                        "probe_strategy": "resolve_action_by_text_role_or_selector",
                    }
                )
    return missing


def _exploration_contract(path_results: list[Mapping[str, Any]]) -> dict[str, Any]:
    completed = [dict(item) for item in path_results if item.get("path_status") == "explored"]
    blocked = [dict(item) for item in path_results if item.get("path_status") != "explored"]
    return {
        "source": "agent3.static-contract",
        "completed_paths": completed,
        "blocked_paths": blocked,
        "summary": {
            "total_paths": len(path_results),
            "completed_path_count": len(completed),
            "blocked_path_count": len(blocked),
        },
    }


def build_static_explore_artifacts(
    *,
    product_id: str,
    entry_url: str | None,
    regression_paths: list[Mapping[str, Any]],
    element_set: Mapping[str, Any],
    knowledge_hints: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build Agent3 page_registry/explore_trace from static page models only."""
    records_by_node: dict[str, dict[str, Any]] = {}
    model_slugs_by_node: dict[str, str] = {}
    path_results: list[dict[str, Any]] = []
    missing_nodes_all: list[dict[str, Any]] = []
    missing_transition_actions_all: list[dict[str, Any]] = []

    for path_index, path_item in enumerate(regression_paths, start=1):
        path_id = str(path_item.get("path_id") or f"PATH-{path_index:03d}")
        required_nodes = _business_nodes(path_item)
        execution_policy = dict(path_item.get("execution_policy", {}) or {})
        skip_absent_optional_nodes = bool(execution_policy.get("skip_absent_optional_nodes"))
        optional_nodes = {
            str(node_id)
            for node_id in path_item.get("optional_nodes", []) or []
            if skip_absent_optional_nodes
        }
        page_content_refs: list[str] = []
        matched_nodes: list[str] = []
        missing_nodes: list[str] = []
        node_progress: list[dict[str, Any]] = []

        for node_id in required_nodes:
            slug, model = _model_for_node(element_set, node_id)
            if not model:
                if node_id in optional_nodes:
                    node_progress.append({"node_id": node_id, "status": "optional_missing", "matched": False})
                else:
                    missing_nodes.append(node_id)
                    missing_nodes_all.append({"path_id": path_id, "node_id": node_id, "reason": "missing_page_model"})
                    node_progress.append({"node_id": node_id, "status": "missing", "matched": False})
                continue
            if node_id not in records_by_node:
                records_by_node[node_id] = _page_record(
                    sequence=len(records_by_node) + 1,
                    slug=slug,
                    model=model,
                    entry_url=entry_url,
                )
                model_slugs_by_node[node_id] = slug
            record = records_by_node[node_id]
            record["source_path_ids"] = list(
                dict.fromkeys([*list(record.get("source_path_ids", []) or []), path_id])
            )
            page_content_refs.append(str(record["page_content_record_id"]))
            matched_nodes.append(node_id)
            node_progress.append({"node_id": node_id, "status": "matched", "matched": True})

        action_chain, missing_transition_actions = _compile_action_chain(
            path_id=path_id,
            required_nodes=required_nodes,
            records_by_node=records_by_node,
            optional_nodes=optional_nodes,
        )
        blocking_missing_transition_actions = [
            item for item in missing_transition_actions if item.get("node_id") not in optional_nodes
        ]
        missing_transition_actions_all.extend(blocking_missing_transition_actions)
        path_records = [
            records_by_node[node_id]
            for node_id in required_nodes
            if node_id in records_by_node
        ]
        path_field_resolution_plan = _build_field_resolution_plan(path_records)
        path_component_strategy = _build_component_strategy(path_records)
        path_targeted_probe_plan = _build_targeted_probe_plan(
            path_id,
            path_records,
            missing_nodes=missing_nodes,
            missing_transition_actions=blocking_missing_transition_actions,
            path_item=path_item,
            knowledge_hints=knowledge_hints,
        )
        validation_report = _build_path_validation_report(
            path_id=path_id,
            required_nodes=required_nodes,
            optional_nodes=optional_nodes,
            records_by_node=records_by_node,
            node_progress=node_progress,
            action_chain=action_chain,
        )
        is_complete = bool(required_nodes) and not missing_nodes and not blocking_missing_transition_actions and validation_report["agent4_ready"]
        completion_rule = {
            "source": "agent3.static-contract",
            "target_node": required_nodes[-1] if required_nodes else None,
            "required_nodes": required_nodes,
            "optional_nodes": sorted(optional_nodes),
            "matched_nodes": matched_nodes,
            "missing_nodes": [*missing_nodes, *[item["node_id"] for item in blocking_missing_transition_actions]],
            "is_complete": is_complete,
        }
        path_results.append(
            {
                "path_id": path_id,
                "case_ids": list(path_item.get("case_ids", []) or []),
                "target_node": required_nodes[-1] if required_nodes else None,
                "planned_page_refs": [model_slugs_by_node[node_id] for node_id in matched_nodes],
                "page_content_refs": page_content_refs,
                "node_progress": node_progress,
                "completion_rule": completion_rule,
                "path_status": "explored" if is_complete else "blocked",
                "blocked_node": (missing_nodes[0] if missing_nodes else None)
                or (blocking_missing_transition_actions[0]["node_id"] if blocking_missing_transition_actions else None),
                "blocked_reason": "Missing static page model" if missing_nodes else (
                    "Missing executable transition action" if blocking_missing_transition_actions else (
                        "Agent3 validation failed" if not validation_report["agent4_ready"] else None
                    )
                ),
                "action_chain": action_chain,
                "field_resolution_plan": path_field_resolution_plan,
                "component_strategy": path_component_strategy,
                "targeted_probe_plan": path_targeted_probe_plan,
                "validation_report": validation_report,
            }
        )

    records = list(records_by_node.values())
    missing_required_elements = _missing_required_elements(records)
    contract = _exploration_contract(path_results)
    field_resolution_plan = _build_field_resolution_plan(records)
    component_strategy = _build_component_strategy(records)
    validation_report = _aggregate_validation_report(path_results)
    targeted_probe_plan = _aggregate_targeted_probe_plan(path_results)
    if knowledge_hints:
        targeted_probe_plan = enrich_targeted_probe_plan(targeted_probe_plan, knowledge_hints)
    missing_report = _build_static_missing_report(targeted_probe_plan)
    knowledge_assist = knowledge_assist_summary(knowledge_hints)
    completed_path_count = int(contract["summary"]["completed_path_count"])
    blocked_path_count = int(contract["summary"]["blocked_path_count"])
    page_registry = {
        "product_id": product_id,
        "entry_url": entry_url,
        "platform": "h5" if entry_url and "/m/" in str(entry_url) else "pc",
        "generated_by": "agent3.static-first",
        "planned_page_catalog": [
            {
                "node_id": node_id,
                "page_model_id": record.get("page_model_id"),
                "page_content_record_id": record.get("page_content_record_id"),
            }
            for node_id, record in records_by_node.items()
        ],
        "pages": [
            {
                "page_key": record.get("actual_page_key"),
                "url": record.get("actual_url"),
                "source_url": record.get("actual_url"),
                "title": record.get("title"),
                "field_count": record.get("field_count"),
                "action_count": record.get("action_count"),
                "fields": record.get("field_map", []),
                "actions": (record.get("selector_map", {}) or {}).get("actions", []),
                "primary_actions": [
                    action
                    for action in (record.get("selector_map", {}) or {}).get("actions", [])
                    if action.get("required")
                ][:3],
                "candidate_links": [],
                "matched_node_ids": record.get("matched_node_ids", []),
            }
            for record in records
        ],
        "primary_actions": [
            action
            for record in records
            for action in (record.get("selector_map", {}) or {}).get("actions", [])
            if action.get("required")
        ][:5],
        "page_content_records": records,
        "path_exploration_results": path_results,
        "exploration_contract": contract,
        "field_resolution_plan": field_resolution_plan,
        "component_strategy": component_strategy,
        "targeted_probe_plan": targeted_probe_plan,
        "missing_report": missing_report,
        "selector_override_priority": [dict(item) for item in _SELECTOR_OVERRIDE_PRIORITY],
        "validation_report": validation_report,
        "static_contract": {
            "source": "agent3.static-element-set",
            "is_usable": bool(regression_paths) and blocked_path_count == 0 and not missing_required_elements,
            "has_executable_paths": completed_path_count > 0 and not missing_required_elements,
            "agent4_ready": validation_report["agent4_ready"] and not missing_required_elements,
            "completed_path_count": completed_path_count,
            "blocked_path_count": blocked_path_count,
            "missing_nodes": missing_nodes_all,
            "missing_transition_actions": missing_transition_actions_all,
            "missing_required_elements": missing_required_elements,
            "targeted_probe_request_count": targeted_probe_plan["summary"]["request_count"],
            "missing_report": missing_report,
            "selector_override_priority": [dict(item) for item in _SELECTOR_OVERRIDE_PRIORITY],
            "knowledge_assist": knowledge_assist,
            "requires_targeted_probe": bool(
                missing_nodes_all
                or missing_required_elements
                or missing_transition_actions_all
                or targeted_probe_plan["summary"]["request_count"]
            ),
        },
    }
    page_registry["static_contract"]["is_usable"] = bool(regression_paths) and not (
        missing_nodes_all or missing_required_elements or missing_transition_actions_all
    )
    page_registry["static_contract"]["has_executable_paths"] = completed_path_count > 0 and not missing_required_elements
    page_registry["static_contract"]["agent4_ready"] = bool(regression_paths) and validation_report["agent4_ready"] and not (
        missing_nodes_all or missing_required_elements or missing_transition_actions_all
    )
    return {
        "page_registry": page_registry,
        "explore_trace": {
            "product_id": product_id,
            "mode": "static-first",
            "generated_by": "agent3.static-first",
            "visited_urls": [],
            "discovered_page_count": len(records),
            "exploration_contract": contract,
            "static_contract": page_registry["static_contract"],
            "targeted_probe_plan": targeted_probe_plan,
            "missing_report": missing_report,
            "knowledge_hints": knowledge_assist,
            "warnings": [],
            "timestamp": datetime.now(UTC).isoformat(),
        },
        "element_set_summary": {
            "source": "agent3.static-element-set",
            **dict(element_set.get("summary", {}) or {}),
            "page_model_count": len((element_set.get("page_models", {}) or {})),
            "static_page_record_count": len(records),
            "missing_node_count": len(missing_nodes_all),
            "missing_required_element_count": len(missing_required_elements),
            "targeted_probe_request_count": targeted_probe_plan["summary"]["request_count"],
        },
        "warnings": [],
    }

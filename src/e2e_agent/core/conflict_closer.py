"""Render conflict resolution suggestions for R1 review."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

SUPPORTED_POLICIES = (
    "temporary-override",
    "update-human-case",
    "update-prd",
    "update-knowledge-base",
    "needs-product-confirmation",
)

_TYPE_POLICY = {
    "assertion_mismatch": "needs-product-confirmation",
    "step_contradiction": "temporary-override",
    "precondition_conflict": "needs-product-confirmation",
    "scope_overlap": "update-human-case",
    "missing_coverage": "update-human-case",
}

_POLICY_DETAILS = {
    "temporary-override": {
        "owner": "qa-lead",
        "suggestion": "本轮同时保留两侧证据，并将合并用例标记为需要人工临时复核。",
        "reason": "该冲突影响执行顺序，但可以在不修改源资产的情况下先隔离处理。",
    },
    "update-human-case": {
        "owner": "qa-owner",
        "suggestion": "更新人工用例标题、步骤或覆盖范围，使其与当前 PRD 推导出的回归意图一致。",
        "reason": "PRD 推导用例可用于回归，但现有人工用例可能过期或覆盖范围过宽。",
    },
    "update-prd": {
        "owner": "product-owner",
        "suggestion": "先补充或澄清 PRD 验收标准，再把该路径作为稳定回归契约。",
        "reason": "人工证据比 PRD 描述更具体，可能暴露产品需求缺口。",
    },
    "update-knowledge-base": {
        "owner": "domain-knowledge-owner",
        "suggestion": "新增或修订领域合并规则，让后续相似用例可以稳定归类。",
        "reason": "该冲突更像可复用的决策规则，而不是一次性的用例修正。",
    },
    "needs-product-confirmation": {
        "owner": "product-owner",
        "suggestion": "请产品或资深 QA 确认预期业务行为，再在 R1 决定通过或驳回合并结果。",
        "reason": "该冲突会改变预期业务行为，不应自动判定。",
    },
}


def _policy_for_conflict(conflict: Mapping[str, Any]) -> str:
    explicit_policy = str(conflict.get("policy") or "")
    if explicit_policy in SUPPORTED_POLICIES:
        return explicit_policy
    conflict_type = str(conflict.get("type") or "")
    return _TYPE_POLICY.get(conflict_type, "needs-product-confirmation")


def _evidence(
    conflict: Mapping[str, Any],
    manual_case: Mapping[str, Any] | None,
    ac_case: Mapping[str, Any] | None,
) -> dict[str, Any]:
    evidence = {
        "conflict_id": conflict.get("conflict_id"),
        "type": conflict.get("type"),
        "manual_case_id": conflict.get("manual_case_id"),
        "ac_case_id": conflict.get("ac_case_id"),
        "description": conflict.get("description"),
    }
    if manual_case:
        evidence["manual_case"] = {
            "case_id": manual_case.get("case_id"),
            "title": manual_case.get("title"),
            "assertions": list(manual_case.get("assertions", []) or []),
            "steps": list(manual_case.get("steps", []) or []),
        }
    if ac_case:
        evidence["ac_case"] = {
            "case_id": ac_case.get("case_id"),
            "title": ac_case.get("title"),
            "assertions": list(ac_case.get("assertions", []) or []),
            "steps": list(ac_case.get("steps", []) or []),
        }
    return evidence


def close_conflict(
    conflict: Mapping[str, Any],
    *,
    manual_case: Mapping[str, Any] | None = None,
    ac_case: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a conflict enriched with review-only resolution guidance."""
    policy = _policy_for_conflict(conflict)
    details = _POLICY_DETAILS[policy]
    return {
        **dict(conflict),
        "policy": policy,
        "suggestion": str(details["suggestion"]),
        "reason": str(details["reason"]),
        "evidence": _evidence(conflict, manual_case, ac_case),
        "owner": str(details["owner"]),
        "resolution_note": conflict.get("resolution_note"),
        "resolved": bool(conflict.get("resolved", False)),
    }


def render_conflict_closures(
    conflicts: Sequence[Mapping[str, Any]],
    *,
    cases_by_id: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Render R1 suggestions for a list of merge conflicts."""
    case_index = cases_by_id or {}
    rendered: list[dict[str, Any]] = []
    for conflict in conflicts:
        manual_case_id = str(conflict.get("manual_case_id") or "")
        ac_case_id = str(conflict.get("ac_case_id") or "")
        rendered.append(
            close_conflict(
                conflict,
                manual_case=case_index.get(manual_case_id),
                ac_case=case_index.get(ac_case_id),
            )
        )
    return rendered


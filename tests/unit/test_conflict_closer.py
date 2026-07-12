from __future__ import annotations


def _base_conflict(policy: str | None = None) -> dict:
    conflict = {
        "conflict_id": "CONFLICT-001",
        "type": "assertion_mismatch",
        "manual_case_id": "MANUAL-001",
        "ac_case_id": "AC-001",
        "description": "Manual case expects success while PRD AC expects product confirmation.",
        "resolution_note": None,
        "resolved": False,
    }
    if policy:
        conflict["policy"] = policy
    return conflict


def test_close_conflict_renders_r1_review_material() -> None:
    from e2e_agent.core.conflict_closer import close_conflict

    result = close_conflict(_base_conflict())

    assert result["policy"] == "needs-product-confirmation"
    assert result["owner"] == "product-owner"
    assert result["suggestion"]
    assert result["reason"]
    assert result["evidence"]["manual_case_id"] == "MANUAL-001"
    assert result["evidence"]["ac_case_id"] == "AC-001"
    assert result["resolved"] is False


def test_close_conflict_supports_all_w4_policies() -> None:
    from e2e_agent.core.conflict_closer import SUPPORTED_POLICIES, close_conflict

    rendered = [
        close_conflict(_base_conflict(policy=policy))
        for policy in SUPPORTED_POLICIES
    ]

    assert [item["policy"] for item in rendered] == list(SUPPORTED_POLICIES)
    assert all(item["suggestion"] for item in rendered)
    assert all(item["owner"] for item in rendered)


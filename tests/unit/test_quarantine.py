from __future__ import annotations

import pytest


def test_build_quarantine_report_groups_blocking_failures() -> None:
    from e2e_agent.core.quarantine import build_quarantine_report

    report = build_quarantine_report(
        [
            {
                "case_id": "TC-001",
                "path_id": "PATH-ORDER",
                "status": "failed",
                "failure_category": "product_bug",
                "error_message": "order status mismatch",
                "screenshot_path": "trace/order.png",
            },
            {
                "case_id": "TC-002",
                "path_id": "PATH-SMOKE",
                "status": "passed",
            },
            {
                "case_id": "TC-003",
                "path_id": "PATH-SCRIPT",
                "status": "error",
                "failure_category": "script_bug",
                "error_message": "selector changed",
            },
        ],
        product_id="demo-product",
        run_id="run-001",
    )

    assert report["summary"] == {
        "total": 2,
        "blocking": 1,
        "by_category": {"product_bug": 1, "script_bug": 1},
        "by_status": {"new": 2},
    }
    assert report["items"][0]["quarantine_id"] == "QR-run-001-001"
    assert report["items"][0]["release_blocker"] is True
    assert report["items"][0]["evidence"]["screenshot_path"] == "trace/order.png"
    assert report["items"][1]["release_blocker"] is False


def test_transition_quarantine_item_enforces_state_machine() -> None:
    from e2e_agent.core.quarantine import transition_quarantine_item

    item = {
        "quarantine_id": "QR-1",
        "status": "new",
        "history": [],
    }

    assigned = transition_quarantine_item(item, "assigned", actor="qa", note="triage")
    assert assigned["status"] == "assigned"
    assert assigned["history"][0]["from_status"] == "new"
    assert assigned["history"][0]["to_status"] == "assigned"

    with pytest.raises(ValueError, match="Invalid quarantine transition"):
        transition_quarantine_item(assigned, "new")

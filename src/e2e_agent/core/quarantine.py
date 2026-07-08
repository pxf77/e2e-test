"""Quarantine report helpers for failed Agent4 executions."""
from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any, Mapping


QUARANTINE_STATUSES = (
    "new",
    "observing",
    "assigned",
    "fixed_waiting_verify",
    "closed",
)

_ALLOWED_TRANSITIONS = {
    "new": {"observing", "assigned", "closed"},
    "observing": {"assigned", "closed"},
    "assigned": {"fixed_waiting_verify", "observing", "closed"},
    "fixed_waiting_verify": {"closed", "assigned"},
    "closed": set(),
}

_RELEASE_BLOCKING_CATEGORIES = {
    "product_bug",
    "test_data",
    "agent3_contract_blocked",
    "agent3_script_blocked",
}


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _failure_category(result: Mapping[str, Any]) -> str:
    category = _text(result.get("failure_category"))
    return category or "unknown"


def _is_failed_execution(result: Mapping[str, Any]) -> bool:
    return _text(result.get("status")) in {"failed", "error", "skipped"}


def _release_blocker(result: Mapping[str, Any]) -> bool:
    return _failure_category(result) in _RELEASE_BLOCKING_CATEGORIES


def _evidence(result: Mapping[str, Any]) -> dict[str, Any]:
    evidence_keys = (
        "error_message",
        "blocked_reason",
        "screenshot_path",
        "trace_path",
        "video_path",
        "execution_artifacts_dir",
        "final_url",
        "target_node",
        "reached_target_node",
    )
    return {
        key: result.get(key)
        for key in evidence_keys
        if result.get(key) not in (None, "")
    }


def build_quarantine_item(
    result: Mapping[str, Any],
    *,
    product_id: str,
    run_id: str,
    sequence: int,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Build a triage item from one failed Agent4 result."""
    timestamp = created_at or _utc_now()
    return {
        "quarantine_id": f"QR-{run_id}-{sequence:03d}",
        "product_id": product_id,
        "run_id": run_id,
        "case_id": _text(result.get("case_id")),
        "path_id": _text(result.get("path_id")),
        "status": "new",
        "execution_status": _text(result.get("execution_status")) or _text(result.get("status")),
        "failure_category": _failure_category(result),
        "release_blocker": _release_blocker(result),
        "owner": None,
        "created_at": timestamp,
        "updated_at": timestamp,
        "evidence": _evidence(result),
        "history": [],
    }


def summarize_quarantine_items(items: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Summarize quarantine pressure for Gate and report consumption."""
    by_category: dict[str, int] = {}
    by_status: dict[str, int] = {}
    blocking = 0
    for item in items:
        category = _text(item.get("failure_category")) or "unknown"
        status = _text(item.get("status")) or "new"
        by_category[category] = by_category.get(category, 0) + 1
        by_status[status] = by_status.get(status, 0) + 1
        if bool(item.get("release_blocker")):
            blocking += 1
    return {
        "total": len(items),
        "blocking": blocking,
        "by_category": dict(sorted(by_category.items())),
        "by_status": dict(sorted(by_status.items())),
    }


def build_quarantine_report(
    results: list[Mapping[str, Any]],
    *,
    product_id: str,
    run_id: str,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build a quarantine report from Agent4 execution results."""
    timestamp = generated_at or _utc_now()
    items = [
        build_quarantine_item(
            result,
            product_id=product_id,
            run_id=run_id,
            sequence=index,
            created_at=timestamp,
        )
        for index, result in enumerate(
            [item for item in results if _is_failed_execution(item)],
            start=1,
        )
    ]
    return {
        "version": "1.0",
        "product_id": product_id,
        "run_id": run_id,
        "generated_at": timestamp,
        "summary": summarize_quarantine_items(items),
        "items": items,
    }


def transition_quarantine_item(
    item: Mapping[str, Any],
    new_status: str,
    *,
    actor: str = "",
    note: str = "",
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Return a copy of a quarantine item after validating a state transition."""
    current = _text(item.get("status")) or "new"
    target = _text(new_status)
    if current not in _ALLOWED_TRANSITIONS or target not in QUARANTINE_STATUSES:
        raise ValueError(f"Invalid quarantine status: {current} -> {target}")
    if target not in _ALLOWED_TRANSITIONS[current]:
        raise ValueError(f"Invalid quarantine transition: {current} -> {target}")

    updated = deepcopy(dict(item))
    changed_at = timestamp or _utc_now()
    updated["status"] = target
    updated["updated_at"] = changed_at
    history = list(updated.get("history", []) or [])
    history.append(
        {
            "from_status": current,
            "to_status": target,
            "actor": actor,
            "note": note,
            "timestamp": changed_at,
        }
    )
    updated["history"] = history
    return updated

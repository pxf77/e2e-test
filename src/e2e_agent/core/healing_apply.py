"""Apply advisory healing events with audit evidence."""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_SAFE_EVENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _events_payload_path(run_dir: Path) -> Path:
    agent4_result = run_dir / "agent4-result.json"
    if agent4_result.exists():
        return agent4_result
    events = run_dir / "healing-events.json"
    if events.exists():
        return events
    raise FileNotFoundError(f"Healing events not found under run dir: {run_dir}")


def _extract_events(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [dict(item) for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        return [dict(item) for item in payload.get("healing_events", []) or [] if isinstance(item, dict)]
    return []


def _replace_events(payload: Any, events: list[dict[str, Any]]) -> Any:
    if isinstance(payload, list):
        return events
    updated = dict(payload) if isinstance(payload, dict) else {}
    updated["healing_events"] = events
    return updated


def _safe_event_file_stem(event_id: str) -> str:
    if Path(event_id).name != event_id or any(token in event_id for token in ("/", "\\")):
        raise ValueError(f"Unsafe event id: {event_id}")
    if not _SAFE_EVENT_ID_PATTERN.fullmatch(event_id):
        raise ValueError(f"Unsafe event id: {event_id}")
    return event_id


def apply_healing_event(
    run_dir: str | Path,
    event_id: str,
    *,
    evidence_file: str | Path,
    operator: str = "manual",
) -> dict[str, Any]:
    """Mark one advisory healing event as applied and persist an audit record."""
    resolved_run_dir = Path(run_dir)
    resolved_evidence = Path(evidence_file)
    safe_event_id = _safe_event_file_stem(event_id)
    if not resolved_evidence.exists():
        raise FileNotFoundError(f"Evidence file not found: {resolved_evidence}")

    payload_path = _events_payload_path(resolved_run_dir)
    payload = _read_json(payload_path)
    events = _extract_events(payload)
    target_index = next((index for index, item in enumerate(events) if item.get("event_id") == event_id), -1)
    if target_index < 0:
        raise ValueError(f"Healing event not found: {event_id}")

    timestamp = _utc_now()
    target = dict(events[target_index])
    patch_dir = resolved_run_dir / "agent4" / "healing-applied"
    patch_dir.mkdir(parents=True, exist_ok=True)
    patch_path = patch_dir / f"{safe_event_id}.patch"
    suggestion = target.get("suggestion", {}) if isinstance(target.get("suggestion"), dict) else {}
    patch_path.write_text(str(suggestion.get("code_diff") or ""), encoding="utf-8")

    audit = {
        "run_id": resolved_run_dir.name,
        "event_id": event_id,
        "operator": operator,
        "applied_at": timestamp,
        "evidence_file": str(resolved_evidence),
        "patch_path": str(patch_path),
        "suggestion": suggestion,
    }
    audit_path = patch_dir / f"{safe_event_id}.audit.json"
    _write_json(audit_path, audit)

    target.update(
        {
            "applied": True,
            "applied_by": operator,
            "applied_at": timestamp,
            "evidence_file": str(resolved_evidence),
            "patch_path": str(patch_path),
            "audit_path": str(audit_path),
        }
    )
    events[target_index] = target
    _write_json(payload_path, _replace_events(payload, events))

    return audit | {"audit_path": str(audit_path)}

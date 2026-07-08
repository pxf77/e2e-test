"""Offline model acceptance report helpers."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Mapping


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _normalise_candidate(candidate: Mapping[str, Any]) -> dict[str, Any]:
    usage = candidate.get("token_usage") if isinstance(candidate.get("token_usage"), Mapping) else {}
    return {
        "provider": str(candidate.get("provider") or "unknown"),
        "route_key": str(candidate.get("route_key") or ""),
        "model_routed": str(candidate.get("model_routed") or ""),
        "status": str(candidate.get("status") or "unknown"),
        "cost_usd": _number(candidate.get("cost_usd")),
        "token_usage": dict(usage),
        "failure_reason": str(candidate.get("failure_reason") or ""),
    }


def _normalise_fallback_attempt(attempt: Mapping[str, Any]) -> dict[str, Any]:
    primary = str(attempt.get("model_primary") or "")
    routed = str(attempt.get("model_routed") or "")
    is_fallback = bool(attempt.get("is_fallback")) or bool(primary and routed and primary != routed)
    return {
        "case_id": str(attempt.get("case_id") or ""),
        "route_key": str(attempt.get("route_key") or ""),
        "model_primary": primary,
        "model_routed": routed,
        "status": str(attempt.get("status") or "unknown"),
        "is_fallback": is_fallback,
        "pipeline_continued": bool(attempt.get("pipeline_continued")),
        "artifact_recorded": bool(attempt.get("artifact_recorded")),
        "failure_reason": str(attempt.get("failure_reason") or ""),
    }


def build_fallback_acceptance_evidence(
    *,
    run_id: str,
    product_id: str,
    route_key: str,
    attempts: list[Mapping[str, Any]],
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Summarise mocked primary-to-fallback acceptance attempts."""
    normalised = [_normalise_fallback_attempt(item) for item in attempts]
    fallback_attempts = [item for item in normalised if item["is_fallback"]]
    fallback_successes = [
        item
        for item in fallback_attempts
        if item["status"] == "passed" and item["pipeline_continued"] is True
    ]
    return {
        "version": "1.0",
        "run_id": run_id,
        "product_id": product_id,
        "route_key": route_key,
        "generated_at": generated_at or _utc_now(),
        "summary": {
            "total": len(normalised),
            "fallback_attempts": len(fallback_attempts),
            "fallback_successes": len(fallback_successes),
            "fallback_success_rate": (len(fallback_successes) / len(fallback_attempts))
            if fallback_attempts
            else None,
            "pipeline_interrupted_count": sum(1 for item in normalised if not item["pipeline_continued"]),
            "artifact_recorded_count": sum(1 for item in normalised if item["artifact_recorded"]),
            "failure_reasons": sorted(
                {item["failure_reason"] for item in normalised if item["failure_reason"]}
            ),
        },
        "attempts": normalised,
    }


def build_model_acceptance_report(
    *,
    run_id: str,
    product_id: str,
    candidates: list[Mapping[str, Any]],
    fallback_evidence: Mapping[str, Any] | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Summarise offline cross-provider acceptance evidence."""
    normalised = [_normalise_candidate(item) for item in candidates]
    for item in normalised:
        item["run_id"] = run_id
        item["product_id"] = product_id
    report = {
        "version": "1.0",
        "run_id": run_id,
        "product_id": product_id,
        "generated_at": generated_at or _utc_now(),
        "summary": {
            "total": len(normalised),
            "passed": sum(1 for item in normalised if item["status"] == "passed"),
            "failed": sum(1 for item in normalised if item["status"] != "passed"),
            "total_cost_usd": round(sum(_number(item.get("cost_usd")) for item in normalised), 8),
            "total_tokens": sum(_int((item.get("token_usage") or {}).get("total_tokens")) for item in normalised),
        },
        "candidates": normalised,
    }
    if fallback_evidence is not None:
        report["fallback_evidence"] = dict(fallback_evidence)
    return report

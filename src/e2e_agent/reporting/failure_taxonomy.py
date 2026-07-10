from __future__ import annotations

from typing import Any

KNOWN_CATEGORIES = {
    "locator_broken",
    "assertion_failed",
    "business_rule_failed",
    "test_data_invalid",
    "environment_unavailable",
    "auth_failed",
    "network_error",
    "third_party_unavailable",
    "runner_error",
    "llm_generation_error",
    "contract_validation_error",
    "unknown",
}


def normalize_failure(failure: dict[str, Any]) -> dict[str, Any]:
    result = dict(failure)
    category = str(result.get("category") or "").strip()
    message = str(result.get("message") or result.get("error") or "")
    if category not in KNOWN_CATEGORIES:
        category = classify_message(message)
    result["category"] = category
    result.setdefault("id", str(result.get("scenario_id") or "failure"))
    result.setdefault("message", message or category)
    result.setdefault("retryable", category in {"network_error", "environment_unavailable", "third_party_unavailable"})
    return result


def classify_message(message: str) -> str:
    text = message.lower()
    if any(token in text for token in ("locator", "selector", "element not found")):
        return "locator_broken"
    if any(token in text for token in ("assert", "expected", "mismatch")):
        return "assertion_failed"
    if any(token in text for token in ("unauthorized", "forbidden", "login", "authentication")):
        return "auth_failed"
    if any(token in text for token in ("timeout", "connection", "network", "dns")):
        return "network_error"
    if any(token in text for token in ("schema", "contract", "validation")):
        return "contract_validation_error"
    if any(token in text for token in ("fixture", "test data", "seed")):
        return "test_data_invalid"
    if any(token in text for token in ("environment", "service unavailable", "not available")):
        return "environment_unavailable"
    return "unknown"

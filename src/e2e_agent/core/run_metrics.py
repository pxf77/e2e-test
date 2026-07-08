"""Run-level evaluation and cost summaries."""
from __future__ import annotations

from typing import Any, Mapping


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


def _ratio(numerator: int | float, denominator: int | float) -> float | None:
    return (numerator / denominator) if denominator else None


def build_cost_summary(fingerprints: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate routed model usage from artifact fingerprints."""
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0
    total_cost = 0.0
    fallback_count = 0
    deterministic_count = 0
    by_model: dict[str, dict[str, Any]] = {}

    for item in fingerprints:
        model = str(item.get("model_routed") or "unknown")
        usage = item.get("token_usage") if isinstance(item.get("token_usage"), Mapping) else {}
        item_prompt = _int(usage.get("prompt_tokens"))
        item_completion = _int(usage.get("completion_tokens"))
        item_total = _int(usage.get("total_tokens"))
        item_cost = _number(item.get("cost_usd"))

        prompt_tokens += item_prompt
        completion_tokens += item_completion
        total_tokens += item_total
        total_cost += item_cost
        if bool(item.get("is_fallback")):
            fallback_count += 1
        if model == "deterministic-skill":
            deterministic_count += 1

        model_summary = by_model.setdefault(model, {"count": 0, "cost_usd": 0.0, "total_tokens": 0})
        model_summary["count"] += 1
        model_summary["cost_usd"] = round(float(model_summary["cost_usd"]) + item_cost, 8)
        model_summary["total_tokens"] += item_total

    return {
        "total_cost_usd": round(total_cost, 8),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "fallback_count": fallback_count,
        "deterministic_artifact_count": deterministic_count,
        "by_model": {key: by_model[key] for key in sorted(by_model)},
    }


def _model_budget_class(model: str) -> str | None:
    lowered = model.lower()
    if lowered.startswith("gemini/") or lowered.startswith("gemini"):
        return "gemini"
    if "gpt-4o-mini" in lowered:
        return "openai_mini"
    if "gpt-4o" in lowered:
        return "openai_full"
    return None


def build_w7_cost_summary(
    fingerprints: list[Mapping[str, Any]],
    *,
    budgets: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Aggregate run cost and evaluate W7 OpenAI/Gemini budget classes."""
    effective_budgets = {
        "openai_full": 2.0,
        "openai_mini": 0.5,
        "gemini": 2.0,
    }
    if budgets:
        effective_budgets.update({key: float(value) for key, value in budgets.items()})

    bucket_costs = {key: 0.0 for key in effective_budgets}
    bucket_models: dict[str, set[str]] = {key: set() for key in effective_budgets}
    for item in fingerprints:
        model = str(item.get("model_routed") or "unknown")
        budget_class = _model_budget_class(model)
        if budget_class not in bucket_costs:
            continue
        bucket_costs[budget_class] += _number(item.get("cost_usd"))
        bucket_models[budget_class].add(model)

    budget_evaluation = {}
    for key in sorted(effective_budgets):
        cost = round(bucket_costs[key], 8)
        budget = float(effective_budgets[key])
        models = sorted(bucket_models[key])
        status = "not_observed" if not models else ("passed" if cost <= budget else "exceeded")
        budget_evaluation[key] = {
            "budget_usd": budget,
            "cost_usd": cost,
            "models": models,
            "status": status,
        }

    return {
        "cost": build_cost_summary(fingerprints),
        "budget_evaluation": budget_evaluation,
    }


def _first_report(state: Mapping[str, Any]) -> Mapping[str, Any]:
    reports = state.get("reports", []) or []
    return reports[0] if reports and isinstance(reports[0], Mapping) else {}


def _probe_summary(report: Mapping[str, Any]) -> Mapping[str, Any]:
    probes = report.get("side_effect_probes", {}) if isinstance(report.get("side_effect_probes"), Mapping) else {}
    summary = probes.get("summary", {}) if isinstance(probes.get("summary"), Mapping) else {}
    return summary


def _healing_precision(events: list[Any]) -> float | None:
    applied = [item for item in events if isinstance(item, Mapping) and item.get("applied") is True]
    if not applied:
        return None
    effective = [item for item in applied if item.get("verified_effective") is True]
    return len(effective) / len(applied)


def build_evaluation_metrics(state: Mapping[str, Any]) -> dict[str, Any]:
    """Build run metrics from currently available deterministic evidence."""
    report = _first_report(state)
    report_summary = report.get("summary", {}) if isinstance(report.get("summary"), Mapping) else {}
    total = _int(report_summary.get("total"))
    failed = _int(report_summary.get("failed"))
    passed = _int(report_summary.get("passed"))
    errors = _int(report_summary.get("error"))
    skipped = _int(report_summary.get("skipped"))

    assertion_summary = state.get("assertion_template_summary", {})
    if not isinstance(assertion_summary, Mapping):
        assertion_summary = {}
    template_rate = assertion_summary.get("template_coverage_rate")

    probe_summary = _probe_summary(report)
    probe_total = _int(probe_summary.get("total"))
    probe_na = _int(probe_summary.get("na"))
    available_probes = probe_total - probe_na

    quarantine_summary = state.get("quarantine_report", {})
    if isinstance(quarantine_summary, Mapping):
        quarantine_summary = quarantine_summary.get("summary", {})
    if not isinstance(quarantine_summary, Mapping):
        quarantine_summary = {}

    return {
        "sample_count": total,
        "execution_pass_rate": _ratio(passed, total),
        "defect_detection_rate": _ratio(failed + errors, total),
        "false_green_rate": state.get("false_green_rate"),
        "self_healing_precision": _healing_precision(list(state.get("healing_events", []) or [])),
        "manual_review_hours": state.get("manual_review_hours"),
        "template_coverage_rate": float(template_rate) if template_rate is not None else None,
        "probe_availability_rate": _ratio(available_probes, probe_total),
        "quarantine_total": _int(quarantine_summary.get("total")),
        "quarantine_blocking": _int(quarantine_summary.get("blocking")),
        "skipped_rate": _ratio(skipped, total),
    }

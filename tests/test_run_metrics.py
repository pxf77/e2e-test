from __future__ import annotations


def test_build_cost_summary_aggregates_tokens_cost_and_fallbacks() -> None:
    from e2e_agent.core.run_metrics import build_cost_summary

    summary = build_cost_summary(
        [
            {
                "model_routed": "gpt-4o",
                "is_fallback": False,
                "token_usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                "cost_usd": 0.03,
            },
            {
                "model_routed": "gemini/gemini-1.5-pro",
                "is_fallback": True,
                "token_usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
                "cost_usd": 0.02,
            },
            {
                "model_routed": "deterministic-skill",
                "is_fallback": False,
                "token_usage": None,
                "cost_usd": None,
            },
        ]
    )

    assert summary == {
        "total_cost_usd": 0.05,
        "prompt_tokens": 17,
        "completion_tokens": 8,
        "total_tokens": 25,
        "fallback_count": 1,
        "deterministic_artifact_count": 1,
        "by_model": {
            "deterministic-skill": {"count": 1, "cost_usd": 0.0, "total_tokens": 0},
            "gemini/gemini-1.5-pro": {"count": 1, "cost_usd": 0.02, "total_tokens": 10},
            "gpt-4o": {"count": 1, "cost_usd": 0.03, "total_tokens": 15},
        },
    }


def test_build_w7_cost_summary_evaluates_model_budget_classes() -> None:
    from e2e_agent.core.run_metrics import build_w7_cost_summary

    summary = build_w7_cost_summary(
        [
            {
                "model_routed": "gpt-4o",
                "token_usage": {"total_tokens": 100},
                "cost_usd": 1.25,
            },
            {
                "model_routed": "gpt-4o-mini",
                "token_usage": {"total_tokens": 200},
                "cost_usd": 0.51,
            },
            {
                "model_routed": "gemini/gemini-1.5-pro",
                "token_usage": {"total_tokens": 300},
                "cost_usd": 1.75,
            },
            {
                "model_routed": "rule-based-fallback",
                "token_usage": None,
                "cost_usd": None,
            },
        ],
        budgets={"openai_full": 2.0, "openai_mini": 0.5, "gemini": 2.0},
    )

    assert summary["budget_evaluation"] == {
        "gemini": {
            "budget_usd": 2.0,
            "cost_usd": 1.75,
            "models": ["gemini/gemini-1.5-pro"],
            "status": "passed",
        },
        "openai_full": {
            "budget_usd": 2.0,
            "cost_usd": 1.25,
            "models": ["gpt-4o"],
            "status": "passed",
        },
        "openai_mini": {
            "budget_usd": 0.5,
            "cost_usd": 0.51,
            "models": ["gpt-4o-mini"],
            "status": "exceeded",
        },
    }
    assert summary["cost"]["total_cost_usd"] == 3.51


def test_build_w7_cost_summary_marks_missing_model_classes_not_observed() -> None:
    from e2e_agent.core.run_metrics import build_w7_cost_summary

    summary = build_w7_cost_summary(
        [
            {
                "model_routed": "deterministic-skill",
                "token_usage": None,
                "cost_usd": None,
            }
        ]
    )

    assert summary["budget_evaluation"]["openai_full"]["status"] == "not_observed"
    assert summary["budget_evaluation"]["openai_mini"]["status"] == "not_observed"
    assert summary["budget_evaluation"]["gemini"]["status"] == "not_observed"


def test_build_evaluation_metrics_uses_available_agent_outputs() -> None:
    from e2e_agent.core.run_metrics import build_evaluation_metrics

    metrics = build_evaluation_metrics(
        {
            "reports": [
                {
                    "summary": {"total": 4, "passed": 2, "failed": 1, "skipped": 1, "error": 0},
                    "side_effect_probes": {"summary": {"total": 3, "success": 1, "fail": 1, "na": 1}},
                }
            ],
            "quarantine_report": {
                "summary": {
                    "total": 2,
                    "blocking": 1,
                    "by_category": {"product_bug": 1, "script_bug": 1},
                    "by_status": {"new": 2},
                }
            },
            "assertion_template_summary": {
                "total_assertion_count": 4,
                "matched_template_count": 3,
                "template_coverage_rate": 0.75,
            },
            "healing_events": [
                {"applied": True, "verified_effective": True},
                {"applied": True, "verified_effective": False},
                {"applied": False},
            ],
        }
    )

    assert metrics["execution_pass_rate"] == 0.5
    assert metrics["defect_detection_rate"] == 0.25
    assert metrics["false_green_rate"] is None
    assert metrics["self_healing_precision"] == 0.5
    assert metrics["template_coverage_rate"] == 0.75
    assert metrics["probe_availability_rate"] == 2 / 3
    assert metrics["manual_review_hours"] is None

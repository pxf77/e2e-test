from __future__ import annotations

import json
from pathlib import Path


def test_build_model_acceptance_report_records_route_and_cost() -> None:
    from e2e_agent.core.model_acceptance import (
        build_fallback_acceptance_evidence,
        build_model_acceptance_report,
    )

    report = build_model_acceptance_report(
        run_id="run-001",
        product_id="demo-product",
        candidates=[
            {
                "provider": "openai",
                "model_routed": "gpt-4o",
                "route_key": "exec_healing",
                "status": "passed",
                "cost_usd": 0.01,
                "token_usage": {"total_tokens": 12},
            },
            {
                "provider": "google",
                "model_routed": "gemini/gemini-1.5-pro",
                "route_key": "exec_healing",
                "status": "failed",
                "cost_usd": 0.02,
                "token_usage": {"total_tokens": 20},
            },
        ],
        fallback_evidence=build_fallback_acceptance_evidence(
            run_id="run-001",
            product_id="demo-product",
            route_key="exec_healing",
            attempts=[
                {
                    "case_id": "PATH-001",
                    "model_primary": "gpt-4o-mini",
                    "model_routed": "gemini/gemini-1.5-pro",
                    "status": "passed",
                    "failure_reason": "openai_rate_limited",
                    "pipeline_continued": True,
                    "artifact_recorded": True,
                },
                {
                    "case_id": "PATH-002",
                    "model_primary": "gpt-4o-mini",
                    "model_routed": "gemini/gemini-1.5-pro",
                    "status": "failed",
                    "failure_reason": "gemini_timeout",
                    "pipeline_continued": False,
                    "artifact_recorded": True,
                },
            ],
        ),
    )

    assert report["summary"] == {
        "total": 2,
        "passed": 1,
        "failed": 1,
        "total_cost_usd": 0.03,
        "total_tokens": 32,
    }
    assert report["candidates"][0]["provider"] == "openai"
    assert report["candidates"][0]["run_id"] == "run-001"
    assert report["candidates"][0]["product_id"] == "demo-product"
    assert report["fallback_evidence"]["summary"] == {
        "total": 2,
        "fallback_attempts": 2,
        "fallback_successes": 1,
        "fallback_success_rate": 0.5,
        "pipeline_interrupted_count": 1,
        "artifact_recorded_count": 2,
        "failure_reasons": ["gemini_timeout", "openai_rate_limited"],
    }
    assert report["fallback_evidence"]["attempts"][1]["failure_reason"] == "gemini_timeout"


def test_model_acceptance_cli_writes_report(tmp_path: Path, capsys) -> None:
    from tools.diagnostics import model_acceptance as model_acceptance_harness

    candidates_path = tmp_path / "candidates.json"
    candidates_path.write_text(
        json.dumps(
            [
                {
                    "provider": "openai",
                    "model_routed": "gpt-4o",
                    "route_key": "explore",
                    "status": "passed",
                    "cost_usd": 0.01,
                    "token_usage": {"total_tokens": 7},
                }
            ]
        ),
        encoding="utf-8",
    )
    fallback_path = tmp_path / "fallback-attempts.json"
    fallback_path.write_text(
        json.dumps(
            [
                {
                    "case_id": "PATH-001",
                    "model_primary": "gpt-4o",
                    "model_routed": "gemini/gemini-1.5-pro",
                    "status": "passed",
                    "failure_reason": "openai_api_key_missing",
                    "pipeline_continued": True,
                    "artifact_recorded": True,
                }
            ]
        ),
        encoding="utf-8",
    )
    output_path = tmp_path / "report.json"

    exit_code = model_acceptance_harness.main(
        [
            "--run-id",
            "run-001",
            "--product-id",
            "demo-product",
            "--candidates",
            str(candidates_path),
            "--fallback-attempts",
            str(fallback_path),
            "--output",
            str(output_path),
        ]
    )

    assert exit_code == 0
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["summary"]["passed"] == 1
    assert report["fallback_evidence"]["summary"]["fallback_success_rate"] == 1.0
    assert json.loads(capsys.readouterr().out)["output"] == str(output_path)

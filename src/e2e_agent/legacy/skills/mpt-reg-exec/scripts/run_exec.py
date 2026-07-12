from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

REPO_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "pyproject.toml").exists())
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from e2e_agent.legacy.agents.agent4_exec.node import (
    _assertion_template_index,
    _attach_agent3_formal_chain_specs,
    _build_summary,
    _build_suggestion,
    _normalise_execution_result,
    _playwright_python_available,
    _utc_now,
)
from e2e_agent.legacy.browser.runner import PlaywrightTSRunner


async def _run(payload: dict) -> dict:
    root_dir = Path(str(payload.get("root_dir") or REPO_ROOT))
    assertion_template_source = str(
        payload.get("assertion_template_source") or "config/assertion-templates.yaml"
    )
    warnings: list[str] = []
    runner = PlaywrightTSRunner(root_dir)
    if not runner.check_node_available() and not _playwright_python_available():
        warnings.append("No Playwright CLI or Python runtime available for scenario execution")

    product_id = str(payload.get("product_id") or "product")
    run_id = str(payload.get("run_id") or "run-unknown")
    scenarios = _attach_agent3_formal_chain_specs(
        list(payload.get("scenarios", []) or []),
        product_id=product_id,
        root_dir=root_dir,
        run_dir=payload.get("run_dir"),
    )
    if not scenarios:
        warnings.append("No scenarios available for execution")

    governance_summary = payload.get("governance_summary") or {}
    runtime_context = payload.get("runtime_context") or {}
    assertion_template_by_case, used_templates = _assertion_template_index(
        list(payload.get("assertion_results", []) or [])
    )

    all_results: list[dict] = []
    healing_events: list[dict] = []
    event_counter = 1
    model_used = "rule-based-fallback"

    for scenario in scenarios:
        scenario = {
            **scenario,
            "root_dir": str(root_dir),
            "run_dir": payload.get("run_dir"),
            "run_id": run_id,
            "runtime_context": scenario.get("runtime_context", runtime_context),
        }
        results, healing_inputs, _ = await _normalise_execution_result(
            product_id=product_id,
            scenario=scenario,
            runner=runner,
            warnings=warnings,
            root_dir=root_dir,
        )
        for item in results:
            case_id = str(item.get("case_id") or "")
            item["assertion_template"] = assertion_template_by_case.get(case_id)
            item["assertion_template_source"] = assertion_template_source
            item["failure_category_source"] = "mpt-reg-exec.rule_classifier"
        all_results.extend(results)

        for base_event, message in healing_inputs:
            suggestion, model_name = await _build_suggestion(
                base_event["failure_category"],
                message,
            )
            if model_used == "rule-based-fallback" and model_name != "rule-based-fallback":
                model_used = model_name
            healing_events.append(
                {
                    "event_id": f"HEAL-{event_counter:03d}",
                    "case_id": base_event["case_id"],
                    "run_id": run_id,
                    "failure_category": base_event["failure_category"],
                    "error_message": base_event["error_message"],
                    "failure_category_source": "mpt-reg-exec.rule_classifier",
                    "suggestion": suggestion,
                    "applied": False,
                    "timestamp": _utc_now(),
                }
            )
            event_counter += 1

    report = {
        "run_id": run_id,
        "product_id": product_id,
        "timestamp": _utc_now(),
        "execution_entry": "mpt-reg-exec",
        "model_used": model_used,
        "assertion_template_source": assertion_template_source,
        "assertion_templates_used": used_templates,
        "failure_category_source": "mpt-reg-exec.rule_classifier",
        "governance_source": "state.governance_summary",
        "governance_warning_count": len(governance_summary.get("warnings", []) or []),
        "planned_page_key_count": int(governance_summary.get("summary", {}).get("total_page_keys", 0) or 0),
        "session_reused": bool(runtime_context.get("session_reused")),
        "summary": _build_summary(all_results),
        "results": all_results,
        "cost_usd": None,
    }
    return {
        "reports": [report],
        "healing_events": healing_events,
        "warnings": warnings,
    }


def main() -> int:
    payload = json.loads(sys.stdin.read() or "{}")
    result = asyncio.run(_run(payload))
    json.dump(result, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT_DIR = next(parent for parent in Path(__file__).resolve().parents if (parent / "pyproject.toml").exists())
SRC_ROOT = ROOT_DIR / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from e2e_agent.legacy.agents.agent1_tc_merge.node import tc_merge_node
from e2e_agent.legacy.agents.agent2_path_extract.node import path_extract_node
from e2e_agent.legacy.agents.agent3_explore.node import explore_node
from e2e_agent.legacy.agents.agent4_exec.node import exec_healing_node
from e2e_agent.legacy.agents.agent4_exec.report import generate_agent4_html_report
from e2e_agent.artifacts.paths import product_artifact_dir
from e2e_agent.core.page_exploration import _resolve_id_card_image_paths
from e2e_agent.core.policy_info_generator import generate_policy_mock_data
from e2e_agent.core.quarantine import build_quarantine_report
from e2e_agent.core.run_metrics import build_cost_summary, build_evaluation_metrics


def default_product_input_path() -> Path:
    product_root = ROOT_DIR / "products" / "test-product"
    preferred = product_root / "eman" / "product-input.json"
    if preferred.exists():
        return preferred
    candidates = sorted(
        item / "product-input.json"
        for item in product_root.iterdir()
        if item.is_dir() and (item.name.endswith(".assets") or (item / "product-input.json").exists())
    )
    if not candidates:
        raise FileNotFoundError("No product-input.json found under products/test-product source or artifact dirs")
    return candidates[0]


def resolve_product_dirs(product_input_path: Path) -> dict[str, Path]:
    product_source_dir = product_input_path.parent
    try:
        product_input = read_json(product_input_path)
        product_id = str(product_input.get("product_id") or product_source_dir.parent.name)
    except (OSError, json.JSONDecodeError):
        product_id = product_source_dir.parent.name
    artifact_dir = product_artifact_dir(
        ROOT_DIR,
        product_id,
        source_paths=[product_input_path],
    )
    return {
        "product_source_dir": product_source_dir,
        "product_artifact_dir": artifact_dir,
    }


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def read_source_product_config(product_source_dir: Path) -> dict[str, Any]:
    config_path = product_source_dir / "automation" / "product.config.json"
    if not config_path.exists():
        return {}
    try:
        payload = read_json(config_path)
    except (OSError, json.JSONDecodeError):
        return {}
    return dict(payload) if isinstance(payload, dict) else {}


def product_config_from_input(
    product_input: dict[str, Any],
    source_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    config = dict(source_config or {})
    config.update(dict(product_input.get("product_config") or {}))
    if product_input.get("agent3_mode"):
        config["agent3_mode"] = product_input["agent3_mode"]
    return config


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def materialise_id_card_preview_assets(product_dir: Path, mock_data: dict[str, Any]) -> list[Path]:
    preview_dir = product_dir / ".tmp" / "id-card-preview"
    preview_dir.mkdir(parents=True, exist_ok=True)
    resolved_paths = list(_resolve_id_card_image_paths(mock_data))
    if len(resolved_paths) == 1:
        resolved_paths = [resolved_paths[0], resolved_paths[0]]
    materialised: list[Path] = []
    for source, filename in zip(resolved_paths[:2], ("id-card-front.jpg", "id-card-back.jpg"), strict=False):
        target = preview_dir / filename
        if source.resolve() != target.resolve():
            shutil.copyfile(source, target)
        materialised.append(target)
    return materialised


def workflow_log(stage: str, message: str, details: dict[str, Any] | None = None) -> None:
    payload: dict[str, Any] = {
        "stage": stage,
        "message": message,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }
    if details:
        payload["details"] = details
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def resolve_repo_path(value: str | None) -> Path | None:
    if not value:
        return None
    candidate = Path(value)
    return candidate if candidate.is_absolute() else ROOT_DIR / candidate


def materialise_agent_outputs(product_dir: Path, state: dict[str, Any]) -> None:
    agent_payloads = {
        "agent1": {
            "prd-analysis.json": state.get("prd_analysis", {}),
            "test-case-skeleton.json": state.get("test_case_skeleton", []),
            "candidate-cases.json": state.get("candidate_cases", []),
            "merged-cases.json": state.get("merged_cases", []),
            "excluded-cases.json": state.get("excluded_cases", []),
            "conflicts.json": state.get("conflicts", []),
            "merge-trace.json": state.get("merge_trace", {}),
            "mock-data.json": state.get("mock_data", {}),
        },
        "agent2": {
            "regression-flow.json": state.get("regression_flow", {}),
            "regression-paths.json": state.get("regression_paths", []),
            "governance-summary.json": state.get("governance_summary", {}),
            "mock-data.json": state.get("mock_data", {}),
        },
        "agent3": {
            "page-registry.json": state.get("page_registry", {}),
            "explore-trace.json": state.get("explore_trace", {}),
            "runtime-context.json": state.get("runtime_context", {}),
            "page-functions.json": state.get("page_functions", []),
            "scenarios.json": state.get("agent3_scenarios_all", state.get("scenarios", [])),
            "script-plan.json": state.get("script_plan", {}),
            "script-bundle.json": state.get("script_bundle", {}),
            "script-validation.json": state.get("script_validation", {}),
            "assertion-results.json": state.get("assertion_results", []),
            "element-set.json": state.get("element_set", {}),
        },
        "agent4": {
            "reports.json": state.get("reports", []),
            "healing-events.json": state.get("healing_events", []),
            "teardown-report.json": state.get("teardown_report", {}),
            "quarantine.json": state.get("quarantine_report", {}),
        },
    }
    for agent_name, payloads in agent_payloads.items():
        for filename, payload in payloads.items():
            if filename == "quarantine.json" and not payload:
                continue
            write_json(product_dir / agent_name / filename, payload)


def blocked_paths(state: dict[str, Any]) -> list[dict[str, Any]]:
    contract = (state.get("page_registry", {}) or {}).get("exploration_contract", {}) or {}
    return [item for item in contract.get("blocked_paths", []) or [] if isinstance(item, dict)]


def _gate_payload(gate_name: str, status: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "gate": gate_name,
        "status": status,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        **(details or {}),
    }


def agent4_required_priorities(product_input: dict[str, Any]) -> set[str]:
    raw = product_input.get("agent4_required_priorities")
    if raw is None:
        return {"P0"}
    if isinstance(raw, str):
        values = [raw]
    elif isinstance(raw, list):
        values = [str(item) for item in raw]
    else:
        values = []
    priorities = {item.strip().upper() for item in values if item.strip()}
    return priorities or {"P0"}


def required_agent4_case_ids(state: dict[str, Any], required_priorities: set[str]) -> set[str]:
    ids: set[str] = set()
    for case in state.get("merged_cases", []) or []:
        if not isinstance(case, dict):
            continue
        case_id = str(case.get("case_id") or case.get("id") or "").strip()
        priority = str(case.get("priority") or "").strip().upper()
        if case_id and priority in required_priorities:
            ids.add(case_id)
    return ids


def _scenario_has_required_case(scenario: dict[str, Any], required_case_ids: set[str] | None) -> bool:
    if required_case_ids is None:
        return True
    case_ids = [str(case_id) for case_id in scenario.get("case_ids", []) or []]
    return any(case_id in required_case_ids for case_id in case_ids)


def prepare_agent4_scenarios(
    state: dict[str, Any],
    entry_url: str | None,
    blocked_path_ids: set[str] | None = None,
    required_case_ids: set[str] | None = None,
) -> dict[str, int]:
    scenarios = [dict(item) for item in state.get("scenarios", []) or [] if isinstance(item, dict)]
    blocked_ids = blocked_path_ids or set()
    runnable: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for scenario in scenarios:
        path_id = str(scenario.get("path_id") or scenario.get("scenario_id") or "")
        completion_rule = scenario.get("completion_rule", {}) or {}
        coverage_status = str(scenario.get("coverage_status") or "")
        is_complete = completion_rule.get("is_complete") is True
        is_blocked = bool(
            path_id in blocked_ids
            or scenario.get("blocked_node")
            or scenario.get("blocked_reason")
            or coverage_status == "coverage-gap"
        )
        if coverage_status == "covered" and is_complete and not is_blocked:
            scenario["entry_url"] = scenario.get("entry_url") or entry_url
            runnable.append(scenario)
        else:
            blocked.append(scenario)
    state["scenarios"] = runnable
    state["agent4_input_scenarios"] = runnable
    state["agent4_blocked_scenarios"] = blocked
    blocked_required = [
        scenario for scenario in blocked if _scenario_has_required_case(scenario, required_case_ids)
    ]
    return {
        "runnable": len(runnable),
        "blocked": len(blocked),
        "blocked_required": len(blocked_required),
        "blocked_non_required": len(blocked) - len(blocked_required),
    }


def report_path_count(reports: list[dict[str, Any]]) -> int:
    return int(((reports[0] if reports else {}).get("summary", {}) or {}).get("total", 0) or 0)


def build_summary(
    *,
    run_id: str,
    product_dir: Path,
    mock_data_path: Path,
    state: dict[str, Any],
    regression_paths: list[dict[str, Any]],
    final_blocked_paths: list[dict[str, Any]],
    agent1_result: dict[str, Any],
    agent2_result: dict[str, Any],
    agent3_result: dict[str, Any],
    agent4_result: dict[str, Any],
    html_report: str,
) -> dict[str, Any]:
    reports = state.get("reports", []) or []
    quarantine_summary = (state.get("quarantine_report", {}) or {}).get("summary", {}) or {}
    cost_summary = build_cost_summary(
        [item for item in state.get("artifact_fingerprints", []) or [] if isinstance(item, dict)]
    )
    evaluation_metrics = build_evaluation_metrics(state)
    return {
        "run_id": run_id,
        "product_id": state["product_id"],
        "agent1": {
            "merged_cases": len(state.get("merged_cases", []) or []),
            "conflicts": len(state.get("conflicts", []) or []),
            "error": agent1_result.get("error"),
        },
        "agent2": {
            "paths": len(regression_paths),
            "active_paths": len(state.get("regression_paths", []) or []),
            "error": agent2_result.get("error"),
        },
        "agent3": {
            "scenarios": len(state.get("agent3_scenarios_all", state.get("scenarios", [])) or []),
            "page_functions": len(state.get("page_functions", []) or []),
            "blocked_paths": len(final_blocked_paths),
            "error": agent3_result.get("error"),
        },
        "agent4": {
            "skipped": bool(agent4_result.get("skipped")),
            "input_scenarios": len(state.get("agent4_input_scenarios", state.get("scenarios", [])) or []),
            "blocked_scenarios": len(state.get("agent4_blocked_scenarios", []) or []),
            "reports": len(reports),
            "results": report_path_count(reports),
            "healing_events": len(state.get("healing_events", []) or []),
            "quarantine_total": int(quarantine_summary.get("total", 0) or 0),
            "quarantine_blocking": int(quarantine_summary.get("blocking", 0) or 0),
            "error": agent4_result.get("error"),
        },
        "outputs": {
            "run_dir": str(state["run_dir"]),
            "product_dir": str(product_dir),
            "html_report": html_report,
            "mock_data_path": str(mock_data_path),
        },
        "cost": cost_summary,
        "evaluation": evaluation_metrics,
    }


def make_blocked_agent4_result(
    final_blocked_paths: list[dict[str, Any]],
    *,
    product_id: str,
    run_id: str,
) -> dict[str, Any]:
    quarantine_inputs = [
        {
            "case_id": str(case_id),
            "path_id": str(path.get("path_id") or ""),
            "status": "skipped",
            "execution_status": "blocked_by_agent3_contract",
            "failure_category": "agent3_contract_blocked",
            "blocked_node": path.get("blocked_node"),
            "blocked_reason": path.get("blocked_reason") or "Agent3 did not complete the planned path",
            "target_node": path.get("target_node"),
        }
        for path in final_blocked_paths
        for case_id in (path.get("case_ids", []) or [path.get("path_id") or "UNKNOWN-CASE"])
    ]
    return {
        "skipped": True,
        "reports": [],
        "healing_events": [],
        "teardown_report": {},
        "quarantine_report": build_quarantine_report(
            quarantine_inputs,
            product_id=product_id,
            run_id=run_id,
        ),
        "blocked_paths": final_blocked_paths,
        "error": "Agent4 was not started because Agent3 reported blocked paths.",
    }


async def run_workflow(product_input_path: Path) -> dict[str, Any]:
    product_input = read_json(product_input_path)
    product_dirs = resolve_product_dirs(product_input_path)
    product_source_dir = product_dirs["product_source_dir"]
    product_dir = product_dirs["product_artifact_dir"]
    prd_path = resolve_repo_path(product_input.get("prd_path"))
    if prd_path is None or not prd_path.exists():
        raise FileNotFoundError(f"PRD markdown not found: {prd_path}")

    manual_cases_path = resolve_repo_path(product_input.get("manual_cases_path"))
    run_id = "agent1-agent4-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = product_dir / "runs" / run_id
    entry_url = product_input.get("entry_url")
    required_priorities = agent4_required_priorities(product_input)
    product_config = product_config_from_input(product_input, read_source_product_config(product_source_dir))

    workflow_log(
        "init",
        "Full workflow starts. Existing artifacts are regenerated.",
        {
            "run_id": run_id,
            "product_id": product_input["product_id"],
            "product_input": str(product_input_path),
            "product_source_dir": str(product_source_dir),
            "product_artifact_dir": str(product_dir),
            "run_dir": str(run_dir),
            "rules": {
                "show_every_stage": True,
                "agent3_paths": "all",
                "agent4_requires_agent3_paths": True,
                "agent4_required_priorities": sorted(required_priorities),
            },
        },
    )

    os.environ["AGENT3_RUN_DIR"] = str(run_dir)
    os.environ["AGENT4_RUN_DIR"] = str(run_dir)
    os.environ["AGENT3_SUBMIT_SCREENSHOT_DIR"] = str(run_dir / "submit-screenshots")
    os.environ.setdefault("AGENT3_HEADLESS", "1")
    os.environ.setdefault("AGENT3_PATCH_TRIAL_GENES", "1")

    mock_seed = time.time_ns()
    mock_data = generate_policy_mock_data(
        [],
        seed=mock_seed,
        preferred_bank_type=os.environ.get("AGENT3_BANK_TYPE", "ICBC"),
    )
    mock_data["policy_tool.seed"] = str(mock_seed)
    mock_data_path = product_dir / "agent1" / "mock-data.json"
    write_json(mock_data_path, mock_data)
    os.environ["AGENT3_MOCK_DATA_PATH"] = str(mock_data_path)
    id_card_preview_paths = materialise_id_card_preview_assets(product_dir, mock_data)

    state: dict[str, Any] = {
        "product_id": product_input["product_id"],
        "product_name": product_input.get("product_name"),
        "prd_path": str(prd_path),
        "manual_cases_path": str(manual_cases_path) if manual_cases_path else None,
        "entry_url": entry_url,
        "agent3_mode": product_input.get("agent3_mode"),
        "product_config": product_config,
        "run_id": run_id,
        "run_dir": str(run_dir),
        "artifact_root_dir": str(ROOT_DIR),
        "product_source_dir": str(product_source_dir),
        "product_artifact_dir": str(product_dir),
        "artifact_fingerprints": [],
        "mock_seed": mock_seed,
        "mock_data": mock_data,
        "id_card_preview_assets": [str(path) for path in id_card_preview_paths],
        "agent4_required_priorities": sorted(required_priorities),
        "error": None,
    }
    write_json(run_dir / "input-state.json", state)

    workflow_log("agent1", "Agent1 starts: PRD analysis, test-case skeleton generation, case merge.")
    agent1_result = await tc_merge_node(state)
    write_json(run_dir / "agent1-result.json", agent1_result)
    state.update(agent1_result)
    workflow_log(
        "agent1",
        "Agent1 finishes: merged test cases are regenerated.",
        {
            "merged_cases": len(state.get("merged_cases", [])),
            "candidate_cases": len(state.get("candidate_cases", [])),
            "conflicts": len(state.get("conflicts", [])),
            "error": agent1_result.get("error"),
        },
    )
    workflow_log("r1_gate", "R1 Gate approved.")
    write_json(
        run_dir / "r1-gate-result.json",
        _gate_payload(
            "r1",
            "approved",
            {
                "merged_cases": len(state.get("merged_cases", []) or []),
                "required_case_ids": sorted(required_agent4_case_ids(state, required_priorities)),
            },
        ),
    )

    workflow_log("agent2", "Agent2 starts: regression path extraction and governance summary.")
    agent2_result = await path_extract_node(state)
    write_json(run_dir / "agent2-result.json", agent2_result)
    state.update(agent2_result)
    regression_paths = [item for item in state.get("regression_paths", []) or [] if isinstance(item, dict)]
    state["regression_paths_all"] = regression_paths
    state["regression_paths"] = regression_paths
    workflow_log(
        "agent2",
        "Agent2 finishes: all regression paths are ready for Agent3 and Agent4.",
        {
            "paths": len(regression_paths),
            "path_ids": [item.get("path_id") for item in regression_paths],
            "error": agent2_result.get("error"),
        },
    )
    workflow_log("r2_gate", "R2 Gate approved.")
    write_json(
        run_dir / "r2-gate-result.json",
        _gate_payload(
            "r2",
            "approved",
            {
                "paths": len(regression_paths),
                "path_ids": [item.get("path_id") for item in regression_paths],
            },
        ),
    )

    materialise_agent_outputs(product_dir, state)

    workflow_log(
        "agent3",
        "Agent3 starts: live exploration and script generation for all regression paths.",
        {"headless": os.environ.get("AGENT3_HEADLESS")},
    )
    agent3_result = await explore_node(state)
    write_json(run_dir / "agent3-result.json", agent3_result)
    state.update(agent3_result)
    state["agent3_scenarios_all"] = [dict(item) for item in state.get("scenarios", []) or [] if isinstance(item, dict)]
    final_blocked_paths = blocked_paths(state)
    workflow_log(
        "agent3",
        "Agent3 finishes.",
        {
            "scenarios": len(state.get("scenarios", []) or []),
            "page_functions": len(state.get("page_functions", []) or []),
            "blocked_paths": len(final_blocked_paths),
            "error": agent3_result.get("error"),
        },
    )

    blocked_path_ids = {str(item.get("path_id") or "") for item in final_blocked_paths if item.get("path_id")}
    required_case_ids = required_agent4_case_ids(state, required_priorities)
    gate_result = prepare_agent4_scenarios(state, entry_url, blocked_path_ids, required_case_ids)
    r3_status = "rejected" if gate_result["blocked_required"] else "approved"
    write_json(
        run_dir / "r3-gate-result.json",
        _gate_payload(
            "r3",
            r3_status,
            gate_result
            | {
                "blocked_path_ids": sorted(blocked_path_ids),
                "required_priorities": sorted(required_priorities),
                "required_case_ids": sorted(required_case_ids),
            },
        ),
    )
    if gate_result["runnable"] == 0 or gate_result["blocked_required"]:
        workflow_log("r3_gate", "R3 Gate stopped Agent4 because required Agent3 paths were not complete.", gate_result)
        agent4_result = make_blocked_agent4_result(
            final_blocked_paths,
            product_id=str(state["product_id"]),
            run_id=run_id,
        )
        write_json(run_dir / "agent4-result.json", agent4_result)
        state.update(agent4_result)
        reports = state.get("reports", []) or []
        html_report = str(
            generate_agent4_html_report(
                run_dir=run_dir,
                product_id=str(state["product_id"]),
                run_id=run_id,
                reports=reports,
                blocked_paths=final_blocked_paths,
            )
        )
        state["html_report"] = html_report
        write_json(run_dir / "final-state.json", state)
        materialise_agent_outputs(product_dir, state)
        summary = build_summary(
            run_id=run_id,
            product_dir=product_dir,
            mock_data_path=mock_data_path,
            state=state,
            regression_paths=regression_paths,
            final_blocked_paths=final_blocked_paths,
            agent1_result=agent1_result,
            agent2_result=agent2_result,
            agent3_result=agent3_result,
            agent4_result=agent4_result,
            html_report=html_report,
        )
        write_json(run_dir / "run-summary.json", summary)
        workflow_log("done", "Full workflow finished before Agent4; no completed paths were available.", summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        return summary

    workflow_log("r3_gate", "R3 Gate approved all required completed paths; non-required blocked paths were preserved.", gate_result)
    materialise_agent_outputs(product_dir, state)

    workflow_log(
        "agent4",
        "Agent4 starts: execute generated scenarios and record healing results.",
        {
            "force_visible_browser": os.environ.get("AGENT4_FORCE_VISIBLE_BROWSER"),
            "scenarios": len(state.get("scenarios", []) or []),
        },
    )
    agent4_result = await exec_healing_node(state)
    write_json(run_dir / "agent4-result.json", agent4_result)
    state.update(agent4_result)

    reports = state.get("reports", []) or []
    html_report = str(
        generate_agent4_html_report(
            run_dir=run_dir,
            product_id=str(state["product_id"]),
            run_id=run_id,
            reports=reports,
            blocked_paths=final_blocked_paths,
        )
    )
    state["html_report"] = html_report
    write_json(run_dir / "final-state.json", state)
    materialise_agent_outputs(product_dir, state)

    summary = build_summary(
        run_id=run_id,
        product_dir=product_dir,
        mock_data_path=mock_data_path,
        state=state,
        regression_paths=regression_paths,
        final_blocked_paths=final_blocked_paths,
        agent1_result=agent1_result,
        agent2_result=agent2_result,
        agent3_result=agent3_result,
        agent4_result=agent4_result,
        html_report=html_report,
    )
    report_summary = (((state.get("reports") or [{}])[0] or {}).get("summary", {}) or {})
    r4_status = "approved" if not any(int(report_summary.get(key, 0) or 0) for key in ("failed", "skipped", "error")) else "rejected"
    write_json(
        run_dir / "r4-gate-result.json",
        _gate_payload(
            "r4",
            r4_status,
            {
                "summary": report_summary,
                "required_priorities": sorted(required_priorities),
                "required_case_ids": sorted(required_case_ids),
            },
        ),
    )
    write_json(run_dir / "run-summary.json", summary)
    workflow_log("done", "Full workflow finished.", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the full Agent1 to Agent4 E2E workflow.")
    parser.add_argument("--product-input", default=str(default_product_input_path()))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    asyncio.run(run_workflow(Path(args.product_input)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

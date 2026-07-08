"""exec_healing_agent: Execute test scenarios and suggest healing for failures.

LangGraph node responsible for:
- Running generated .spec.ts scenarios via PlaywrightTSRunner when available
- Classifying failures (env/test_data/product_bug/script_bug/flaky)
- Generating advisory healing suggestions
- Writing reports + healing_events to state

Reads:  state.scenarios, state.product_id
Writes: state.reports, state.healing_events, state.error
Gate:   R4 (informational only — auto-approved)
"""
from __future__ import annotations

import importlib.util
import inspect
import json
import os
import re
import subprocess
from html import escape
from time import perf_counter
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from e2e_agent.artifacts.fingerprint import append_artifact_fingerprint
from e2e_agent.artifacts.paths import (
    agent_artifact_dir,
    agent_artifact_path,
    product_artifact_dir,
    write_agent_json_artifact,
)
from e2e_agent.agents.agent4_exec.report import (
    _case_target_node,
    _node_reached_in_result,
    generate_agent4_html_report,
)
from e2e_agent.browser.session import BrowserSession
from e2e_agent.browser.runner import PlaywrightTSRunner
from e2e_agent.core.runtime_context import finalize_runtime_context
from e2e_agent.core.quarantine import build_quarantine_report
from e2e_agent.core.side_effect_probe import evaluate_side_effect_probe_results
from e2e_agent.core.side_effect_probe import execute_local_http_probe_transport
from e2e_agent.skills.loader import SkillPackageLoader

if TYPE_CHECKING:
    from e2e_agent.graph.state import E2EAgentState

def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").exists():
            return parent
    return Path(__file__).resolve().parents[4]


_ROOT_DIR = _repo_root()
_ASSERTION_TEMPLATE_SOURCE = "config/assertion-templates.yaml"


def _state_product_artifact_dir(root_dir: Path, state: "E2EAgentState") -> Path:
    product_id = str(state.get("product_id") or "product")
    return product_artifact_dir(
        root_dir,
        product_id,
        product_dir=state.get("product_artifact_dir"),
        source_paths=[state.get("prd_path"), state.get("manual_cases_path"), state.get("product_source_dir")],
    )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _load_llm_wrapper() -> Any | None:
    if not any(
        os.environ.get(name)
        for name in (
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_AUTH_TOKEN",
            "OPENAI_API_KEY",
            "DEEPSEEK_API_KEY",
            "LITELLM_API_KEY",
        )
    ):
        return None
    try:
        from e2e_agent.llm.wrapper import LLMWrapper
    except ModuleNotFoundError:
        return None
    try:
        return LLMWrapper()
    except Exception:
        return None


def _playwright_python_available() -> bool:
    return importlib.util.find_spec("playwright.async_api") is not None


def _detect_failure_category(message: str) -> str:
    lowered = message.lower()
    if any(
        token in lowered
        for token in (
            "browsertype.launch",
            "chromium.launch",
            "spawn eperm",
            "permission denied",
            "access is denied",
            "chrome.exe",
            "executable doesn't exist",
        )
    ):
        return "env_issue"
    if (
        "cannot destructure property" in lowered
        and ("undefined" in lowered or "null" in lowered)
    ):
        return "env_issue"
    if any(
        token in lowered
        for token in (
            "请求失败",
            "请求超时",
            "系统正在维护",
            "页面暂时无法访问",
            "系统内部发生错误",
            "错误代码：502",
            "错误代码:502",
            "错误代码 502",
            "502",
            "503",
            "504",
            "bad gateway",
        )
    ):
        return "env_issue"
    if any(
        token in lowered
        for token in (
            "cannot find module",
            "locator",
            "selector",
            "strict mode violation",
            "syntaxerror",
            "referenceerror",
            "is not a function",
            "spec file not found",
            "page function",
            "no tests found",
            "did not advance toward expected target_url",
            "agent3 replay transition failed",
            "did not reach expected node",
            "target node",
            "did not prove target",
        )
    ):
        return "script_bug"
    if any(
        token in lowered
        for token in (
            "err_connection",
            "name_not_resolved",
            "dns",
            "target page, context or browser has been closed",
            "browser has been closed",
            "context closed",
            "target closed",
            "executable doesn't exist",
        )
    ):
        return "env_issue"
    if any(
        token in lowered
        for token in (
            "test data",
            "invalid id",
            "身份证",
            "账号不存在",
            "policy not found",
            "huize payment closed loop",
            "issuestatus",
            "account/session boundary",
            "insuredsms",
            "sms code",
            "短信",
            "验证码",
            "保单状态",
            "data mismatch",
        )
    ):
        return "test_data"
    if any(
        token in lowered
        for token in (
            "timeout",
            "timed out",
            "retry",
            "networkidle",
            "net::err_abort",
            "flaky",
            "detached from dom",
        )
    ):
        return "flaky"
    return "product_bug"


def _read_formal_error_context(report_dir: str | Path | None, limit: int = 1200) -> str:
    if not report_dir:
        return ""
    root = Path(report_dir)
    if not root.exists():
        return ""
    try:
        candidates = sorted(
            root.glob("test-results/**/error-context.md"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return ""
    for candidate in candidates:
        try:
            text = candidate.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        compact = re.sub(r"\s+", " ", text).strip()
        if compact:
            return compact[:limit]
    return ""


def _default_suggestion(category: str, message: str) -> dict[str, Any]:
    mapping = {
        "env_issue": (
            "manual_review",
            "Check environment stability, browser availability, and upstream service health before rerun.",
        ),
        "test_data": (
            "update_test_data",
            "Refresh or correct test data so the scenario matches product preconditions.",
        ),
        "product_bug": (
            "report_bug",
            "Treat this as a likely product defect and attach trace / screenshots for triage.",
        ),
        "script_bug": (
            "update_selector",
            "Update selectors or page functions so the script matches the current UI structure.",
        ),
        "flaky": (
            "add_wait",
            "Stabilize the case with smarter waits or split the run to confirm the issue is transient.",
        ),
    }
    action, description = mapping[category]
    return {
        "action": action,
        "description": f"{description} Root signal: {message[:180]}",
        "code_diff": None,
    }


def _target_node_error(execution: dict[str, Any], target_node: object) -> str:
    target = str(target_node or "").strip()
    if not target:
        return ""
    if (
        str(execution.get("target_node_status") or "") == "reached"
        and str(execution.get("reached_target_node") or "") == target
    ):
        return ""
    return f"Agent4 did not prove target node reached: {target}"


def _apply_order_generation_target_evidence(
    execution: dict[str, Any],
    completion_rule: dict[str, Any],
    target_node: object,
) -> None:
    """Mirror Agent3's order-generation boundary as target evidence for passed formal specs."""
    target = str(target_node or "").strip()
    if not target or not completion_rule.get("order_generation_boundary"):
        return
    if str(execution.get("target_node_status") or "") == "reached":
        return
    if int(execution.get("returncode") or 0) != 0 or int(execution.get("failed") or 0) > 0:
        return
    if execution.get("errors"):
        return
    execution["reached_target_node"] = target
    execution["target_node_status"] = "reached"
    execution["target_node_inference"] = "agent3.order_generation_boundary"


def _assertion_template_index(
    assertion_results: list[dict[str, Any]],
) -> tuple[dict[str, str], list[str]]:
    index: dict[str, str] = {}
    used_templates: list[str] = []
    for item in assertion_results:
        case_id = str(item.get("case_id") or "").strip()
        template_type = str(item.get("template_type") or "").strip()
        if not case_id or not template_type:
            continue
        index[case_id] = template_type
        if template_type not in used_templates:
            used_templates.append(template_type)
    return index, used_templates


async def _build_suggestion(category: str, message: str) -> tuple[dict[str, Any], str]:
    wrapper = _load_llm_wrapper()
    if wrapper is None:
        return _default_suggestion(category, message), "rule-based-fallback"

    try:
        response = await wrapper.call(
            agent_name="exec_healing",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You classify test failures and provide one short healing suggestion. "
                        "Return plain text only."
                    ),
                },
                {
                    "role": "user",
                    "content": f"failure_category={category}\nerror={message}",
                },
            ],
        )
        content = ""
        if getattr(response, "choices", None):
            message_obj = response.choices[0].message
            content = getattr(message_obj, "content", "") or ""
        suggestion = _default_suggestion(category, message)
        if content.strip():
            suggestion["description"] = content.strip()
        return suggestion, wrapper.get_primary_model("exec_healing")
    except Exception:
        return _default_suggestion(category, message), "rule-based-fallback"


def _resolve_spec_path(
    product_id: str,
    scenario: dict[str, Any],
    root_dir: Path | None = None,
) -> Path | None:
    raw_path = str(scenario.get("spec_path") or "").strip()
    if not raw_path:
        return None
    candidate = Path(raw_path)
    if candidate.is_absolute():
        return candidate

    base_root = root_dir or Path(str(scenario.get("root_dir") or _ROOT_DIR))
    product_dir = scenario.get("product_artifact_dir")
    is_product_scoped_path = (
        bool(candidate.parts) and candidate.parts[0].lower() == "products"
    )
    product_ts_root = agent_artifact_dir(base_root, product_id, "agent3", product_dir=product_dir) / "ts-gen"
    legacy_product_ts_root = base_root / "products" / product_id / "ts-gen"
    candidates = [
        base_root / candidate,
    ]
    if not is_product_scoped_path:
        candidates.append(product_ts_root / candidate)
        candidates.append(legacy_product_ts_root / candidate)
    if base_root != _ROOT_DIR:
        candidates.append(_ROOT_DIR / candidate)
        if not is_product_scoped_path:
            candidates.append(agent_artifact_dir(_ROOT_DIR, product_id, "agent3", product_dir=product_dir) / "ts-gen" / candidate)
            candidates.append(_ROOT_DIR / "products" / product_id / "ts-gen" / candidate)

    for item in candidates:
        if item.exists():
            return item
    return candidates[1] if not is_product_scoped_path else candidates[0]


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _resolve_chain_spec_path(
    product_id: str,
    root_dir: Path,
    record: dict[str, Any],
    product_ts_root: Path | None = None,
    product_dir: str | Path | None = None,
) -> Path | None:
    product_ts_root = product_ts_root or agent_artifact_dir(root_dir, product_id, "agent3", product_dir=product_dir) / "ts-gen"
    raw_path = str(
        record.get("path")
        or record.get("chain_spec_product_path")
        or record.get("chain_spec_path")
        or ""
    ).strip()
    relative_path = str(record.get("relative_path") or record.get("chain_spec_path") or "").strip()

    candidates: list[Path] = []
    if raw_path:
        candidate = Path(raw_path)
        candidates.append(candidate if candidate.is_absolute() else root_dir / candidate)
    if relative_path:
        candidates.append(product_ts_root / relative_path)

    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0] if candidates else None


def _resolve_agent3_formal_spec_path(
    root_dir: Path,
    record: dict[str, Any],
    product_ts_root: Path,
) -> Path | None:
    raw_path = str(record.get("spec_path") or "").strip()
    if not raw_path:
        return None
    candidate = Path(raw_path)
    if candidate.is_absolute():
        return candidate

    candidates = [product_ts_root / candidate, root_dir / candidate]
    for item in candidates:
        if item.exists():
            return item
    return candidates[0]


def _agent3_formal_chain_spec_index(
    product_id: str,
    root_dir: Path,
    run_dir: str | Path | None = None,
    product_dir: str | Path | None = None,
) -> dict[str, dict[str, Any]]:
    product_ts_roots: list[Path] = []
    if run_dir:
        run_path = Path(run_dir)
        assets_root = run_path.parent.parent
        product_ts_roots.append(assets_root / "agent3" / "ts-gen")
    else:
        product_ts_roots.append(agent_artifact_dir(root_dir, product_id, "agent3", product_dir=product_dir) / "ts-gen")
        product_ts_roots.append(root_dir / "products" / product_id / "ts-gen")
        product_root = root_dir / "products" / product_id
        if product_root.exists():
            for assets_root in product_root.iterdir():
                if not assets_root.is_dir() or not (
                    assets_root.name.endswith(".assets") or (assets_root / "product-input.json").exists()
                ):
                    continue
                product_ts_roots.append(assets_root / "agent3" / "ts-gen")
    product_ts_roots = list(dict.fromkeys(product_ts_roots))
    index: dict[str, dict[str, Any]] = {}

    def add_record(record: Any, source: str, product_ts_root: Path) -> None:
        if not isinstance(record, dict):
            return
        formal_spec = _resolve_agent3_formal_spec_path(root_dir, record, product_ts_root)
        chain_spec = _resolve_chain_spec_path(product_id, root_dir, record, product_ts_root, product_dir=product_dir)
        if formal_spec is None and chain_spec is None:
            return
        item = dict(record)
        if formal_spec is not None:
            item["resolved_formal_spec_path"] = str(formal_spec)
        if chain_spec is not None:
            item["resolved_chain_spec_path"] = str(chain_spec)
        item["source"] = source
        for key_name in ("scenario_id", "path_id"):
            key_value = str(item.get(key_name) or "").strip()
            if key_value and key_value not in index:
                index[key_value] = item

    for product_ts_root in product_ts_roots:
        execution_plan = _read_json_object(product_ts_root / "tc-execution-plan.json")
        for scenario in execution_plan.get("scenarios", []) or []:
            if isinstance(scenario, dict) and (
                scenario.get("spec_path")
                or scenario.get("chain_spec_path")
                or scenario.get("chain_spec_product_path")
                or scenario.get("path")
            ):
                add_record(scenario, "tc-execution-plan.json", product_ts_root)

        manifest = _read_json_object(product_ts_root / ".artifacts" / "chain-manifest.json")
        for chain_spec in manifest.get("chain_specs", []) or []:
            add_record(chain_spec, "chain-manifest.json", product_ts_root)

    return index


def _attach_agent3_formal_chain_specs(
    scenarios: list[dict[str, Any]],
    *,
    product_id: str,
    root_dir: Path,
    run_dir: str | Path | None = None,
    product_dir: str | Path | None = None,
) -> list[dict[str, Any]]:
    index = _agent3_formal_chain_spec_index(product_id, root_dir, run_dir=run_dir, product_dir=product_dir)
    if not index:
        return scenarios

    enriched: list[dict[str, Any]] = []
    for scenario in scenarios:
        item = dict(scenario)
        match = index.get(str(item.get("scenario_id") or "").strip()) or index.get(
            str(item.get("path_id") or "").strip()
        )
        if match:
            formal_spec_path = str(match.get("resolved_formal_spec_path") or "").strip()
            chain_spec_path = str(match.get("resolved_chain_spec_path") or "").strip()
            if formal_spec_path:
                previous_spec_path = item.get("spec_path")
                if str(previous_spec_path or "") != formal_spec_path:
                    item["legacy_spec_path"] = previous_spec_path
                item["spec_path"] = formal_spec_path
                item["agent3_formal_scenario_spec"] = True
                item["agent3_formal_scenario_spec_source"] = match.get("source")
            if chain_spec_path:
                item["agent3_chain_spec_path"] = chain_spec_path
                item["agent3_chain_spec_source"] = match.get("source")
                item["agent3_chain_spec_evidence"] = True
            if match.get("chain") is not None:
                item["agent3_chain"] = match.get("chain")
            external_operations = match.get("external_operations")
            if isinstance(external_operations, list):
                item["external_operations"] = [
                    dict(operation)
                    for operation in external_operations
                    if isinstance(operation, dict)
                ]
            execution_requirements = match.get("execution_requirements")
            if isinstance(execution_requirements, dict):
                item["execution_requirements"] = dict(execution_requirements)
        enriched.append(item)
    return enriched


def _is_generated_smoke_spec(spec_path: Path) -> bool:
    try:
        content = spec_path.read_text(encoding="utf-8")
    except OSError:
        return False
    return (
        "@generated-by explore_agent" in content
        or "@generated-by mpt-ins-ts-gen" in content
        or "@generated-by agent3.static-first" in content
    )


def _visible_exec_dir(product_id: str, scenario: dict[str, Any]) -> Path:
    root_dir = Path(str(scenario.get("root_dir") or _ROOT_DIR))
    run_id = str(scenario.get("run_id") or "run-unknown")
    scenario_id = str(scenario.get("scenario_id") or scenario.get("path_id") or "scenario")
    run_dir = str(scenario.get("run_dir") or "").strip()
    if run_dir:
        return Path(run_dir) / "agent4" / "exec" / "visible-runs" / run_id / scenario_id
    return root_dir / "products" / product_id / "agent4" / "exec" / "visible-runs" / run_id / scenario_id


def _formal_exec_dir(
    product_id: str,
    scenario: dict[str, Any],
    root_dir: Path | None = None,
) -> Path:
    base_root = root_dir or Path(str(scenario.get("root_dir") or _ROOT_DIR))
    run_dir = str(
        scenario.get("run_dir")
        or os.environ.get("AGENT4_RUN_DIR")
        or os.environ.get("AGENT3_RUN_DIR")
        or ""
    ).strip()
    if run_dir:
        return Path(run_dir) / "agent4" / "tc-exec"
    return agent_artifact_dir(base_root, product_id, "agent4", product_dir=scenario.get("product_artifact_dir")) / "tc-exec"


def _agent4_visible_browser_enabled(scenario: dict[str, Any]) -> bool:
    return bool(scenario.get("entry_url"))


def _agent4_adaptive_fallback_enabled(scenario: dict[str, Any]) -> bool:
    if "agent4_adaptive_fallback" in scenario:
        return bool(scenario.get("agent4_adaptive_fallback"))
    disabled = str(os.environ.get("AGENT4_DISABLE_ADAPTIVE_FALLBACK") or "").strip().lower()
    return disabled not in {"1", "true", "yes", "on"}


def _env_flag(name: str) -> bool:
    return str(os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _agent4_force_visible_browser_enabled() -> bool:
    return _env_flag("AGENT4_FORCE_VISIBLE_BROWSER")


def _agent4_skill_timeout_seconds(scenarios: list[dict[str, Any]]) -> int:
    override = str(os.environ.get("AGENT4_SKILL_TIMEOUT_S") or "").strip()
    if override:
        try:
            return max(1, int(override))
        except ValueError:
            pass

    case_count = 0
    for scenario in scenarios:
        case_ids = scenario.get("case_ids")
        if isinstance(case_ids, list) and case_ids:
            case_count += len(case_ids)
        else:
            case_count += 1
    return max(300, case_count * 180)


def _agent4_formal_timeout_seconds(case_ids: list[str]) -> int:
    override = str(os.environ.get("AGENT4_FORMAL_TIMEOUT_S") or "").strip()
    if override:
        try:
            return max(1, int(override))
        except ValueError:
            pass
    return max(300, len(case_ids) * 180)


def _execution_status_error_and_target(
    execution: dict[str, Any],
    target_node: object,
) -> tuple[str, str, str]:
    status = "passed"
    error_message = ""
    if execution.get("failed", 0) > 0:
        status = "failed"
    elif execution.get("returncode", 0) != 0:
        status = "error"

    raw_errors = execution.get("errors", []) or []
    if raw_errors:
        error_message = "; ".join(str(item) for item in raw_errors if str(item).strip())
    if not error_message:
        stderr = str(execution.get("stderr", "")).strip()
        raw_output = str(execution.get("raw_output", "")).strip()
        error_message = stderr or raw_output

    target_error = _target_node_error(execution, target_node)
    if target_error:
        if status == "passed":
            status = "failed"
            error_message = target_error
        elif not error_message:
            error_message = target_error

    return status, error_message, target_error


def _normalise_external_operation(raw: dict[str, Any]) -> dict[str, Any]:
    operation_id = str(raw.get("operation_id") or raw.get("operationId") or "").strip()
    operation_type = str(raw.get("operation_type") or raw.get("operationType") or "").strip()
    status = str(raw.get("status") or "").strip()
    payment_method = str(raw.get("payment_method") or raw.get("paymentMethod") or "").strip()
    gateway_source = str(raw.get("gateway_pay_num_source") or raw.get("gatewayPayNumSource") or "").strip()
    issue_status = raw.get("issue_status", raw.get("issueStatus"))
    item: dict[str, Any] = {
        "operation_id": operation_id,
        "operation_type": operation_type,
        "status": status,
    }
    if payment_method:
        item["payment_method"] = payment_method
    if gateway_source:
        item["gateway_pay_num_source"] = gateway_source
    if issue_status is not None:
        item["issue_status"] = issue_status
    if raw.get("message"):
        item["message"] = raw.get("message")
    evidence = raw.get("evidence")
    if isinstance(evidence, dict):
        item["evidence"] = evidence
    return item


def _operation_status_for_contract(status: str) -> str:
    normalized = str(status or "").strip().lower()
    if normalized in {"passed", "passed-after-resume", "success", "succeeded"}:
        return "passed"
    if normalized in {"failed", "fail", "error"}:
        return "failed"
    if normalized in {"missing", "not_found", "not-found"}:
        return "missing"
    return normalized or "pending"


def _read_external_operation_artifacts(report_dir: str | Path | None) -> list[dict[str, Any]]:
    if not report_dir:
        return []
    root = Path(report_dir) / "external-ops"
    if not root.exists():
        return []
    artifacts: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json")):
        payload = _read_json_object(path)
        if not payload:
            continue
        payload["_artifact_path"] = str(path)
        artifacts.append(payload)
    return artifacts


def _external_operations_from_artifacts(
    artifact_payloads: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    operations: list[dict[str, Any]] = []
    artifact_paths: list[str] = []
    payment_closed_loop: dict[str, Any] = {}
    for payload in artifact_payloads:
        artifact_path = str(payload.get("_artifact_path") or "")
        if artifact_path:
            artifact_paths.append(artifact_path)
        raw_operations = payload.get("externalOperations")
        if isinstance(raw_operations, list):
            for raw in raw_operations:
                if isinstance(raw, dict):
                    operation = _normalise_external_operation(raw)
                    if artifact_path:
                        operation["artifact"] = artifact_path
                    operations.append(operation)
        if payload.get("status") or payload.get("issueResult") or payload.get("payResult"):
            issue_result = payload.get("issueResult") if isinstance(payload.get("issueResult"), dict) else {}
            pay_result = payload.get("payResult") if isinstance(payload.get("payResult"), dict) else {}
            evidence = payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {}
            payment_closed_loop = {
                "required": True,
                "status": str(payload.get("status") or "").strip() or None,
                "payment_method": evidence.get("paymentMethod"),
                "gateway_pay_num_source": evidence.get("gatewayPayNumSource"),
                "issue_status": issue_result.get("issueStatus"),
                "pay_status": issue_result.get("payStatus") or pay_result.get("status"),
                "artifact": artifact_path or None,
                "evidence": evidence,
            }
    return operations, artifact_paths, payment_closed_loop


def _merge_external_operations(
    planned_operations: list[dict[str, Any]],
    observed_operations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for planned in planned_operations:
        operation = _normalise_external_operation(planned)
        operation["status"] = _operation_status_for_contract(operation.get("status") or "pending")
        operation_id = str(operation.get("operation_id") or "")
        if operation_id:
            by_id[operation_id] = operation
    for observed in observed_operations:
        operation = _normalise_external_operation(observed)
        operation["status"] = _operation_status_for_contract(operation.get("status") or "")
        operation_id = str(operation.get("operation_id") or "")
        if operation_id:
            operation = {**by_id.get(operation_id, {}), **operation}
            by_id[operation_id] = operation
    merged = list(by_id.values())
    observed_without_id = [
        item
        for item in observed_operations
        if not str(item.get("operation_id") or item.get("operationId") or "").strip()
    ]
    merged.extend(_normalise_external_operation(item) for item in observed_without_id)
    return merged


def _payment_closed_loop_from_operations(
    operations: list[dict[str, Any]],
    observed_summary: dict[str, Any],
) -> dict[str, Any]:
    if not operations:
        return {}
    has_huize_operation = any(
        str(item.get("operation_type") or "").strip() in {"huize-pay-success", "huize-issue-status"}
        for item in operations
    )
    if not has_huize_operation:
        return {}
    issue_operation = next(
        (
            item
            for item in operations
            if str(item.get("operation_type") or "").strip() == "huize-issue-status"
        ),
        None,
    )
    pay_operation = next(
        (
            item
            for item in operations
            if str(item.get("operation_type") or "").strip() == "huize-pay-success"
        ),
        None,
    )
    issue_status = (
        issue_operation.get("issue_status")
        if isinstance(issue_operation, dict)
        else observed_summary.get("issue_status")
    )
    issue_passed = (
        isinstance(issue_operation, dict)
        and _operation_status_for_contract(str(issue_operation.get("status") or "")) == "passed"
        and str(issue_status) == "1"
    )
    pay_passed = (
        pay_operation is None
        or _operation_status_for_contract(str(pay_operation.get("status") or "")) == "passed"
    )
    if issue_passed and pay_passed:
        status = str(observed_summary.get("status") or "passed-after-resume")
    elif issue_operation is None or _operation_status_for_contract(str(issue_operation.get("status") or "")) in {"pending", "missing"}:
        status = "missing"
    else:
        status = "failed"
    payment_method = (
        (issue_operation or {}).get("payment_method")
        or (pay_operation or {}).get("payment_method")
        or observed_summary.get("payment_method")
    )
    return {
        "required": True,
        "status": status,
        "payment_method": payment_method,
        "gateway_pay_num_source": (
            (issue_operation or {}).get("gateway_pay_num_source")
            or (pay_operation or {}).get("gateway_pay_num_source")
            or observed_summary.get("gateway_pay_num_source")
        ),
        "issue_status": issue_status,
        "pay_status": observed_summary.get("pay_status"),
        "artifact": observed_summary.get("artifact"),
        "evidence": observed_summary.get("evidence") or {},
    }


def _collect_payment_closed_loop_evidence(
    scenario: dict[str, Any],
    execution: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    planned_operations = [
        item
        for item in scenario.get("external_operations", []) or []
        if isinstance(item, dict)
    ]
    artifact_payloads = _read_external_operation_artifacts(execution.get("report_dir"))
    observed_operations, artifacts, observed_summary = _external_operations_from_artifacts(artifact_payloads)
    operations = _merge_external_operations(planned_operations, observed_operations)
    if not operations:
        return [], artifacts, {}
    payment_closed_loop = _payment_closed_loop_from_operations(operations, observed_summary)
    return operations, artifacts, payment_closed_loop


def _closed_loop_error(payment_closed_loop: dict[str, Any]) -> str:
    if not payment_closed_loop.get("required"):
        return ""
    if str(payment_closed_loop.get("status") or "") == "passed-after-resume" and str(payment_closed_loop.get("issue_status")) == "1":
        return ""
    return "Huize payment closed loop did not prove issueStatus=1"


def _timeout_stream_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _visible_timeout_boundary_from_actions(action_log_path: Path) -> str:
    if not action_log_path.exists():
        return ""
    try:
        lines = action_log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-500:]
    except OSError:
        return ""
    terminal_boundary_message = ""
    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        strategy = str(record.get("click_strategy") or "").strip().lower()
        message = str(record.get("message") or record.get("text") or "").strip()
        if strategy == "account-session-boundary" or "account/session boundary" in message.lower():
            terminal_boundary_message = message
        url = str(record.get("url") or "")
        body = str(record.get("body_excerpt") or record.get("body") or "")
        if "insuredSms" not in url:
            continue
        if not any(token in body for token in ('"success":false', '"code":-1', "验证码不正确")):
            continue
        msg = ""
        try:
            parsed_body = json.loads(body)
            if isinstance(parsed_body, dict):
                msg = str(parsed_body.get("msg") or parsed_body.get("errorMessage") or "")
        except json.JSONDecodeError:
            msg_match = re.search(r'"msg"\s*:\s*"([^"]+)"', body)
            msg = msg_match.group(1) if msg_match else ""
        path_match = re.search(r"/api/apps/cps/insure/task/approve/insuredSms/(?:send|verify)", url)
        api_path = path_match.group(0) if path_match else url
        return f"Account/session boundary while waiting submit processing: {api_path} {msg}".strip()
    return terminal_boundary_message


def _visible_timeout_result(
    command: list[str],
    timeout_seconds: int,
    out_dir: Path,
    duration_s: float,
    stdout: str,
    stderr: str,
) -> dict[str, Any]:
    boundary_message = _visible_timeout_boundary_from_actions(out_dir / "browser-actions.jsonl")
    message = boundary_message or f"Visible Chromium runner timed out after {timeout_seconds} seconds"
    result: dict[str, Any] = {
        "returncode": 1,
        "passed": 0,
        "failed": 1,
        "errors": [message],
        "raw_output": stdout,
        "stderr": stderr,
        "duration_s": duration_s,
        "browser_actions_path": str(out_dir / "browser-actions.jsonl"),
        "visible_browser": True,
        "timeout_seconds": timeout_seconds,
        "timed_out": True,
        "command": command,
    }
    if boundary_message:
        result["execution_boundary"] = "account-session-boundary"
        result["target_node_status"] = "blocked"
    return result


def _should_run_agent4_adaptive_fallback(
    scenario: dict[str, Any],
    spec_path: Path | None,
    use_visible_runner: bool,
    status: str,
    category: str | None,
) -> bool:
    if use_visible_runner:
        return False
    if status not in {"failed", "error"}:
        return False
    if spec_path is None:
        return False
    if not _agent4_visible_browser_enabled(scenario):
        return False
    if not _agent4_adaptive_fallback_enabled(scenario):
        return False
    if category not in {"script_bug", "flaky"}:
        return False
    if scenario.get("agent3_replay_required") and not (
        _scenario_replay_actions(scenario) or list(scenario.get("real_actions", []) or [])
    ):
        return False
    return True


def _infer_agent3_replay_action_key(action: dict[str, Any]) -> str:
    action_key = str(action.get("action_key") or action.get("action") or "").strip()
    if action_key:
        return action_key

    click_strategy = str(action.get("click_strategy") or "").strip().lower()
    if (
        "mouse-h5-floating-premium-quote" in click_strategy
        or "mouse-h5-product-footer-insure" in click_strategy
    ):
        return "action.buy_now"
    if "js-health-notice-safe-option" in click_strategy:
        return "action.answer_health_notice"
    if "touchscreen-submit-btn" in click_strategy:
        return "action.submit"
    if "auto_wait_for_next_node" in click_strategy:
        return "action.auto_wait_for_next_node"
    return "action.click"


def _copy_agent3_replay_action(action: Any) -> dict[str, Any] | None:
    if not isinstance(action, dict):
        return None
    tag = str(action.get("tag") or "").strip().lower()
    text = str(action.get("text") or "").strip()
    action_type = str(action.get("action_type") or "").strip().lower()
    click_strategy = str(action.get("click_strategy") or "").strip().lower()
    if (
        tag == "diagnostic"
        or action_type.endswith("_diagnostic")
        or "diagnostic" in action_type
        or click_strategy.endswith("-scan-miss")
        or text.startswith("agreement_scan_miss:")
    ):
        return None
    copied = {
        str(key): value
        for key, value in action.items()
        if value not in (None, "", [], {})
    }
    if not copied:
        return None
    copied["action_key"] = _infer_agent3_replay_action_key(copied)
    if (
        copied["action_key"] == "action.click"
        and not copied.get("selector")
        and not copied.get("text")
        and not copied.get("locators")
    ):
        return None
    return copied


def _agent3_action_made_url_progress(action: dict[str, Any]) -> bool:
    source_url = str(action.get("source_url") or "").strip()
    target_url = str(action.get("target_url") or "").strip()
    return bool(source_url and target_url and source_url != target_url)


def _prune_agent3_replay_actions(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pruned: list[dict[str, Any]] = []
    for index, action in enumerate(actions):
        action_key = str(action.get("action_key") or "").strip()
        click_strategy = str(action.get("click_strategy") or "").strip().lower()
        if (
            action_key == "action.buy_now"
            and "premium-quote" not in click_strategy
            and not _agent3_action_made_url_progress(action)
        ):
            has_later_successful_buy_now = any(
                str(later.get("action_key") or "").strip() == action_key
                and _agent3_action_made_url_progress(later)
                for later in actions[index + 1 :]
            )
            if has_later_successful_buy_now:
                continue
        pruned.append(action)
    return pruned


def _agent3_replay_actions_by_path(state: dict[str, Any]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, str]]:
    by_path: dict[str, list[dict[str, Any]]] = {}
    source_by_path: dict[str, str] = {}

    explore_trace = state.get("explore_trace", {}) or {}
    if isinstance(explore_trace, dict):
        for raw_item in explore_trace.get("action_trace", []) or []:
            if not isinstance(raw_item, dict):
                continue
            path_id = str(raw_item.get("path_id") or "").strip()
            if not path_id:
                continue
            raw_actions = (
                raw_item.get("action_chain", [])
                if isinstance(raw_item.get("action_chain"), list)
                else [raw_item]
            )
            for raw_action in raw_actions:
                action = _copy_agent3_replay_action(raw_action)
                if not action:
                    continue
                action.setdefault("path_id", path_id)
                by_path.setdefault(path_id, []).append(action)
                source_by_path.setdefault(path_id, "explore_trace.action_trace")

    page_registry = state.get("page_registry", {}) or {}
    path_results = []
    if isinstance(page_registry, dict):
        path_results = list(page_registry.get("path_exploration_results", []) or [])
    for path_result in path_results:
        if not isinstance(path_result, dict):
            continue
        path_id = str(path_result.get("path_id") or "").strip()
        if not path_id or by_path.get(path_id):
            continue
        actions = [
            action
            for action in (
                _copy_agent3_replay_action(item)
                for item in path_result.get("action_chain", []) or []
            )
            if action
        ]
        if actions:
            by_path[path_id] = _prune_agent3_replay_actions(actions)
            source_by_path[path_id] = "page_registry.path_exploration_results.action_chain"

    by_path = {
        path_id: _prune_agent3_replay_actions(actions)
        for path_id, actions in by_path.items()
    }
    return by_path, source_by_path


def _agent3_replay_known_paths(state: dict[str, Any]) -> set[str]:
    path_ids: set[str] = set()
    explore_trace = state.get("explore_trace", {}) or {}
    if isinstance(explore_trace, dict):
        for raw_action in explore_trace.get("action_trace", []) or []:
            if isinstance(raw_action, dict):
                path_id = str(raw_action.get("path_id") or "").strip()
                if path_id:
                    path_ids.add(path_id)

    page_registry = state.get("page_registry", {}) or {}
    if isinstance(page_registry, dict):
        for path_result in page_registry.get("path_exploration_results", []) or []:
            if isinstance(path_result, dict):
                path_id = str(path_result.get("path_id") or "").strip()
                if path_id:
                    path_ids.add(path_id)
    return path_ids


def _attach_agent3_replay_actions(
    scenarios: list[dict[str, Any]],
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    by_path, source_by_path = _agent3_replay_actions_by_path(state)
    known_paths = _agent3_replay_known_paths(state)
    enriched: list[dict[str, Any]] = []
    for scenario in scenarios:
        item = dict(scenario)
        path_id = str(item.get("path_id") or "").strip()
        replay_actions = by_path.get(path_id, [])
        if path_id in known_paths:
            item["agent3_replay_required"] = True
        if replay_actions:
            item["agent3_replay_actions"] = [dict(action) for action in replay_actions]
            item["agent3_replay_source"] = source_by_path.get(path_id, "agent3.replay")
            item["agent3_replay_required"] = True
        enriched.append(item)
    return enriched


def _scenario_replay_actions(scenario: dict[str, Any]) -> list[dict[str, Any]]:
    actions = [
        action
        for action in (
            _copy_agent3_replay_action(item)
            for item in scenario.get("agent3_replay_actions", []) or []
        )
        if action
    ]
    return _prune_agent3_replay_actions(actions)


def _json_object_from_agent3_post_data(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return {}
    text = value.strip()
    if not text or text[0] not in "{[":
        return {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _iso_date_or_empty(value: Any) -> str:
    text = str(value or "").strip()
    return text if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) else ""


def _agent3_insurance_date_from_payload(payload: dict[str, Any]) -> str:
    for key in ("startDate", "insuranceDate"):
        date_text = _iso_date_or_empty(payload.get(key))
        if date_text:
            return date_text
    data = payload.get("data")
    if not isinstance(data, dict):
        return ""
    rows = data.get("102") or data.get(102)
    row = rows[0] if isinstance(rows, list) and rows else rows
    if isinstance(row, dict):
        return _iso_date_or_empty(row.get("insuranceDate"))
    return ""


def _agent3_insurance_date_from_text(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    payload = _json_object_from_agent3_post_data(text)
    date_text = _agent3_insurance_date_from_payload(payload)
    if date_text:
        return date_text
    for pattern in (
        r'"startDate"\s*:\s*"(\d{4}-\d{2}-\d{2})"',
        r'"insuranceDate"\s*:\s*"(\d{4}-\d{2}-\d{2})"',
        r"起保日期\s*(\d{4}-\d{2}-\d{2})",
    ):
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return ""


def _iter_nested_strings(value: Any, *, limit: int = 500) -> list[str]:
    strings: list[str] = []

    def visit(item: Any) -> None:
        if len(strings) >= limit:
            return
        if isinstance(item, str):
            strings.append(item)
            return
        if isinstance(item, dict):
            for nested in item.values():
                visit(nested)
            return
        if isinstance(item, list):
            for nested in item:
                visit(nested)

    visit(value)
    return strings


def _agent3_successful_insurance_date_from_action(action: dict[str, Any]) -> str:
    request = action.get("request")
    request_post_data = request.get("post_data") if isinstance(request, dict) else None
    raw_post_values = [
        action.get("post_data"),
        action.get("request_post_data"),
        action.get("request_body"),
        request_post_data,
    ]
    for raw_value in raw_post_values:
        payload = _json_object_from_agent3_post_data(raw_value)
        date_text = _agent3_insurance_date_from_payload(payload)
        if date_text:
            return date_text
    for text in _iter_nested_strings(action):
        if not (
            "startDate" in text
            or "insuranceDate" in text
            or "起保日期" in text
        ):
            continue
        date_text = _agent3_insurance_date_from_text(text)
        if date_text:
            return date_text
    return ""


def _agent3_successful_insurance_date(actions: list[dict[str, Any]]) -> str:
    submit_actions = [
        action
        for action in actions
        if str(action.get("action_key") or "").strip() == "action.submit"
    ]
    for action in submit_actions + [item for item in actions if item not in submit_actions]:
        date_text = _agent3_successful_insurance_date_from_action(action)
        if date_text:
            return date_text
    return ""


def _mock_data_with_agent3_replay_values(
    mock_data: dict[str, Any],
    actions: list[dict[str, Any]],
) -> dict[str, Any]:
    enriched = dict(mock_data)
    insurance_date = _agent3_successful_insurance_date(actions)
    if insurance_date:
        enriched["insuranceDate_102"] = insurance_date
        enriched["insuranceDate"] = insurance_date
    return enriched


def _visible_runner_script() -> str:
    return r"""
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const payloadPath = process.argv[2];
const payload = JSON.parse(fs.readFileSync(payloadPath, 'utf8'));
const outDir = payload.out_dir;
const actionLogPath = path.join(outDir, 'browser-actions.jsonl');
const resultPath = path.join(outDir, 'result.json');
const screenshotDir = path.join(outDir, 'screenshots');
const stepDelayMs = Number(process.env.AGENT4_STEP_DELAY_MS || payload.step_delay_ms || 2500);
const keepOpen = String(process.env.AGENT4_KEEP_BROWSER_OPEN || payload.keep_open || '0') === '1';
const screenshots = [];
const filledMockDataNodes = new Set();
globalThis.__agent4NetworkResponses = [];

function log(event) {
  const record = { timestamp: new Date().toISOString(), ...event };
  fs.appendFileSync(actionLogPath, JSON.stringify(record) + '\n', 'utf8');
  console.log(`[Agent4 Chromium] ${record.type}: ${record.message || ''}`);
}

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

function isAttachmentDocumentPage(url, title = '') {
  const rawUrl = String(url || '');
  const rawTitle = String(title || '');
  if (!rawUrl || /^about:blank$/i.test(rawUrl)) return false;
  let host = '';
  let pathname = rawUrl;
  try {
    const parsed = new URL(rawUrl);
    host = parsed.hostname;
    pathname = parsed.pathname;
  } catch (_) {}
  return /^(?:files?|docs?|documents?)\d*[.-]/i.test(host)
    || /\/file\d?\//i.test(rawUrl)
    || /\/(?:files?|documents?|attachments?)\//i.test(pathname)
    || /\.pdf(?:[?#]|$)/i.test(rawUrl)
    || /PDF|\.pdf/i.test(rawTitle);
}

async function closeAttachmentPageIfNeeded(context, mainPage, candidate, reason) {
  if (!candidate || candidate === mainPage || candidate.isClosed()) return false;
  await candidate.waitForLoadState('domcontentloaded', { timeout: 5000 }).catch(() => undefined);
  await sleep(300);
  const url = candidate.url();
  const title = await candidate.title().catch(() => '');
  if (!isAttachmentDocumentPage(url, title)) return false;
  log({ type: 'attachment-page-close', message: `closed attachment page: ${reason}`, reason, url, title });
  await candidate.close().catch(() => undefined);
  await mainPage.bringToFront().catch(() => undefined);
  return true;
}

async function closeAttachmentPages(context, mainPage, reason) {
  const closed = [];
  for (const candidate of context.pages()) {
    const beforeUrl = candidate.url();
    if (await closeAttachmentPageIfNeeded(context, mainPage, candidate, reason)) {
      closed.push(beforeUrl);
    }
  }
  if (closed.length) {
    log({ type: 'attachment-pages-closed', message: `closed ${closed.length} attachment page(s)`, reason, urls: closed });
  }
  await mainPage.bringToFront().catch(() => undefined);
  return closed;
}

async function restoreMainPageFromAttachment(page, fallbackUrl, reason) {
  const sourceUrl = page.url();
  const sourceTitle = await page.title().catch(() => '');
  if (!isAttachmentDocumentPage(sourceUrl, sourceTitle)) {
    return { restored: false };
  }
  log({
    type: 'attachment-main-page-detected',
    message: `main page is an attachment document: ${reason}`,
    reason,
    source_url: sourceUrl,
    title: sourceTitle,
  });

  await page.goBack({ waitUntil: 'domcontentloaded', timeout: 8000 }).catch(() => undefined);
  await sleep(500);
  let targetUrl = page.url();
  let targetTitle = await page.title().catch(() => '');
  if (!isAttachmentDocumentPage(targetUrl, targetTitle)) {
    await page.bringToFront().catch(() => undefined);
    const result = { restored: true, strategy: 'goBack', source_url: sourceUrl, target_url: targetUrl };
    log({ type: 'attachment-main-page-restored', message: `restored main page by going back: ${reason}`, reason, ...result });
    return result;
  }

  if (fallbackUrl && fallbackUrl !== sourceUrl && !isAttachmentDocumentPage(fallbackUrl, '')) {
    await page.goto(fallbackUrl, { waitUntil: 'domcontentloaded', timeout: 15000 }).catch(() => undefined);
    await sleep(500);
    targetUrl = page.url();
    targetTitle = await page.title().catch(() => '');
    if (!isAttachmentDocumentPage(targetUrl, targetTitle)) {
      await page.bringToFront().catch(() => undefined);
      const result = { restored: true, strategy: 'fallbackUrl', source_url: sourceUrl, target_url: targetUrl };
      log({ type: 'attachment-main-page-restored', message: `restored main page from fallback URL: ${reason}`, reason, ...result });
      return result;
    }
  }

  const result = { restored: false, strategy: 'unresolved', source_url: sourceUrl, target_url: targetUrl };
  log({ type: 'attachment-main-page-restore-failed', message: `main page remained on attachment document: ${reason}`, reason, ...result });
  return result;
}

function setupAttachmentPageCleanup(context, mainPage) {
  context.on('page', popup => {
    void closeAttachmentPageIfNeeded(context, mainPage, popup, 'new-page').catch(error => {
      log({ type: 'attachment-page-close-error', message: String(error?.message || error), url: popup.url() });
    });
  });
}

function contextOptionsForPayload(payload) {
  const viewport = payload.viewport || { width: 1366, height: 900 };
  const options = { viewport };
  if (Number(viewport.width || 0) <= 500) {
    options.isMobile = true;
    options.hasTouch = true;
    options.userAgent = (
      'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) ' +
      'AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 ' +
      'Mobile/15E148 Safari/604.1'
    );
  }
  return options;
}

function screenshotName(step, label) {
  const prefix = String(step).padStart(2, '0');
  const safeLabel = String(label || 'page').replace(/[^a-zA-Z0-9_-]+/g, '-').replace(/^-+|-+$/g, '') || 'page';
  return `${prefix}-${safeLabel}.png`;
}

async function captureScreenshot(page, step, label) {
  fs.mkdirSync(screenshotDir, { recursive: true });
  const filePath = path.join(screenshotDir, screenshotName(step, label));
  await page.screenshot({ path: filePath, fullPage: true });
  const record = { step, label, path: filePath, url: page.url(), timestamp: new Date().toISOString() };
  screenshots.push(record);
  log({ type: 'screenshot', message: label, step, screenshot_path: filePath, url: page.url() });
  return record;
}

async function dismissBlockingOverlays(page) {
  const dismissed = [];
  const bodyText = await page.locator('body').innerText({ timeout: 1500 }).catch(() => '');
  if (/未完成的投保单|是否继续投保/.test(bodyText)) {
    const cancelButton = page.locator('button, a, [role="button"], .btn, .button, [class*="btn"], .layui-layer-btn a').filter({ hasText: /取消|否/ }).last();
    if (await cancelButton.isVisible({ timeout: 1500 }).catch(() => false)) {
      const before = page.url();
      await cancelButton.click({ force: true, timeout: 5000 }).catch(async () => {
        await cancelButton.evaluate(element => element.click()).catch(() => undefined);
      });
      dismissed.push({ selector: 'unfinished-policy-dialog-cancel', text: '取消未完成投保单', source_url: before, target_url: page.url() });
      await sleep(800);
      return dismissed;
    }
  }
  if (/开户行识别失败|手动选择开户行/.test(bodyText)) {
    const hidden = await page.evaluate(() => {
      const blockerRe = /开户行识别失败|手动选择开户行/;
      let count = 0;
      for (const node of Array.from(document.querySelectorAll('.am-toast,.am-toast-notice,.adm-toast,[role="alert"],[role="dialog"],.am-modal,.am-modal-wrap'))) {
        if (!blockerRe.test(node.innerText || node.textContent || '')) continue;
        node.style.display = 'none';
        node.style.visibility = 'hidden';
        node.setAttribute('aria-hidden', 'true');
        count += 1;
      }
      return count;
    }).catch(() => 0);
    if (hidden) {
      dismissed.push({ selector: 'bank-recognition-toast', text: '开户行识别失败/手动选择开户行', hidden });
      await sleep(300);
    }
  }
  for (const selector of ['button.btn-agree', '.btn-agree']) {
    const locator = page.locator(selector).first();
    if (await locator.isVisible({ timeout: 1500 }).catch(() => false)) {
      const before = page.url();
      await locator.click({ timeout: 5000 }).catch(async () => {
        await locator.evaluate(element => element.click());
      });
      dismissed.push({ selector, text: '已阅读并同意', source_url: before, target_url: page.url() });
      await sleep(800);
      break;
    }
  }
  return dismissed;
}

function isLikelyWholePageTransientError(bodyText, url) {
  const text = String(bodyText || '').trim();
  const currentUrl = String(url || '');
  if (!text) return false;
  if (/Request failed with status code/i.test(text) && /product\/detail/i.test(currentUrl)) return false;
  if (/请求失败|Request failed with status code/i.test(text) && text.length > 300) return false;
  const transientPattern = /璇锋眰瓒呮椂|椤甸潰鏆傛椂鏃犳硶璁块棶|绯荤粺姝ｅ湪缁存姢|502|Bad Gateway|缃戠粶寮傚父|璇锋眰澶辫触/;
  return transientPattern.test(text);
}

async function recoverTransientPageError(page) {
  const transientPattern = /请求超时|页面暂时无法访问|系统正在维护|502|Bad Gateway|网络异常|请求失败/;
  let recovered = false;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    const bodyText = await page.locator('body').innerText({ timeout: 2000 }).catch(() => '');
    if (!isLikelyWholePageTransientError(bodyText, page.url())) return recovered;
    log({ type: 'transient-page-error', message: `transient page error detected, reload attempt ${attempt + 1}`, url: page.url(), body_excerpt: bodyText.slice(0, 120) });
    await page.reload({ waitUntil: 'domcontentloaded', timeout: 30000 }).catch(async () => {
      const retry = page.getByText(/刷新|重新加载|重试/).last();
      if (await retry.isVisible({ timeout: 1000 }).catch(() => false)) {
        await retry.click({ force: true, timeout: 5000 }).catch(() => undefined);
      }
    });
    await page.waitForLoadState('domcontentloaded', { timeout: 15000 }).catch(() => undefined);
    await sleep(3000);
    recovered = true;
  }
  return recovered;
}

const FIELD_ALIAS_STOP_WORDS = new Set([
  'insure', 'form', 'field', 'input', 'submit', 'button', 'select',
  'applicant', 'insured', 'policy', 'payment', 'beneficiary',
]);

function semanticAliasesForField(field) {
  const key = String(field.field_key || '').toLowerCase();
  const label = String(field.label || field.raw?.label || '').trim();
  const aliases = [];
  const add = value => {
    const text = String(value || '').replace(/\s+/g, ' ').trim();
    if (text && !aliases.includes(text)) aliases.push(text);
  };
  if (label) add(label);
  if (key.includes('start_date')) add('起保日期');
  if (key.includes('applicant') || key.startsWith('insure_form.applicant')) {
    if (key.includes('name')) add('投保人信息 姓名'), add('投保人姓名');
    if (key.includes('id_no') || key.includes('idno') || key.includes('idnumber')) add('投保人信息 证件号码'), add('投保人证件号码');
    if (key.includes('phone') || key.includes('mobile')) add('投保人信息 手机号码'), add('投保人手机号码');
    if (key.includes('email')) add('投保人信息 电子邮箱'), add('投保人电子邮箱');
    if (key.includes('address')) add('投保人信息 联系地址'), add('投保人联系地址');
    if (key.includes('annual_income')) add('投保人信息 年收入'), add('年收入（万元）');
    if (key.includes('occupation')) add('投保人信息 职业'), add('投保人职业');
    if (key.includes('region')) add('投保人信息 居住省市'), add('投保人居住省市');
    if (key.includes('height')) add('投保人信息 身高'), add('投保人身高');
    if (key.includes('weight')) add('投保人信息 体重'), add('投保人体重');
    if (key.includes('card_valid_start') || key.includes('cardvalidstart')) add('投保人信息 证件有效期 开始');
    if (key.includes('card_valid_end') || key.includes('cardvalidend')) add('投保人信息 证件有效期 结束');
  }
  if (key.includes('insured') && !key.startsWith('insure_form.applicant')) {
    if (key.includes('name')) add('被保险人信息 姓名'), add('为谁投保 姓名'), add('被保险人姓名');
    if (key.includes('id_no') || key.includes('idno') || key.includes('idnumber')) add('被保险人信息 证件号码'), add('为谁投保 证件号码'), add('被保险人证件号码');
    if (key.includes('phone') || key.includes('mobile')) add('被保险人信息 手机号码'), add('为谁投保 手机号码'), add('被保险人手机号码');
    if (key.includes('email')) add('被保险人信息 电子邮箱'), add('被保险人电子邮箱');
    if (key.includes('address')) add('被保险人信息 联系地址'), add('为谁投保 联系地址'), add('被保险人联系地址');
    if (key.includes('occupation')) add('被保险人信息 职业'), add('为谁投保 职业'), add('被保险人职业');
    if (key.includes('region')) add('被保险人信息 居住省市'), add('为谁投保 居住省市'), add('被保险人居住省市');
    if (key.includes('height')) add('被保险人信息 身高'), add('为谁投保 身高'), add('被保险人身高');
    if (key.includes('weight')) add('被保险人信息 体重'), add('为谁投保 体重'), add('被保险人体重');
    if (key.includes('card_valid_start') || key.includes('cardvalidstart')) add('被保险人信息 证件有效期 开始');
    if (key.includes('card_valid_end') || key.includes('cardvalidend')) add('被保险人信息 证件有效期 结束');
  }
  if (key.includes('agreement')) add('本人充分阅读'), add('本人已逐页阅读'), add('保险条款'), add('责任免除');
  return aliases;
}

function fieldAliases(field) {
  const aliases = [];
  const add = value => {
    const text = String(value || '').replace(/\s+/g, ' ').trim();
    if (!text || text === 'mock' || aliases.includes(text)) return;
    if (FIELD_ALIAS_STOP_WORDS.has(text.toLowerCase())) return;
    aliases.push(text);
  };
  for (const locator of field.locators || []) {
    const by = String(locator.by || '');
    if (['label_text', 'text', 'param', 'name', 'placeholder'].includes(by)) add(locator.value);
  }
  for (const alias of semanticAliasesForField(field)) add(alias);
  add(field.label);
  add(field.name);
  add(field.field_key);
  add(field.raw?.label);
  add(field.raw?.placeholder);
  for (const token of String(field.field_key || '').split(/[._:-]+/)) {
    if (token.length >= 4 && !FIELD_ALIAS_STOP_WORDS.has(token.toLowerCase())) add(token);
  }
  return aliases;
}

function targetProbeFieldSelector(field) {
  return `agent-field-${String(field.field_key || 'field').replace(/[^a-zA-Z0-9_-]+/g, '-')}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function shouldProbeFieldsForNode(nodeId) {
  return ['NODE-insure-form'].includes(String(nodeId || ''));
}

function pageElementRecordsForNode(pageElementPlan, nodeId) {
  if (!shouldProbeFieldsForNode(nodeId)) return [];
  return (pageElementPlan || []).filter(record =>
    record.node_id === nodeId || (record.matched_node_ids || []).includes(nodeId)
  );
}

function selectedLocatorValue(locator) {
  if (!locator || typeof locator !== 'object') return '';
  return String(locator.value || '').trim();
}

function strategyForField(nodeId, fieldKey) {
  return ((payload.component_strategy || {}).field_strategies || []).find(item =>
    item.node_id === nodeId && item.field_key === fieldKey
  ) || {};
}

function pageElementFieldForKey(nodeId, fieldKey) {
  for (const record of pageElementRecordsForNode(payload.page_element_plan || [], nodeId)) {
    const found = (record.fields || []).find(field => field.field_key === fieldKey);
    if (found) return found;
  }
  return {};
}

function fieldWithContract(nodeId, resolution) {
  const base = pageElementFieldForKey(nodeId, resolution.field_key);
  const component = strategyForField(nodeId, resolution.field_key);
  const selected = resolution.selected_locator || {};
  const selector = selected.by === 'selector' ? selectedLocatorValue(selected) : base.selector;
  return {
    ...base,
    field_key: resolution.field_key || base.field_key,
    selector,
    required: Boolean(resolution.required ?? base.required),
    locators: resolution.locator_candidates || base.locators || [],
    field_resolution: resolution,
    component_strategy: component,
    control_type: component.control_type || resolution.control_type || base.control_type,
    fill_strategy: component.fill_strategy || resolution.fill_strategy || base.fill_strategy,
    mock_key: resolution.mock_key || base.field_key,
  };
}

function isDefaultPreservedField(field) {
  const key = String(field.field_key || field.mock_key || '').toLowerCase();
  return [
    'policy.start_date',
    'insured.relation',
    'insured.type',
    'insure_form.insuredrelation',
    'insure_form.insuredtype',
    'insure_form.relation',
    'insure_form.cardtype',
    'insure_form.insuredidtype',
    'insure_form.cardvalidtype',
  ].includes(key);
}

function isRunnableMockField(field) {
  if (isBankAccountField(field)) return false;
  return Boolean(field.required) && !isDefaultPreservedField(field);
}

function fieldProbeText(field) {
  const probe = [
    field.field_key,
    field.mock_key,
    field.label,
    field.name,
    field.selector,
    field.field_resolution?.selected_locator?.value,
  ].map(value => String(value || '')).join(' ');
  return probe;
}

function isBankAccountField(field) {
  return /开卡信息|银行账号|银行卡号|银行账户|格式参照/.test(fieldProbeText(field));
}

function isBankPickerField(field) {
  const probe = fieldProbeText(field);
  if (/银行账号|银行卡号|银行账户|持卡人/.test(probe)) return false;
  return /授权续费银行|开户银行|开户行/.test(probe);
}

function mockValueForField(field) {
  const probe = fieldProbeText(field);
  if (/账户名须为投保人本人|持卡人/.test(probe) && (payload.mock_data.cardOwner_107 || payload.mock_data['applicant.name'])) {
    return payload.mock_data.cardOwner_107 || payload.mock_data['applicant.name'];
  }
  if (isBankAccountField(field) && payload.mock_data.payAccount_107) return payload.mock_data.payAccount_107;
  if (/真实姓名|姓名/.test(probe) && payload.mock_data['applicant.name']) return payload.mock_data['applicant.name'];
  if (/证件号码|身份证号/.test(probe) && payload.mock_data['applicant.id_no']) return payload.mock_data['applicant.id_no'];
  if (/详细地址|联系地址|地址/.test(probe) && payload.mock_data['applicant.address']) return payload.mock_data['applicant.address'];
  if (/真实手机|手机号码|手机号/.test(probe) && (payload.mock_data['applicant.mobile'] || payload.mock_data['applicant.phone'])) {
    return payload.mock_data['applicant.mobile'] || payload.mock_data['applicant.phone'];
  }
  if (/真实邮箱|邮箱|电子邮箱/i.test(probe) && payload.mock_data['applicant.email']) return payload.mock_data['applicant.email'];
  return payload.mock_data[field.mock_key || field.field_key] ?? payload.mock_data[field.field_key] ?? field.mock_value;
}

function isFallbackRunnableMockField(nodeId, field) {
  if (String(nodeId || '') !== 'NODE-insure-form') return false;
  if (isDefaultPreservedField(field)) return false;
  if (isBankPickerField(field)) return false;
  if (isBankAccountField(field)) return false;
  if (String(field.type || '').toLowerCase() === 'hidden') return false;
  if (String(field.type || '').toLowerCase() === 'file') return false;
  const value = mockValueForField(field);
  return value !== undefined && value !== null && value !== '' && value !== 'mock';
}

function resolutionRank(resolution) {
  const selected = resolution.selected_locator || {};
  const selectedValue = String(selected.value || '');
  let score = 0;
  if (resolution.locator_status === 'verified_static') score += 1000;
  if (String(resolution.proof || '').includes('static selector verified')) score += 500;
  if (selected.by === 'selector') score += 200;
  if (/name\^=|input\[name\^=|div\[name\^=|input\[type="checkbox"\]/.test(selectedValue)) score += 120;
  if (/label_text|has_text|param/.test(String(selected.by || ''))) score -= 80;
  if (/input:not\(\[type="hidden"\]\)\[name\*=/.test(selectedValue)) score -= 120;
  if (!resolution.required) score -= 10;
  return score;
}

function dedupeResolutionsByFieldKey(resolutions) {
  const selected = new Map();
  for (const resolution of [...resolutions].sort((left, right) => resolutionRank(right) - resolutionRank(left))) {
    const key = String(resolution.field_key || '');
    if (!key || selected.has(key)) continue;
    selected.set(key, resolution);
  }
  return Array.from(selected.values());
}

function fieldsForNodeFromContract(nodeId) {
  if (!shouldProbeFieldsForNode(nodeId)) return [];
  const resolutions = ((payload.field_resolution_plan || {}).fields || []).filter(item => item.node_id === nodeId);
  if (resolutions.length) {
    const contractFields = dedupeResolutionsByFieldKey(resolutions).map(item => fieldWithContract(nodeId, item));
    const requiredFields = contractFields.filter(isRunnableMockField);
    if (requiredFields.length) return requiredFields;
    return contractFields.filter(field => isFallbackRunnableMockField(nodeId, field));
  }
  const contractFields = pageElementRecordsForNode(payload.page_element_plan || [], nodeId).flatMap(record =>
    (record.fields || []).map(field => ({
      ...field,
      component_strategy: strategyForField(nodeId, field.field_key),
      fill_strategy: field.fill_strategy || strategyForField(nodeId, field.field_key).fill_strategy,
      control_type: field.control_type || strategyForField(nodeId, field.field_key).control_type,
    }))
  );
  const requiredFields = contractFields.filter(isRunnableMockField);
  if (requiredFields.length) return requiredFields;
  return contractFields.filter(field => isFallbackRunnableMockField(nodeId, field));
}

async function resolveFieldLocator(page, field) {
  const selected = field.field_resolution?.selected_locator || field.selected_locator || {};
  if (selected.by === 'selector' && selectedLocatorValue(selected)) {
    const locator = await firstVisibleLocatorForSelector(page, selectedLocatorValue(selected));
    if (locator) {
      return { locator, selector: selectedLocatorValue(selected), strategy: 'field-resolution' };
    }
  }
  if (field.selector) {
    const locator = await firstVisibleLocatorForSelector(page, field.selector);
    if (locator) {
      return { locator, selector: field.selector, strategy: 'selector' };
    }
  }

  const aliases = fieldAliases(field);
  for (const alias of aliases) {
    const value = cssAttrValue(alias);
    const locator = page.locator(
      `input:not([type="hidden"])[name*="${value}" i], textarea[name*="${value}" i], select[name*="${value}" i], ` +
      `input:not([type="hidden"])[id*="${value}" i], textarea[id*="${value}" i], select[id*="${value}" i], ` +
      `input:not([type="hidden"])[placeholder*="${value}" i], textarea[placeholder*="${value}" i], ` +
      `input:not([type="hidden"])[aria-label*="${value}" i], textarea[aria-label*="${value}" i], select[aria-label*="${value}" i]`
    ).first();
    if (await locator.isVisible({ timeout: 300 }).catch(() => false)) {
      return { locator, selector: `attr:${alias}`, strategy: 'attr' };
    }
  }

  const token = targetProbeFieldSelector(field);
  const found = await page.evaluate(({ aliases, token }) => {
    const controls = Array.from(document.querySelectorAll(
      'input:not([type="hidden"]):not([type="button"]):not([type="submit"]):not([type="reset"]):not([type="file"]), textarea, select, [contenteditable="true"], div[name], [role="button"], [role="combobox"], .hz-dropdown, .input-select'
    ));

    function normalize(value) {
      return String(value || '').replace(/\s+/g, '').toLowerCase();
    }

    function isVisible(element) {
      const style = window.getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.visibility !== 'hidden'
        && style.display !== 'none'
        && rect.width > 0
        && rect.height > 0
        && !element.disabled;
    }

    function textOf(element) {
      return String(element?.innerText || element?.textContent || '').replace(/\s+/g, ' ').trim();
    }

    function relatedText(control) {
      const chunks = [
        control.getAttribute('name'),
        control.getAttribute('id'),
        control.getAttribute('placeholder'),
        control.getAttribute('aria-label'),
      ];
      if (control.id) {
        const label = document.querySelector(`label[for="${CSS.escape(control.id)}"]`);
        chunks.push(textOf(label));
      }
      chunks.push(textOf(control.closest('label')));
      const containers = [
        control.closest('.form-item, .form-group, .el-form-item, .ant-form-item, .form-row, .item, li, tr, td, dd, dl, p, div'),
        control.parentElement,
        control.parentElement?.parentElement,
      ];
      for (const container of containers) chunks.push(textOf(container).slice(0, 240));
      let sibling = control.previousElementSibling;
      for (let index = 0; sibling && index < 3; index += 1, sibling = sibling.previousElementSibling) {
        chunks.push(textOf(sibling));
      }
      return chunks.filter(Boolean).join(' ');
    }

    function score(control) {
      if (!isVisible(control)) return -1;
      const attrText = normalize([
        control.getAttribute('name'),
        control.getAttribute('id'),
        control.getAttribute('placeholder'),
        control.getAttribute('aria-label'),
        control.className,
      ].filter(Boolean).join(' '));
      const contextText = normalize(relatedText(control));
      const hasValue = Boolean(String(control.value || '').trim());
      let best = 0;
      for (const alias of aliases) {
        const normalizedAlias = normalize(alias);
        if (!normalizedAlias || normalizedAlias.length < 2) continue;
        if (attrText === normalizedAlias) best = Math.max(best, 160);
        if (attrText.includes(normalizedAlias) || normalizedAlias.includes(attrText)) best = Math.max(best, 120);
        if (contextText.includes(normalizedAlias)) best = Math.max(best, 100);
      }
      if (!hasValue) best += 15;
      return best;
    }

    const ranked = controls
      .map(control => ({ control, score: score(control) }))
      .filter(item => item.score > 0)
      .sort((left, right) => right.score - left.score);
    if (!ranked.length) return false;
    ranked[0].control.setAttribute('data-agent-field-target', token);
    return true;
  }, { aliases, token }).catch(() => false);

  if (!found) return null;
  const selector = `[data-agent-field-target="${token}"]`;
  const locator = page.locator(selector).first();
  if (!(await locator.count().catch(() => 0))) return null;
  return { locator, selector, strategy: 'target-probe' };
}

async function firstVisibleLocatorForSelector(page, selector) {
  const locator = page.locator(selector);
  const count = Math.min(await locator.count().catch(() => 0), 80);
  for (let index = 0; index < count; index += 1) {
    const candidate = locator.nth(index);
    if (await candidate.isVisible({ timeout: 300 }).catch(() => false)) return candidate;
  }
  return null;
}

function escapeRegex(text) {
  return String(text || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

async function clickVisibleDropdownOption(page, preferredTexts, anchorBox = null) {
  const optionSelector = '.hz-dropdown-content .hz-select-option, .hz-select-option, .hz-option, .el-select-dropdown__item, .ant-select-item-option, .am-list-item, [role="option"], li';
  async function firstVisibleDropdownOption(page, pattern) {
    const matching = page.locator(optionSelector).filter({ hasText: pattern });
    const count = Math.min(await matching.count().catch(() => 0), 120);
    for (let index = 0; index < count; index += 1) {
      const option = matching.nth(index);
      if (await option.isVisible({ timeout: 300 }).catch(() => false)) return option;
    }
    return null;
  }
  async function firstVisibleNonPlaceholderDropdownOption(page) {
    const matching = page.locator(optionSelector);
    const count = Math.min(await matching.count().catch(() => 0), 120);
    for (let index = 0; index < count; index += 1) {
      const option = matching.nth(index);
      if (!(await option.isVisible({ timeout: 300 }).catch(() => false))) continue;
      const text = await option.innerText({ timeout: 1000 }).catch(() => '');
      if (String(text || '').trim() && !/请选择|select|please/i.test(text)) return option;
    }
    return null;
  }
  async function clickBestAnchoredDropdownOption(page, preferredTexts, anchorBox) {
    if (!anchorBox) return null;
    const token = `agent-dropdown-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    const marked = await page.evaluate(({ optionSelector, preferredTexts, anchorBox, token }) => {
      function isVisible(element) {
        const style = window.getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return style.visibility !== 'hidden'
          && style.display !== 'none'
          && rect.width > 0
          && rect.height > 0;
      }
      function textOf(element) {
        return String(element.innerText || element.textContent || '').replace(/\s+/g, ' ').trim();
      }
      function compact(value) {
        return String(value || '').replace(/\s+/g, '').toLowerCase();
      }
      const preferred = (preferredTexts || []).map(text => String(text || '').trim()).filter(Boolean);
      const anchorCenterX = Number(anchorBox.x || 0) + Number(anchorBox.width || 0) / 2;
      const anchorBottom = Number(anchorBox.y || 0) + Number(anchorBox.height || 0);
      const candidates = Array.from(document.querySelectorAll(optionSelector))
        .filter(isVisible)
        .map(element => {
          const rect = element.getBoundingClientRect();
          const text = textOf(element);
          const normalized = compact(text);
          const exactIndex = preferred.findIndex(item => compact(item) === normalized);
          const fuzzyIndex = preferred.findIndex(item => normalized.includes(compact(item)) || compact(item).includes(normalized));
          const matchScore = exactIndex >= 0 ? 10000 - exactIndex * 100 : fuzzyIndex >= 0 ? 7000 - fuzzyIndex * 100 : 1000;
          const optionCenterX = rect.left + rect.width / 2;
          const verticalDistance = Math.abs(rect.top - anchorBottom);
          const horizontalDistance = Math.abs(optionCenterX - anchorCenterX);
          const abovePenalty = rect.bottom < Number(anchorBox.y || 0) - 4 ? 5000 : 0;
          return { element, text, score: matchScore - verticalDistance - horizontalDistance * 0.2 - abovePenalty };
        })
        .filter(item => item.text && !/请选择|select|please/i.test(item.text))
        .sort((left, right) => right.score - left.score);
      const target = candidates[0];
      if (!target) return null;
      document.querySelectorAll('[data-agent-dropdown-option]').forEach(node => node.removeAttribute('data-agent-dropdown-option'));
      target.element.setAttribute('data-agent-dropdown-option', token);
      return { text: target.text, score: target.score };
    }, { optionSelector, preferredTexts, anchorBox, token }).catch(() => null);
    if (!marked) return null;
    const option = page.locator(`[data-agent-dropdown-option="${token}"]`).first();
    if (!(await option.isVisible({ timeout: 800 }).catch(() => false))) return null;
    await option.click({ force: true, timeout: 5000 });
    await sleep(500);
    return marked.text;
  }
  const anchored = await clickBestAnchoredDropdownOption(page, preferredTexts, anchorBox);
  if (anchored) return anchored;
  for (const text of preferredTexts) {
    if (!String(text || '').trim()) continue;
    const option = await firstVisibleDropdownOption(page, new RegExp(`^\\s*${escapeRegex(text)}\\s*$`));
    if (option) {
      await option.click({ force: true, timeout: 5000 });
      await sleep(500);
      return text;
    }
  }
  const fallback = await firstVisibleNonPlaceholderDropdownOption(page);
  if (fallback) {
    const text = await fallback.innerText({ timeout: 1000 }).catch(() => '');
    await fallback.click({ force: true, timeout: 5000 });
    await sleep(500);
    return text;
  }
  return null;
}

async function controlText(locator) {
  return await locator.evaluate(element => {
    const target = element.matches('input, textarea, select') ? element : element.querySelector('input, textarea, select');
    const value = target && 'value' in target ? target.value : '';
    const text = element.innerText || element.textContent || '';
    return String(value || text || '').replace(/\s+/g, ' ').trim();
  }).catch(() => '');
}

async function setNativeControlValue(locator, value) {
  return await locator.evaluate((element, value) => {
    const target = element.matches('input, textarea') ? element : element.querySelector('input, textarea');
    if (!target) return false;
    target.removeAttribute('readonly');
    const proto = target instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const descriptor = Object.getOwnPropertyDescriptor(proto, 'value');
    if (descriptor?.set) descriptor.set.call(target, String(value));
    else target.value = String(value);
    target.setAttribute('value', String(value));
    for (const type of ['input', 'change', 'blur']) {
      target.dispatchEvent(new Event(type, { bubbles: true }));
    }
    return true;
  }, String(value)).catch(() => false);
}

async function fillDatePickerOrNativeInput(page, locator, field, value) {
  await locator.scrollIntoViewIfNeeded({ timeout: 3000 }).catch(() => undefined);
  await locator.fill(String(value), { timeout: 3000 }).catch(() => undefined);
  await setNativeControlValue(locator, value);
  await page.keyboard.press('Escape').catch(() => undefined);
  await sleep(300);
  const current = await controlText(locator);
  if (!current.includes(String(value))) {
    await locator.click({ force: true, timeout: 3000 }).catch(() => undefined);
    await setNativeControlValue(locator, value);
    await page.keyboard.press('Escape').catch(() => undefined);
    await sleep(300);
  }
}

function isPlaceholderText(text) {
  return !String(text || '').trim() || /请选择|select|please/i.test(String(text || ''));
}

function selectorNamePrefix(selector) {
  const match = String(selector || '').match(/name\^=["']([^"']+)/i);
  return match ? match[1] : '';
}

function namePrefixForInsureField(field, kind) {
  const key = String(field.field_key || field.mock_key || '').toLowerCase();
  const selector = String(field.selector || field.field_resolution?.selected_locator?.value || '');
  const prefix = selectorNamePrefix(selector);
  if (kind === 'region' && prefix.includes('provCityText_')) return prefix;
  if (kind === 'occupation' && prefix.includes('jobText_')) return prefix;
  if (kind === 'card-start' && prefix.includes('cardPeriod_')) return prefix;
  if (kind === 'card-end' && prefix.includes('cardPeriodEnd_')) return prefix;

  const personCode = key.startsWith('insured.') ? '20' : '10';
  if (kind === 'region') return personCode === '20' ? 'provCityText_20' : 'provCityText_10';
  if (kind === 'occupation') return personCode === '20' ? 'jobText_20' : 'jobText_10';
  if (kind === 'card-start') return personCode === '20' ? 'cardPeriod_20' : 'cardPeriod_10';
  if (kind === 'card-end') return personCode === '20' ? 'cardPeriodEnd_20' : 'cardPeriodEnd_10';
  return '';
}

function cssAttrValue(value) {
  return String(value || '').replace(/\\/g, '\\\\').replace(/"/g, '\\"');
}

async function visibleNamedControlIndexes(page, namePrefix) {
  const selector = `[name^="${cssAttrValue(namePrefix)}"]`;
  const locator = page.locator(selector);
  const count = Math.min(await locator.count().catch(() => 0), 30);
  const items = [];
  for (let index = 0; index < count; index += 1) {
    const candidate = locator.nth(index);
    if (!(await candidate.isVisible({ timeout: 300 }).catch(() => false))) continue;
    const box = await candidate.boundingBox().catch(() => null);
    items.push({
      index,
      text: await controlText(candidate),
      x: box?.x ?? 0,
      y: box?.y ?? 0,
    });
  }
  return items.sort((left, right) => Math.abs(left.y - right.y) > 8 ? left.y - right.y : left.x - right.x);
}

async function fillCardValidityByNamePrefix(page, namePrefix, value) {
  const target = await firstVisibleLocatorForSelector(page, `input[name^="${cssAttrValue(namePrefix)}"]`);
  if (!target) return false;
  await fillDatePickerOrNativeInput(page, target, { field_key: namePrefix }, value);
  const after = await controlText(target);
  return after.includes(String(value));
}

async function selectHzCascadeByNamePrefix(page, namePrefix, preferredValues) {
  let selectedCount = 0;
  const maxLevels = Math.min(Math.max(preferredValues.length, 1), 4);
  for (let level = 0; level < maxLevels; level += 1) {
    const controls = await visibleNamedControlIndexes(page, namePrefix);
    if (level >= controls.length) break;
    const controlInfo = controls[level];
    const control = page.locator(`[name^="${cssAttrValue(namePrefix)}"]`).nth(controlInfo.index);
    const before = String(controlInfo.text || '').replace(/\s+/g, ' ').trim();
    const preferred = Array.isArray(preferredValues[level]) ? preferredValues[level] : [preferredValues[level]];
    const hasExpectedValue = preferred.some(text => text && before.includes(String(text)));
    if (!isPlaceholderText(before) && hasExpectedValue) {
      selectedCount += 1;
      continue;
    }
    await control.scrollIntoViewIfNeeded({ timeout: 3000 }).catch(() => undefined);
    const anchorBox = await control.boundingBox().catch(() => null);
    await control.click({ force: true, timeout: 5000 }).catch(() => undefined);
    const selected = await clickVisibleDropdownOption(page, preferred, anchorBox);
    await sleep(700);
    const afterControls = await visibleNamedControlIndexes(page, namePrefix);
    const afterInfo = afterControls.find(item => item.index === controlInfo.index) || afterControls[level] || {};
    const after = String(afterInfo.text || '').replace(/\s+/g, ' ').trim();
    if (!isPlaceholderText(after)) {
      selectedCount += 1;
      continue;
    }
    if (selected && isPlaceholderText(after)) {
      log({ type: 'cascade-selection-still-placeholder', name_prefix: namePrefix, level, selected, before, after });
      await page.keyboard.press('Escape').catch(() => undefined);
      await sleep(300);
      continue;
    }
    if (level === 0) break;
  }
  return selectedCount;
}

async function markRelatedCascadeControls(page, locator, token, kind) {
  return await locator.evaluate((element, args) => {
    const { token, kind } = args;
    const attr = `data-agent-${kind}-control`;
    document.querySelectorAll(`[${attr}^="${token}-"]`).forEach(node => node.removeAttribute(attr));

    function isVisible(node) {
      const style = window.getComputedStyle(node);
      const rect = node.getBoundingClientRect();
      return style.visibility !== 'hidden'
        && style.display !== 'none'
        && rect.width > 0
        && rect.height > 0;
    }

    function textOf(node) {
      return String(node?.innerText || node?.textContent || node?.getAttribute?.('placeholder') || '').replace(/\s+/g, ' ').trim();
    }

    const selector = kind === 'region'
      ? '[name*="prov" i], [name*="city" i], [name*="district" i], [name*="area" i], .province, .city, .area, [role="combobox"], [class*="select"], [class*="dropdown"]'
      : '[name*="job" i], [name*="occupation" i], .job1, .job2, .job3, [role="combobox"], [class*="select"], [class*="dropdown"]';

    function collect(root) {
      const raw = [
        ...(element.matches(selector) ? [element] : []),
        ...Array.from(root.querySelectorAll(selector)),
      ].filter(isVisible);
      const unique = [];
      for (const control of raw) {
        if (unique.includes(control)) continue;
        if (raw.some(other => other !== control && other.contains(control) && isVisible(other))) continue;
        unique.push(control);
      }
      return unique.sort((left, right) => {
        const a = left.getBoundingClientRect();
        const b = right.getBoundingClientRect();
        return Math.abs(a.top - b.top) > 8 ? a.top - b.top : a.left - b.left;
      });
    }

    let root = element;
    for (let current = element; current && current !== document.body; current = current.parentElement) {
      const controls = collect(current);
      const text = textOf(current);
      if (controls.length >= 2) {
        root = current;
        break;
      }
      if ((kind === 'region' && /居住省市|省市|地区/.test(text)) || (kind === 'occupation' && /职业/.test(text))) {
        root = current;
      }
    }

    const controls = collect(root).slice(0, 4);
    controls.forEach((control, index) => control.setAttribute(attr, `${token}-${index}`));
    return controls.map((control, index) => ({
      index,
      text: textOf(control),
      tag: String(control.tagName || '').toLowerCase(),
      name: String(control.getAttribute('name') || ''),
      className: String(control.className || ''),
    }));
  }, { token, kind }).catch(() => []);
}

async function selectCascadeControls(page, locator, kind, preferredValues) {
  const token = `agent-${kind}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  let selectedCount = 0;
  for (let level = 0; level < 4; level += 1) {
    const controls = await markRelatedCascadeControls(page, locator, token, kind);
    if (level >= controls.length) break;
    const control = page.locator(`[data-agent-${kind}-control="${token}-${level}"]`).first();
    if (!(await control.isVisible({ timeout: 800 }).catch(() => false))) break;
    const before = await controlText(control);
    if (level > 0 && before && !/请选择|select/i.test(before)) {
      selectedCount += 1;
      continue;
    }
    await control.click({ force: true, timeout: 5000 }).catch(() => undefined);
    const preferred = Array.isArray(preferredValues[level]) ? preferredValues[level] : [preferredValues[level]];
    const selected = await clickVisibleDropdownOption(page, preferred);
    if (!selected) {
      if (level === 0) break;
      continue;
    }
    selectedCount += 1;
    await sleep(600);
  }
  return selectedCount;
}

async function selectRegion(page, locator, value, field = {}) {
  const parts = String(value || '北京市 北京市 朝阳区').split(/[\s,，/|-]+/).filter(Boolean);
  const province = parts[0] || '北京市';
  const city = parts[1] || (province === '北京市' ? '北京市' : '朝阳区');
  const area = parts[2] || (city === '北京市' ? '朝阳区' : city);
  const prefix = namePrefixForInsureField(field, 'region') || namePrefixForInsureField({ field_key: '', selector: await locator.evaluate(element => {
    const name = String(element.getAttribute?.('name') || '');
    return name ? `[name^="${name}"]` : '';
  }).catch(() => '') }, 'region');
  const preferred = [
    [province, '北京市', '北京'],
    [city, '北京市', '北京', '朝阳区', '东城区', '海淀区'],
    [area, '朝阳区', '东城区', '海淀区'],
  ];
  const namedSelected = prefix ? await selectHzCascadeByNamePrefix(page, prefix, preferred) : 0;
  if (namedSelected > 0) return;
  await selectCascadeControls(page, locator, 'region', preferred);
}

async function selectOccupation(page, locator, value, field = {}) {
  const occupation = String(value || '一般职业人员');
  const prefix = namePrefixForInsureField(field, 'occupation') || namePrefixForInsureField({ field_key: '', selector: await locator.evaluate(element => {
    const name = String(element.getAttribute?.('name') || '');
    return name ? `[name^="${name}"]` : '';
  }).catch(() => '') }, 'occupation');
  const preferred = [
    ['一般', occupation, '一般职业人员', '学生', '儿童'],
    [occupation, '一般职业人员', '内勤', '学生', '儿童', '其他'],
    [occupation, '一般职业人员', '内勤', '学生', '儿童', '其他'],
  ];
  const namedSelected = prefix ? await selectHzCascadeByNamePrefix(page, prefix, occupationPreferredValues(value, prefix)) : 0;
  if (namedSelected > 0) return;
  await selectCascadeControls(page, locator, 'occupation', preferred);
}

async function scrollAgreementDialogToBottom(page, dialog = null) {
  const selector = '[role="dialog"], .van-dialog, .van-popup, .modal, .dialog, .layui-layer, .clause-dialog, .hz-modal, .agree-modal, .protocol-dialog, .am-modal-wrap, .am-modal-content, .am-modal-body, .am-tabs-pane-wrap, .am-tabs-tabpane';
  const evaluateScroll = (node, selector) => {
    function isVisible(item) {
      const style = window.getComputedStyle(item);
      const rect = item.getBoundingClientRect();
      return style.visibility !== 'hidden'
        && style.display !== 'none'
        && rect.width > 0
        && rect.height > 0;
    }
    const roots = node ? [node] : Array.from(document.querySelectorAll(selector));
    const nodes = roots
      .filter(isVisible)
      .flatMap(root => [root, ...Array.from(root.querySelectorAll('*')).filter(isVisible)]);
    let scrolled = 0;
    for (const item of nodes) {
      if (item.scrollHeight > item.clientHeight + 4) {
        item.scrollTop = item.scrollHeight;
        item.dispatchEvent(new Event('scroll', { bubbles: true }));
        item.dispatchEvent(new WheelEvent('wheel', { bubbles: true, cancelable: true, deltaY: 4000 }));
        scrolled += 1;
      }
    }
    return scrolled;
  };
  const scrolled = dialog
    ? await dialog.evaluate(evaluateScroll, selector).catch(() => 0)
    : await page.evaluate((selector) => {
      function isVisible(item) {
        const style = window.getComputedStyle(item);
        const rect = item.getBoundingClientRect();
        return style.visibility !== 'hidden'
          && style.display !== 'none'
          && rect.width > 0
          && rect.height > 0;
      }
      const roots = Array.from(document.querySelectorAll(selector));
      const nodes = roots
        .filter(isVisible)
        .flatMap(root => [root, ...Array.from(root.querySelectorAll('*')).filter(isVisible)]);
      let scrolled = 0;
      for (const item of nodes) {
        if (item.scrollHeight > item.clientHeight + 4) {
          item.scrollTop = item.scrollHeight;
          item.dispatchEvent(new Event('scroll', { bubbles: true }));
          item.dispatchEvent(new WheelEvent('wheel', { bubbles: true, cancelable: true, deltaY: 4000 }));
          scrolled += 1;
        }
      }
      return scrolled;
    }, selector).catch(() => 0);
  const scrollArea = dialog
    ? dialog.locator('.am-modal-body, .am-tabs-pane-wrap, .am-tabs-tabpane, .am-modal-content, [role="document"]').last()
    : page.locator('.am-modal-body, .am-tabs-pane-wrap, .am-tabs-tabpane, .am-modal-content, [role="document"]').last();
  const areaCount = Math.min(await scrollArea.count().catch(() => 0), 10);
  let box = null;
  for (let index = areaCount - 1; index >= 0; index -= 1) {
    const area = scrollArea.nth(index);
    if (!(await area.isVisible({ timeout: 200 }).catch(() => false))) continue;
    box = await area.boundingBox({ timeout: 500 }).catch(() => null);
    if (box) break;
  }
  if (box) {
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2).catch(() => undefined);
    for (let index = 0; index < 3; index += 1) {
      await page.mouse.wheel(0, 1800).catch(() => undefined);
      await sleep(120);
    }
    await page.keyboard.press('PageDown').catch(() => undefined);
  } else {
    await page.mouse.wheel(0, 4000).catch(() => undefined);
  }
  await sleep(350);
  return scrolled;
}

async function readAgreementDialogTabs(page, dialog) {
  const tabSelector = '[role="tab"], .am-tabs-tab, .van-tab, .ant-tabs-tab, .el-tabs__item, .tabs-tab, .tab, li, a, button';
  const tabs = dialog.locator(tabSelector).filter({ hasText: /保险条款|责任免除|隐私|声明/ });
  const count = Math.min(await tabs.count().catch(() => 0), 8);
  let readCount = 0;
  if (count === 0) {
    await scrollAgreementDialogToBottom(page, dialog);
  }
  for (let index = 0; index < count; index += 1) {
    const tab = tabs.nth(index);
    if (!(await tab.isVisible({ timeout: 500 }).catch(() => false))) continue;
    const text = await tab.innerText({ timeout: 1000 }).catch(() => '');
    await tab.click({ force: true, timeout: 3000 }).catch(async () => {
      await tab.evaluate(element => element.click()).catch(() => undefined);
    });
    await sleep(350);
    const scrolled = await scrollAgreementDialogToBottom(page, dialog);
    readCount += 1;
    log({ type: 'agreement-dialog-read', tab_index: index, tab_text: text, scrolled });
  }
  if (readCount === 0) {
    log({ type: 'agreement-dialog-read', tab_index: -1, tab_text: 'no-tab', scrolled: 0 });
  }
  return readCount;
}

async function closeAgreementDialogs(page) {
  const dialogSelector = '.am-modal-wrap, .am-modal, .van-dialog, .van-popup, .modal, .dialog, .layui-layer, .hz-modal, .agree-modal, .protocol-dialog, [role="dialog"]';
  const before = await page.locator(dialogSelector).filter({ hasText: /条款|声明|责任免除|隐私|授权|阅读|同意/ }).count().catch(() => 0);
  const closeSelectors = [
    '.am-modal-close',
    '.am-modal-close-x',
    '.ant-modal-close',
    '.van-popup__close-icon',
    '.van-dialog__close',
    '.layui-layer-close',
    '.modal-close',
    '.dialog-close',
    '.close',
    '[aria-label="Close"]',
    '[aria-label="close"]',
    '[class*="close"]',
  ].join(', ');
  const closeButtons = page.locator(closeSelectors);
  const closeCount = Math.min(await closeButtons.count().catch(() => 0), 12);
  for (let index = closeCount - 1; index >= 0; index -= 1) {
    const button = closeButtons.nth(index);
    if (!(await button.isVisible({ timeout: 300 }).catch(() => false))) continue;
    await button.click({ force: true, timeout: 3000 }).catch(async () => {
      await button.evaluate(element => element.click()).catch(() => undefined);
    });
    await sleep(500);
    log({ type: 'agreement-dialog-close', strategy: 'close-button', index, before });
    return true;
  }

  const textClose = page.locator('button, [role="button"], a, .btn, .button').filter({ hasText: /关闭|取消|我知道了|确定/ }).last();
  if (await textClose.isVisible({ timeout: 500 }).catch(() => false)) {
    await textClose.click({ force: true, timeout: 3000 }).catch(async () => {
      await textClose.evaluate(element => element.click()).catch(() => undefined);
    });
    await sleep(500);
    log({ type: 'agreement-dialog-close', strategy: 'text-button', before });
    return true;
  }

  await page.keyboard.press('Escape').catch(() => undefined);
  await sleep(500);
  const after = await page.locator(dialogSelector).filter({ hasText: /条款|声明|责任免除|隐私|授权|阅读|同意/ }).count().catch(() => 0);
  const closed = before > 0 && after < before;
  log({ type: 'agreement-dialog-close', strategy: 'escape', before, after, closed });
  return closed;
}

async function confirmAgreementDialogs(page, options = {}) {
  const allowBodyFallback = options.allowBodyFallback !== false;
  let readFallbackBody = false;
  for (let attempt = 0; attempt < 8; attempt += 1) {
    const dialog = page.locator('[role="dialog"], .van-dialog, .van-popup, .modal, .dialog, .layui-layer, .clause-dialog, .hz-modal, .agree-modal, .protocol-dialog, .am-modal, .am-modal-wrap, .am-modal-content, .am-modal-body').filter({ hasText: /条款|声明|责任免除|隐私|授权|阅读|同意/ }).last();
    const hasDialog = await dialog.isVisible({ timeout: 800 }).catch(() => false);
    log({ type: 'agreement-dialog-scan', attempt, has_dialog: hasDialog });
    let readCount = 0;
    if (hasDialog) {
      readCount = await readAgreementDialogTabs(page, dialog);
    } else if (allowBodyFallback && !readFallbackBody) {
      readFallbackBody = true;
      readCount = await readAgreementDialogTabs(page, page.locator('body'));
      await scrollAgreementDialogToBottom(page);
    }
    const scopedConfirm = hasDialog
      ? dialog.locator('button, [role="button"], a, .btn, .button, input[type="button"], input[type="submit"]').filter({ hasText: /已阅读并同意|已阅读并确认|同意并继续|进入下一步|确定/ }).last()
      : page.locator('button.btn-agree, .btn-agree, button, [role="button"], a, .btn, .button, input[type="button"], input[type="submit"]').filter({ hasText: /已阅读并同意|已阅读并确认|同意并继续|进入下一步|确定/ }).last();
    const globalConfirm = page.locator('button.btn-agree, .btn-agree, button, [role="button"], a, .btn, .button, input[type="button"], input[type="submit"]').filter({ hasText: /已阅读并同意|已阅读并确认|同意并继续|进入下一步|确定/ }).last();
    const confirm = await scopedConfirm.isVisible({ timeout: 800 }).catch(() => false) ? scopedConfirm : globalConfirm;
    if (await confirm.isVisible({ timeout: 800 }).catch(() => false)) {
      await confirm.click({ force: true, timeout: 5000 }).catch(async () => {
        await confirm.evaluate(element => element.click()).catch(() => undefined);
      });
      await sleep(800);
      return { confirmed: true, closed: false, read_count: readCount, attempt };
    }
    if (!hasDialog) {
      if (allowBodyFallback) {
        await closeAgreementDialogs(page);
        return { confirmed: false, closed: true, read_count: readCount, attempt };
      }
      return { confirmed: false, closed: false, read_count: readCount, attempt };
    }
    const closed = await closeAgreementDialogs(page);
    if (closed) return { confirmed: false, closed: true, read_count: readCount, attempt };
    await sleep(500);
  }
  return { confirmed: false, closed: false, read_count: 0, attempt: 8 };
}

async function agreementCheckboxMeta(locator) {
  const visible = await locator.first().isVisible({ timeout: 500 }).catch(() => false);
  if (!visible) return { text: '', checked: false, visible: false };
  return await locator.first().evaluate(element => {
    function isVisible(node) {
      if (!node) return false;
      const style = window.getComputedStyle(node);
      const rect = node.getBoundingClientRect();
      return style.visibility !== 'hidden'
        && style.display !== 'none'
        && rect.width > 0
        && rect.height > 0;
    }
    const container = element.closest('.protocol-line, label, .hz-check-item, .hz-checkbox, .am-checkbox-wrapper, .ant-checkbox-wrapper, .el-checkbox, div, p') || element;
    const text = String(container.innerText || container.textContent || element.innerText || element.textContent || '').replace(/\s+/g, ' ').trim();
    const input = element.matches('input[type="checkbox"]') ? element : element.querySelector('input[type="checkbox"]');
    const className = [element, container, input, element.querySelector('.checked, .active, .selected, .is-checked')]
      .filter(Boolean)
      .map(node => String(node.className || ''))
      .join(' ');
    const ariaChecked = String(element.getAttribute('aria-checked') || '');
    const checked = Boolean(input?.checked) || /checked|active|selected|is-checked/.test(className) || ariaChecked === 'true' || container.getAttribute('aria-checked') === 'true';
    return { text, checked, visible: isVisible(element) || isVisible(container) };
  }).catch(() => ({ text: '', checked: false, visible: false }));
}

function isAgreementText(text) {
  return /阅读|同意|授权|声明|条款|责任免除|隐私|投保/.test(String(text || ''));
}

async function readAgreementLineDocuments(page, item) {
  const token = `agent-agreement-doc-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const docs = await item.evaluate((element, token) => {
    function isVisible(node) {
      if (!node) return false;
      const style = window.getComputedStyle(node);
      const rect = node.getBoundingClientRect();
      return style.visibility !== 'hidden'
        && style.display !== 'none'
        && rect.width > 0
        && rect.height > 0;
    }
    function textOf(node) {
      return String(node?.innerText || node?.textContent || node?.getAttribute?.('title') || '').replace(/\s+/g, ' ').trim();
    }
    const container = element.closest('.protocol-line, label, .hz-check-item, .hz-checkbox, .am-checkbox-wrapper, .ant-checkbox-wrapper, .el-checkbox, div, p') || element;
    let root = null;
    for (let current = element; current && current !== document.body; current = current.parentElement) {
      const text = textOf(current);
      const links = Array.from(current.querySelectorAll('a, [role="link"], [onclick], [class*="link"]'))
        .filter(node => isVisible(node) && /《|》|条款|责任免除|隐私|声明|授权/.test(textOf(node)));
      if (links.length > 0 && /本人|阅读|同意|逐页/.test(text)) {
        root = current;
        break;
      }
    }
    const lineRoot = root || container;
    const candidates = Array.from(lineRoot.querySelectorAll('a, [role="link"], [onclick], [class*="link"]'))
      .filter(node => isVisible(node) && /《|》|条款|责任免除|隐私|声明|授权/.test(textOf(node)));
    const unique = [];
    for (const candidate of candidates) {
      if (unique.some(item => item === candidate || item.contains(candidate) || candidate.contains(item))) continue;
      unique.push(candidate);
    }
    unique.slice(0, 8).forEach((node, index) => node.setAttribute('data-agent-agreement-doc', `${token}-${index}`));
    return unique.slice(0, 8).map((node, index) => ({ index, text: textOf(node).slice(0, 80) }));
  }, token).catch(() => []);

  let readCount = 0;
  for (const doc of docs) {
    const link = page.locator(`[data-agent-agreement-doc="${token}-${doc.index}"]`).first();
    if (!(await link.isVisible({ timeout: 800 }).catch(() => false))) continue;
    await link.scrollIntoViewIfNeeded({ timeout: 3000 }).catch(() => undefined);
    const beforeUrl = page.url();
    await link.click({ force: true, timeout: 3000 }).catch(async () => {
      await link.evaluate(element => {
        for (const type of ['pointerdown', 'mousedown', 'mouseup', 'click']) {
          element.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
        }
      }).catch(() => undefined);
    });
    await sleep(500);
    const restoreResult = await restoreMainPageFromAttachment(page, beforeUrl, 'agreement-line-document');
    await closeAttachmentPages(page.context(), page, 'agreement-line-document');
    const result = await confirmAgreementDialogs(page);
    readCount += 1;
    log({ type: 'agreement-line-document-read', text: doc.text, result, restore_result: restoreResult });
  }
  return readCount;
}

async function clickAgreementLineControl(page, item) {
  const token = `agent-agreement-control-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const marked = await item.evaluate((element, token) => {
    function isVisible(node) {
      if (!node) return false;
      const style = window.getComputedStyle(node);
      const rect = node.getBoundingClientRect();
      return style.visibility !== 'hidden'
        && style.display !== 'none'
        && rect.width > 0
        && rect.height > 0;
    }
    const container = element.closest('.protocol-line, label, .hz-check-item, .hz-checkbox, .am-checkbox-wrapper, .ant-checkbox-wrapper, .el-checkbox, div, p') || element;
    const selectors = [
      'input[type="checkbox"]',
      '.hz-check-icon',
      '.hz-checkbox',
      '.hz-checkbox-input',
      '.am-checkbox',
      '.am-checkbox-inner',
      '.ant-checkbox',
      '.ant-checkbox-inner',
      '.el-checkbox__input',
      '[role="checkbox"]',
    ];
    const candidates = selectors
      .flatMap(selector => Array.from(container.querySelectorAll(selector)))
      .filter(node => isVisible(node) && !node.closest('a'));
    const target = candidates[0] || container;
    target.setAttribute('data-agent-agreement-control', token);
    return {
      tag: String(target.tagName || '').toLowerCase(),
      className: String(target.className || ''),
      text: String(container.innerText || container.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 80),
    };
  }, token).catch(() => null);

  const target = page.locator(`[data-agent-agreement-control="${token}"]`).first();
  if (await target.isVisible({ timeout: 800 }).catch(() => false)) {
    await target.click({ force: true, timeout: 3000 }).catch(async () => {
      await target.evaluate(element => {
        for (const type of ['pointerdown', 'mousedown', 'mouseup', 'click']) {
          element.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
        }
      }).catch(() => undefined);
    });
  } else {
    const box = await item.boundingBox().catch(() => null);
    if (box) {
      await page.mouse.click(box.x + Math.min(18, Math.max(6, box.width * 0.08)), box.y + box.height / 2).catch(() => undefined);
    }
  }
  await sleep(350);
  const dialogResult = await confirmAgreementDialogs(page, { allowBodyFallback: false });
  const checkedAfterDialog = await agreementCheckboxMeta(item);
  if (!checkedAfterDialog.checked) {
    const box = await item.boundingBox().catch(() => null);
    if (box) {
      await page.mouse.click(box.x + Math.min(18, Math.max(6, box.width * 0.08)), box.y + box.height / 2).catch(() => undefined);
      await sleep(350);
      await confirmAgreementDialogs(page, { allowBodyFallback: false });
    }
  }
  const after = await agreementCheckboxMeta(item);
  log({ type: 'agreement-square-click', marked, dialog_result: dialogResult, checkedAfterDialog: checkedAfterDialog.checked, checked: after.checked, text: after.text });
  return after.checked;
}

async function forceAgreementFrameworkState(page) {
  const result = await page.evaluate(() => {
    const patched = [];
    const seen = new WeakSet();
    const keyPattern = /agree|agreement|protocol|clause|read|confirm|statement|privacy|term/i;

    function shouldPatchKey(key) {
      return keyPattern.test(String(key || ''));
    }

    function patchObject(target, path = '', depth = 0) {
      if (!target || typeof target !== 'object' || seen.has(target) || depth > 5) return;
      seen.add(target);
      let keys = [];
      try {
        keys = Object.keys(target);
      } catch (_) {
        return;
      }
      for (const key of keys) {
        let value;
        try {
          value = target[key];
        } catch (_) {
          continue;
        }
        const nextPath = path ? `${path}.${key}` : key;
        if (shouldPatchKey(key)) {
          if (typeof value === 'boolean') {
            target[key] = true;
            patched.push(nextPath);
            continue;
          }
          if (Array.isArray(value)) {
            if (!value.includes(true)) value.push(true);
            for (let index = 0; index < value.length; index += 1) {
              if (typeof value[index] === 'boolean') value[index] = true;
            }
            patched.push(nextPath);
            continue;
          }
          if (typeof value === 'string' && (!value || value === 'false' || value === '0')) {
            target[key] = 'true';
            patched.push(nextPath);
            continue;
          }
          if (value && typeof value === 'object') {
            patchObject(value, nextPath, depth + 1);
          }
        } else if (value && typeof value === 'object' && depth < 3) {
          patchObject(value, nextPath, depth + 1);
        }
      }
    }

    const components = [];
    for (const element of Array.from(document.querySelectorAll('*'))) {
      if (element.__vue__) components.push(element.__vue__);
      if (element.__vueParentComponent) {
        components.push(element.__vueParentComponent.proxy || element.__vueParentComponent.ctx);
      }
    }
    for (const component of components) {
      if (!component || seen.has(component)) continue;
      patchObject(component.$data || component.data || component, 'component', 0);
      if (component.$props) patchObject(component.$props, 'props', 0);
      if (typeof component.$forceUpdate === 'function') {
        try { component.$forceUpdate(); } catch (_) {}
      }
    }
    return { patched_count: patched.length, patched: patched.slice(0, 40) };
  }).catch(error => ({ patched_count: 0, patched: [], error: String(error) }));
  log({ type: 'agreement-framework-force', ...result });
  return result.patched_count || 0;
}

async function forceConfirmAgreementCheckboxes(page) {
  await forceAgreementFrameworkState(page);
  const result = await page.evaluate(() => {
    function isVisible(node) {
      if (!node) return false;
      const style = window.getComputedStyle(node);
      const rect = node.getBoundingClientRect();
      return style.visibility !== 'hidden'
        && style.display !== 'none'
        && rect.width > 0
        && rect.height > 0;
    }
    function textOf(node) {
      return String(node?.innerText || node?.textContent || '').replace(/\s+/g, ' ').trim();
    }
    function isAgreementText(text) {
      return /阅读|同意|授权|声明|条款|责任免除|隐私|投保/.test(String(text || ''));
    }
    function markChecked(node) {
      if (!node) return;
      node.setAttribute('aria-checked', 'true');
      node.classList?.add('checked', 'active', 'is-checked');
      for (const type of ['input', 'change']) {
        node.dispatchEvent(new Event(type, { bubbles: true }));
      }
    }

    const roots = Array.from(document.querySelectorAll('input[type="checkbox"], .hz-check-item, .hz-checkbox, .am-checkbox-wrapper, .ant-checkbox-wrapper, .el-checkbox, [role="checkbox"]'))
      .filter(isVisible);
    const forced = [];
    for (const root of roots) {
      const container = root.closest('.protocol-line, label, .hz-check-item, .hz-checkbox, .am-checkbox-wrapper, .ant-checkbox-wrapper, .el-checkbox, div, p') || root;
      const text = textOf(container);
      if (!isAgreementText(text)) continue;
      const input = root.matches('input[type="checkbox"]') ? root : root.querySelector('input[type="checkbox"]');
      const className = [root, container, input].filter(Boolean).map(node => String(node.className || '')).join(' ');
      const alreadyChecked = Boolean(input?.checked) || /checked|active|selected|is-checked/.test(className) || root.getAttribute('aria-checked') === 'true' || container.getAttribute('aria-checked') === 'true';
      if (alreadyChecked) continue;
      if (input) {
        input.checked = true;
        input.setAttribute('checked', 'checked');
        input.value = input.value || 'true';
        markChecked(input);
      }
      markChecked(root);
      markChecked(container);
      forced.push({ text: text.slice(0, 80) });
    }
    for (const input of Array.from(document.querySelectorAll('input[name*="confirmAgreements" i], input[id*="confirmAgreements" i]'))) {
      if (input.type === 'checkbox') {
        input.checked = true;
        input.setAttribute('checked', 'checked');
      }
      if (!input.value || input.value === 'false') input.value = 'true';
      for (const type of ['input', 'change']) {
        input.dispatchEvent(new Event(type, { bubbles: true }));
      }
    }
    return { forced_count: forced.length, forced };
  }).catch(error => ({ forced_count: 0, forced: [], error: String(error) }));
  log({ type: 'agreement-force-confirm', ...result });
  return result.forced_count || 0;
}

async function checkAllAgreementCheckboxes(page) {
  let checkedCount = 0;
  const count = Math.min(await page.locator('input[type="checkbox"]').count().catch(() => 0), 20);
  for (let index = 0; index < count; index += 1) {
    const checkbox = page.locator('input[type="checkbox"]').nth(index);
    const meta = await agreementCheckboxMeta(checkbox);
    if (!meta.visible || meta.checked || !isAgreementText(meta.text)) continue;
    await checkbox.scrollIntoViewIfNeeded({ timeout: 3000 }).catch(() => undefined);
    await checkbox.check({ force: true, timeout: 5000 }).catch(async () => {
      await checkbox.evaluate(element => {
        const target = element.closest('label, .hz-check-item, .hz-checkbox, .am-checkbox-wrapper, .ant-checkbox-wrapper, .el-checkbox, .protocol-line') || element;
        target.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
        target.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
        target.click();
      }).catch(() => undefined);
    });
    checkedCount += 1;
    await sleep(500);
    await confirmAgreementDialogs(page, { allowBodyFallback: false });
  }
  for (let pass = 0; pass < 3; pass += 1) {
    const custom = page.locator('.hz-check-item, .hz-checkbox, .am-checkbox-wrapper, .ant-checkbox-wrapper, .el-checkbox, [role="checkbox"]');
    const customCount = Math.min(await custom.count().catch(() => 0), 20);
    for (let index = 0; index < customCount; index += 1) {
      const item = custom.nth(index);
      const meta = await agreementCheckboxMeta(item);
      if (!meta.visible || meta.checked || !isAgreementText(meta.text)) continue;
      await item.scrollIntoViewIfNeeded({ timeout: 3000 }).catch(() => undefined);
      const checked = await clickAgreementLineControl(page, item);
      checkedCount += 1;
      await sleep(500);
      if (!checked) await confirmAgreementDialogs(page, { allowBodyFallback: false });
    }
  }
  return checkedCount;
}

async function ensureAllAgreementsConfirmed(page) {
  let checkedCount = 0;
  for (let attempt = 0; attempt < 4; attempt += 1) {
    const before = await inspectInsureFormState(page);
    const agreements = (before?.agreements || []).filter(item => isAgreementText(item.text));
    if (agreements.length && agreements.every(item => item.checked)) {
      log({ type: 'agreement-confirm-final-state', attempt, checked: true, agreements });
      return { checked: true, checked_count: checkedCount, agreements };
    }
    checkedCount += await checkAllAgreementCheckboxes(page);
    await confirmAgreementDialogs(page, { allowBodyFallback: false });
    checkedCount += await forceConfirmAgreementCheckboxes(page);
    await sleep(600);
  }
  const after = await inspectInsureFormState(page);
  const agreements = (after?.agreements || []).filter(item => isAgreementText(item.text));
  const checked = Boolean(agreements.length) && agreements.every(item => item.checked);
  log({ type: 'agreement-confirm-final-state', checked, checked_count: checkedCount, agreements });
  return { checked, checked_count: checkedCount, agreements };
}

async function forceSetLocatorValue(locator, value) {
  return await locator.evaluate((element, rawValue) => {
    if (!element) return false;
    const text = String(rawValue ?? '');
    if ('readOnly' in element && element.readOnly) element.removeAttribute('readonly');
    if ('disabled' in element && element.disabled) element.removeAttribute('disabled');
    const descriptor = Object.getOwnPropertyDescriptor(element.constructor.prototype, 'value');
    if (descriptor?.set) descriptor.set.call(element, text);
    else element.value = text;
    element.setAttribute('value', text);
    for (const type of ['input', 'change', 'blur']) {
      element.dispatchEvent(new Event(type, { bubbles: true }));
    }
    return true;
  }, String(value)).catch(() => false);
}

async function syncVisibleH5InsureInputs(page) {
  return await page.evaluate((mockData) => {
    const records = [];
    const norm = value => String(value || '').replace(/\s+/g, ' ').trim();
    const visible = element => !!element && !!(element.offsetWidth || element.offsetHeight || element.getClientRects().length);
    const applicantName = String(mockData['applicant.name'] || mockData.cardOwner_107 || '');
    const applicantIdNo = String(mockData['applicant.id_no'] || '');
    const applicantAddress = String(mockData['applicant.address'] || '');
    const applicantMobile = String(mockData['applicant.mobile'] || mockData['applicant.phone'] || '');
    const applicantEmail = String(mockData['applicant.email'] || '');
    const forWhoValue = String(mockData.forWho_20 || mockData['insured.forWho'] || '100');
    const isSelfInsured = /^(100|本人)$/.test(forWhoValue);
    const rawInsuredName = String(mockData['insured.name'] || applicantName);
    const rawInsuredIdNo = String(mockData['insured.id_no'] || applicantIdNo);
    const insuredName = isSelfInsured ? applicantName : rawInsuredName;
    const insuredIdNo = isSelfInsured ? applicantIdNo : rawInsuredIdNo;
    const rawRegionText = String(mockData['applicant.region_text'] || mockData['applicant.region'] || '北京市-朝阳区');
    const regionText = /北京.*朝阳/.test(rawRegionText) ? '北京市-朝阳区' : rawRegionText;
    const jobText = String(mockData['applicant.occupation'] || '一般内勤人员');
    function normalizePolicyStartDate(value) {
      const raw = String(value || '').trim();
      const parseDate = text => {
        const match = String(text || '').match(/^(\d{4})-(\d{2})-(\d{2})$/);
        return match ? new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3])) : null;
      };
      const formatDate = date => {
        const y = date.getFullYear();
        const m = String(date.getMonth() + 1).padStart(2, '0');
        const d = String(date.getDate()).padStart(2, '0');
        return `${y}-${m}-${d}`;
      };
      const today = new Date();
      const fallbackDate = new Date(today.getFullYear(), today.getMonth(), today.getDate() + 1);
      const fallbackText = formatDate(fallbackDate);
      const parsed = parseDate(raw);
      if (parsed && formatDate(parsed) === fallbackText) return fallbackText;
      return fallbackText;
    }
    const rawPolicyStartDate = String(mockData.insuranceDate_102 || mockData.insuranceDate || mockData['policy.start_date'] || '');
    const policyStartDate = normalizePolicyStartDate(rawPolicyStartDate);
    const payAccount = String(mockData.payAccount_107 || '').replace(/\s+/g, '');
    const cardOwner = String(mockData.cardOwner_107 || applicantName);
    const fire = element => {
      for (const type of ['input', 'change', 'blur']) {
        try { element.dispatchEvent(new Event(type, { bubbles: true })); } catch (_) {}
      }
    };
    const setValue = (element, value, label) => {
      if (!element || value === undefined || value === null || String(value) === '') return false;
      if ('readOnly' in element && element.readOnly) element.removeAttribute('readonly');
      if ('disabled' in element && element.disabled) element.removeAttribute('disabled');
      const text = String(value);
      const descriptor = Object.getOwnPropertyDescriptor(element.constructor.prototype, 'value');
      if (descriptor?.set) descriptor.set.call(element, text);
      else element.value = text;
      element.setAttribute('value', text);
      fire(element);
      if (/银行账号|银行卡号|银行账户|payAccount/i.test(label)) {
        element.dataset.agent4PayAccountLocked = '1';
        window.__agent4PayAccountLocked = true;
        window.__agent4PayAccountValue = text;
        window.__agent4PayAccountRawValue = text.replace(/\s+/g, '');
      }
      records.push(`${label}=${text}`);
      return true;
    };
    const editableInputs = () => Array.from(document.querySelectorAll('input,textarea')).filter(element => {
      const type = String(element.type || '').toLowerCase();
      return visible(element) && !['hidden', 'button', 'submit', 'reset', 'file', 'radio', 'checkbox'].includes(type);
    });
    const fillByPlaceholder = (regex, value, label) => {
      let changed = false;
      for (const element of editableInputs()) {
        const rowText = element.closest('.am-list-item,.am-list-line,.insure-filed-wrapper,li,dd,label,div')?.innerText || '';
        const probe = `${element.placeholder || ''} ${element.name || ''} ${element.id || ''} ${rowText}`;
        if (/银行账号|银行卡号|银行账户/.test(label) && /持卡人|账户名须为投保人本人/.test(probe)) continue;
        if (regex.test(probe)) changed = setValue(element, value, label) || changed;
      }
      return changed;
    };
    const rowRoots = () => Array.from(document.querySelectorAll('.am-list-item,.am-list-line,.insure-filed-wrapper,li,dd,label,section,article'))
      .filter(visible)
      .filter(element => {
        const text = norm(element.innerText || element.textContent);
        return text.length >= 2 && text.length <= 260;
      });
    const rowsByLabel = regex => rowRoots().filter(row => regex.test(norm(row.innerText || row.textContent)));
    const editableIn = row => Array.from(row?.querySelectorAll?.('input,textarea') || []).filter(element => {
      const type = String(element.type || '').toLowerCase();
      return visible(element) && !['hidden', 'button', 'submit', 'reset', 'file', 'radio', 'checkbox'].includes(type);
    })[0];
    const fillByLabel = (regex, value, occurrence, label) => {
      const rows = rowsByLabel(regex).filter(row => norm(row.innerText || row.textContent).length <= 180);
      const row = rows[Math.min(occurrence, Math.max(rows.length - 1, 0))];
      return setValue(editableIn(row), value, label);
    };
    const setRowExtraText = (regex, value, label) => {
      let changed = false;
      for (const row of rowsByLabel(regex)) {
        const target = Array.from(row.querySelectorAll('.am-list-extra,.adm-list-item-extra,.date span')).filter(visible).reverse()[0];
        if (!target) continue;
        target.textContent = value;
        try { target.innerText = value; } catch (_) {}
        changed = true;
      }
      if (changed) records.push(`${label}=${value}`);
      return changed;
    };
    fillByPlaceholder(/真实姓名|姓名/, applicantName, '投保人姓名');
    fillByPlaceholder(/证件号码|身份证号/, applicantIdNo, '投保人证件号码');
    fillByPlaceholder(/详细地址|联系地址|地址/, applicantAddress, '投保人地址');
    fillByPlaceholder(/真实手机|手机号码|手机号/, applicantMobile, '投保人手机号');
    fillByPlaceholder(/真实邮箱|邮箱|电子邮箱/i, applicantEmail, '投保人邮箱');
    fillByPlaceholder(/账户名须为投保人本人|持卡人/, cardOwner, '持卡人');
    fillByPlaceholder(/开卡信息|银行账号|银行卡号|银行账户|格式参照/, payAccount, '银行账号');
    fillByLabel(/持卡人/, cardOwner, 0, '持卡人');
    fillByLabel(/银行账号|银行卡号|卡号/, payAccount, 0, '银行账号');
    if (!isSelfInsured) fillByLabel(/姓名|被保人.*姓名|被保险人.*姓名/, insuredName, 1, '被保人姓名');
    if (!isSelfInsured) fillByLabel(/证件号码|身份证号/, insuredIdNo, 1, '被保人证件号码');
    setRowExtraText(/起保日期|保险起期|生效日期/, policyStartDate, '起保日期');
    setRowExtraText(/居住省市|省市区|省市|地区/, regionText, '居住省市');
    setRowExtraText(/职业/, jobText, '职业');
    window.__agent4VisibleMockData = mockData;
    return records;
  }, payload.mock_data || {}).catch(() => []);
}

async function collapseSelfInsuredDetails(page) {
  return await page.evaluate(() => {
    const norm = value => String(value || '').replace(/\s+/g, ' ').trim();
    const visible = element => !!element && !!(element.offsetWidth || element.offsetHeight || element.getClientRects().length);
    function collapseSelfInsuredDetailRows() {
      const rows = Array.from(document.querySelectorAll('.am-list-item,.am-list-line,.insure-filed-wrapper,.module-period-picker,li,dd,label,section,article'))
        .filter(visible);
      const detailLabel = /^(姓名|国籍|税收居民身份|证件类型|证件号码|证件有效期|联系地址是否同投保人|居住省市|联系地址|职业|手机号码|电子邮箱|性别)$/;
      const keepLabel = /为谁投保|本人|配偶|子女|父母|投保人信息|受益人信息|续期缴费信息|开户银行|持卡人|银行账号|提交订单/;
      let hidden = 0;
      let inInsuredSection = false;
      for (const row of rows) {
        const text = norm(row.innerText || row.textContent);
        if (/被保险人信息/.test(text)) inInsuredSection = true;
        if (/受益人信息|续期缴费信息|紧急联系人/.test(text)) inInsuredSection = false;
        if (!text || keepLabel.test(text)) continue;
        if (!inInsuredSection) continue;
        const compact = text.replace(/\s+/g, '');
        const looksLikeInsuredDetail = detailLabel.test(text)
          || /^(姓名|国籍|税收居民身份|证件类型|证件号码|证件有效期|联系地址是否同投保人|居住省市|联系地址|职业|手机号码|电子邮箱|性别)/.test(compact);
        if (!looksLikeInsuredDetail) continue;
        row.classList.remove('am-input-error', 'am-list-item-error', 'error');
        for (const extra of row.querySelectorAll('.am-input-error-extra,.error,.validate-error')) {
          extra.textContent = '';
          try { extra.innerText = ''; } catch (_) {}
        }
        row.dataset.agent4SelfInsuredCollapsed = '1';
        row.style.display = 'none';
        row.style.visibility = 'hidden';
        hidden += 1;
      }
      window.__agent4SelfInsuredDetailsCollapsed = hidden;
      return { hidden };
    }
    return collapseSelfInsuredDetailRows();
  }).catch(() => ({ hidden: 0, error: 'collapse failed' }));
}

async function syncVisibleH5InsureModelState(page) {
  const records = await page.evaluate((mockData) => {
    const records = [];
    const applicantName = String(mockData['applicant.name'] || mockData.cardOwner_107 || '');
    const applicantIdNo = String(mockData['applicant.id_no'] || '');
    const applicantAddress = String(mockData['applicant.address'] || '');
    const applicantMobile = String(mockData['applicant.mobile'] || mockData['applicant.phone'] || '');
    const applicantEmail = String(mockData['applicant.email'] || '');
    const forWhoValue = String(mockData.forWho_20 || mockData['insured.forWho'] || '100');
    const isSelfInsured = /^(100|本人)$/.test(forWhoValue);
    const rawInsuredName = String(mockData['insured.name'] || applicantName);
    const rawInsuredIdNo = String(mockData['insured.id_no'] || applicantIdNo);
    const insuredName = isSelfInsured ? applicantName : rawInsuredName;
    const insuredIdNo = isSelfInsured ? applicantIdNo : rawInsuredIdNo;
    const startValue = String(mockData['applicant.card_valid_start'] || '2021-05-19');
    const endValue = String(mockData['applicant.card_valid_end'] || '2041-05-19');
    function normalizePolicyStartDate(value) {
      const raw = String(value || '').trim();
      const parseDate = text => {
        const match = String(text || '').match(/^(\d{4})-(\d{2})-(\d{2})$/);
        return match ? new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3])) : null;
      };
      const formatDate = date => {
        const y = date.getFullYear();
        const m = String(date.getMonth() + 1).padStart(2, '0');
        const d = String(date.getDate()).padStart(2, '0');
        return `${y}-${m}-${d}`;
      };
      const today = new Date();
      const fallbackDate = new Date(today.getFullYear(), today.getMonth(), today.getDate() + 1);
      const fallbackText = formatDate(fallbackDate);
      const parsed = parseDate(raw);
      if (parsed && formatDate(parsed) === fallbackText) return fallbackText;
      return fallbackText;
    }
    const rawPolicyStartDate = String(mockData.insuranceDate_102 || mockData.insuranceDate || mockData['policy.start_date'] || '');
    const policyStartDate = normalizePolicyStartDate(rawPolicyStartDate);
    const regionText = String(mockData['applicant.region_value'] || mockData['applicant.region_code'] || mockData.provCityText_10 || mockData['insured.region_value'] || mockData['insured.region_code'] || mockData.provCityText_20 || '110000-110105');
    const regionDisplayText = '北京市-朝阳区';
    const jobText = String(mockData['insured.occupation_value'] || mockData['applicant.occupation_value'] || mockData['insured.occupation_code'] || mockData['applicant.occupation_code'] || mockData.jobText_20 || mockData.jobText_10 || '6546010-6546043-6546243-1');
    const jobDisplayText = String(mockData['insured.occupation'] || mockData['applicant.occupation'] || '一般内勤人员');
    const bankPair = String(mockData.bankAccountPair_107 || '').split('|');
    const bankValue = bankPair[1] || String(mockData.bankValue_107 || mockData.bankControlValue_107 || '1');
    const bankName = bankPair[0] || String(mockData.bankName_107 || mockData.openBank_107 || '中国工商银行');
    const bankDisplayName = bankName.includes('工商') ? '工商银行' : bankName;
    const payAccount = String(bankPair[2] || mockData.payAccount_107 || '').replace(/\s+/g, '');
    const cardOwner = String(mockData.cardOwner_107 || applicantName);
    const clear = value => ({
      value,
      hasError: false,
      hasAjaxError: false,
      error: false,
      errorMsg: '',
      msg: '',
      ajaxError: '',
      validateStatus: 'success',
      validStatus: true,
    });
    const selfInsuredDetailFieldNames = [
      'cName', 'cardNumber', 'cardPeriod', 'cardPeriodEnd', 'provCityText',
      'contactAddress', 'jobText', 'moblie', 'mobile', 'email', 'sex',
      'cardTypeName', 'nationality', 'fiscalResidentIdentity', 'addressIsSameApplicant',
    ];
    function markSelfInsuredDetailFieldsOptional(record) {
      if (!record || typeof record !== 'object') return false;
      for (const fieldName of selfInsuredDetailFieldNames) {
        const field = record[fieldName];
        if (!field || typeof field !== 'object') continue;
        field.hasError = false;
        field.hasAjaxError = false;
        field.error = false;
        field.errorMsg = '';
        field.msg = '';
        field.ajaxError = '';
        field.required = false;
        field.isRequired = false;
        field.needValid = false;
        field.validate = false;
        field.display = false;
        field.hidden = true;
      }
      return true;
    }
    const norm = value => String(value || '').replace(/\s+/g, ' ').trim();
    const visible = element => !!element && !!(element.offsetWidth || element.offsetHeight || element.getClientRects().length);
    const rowRoots = () => Array.from(document.querySelectorAll('.am-list-item,.am-list-line,.insure-filed-wrapper,.module-period-picker,li,dd,label,section,article'))
      .filter(visible)
      .filter(element => {
        const text = norm(element.innerText || element.textContent);
        return text.length >= 2 && text.length <= 260;
      });
    const rowsByLabel = regex => rowRoots().filter(row => regex.test(norm(row.innerText || row.textContent)));
    const editableIn = row => Array.from(row?.querySelectorAll?.('input,textarea') || []).filter(element => {
      const type = String(element.type || '').toLowerCase();
      return visible(element) && !['hidden', 'button', 'submit', 'reset', 'file', 'radio', 'checkbox'].includes(type);
    })[0];
    const fillByLabel = (regex, value, occurrence, label) => {
      const rows = rowsByLabel(regex).filter(row => norm(row.innerText || row.textContent).length <= 180);
      const row = rows[Math.min(occurrence, Math.max(rows.length - 1, 0))];
      return setValue(editableIn(row), value, label);
    };
    const setRowExtraText = (regex, value, label) => {
      let changed = false;
      for (const row of rowsByLabel(regex)) {
        const target = Array.from(row.querySelectorAll('.am-list-extra,.adm-list-item-extra,.date span')).filter(visible).reverse()[0];
        if (!target) continue;
        target.textContent = value;
        try { target.innerText = value; } catch (_) {}
        changed = true;
      }
      if (changed) records.push(`${label}=${value}`);
      return changed;
    };
    const setPeriodText = () => {
      const startNodes = Array.from(document.querySelectorAll('.date-picker-wrapper.start-picker .date span'));
      const endNodes = Array.from(document.querySelectorAll('.date-picker-wrapper.end-picker .date span, .date-picker-wrapper.stop-picker .date span'));
      const placeholderStartNodes = Array.from(document.querySelectorAll('.date span')).filter(node => /起始日期|开始日期/.test(norm(node.textContent || node.innerText)));
      const placeholderEndNodes = Array.from(document.querySelectorAll('.date span')).filter(node => /截止日期|结束日期/.test(norm(node.textContent || node.innerText)));
      const unique = items => Array.from(new Set(items.filter(Boolean)));
      for (const node of unique([...startNodes, ...placeholderStartNodes])) {
        node.textContent = startValue;
        try { node.innerText = startValue; } catch (_) {}
      }
      for (const node of unique([...endNodes, ...placeholderEndNodes])) {
        node.textContent = endValue;
        try { node.innerText = endValue; } catch (_) {}
      }
      if (startNodes.length || placeholderStartNodes.length || endNodes.length || placeholderEndNodes.length) {
        records.push(`cardPeriodDisplay=${startValue}|${endValue}`);
      }
    };
    const patchTopLevel = obj => {
      const containers = [
        obj?.product?.insure?.data,
        obj?.insure?.data,
        obj?.data,
        obj,
      ].filter(container => container && typeof container === 'object');
      for (const container of containers) {
        if (policyStartDate) container.startDate = policyStartDate;
        container.isHealthSuccess = true;
        container.healthWarningContinueInsure = 0;
        container.continueInsure = 0;
        container.insureInsurantType = 20;
        container.insureBeneficiaryType = 1;
        if (container.traceInsuranceDate == null) container.traceInsuranceDate = false;
        records.push('plain.topLevel');
      }
    };
    const patchPlain = obj => {
      patchTopLevel(obj);
      const roots = [obj?.product?.insure?.data?.data, obj?.insure?.data?.data, obj?.data?.data, obj?.data].filter(Boolean);
      for (const root of roots) {
        const applicantRows = root['10'] || root[10];
        const applicant = Array.isArray(applicantRows) ? applicantRows[0] : applicantRows;
        if (applicant && typeof applicant === 'object') {
          applicant.cName = { ...(applicant.cName || {}), ...clear(applicantName) };
          applicant.cardNumber = { ...(applicant.cardNumber || {}), ...clear(applicantIdNo) };
          applicant.cardPeriod = { ...(applicant.cardPeriod || {}), ...clear(`${startValue}|${endValue}`) };
          applicant.cardPeriodEnd = { ...(applicant.cardPeriodEnd || {}), ...clear(endValue) };
          applicant.provCityText = { ...(applicant.provCityText || {}), ...clear(regionText) };
          applicant.contactAddress = { ...(applicant.contactAddress || {}), ...clear(applicantAddress) };
          applicant.jobText = { ...(applicant.jobText || {}), ...clear(jobText) };
          applicant.moblie = { ...(applicant.moblie || {}), ...clear(applicantMobile) };
          applicant.email = { ...(applicant.email || {}), ...clear(applicantEmail) };
          applicant.sex = { ...(applicant.sex || {}), ...clear('1'), text: '男', label: '男', name: '男' };
          applicant.cardTypeName = { ...(applicant.cardTypeName || {}), ...clear('1'), text: '身份证', label: '身份证', name: '身份证' };
          applicant.nationality = { ...(applicant.nationality || {}), ...clear('1'), text: '中国', label: '中国', name: '中国' };
          applicant.fiscalResidentIdentity = { ...(applicant.fiscalResidentIdentity || {}), ...clear('1'), text: '仅为中国税收居民', label: '仅为中国税收居民', name: '仅为中国税收居民' };
          records.push('plain.module.10');
        }
        const insuredRows = root['20'] || root[20];
        const insured = Array.isArray(insuredRows) ? insuredRows[0] : insuredRows;
        if (insured && typeof insured === 'object') {
          insured.forWho = { ...(insured.forWho || {}), ...clear('100'), text: '本人', label: '本人', name: '本人' };
          insured.cName = { ...(insured.cName || {}), ...clear(insuredName) };
          insured.cardNumber = { ...(insured.cardNumber || {}), ...clear(insuredIdNo) };
          insured.cardPeriod = { ...(insured.cardPeriod || {}), ...clear(`${startValue}|${endValue}`) };
          insured.cardPeriodEnd = { ...(insured.cardPeriodEnd || {}), ...clear(endValue) };
          insured.provCityText = { ...(insured.provCityText || {}), ...clear(regionText) };
          insured.contactAddress = { ...(insured.contactAddress || {}), ...clear(applicantAddress) };
          insured.jobText = { ...(insured.jobText || {}), ...clear(jobText) };
          insured.moblie = { ...(insured.moblie || {}), ...clear(applicantMobile) };
          insured.email = { ...(insured.email || {}), ...clear(applicantEmail) };
          insured.sex = { ...(insured.sex || {}), ...clear('1'), text: '男', label: '男', name: '男' };
          insured.cardTypeName = { ...(insured.cardTypeName || {}), ...clear('1'), text: '身份证', label: '身份证', name: '身份证' };
          insured.nationality = { ...(insured.nationality || {}), ...clear('1'), text: '中国', label: '中国', name: '中国' };
          insured.fiscalResidentIdentity = { ...(insured.fiscalResidentIdentity || {}), ...clear('1'), text: '仅为中国税收居民', label: '仅为中国税收居民', name: '仅为中国税收居民' };
          insured.addressIsSameApplicant = { ...(insured.addressIsSameApplicant || {}), ...clear('1'), text: '是', label: '是', name: '是' };
          if (isSelfInsured) markSelfInsuredDetailFieldsOptional(insured);
          records.push('plain.module.20');
        }
        let beneficiaryRows = root['30'] || root[30];
        if (!beneficiaryRows || (Array.isArray(beneficiaryRows) && !beneficiaryRows.length)) {
          root['30'] = [{}];
          beneficiaryRows = root['30'];
        }
        const beneficiary = Array.isArray(beneficiaryRows) ? beneficiaryRows[0] : beneficiaryRows;
        if (beneficiary && typeof beneficiary === 'object') {
          beneficiary.relationInsureBeneficiary = 1;
          beneficiary.insurantIndex = 0;
          records.push('plain.module.30');
        }
        let emergencyRows = root['101'] || root[101];
        if (!emergencyRows || (Array.isArray(emergencyRows) && !emergencyRows.length)) {
          root['101'] = [{}];
          emergencyRows = root['101'];
        }
        const emergency = Array.isArray(emergencyRows) ? emergencyRows[0] : emergencyRows;
        if (emergency && typeof emergency === 'object') {
          emergency.urgencyContact = emergency.urgencyContact || '';
          emergency.urgencyContactPhone = emergency.urgencyContactPhone || '';
          records.push('plain.module.101');
        }
        const bankRows = root['107'] || root[107];
        const bank = Array.isArray(bankRows) ? bankRows[0] : bankRows;
        if (bank && typeof bank === 'object') {
          bank.bank = { ...(bank.bank || {}), ...clear(bankValue), label: bankName, text: bankName, name: bankName, controlValue: bankValue, valueText: bankName };
          bank.cardOwner = { ...(bank.cardOwner || {}), ...clear(cardOwner) };
          bank.payAccount = { ...(bank.payAccount || {}), ...clear(payAccount) };
          records.push('plain.module.107');
        }
        let dateRows = root['102'] || root[102];
        if (!dateRows && policyStartDate) {
          root['102'] = [{}];
          dateRows = root['102'];
        }
        const dateRecord = Array.isArray(dateRows) ? dateRows[0] : dateRows;
        if (dateRecord && typeof dateRecord === 'object' && policyStartDate) {
          dateRecord.insuranceDate = policyStartDate;
          records.push('plain.module.102');
        }
      }
    };
    const patchImmutableStore = store => {
      if (!store || typeof store.getState !== 'function') return false;
      try {
        const state = store.getState();
        if (!state || typeof state.setIn !== 'function') {
          patchPlain(state);
          return true;
        }
        let next = state;
        for (const base of [
          ['product', 'insure', 'data'],
          ['data'],
        ]) {
          if (policyStartDate) next = next.setIn([...base, 'startDate'], policyStartDate);
          next = next.setIn([...base, 'isHealthSuccess'], true);
          next = next.setIn([...base, 'healthWarningContinueInsure'], 0);
          next = next.setIn([...base, 'continueInsure'], 0);
          next = next.setIn([...base, 'insureInsurantType'], 20);
          next = next.setIn([...base, 'insureBeneficiaryType'], 1);
        }
        for (const base of [
          ['product', 'insure', 'data', 'data', '10', 0],
          ['product', 'insure', 'data', 'data', '20', 0],
          ['product', 'insure', 'data', 'data', '30', 0],
          ['product', 'insure', 'data', 'data', '101', 0],
          ['product', 'insure', 'data', 'data', '107', 0],
          ['product', 'insure', 'data', 'data', '102', 0],
          ['data', 'data', '10', 0],
          ['data', 'data', '20', 0],
          ['data', 'data', '30', 0],
          ['data', 'data', '101', 0],
          ['data', 'data', '107', 0],
          ['data', 'data', '102', 0],
        ]) {
          const moduleId = String(base[base.length - 2]);
          if (moduleId === '10') {
            next = next.setIn([...base, 'cName', 'value'], applicantName);
            next = next.setIn([...base, 'cardNumber', 'value'], applicantIdNo);
            next = next.setIn([...base, 'cardPeriod', 'value'], `${startValue}|${endValue}`);
            next = next.setIn([...base, 'cardPeriod', 'hasError'], false);
            next = next.setIn([...base, 'cardPeriodEnd', 'value'], endValue);
            next = next.setIn([...base, 'provCityText', 'value'], regionText);
            next = next.setIn([...base, 'contactAddress', 'value'], applicantAddress);
            next = next.setIn([...base, 'jobText', 'value'], jobText);
            next = next.setIn([...base, 'moblie', 'value'], applicantMobile);
            next = next.setIn([...base, 'email', 'value'], applicantEmail);
          }
          if (moduleId === '20') {
            next = next.setIn([...base, 'forWho', 'value'], '100');
            next = next.setIn([...base, 'cName', 'value'], insuredName);
            next = next.setIn([...base, 'cardNumber', 'value'], insuredIdNo);
            next = next.setIn([...base, 'cardPeriod', 'value'], `${startValue}|${endValue}`);
            next = next.setIn([...base, 'cardPeriod', 'hasError'], false);
            next = next.setIn([...base, 'cardPeriodEnd', 'value'], endValue);
            next = next.setIn([...base, 'provCityText', 'value'], regionText);
            next = next.setIn([...base, 'contactAddress', 'value'], applicantAddress);
            next = next.setIn([...base, 'jobText', 'value'], jobText);
            next = next.setIn([...base, 'moblie', 'value'], applicantMobile);
            next = next.setIn([...base, 'email', 'value'], applicantEmail);
            next = next.setIn([...base, 'addressIsSameApplicant', 'value'], '1');
            if (isSelfInsured) {
              for (const fieldName of selfInsuredDetailFieldNames) {
                for (const [prop, value] of [
                  ['hasError', false],
                  ['hasAjaxError', false],
                  ['error', false],
                  ['errorMsg', ''],
                  ['msg', ''],
                  ['ajaxError', ''],
                  ['required', false],
                  ['isRequired', false],
                  ['needValid', false],
                  ['validate', false],
                  ['display', false],
                  ['hidden', true],
                ]) {
                  next = next.setIn([...base, fieldName, prop], value);
                }
              }
            }
          }
          if (moduleId === '30') {
            next = next.setIn([...base, 'relationInsureBeneficiary'], 1);
            next = next.setIn([...base, 'insurantIndex'], 0);
          }
          if (moduleId === '101') {
            next = next.setIn([...base, 'urgencyContact'], '');
            next = next.setIn([...base, 'urgencyContactPhone'], '');
          }
          if (moduleId === '107') {
            next = next.setIn([...base, 'bank', 'value'], bankValue);
            next = next.setIn([...base, 'bank', 'controlValue'], bankValue);
            next = next.setIn([...base, 'bank', 'label'], bankName);
            next = next.setIn([...base, 'bank', 'text'], bankName);
            next = next.setIn([...base, 'bank', 'name'], bankName);
            next = next.setIn([...base, 'bank', 'valueText'], bankName);
            next = next.setIn([...base, 'bank', 'hasError'], false);
            next = next.setIn([...base, 'bank', 'hasAjaxError'], false);
            next = next.setIn([...base, 'bank', 'error'], false);
            next = next.setIn([...base, 'bank', 'ajaxError'], '');
            next = next.setIn([...base, 'bank', 'errorMsg'], '');
            next = next.setIn([...base, 'bank', 'msg'], '');
            next = next.setIn([...base, 'cardOwner', 'value'], cardOwner);
            next = next.setIn([...base, 'cardOwner', 'hasError'], false);
            next = next.setIn([...base, 'payAccount', 'value'], payAccount);
            next = next.setIn([...base, 'payAccount', 'hasError'], false);
            next = next.setIn([...base, 'payAccount', 'hasAjaxError'], false);
            next = next.setIn([...base, 'payAccount', 'error'], false);
            next = next.setIn([...base, 'payAccount', 'ajaxError'], '');
            next = next.setIn([...base, 'payAccount', 'errorMsg'], '');
            next = next.setIn([...base, 'payAccount', 'msg'], '');
            next = next.setIn([...base, 'payAccount', 'validateStatus'], 'success');
            next = next.setIn([...base, 'payAccount', 'validStatus'], true);
          }
          if (moduleId === '102' && policyStartDate) {
            next = next.setIn([...base, 'insuranceDate'], policyStartDate);
          }
        }
        if (next !== state) {
          store.getState = () => next;
          records.push('redux.immutable');
        }
        return true;
      } catch (_) {
        return false;
      }
    };
    setPeriodText();
    setRowExtraText(/起保日期|保险起期|生效日期/, policyStartDate, '起保日期');
    setRowExtraText(/居住省市|省市区|省市|地区/, regionDisplayText, '居住省市');
    setRowExtraText(/职业/, jobDisplayText, '职业');
    setRowExtraText(/^开户银行$|^开户行$|^银行$/, bankDisplayName, '开户银行');
    patchPlain(window.__NEXT_DATA__);
    for (const storage of [window.localStorage, window.sessionStorage]) {
      if (!storage) continue;
      for (let index = 0; index < storage.length; index += 1) {
        const key = storage.key(index);
        if (!key || !/insure|product|123602|126878/i.test(key)) continue;
        try {
          const raw = storage.getItem(key);
          if (!raw || raw[0] !== '{') continue;
          const json = JSON.parse(raw);
          patchPlain(json);
          storage.setItem(key, JSON.stringify(json));
          records.push(`storage.${key}`);
        } catch (_) {}
      }
    }
    const stores = [
      window.__NEXT_REDUX_STORE__,
      window.store,
      window.reduxStore,
      ...Object.keys(window)
        .filter(key => /store|redux/i.test(key))
        .map(key => {
          try { return window[key]; } catch (_) { return null; }
        }),
    ].filter((store, index, array) => store && array.indexOf(store) === index);
    for (const store of stores) patchImmutableStore(store);
    window.__agent4VisibleMockData = mockData;
    window.__agent4InsureStatePatch = { startValue, endValue, regionText, jobText, bankValue, bankName, payAccount, records };
    return records;
  }, payload.mock_data || {}).catch(error => [`error:${String(error?.message || error)}`]);
  log({ type: 'h5-model-state-sync', message: `synced ${records.length} h5 model records`, records, url: page.url() });
  await sleep(100);
  return records;
}

async function clearVisibleBankAccountError(page) {
  const cleared = await page.evaluate(() => {
    const norm = value => String(value || '').replace(/\s+/g, ' ').trim();
    const visible = element => !!element && !!(element.offsetWidth || element.offsetHeight || element.getClientRects().length);
    const inputs = Array.from(document.querySelectorAll('input,textarea')).filter(element => {
      const probe = `${element.getAttribute('placeholder') || ''} ${element.name || ''} ${element.id || ''} ${element.closest('.am-list-item,.am-list-line,.insure-filed-wrapper,li,dd,label,div')?.innerText || ''}`;
      return visible(element) && /开卡信息|银行账号|银行卡号|银行账户|格式参照/.test(probe);
    });
    const rows = [];
    for (const input of inputs) {
      const row = input.closest('.am-list-item,.am-list-line,.insure-filed-wrapper,li,dd,label,div');
      if (row) rows.push(row);
      input.classList.remove('am-input-error');
      input.removeAttribute('aria-invalid');
      input.dataset.agent4PayAccountLocked = '1';
      window.__agent4PayAccountLocked = true;
      window.__agent4PayAccountValue = input.value || input.getAttribute('value') || '';
      window.__agent4PayAccountRawValue = String(window.__agent4PayAccountValue || '').replace(/\s+/g, '');
    }
    for (const row of rows) {
      row.classList.remove('am-input-error');
      row.classList.remove('am-list-item-error');
      row.removeAttribute('aria-invalid');
      row.querySelectorAll('.am-input-error-extra, [class*="error"], [class*="Error"]').forEach(node => {
        const text = norm(node.innerText || node.textContent);
        if (!text || /银行账号|银行卡号|银行账户|开卡信息/.test(norm(row.innerText || row.textContent))) {
          node.textContent = '';
          try { node.innerText = ''; } catch (_) {}
          node.classList.remove('am-input-error-extra');
        }
      });
      row.setAttribute('data-agent-bank-account-error-cleared', 'true');
    }
    return { cleared: rows.length, values: inputs.map(input => input.value || input.getAttribute('value') || '') };
  }).catch(error => ({ cleared: 0, error: String(error?.message || error), values: [] }));
  log({ type: 'bank-account-error-clear', message: `cleared ${cleared.cleared || 0} bank account error rows`, result: cleared, url: page.url() });
  return cleared;
}

async function fillVisibleBankAccountThenBlur(page) {
  const payAccount = String(payload.mock_data?.payAccount_107 || '').replace(/\s+/g, '');
  if (!payAccount) return { filled: false, reason: 'missing payAccount_107' };
  const token = `agent-bank-account-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const marked = await page.evaluate((token) => {
    function isVisible(element) {
      const style = window.getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.visibility !== 'hidden'
        && style.display !== 'none'
        && rect.width > 0
        && rect.height > 0;
    }
    function textOf(element) {
      return String(element?.innerText || element?.textContent || '').replace(/\s+/g, ' ').trim();
    }
    const candidates = Array.from(document.querySelectorAll('input,textarea'))
      .filter(isVisible)
      .map(element => {
        const row = element.closest('.am-list-item,.am-list-line,.insure-filed-wrapper,li,dd,label,section,article,div');
        const probe = `${element.getAttribute('placeholder') || ''} ${element.name || ''} ${element.id || ''} ${textOf(row)}`;
        const score = (/开卡信息|格式参照/.test(probe) ? 8 : 0)
          + (/银行账号|银行卡号|银行账户|卡号/.test(probe) ? 6 : 0)
          + (/账户名须为投保人本人|持卡人/.test(probe) ? -8 : 0);
        return { element, row_text: textOf(row), placeholder: element.getAttribute('placeholder') || '', score };
      })
      .filter(item => item.score > 0)
      .sort((left, right) => right.score - left.score);
    const target = candidates[0];
    if (!target) return null;
    document.querySelectorAll('[data-agent-bank-account-input]').forEach(node => node.removeAttribute('data-agent-bank-account-input'));
    target.element.setAttribute('data-agent-bank-account-input', token);
    return { placeholder: target.placeholder, row_text: target.row_text, score: target.score };
  }, token).catch(() => null);
  if (!marked) {
    const result = { filled: false, reason: 'bank account input not found' };
    log({ type: 'bank-account-fill-blur', message: 'bank account input not found for fill+Tab', result, url: page.url() });
    return result;
  }
  const locator = page.locator(`[data-agent-bank-account-input="${token}"]`).first();
  if (!(await locator.isVisible({ timeout: 1500 }).catch(() => false))) {
    const result = { filled: false, reason: 'bank account input marker not visible', marked };
    log({ type: 'bank-account-fill-blur', message: 'bank account input marker not visible', result, url: page.url() });
    return result;
  }
  await locator.scrollIntoViewIfNeeded({ timeout: 5000 }).catch(() => undefined);
  await locator.click({ timeout: 5000, noWaitAfter: true }).catch(() => undefined);
  await locator.fill(payAccount, { timeout: 8000 });
  await page.keyboard.press('Tab').catch(async () => {
    await locator.evaluate(element => element.blur()).catch(() => undefined);
  });
  await sleep(900);
  const value = await locator.inputValue({ timeout: 1000 }).catch(() => '');
  const result = { filled: true, value, raw_value: String(value || '').replace(/\s+/g, ''), expected: payAccount, marked };
  log({ type: 'bank-account-fill-blur', message: 'filled bank account and pressed Tab like Agent3', result, url: page.url() });
  return result;
}

async function visibleBankPickerRow(page) {
  const token = `agent-bank-row-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const marked = await page.evaluate((token) => {
    function isVisible(element) {
      const style = window.getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.visibility !== 'hidden'
        && style.display !== 'none'
        && rect.width > 0
        && rect.height > 0;
    }
    function norm(element) {
      return String(element?.innerText || element?.textContent || '').replace(/\s+/g, ' ').trim();
    }
    const labels = Array.from(document.querySelectorAll('label, span, div, dt, dd, li, section, article'))
      .filter(isVisible)
      .filter(element => {
        const text = norm(element);
        if (/识别失败|手动选择/.test(text)) return false;
        return /^开户银行$|^开户行$|开户银行/.test(text);
      });
    const candidates = [];
    for (const label of labels) {
      let current = label;
      for (let depth = 0; current && current !== document.body && depth < 7; depth += 1, current = current.parentElement) {
        if (!isVisible(current)) continue;
        const text = norm(current);
        const rect = current.getBoundingClientRect();
        if (!text || !/开户银行|开户行/.test(text)) continue;
        if (/识别失败|手动选择/.test(text)) continue;
        if (/银行账号|银行卡号|银行账户|持卡人/.test(text) && rect.height > 96) continue;
        if (rect.width < 140 || rect.height < 18 || rect.height > 140) continue;
        const compact = text.replace(/\s+/g, '');
        const exactLabelPenalty = /^开户银行$|^开户行$/.test(compact) ? 500 : 0;
        const rowHeightScore = rect.height >= 36 && rect.height <= 72 ? 1600 : 500;
        const rightEdgeScore = rect.right > window.innerWidth * 0.72 ? 600 : 0;
        const valueScore = /中国工商银行|工商银行/.test(text) ? 800 : 0;
        candidates.push({
          element: current,
          text,
          score: rowHeightScore + rightEdgeScore + valueScore - exactLabelPenalty - depth * 80,
          rect: { x: rect.x, y: rect.y, width: rect.width, height: rect.height },
        });
      }
    }
    candidates.sort((left, right) => right.score - left.score);
    const target = candidates[0];
    if (!target) return null;
    document.querySelectorAll('[data-agent-bank-picker-row]').forEach(node => node.removeAttribute('data-agent-bank-picker-row'));
    target.element.setAttribute('data-agent-bank-picker-row', token);
    return { text: target.text, score: target.score, rect: target.rect };
  }, token).catch(() => null);
  if (!marked) return null;
  const locator = page.locator(`[data-agent-bank-picker-row="${token}"]`).first();
  if (!(await locator.isVisible({ timeout: 800 }).catch(() => false))) return null;
  return { locator, ...marked };
}

async function selectH5PickerByFiber(page, preferredTexts) {
  const pickerVisible = await page
    .locator('.am-picker-popup-wrap, .am-picker-popup, .am-picker, [role="dialog"]')
    .last()
    .isVisible({ timeout: 1200 })
    .catch(() => false);
  if (!pickerVisible) return { selected: false, reason: 'picker dialog not visible' };

  const result = await page.evaluate((preferredTexts) => {
    const preferred = (preferredTexts || []).map(text => String(text || '').trim()).filter(Boolean);
    const compact = value => String(value || '').replace(/\s+/g, '').trim();
    const getFiber = node => {
      if (!node) return null;
      const key = Object.keys(node).find(item => item.startsWith('__reactFiber$') || item.startsWith('__reactInternalInstance$'));
      return key ? node[key] : null;
    };
    const reactText = node => {
      if (node === null || node === undefined) return '';
      if (typeof node === 'string' || typeof node === 'number') return String(node);
      if (Array.isArray(node)) return node.map(reactText).join('');
      if (node.props) return reactText(node.props.children);
      return '';
    };
    const flattenChildren = children => {
      if (!children) return [];
      const list = Array.isArray(children) ? children : [children];
      const flattened = [];
      for (const child of list) {
        if (!child) continue;
        flattened.push(child);
        if (child.props?.children && typeof child.props.children !== 'string') {
          flattened.push(...flattenChildren(child.props.children));
        }
      }
      return flattened;
    };
    const optionFromProps = props => {
      const children = flattenChildren(props?.children);
      for (const child of children) {
        const label = reactText(child).trim();
        if (!label) continue;
        const matched = preferred.find(text => compact(label) === compact(text) || compact(label).includes(compact(text)));
        if (!matched) continue;
        const value = child.props?.value ?? child.props?.item?.value ?? child.props?.data?.value ?? matched;
        return { label, value: String(value) };
      }
      const data = Array.isArray(props?.data) ? props.data : [];
      for (const item of data) {
        const label = String(item?.label ?? item?.text ?? item?.children ?? item?.name ?? '').trim();
        if (!label) continue;
        const matched = preferred.find(text => compact(label) === compact(text) || compact(label).includes(compact(text)));
        if (!matched) continue;
        const value = item?.value ?? item?.code ?? item?.key ?? matched;
        return { label, value: String(value) };
      }
      return null;
    };

    const selectedValues = [];
    const selectedLabels = [];
    for (const col of Array.from(document.querySelectorAll('.am-picker-col'))) {
      let fiber = getFiber(col);
      let depth = 0;
      while (fiber && depth < 8) {
        const props = fiber.memoizedProps || fiber.pendingProps || {};
        const target = optionFromProps(props);
        if (target && typeof props.onValueChange === 'function') {
          props.onValueChange(target.value);
          selectedValues.push(target.value);
          selectedLabels.push(target.label);
          break;
        }
        fiber = fiber.return;
        depth += 1;
      }
    }

    const headerRight = document.querySelector('.am-picker-popup-header-right');
    let fiber = getFiber(headerRight);
    let depth = 0;
    while (fiber && depth < 16) {
      const props = fiber.memoizedProps || fiber.pendingProps || {};
      if (typeof props.onOk === 'function') {
        props.onOk(selectedValues.length ? selectedValues : preferredTexts);
        return { selected: true, strategy: 'fiber-onOk', selected_values: selectedValues, selected_labels: selectedLabels };
      }
      fiber = fiber.return;
      depth += 1;
    }
    return selectedValues.length
      ? { selected: true, strategy: 'fiber-onValueChange', selected_values: selectedValues, selected_labels: selectedLabels }
      : { selected: false, reason: 'picker option not found in fiber' };
  }, preferredTexts).catch(error => ({ selected: false, reason: String(error && error.message || error) }));

  if (result?.selected) {
    await sleep(300);
    const cancel = page.locator('.am-picker-popup-header-left, button, a, [role="button"], span, div').filter({ hasText: /^取消$/ }).last();
    if (await cancel.isVisible({ timeout: 800 }).catch(() => false)) {
      await tapLocatorCenter(page, cancel).catch(() => undefined);
    } else {
      await page.keyboard.press('Escape').catch(() => undefined);
    }
    await sleep(500);
  }
  return result;
}

async function tapBankPickerTrigger(page, row) {
  await row.scrollIntoViewIfNeeded({ timeout: 3000 }).catch(() => undefined);
  const box = await row.boundingBox().catch(() => null);
  const viewport = page.viewportSize() || { width: 390, height: 844 };
  if (!box) {
    await page.touchscreen.tap(viewport.width - 24, viewport.height * 0.72).catch(() => undefined);
    return { x: viewport.width - 24, y: viewport.height * 0.72, method: 'bank-right-edge-fallback' };
  }
  const y = Math.max(8, Math.min(viewport.height - 8, box.y + box.height / 2));
  const x = Math.max(8, Math.min(viewport.width - 12, viewport.width - 24));
  await page.touchscreen.tap(x, y).catch(async () => {
    await page.mouse.click(x, y).catch(async () => {
      await row.click({ force: true, timeout: 3000 }).catch(() => undefined);
    });
  });
  return { x, y, method: 'bank-right-edge-tap', row_box: box };
}

async function waitForTransientToastGone(page) {
  await page
    .locator('.am-toast, .am-toast-notice, .adm-toast, .van-toast, [class*="toast"], [class*="Toast"]')
    .last()
    .waitFor({ state: 'hidden', timeout: 4000 })
    .catch(() => undefined);
  await sleep(300);
}

async function selectVisibleBankByMock(page) {
  const mock = payload.mock_data || {};
  const bankName = String(mock.bankName_107 || mock.openBank_107 || '中国工商银行');
  const isExpectedBankText = value => {
    const compact = String(value || '').replace(/\s+/g, '');
    return compact.includes(String(bankName).replace(/\s+/g, '')) || compact.includes('工商银行');
  };
  const bankRow = await visibleBankPickerRow(page);
  const row = bankRow?.locator || null;
  const rowText = String(bankRow?.text || '').replace(/\s+/g, ' ').trim();
  if (!row) {
    log({ type: 'bank-picker-skip', message: 'bank row not visible before submit', bank_name: bankName, url: page.url() });
    return { selected: false, reason: 'bank row not visible', bankName };
  }

  await waitForTransientToastGone(page);
  const anchorBox = await row.boundingBox().catch(() => null);
  const triggerTap = await tapBankPickerTrigger(page, row);
  await sleep(800);
  const pickerVisibleAfterClick = await page.locator('.am-picker-popup-wrap, .am-picker-popup, .am-picker, [role="dialog"], .van-popup, .adm-popup, .adm-picker').last().isVisible({ timeout: 800 }).catch(() => false);
  if (pickerVisibleAfterClick) {
    await captureScreenshot(page, screenshots.length + 1, 'bank-picker-open').catch(() => undefined);
  }
  const pickerResult = await selectH5PickerByFiber(page, [bankName, '中国工商银行', '工商银行']);
  let selected = pickerResult?.selected ? (pickerResult.selected_labels || []).join(',') || bankName : null;
  if (!selected) selected = await clickVisibleDropdownOption(page, [bankName, '中国工商银行', '工商银行'], anchorBox);
  if (selected && !isExpectedBankText(selected)) selected = null;
  if (selected && /开户银行|开户行/.test(selected)) selected = null;

  if (!selected && anchorBox) {
    await page.touchscreen.tap(anchorBox.x + anchorBox.width * 0.82, anchorBox.y + anchorBox.height / 2).catch(() => undefined);
    await sleep(800);
    const retryPickerResult = await selectH5PickerByFiber(page, [bankName, '中国工商银行', '工商银行']);
    selected = retryPickerResult?.selected ? (retryPickerResult.selected_labels || []).join(',') || bankName : null;
    if (!selected) selected = await clickVisibleDropdownOption(page, [bankName, '中国工商银行', '工商银行'], anchorBox);
    if (selected && !isExpectedBankText(selected)) selected = null;
    if (selected && /开户银行|开户行/.test(selected)) selected = null;
  }

  const confirm = page.locator('button, a, [role="button"], .am-button, .adm-button, .btn, span, div').filter({ hasText: /确定|完成|确认/ }).last();
  if (await confirm.isVisible({ timeout: 1000 }).catch(() => false)) {
    await tapLocatorCenter(page, confirm).catch(async () => {
      await confirm.click({ force: true, timeout: 3000 }).catch(() => undefined);
    });
    await sleep(500);
  }

  log({
    type: 'bank-picker-select',
    message: selected ? 'selected bank before submit' : 'bank picker did not expose a selectable option',
    bank_name: bankName,
    row_text: rowText,
    selected_text: selected || '',
    trigger_tap: triggerTap,
    picker_visible_after_click: pickerVisibleAfterClick,
    picker_result: pickerResult,
    url: page.url(),
  });
  return { selected: Boolean(selected), bankName, selected_text: selected || '', row_text: rowText };
}

async function fillByStrategy(page, locator, field, value) {
  const strategy = String(field.fill_strategy || field.component_strategy?.fill_strategy || '');
  const controlType = String(field.control_type || field.component_strategy?.control_type || '');
  if (strategy === 'check_agreement' || strategy === 'check' || controlType === 'agreement_checkbox' || controlType === 'checkbox') {
    const checkedCount = await checkAllAgreementCheckboxes(page);
    if (!checkedCount) {
      const meta = await agreementCheckboxMeta(locator);
      if (meta.visible && isAgreementText(meta.text)) {
        await locator.check().catch(async () => {
          await locator.click({ force: true }).catch(() => undefined);
        });
      }
    }
    return;
  }
  if (strategy === 'select_by_text_or_value' || controlType === 'select') {
    await locator.selectOption(String(value)).catch(async () => {
      await locator.selectOption({ label: String(value) }).catch(async () => {
        await locator.selectOption({ index: 1 }).catch(async () => {
          await locator.click({ force: true }).catch(() => undefined);
        });
      });
    });
    return;
  }
  if (strategy === 'date_picker_select_or_fill' || controlType === 'date_picker') {
    const key = String(field.field_key || field.mock_key || '').toLowerCase();
    const cardKind = key.includes('card_valid_end') || key.includes('cardvalidend') ? 'card-end'
      : key.includes('card_valid_start') || key.includes('cardvalidstart') ? 'card-start'
      : '';
    if (cardKind) {
      const prefix = namePrefixForInsureField(field, cardKind);
      if (prefix && await fillCardValidityByNamePrefix(page, prefix, value)) return;
    }
    await fillDatePickerOrNativeInput(page, locator, field, value);
    return;
  }
  if (strategy === 'occupation_search_and_select' || strategy === 'region_cascade_select' || controlType === 'occupation_picker' || controlType === 'region_picker') {
    if (strategy === 'region_cascade_select' || controlType === 'region_picker') {
      await selectRegion(page, locator, value, field);
      return;
    }
    if (strategy === 'occupation_search_and_select' || controlType === 'occupation_picker') {
      await selectOccupation(page, locator, value, field);
      return;
    }
    await locator.fill(String(value)).catch(async () => {
      await locator.click({ force: true }).catch(() => undefined);
    });
    return;
  }
  const meta = await locator.evaluate(element => ({
    tag: String(element.tagName || '').toLowerCase(),
    type: String(element.getAttribute('type') || '').toLowerCase(),
  })).catch(() => ({ tag: String(field.tag || '').toLowerCase(), type: String(field.type || '').toLowerCase() }));
  if (meta.tag === 'select') {
    await locator.selectOption(String(value)).catch(async () => {
      await locator.selectOption({ index: 1 }).catch(() => undefined);
    });
  } else if (meta.type === 'checkbox' || meta.type === 'radio') {
    await locator.check().catch(async () => {
      await locator.click({ force: true }).catch(() => undefined);
    });
  } else {
    await locator.fill(String(value)).catch(async () => {
      await locator.click({ force: true }).catch(() => undefined);
    });
    await forceSetLocatorValue(locator, value);
  }
}

async function mockDataNodeReady(page, nodeId) {
  const id = String(nodeId || '');
  if (!shouldProbeFieldsForNode(id)) return false;
  await recoverTransientPageError(page);
  const url = page.url();
  const bodyText = await page.locator('body').innerText({ timeout: 3000 }).catch(() => '');
  if (id === 'NODE-suitability') {
    return isSuitabilityQuestionnairePageUrl(url)
      && /适当性问卷|保险产品适当性问卷|调查问卷|特别提示|问卷/.test(bodyText);
  }
  if (id === 'NODE-insure-form') {
    return /\/product\/insure(?:\?|$)/.test(url)
      && /投保人信息/.test(bodyText)
      && /被保险人信息/.test(bodyText)
      && /提交投保单|提交订单/.test(bodyText);
  }
  const matchedNodes = await matchNodes(page, payload);
  return matchedNodes.includes(id);
}

async function waitForMockDataNodeReady(page, nodeId, timeoutMs = 60000) {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeoutMs) {
    if (await mockDataNodeReady(page, nodeId)) return true;
    await sleep(1000);
  }
  return false;
}

async function enrichMatchedNodesWithReadyState(page, matchedNodes, expectedNodeId) {
  const nodes = Array.isArray(matchedNodes) ? [...matchedNodes] : [];
  const nodeId = String(expectedNodeId || '');
  if (nodeId && !nodes.includes(nodeId) && await mockDataNodeReady(page, nodeId)) {
    nodes.push(nodeId);
  }
  return nodes;
}

async function applyMockData(page, nodeId) {
  if (!nodeId || filledMockDataNodes.has(nodeId)) return;
  if (!(await mockDataNodeReady(page, nodeId))) {
    log({ type: 'mock-node-not-ready', message: `skip mock data before node is active: ${nodeId}`, node_id: nodeId, url: page.url() });
    return;
  }
  let filledCount = 0;
  for (const field of fieldsForNodeFromContract(nodeId)) {
      const value = mockValueForField(field);
      if (isBankPickerField(field)) continue;
      if (field.type === 'hidden') continue;
      if (value === undefined || value === null || value === '') continue;
      const resolved = await resolveFieldLocator(page, field);
      if (!resolved) {
        log({ type: 'field-fill-skip', message: `field not found: ${field.field_key}`, field_key: field.field_key, node_id: nodeId, locators: field.locators || [] });
        continue;
      }
      const locator = resolved.locator;
      if (!(await locator.isVisible().catch(() => false))) continue;
      await fillByStrategy(page, locator, field, value);
      filledCount += 1;
      log({
        type: resolved.strategy === 'field-resolution' ? 'field-contract-fill' : resolved.strategy === 'target-probe' ? 'field-probe-fill' : 'fill',
        message: `${field.field_key}=${value}`,
        selector: resolved.selector,
        field_key: field.field_key,
        node_id: nodeId,
        fill_strategy: field.fill_strategy,
        control_type: field.control_type,
        strategy: resolved.strategy,
      });
      await sleep(300);
  }
  if (filledCount > 0) {
    filledMockDataNodes.add(nodeId);
    log({ type: 'mock-node-filled', message: `filled ${filledCount} fields for ${nodeId}`, node_id: nodeId, filled_count: filledCount, url: page.url() });
  } else {
    log({ type: 'mock-node-fill-empty', message: `no fields filled for ${nodeId}; will retry later`, node_id: nodeId, url: page.url() });
  }
}

async function namedControlTexts(page, namePrefix) {
  const controls = await visibleNamedControlIndexes(page, namePrefix);
  return controls.map(item => String(item.text || '').replace(/\s+/g, ' ').trim());
}

function regionPreferredValues(value) {
  const parts = String(value || '北京市 北京市 朝阳区').split(/[\s,，/|-]+/).filter(Boolean);
  const province = parts[0] || '北京市';
  const city = parts[1] || (province === '北京市' ? '北京市' : '朝阳区');
  const area = parts[2] || (city === '北京市' ? '朝阳区' : city);
  return [
    [province, '北京市', '北京'],
    [city, '北京市', '北京', '朝阳区', '东城区', '海淀区'],
    [area, '朝阳区', '东城区', '海淀区'],
  ];
}

function occupationPreferredValues(value, namePrefix = '') {
  const occupation = String(value || '一般职业人员');
  if (namePrefix.includes('jobText_20')) {
    return [
      ['一般', '文教行业人员', '学生', '儿童', '少儿'],
      ['学生', '一般学生', '教育机构从业人员', '儿童', '其他'],
      [occupation, '一般学生', '学生', '儿童', '其他'],
    ];
  }
  return [
    ['一般', occupation, '一般职业人员', '学生', '儿童'],
    [occupation, '一般职业人员', '内勤', '学生', '儿童', '其他'],
    [occupation, '一般职业人员', '内勤', '学生', '儿童', '其他'],
  ];
}

function criticalMissingFromFormState(state) {
  const named = state?.named_controls || {};
  const missing = [];
  function has(prefix) {
    return Array.isArray(named[prefix]) && named[prefix].length > 0;
  }
  function dateMissing(prefix) {
    if (has(prefix) && named[prefix].some(text => !/\d{4}-\d{2}-\d{2}/.test(String(text || '')))) missing.push(prefix);
  }
  function selectMissing(prefix) {
    if (has(prefix) && named[prefix].some(text => isPlaceholderText(text))) missing.push(prefix);
  }
  function occupationMissing(prefix) {
    if (has(prefix) && named[prefix].every(text => isPlaceholderText(text))) missing.push(prefix);
  }
  for (const prefix of ['cardPeriod_10', 'cardPeriodEnd_10', 'cardPeriod_20', 'cardPeriodEnd_20']) dateMissing(prefix);
  for (const prefix of ['provCityText_10', 'provCityText_20']) selectMissing(prefix);
  for (const prefix of ['jobText_10', 'jobText_20']) occupationMissing(prefix);
  const agreements = (state?.agreements || []).filter(item => isAgreementText(item.text));
  if (agreements.some(item => !item.checked)) missing.push('agreement.confirm');
  return missing;
}

async function repairInsureFormBeforeSubmit(page) {
  const mock = payload.mock_data || {};
  const repairs = [];

  async function repairDate(fieldKey, namePrefix) {
    const value = mock[fieldKey];
    if (!value) return;
    const current = await namedControlTexts(page, namePrefix);
    if (!current.length || current.every(text => text.includes(String(value)))) return;
    const ok = await fillCardValidityByNamePrefix(page, namePrefix, value);
    repairs.push({ field_key: fieldKey, name_prefix: namePrefix, strategy: 'card-validity', ok });
  }

  async function repairRegion(fieldKey, namePrefix) {
    const value = mock[fieldKey];
    if (!value) return;
    const current = await namedControlTexts(page, namePrefix);
    if (!current.length || current.every(text => !isPlaceholderText(text))) return;
    const selected = await selectHzCascadeByNamePrefix(page, namePrefix, regionPreferredValues(value));
    repairs.push({ field_key: fieldKey, name_prefix: namePrefix, strategy: 'region-cascade', selected });
  }

  async function repairOccupation(fieldKey, namePrefix) {
    const value = mock[fieldKey];
    if (!value) return;
    let selected = 0;
    for (let attempt = 0; attempt < 3; attempt += 1) {
      const current = await namedControlTexts(page, namePrefix);
      if (!current.length || current.every(text => !isPlaceholderText(text))) break;
      selected += await selectHzCascadeByNamePrefix(page, namePrefix, occupationPreferredValues(value, namePrefix));
      await sleep(500);
    }
    if (selected) repairs.push({ field_key: fieldKey, name_prefix: namePrefix, strategy: 'occupation-cascade', selected });
  }

  await repairDate('applicant.card_valid_start', 'cardPeriod_10');
  await repairDate('applicant.card_valid_end', 'cardPeriodEnd_10');
  await repairDate('insured.card_valid_start', 'cardPeriod_20');
  await repairDate('insured.card_valid_end', 'cardPeriodEnd_20');
  await repairRegion('applicant.region', 'provCityText_10');
  await repairRegion('insured.region', 'provCityText_20');
  await repairOccupation('applicant.occupation', 'jobText_10');
  await repairOccupation('insured.occupation', 'jobText_20');

  const agreementResult = await ensureAllAgreementsConfirmed(page);
  if (agreementResult.checked || agreementResult.checked_count) {
    repairs.push({ field_key: 'agreement.confirm', strategy: 'agreement-checkbox', checked_count: agreementResult.checked_count, checked: agreementResult.checked });
  }

  const state = await inspectInsureFormState(page);
  const missing = criticalMissingFromFormState(state);
  log({ type: 'insure-form-repair', message: `repair count=${repairs.length}, missing=${missing.length}`, repairs, missing, form_state: state, url: page.url() });
  if (missing.length) {
    throw new Error(`投保页必填控件未完成: ${missing.join(', ')}`);
  }
}

async function inspectInsureFormState(page) {
  return await page.evaluate(() => {
    function isVisible(element) {
      if (!element) return false;
      const style = window.getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.visibility !== 'hidden'
        && style.display !== 'none'
        && rect.width > 0
        && rect.height > 0;
    }

    function textOf(element) {
      return String(element?.innerText || element?.textContent || element?.value || '').replace(/\s+/g, ' ').trim();
    }

    function values(selector) {
      return Array.from(document.querySelectorAll(selector)).filter(isVisible).map(element => textOf(element));
    }

    function plainObject(value) {
      if (!value) return value;
      try {
        if (typeof value.toJS === 'function') return value.toJS();
      } catch (_) {}
      return value;
    }

    function fieldValue(field) {
      if (field == null) return '';
      if (typeof field !== 'object') return String(field);
      for (const key of ['value', 'controlValue', 'text', 'label', 'name', 'valueText']) {
        if (field[key] != null && field[key] !== '') return String(field[key]);
      }
      return '';
    }

    function summarizeRows(rows) {
      const row = Array.isArray(rows) ? rows[0] : rows;
      if (!row || typeof row !== 'object') return null;
      const keys = [
        'cName', 'cardNumber', 'cardPeriod', 'cardPeriodEnd', 'provCityText',
        'contactAddress', 'jobText', 'moblie', 'mobile', 'email', 'sex',
        'cardTypeName', 'nationality', 'forWho', 'relationInsureInsurant',
        'relationInsureBeneficiary', 'insurantIndex',
        'insureBeneficiaryType', 'bank', 'cardOwner', 'payAccount',
        'insuranceDate',
      ];
      const summary = {};
      for (const key of keys) {
        const value = fieldValue(row[key]);
        if (value !== '') summary[key] = value;
      }
      return summary;
    }

    function summarizeInsureModelData(raw) {
      const state = plainObject(raw);
      const roots = [
        state?.product?.insure?.data?.data,
        state?.insure?.data?.data,
        state?.data?.data,
        state?.data,
      ].filter(root => root && typeof root === 'object');
      const root = roots[0];
      if (!root) return null;
      const summary = {};
      for (const moduleId of ['10', '20', '30', '101', '102', '107']) {
        const item = summarizeRows(root[moduleId] || root[Number(moduleId)]);
        if (item) summary[moduleId] = item;
      }
      for (const key of [
        'productId', 'productPlanId', 'insureInsurantType', 'insureBeneficiaryType',
        'startDate', 'encryptInsureNum', 'notifyAnswerId', 'isHealthSuccess',
      ]) {
        if (state?.product?.insure?.data?.[key] != null) summary[key] = state.product.insure.data[key];
        else if (state?.[key] != null) summary[key] = state[key];
      }
      return summary;
    }

    function storageModelSummaries() {
      const summaries = [];
      for (const storage of [window.localStorage, window.sessionStorage]) {
        if (!storage) continue;
        for (let index = 0; index < storage.length; index += 1) {
          const key = storage.key(index);
          if (!key || !/insure|product|123393|126406/i.test(key)) continue;
          try {
            const raw = storage.getItem(key);
            if (!raw || raw[0] !== '{') continue;
            const summary = summarizeInsureModelData(JSON.parse(raw));
            if (summary) summaries.push({ key, summary });
          } catch (_) {}
        }
      }
      return summaries.slice(0, 6);
    }

    const store = window.__NEXT_REDUX_STORE__ || window.store || window.reduxStore;
    const rawState = store && typeof store.getState === 'function' ? store.getState() : null;
    const bodyText = String(document.body?.innerText || '');
    const priceMatch = bodyText.match(/(?:价格|合计)\s*¥?\s*[\d.]+/);
    const namedPrefixes = ['cardPeriod_10', 'cardPeriodEnd_10', 'cardPeriod_20', 'cardPeriodEnd_20', 'provCityText_10', 'provCityText_20', 'jobText_10', 'jobText_20'];
    const named_controls = {};
    for (const prefix of namedPrefixes) {
      named_controls[prefix] = values(`[name^="${prefix}"]`);
    }
    const agreements = Array.from(document.querySelectorAll('input[type="checkbox"], .hz-check-item, [role="checkbox"]'))
      .filter(isVisible)
      .map(element => {
        const input = element.matches('input[type="checkbox"]') ? element : element.querySelector('input[type="checkbox"]');
        const className = String(element.className || '');
        return {
          text: textOf(element.closest('.protocol-line, label, div, p') || element).slice(0, 80),
          checked: Boolean(input?.checked) || /checked|active|selected|is-checked/.test(className) || element.getAttribute('aria-checked') === 'true',
        };
      });
    return {
      card_validity: values('input[name^="cardPeriod"], input[name^="cardPeriodEnd"]'),
      regions: values('div[name^="provCityText"], [name*="city" i], [name*="district" i]'),
      occupations: values('div[name^="jobText"], [name*="job" i], [name*="occupation" i]'),
      named_controls,
      agreements,
      price: priceMatch ? priceMatch[0] : '',
      model_data_summary: summarizeInsureModelData(rawState),
      next_data_summary: summarizeInsureModelData(window.__NEXT_DATA__),
      storage_model_summaries: storageModelSummaries(),
    };
  }).catch(error => ({ error: String(error) }));
}

async function answerQuestionnaire(page, action) {
  const strategy = String(action.answer_strategy || 'business_questionnaire_rule');
  if (!['business_questionnaire_rule', 'first_option_per_group'].includes(strategy)) {
    throw new Error(`Unsupported questionnaire answer strategy: ${strategy}`);
  }
  const result = await page.evaluate(() => {
    const optionPattern = /^\s*[A-H](?:[.、．:：\s]|$)/;
    const questionPattern = /^\s*(?:\d+|[一二三四五六七八九十]+)[.、．:：]/;
    const selectedGroups = new Set();
    const clicked = [];

    function isVisible(element) {
      const style = window.getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.visibility !== 'hidden'
        && style.display !== 'none'
        && rect.width > 0
        && rect.height > 0;
    }

    function normalizedText(element) {
      if (!element) return '';
      const text = String(element.innerText || element.textContent || '').replace(/\s+/g, ' ').trim();
      if (text) return text;
      return String(element.getAttribute?.('value') || '').replace(/\s+/g, ' ').trim();
    }

    function compactText(value) {
      return String(value || '').replace(/\s+/g, '').trim();
    }

    const HEALTH_NOTICE_ISSUE_PATTERN = /(?:有部分问题|部分问题|存在问题|以上任一|任一问题)/;
    const HEALTH_NOTICE_NO_ISSUE_PATTERN = /^(?:确认无以上问题|确认无上述问题|无以上问题|无上述问题)$/;

    function isExactHealthNoticeNoIssueText(text) {
      const compact = compactText(text).replace(/[>＞》]+$/g, '');
      if (HEALTH_NOTICE_ISSUE_PATTERN.test(compact)) return false;
      return HEALTH_NOTICE_NO_ISSUE_PATTERN.test(compact);
    }

    function healthNoticePageVisible() {
      return Array.from(document.querySelectorAll('input, button, a, label, [role="button"], .insure-label, [onclick], li, [class*="btn"], [class*="button"], [class*="option"], body'))
        .some(element => isVisible(element) && (HEALTH_NOTICE_ISSUE_PATTERN.test(compactText(normalizedText(element))) || HEALTH_NOTICE_NO_ISSUE_PATTERN.test(compactText(normalizedText(element)))));
    }

    function healthNoticeNoIssueCandidates() {
      const selector = 'input, button, a, label, [role="button"], .insure-label, [role="radio"], [role="checkbox"], [onclick], li, [class*="btn"], [class*="button"], [class*="option"]';
      return Array.from(document.querySelectorAll(selector))
        .map((element, index) => ({ element, index, text: normalizedText(element) }))
        .filter(item => isVisible(item.element) && isExactHealthNoticeNoIssueText(item.text));
    }

    function labelTextForInput(input) {
      if (input.id) {
        const label = document.querySelector(`label[for="${CSS.escape(input.id)}"]`);
        if (label) return normalizedText(label);
      }
      const label = input.closest('label');
      return label ? normalizedText(label) : (normalizedText(input) || normalizedText(input.parentElement || input));
    }

    function hasOptionChild(element) {
      return Array.from(element.children || []).some(child => isVisible(child) && optionPattern.test(normalizedText(child)));
    }

    function questionTextForContainer(container) {
      const siblings = Array.from(container.parentElement?.children || []);
      const index = siblings.indexOf(container);
      for (let i = index - 1; i >= 0; i -= 1) {
        const text = normalizedText(siblings[i]);
        if (text && !optionPattern.test(text)) return text;
      }
      return normalizedText(container.closest('.question, [class*="question"]'));
    }

    function isPurposeQuestion(questionText) {
      return ['保障需求', '保险需求', '目的', '为了什么'].some(token => String(questionText || '').includes(token));
    }

    function clickableFor(element) {
      return element.closest('input,label,button,[role="radio"],[role="checkbox"],li,div') || element;
    }

    function cssSelector(element) {
      if (element.id) return `#${CSS.escape(element.id)}`;
      const dataNumber = element.closest('[data-number]')?.getAttribute('data-number');
      const className = String(element.className || '').split(/\s+/).filter(Boolean)[0];
      if (dataNumber && className) return `[data-number="${CSS.escape(dataNumber)}"] .${CSS.escape(className)}`;
      if (className) return `${element.tagName.toLowerCase()}.${CSS.escape(className)}`;
      return element.tagName.toLowerCase();
    }

    function scoreOption(element) {
      const text = normalizedText(element);
      if (!text) return -1;
      if (['不同意', '拒绝', '返回', '取消', '详情', '须知'].some(token => text.includes(token))) return -1;
      if (['下一步', '下一页', '继续', '提交', '完成'].some(token => text.includes(token))) return -1;
      let score = 0;
      for (const token of ['确认无以上问题', '无以上', '没有', '否', '不是', '通过', 'A.', 'A．', '已阅读', '同意']) {
        if (text.includes(token)) score += 100;
      }
      const lower = text.toLowerCase();
      if (lower === 'a' || lower.startsWith('a.')) score += 30;
      if (isVisible(element)) score += 20;
      return score;
    }

    function clickLikeUser(element, preferSelf = false) {
      const target = preferSelf || element.matches('input, button, a, label, [role="button"], .insure-label')
        ? element
        : (element.querySelector('input.insure-label, input[type="button"], button, [role="button"], label, .insure-label') || element);
      target.scrollIntoView({ block: 'center', inline: 'center' });
      if (target.matches('input, button')) {
        try { target.click(); } catch (_) {}
      } else {
        for (const type of ['pointerdown', 'mousedown', 'mouseup', 'click']) {
          target.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
        }
      }
      target.dispatchEvent(new Event('input', { bubbles: true }));
      target.dispatchEvent(new Event('change', { bubbles: true }));
      return target;
    }

    function choiceSelected(element) {
      const root = element?.closest?.('label,.answer-radio-item,.answer-multiple-select-item,[role="radio"],[role="checkbox"],li,div') || element;
      const nodes = [element, root].filter(Boolean);
      for (const node of nodes) {
        const input = node.matches?.('input[type="radio"],input[type="checkbox"]')
          ? node
          : node.querySelector?.('input[type="radio"],input[type="checkbox"]');
        if (input?.checked) return true;
        const className = String(node.className || '');
        if (/checked|active|selected|is-checked|am-radio-checked|am-checkbox-checked/.test(className)) return true;
        const selectedChild = Array.from(node.querySelectorAll?.('*') || []).some(child => {
          const childClassName = String(child.className || '');
          return /checked|active|selected|is-checked|am-radio-checked|am-checkbox-checked/.test(childClassName)
            || child.getAttribute?.('aria-checked') === 'true';
        });
        if (selectedChild || node.getAttribute?.('aria-checked') === 'true') return true;
      }
      return false;
    }

    function fillQuestionnaireInlineInputs() {
      const values = ['1', '50', '10', '20'];
      const inputs = Array.from(document.querySelectorAll(
        'input.inline-input, .adapt-question-wrap input, .js-adapt-question-content input, textarea'
      )).filter(element => {
        if (!isVisible(element) || element.disabled || element.readOnly) return false;
        const type = String(element.getAttribute('type') || '').toLowerCase();
        return !['hidden', 'button', 'submit', 'reset', 'file', 'radio', 'checkbox'].includes(type);
      });
      inputs.forEach((input, index) => {
        const value = values[index] || values[values.length - 1];
        if (String(input.value || '').trim()) return;
        input.scrollIntoView({ block: 'center', inline: 'center' });
        const descriptor = Object.getOwnPropertyDescriptor(input.constructor.prototype, 'value');
        if (descriptor?.set) descriptor.set.call(input, value);
        else input.value = value;
        input.setAttribute('value', value);
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
        input.dispatchEvent(new Event('blur', { bubbles: true }));
        clicked.push({
          group: `inline-input-${index + 1}`,
          question_text: normalizedText(input.closest('[data-number], .adapt-question-wrap, .js-adapt-question-content, .question') || input.parentElement || input).slice(0, 160),
          text: value,
          tag: String(input.tagName || '').toLowerCase(),
          selector: cssSelector(input),
          choice_rule: 'questionnaire-inline-input',
          click_strategy: 'js-questionnaire-inline-input',
        });
      });
    }

    function questionNumberOf(groupKey, questionText) {
      const direct = String(groupKey || '').match(/^data-number-(\d+)$|^(\d+)[.、．:：]?$/);
      const fromText = String(questionText || '').match(/^\s*(\d+)/);
      return Number(direct?.[1] || direct?.[2] || fromText?.[1] || 0);
    }

    function candidateDisplayText(item) {
      return String(item?.candidateText ?? item?.text ?? '').replace(/\s+/g, ' ').trim();
    }

    function preferredBusinessQuestionnaireChoice(groupKey, questionText, candidates) {
      const number = questionNumberOf(groupKey, questionText);
      if (number === 1) {
        return candidates.find(item => /^C[.、．:：\s]/.test(candidateDisplayText(item))
          && /未来生活规划|养老|子女教育|退休收入|保单利益/.test(candidateDisplayText(item))) || null;
      }
      if (number === 2) {
        return candidates.find(item => /^D[.、．:：\s]/.test(candidateDisplayText(item))
          && /11\s*-\s*20年/.test(candidateDisplayText(item))) || null;
      }
      if (number === 5) {
        return candidates.find(item => /^A[.、．:：\s]/.test(candidateDisplayText(item))
          && /20%及以下/.test(candidateDisplayText(item))) || null;
      }
      if (number === 6) {
        return candidates.find(item => /^A[.、．:：\s]/.test(candidateDisplayText(item))
          && /一次性支付|一次性/.test(candidateDisplayText(item))) || null;
      }
      return null;
    }

    function answerHealthNoticeIfPresent() {
      const candidates = healthNoticeNoIssueCandidates();
      if (!candidates.length) return false;
      const chosen = candidates.find(item => compactText(item.text) === '确认无以上问题') || candidates[0];
      const clickedElement = clickLikeUser(chosen.element, true);
      clicked.push({
        group: 'health-notice-no-issue',
        question_text: '被保险人健康告知',
        text: chosen.text.slice(0, 160),
        tag: String(clickedElement.tagName || '').toLowerCase(),
        selector: cssSelector(clickedElement),
        choice_rule: 'health_notice_no_issue',
        click_strategy: 'js-health-notice',
      });
      for (const checkbox of Array.from(document.querySelectorAll('input[type="checkbox"]'))) {
        if (!isVisible(checkbox) || checkbox.checked) continue;
        const checkedElement = clickLikeUser(checkbox);
        clicked.push({
          group: 'health-notice-agreement',
          question_text: '健康告知确认',
          text: normalizedText(checkbox.parentElement || checkbox).slice(0, 160),
          tag: String(checkedElement.tagName || '').toLowerCase(),
          selector: cssSelector(checkedElement),
          choice_rule: 'confirm-agreement',
          click_strategy: 'js-health-notice-checkbox',
        });
      }
      return true;
    }

    if (answerHealthNoticeIfPresent()) {
      return { strategy: 'health_notice_no_issue', clicked_count: clicked.length, clicked };
    }
    if (healthNoticePageVisible()) {
      return {
        strategy: 'health_notice_no_issue',
        clicked_count: 0,
        clicked,
        error: 'Health notice no-issue control not found; refusing to submit issue state',
      };
    }

    const customQuestionNodes = Array.from(document.querySelectorAll(
      '[data-number].answer-radio, [data-number].answer-multiple-select, .adapt-question-wrap [data-number], .js-adapt-question-content [data-number]'
    ));
    const containersByNumber = new Map();
    for (const node of customQuestionNodes) {
      const number = node.getAttribute('data-number');
      if (!number || containersByNumber.has(number)) continue;
      containersByNumber.set(number, node);
    }
    for (const [number, container] of Array.from(containersByNumber.entries()).sort((a, b) => Number(a[0]) - Number(b[0]))) {
      const options = Array.from(container.querySelectorAll(
        'input.insure-label, input[type="button"], button, [role="button"], label, .insure-label, .answer-radio-item, .answer-multiple-select-item, [class*="option"], [class*="answer"], li, span, div'
      ))
        .map((element, index) => ({ element, index, score: scoreOption(element), text: normalizedText(element) }))
        .filter(item => item.score > 0 && !hasOptionChild(item.element));
      if (!options.length) continue;
      const questionText = questionTextForContainer(container);
      const preferred = preferredBusinessQuestionnaireChoice(number, questionText, options);
      const chosen = preferred || options[isPurposeQuestion(questionText) ? 0 : options.length - 1];
      if (choiceSelected(chosen.element)) {
        clicked.push({
          group: `data-number-${number}`,
          question_text: questionText.slice(0, 160),
          text: chosen.text.slice(0, 160),
          tag: String(chosen.element.tagName || '').toLowerCase(),
          selector: cssSelector(chosen.element),
          question_number: Number(number),
          choice_rule: preferred ? 'business-safe-option' : (isPurposeQuestion(questionText) ? 'purpose-first-option' : 'non-purpose-last-option'),
          click_strategy: 'js-custom-questionnaire-already-selected',
          already_selected: true,
        });
        continue;
      }
      const clickedElement = clickLikeUser(chosen.element);
      clicked.push({
        group: `data-number-${number}`,
        question_text: questionText.slice(0, 160),
        text: chosen.text.slice(0, 160),
        tag: String(clickedElement.tagName || '').toLowerCase(),
        selector: cssSelector(clickedElement),
        question_number: Number(number),
        choice_rule: preferred ? 'business-safe-option' : (isPurposeQuestion(questionText) ? 'purpose-first-option' : 'non-purpose-last-option'),
        click_strategy: 'js-custom-questionnaire',
      });
    }
    fillQuestionnaireInlineInputs();
    if (clicked.length) {
      return { strategy: 'business_questionnaire_rule', clicked_count: clicked.length, clicked };
    }

    const elements = Array.from(document.querySelectorAll('body *')).filter(isVisible);
    let currentGroup = '';
    const fallbackGroups = new Map();
    for (const element of elements) {
      const tag = String(element.tagName || '').toLowerCase();
      const role = String(element.getAttribute('role') || '').toLowerCase();
      const inputType = String(element.getAttribute('type') || '').toLowerCase();
      const rawText = normalizedText(element);
      if (!rawText) continue;
      if (questionPattern.test(rawText) && !optionPattern.test(rawText)) {
        currentGroup = rawText.slice(0, 120);
        continue;
      }

      const isChoiceInput = tag === 'input' && ['radio', 'checkbox'].includes(inputType) && !element.disabled;
      const isChoiceRole = ['radio', 'checkbox'].includes(role);
      const candidateText = isChoiceInput ? labelTextForInput(element) : rawText;
      const isTextOption = optionPattern.test(candidateText);
      if (!isChoiceInput && !isChoiceRole && !isTextOption) continue;
      if (!isChoiceInput && !isChoiceRole && hasOptionChild(element)) continue;

      const rect = element.getBoundingClientRect();
      const inputName = isChoiceInput ? String(element.getAttribute('name') || '') : '';
      const groupKey = inputName ? `input-name-${inputName}` : (currentGroup || `visual-row-${Math.floor(rect.top / 90)}`);
      if (!fallbackGroups.has(groupKey)) fallbackGroups.set(groupKey, []);
      fallbackGroups.get(groupKey).push({ element, candidateText, tag, role, inputType, inputName, questionText: currentGroup });
    }
    for (const [groupKey, candidates] of fallbackGroups.entries()) {
      if (selectedGroups.has(groupKey) || !candidates.length) continue;
      selectedGroups.add(groupKey);
      const questionText = candidates[0].questionText || groupKey;
      const preferred = preferredBusinessQuestionnaireChoice(groupKey, questionText, candidates);
      const chosen = preferred || candidates[isPurposeQuestion(questionText) ? 0 : candidates.length - 1];
      const target = clickableFor(chosen.element);
      if (choiceSelected(chosen.element)) {
        clicked.push({
          group: groupKey,
          question_text: questionText.slice(0, 160),
          text: chosen.candidateText.slice(0, 160),
          tag: chosen.tag,
          role: chosen.role,
          input_type: chosen.inputType,
          input_name: chosen.inputName,
          choice_rule: preferred ? 'business-safe-option' : (isPurposeQuestion(questionText) ? 'purpose-first-option' : 'non-purpose-last-option'),
          click_strategy: 'js-questionnaire-already-selected',
          already_selected: true,
        });
        continue;
      }
      target.scrollIntoView({ block: 'center', inline: 'nearest' });
      target.click();
      clicked.push({
        group: groupKey,
        question_text: questionText.slice(0, 160),
        text: chosen.candidateText.slice(0, 160),
        tag: chosen.tag,
        role: chosen.role,
        input_type: chosen.inputType,
        input_name: chosen.inputName,
        choice_rule: preferred ? 'business-safe-option' : (isPurposeQuestion(questionText) ? 'purpose-first-option' : 'non-purpose-last-option'),
      });
    }
    fillQuestionnaireInlineInputs();
    return { strategy: 'business_questionnaire_rule', clicked_count: clicked.length, clicked };
  });
  log({ type: 'questionnaire', message: `answered ${result.clicked_count} question groups`, answer_result: result });
  if (!result.clicked_count) {
    throw new Error('Questionnaire answer strategy did not find clickable options');
  }
  await sleep(500);
  return result;
}

async function verifyAgent3SuitabilityChoiceSelection(page, marker) {
  return await page.evaluate((rawMarker) => {
    const marker = String(rawMarker || '');
    const target = document.querySelector(`[data-agent3-suitability-target="${CSS.escape(marker)}"]`);
    const root = target?.closest?.('label,.answer-radio-item,.answer-multiple-select-item,[role="radio"],[role="checkbox"],li,div') || target;
    const related = [target, root].filter(Boolean);
    for (const node of related) {
      const input = node.matches?.('input[type="radio"],input[type="checkbox"]')
        ? node
        : node.querySelector?.('input[type="radio"],input[type="checkbox"]');
      if (input?.checked) {
        return { selected: true, marker, input_checked: true, class_name: String(node.className || '') };
      }
      const className = String(node.className || '');
      if (/checked|active|selected|is-checked|am-radio-checked|am-checkbox-checked/.test(className)) {
        return { selected: true, marker, input_checked: Boolean(input?.checked), class_name: className };
      }
      const selectedChild = Array.from(node.querySelectorAll?.('*') || []).some(child => {
        const childClassName = String(child.className || '');
        return /checked|active|selected|is-checked|am-radio-checked|am-checkbox-checked/.test(childClassName)
          || child.getAttribute?.('aria-checked') === 'true';
      });
      if (selectedChild || node.getAttribute?.('aria-checked') === 'true') {
        return { selected: true, marker, input_checked: Boolean(input?.checked), class_name: className };
      }
    }
    return { selected: false, marker, input_checked: false, class_name: String(root?.className || target?.className || '') };
  }, marker).catch(error => ({ selected: false, marker, error: String(error?.message || error) }));
}

async function forceAgent3SuitabilityChoiceSelection(page, marker) {
  const result = await page.evaluate((rawMarker) => {
    const marker = String(rawMarker || '');
    const target = document.querySelector(`[data-agent3-suitability-target="${CSS.escape(marker)}"]`);
    const root = target?.closest?.('label,.answer-radio-item,.answer-multiple-select-item,[role="radio"],[role="checkbox"],li,div') || target;
    const input = document.querySelector(`[data-agent3-suitability-input="${CSS.escape(marker)}"]`)
      || root?.querySelector?.('input[type="radio"],input[type="checkbox"]')
      || target?.querySelector?.('input[type="radio"],input[type="checkbox"]');
    function selected() {
      if (input?.checked) return true;
      const nodes = [target, root].filter(Boolean);
      return nodes.some(node => {
        const className = String(node.className || '');
        if (/checked|active|selected|is-checked|am-radio-checked|am-checkbox-checked/.test(className)) return true;
        return Array.from(node.querySelectorAll?.('*') || []).some(child => {
          const childClassName = String(child.className || '');
          return /checked|active|selected|is-checked|am-radio-checked|am-checkbox-checked/.test(childClassName)
            || child.getAttribute?.('aria-checked') === 'true';
        });
      });
    }
    if (!target || !input) {
      return { forced: false, marker, reason: 'choice input not found', selected_after: selected() };
    }
    const descriptor = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'checked');
    if (descriptor?.set) descriptor.set.call(input, true);
    else input.checked = true;
    input.setAttribute('checked', 'checked');
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
    input.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
    const control = input.closest('.am-checkbox,.am-radio') || root?.querySelector?.('.am-checkbox,.am-radio');
    const label = input.closest('label') || root;
    if (String(input.type || '').toLowerCase() === 'checkbox') {
      control?.classList?.add('am-checkbox-checked');
      label?.classList?.add('am-checkbox-wrapper-checked');
    } else {
      control?.classList?.add('am-radio-checked');
      label?.classList?.add('am-radio-wrapper-checked');
    }
    return {
      forced: true,
      marker,
      input_type: String(input.type || ''),
      selected_after: selected(),
      class_name: String((label || root || target).className || ''),
    };
  }, marker).catch(error => ({ forced: false, marker, reason: String(error?.message || error), selected_after: false }));
  log({ type: 'agent3-suitability-force-selection', message: 'forced Agent3 suitability choice selected state', result, url: page.url() });
  return result;
}

async function prepareAgent3SuitabilityChoiceForPlaywrightClick(page, marker) {
  const result = await page.evaluate((rawMarker) => {
    const marker = String(rawMarker || '');
    const target = document.querySelector(`[data-agent3-suitability-target="${CSS.escape(marker)}"]`);
    const root = target?.closest?.('label,.answer-radio-item,.answer-multiple-select-item,[role="radio"],[role="checkbox"],li,div') || target;
    if (!target || !root) return { prepared: false, marker, reason: 'marked target not found' };
    const touched = [];
    const rootInput = root.querySelector?.('input[type="radio"],input[type="checkbox"]');
    const name = rootInput?.getAttribute?.('name') || '';
    const type = rootInput?.getAttribute?.('type') || '';
    const controls = name
      ? Array.from(document.querySelectorAll(`input[type="${CSS.escape(type)}"][name="${CSS.escape(name)}"]`))
      : [rootInput].filter(Boolean);
    for (const input of controls) {
      const optionRoot = input.closest?.('label,.answer-radio-item,.answer-multiple-select-item,[role="radio"],[role="checkbox"],li,div') || input;
      if (input !== rootInput) continue;
      input.checked = false;
      input.removeAttribute('checked');
      touched.push({ tag: 'input', name, type });
      for (const node of [optionRoot, ...Array.from(optionRoot.querySelectorAll?.('*') || [])]) {
        const className = String(node.className || '');
        const nextClassName = className
          .replace(/\bam-checkbox-checked\b/g, '')
          .replace(/\bam-radio-checked\b/g, '')
          .replace(/\bis-checked\b/g, '')
          .replace(/\bchecked\b/g, '')
          .replace(/\bactive\b/g, '')
          .replace(/\bselected\b/g, '')
          .replace(/\s+/g, ' ')
          .trim();
        if (nextClassName !== className) {
          node.className = nextClassName;
          touched.push({ tag: String(node.tagName || '').toLowerCase(), className });
        }
        if (node.getAttribute?.('aria-checked') === 'true') node.setAttribute('aria-checked', 'false');
      }
    }
    root.scrollIntoView({ block: 'center', inline: 'center' });
    return {
      prepared: true,
      marker,
      target_tag: String(target.tagName || '').toLowerCase(),
      root_tag: String(root.tagName || '').toLowerCase(),
      touched_count: touched.length,
      touched: touched.slice(0, 12),
    };
  }, marker).catch(error => ({ prepared: false, marker, reason: String(error?.message || error) }));
  log({ type: 'agent3-suitability-playwright-click-prepare', message: 'prepared Agent3 suitability choice for trusted click', result, url: page.url() });
  return result;
}

async function applyAgent3SuitabilityAnswer(page, action) {
  const result = await page.evaluate(async (rawAction) => {
    const norm = value => String(value || '').replace(/\s+/g, ' ').trim();
    const compact = value => String(value || '').replace(/\s+/g, '').trim();
    const visible = element => {
      if (!element) return false;
      const style = window.getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.visibility !== 'hidden'
        && style.display !== 'none'
        && rect.width > 0
        && rect.height > 0;
    };
    const text = String(rawAction?.text || '');
    const selector = String(rawAction?.selector || '');
    const selectorText = String(selector.match(/text=(.+)$/)?.[1] || '');
    const questionMatch = text.match(/Q\s*(\d+)/i) || selector.match(/data-number=["']?(\d+)/i);
    const questionNumber = questionMatch ? Number(questionMatch[1]) : null;
    const letterMatch = text.match(/Q\s*\d+\s*=\s*([A-H])/i)
      || selectorText.match(/^([A-H])[\.\u3001:：]/i)
      || text.match(/=\s*([A-H])[\.\u3001:：]/i);
    const desiredLetter = letterMatch ? letterMatch[1].toUpperCase() : '';
    const amountMatch = text.match(/金额\s*=\s*([0-9]+(?:\.[0-9]+)?)/) || text.match(/=\s*([0-9]+(?:\.[0-9]+)?)\s*$/);
    const inlineIndexMatch = selector.match(/inline-input:nth\((\d+)\)/);
    const inlineIndex = inlineIndexMatch ? Number(inlineIndexMatch[1]) : null;
    const clicked = [];
    function cssSelector(element) {
      if (element.id) return `#${CSS.escape(element.id)}`;
      const dataNumber = element.closest('[data-number]')?.getAttribute('data-number');
      const className = String(element.className || '').split(/\s+/).filter(Boolean)[0];
      if (dataNumber && className) return `[data-number="${CSS.escape(dataNumber)}"] .${CSS.escape(className)}`;
      if (className) return `${element.tagName.toLowerCase()}.${CSS.escape(className)}`;
      return element.tagName.toLowerCase();
    }
    function hasOptionChild(element) {
      return Array.from(element.children || []).some(child => visible(child) && /^[A-H][\.\u3001:：]/i.test(norm(child.innerText || child.textContent)));
    }
    async function dispatchLikeUser(element) {
      const chain = [];
      let current = element;
      while (current && chain.length < 5) {
        chain.push(current);
        if (current.matches?.('label,button,a,[role="button"],[role="radio"],[role="checkbox"],li,.answer-radio-item,.answer-multiple-select-item,.insure-label')) break;
        current = current.parentElement;
      }
      const pause = ms => new Promise(resolve => window.setTimeout(resolve, ms));
      const reactInvoked = [];
      const invokedHandlers = new WeakSet();
      const getFiber = node => {
        if (!node) return null;
        const key = Object.keys(node).find(item => item.startsWith('__reactFiber$') || item.startsWith('__reactInternalInstance$'));
        return key ? node[key] : null;
      };
      const propsFromNode = node => {
        if (!node) return null;
        const key = Object.keys(node).find(item => item.startsWith('__reactProps$') || item.startsWith('__reactEventHandlers$'));
        return key ? node[key] : null;
      };
      const invokeReactHandler = async (handler, node, source, handlerName) => {
        if (typeof handler !== 'function' || !node || invokedHandlers.has(handler)) return false;
        invokedHandlers.add(handler);
        const eventType = String(handlerName || '').replace(/^on/, '').toLowerCase() || 'click';
        const eventLike = {
          type: eventType,
          target: node,
          currentTarget: node,
          bubbles: true,
          cancelable: true,
          defaultPrevented: false,
          propagationStopped: false,
          touches: [],
          changedTouches: [],
          preventDefault() { this.defaultPrevented = true; },
          stopPropagation() { this.propagationStopped = true; },
          persist() {},
          isDefaultPrevented() { return this.defaultPrevented; },
          isPropagationStopped() { return this.propagationStopped; },
          nativeEvent: { target: node, currentTarget: node, type: eventType },
        };
        try {
          const output = handler(eventLike);
          if (output && typeof output.then === 'function') await output;
          reactInvoked.push({
            source,
            handler: handlerName,
            tag: String(node.tagName || '').toLowerCase(),
            default_prevented: Boolean(eventLike.defaultPrevented),
          });
          return true;
        } catch (error) {
          reactInvoked.push({
            source: 'agent3_suitability_react_handler_error',
            handler: handlerName,
            original_source: source,
            tag: String(node.tagName || '').toLowerCase(),
            error: String(error?.message || error).slice(0, 160),
          });
          return false;
        }
      };
      const invokeHandlersFromProps = async (props, node, source) => {
        for (const handlerName of ['onTouchStart', 'onTouchEnd', 'onMouseDown', 'onMouseUp', 'onClick']) {
          await invokeReactHandler(props?.[handlerName], node, source, handlerName);
          await pause(20);
          if (isAlreadySelected(element)) break;
        }
      };
      const invokeNativeOnClick = async node => {
        if (!node || typeof node.onclick !== 'function') return false;
        return await invokeReactHandler(node.onclick, node, 'agent3_suitability_native_onclick', 'onClick');
      };
      const invokeReactHandlers = async node => {
        let current = node;
        for (let depth = 0; current && current !== document.body && depth < 8; depth += 1, current = current.parentElement) {
          await invokeHandlersFromProps(propsFromNode(current), current, 'agent3_suitability_react_click.props');
          let fiber = getFiber(current);
          let fiberDepth = 0;
          while (fiber && fiberDepth < 12) {
            const fiberProps = fiber.memoizedProps || fiber.pendingProps || fiber._currentElement?.props;
            await invokeHandlersFromProps(fiberProps, current, 'agent3_suitability_react_click.fiber');
            fiber = fiber.return || fiber._hostParent || fiber._currentElement?._owner;
            fiberDepth += 1;
            if (isAlreadySelected(element)) break;
          }
          if (isAlreadySelected(element)) break;
        }
      };
      const dispatchEventLikeTap = target => {
        for (const type of ['pointerdown', 'touchstart', 'touchend', 'pointerup', 'mousedown', 'mouseup']) {
          let event;
          try {
            event = /^touch/.test(type)
              ? new TouchEvent(type, { bubbles: true, cancelable: true, view: window })
              : new MouseEvent(type, { bubbles: true, cancelable: true, view: window });
          } catch (_) {
            event = new Event(type, { bubbles: true, cancelable: true });
          }
          target.dispatchEvent(event);
        }
      };
      for (const target of chain) {
        target.scrollIntoView({ block: 'center', inline: 'center' });
        dispatchEventLikeTap(target);
        await invokeNativeOnClick(target);
        await invokeReactHandlers(target);
        if (!isAlreadySelected(element)) {
          try { target.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window })); } catch (_) {}
          try { target.click?.(); } catch (_) {}
        }
        target.dispatchEvent(new Event('input', { bubbles: true }));
        target.dispatchEvent(new Event('change', { bubbles: true }));
        await pause(60);
        if (isAlreadySelected(element)) break;
      }
      return { target: chain[chain.length - 1] || element, react_choice_click: reactInvoked };
    }
    function isAlreadySelected(element) {
      const related = [
        element,
        element.closest?.('label,.answer-radio-item,.answer-multiple-select-item,[role="radio"],[role="checkbox"],li,div'),
      ].filter(Boolean);
      for (const node of related) {
        const input = node.matches?.('input[type="radio"],input[type="checkbox"]')
          ? node
          : node.querySelector?.('input[type="radio"],input[type="checkbox"]');
        if (input?.checked) return true;
        const className = String(node.className || '');
        if (/checked|active|selected|is-checked|am-radio-checked|am-checkbox-checked/.test(className)) return true;
        const selectedChild = Array.from(node.querySelectorAll?.('*') || []).some(child => {
          const childClassName = String(child.className || '');
          return /checked|active|selected|is-checked|am-radio-checked|am-checkbox-checked/.test(childClassName)
            || child.getAttribute?.('aria-checked') === 'true';
        });
        if (selectedChild) return true;
        if (node.getAttribute?.('aria-checked') === 'true') return true;
      }
      return false;
    }
    function choiceSelectionState(element) {
      return { selected: isAlreadySelected(element) };
    }
    function choiceClickTarget(element) {
      const root = element.closest?.('label,.answer-radio-item,.answer-multiple-select-item,[role="radio"],[role="checkbox"],li') || element;
      return root.querySelector?.('.am-checkbox,.am-radio,.am-checkbox-inner,.am-radio-inner')
        || root
        || element;
    }
    function markChoiceTarget(element) {
      const marker = `agent3-suitability-${Date.now()}-${Math.random().toString(16).slice(2)}`;
      const target = choiceClickTarget(element);
      target.setAttribute('data-agent3-suitability-target', marker);
      const root = target.closest?.('label,.answer-radio-item,.answer-multiple-select-item,[role="radio"],[role="checkbox"],li') || target;
      const input = root.querySelector?.('input[type="radio"],input[type="checkbox"]') || target.querySelector?.('input[type="radio"],input[type="checkbox"]');
      if (input) input.setAttribute('data-agent3-suitability-input', marker);
      return { marker, target, input };
    }
    if (amountMatch) {
      const inputs = Array.from(document.querySelectorAll('input.inline-input, .adapt-question-wrap input, .js-adapt-question-content input, textarea'))
        .filter(element => {
          if (!visible(element) || element.disabled || element.readOnly) return false;
          const type = String(element.getAttribute('type') || '').toLowerCase();
          return !['hidden', 'button', 'submit', 'reset', 'file', 'radio', 'checkbox'].includes(type);
        });
      const input = inlineIndex == null ? inputs.find(item => !String(item.value || '').trim()) || inputs[0] : inputs[inlineIndex];
      if (!input) return { applied: false, reason: 'agent3 suitability amount input not found', action_text: text, selector };
      const value = amountMatch[1];
      input.scrollIntoView({ block: 'center', inline: 'center' });
      const descriptor = Object.getOwnPropertyDescriptor(input.constructor.prototype, 'value');
      if (descriptor?.set) descriptor.set.call(input, value);
      else input.value = value;
      input.setAttribute('value', value);
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.dispatchEvent(new Event('change', { bubbles: true }));
      input.dispatchEvent(new Event('blur', { bubbles: true }));
      return {
        applied: true,
        kind: 'amount',
        value,
        inline_index: inlineIndex,
        selector: cssSelector(input),
        action_text: text,
      };
    }
    if (!desiredLetter) {
      return { applied: false, reason: 'agent3 suitability option letter not found', action_text: text, selector };
    }
    const containers = questionNumber == null
      ? [document.body]
      : Array.from(document.querySelectorAll(`[data-number="${questionNumber}"], .adapt-question-wrap [data-number="${questionNumber}"], .js-adapt-question-content [data-number="${questionNumber}"]`));
    const scopes = containers.length ? containers : [document.body];
    const candidates = [];
    for (const scope of scopes) {
      for (const element of Array.from(scope.querySelectorAll('input.insure-label, input[type="button"], button, [role="button"], [role="radio"], [role="checkbox"], label, .insure-label, .answer-radio-item, .answer-multiple-select-item, [class*="option"], [class*="answer"], li, span, div'))) {
        if (!visible(element) || hasOptionChild(element)) continue;
        const candidateText = norm(element.innerText || element.textContent || element.getAttribute?.('value') || '');
        const probe = compact(candidateText);
        const targetProbe = compact(selectorText);
        if (!candidateText) continue;
        const startsWithLetter = new RegExp(`^${desiredLetter}[\\\\.\\\\u3001:：]`, 'i').test(probe);
        const selectorMatch = targetProbe && (probe.includes(targetProbe) || targetProbe.includes(probe));
        if (!startsWithLetter && !selectorMatch) continue;
        candidates.push({ element, text: candidateText, selectorMatch, startsWithLetter });
      }
    }
    candidates.sort((left, right) => {
      if (left.selectorMatch !== right.selectorMatch) return left.selectorMatch ? -1 : 1;
      return left.text.length - right.text.length;
    });
    const chosen = candidates[0];
    if (!chosen) {
      return { applied: false, reason: 'agent3 suitability option not found on page', desired_letter: desiredLetter, question_number: questionNumber, action_text: text, selector };
    }
    const marked = markChoiceTarget(chosen.element);
    if (isAlreadySelected(chosen.element)) {
      return {
        applied: true,
        kind: 'option',
        desired_letter: desiredLetter,
        question_number: questionNumber,
        action_text: text,
        marker: marked.marker,
        selected_after: true,
        already_selected: true,
        clicked: [{
          text: chosen.text.slice(0, 180),
          selector: cssSelector(chosen.element),
        }],
      };
    }
    const clickResult = await dispatchLikeUser(marked.target);
    const clickedElement = clickResult.target;
    const selectionState = choiceSelectionState(chosen.element);
    clicked.push({
      text: chosen.text.slice(0, 180),
      selector: cssSelector(clickedElement),
      click_strategy: 'react-touch-choice-click',
      react_choice_click: clickResult.react_choice_click,
    });
    return {
      applied: true,
      kind: 'option',
      desired_letter: desiredLetter,
      question_number: questionNumber,
      action_text: text,
      marker: marked.marker,
      selected_after: selectionState.selected,
      selection_state: selectionState,
      clicked,
    };
  }, action).catch(error => ({ applied: false, reason: String(error?.message || error), action_text: action?.text || '', selector: action?.selector || '' }));
  if (result?.applied && result.kind === 'option' && result.marker && !result.already_selected) {
    const target = page.locator(`[data-agent3-suitability-target="${result.marker}"]`).first();
    const visible = await target.isVisible({ timeout: 1200 }).catch(() => false);
    if (visible) {
      result.playwright_trusted_resync_prepare = await prepareAgent3SuitabilityChoiceForPlaywrightClick(page, result.marker);
      await target.scrollIntoViewIfNeeded({ timeout: 3000 }).catch(() => undefined);
      await target.click({ timeout: 5000, noWaitAfter: true }).catch(async () => {
        await target.click({ timeout: 5000, noWaitAfter: true, force: true });
      });
      await sleep(300);
      const selectionState = await verifyAgent3SuitabilityChoiceSelection(page, result.marker);
      result.playwright_click = { clicked: true, strategy: 'playwright-trusted-choice-click', selection_state: selectionState };
      result.selected_after = Boolean(selectionState?.selected);
      result.selection_state = selectionState;
    } else {
      result.playwright_click = { clicked: false, strategy: 'playwright-trusted-choice-click', reason: 'marked target not visible' };
    }
    if (!result.selected_after) {
      const forcedSelection = await forceAgent3SuitabilityChoiceSelection(page, result.marker);
      result.forced_input_selection = forcedSelection;
      result.selected_after = Boolean(forcedSelection?.selected_after);
    }
  }
  log({ type: 'agent3-suitability-answer', message: `applied Agent3 suitability answer: ${action?.text || action?.selector || ''}`, result, url: page.url() });
  await sleep(250);
  return result;
}

async function applyAgent3SuitabilityAnswers(page, answerActions) {
  const results = [];
  for (const answerAction of answerActions || []) {
    results.push(await applyAgent3SuitabilityAnswer(page, answerAction));
  }
  return {
    strategy: 'agent3-suitability-replay',
    clicked_count: results.filter(item => item?.applied).length,
    clicked: results,
  };
}

async function acceptQuestionnaireWarningIfPresent(page) {
  const warning = page.getByText(/投保风险警示确认书|风险警示|适当性问卷匹配结果/).first();
  if (!(await warning.isVisible({ timeout: 1200 }).catch(() => false))) return null;

  const candidates = [
    page.locator('button, a, [role="button"], .btn, .button, input[type="button"], input[type="submit"]').filter({ hasText: /阅读并同意|已阅读并同意|继续投保|确认继续|我已知晓|同意/ }).last(),
    page.getByText(/阅读并同意|已阅读并同意|继续投保|确认继续|我已知晓|同意/).last(),
  ];
  for (const candidate of candidates) {
    if (!(await candidate.isVisible({ timeout: 1200 }).catch(() => false))) continue;
    const text = await candidate.innerText({ timeout: 1000 }).catch(async () => {
      return await candidate.inputValue({ timeout: 1000 }).catch(() => '');
    });
    await candidate.click({ timeout: 10000, noWaitAfter: true }).catch(async () => {
      await candidate.click({ timeout: 10000, noWaitAfter: true, force: true });
    });
    await page.waitForLoadState('domcontentloaded', { timeout: 15000 }).catch(() => undefined);
    await sleep(stepDelayMs);
    return {
      strategy: 'questionnaire-warning-confirm',
      accepted: true,
      text: String(text || '').slice(0, 80),
    };
  }
  return {
    strategy: 'questionnaire-warning-confirm',
    accepted: false,
    text: 'warning visible but confirm button not found',
  };
}

function agent3LocatorCandidates(action) {
  const candidates = [];
  const selector = String(action.selector || '').trim();
  if (selector) candidates.push({ by: 'selector', value: selector, source: 'action.selector' });
  for (const locator of Array.isArray(action.locators) ? action.locators : []) {
    const by = String(locator.by || '').trim();
    const value = String(locator.value || '').trim();
    if (!value) continue;
    if (by === 'selector' || by === 'text' || by === 'role:button') {
      candidates.push({ by, value, source: 'action.locators' });
    }
  }
  const seen = new Set();
  return candidates.filter(candidate => {
    const key = `${candidate.by}:${candidate.value}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

async function clickActionByAgent3Locator(page, action) {
  for (const candidate of agent3LocatorCandidates(action)) {
    let locator = null;
    if (candidate.by === 'selector') {
      locator = page.locator(candidate.value).first();
    } else if (candidate.by === 'role:button') {
      locator = page.getByRole('button', { name: candidate.value }).first();
    } else if (candidate.by === 'text') {
      locator = page.getByText(candidate.value).first();
    }
    if (!locator) continue;
    const visible = await locator.isVisible({ timeout: 1500 }).catch(() => false);
    if (!visible) continue;
    const text = await locator.innerText({ timeout: 1000 }).catch(async () => {
      return await locator.getAttribute('value', { timeout: 1000 }).catch(() => '');
    });
    await locator.click({ timeout: 10000, noWaitAfter: true }).catch(async () => {
      await locator.click({ timeout: 10000, noWaitAfter: true, force: true });
    });
    return {
      clicked: true,
      text,
      strategy: 'agent3-locator-replay',
      locator: candidate,
    };
  }
  return { clicked: false, strategy: 'agent3-locator-replay', reason: 'agent3 locator not visible' };
}

function compactActionText(value) {
  return String(value || '').replace(/\s+/g, '').trim();
}

function isPremiumQuoteAction(action) {
  const strategy = String(action?.click_strategy || '').toLowerCase();
  const text = compactActionText(action?.text);
  return strategy.includes('premium-quote') || text.includes('保费试算');
}

function isH5ProductFooterInsureAction(action) {
  return String(action?.click_strategy || '').toLowerCase().includes('mouse-h5-product-footer-insure');
}

async function tapLocatorCenter(page, locator) {
  await locator.scrollIntoViewIfNeeded({ timeout: 5000 }).catch(() => undefined);
  const box = await locator.boundingBox().catch(() => null);
  if (!box) {
    await locator.click({ timeout: 5000, noWaitAfter: true, force: true });
    return { x: null, y: null, method: 'force-click' };
  }
  const x = box.x + box.width / 2;
  const y = box.y + box.height / 2;
  await page.touchscreen.tap(x, y).catch(async () => {
    await page.mouse.click(x, y).catch(async () => {
      await locator.click({ timeout: 5000, noWaitAfter: true, force: true });
    });
  });
  return { x, y, method: 'touchscreen-center' };
}

async function clickH5SubmitLocator(page, locator) {
  const tap = await tapLocatorCenter(page, locator);
  await sleep(250);
  let mouseClick = null;
  const box = await locator.boundingBox().catch(() => null);
  if (box) {
    const x = box.x + box.width / 2;
    const y = box.y + box.height / 2;
    await page.mouse.click(x, y).catch(() => undefined);
    mouseClick = { x, y, method: 'mouse-center' };
    await sleep(250);
  }
  const domClick = await locator.evaluate(async element => {
    function submitClickableAncestor(element) {
      const compact = value => String(value || '').replace(/\s+/g, '').trim();
      const viewportWidth = window.innerWidth || document.documentElement.clientWidth || 390;
      const visible = node => {
        if (!node) return false;
        const rect = node.getBoundingClientRect();
        const style = window.getComputedStyle(node);
        return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
      };
      let best = element.closest('button, a, [role="button"], .am-button, .adm-button, .btn, .button, input[type="button"], input[type="submit"], [onclick]') || element;
      let bestScore = 0;
      for (let current = element; current && current !== document.body; current = current.parentElement) {
        if (!visible(current)) continue;
        const rect = current.getBoundingClientRect();
        if (rect.height > 180 || rect.width > viewportWidth * 0.96) continue;
        const text = compact(current.innerText || current.textContent || current.getAttribute?.('value') || '');
        const marker = `${current.tagName || ''} ${current.className || ''} ${current.getAttribute?.('role') || ''}`;
        if (!/提交订单|提交投保单|提交投保|提交/i.test(text)) continue;
        const clickableLike = current.matches('button, a, [role="button"], input[type="button"], input[type="submit"], [onclick]')
          || /submit|button|btn|footer|am-button|adm-button/i.test(marker);
        if (!clickableLike) continue;
        let score = rect.width + rect.height;
        if (/submit|button|btn|footer|am-button|adm-button|insure/i.test(marker)) score += 500;
        if (current.matches('button, a, [role="button"], input[type="button"], input[type="submit"], [onclick]')) score += 300;
        if (rect.width >= 120) score += 400;
        if (score > bestScore) {
          best = current;
          bestScore = score;
        }
      }
      return best;
    }
    function submitAncestorChain(element) {
      const chain = [];
      for (let current = element, depth = 0; current && current !== document.body && depth < 10; current = current.parentElement, depth += 1) {
        const rect = current.getBoundingClientRect();
        chain.push({
          tag: String(current.tagName || '').toLowerCase(),
          className: String(current.className || '').slice(0, 180),
          role: String(current.getAttribute?.('role') || ''),
          text: String(current.innerText || current.textContent || current.getAttribute?.('value') || '').replace(/\s+/g, ' ').trim().slice(0, 120),
          rect: { x: rect.x, y: rect.y, width: rect.width, height: rect.height },
          has_onclick: Boolean(current.onclick || current.getAttribute?.('onclick')),
        });
      }
      return chain;
    }
    const target = submitClickableAncestor(element);
    const reactInvoked = [];
    const getFiber = node => {
      if (!node) return null;
      const key = Object.keys(node).find(item => item.startsWith('__reactFiber$') || item.startsWith('__reactInternalInstance$'));
      return key ? node[key] : null;
    };
    const propsFromNode = node => {
      if (!node) return null;
      const key = Object.keys(node).find(item => item.startsWith('__reactProps$') || item.startsWith('__reactEventHandlers$'));
      return key ? node[key] : null;
    };
    const invokeReactHandler = async (handler, node, source, handlerName) => {
      if (typeof handler !== 'function' || !node) return false;
      const eventType = String(handlerName || '').replace(/^on/, '').toLowerCase() || 'click';
      const eventLike = {
        type: eventType,
        target: node,
        currentTarget: node,
        bubbles: true,
        cancelable: true,
        defaultPrevented: false,
        propagationStopped: false,
        preventDefault() { this.defaultPrevented = true; },
        stopPropagation() { this.propagationStopped = true; },
        persist() {},
        isDefaultPrevented() { return this.defaultPrevented; },
        isPropagationStopped() { return this.propagationStopped; },
        nativeEvent: { target: node, currentTarget: node, type: eventType },
      };
      try {
        const output = handler(eventLike);
        if (output && typeof output.then === 'function') await output;
        reactInvoked.push({
          source,
          handler: handlerName,
          tag: String(node.tagName || '').toLowerCase(),
          default_prevented: Boolean(eventLike.defaultPrevented),
        });
        return true;
      } catch (error) {
        reactInvoked.push({
          source: 'react_submit_handler_error',
          handler: handlerName,
          original_source: source,
          tag: String(node.tagName || '').toLowerCase(),
          error: String(error?.message || error).slice(0, 160),
        });
        return false;
      }
    };
    const invokeHandlersFromProps = async (props, node, source) => {
      for (const handlerName of ['onTouchStart', 'onTouchEnd', 'onMouseDown', 'onMouseUp', 'onClick']) {
        await invokeReactHandler(props?.[handlerName], node, source, handlerName);
      }
    };
    const invokeNativeOnClick = async node => {
      if (!node || typeof node.onclick !== 'function') return false;
      return await invokeReactHandler(node.onclick, node, 'react_submit_native_onclick', 'onClick');
    };
    const invokeReactHandlers = async node => {
      let current = node;
      for (let depth = 0; current && current !== document.body && depth < 8; depth += 1, current = current.parentElement) {
        const props = propsFromNode(current);
        await invokeHandlersFromProps(props, current, 'react_submit_click.props');
        let fiber = getFiber(current);
        let fiberDepth = 0;
        while (fiber && fiberDepth < 12) {
          const fiberProps = fiber.memoizedProps || fiber.pendingProps || fiber._currentElement?.props;
          await invokeHandlersFromProps(fiberProps, current, 'react_submit_click.fiber');
          fiber = fiber.return || fiber._hostParent || fiber._currentElement?._owner;
          fiberDepth += 1;
        }
      }
    };
    target.scrollIntoView({ block: 'center', inline: 'center' });
    await invokeNativeOnClick(target);
    await invokeReactHandlers(target);
    for (const type of ['pointerdown', 'touchstart', 'touchend', 'pointerup', 'mousedown', 'mouseup', 'click']) {
      let event;
      try {
        event = /^touch/.test(type)
          ? new TouchEvent(type, { bubbles: true, cancelable: true, view: window })
          : new MouseEvent(type, { bubbles: true, cancelable: true, view: window });
      } catch (_) {
        event = new Event(type, { bubbles: true, cancelable: true });
      }
      target.dispatchEvent(event);
    }
    try { target.click(); } catch (_) {}
    return {
      clicked: true,
      tag: String(target.tagName || '').toLowerCase(),
      text: String(target.innerText || target.textContent || target.getAttribute?.('value') || '').replace(/\s+/g, ' ').trim().slice(0, 80),
      react_submit_click: reactInvoked,
      submit_ancestor_chain: submitAncestorChain(element),
    };
  }).catch(error => ({ clicked: false, error: String(error?.message || error) }));
  await sleep(500);
  return { ...tap, method: 'touchscreen+mouse+dom-center', mouse_click: mouseClick, dom_click: domClick };
}

async function exactBottomH5BuyNowButtonLocator(page) {
  const token = `agent-h5-exact-buy-now-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const marked = await page.evaluate((token) => {
    function isVisible(element) {
      const style = window.getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.visibility !== 'hidden'
        && style.display !== 'none'
        && rect.width > 0
        && rect.height > 0;
    }
    function textOf(element) {
      return String(element.innerText || element.textContent || element.getAttribute?.('value') || '').replace(/\s+/g, ' ').trim();
    }
    function compact(value) {
      return String(value || '').replace(/\s+/g, '').trim();
    }
    function isFixedOrSticky(element) {
      let current = element;
      while (current && current !== document.body) {
        const position = window.getComputedStyle(current).position;
        if (position === 'fixed' || position === 'sticky') return true;
        current = current.parentElement;
      }
      return false;
    }

    const viewportHeight = window.innerHeight || document.documentElement.clientHeight || 844;
    const viewportWidth = window.innerWidth || document.documentElement.clientWidth || 390;
    const exactBuyButtonText = /^(投保|立即投保|马上投保|去投保|继续投保)$/;
    const candidates = Array.from(document.querySelectorAll([
      'button',
      'a',
      '[role="button"]',
      '.am-button',
      '.adm-button',
      '.submit-btn',
      '[class*="button"]',
      '[class*="btn"]',
      'div',
      'span',
    ].join(',')))
      .filter(isVisible)
      .map(element => {
        const rect = element.getBoundingClientRect();
        const text = textOf(element);
        const normalized = compact(text);
        const fixed = isFixedOrSticky(element);
        const centerX = rect.left + rect.width / 2;
        const centerY = rect.top + rect.height / 2;
        const nearBottom = centerY >= viewportHeight * 0.62 || fixed;
        const onRightSide = centerX >= viewportWidth * 0.45 || fixed;
        if (!exactBuyButtonText.test(normalized) || !nearBottom || !onRightSide) {
          return null;
        }
        const bottomScore = Math.max(0, 6000 - Math.abs(viewportHeight - centerY) * 10);
        const rightScore = Math.max(0, 2500 - Math.abs(viewportWidth * 0.82 - centerX) * 4);
        const fixedScore = fixed ? 12000 : 0;
        const sizeScore = Math.min(1200, rect.width + rect.height);
        return {
          element,
          text,
          normalized,
          score: fixedScore + bottomScore + rightScore + sizeScore + 10000,
          rect: { x: rect.x, y: rect.y, width: rect.width, height: rect.height },
        };
      })
      .filter(Boolean)
      .sort((left, right) => right.score - left.score);
    const target = candidates[0];
    if (!target) return null;
    document.querySelectorAll('[data-agent-h5-exact-buy-now-button]').forEach(node => node.removeAttribute('data-agent-h5-exact-buy-now-button'));
    target.element.setAttribute('data-agent-h5-exact-buy-now-button', token);
    return { text: target.text, score: target.score, rect: target.rect, normalized: target.normalized };
  }, token).catch(() => null);
  if (!marked) return null;
  const locator = page.locator(`[data-agent-h5-exact-buy-now-button="${token}"]`).first();
  if (!(await locator.isVisible({ timeout: 800 }).catch(() => false))) return null;
  return { locator, ...marked };
}

async function bestH5BuyNowButtonLocator(page) {
  const token = `agent-h5-buy-now-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const marked = await page.evaluate((token) => {
    function isVisible(element) {
      const style = window.getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.visibility !== 'hidden'
        && style.display !== 'none'
        && rect.width > 0
        && rect.height > 0;
    }
    function textOf(element) {
      return String(element.innerText || element.textContent || element.getAttribute?.('value') || '').replace(/\s+/g, ' ').trim();
    }
    function compact(value) {
      return String(value || '').replace(/\s+/g, '').trim();
    }
    function isFixedOrSticky(element) {
      let current = element;
      while (current && current !== document.body) {
        const position = window.getComputedStyle(current).position;
        if (position === 'fixed' || position === 'sticky') return true;
        current = current.parentElement;
      }
      return false;
    }

    const viewportHeight = window.innerHeight || document.documentElement.clientHeight || 844;
    const viewportWidth = window.innerWidth || document.documentElement.clientWidth || 390;
    const badCopy = /保费试算|投保须知|投保人|被保险人|告知书|声明|流程|说明|保障计划|用户评价|保险条款|常见问题|费率表|阅读/;
    const exactBuyButtonText = /^(投保|立即投保|马上投保|去投保|继续投保)$/;
    const buyText = /投\s*保|立即投保|马上投保|去投保|继续投保/;
    const candidates = Array.from(document.querySelectorAll([
      'button',
      'a',
      '[role="button"]',
      '.am-button',
      '.adm-button',
      '.submit-btn',
      '[class*="button"]',
      '[class*="btn"]',
      '[class*="footer"] button',
      '[class*="footer"] a',
      '[class*="footer"] [role="button"]',
      '[class*="footer"] div',
      '[class*="footer"] span',
    ].join(',')))
      .filter(isVisible)
      .map(element => {
        const rect = element.getBoundingClientRect();
        const text = textOf(element);
        const normalized = compact(text);
        const fixed = isFixedOrSticky(element);
        const nearBottom = rect.top >= viewportHeight * 0.55 || fixed;
        const exact = exactBuyButtonText.test(normalized);
        const copyPenalty = badCopy.test(normalized) && !exact ? -20000 : 0;
        const exactScore = exact ? 7000 : buyText.test(text) ? 1800 : -10000;
        const fixedScore = fixed ? 10000 : 0;
        const bottomScore = Math.max(0, 4000 - Math.abs(viewportHeight - (rect.top + rect.height / 2)) * 8);
        const rightScore = Math.max(0, 1600 - Math.abs(viewportWidth * 0.82 - (rect.left + rect.width / 2)) * 3);
        const sizeScore = Math.min(800, rect.width + rect.height);
        return {
          element,
          text,
          normalized,
          score: fixedScore + bottomScore + rightScore + sizeScore + exactScore + copyPenalty + (nearBottom ? 800 : 0),
          rect: { x: rect.x, y: rect.y, width: rect.width, height: rect.height },
        };
      })
      .filter(item => item.score > 0)
      .sort((left, right) => right.score - left.score);
    const target = candidates[0];
    if (!target) return null;
    document.querySelectorAll('[data-agent-h5-buy-now-button]').forEach(node => node.removeAttribute('data-agent-h5-buy-now-button'));
    target.element.setAttribute('data-agent-h5-buy-now-button', token);
    return { text: target.text, score: target.score, rect: target.rect, normalized: target.normalized };
  }, token).catch(() => null);
  if (!marked) return null;
  const locator = page.locator(`[data-agent-h5-buy-now-button="${token}"]`).first();
  if (!(await locator.isVisible({ timeout: 800 }).catch(() => false))) return null;
  return { locator, ...marked };
}

async function clickH5ProductFooterInsureAction(page, beforeUrl) {
  const attempts = [];
  const preSettleEvents = await settleProductEntryFlow(page);
  if (preSettleEvents.length) {
    attempts.push({ strategy: 'h5-pre-settle-product-entry', settle_events: preSettleEvents });
    if (await buyNowAdvanced(page, beforeUrl)) {
      return {
        clicked: true,
        advanced: true,
        strategy: 'mouse-h5-product-footer-insure',
        selector: 'pre-settle-product-entry',
        tap: null,
        settle_events: preSettleEvents,
        attempts,
      };
    }
  }
  const footerSelectors = [
    '.product-detail-footer',
    '[class*="product-detail-footer"]',
    '.product-footer',
    '.footer-bar',
    '.insure-footer',
    '[class*="footer"]',
  ];
  const insureText = /投\s*保|立即投保|马上投保|去投保|继续投保/;
  for (let scoreAttempt = 0; scoreAttempt < 3; scoreAttempt += 1) {
    const exactBottomBuyNow = await exactBottomH5BuyNowButtonLocator(page);
    if (exactBottomBuyNow) {
      attempts.push({
        strategy: 'h5-exact-bottom-buy-now',
        score: exactBottomBuyNow.score,
        text: exactBottomBuyNow.text,
        normalized: exactBottomBuyNow.normalized,
        rect: exactBottomBuyNow.rect,
        attempt: scoreAttempt + 1,
      });
      let tap = null;
      try {
        tap = await tapLocatorCenter(page, exactBottomBuyNow.locator);
      } catch (error) {
        attempts.push({ strategy: 'h5-exact-bottom-buy-now-error', error: String(error?.message || error), attempt: scoreAttempt + 1 });
      }
      await page.waitForLoadState('domcontentloaded', { timeout: 15000 }).catch(() => undefined);
      await sleep(stepDelayMs);
      const settle_events = await settleProductEntryFlow(page);
      const advanced = await buyNowAdvanced(page, beforeUrl);
      if (advanced) {
        return {
          clicked: true,
          advanced,
          strategy: 'mouse-h5-product-footer-insure',
          selector: 'exact-bottom-h5-buy-now',
          tap,
          settle_events,
          attempts,
        };
      }
      await sleep(500);
    }
    const scoredBuyNow = await bestH5BuyNowButtonLocator(page);
    if (!scoredBuyNow) break;
    attempts.push({
      strategy: 'h5-buy-now-bottom-fixed-score',
      score: scoredBuyNow.score,
      text: scoredBuyNow.text,
      normalized: scoredBuyNow.normalized,
      rect: scoredBuyNow.rect,
      attempt: scoreAttempt + 1,
    });
    let tap = null;
    try {
      tap = await tapLocatorCenter(page, scoredBuyNow.locator);
    } catch (error) {
      await page.waitForLoadState('domcontentloaded', { timeout: 5000 }).catch(() => undefined);
      await sleep(500);
      if (await buyNowAdvanced(page, beforeUrl)) {
        return {
          clicked: true,
          advanced: true,
          strategy: 'mouse-h5-product-footer-insure',
          selector: 'scored-h5-buy-now',
          tap: null,
          tap_error: String(error?.message || error),
          settle_events: [],
          attempts,
        };
      }
      attempts.push({ strategy: 'h5-buy-now-bottom-fixed-score-error', error: String(error?.message || error), attempt: scoreAttempt + 1 });
      continue;
    }
    await page.waitForLoadState('domcontentloaded', { timeout: 15000 }).catch(() => undefined);
    await sleep(stepDelayMs);
    const settle_events = await settleProductEntryFlow(page);
    const advanced = await buyNowAdvanced(page, beforeUrl);
    if (advanced) {
      return {
        clicked: true,
        advanced,
        strategy: 'mouse-h5-product-footer-insure',
        selector: 'scored-h5-buy-now',
        tap,
        settle_events,
        attempts,
      };
    }
    await sleep(500);
  }
  for (const selector of footerSelectors) {
    const footer = page.locator(selector).last();
    if (!(await footer.isVisible({ timeout: 800 }).catch(() => false))) continue;
    const target = footer
      .locator('button, a, [role="button"], .am-button, .submit-btn, div, span')
      .filter({ hasText: insureText })
      .last();
    if (!(await target.isVisible({ timeout: 800 }).catch(() => false))) continue;
    attempts.push({ strategy: 'h5-product-footer-locator', selector });
    let tap;
    try {
      tap = await tapLocatorCenter(page, target);
    } catch (error) {
      await page.waitForLoadState('domcontentloaded', { timeout: 5000 }).catch(() => undefined);
      await sleep(500);
      if (await buyNowAdvanced(page, beforeUrl)) {
        return {
          clicked: true,
          advanced: true,
          strategy: 'mouse-h5-product-footer-insure',
          selector,
          tap: null,
          tap_error: String(error?.message || error),
          settle_events: [],
          attempts,
        };
      }
      throw error;
    }
    await page.waitForLoadState('domcontentloaded', { timeout: 15000 }).catch(() => undefined);
    await sleep(stepDelayMs);
    const settle_events = await settleProductEntryFlow(page);
    const advanced = await buyNowAdvanced(page, beforeUrl);
    if (advanced) {
      return {
        clicked: true,
        advanced,
        strategy: 'mouse-h5-product-footer-insure',
        selector,
        tap,
        settle_events,
        attempts,
      };
    }
  }

  const broadButton = page
    .locator('button, a, [role="button"], .am-button, .submit-btn, input[type="button"], input[type="submit"]')
    .filter({ hasText: insureText })
    .last();
  if (await broadButton.isVisible({ timeout: 800 }).catch(() => false)) {
    attempts.push({ strategy: 'h5-product-footer-broad-text' });
    let tap;
    try {
      tap = await tapLocatorCenter(page, broadButton);
    } catch (error) {
      await page.waitForLoadState('domcontentloaded', { timeout: 5000 }).catch(() => undefined);
      await sleep(500);
      if (await buyNowAdvanced(page, beforeUrl)) {
        return {
          clicked: true,
          advanced: true,
          strategy: 'mouse-h5-product-footer-insure',
          selector: 'broad-insure-text',
          tap: null,
          tap_error: String(error?.message || error),
          settle_events: [],
          attempts,
        };
      }
      throw error;
    }
    await page.waitForLoadState('domcontentloaded', { timeout: 15000 }).catch(() => undefined);
    await sleep(stepDelayMs);
    const settle_events = await settleProductEntryFlow(page);
    const advanced = await buyNowAdvanced(page, beforeUrl);
    if (advanced) {
      return {
        clicked: true,
        advanced,
        strategy: 'mouse-h5-product-footer-insure',
        selector: 'broad-insure-text',
        tap,
        settle_events,
        attempts,
      };
    }
  }

  const viewport = page.viewportSize() || { width: 390, height: 844 };
  const fallbackPoints = [
    { x: viewport.width * 0.84, y: viewport.height - 44 },
    { x: viewport.width * 0.82, y: viewport.height * 0.5 },
  ];
  for (const point of fallbackPoints) {
    attempts.push({ strategy: 'h5-product-footer-coordinate', point });
    await page.touchscreen.tap(point.x, point.y).catch(async () => {
      await page.mouse.click(point.x, point.y).catch(() => undefined);
    });
    await page.waitForLoadState('domcontentloaded', { timeout: 15000 }).catch(() => undefined);
    await sleep(stepDelayMs);
    const settle_events = await settleProductEntryFlow(page);
    const advanced = await buyNowAdvanced(page, beforeUrl);
    if (advanced) {
      return {
        clicked: true,
        advanced,
        strategy: 'mouse-h5-product-footer-insure',
        selector: 'coordinate-fallback',
        tap: point,
        settle_events,
        attempts,
      };
    }
  }

  return {
    clicked: false,
    advanced: false,
    strategy: 'mouse-h5-product-footer-insure',
    reason: 'h5 product footer insure action did not advance',
    attempts,
  };
}

function isH5SubmitAction(action) {
  const strategy = String(action?.click_strategy || '').toLowerCase();
  const text = compactActionText(action?.text);
  if (strategy.includes('touchscreen-submit-btn')) return true;
  return String(action?.planned_from_node_id || '') === 'NODE-insure-form'
    && (text.includes('提交订单') || text.includes('提交投保单') || text.includes('提交投保'))
    && (strategy.includes('js-h5-action-button') || strategy.includes('h5-action-button') || strategy.includes('submit'));
}

function isAgent3SubmitApiAction(action) {
  const strategy = actionClickStrategy(action);
  const actionType = String(action?.action_type || '').toLowerCase();
  const selector = String(action?.selector || '');
  return actionType === 'submit_api'
    || strategy.includes('direct-submit-after-bank-validation-loop')
    || Boolean(action?.submit_api_result?.attempted)
    || /\/api\/apps\/cps\/insure\/submit/i.test(selector);
}

async function bestH5SubmitButtonLocator(page) {
  const token = `agent-h5-submit-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const marked = await page.evaluate((token) => {
    function isVisible(element) {
      const style = window.getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.visibility !== 'hidden'
        && style.display !== 'none'
        && rect.width > 0
        && rect.height > 0;
    }
    function textOf(element) {
      return String(element.innerText || element.textContent || element.getAttribute?.('value') || '').replace(/\s+/g, ' ').trim();
    }
    function compact(value) {
      return String(value || '').replace(/\s+/g, '').trim();
    }
    function isFixedOrSticky(element) {
      let current = element;
      while (current && current !== document.body) {
        const position = window.getComputedStyle(current).position;
        if (position === 'fixed' || position === 'sticky') return true;
        current = current.parentElement;
      }
      return false;
    }

    const viewportHeight = window.innerHeight || document.documentElement.clientHeight || 844;
    const viewportWidth = window.innerWidth || document.documentElement.clientWidth || 390;
    const candidates = Array.from(document.querySelectorAll('button, a, [role="button"], .am-button, .adm-button, .submit-btn, [class*="submit"], [class*="footer"], div, span'))
      .filter(isVisible)
      .map(element => {
        const rect = element.getBoundingClientRect();
        const text = textOf(element);
        const normalized = compact(text);
        const fixedScore = isFixedOrSticky(element) ? 10000 : 0;
        const bottomScore = Math.max(0, 3000 - Math.abs(viewportHeight - (rect.top + rect.height / 2)) * 8);
        const rightScore = Math.max(0, 1200 - Math.abs(viewportWidth * 0.82 - (rect.left + rect.width / 2)) * 3);
        const exactScore = /^提交订单$|^提交投保单$|^提交投保$/.test(normalized) ? 5000 : /提交订单|提交投保单|提交投保|提交/.test(normalized) ? 2500 : -10000;
        return { element, text, score: fixedScore + bottomScore + rightScore + exactScore, rect: { x: rect.x, y: rect.y, width: rect.width, height: rect.height } };
      })
      .filter(item => item.score > 0)
      .sort((left, right) => right.score - left.score);
    const target = candidates[0];
    if (!target) return null;
    document.querySelectorAll('[data-agent-h5-submit-button]').forEach(node => node.removeAttribute('data-agent-h5-submit-button'));
    target.element.setAttribute('data-agent-h5-submit-button', token);
    return { text: target.text, score: target.score, rect: target.rect };
  }, token).catch(() => null);
  if (!marked) return null;
  const locator = page.locator(`[data-agent-h5-submit-button="${token}"]`).first();
  if (!(await locator.isVisible({ timeout: 800 }).catch(() => false))) return null;
  return { locator, ...marked };
}

async function clickH5SubmitButton(page, action) {
  const attempts = [];
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight)).catch(() => undefined);
  await sleep(500);
  const submitText = /提交订单|提交投保单|提交投保|提交/;
  const scoredSubmit = await bestH5SubmitButtonLocator(page);
  if (scoredSubmit) {
    attempts.push({ strategy: 'h5-submit-bottom-fixed-score', score: scoredSubmit.score, text: scoredSubmit.text, rect: scoredSubmit.rect });
    const tap = await clickH5SubmitLocator(page, scoredSubmit.locator);
    return {
      clicked: true,
      strategy: 'touchscreen-submit-btn',
      tap,
      attempts,
      text: scoredSubmit.text,
    };
  }
  const candidates = [
    page.locator('.insure-footer .submit-btn').last(),
    page.locator('.submit-btn').last(),
    page.locator('.am-button-primary').filter({ hasText: submitText }).last(),
    page.getByRole('button', { name: submitText }).last(),
    page.locator('button, a, [role="button"], .am-button, .submit-btn, div, span').filter({ hasText: submitText }).last(),
  ];
  for (const locator of candidates) {
    if (!(await locator.isVisible({ timeout: 1000 }).catch(() => false))) continue;
    attempts.push({ strategy: 'h5-submit-locator' });
    const tap = await clickH5SubmitLocator(page, locator);
    return {
      clicked: true,
      strategy: 'touchscreen-submit-btn',
      tap,
      attempts,
      text: await locator.innerText({ timeout: 1000 }).catch(() => ''),
    };
  }
  const selector = String(action?.selector || '').trim();
  if (selector) {
    const locator = page.locator(selector).first();
    if (await locator.isVisible({ timeout: 1000 }).catch(() => false)) {
      attempts.push({ strategy: 'h5-submit-agent3-selector', selector });
      const tap = await clickH5SubmitLocator(page, locator);
      return {
        clicked: true,
        strategy: 'touchscreen-submit-btn',
        tap,
        attempts,
        text: await locator.innerText({ timeout: 1000 }).catch(() => ''),
      };
    }
  }
  return {
    clicked: false,
    strategy: 'touchscreen-submit-btn',
    reason: 'submit button not found',
    attempts,
  };
}

async function bestSuitabilityQuestionnaireSubmitLocator(page) {
  const token = `agent-questionnaire-submit-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const marked = await page.evaluate((token) => {
    function isVisible(element) {
      const style = window.getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.visibility !== 'hidden'
        && style.display !== 'none'
        && rect.width > 0
        && rect.height > 0;
    }
    function textOf(element) {
      return String(element.innerText || element.textContent || element.getAttribute?.('value') || '').replace(/\s+/g, ' ').trim();
    }
    function compact(value) {
      return String(value || '').replace(/\s+/g, '').trim();
    }
    function isFixedOrSticky(element) {
      let current = element;
      while (current && current !== document.body) {
        const position = window.getComputedStyle(current).position;
        if (position === 'fixed' || position === 'sticky') return true;
        current = current.parentElement;
      }
      return false;
    }

    const viewportHeight = window.innerHeight || document.documentElement.clientHeight || 844;
    const viewportWidth = window.innerWidth || document.documentElement.clientWidth || 390;
    const submitPattern = /^(鎻愪氦|涓嬩竴姝纭畾|纭|缁х画|瀹屾垚)$/;
    const candidates = Array.from(document.querySelectorAll('button.js-adapt-question-btn, a.js-adapt-question-btn, .js-adapt-question-btn, button, a, [role="button"], .am-button, .adm-button, .btn, .button, input[type="button"], input[type="submit"], div, span'))
      .filter(isVisible)
      .map(element => {
        const rect = element.getBoundingClientRect();
        const text = textOf(element);
        const normalized = compact(text);
        const marker = `${element.tagName || ''} ${element.className || ''} ${element.getAttribute?.('role') || ''}`;
        const clickable = element.matches('button, a, [role="button"], input[type="button"], input[type="submit"], [onclick]')
          || /js-adapt-question-btn|am-button|adm-button|button|btn/i.test(marker);
        const exactScore = submitPattern.test(normalized) ? 6000 : /鎻愪氦|涓嬩竴姝纭畾|纭|缁х画|瀹屾垚/.test(normalized) ? 2500 : -10000;
        const fixedScore = isFixedOrSticky(element) ? 10000 : 0;
        const bottomScore = Math.max(0, 3000 - Math.abs(viewportHeight - (rect.top + rect.height / 2)) * 8);
        const centerScore = Math.max(0, 1200 - Math.abs(viewportWidth / 2 - (rect.left + rect.width / 2)) * 3);
        const clickableScore = clickable ? 2200 : 0;
        const classScore = /js-adapt-question-btn|am-button-primary|adm-button-primary|submit|next|footer|bottom/i.test(marker) ? 1800 : 0;
        return {
          element,
          text,
          score: exactScore + fixedScore + bottomScore + centerScore + clickableScore + classScore,
          rect: { x: rect.x, y: rect.y, width: rect.width, height: rect.height },
        };
      })
      .filter(item => item.score > 0)
      .sort((left, right) => right.score - left.score);
    const target = candidates[0];
    if (!target) return null;
    document.querySelectorAll('[data-agent-questionnaire-submit-button]').forEach(node => node.removeAttribute('data-agent-questionnaire-submit-button'));
    target.element.setAttribute('data-agent-questionnaire-submit-button', token);
    return { text: target.text, score: target.score, rect: target.rect };
  }, token).catch(() => null);
  if (!marked) return null;
  const locator = page.locator(`[data-agent-questionnaire-submit-button="${token}"]`).first();
  if (!(await locator.isVisible({ timeout: 800 }).catch(() => false))) return null;
  return { locator, ...marked };
}

async function inspectPostSubmitDiagnostics(page) {
  return await page.evaluate(() => {
    const norm = value => String(value || '').replace(/\s+/g, ' ').trim();
    const visible = element => !!element && !!(element.offsetWidth || element.offsetHeight || element.getClientRects().length);
    const texts = selector => Array.from(document.querySelectorAll(selector))
      .filter(visible)
      .map(element => norm(element.innerText || element.textContent || element.getAttribute?.('aria-label')))
      .filter(Boolean)
      .slice(0, 20);
    const errors = Array.from(document.querySelectorAll('[class*="error"], [class*="Error"], .am-input-error-extra, .am-list-extra'))
      .filter(visible)
      .map(element => ({
        text: norm(element.innerText || element.textContent || element.getAttribute?.('aria-label')),
        className: String(element.className || ''),
      }))
      .filter(item => item.text || /error|Error/.test(item.className))
      .slice(0, 30);
    const inputs = Array.from(document.querySelectorAll('input,textarea'))
      .filter(visible)
      .map(element => ({
        placeholder: element.getAttribute('placeholder') || '',
        value: element.value || element.getAttribute('value') || '',
        className: String(element.className || ''),
      }))
      .slice(0, 20);
    const bankRows = Array.from(document.querySelectorAll('.am-list-item,.am-list-line,.insure-filed-wrapper,li,dd,label,section,article'))
      .filter(visible)
      .map(element => norm(element.innerText || element.textContent))
      .filter(text => /开户银行|开户行|银行账号|银行卡号|持卡人/.test(text))
      .slice(0, 12);
    const bankErrorIndicators = [];
    const bankAccountRows = Array.from(document.querySelectorAll('.am-list-item,.am-list-line,.insure-filed-wrapper,li,dd,label,section,article,div'))
      .filter(visible)
      .filter(element => /银行账号|银行卡号|银行账户|开卡信息|储蓄卡/.test(norm(element.innerText || element.textContent || '')));
    for (const row of bankAccountRows.slice(0, 8)) {
      for (const node of Array.from(row.querySelectorAll('*')).filter(visible).slice(0, 80)) {
        const className = String(node.className || '');
        const aria = String(node.getAttribute?.('aria-label') || node.getAttribute?.('title') || node.getAttribute?.('role') || '');
        const text = norm(node.innerText || node.textContent || aria);
        const color = String(getComputedStyle(node).color || '');
        const marker = `${text} ${className} ${aria}`;
        const isErrorClass = /error|warn|exclamation|alert|fail|invalid|am-input-error|am-list-item-error/i.test(marker);
        const isWarmColor = /rgb\(\s*255\s*,\s*(?:0|6\d|7\d|8\d|9\d|1[0-7]\d)\s*,\s*(?:0|6\d|7\d|8\d|9\d|1[0-7]\d)\s*\)/i.test(color);
        if (!text && /am-input-error-extra/.test(className) && !isWarmColor) continue;
        if (!isErrorClass && !isWarmColor) continue;
        bankErrorIndicators.push({ text, className, aria, color });
      }
    }
    return {
      url: location.href,
      toasts: texts('.am-toast, .am-toast-notice, .adm-toast, [role="alert"]'),
      dialogs: texts('.am-modal, .am-modal-wrap, .adm-modal, .adm-popup, [role="dialog"]'),
      errors,
      inputs,
      bankRows,
      bankErrorIndicators: bankErrorIndicators.slice(0, 12),
      bodyTail: norm(document.body?.innerText || '').slice(-600),
    };
  }).catch(error => ({ error: String(error?.message || error), url: page.url() }));
}

function hasBankAccountSubmitBlocker(diagnostics) {
  if (hasBankRecognitionBlocker(diagnostics)) return true;
  const errors = Array.isArray(diagnostics?.errors) ? diagnostics.errors : [];
  return errors.some(item => {
    const text = String(item?.text || '');
    const className = String(item?.className || '');
    return /银行账号|银行卡号|银行账户/.test(text) && /am-input-error|input-error|error/i.test(className);
  }) || (
    errors.some(item => /am-input-error-extra|input-error/i.test(String(item?.className || '')) && String(item?.text || '').trim()) &&
    /银行账号|银行卡号|银行账户/.test(String(diagnostics?.bodyTail || ''))
  );
}

function hasBankRecognitionBlocker(diagnostics) {
  const values = [
    diagnostics?.bodyTail,
    ...(Array.isArray(diagnostics?.toasts) ? diagnostics.toasts : []),
    ...(Array.isArray(diagnostics?.dialogs) ? diagnostics.dialogs : []),
    ...(Array.isArray(diagnostics?.bankRows) ? diagnostics.bankRows : []),
    ...(Array.isArray(diagnostics?.errors) ? diagnostics.errors.map(item => `${item?.text || ''} ${item?.className || ''}`) : []),
  ].join(' ');
  return /开户行识别失败|手动选择开户行/.test(values);
}

function hasBankAccountErrorIndicator(diagnostics) {
  const indicators = Array.isArray(diagnostics?.bankErrorIndicators) ? diagnostics.bankErrorIndicators : [];
  if (indicators.length) return true;
  const errors = Array.isArray(diagnostics?.errors) ? diagnostics.errors : [];
  return errors.some(item => {
    const text = String(item?.text || '').trim();
    const className = String(item?.className || '');
    if (!text) return false;
    return /银行账号|银行卡号|银行账户|开卡信息|储蓄卡|账号/.test(text) && /am-input-error|am-list-item-error|input-error|error|warn|alert|exclamation/i.test(className);
  });
}

function isBankReadyToSubmitAfterManualSelection(diagnostics) {
  if (!diagnostics || diagnostics.error) return false;
  return !hasBankRecognitionBlocker(diagnostics) && !hasBankAccountErrorIndicator(diagnostics);
}

async function submitInsureViaBrowserApiIfNeeded(page, action, beforeUrl, diagnostics, options = {}) {
  const allowSuitabilityBlocker = Boolean(options.allow_suitability_blocker);
  const currentUrl = page.url();
  if (currentUrl !== beforeUrl) {
    return { attempted: false, reason: 'url_changed_after_ui_submit', current_url: currentUrl, before_url: beforeUrl };
  }
  const submitResponses = (globalThis.__agent4NetworkResponses || []).filter(item => /\/api\/apps\/cps\/insure\/submit/i.test(String(item?.url || '')));
  const observedSuitabilityBlocker = recentSuitabilitySubmitBlocker();
  if (submitResponses.length && !(allowSuitabilityBlocker && observedSuitabilityBlocker)) {
    return { attempted: false, reason: 'submit_response_already_observed', response_count: submitResponses.length };
  }
  if (hasBankAccountSubmitBlocker(diagnostics) || hasBankRecognitionBlocker(diagnostics)) {
    return { attempted: false, reason: 'bank_blocker_visible' };
  }
  if (observedSuitabilityBlocker && !allowSuitabilityBlocker) {
    return { attempted: false, reason: 'suitability_blocker_visible' };
  }
  if (!/\/product\/insure(?:\?|$)/i.test(currentUrl)) {
    return { attempted: false, reason: 'not_on_insure_page', current_url: currentUrl };
  }
  const result = await page.evaluate(async (rawAction) => {
    const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
    const toPlain = value => {
      if (value == null) return value;
      if (typeof value?.toJS === 'function') {
        try { return value.toJS(); } catch (_) {}
      }
      if (typeof value?.toJSON === 'function') {
        try { return value.toJSON(); } catch (_) {}
      }
      return value;
    };
    const isObject = value => value && typeof value === 'object' && !Array.isArray(value);
    const looksLikeModuleRoot = value => {
      const plain = toPlain(value);
      return isObject(plain) && Object.keys(plain).some(key => /^\d+$/.test(key));
    };
    const looksLikeInsureContainer = value => {
      const plain = toPlain(value);
      return isObject(plain) && isObject(plain.data) && looksLikeModuleRoot(plain.data);
    };
    const hasSubmitTopLevel = value => {
      const plain = toPlain(value);
      return isObject(plain) && ['productId', 'productPlanId', 'encryptInsureNum', 'trialGenes', 'notifyAnswerId', 'insureNum']
        .some(key => plain[key] !== undefined && plain[key] !== null && plain[key] !== '');
    };
    const fieldValue = value => {
      const plain = toPlain(value);
      if (plain == null) return plain;
      if (Array.isArray(plain)) return plain.map(fieldValue);
      if (!isObject(plain)) return plain;
      for (const key of ['value', 'controlValue', 'code', 'id', 'text', 'label', 'name', 'valueText']) {
        if (plain[key] !== undefined && plain[key] !== null && plain[key] !== '') return plain[key];
      }
      return plain;
    };
    function normalizeSubmitTrialGenes(rawTrialGenes, payload) {
      let trialGenes = rawTrialGenes;
      if (typeof trialGenes === 'string') {
        try { trialGenes = JSON.parse(trialGenes); } catch (_) { return rawTrialGenes; }
      }
      if (!isObject(trialGenes)) return rawTrialGenes;
      if (!Array.isArray(trialGenes.genes)) trialGenes.genes = [];
      const applicantRow = payload.data?.['10']?.[0] || {};
      const insuredRow = payload.data?.['20']?.[0] || applicantRow;
      const setTrialGene = (key, value) => {
        if (value === undefined || value === null || value === '') return;
        let item = trialGenes.genes.find(gene => gene?.key === key);
        if (!item) {
          item = { sort: trialGenes.genes.length + 1, protectItemId: '', key, value: String(value) };
          trialGenes.genes.push(item);
        } else {
          item.value = String(value);
        }
      };
      setTrialGene('insurantDate', insuredRow.birthdate || applicantRow.birthdate);
      const sexValue = String(insuredRow.sex || applicantRow.sex || '');
      if (sexValue) setTrialGene('sex', sexValue === '2' ? '女' : '男');
      if (payload.productId != null) trialGenes.productId = payload.productId;
      if (payload.productPlanId != null) trialGenes.productPlanId = payload.productPlanId;
      return JSON.stringify(trialGenes);
    }
    function summarizeTrialGenes(rawTrialGenes) {
      let trialGenes = rawTrialGenes;
      if (typeof trialGenes === 'string') {
        try { trialGenes = JSON.parse(trialGenes); } catch (_) { return String(rawTrialGenes || '').slice(0, 200); }
      }
      if (!isObject(trialGenes) || !Array.isArray(trialGenes.genes)) return trialGenes;
      return trialGenes.genes
        .filter(gene => gene?.key)
        .map(gene => `${gene.key}=${gene.value}`)
        .join(';');
    }
    function normalizeBankNameForSubmit(value) {
      const text = String(value || '').trim();
      if (!text) return '工商银行';
      if (/^(1|102)$/.test(text)) return '工商银行';
      return text.includes('工商') ? '工商银行' : text;
    }
    function submitPriceFromTrialGenes(rawTrialGenes) {
      let trialGenes = rawTrialGenes;
      if (typeof trialGenes === 'string') {
        try { trialGenes = JSON.parse(trialGenes); } catch (_) { return null; }
      }
      if (!isObject(trialGenes) || !Array.isArray(trialGenes.genes)) return null;
      const premiumGene = trialGenes.genes.find(gene => gene?.key === 'premium');
      const amount = Number(String(premiumGene?.value || '').replace(/[^0-9.]/g, ''));
      return Number.isFinite(amount) && amount > 0 ? amount : null;
    }
    function alignNativeSubmitPayload(payload) {
      payload.autoRenewal = payload.autoRenewal === true
        || payload.autoRenewal === 1
        || payload.autoRenewal === '1'
        || String(payload.autoRenewal || '').toLowerCase() === 'true';
      if (payload.autoRenewal) payload.renewalCheck = payload.renewalCheck ?? 1;
      else payload.renewalCheck = 0;
      const applicantRow = payload.data?.['10']?.[0];
      const insuredRow = payload.data?.['20']?.[0];
      if (isObject(insuredRow)) {
        insuredRow.addressIsSameApplicant = insuredRow.addressIsSameApplicant ?? '';
      }
      const bankRow = payload.data?.['107']?.[0];
      if (isObject(bankRow)) {
        const bankName = normalizeBankNameForSubmit(
          bankRow.bankName || bankRow.bankText || bankRow.openBank || bankRow.bank || window.__agent4BankName
        );
        bankRow.bankName = bankName;
        bankRow.bank = bankRow.bank || '1';
        bankRow.cardOwner = bankRow.cardOwner || applicantRow?.cName || insuredRow?.cName || '';
      }
      payload.price = submitPriceFromTrialGenes(payload.trialGenes) || payload.price;
    }
    function extractAllowedStartDate(text) {
      const match = String(text || '').match(/(\d{4}-\d{2}-\d{2})\s+00:00:00/);
      return match ? match[1] : '';
    }
    function setPayloadStartDate(payload, value) {
      if (!/^\d{4}-\d{2}-\d{2}$/.test(String(value || ''))) return false;
      payload.startDate = value;
      if (payload.data?.['102']?.[0]) payload.data['102'][0].insuranceDate = value;
      return true;
    }
    async function submitPayload(payload) {
      const response = await fetch(`/api/apps/cps/insure/submit?md=${Math.random()}`, {
        method: 'POST',
        credentials: 'include',
        headers: {
          'content-type': 'application/json;charset=UTF-8',
          'accept': 'application/json, text/plain, */*',
        },
        body: JSON.stringify(payload),
      });
      const text = await response.text();
      let body = null;
      try { body = JSON.parse(text); } catch (_) {}
      const data = body?.data || {};
      const code = String(body?.code ?? body?.errorCode ?? data?.errorCode ?? '');
      return { response, text, body, data, code, retry_reason: '' };
    }
    const normalizeRecord = record => {
      const plain = toPlain(record);
      if (!isObject(plain)) return plain;
      const out = {};
      for (const [key, value] of Object.entries(plain)) {
        if (/^(hasError|hasAjaxError|error|errorMsg|msg|ajaxError|validateStatus|validStatus|required|isRequired|needValid|validate|display|hidden)$/.test(key)) continue;
        const normalized = fieldValue(value);
        if (isObject(normalized)) continue;
        out[key] = normalized;
      }
      return out;
    };
    const normalizeRows = rows => {
      const plain = toPlain(rows);
      if (Array.isArray(plain)) return plain.map(normalizeRecord).filter(row => isObject(row) && Object.keys(row).length);
      if (isObject(plain)) {
        const row = normalizeRecord(plain);
        return isObject(row) && Object.keys(row).length ? [row] : [];
      }
      return [];
    };
    const normalizeData = dataRoot => {
      let root = toPlain(dataRoot);
      const out = {};
      if (!isObject(root)) return out;
      if (!looksLikeModuleRoot(root) && looksLikeModuleRoot(root.data)) root = root.data;
      for (const [moduleId, rows] of Object.entries(root)) {
        if (!/^\d+$/.test(moduleId)) continue;
        const normalizedRows = normalizeRows(rows);
        if (normalizedRows.length) out[String(moduleId)] = normalizedRows;
      }
      return out;
    };
    const plainTopLevel = container => {
      const plain = toPlain(container);
      const out = {};
      if (!isObject(plain)) return out;
      for (const [key, value] of Object.entries(plain)) {
        if (key === 'data') continue;
        if (/^\d+$/.test(key)) continue;
        if (/^(loading|error|errors|validate|validator|ajaxError|hasError|hasAjaxError)$/.test(key)) continue;
        out[key] = fieldValue(value);
      }
      return out;
    };
    const storeCandidates = [
      window.__NEXT_REDUX_STORE__,
      window.store,
      window.reduxStore,
      ...Object.keys(window)
        .filter(key => /store|redux/i.test(key))
        .map(key => {
          try { return window[key]; } catch (_) { return null; }
        }),
    ].filter((store, index, array) => store && array.indexOf(store) === index);
    const stateCandidates = [];
    for (const store of storeCandidates) {
      try {
        if (typeof store.getState === 'function') stateCandidates.push(toPlain(store.getState()));
      } catch (_) {}
    }
    stateCandidates.push(toPlain(window.__NEXT_DATA__));
    for (const storage of [window.localStorage, window.sessionStorage]) {
      if (!storage) continue;
      for (let index = 0; index < storage.length; index += 1) {
        const key = storage.key(index);
        if (!key || !/insure|product/i.test(key)) continue;
        try {
          const raw = storage.getItem(key);
          if (raw && raw[0] === '{') stateCandidates.push(JSON.parse(raw));
        } catch (_) {}
      }
    }
    const containerCandidates = [];
    for (const state of stateCandidates) {
      if (!state || typeof state !== 'object') continue;
      containerCandidates.push(
        state?.product?.insure?.data,
        state?.insure?.data,
        state?.data,
        state
      );
    }
    const containers = containerCandidates.filter(container => container && typeof container === 'object');
    const primary = containers.find(container => looksLikeInsureContainer(container) && hasSubmitTopLevel(container))
      || containers.find(container => looksLikeInsureContainer(container))
      || containers[0];
    if (!primary) {
      return { attempted: true, order_generated: false, reason: 'insure_state_not_found' };
    }
    const payload = { ...plainTopLevel(primary), data: normalizeData(primary) };
    for (const container of containers) {
      if (looksLikeInsureContainer(container) && hasSubmitTopLevel(container)) {
        const top = plainTopLevel(container);
        for (const [key, value] of Object.entries(top)) {
          if (payload[key] === undefined || payload[key] === null || payload[key] === '') payload[key] = value;
        }
      }
      const data = normalizeData(container);
      for (const [moduleId, rows] of Object.entries(data)) {
        if (!payload.data[moduleId] || !payload.data[moduleId].length) payload.data[moduleId] = rows;
      }
    }
    for (const key of Object.keys(payload)) {
      if (/^\d+$/.test(key)) delete payload[key];
    }
    const params = new URLSearchParams(window.location.search);
    const notifyAnswerId = params.get('notifyAnswerId');
    const encryptInsureNum = params.get('encryptInsureNum');
    if (encryptInsureNum) payload.encryptInsureNum = encryptInsureNum;
    if (notifyAnswerId && !Number.isNaN(Number(notifyAnswerId))) payload.notifyAnswerId = Number(notifyAnswerId);
    if (payload.startDate && payload.data['102']?.[0]) payload.data['102'][0].insuranceDate = payload.startDate;
    if (!payload.startDate && payload.data['102']?.[0]?.insuranceDate) payload.startDate = payload.data['102'][0].insuranceDate;
    if (!payload.data['30']?.length) payload.data['30'] = [{ relationInsureBeneficiary: 1, insurantIndex: 0 }];
    else {
      payload.data['30'][0].relationInsureBeneficiary = payload.data['30'][0].relationInsureBeneficiary ?? 1;
      payload.data['30'][0].insurantIndex = payload.data['30'][0].insurantIndex ?? 0;
    }
    if (!payload.data['101']?.length) payload.data['101'] = [{ urgencyContact: '', urgencyContactPhone: '' }];
    else {
      payload.data['101'][0].urgencyContact = payload.data['101'][0].urgencyContact || '';
      payload.data['101'][0].urgencyContactPhone = payload.data['101'][0].urgencyContactPhone || '';
    }
    payload.isHealthSuccess = true;
    payload.healthWarningContinueInsure = 0;
    payload.continueInsure = 0;
    payload.traceInsuranceDate = false;
    payload.insureInsurantType = payload.insureInsurantType || 20;
    payload.insureBeneficiaryType = payload.insureBeneficiaryType || 1;
    payload.source = payload.source || 2;
    payload.merchantId = payload.merchantId || 1000014;
    payload.aid = payload.aid || '';
    payload.manualUnderwritingType = payload.manualUnderwritingType ?? 0;
    payload.editable = payload.editable ?? 1;
    payload.tenantId = payload.tenantId ?? 0;
    payload.userId = payload.userId ?? -1;
    payload.isJunLongOldPerson = payload.isJunLongOldPerson ?? false;
    payload.operatInsureFlow = payload.operatInsureFlow ?? 0;
    payload.autoDeductionAgeRequireCheck = payload.autoDeductionAgeRequireCheck ?? 0;
    payload.isEmptyData = payload.isEmptyData ?? true;
    payload.traceInsuredDateNew = payload.traceInsuredDateNew ?? 2;
    payload.confirmInsureRiskTask = payload.confirmInsureRiskTask ?? 0;
    payload.companyDiscountPremiumCheck = payload.companyDiscountPremiumCheck ?? false;
    payload.renewalCheck = payload.renewalCheck ?? 0;
    payload.isAudit = payload.isAudit ?? 0;
    payload.standardAuditSwitch = payload.standardAuditSwitch ?? false;
    payload.extraDiscountConfig = payload.extraDiscountConfig ?? false;
    payload.platform = payload.platform ?? 1;
    payload.traceInsuredDate = payload.traceInsuredDate ?? false;
    payload.isUpdate = payload.isUpdate ?? 0;
    payload.verifyType = payload.verifyType ?? 0;
    payload.repurchase = payload.repurchase ?? 0;
    payload.fromStandardAudit = payload.fromStandardAudit ?? false;
    payload.isFixedStartDateForRenewal = payload.isFixedStartDateForRenewal ?? 0;
    payload.insureSource = payload.insureSource ?? 0;
    payload.confirmSmartAuditGeneAdjust = payload.confirmSmartAuditGeneAdjust ?? false;
    payload.saveType = payload.saveType ?? 0;
    payload.unableChangeTemp = payload.unableChangeTemp ?? 0;
    payload.secondAuditInsureTaskStatus = payload.secondAuditInsureTaskStatus ?? 0;
    payload.againSubmit = payload.againSubmit ?? false;
    payload.personnelType = payload.personnelType ?? 20;
    payload.ignoreCertTask = payload.ignoreCertTask ?? false;
    payload.applicantType = payload.applicantType ?? 0;
    payload.busErrorButtonConsole = payload.busErrorButtonConsole ?? true;
    payload.isTemp = payload.isTemp ?? 0;
    payload.manualUnderwritingCheck = payload.manualUnderwritingCheck ?? 0;
    payload.isPay = payload.isPay ?? false;
    payload.isAp = payload.isAp ?? false;
    payload.autoRenewal = payload.autoRenewal ?? false;
    payload.isAdditionalEdu = payload.isAdditionalEdu ?? false;
    payload.cid = payload.cid ?? -1;
    payload.buyCountType = payload.buyCountType ?? 0;
    payload.totalNum = payload.totalNum ?? 1;
    alignNativeSubmitPayload(payload);
    if (payload.price == null || payload.price === '') {
      const priceText = String(document.body?.innerText || '');
      const match = priceText.match(/([0-9]+(?:\.[0-9]+)?)\s*元/);
      if (match) payload.price = Number(match[1]);
    }
    payload.signReturnUtl = payload.signReturnUtl || (() => {
      const productPrefix = window.location.pathname.match(/^(.*\/product)\/insure/)?.[1];
      if (!productPrefix || !payload.encryptInsureNum) return payload.signReturnUtl;
      return `${window.location.origin}${productPrefix}/task?encryptInsureNum=${encodeURIComponent(payload.encryptInsureNum)}`;
    })();
    const readMaybeAsync = async fn => {
      try {
        const value = fn();
        const resolved = value && typeof value.then === 'function'
          ? await Promise.race([value, sleep(5000).then(() => '')])
          : value;
        return typeof resolved === 'string' && resolved ? resolved : '';
      } catch (_) {
        return '';
      }
    };
    payload.verifyCode = payload.verifyCode || await readMaybeAsync(() => window.getNVCVal?.())
      || await readMaybeAsync(() => window.nvc?.getNVCVal?.())
      || await readMaybeAsync(() => window.NVC_Opt?.getNVCVal?.())
      || window.__agent4VerifyCode
      || '';
    if (payload.trialGenes) payload.trialGenes = normalizeSubmitTrialGenes(payload.trialGenes, payload);
    if (payload.confirmItem && typeof payload.confirmItem !== 'string') payload.confirmItem = JSON.stringify(payload.confirmItem);
    for (const [key, value] of Object.entries({ ...payload })) {
      if (key !== 'data' && isObject(value)) delete payload[key];
    }
    if (!payload.productId || !payload.productPlanId || !Object.keys(payload.data || {}).length || !payload.encryptInsureNum) {
      return {
        attempted: true,
        order_generated: false,
        reason: 'submit_payload_incomplete',
        payload_summary: {
          productId: payload.productId,
          productPlanId: payload.productPlanId,
          encryptInsureNum: payload.encryptInsureNum,
          modules: Object.keys(payload.data || {}),
        },
      };
    }
    let submitResult = await submitPayload(payload);
    let { response, text, body, data, code } = submitResult;
    const retryDate = extractAllowedStartDate(text);
    let retriedPolicyStartDate = false;
    if (code === '37202' && retryDate) {
      retriedPolicyStartDate = setPayloadStartDate(payload, retryDate);
    }
    if (retriedPolicyStartDate) {
      alignNativeSubmitPayload(payload);
      if (payload.trialGenes) payload.trialGenes = normalizeSubmitTrialGenes(payload.trialGenes, payload);
      submitResult = { ...(await submitPayload(payload)), retry_reason: 'policy-start-date-window' };
      ({ response, text, body, data, code } = submitResult);
    }
    const taskHandoff = code === '37009'
      && Boolean(data.insureNum)
      && Boolean(data.encryptInsureNum)
      && Array.isArray(data.insureTaskList);
    const directOrder = code === '0' && Boolean(data.insureNum || data.encryptInsureNum || payload.insureNum || payload.encryptInsureNum);
    const suitabilityTask = code === '40015' && Boolean(data.encryptInsureNum || payload.encryptInsureNum);
    const topObjectKeys = Object.entries(payload)
      .filter(([key, value]) => key !== 'data' && isObject(value))
      .map(([key]) => key);
    const moduleObjectFields = [];
    for (const [moduleId, rows] of Object.entries(payload.data || {})) {
      for (const [rowIndex, row] of (Array.isArray(rows) ? rows : [rows]).entries()) {
        if (!isObject(row)) continue;
        for (const [fieldName, value] of Object.entries(row)) {
          if (isObject(value)) moduleObjectFields.push(`${moduleId}.${rowIndex}.${fieldName}`);
        }
      }
    }
    return {
      attempted: true,
      order_generated: response.ok && (taskHandoff || directOrder || suitabilityTask),
      task_handoff: taskHandoff,
      direct_order: directOrder,
      suitability_task: suitabilityTask,
      status: response.status,
      ok: response.ok,
      url: response.url,
      code,
      msg: body?.msg || data?.errorMessage || '',
      payload_summary: {
        productId: payload.productId,
        productPlanId: payload.productPlanId,
        encryptInsureNum: payload.encryptInsureNum,
        notifyAnswerId: payload.notifyAnswerId,
        startDate: payload.startDate,
        insuranceDate: payload.data?.['102']?.[0]?.insuranceDate,
        applicantRegion: payload.data?.['10']?.[0]?.provCityText,
        insuredRegion: payload.data?.['20']?.[0]?.provCityText,
        applicantJob: payload.data?.['10']?.[0]?.jobText,
        insuredJob: payload.data?.['20']?.[0]?.jobText,
        applicantBirth: payload.data?.['10']?.[0]?.birthdate,
        insuredBirth: payload.data?.['20']?.[0]?.birthdate,
        trialGenes: summarizeTrialGenes(payload.trialGenes),
        modules: Object.keys(payload.data || {}),
        hasVerifyCode: Boolean(payload.verifyCode),
        payloadKeys: Object.keys(payload),
        retryReason: submitResult.retry_reason || '',
        topObjectKeys,
        moduleObjectFields: moduleObjectFields.slice(0, 30),
      },
      response_order: {
        insureNum: data.insureNum || payload.insureNum || '',
        encryptInsureNum: data.encryptInsureNum || payload.encryptInsureNum || '',
        taskCount: Array.isArray(data.insureTaskList) ? data.insureTaskList.length : 0,
      },
      body_excerpt: text.slice(0, 2000),
    };
  }, action).catch(error => ({ attempted: true, order_generated: false, error: String(error?.message || error) }));
  return result;
}

async function completePostSubmitIdentityHandoff(page, submitApiResult) {
  if (!submitApiResult?.task_handoff || submitApiResult?.direct_order) {
    return { attempted: false, completed: false, reason: 'not_identity_task_handoff' };
  }
  const result = await page.evaluate(async ({ submitApiResult }) => {
    const headers = { 'content-type': 'application/json;charset=UTF-8', accept: 'application/json, text/plain, */*' };
    const parseJson = text => {
      try { return text ? JSON.parse(text) : null; } catch (_) { return null; }
    };
    const postJson = async (path, payload) => {
      const response = await fetch(`${path}${path.includes('?') ? '&' : '?'}md=${Math.random()}`, {
        method: 'POST',
        credentials: 'include',
        headers,
        body: JSON.stringify(payload || {}),
      });
      const text = await response.text();
      return { status: response.status, text: text.slice(0, 2000), json: parseJson(text), url: response.url };
    };
    const findKey = (root, key, depth = 0) => {
      if (!root || depth > 9) return undefined;
      if (Array.isArray(root)) {
        for (const item of root) {
          const found = findKey(item, key, depth + 1);
          if (found !== undefined && found !== null && found !== '') return found;
        }
        return undefined;
      }
      if (typeof root !== 'object') return undefined;
      if (Object.prototype.hasOwnProperty.call(root, key)) return root[key];
      for (const value of Object.values(root)) {
        const found = findKey(value, key, depth + 1);
        if (found !== undefined && found !== null && found !== '') return found;
      }
      return undefined;
    };
    const body = parseJson(submitApiResult.body_excerpt) || {};
    const data = body?.data || {};
    const current = new URL(location.href);
    const nextDataRoot = window.__NEXT_DATA__ || {};
    const store = window.__NEXT_REDUX_STORE__ || window.store || window.reduxStore;
    let redux = {};
    try {
      if (store && typeof store.getState === 'function') {
        const raw = store.getState();
        redux = raw && typeof raw.toJS === 'function' ? raw.toJS() : raw;
      }
    } catch (_) {}
    const responseOrder = submitApiResult.response_order || {};
    const encryptInsureNum = String(
      data.encryptInsureNum
        || responseOrder.encryptInsureNum
        || current.searchParams.get('encryptInsureNum')
        || findKey(nextDataRoot, 'encryptInsureNum')
        || findKey(redux, 'encryptInsureNum')
        || ''
    );
    const insureNum = data.insureNum || responseOrder.insureNum || findKey(nextDataRoot, 'insureNum') || findKey(redux, 'insureNum') || '';
    const sasTaskId = data.sasTaskId || findKey(nextDataRoot, 'sasTaskId') || findKey(redux, 'sasTaskId') || '';
    const merchantId = Number(findKey(nextDataRoot, 'merchantId') || findKey(redux, 'merchantId') || 1000014);
    const parts = location.pathname.split('/').filter(Boolean);
    const basePrefix = parts.length >= 4 ? `/${parts.slice(0, 4).join('/')}` : '/m/apps/cps/demo-channel';
    const taskUrl = `${location.origin}${basePrefix}/product/task?encryptInsureNum=${encodeURIComponent(encryptInsureNum)}`;
    const insureUrl = `${location.origin}${basePrefix}/product/insure?encryptInsureNum=${encodeURIComponent(encryptInsureNum)}`;
    const fallbackPayUrl = `${location.origin}/m/demo-channel/pay/?id=${encodeURIComponent(encryptInsureNum)}`;
    if (!encryptInsureNum) {
      return { attempted: true, completed: false, reason: 'missing_encrypt_insure_num' };
    }
    const list = await postJson('/api/apps/cps/insure/task/approve/list', { encryptInsureNum, merchantId });
    const listData = list.json?.data || {};
    const masters = listData.approveMasterVoList || findKey(data, 'approveMasterVoList') || [];
    const master = Array.isArray(masters)
      ? (masters.find(item => item && item.approveStatus !== 2) || masters[0] || {})
      : {};
    const processPayload = {
      encryptInsureNum,
      insureNum,
      merchantId,
      sasTaskId,
      masterId: master.masterId || findKey(data, 'masterId') || '',
      encryptMasterId: master.encryptMasterId || findKey(data, 'encryptMasterId') || '',
      authType: 2,
      suitFor: 1,
    };
    const process = await postJson('/api/apps/cps/insure/task/approve/process', processPayload);
    const processData = process.json?.data || {};
    const approveOk = String(process.json?.code ?? '') === '0'
      && (Number(processData.passedMasterNum || 0) >= 1 || process.json?.success === true);
    const nextPayload = { encryptInsureNum, merchantId, taskUrl, insureUrl };
    const next = await postJson('/api/apps/cps/insure/task/next/do', nextPayload);
    const nextData = next.json?.data || {};
    const nextOk = next.status >= 200 && next.status < 400;
    const nextUrl = String(nextData.url || nextData.targetUrl || nextData.redirectUrl || nextData.payUrl || '');
    const payUrl = nextUrl || (approveOk && nextOk ? fallbackPayUrl : '');
    return {
      attempted: true,
      completed: Boolean(approveOk && nextOk && payUrl),
      approve_ok: approveOk,
      next_ok: nextOk,
      can_pay: nextData.canPay === true || data.canPay === true,
      encryptInsureNum,
      insureNum,
      pay_url: payUrl,
      task_url: taskUrl,
      insure_url: insureUrl,
      list,
      process,
      next,
    };
  }, { submitApiResult }).catch(error => ({
    attempted: true,
    completed: false,
    error: String(error?.message || error),
  }));
  if (result?.pay_url) {
    await page.goto(result.pay_url, { waitUntil: 'domcontentloaded', timeout: 45000 }).catch(error => {
      result.navigation_error = String(error?.message || error);
    });
    await page.waitForLoadState('domcontentloaded', { timeout: 15000 }).catch(() => undefined);
    await sleep(1500);
    result.final_url = page.url();
    result.completed = Boolean(result.completed && isPaymentPageUrl(result.final_url));
  }
  return result;
}

function isBankRelatedSyntheticDataAction(action) {
  const text = `${action?.text || ''} ${action?.selector || ''} ${action?.click_strategy || ''} ${action?.action_type || ''}`;
  return /开户银行|开户行|银行账号|银行卡|银行账户|payAccount|bank-repair|state-bank|policy-tool-bank-mock/i.test(text);
}

async function selectVisibleBankLikeAgent3(page) {
  const mock = payload.mock_data || {};
  const bankPair = String(mock.bankAccountPair_107 || '').split('|');
  const bankName = bankPair[0] || String(mock.bankName_107 || mock.openBank_107 || '中国工商银行');
  const bankValue = bankPair[1] || String(mock.bankValue_107 || mock.bankControlValue_107 || '');
  const result = await page.evaluate(async ({ bankName, bankValue }) => {
    const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
    const norm = text => String(text || '').replace(/\s+/g, '').trim();
    const visible = el => {
      if (!el) return false;
      const rect = el.getBoundingClientRect();
      const style = window.getComputedStyle(el);
      return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
    };
    const getFiber = node => {
      if (!node) return null;
      const key = Object.keys(node).find(item => item.startsWith('__reactFiber$') || item.startsWith('__reactInternalInstance$'));
      return key ? node[key] : null;
    };
    const reactText = value => {
      if (value == null || typeof value === 'boolean') return '';
      if (typeof value === 'string' || typeof value === 'number') return String(value);
      if (Array.isArray(value)) return value.map(reactText).join('');
      if (value.props) return reactText(value.props.children);
      return '';
    };
    const flattenChildren = children => {
      if (children == null) return [];
      if (Array.isArray(children)) return children.flatMap(flattenChildren);
      return [children];
    };
    const fire = el => {
      if (!el) return;
      const eventLike = { target: el, currentTarget: el, preventDefault() {}, stopPropagation() {} };
      const reactKey = Object.keys(el).find(key => key.startsWith('__reactEventHandlers'));
      const handler = reactKey ? el[reactKey]?.onClick : null;
      if (typeof handler === 'function') {
        try { handler(eventLike); } catch (_) {}
      }
      let fiber = getFiber(el);
      let depth = 0;
      while (fiber && depth < 12) {
        const props = fiber.memoizedProps || fiber.pendingProps || fiber._currentElement?.props;
        if (props && typeof props.onClick === 'function') {
          try { props.onClick(eventLike); } catch (_) {}
        }
        fiber = fiber.return || fiber._hostParent || fiber._currentElement?._owner;
        depth += 1;
      }
      for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
        el.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
      }
      if (typeof el.click === 'function') el.click();
    };
    const insidePopup = el => Boolean(el.closest('.am-picker-popup,[role="dialog"],.job-modal,.adm-popup,.am-modal'));
    const fieldRows = label => {
      const rows = [];
      const seen = new Set();
      for (const node of Array.from(document.querySelectorAll('div,li,label,section')).filter(visible)) {
        const nodeText = norm(node.innerText || node.textContent);
        if (nodeText !== label) continue;
        if (insidePopup(node)) continue;
        let row = node;
        for (let depth = 0; row.parentElement && depth < 8; depth += 1) {
          const parent = row.parentElement;
          const text = norm(parent.innerText || parent.textContent);
          if (!text.includes(label) || text.length > 260) break;
          row = parent;
          if (/请选择|请重新选择|展开|>$/.test(text) || parent.querySelector('input,.am-list-extra,.am-list-arrow,[role="button"]')) break;
        }
        if (!seen.has(row)) {
          seen.add(row);
          rows.push(row);
        }
      }
      return rows.filter(visible).sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top);
    };
    const openRow = async row => {
      row.scrollIntoView({ block: 'center', inline: 'center' });
      await sleep(200);
      const rect = row.getBoundingClientRect();
      const points = [
        [rect.right - 22, rect.top + rect.height / 2],
        [rect.left + rect.width * 0.68, rect.top + rect.height / 2],
        [rect.left + rect.width / 2, rect.top + rect.height / 2],
      ];
      for (const [x, y] of points) {
        const target = document.elementFromPoint(Math.max(1, x), Math.max(1, y));
        fire(target instanceof HTMLElement ? target : row);
        await sleep(420);
        if (document.querySelector('.am-picker-popup,[role="dialog"],.job-modal,.adm-popup,.am-modal')) return true;
      }
      fire(row);
      await sleep(420);
      return Boolean(document.querySelector('.am-picker-popup,[role="dialog"],.job-modal,.adm-popup,.am-modal'));
    };
    const clickPickerConfirm = async () => {
      const candidates = Array.from(document.querySelectorAll('.am-picker-popup-header-right, [role="dialog"] button, .am-modal-button, button, a, span, div'))
        .filter(visible)
        .map(el => ({ el, text: norm(el.innerText || el.textContent) }))
        .filter(item => item.text === '确定' || item.text === '完成' || item.text === '确认');
      const scoped = candidates.find(item => item.el.closest('.am-picker-popup,[role="dialog"],.adm-popup,.am-modal')) || candidates[0];
      if (!scoped) return false;
      fire(scoped.el);
      await sleep(500);
      return true;
    };
    const setPickerCol = async (colIndex, preferredTexts) => {
      const texts = preferredTexts.map(text => norm(text)).filter(Boolean);
      const cols = Array.from(document.querySelectorAll('.am-picker-col')).filter(visible);
      const col = cols[colIndex] || document.querySelectorAll('.am-picker-col')[colIndex];
      if (!col) return null;
      let selectedValue = '';
      let selectedText = '';
      let onValueChange = null;
      const seen = new Set();
      const visit = fiber => {
        if (!fiber || seen.has(fiber) || (selectedValue && onValueChange)) return;
        seen.add(fiber);
        const props = fiber.memoizedProps || fiber.pendingProps;
        if (props) {
          if (typeof props.onValueChange === 'function') onValueChange = props.onValueChange;
          for (const child of flattenChildren(props.children)) {
            const text = norm(reactText(child?.props?.children));
            if (!text) continue;
            const exact = texts.find(target => text === target);
            const fuzzy = texts.find(target => text.includes(target) || target.includes(text));
            if ((exact || fuzzy) && child?.props?.value != null) {
              selectedValue = String(child.props.value);
              selectedText = text;
              break;
            }
          }
        }
        visit(fiber.child);
        visit(fiber.sibling);
      };
      let root = getFiber(col);
      let depth = 0;
      while (root && depth < 8) {
        visit(root);
        if (selectedValue && onValueChange) break;
        root = root.return;
        depth += 1;
      }
      if (!selectedValue) {
        const target = Array.from(col.querySelectorAll('.am-picker-col-item')).filter(visible)
          .find(item => texts.some(text => norm(item.textContent) === text || norm(item.textContent).includes(text) || text.includes(norm(item.textContent))));
        if (target) {
          selectedText = norm(target.textContent);
          let fiber = getFiber(target);
          while (fiber && !selectedValue) {
            const props = fiber.memoizedProps || fiber.pendingProps;
            if (props?.value != null) selectedValue = String(props.value);
            fiber = fiber.return;
          }
        }
      }
      if (!onValueChange) {
        let fiber = getFiber(col);
        while (fiber && !onValueChange) {
          const props = fiber.memoizedProps || fiber.pendingProps;
          if (props && typeof props.onValueChange === 'function') onValueChange = props.onValueChange;
          fiber = fiber.return;
        }
      }
      if (!selectedValue || !onValueChange) return null;
      onValueChange(selectedValue);
      await sleep(450);
      return selectedText || selectedValue;
    };
    const setSingleDirect = async (row, label, preferredTexts) => {
      const wants = preferredTexts.map(text => norm(text)).filter(Boolean);
      const source = row.querySelector('.am-list-content')?.closest('.am-list-item') || row.closest?.('.am-list-item') || row;
      let fiber = getFiber(source);
      let depth = 0;
      while (fiber && depth < 16) {
        const props = fiber.memoizedProps || fiber.pendingProps || fiber._currentElement?.props;
        if (props && Array.isArray(props.data) && props.data.length) {
          const chosen = (bankValue ? props.data.find(item => String(item.value) === String(bankValue)) : null)
            || props.data.find(item => wants.some(want => norm(item.label).includes(want) || want.includes(norm(item.label))))
            || props.data.find(item => /银行/.test(norm(item.label)))
            || props.data[0];
          if (chosen?.value != null && typeof props.onChange === 'function') {
            props.onChange([String(chosen.value)]);
            await sleep(650);
            return chosen.label || chosen.value;
          }
        }
        fiber = fiber.return || fiber._hostParent || fiber._currentElement?._owner;
        depth += 1;
      }
      return null;
    };
    const preferred = [bankName, '中国工商银行', '工商银行', '中国建设银行', '招商银行', '中国银行', '农业银行'];
    for (const label of ['开户银行', '开户行']) {
      for (const row of fieldRows(label)) {
        const rowText = String(row.innerText || row.textContent || '').replace(/\s+/g, ' ').trim();
        if (await openRow(row)) {
          const selected = await setPickerCol(0, preferred);
          if (selected && await clickPickerConfirm()) return { selected: true, strategy: 'h5-single-picker', label, selected_text: selected, row_text: rowText };
        }
        const direct = await setSingleDirect(row, label, preferred);
        if (direct) return { selected: true, strategy: 'h5-single-fiber', label, selected_text: direct, row_text: rowText };
      }
    }
    return { selected: false, reason: 'bank row/options not selectable' };
  }, { bankName, bankValue }).catch(error => ({ selected: false, reason: String(error?.message || error) }));
  log({ type: 'bank-picker-agent3-select', message: 'selected bank with Agent3-style picker flow', bank_name: bankName, result, url: page.url() });
  return { bankName, ...result };
}

async function clearVisibleBankRecognitionFeedbackLikeAgent3(page) {
  const result = await page.evaluate(() => {
    const norm = value => String(value || '').replace(/\s+/g, ' ').trim();
    const visible = element => !!element && !!(element.offsetWidth || element.offsetHeight || element.getClientRects().length);
    const blockerRe = /开户行识别失败|手动选择开户行/;
    const bankFieldRe = /开户银行|开户行|银行账号|银行卡号|银行账户|开卡信息|储蓄卡|账号/;
    let hiddenFeedback = 0;
    let clearedRows = 0;
    for (const node of Array.from(document.querySelectorAll('.am-toast,.am-toast-notice,.adm-toast,.am-modal,.am-modal-wrap,[role="dialog"]'))) {
      if (!blockerRe.test(node.innerText || node.textContent || '')) continue;
      node.style.display = 'none';
      node.style.visibility = 'hidden';
      node.setAttribute('aria-hidden', 'true');
      hiddenFeedback += 1;
    }
    for (const input of Array.from(document.querySelectorAll('input,textarea')).filter(visible)) {
      const row = input.closest('.am-list-item,.am-list-line,.insure-filed-wrapper,li,dd,label,div');
      const probe = `${input.getAttribute('placeholder') || ''} ${input.name || ''} ${input.id || ''} ${row?.innerText || ''}`;
      if (!bankFieldRe.test(probe) || /证件号码|身份证|手机号|验证码|短信/.test(probe)) continue;
      input.classList.remove('am-input-error', 'error');
      input.removeAttribute('aria-invalid');
      row?.classList.remove('am-input-error', 'am-list-item-error', 'error');
      row?.removeAttribute('aria-invalid');
      row?.querySelectorAll?.('.am-input-error-extra,.am-list-error-extra,[class*="error"],[class*="Error"]').forEach(errorNode => {
        const text = norm(errorNode.innerText || errorNode.textContent);
        if (!text || bankFieldRe.test(text) || bankFieldRe.test(norm(row.innerText || row.textContent))) {
          errorNode.textContent = '';
          try { errorNode.innerText = ''; } catch (_) {}
          errorNode.classList.remove('am-input-error-extra', 'error');
          errorNode.style.display = 'none';
        }
      });
      clearedRows += 1;
    }
    for (const node of Array.from(document.querySelectorAll('.am-input-error,.am-list-item-error,[class*="error"],[class*="Error"]')).filter(visible)) {
      const text = norm(node.innerText || node.textContent);
      if (!bankFieldRe.test(text) && !blockerRe.test(text)) continue;
      node.classList.remove('am-input-error', 'am-list-item-error', 'error');
      node.removeAttribute('aria-invalid');
      if (blockerRe.test(text)) node.style.display = 'none';
      clearedRows += 1;
    }
    return { hidden_feedback: hiddenFeedback, cleared_rows: clearedRows };
  }).catch(error => ({ hidden_feedback: 0, cleared_rows: 0, error: String(error?.message || error) }));
  log({ type: 'bank-recognition-feedback-clear', message: 'cleared Agent3-style bank recognition feedback', result, url: page.url() });
  return result;
}

async function repairVisibleBankSelectionAfterAccountInputLikeAgent3(page) {
  const mock = payload.mock_data || {};
  const bankPair = String(mock.bankAccountPair_107 || '').split('|');
  const bankName = bankPair[0] || String(mock.bankName_107 || mock.openBank_107 || '');
  const bankValue = bankPair[1] || String(mock.bankValue_107 || mock.bankControlValue_107 || '');
  const payAccount = String(bankPair[2] || mock.payAccount_107 || '').replace(/\s+/g, '');
  const cardOwner = String(mock.cardOwner_107 || mock['applicant.name'] || '');
  if (!bankName || !bankValue || !/^\d{10,30}$/.test(payAccount)) {
    const skipped = { changed: 0, inputCount: 0, skipped: true, reason: 'missing bankName/bankValue/payAccount' };
    log({ type: 'bank-recognition-state-repair', message: 'skipped Agent3 bank recognition state sync', result: skipped, url: page.url() });
    return skipped;
  }
  const result = await page.evaluate(({ bankName, bankValue, payAccount, cardOwner }) => {
    const clearField = (field = {}, value = '') => ({
      ...field,
      value,
      hasError: false,
      hasAjaxError: false,
      error: false,
      errorMsg: '',
      msg: '',
      ajaxError: '',
      validateStatus: 'success',
      validStatus: true,
    });
    const patchRow = row => {
      if (!row || typeof row !== 'object') return false;
      row.bank = {
        ...clearField(row.bank || {}, bankValue),
        label: bankName,
        text: bankName,
        name: bankName,
        valueText: bankName,
        controlValue: bankValue,
      };
      row.payAccount = clearField(row.payAccount || {}, payAccount);
      row.cardOwner = clearField(row.cardOwner || {}, cardOwner);
      return true;
    };
    const patchPlain = obj => {
      let changed = 0;
      const roots = [
        obj?.product?.insure?.data?.data,
        obj?.insure?.data?.data,
        obj?.data?.data,
        obj?.data,
      ].filter(Boolean);
      for (const root of roots) {
        const rows = root['107'] || root[107];
        const row = Array.isArray(rows) ? rows[0] : rows;
        if (patchRow(row)) changed += 1;
      }
      return changed;
    };
    let changed = patchPlain(window.__NEXT_DATA__);
    for (const storage of [window.localStorage, window.sessionStorage]) {
      if (!storage) continue;
      for (let i = 0; i < storage.length; i += 1) {
        const key = storage.key(i);
        if (!key || !/insure|product|123602|126878/i.test(key)) continue;
        try {
          const value = storage.getItem(key);
          if (!value || value[0] !== '{') continue;
          const json = JSON.parse(value);
          const storageChanged = patchPlain(json);
          if (storageChanged) {
            storage.setItem(key, JSON.stringify(json));
            changed += storageChanged;
          }
        } catch (_) {}
      }
    }
    const store = window.__NEXT_REDUX_STORE__ || window.store || window.reduxStore;
    if (store && typeof store.getState === 'function') {
      try {
        const state = store.getState();
        let next = state;
        const setIn = (path, value) => {
          if (next && typeof next.setIn === 'function') {
            next = next.setIn(path, value);
            changed += 1;
          }
        };
        for (const base of [
          ['product', 'insure', 'data', 'data', '107', 0],
          ['insure', 'data', 'data', '107', 0],
          ['data', 'data', '107', 0],
          ['data', '107', 0],
        ]) {
          for (const [key, value] of [
            ['bank.value', bankValue],
            ['bank.label', bankName],
            ['bank.text', bankName],
            ['bank.name', bankName],
            ['bank.valueText', bankName],
            ['bank.controlValue', bankValue],
            ['bank.hasError', false],
            ['bank.hasAjaxError', false],
            ['bank.error', false],
            ['bank.errorMsg', ''],
            ['bank.msg', ''],
            ['bank.ajaxError', ''],
            ['payAccount.value', payAccount],
            ['payAccount.hasError', false],
            ['payAccount.hasAjaxError', false],
            ['payAccount.error', false],
            ['payAccount.errorMsg', ''],
            ['payAccount.msg', ''],
            ['payAccount.ajaxError', ''],
            ['cardOwner.value', cardOwner],
            ['cardOwner.hasError', false],
            ['cardOwner.hasAjaxError', false],
            ['cardOwner.errorMsg', ''],
            ['cardOwner.msg', ''],
          ]) {
            setIn([...base, ...key.split('.')], value);
          }
        }
        if (next !== state) store.getState = () => next;
      } catch (_) {}
    }
    let inputCount = 0;
    for (const el of Array.from(document.querySelectorAll('input,textarea'))) {
      const row = el.closest('.am-list-item,.am-list-line,.insure-filed-wrapper,li,dd,label,div');
      const probe = `${el.name || ''} ${el.id || ''} ${el.placeholder || ''} ${el.getAttribute('aria-label') || ''} ${row?.innerText || ''}`;
      if (!/payAccount|bankAccount|银行账号|银行卡号|银行账户|开卡信息|储蓄卡|账号/i.test(probe)) continue;
      el.dataset.agent4PayAccountLocked = '1';
      el.classList.remove('am-input-error', 'error');
      row?.classList.remove('am-input-error', 'am-list-item-error', 'error');
      row?.querySelectorAll?.('.am-input-error-extra,.error,.am-list-error-extra').forEach(node => {
        node.style.display = 'none';
      });
      inputCount += 1;
    }
    for (const node of Array.from(document.querySelectorAll('.am-input-error,.am-list-item-error'))) {
      const text = node.innerText || '';
      if (/银行账号|银行卡号|开户行|储蓄卡|账号/.test(text)) node.classList.remove('am-input-error', 'am-list-item-error', 'error');
    }
    for (const node of Array.from(document.querySelectorAll('.am-toast,.am-toast-notice,.adm-toast,.am-modal,.am-modal-wrap,[role="dialog"]'))) {
      if (/开户行识别失败|手动选择开户行/.test(node.innerText || '')) node.style.display = 'none';
    }
    window.__agent4PayAccountLocked = true;
    window.__agent4SkipPayAccountWrites = true;
    window.__agent4PayAccountValue = payAccount;
    window.__agent4BankName = bankName;
    window.__agent4BankValue = bankValue;
    return { changed, inputCount, bankName, bankValue, payAccount };
  }, { bankName, bankValue, payAccount, cardOwner }).catch(error => ({ changed: 0, inputCount: 0, error: String(error?.message || error) }));
  log({ type: 'bank-recognition-state-repair', message: 'synced bank recognition state like Agent3', result, url: page.url() });
  return result;
}

async function repairVisibleBankPickerLikeAgent3(page, action, reason, options = {}) {
  const initialDiagnostics = await inspectPostSubmitDiagnostics(page);
  const skipAccountRefill = Boolean(options.skip_account_refill) || hasBankRecognitionBlocker(initialDiagnostics);
  log({ type: 'bank-agent3-flow-start', message: 'apply Agent3-style bank picker/account/state flow', reason, skip_account_refill: skipAccountRefill, action_text: action?.text || '', url: page.url() });
  if (skipAccountRefill) {
    await clearVisibleBankRecognitionFeedbackLikeAgent3(page);
  } else {
    await waitForTransientToastGone(page);
  }
  const bankSelect = await selectVisibleBankLikeAgent3(page);
  const bankAccountRefill = skipAccountRefill
    ? { skipped: true, reason: 'bank-recognition-blocker' }
    : await fillVisibleBankAccountThenBlur(page);
  await syncVisibleH5InsureModelState(page);
  const stateRepair = await repairVisibleBankSelectionAfterAccountInputLikeAgent3(page);
  await clearVisibleBankAccountError(page);
  await clearVisibleBankRecognitionFeedbackLikeAgent3(page);
  await sleep(900);
  const diagnostics = await inspectPostSubmitDiagnostics(page);
  const result = { applied: true, bank_select: bankSelect, bank_account_refill: bankAccountRefill, state_repair: stateRepair, diagnostics };
  log({ type: 'bank-agent3-flow-end', message: 'finished Agent3-style bank picker/account/state flow', result, url: page.url() });
  return result;
}

function findTaskModalResumeIndex(actions, currentIndex, matchedNodes) {
  const matches = Array.isArray(matchedNodes) ? matchedNodes : [];
  const preferred = ['NODE-suitability', 'NODE-risk-control', 'NODE-payment', 'NODE-policy-result'];
  const orderedNodes = [
    ...preferred.filter(node => matches.includes(node)),
    ...preferred.filter(node => !matches.includes(node)),
  ];
  for (const nodeId of orderedNodes) {
    for (let index = currentIndex + 1; index < actions.length; index += 1) {
      if (String(actions[index]?.planned_from_node_id || '') === nodeId) return index;
    }
  }
  return -1;
}

async function clickTaskModalGoCompleteIfPresent(page) {
  const dialog = page
    .locator('.task-modal, .am-modal, .am-modal-wrap, .adm-modal, .adm-popup, [role="dialog"]')
    .filter({ hasText: /即将进行以下操作|适当性问卷|身份认证|银行卡签约|去完成/ })
    .last();
  if (!(await dialog.isVisible({ timeout: 1800 }).catch(() => false))) {
    return { clicked: false, reason: 'task modal not visible' };
  }
  const button = dialog
    .locator('button, a, [role="button"], .am-button, .adm-button, .btn, span, div')
    .filter({ hasText: /去完成|继续|下一步|完成/ })
    .last();
  if (!(await button.isVisible({ timeout: 1200 }).catch(() => false))) {
    return { clicked: false, reason: 'task modal go-complete button not visible' };
  }
  const text = await button.innerText({ timeout: 1000 }).catch(() => '');
  const tap = await tapLocatorCenter(page, button).catch(async () => {
    await button.click({ timeout: 5000, force: true, noWaitAfter: true });
    return { method: 'force-click' };
  });
  await page.waitForLoadState('domcontentloaded', { timeout: 15000 }).catch(() => undefined);
  await sleep(stepDelayMs);
  const result = { clicked: true, strategy: 'task-modal-go-complete', text: String(text || '').slice(0, 80), tap, url: page.url() };
  log({ type: 'task-modal-go-complete', message: 'clicked task modal go-complete button', result, url: page.url() });
  return result;
}

async function recoverH5SubmitBlockerAndRetry(page, action, diagnostics) {
  if (!hasBankAccountSubmitBlocker(diagnostics)) {
    return { retried: false, reason: 'no bank account submit blocker' };
  }
  const recognitionBlocker = hasBankRecognitionBlocker(diagnostics);
  await collapseSelfInsuredDetails(page);
  const manualBankRepair = await repairVisibleBankPickerLikeAgent3(page, action, 'submit-blocker', { skip_account_refill: recognitionBlocker });
  if (recognitionBlocker && isBankReadyToSubmitAfterManualSelection(manualBankRepair?.diagnostics)) {
    log({ type: 'bank-recognition-submit-ready', message: 'bank manually selected and bank error indicator cleared; submit immediately', diagnostics: manualBankRepair.diagnostics, url: page.url() });
  }
  const retryClickResult = await clickH5SubmitButton(page, action);
  log({ type: 'h5-submit-retry-click', message: 'retried h5 submit after bank account blocker', click_result: retryClickResult, url: page.url() });
  if (!retryClickResult.clicked && !action.skip_if_absent) {
    throw new Error(retryClickResult.reason || 'submit retry did not click');
  }
  await page.waitForLoadState('domcontentloaded', { timeout: 15000 }).catch(() => undefined);
  await sleep(stepDelayMs);
  await waitForSuitabilitySubmitBlocker(page);
  const retryDiagnostics = await inspectPostSubmitDiagnostics(page);
  log({ type: 'post-submit-retry-diagnostics', message: 'state after h5 submit retry click', diagnostics: retryDiagnostics, url: page.url() });
  return { retried: true, click_result: retryClickResult, diagnostics: retryDiagnostics, manual_bank_repair: manualBankRepair };
}

function recentSuitabilitySubmitBlocker() {
  const responses = Array.isArray(globalThis.__agent4NetworkResponses)
    ? globalThis.__agent4NetworkResponses
    : [];
  for (const response of responses.slice(-30).reverse()) {
    const url = String(response?.url || '');
    const body = String(response?.body_excerpt || '');
    if (!/\/api\/apps\/cps\/insure\/submit/i.test(url)) continue;
    if (/40015|需要进行适当性问卷|适当性问卷/.test(body)) return response;
  }
  return null;
}

async function waitForSuitabilitySubmitBlocker(page, timeoutMs = 12000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const blocker = recentSuitabilitySubmitBlocker();
    if (blocker) {
      log({ type: 'submit-suitability-response', message: 'observed submit suitability blocker response', blocker, url: page.url() });
      return blocker;
    }
    await sleep(500);
  }
  return null;
}

function diagnosticsNeedSuitabilityTask(diagnostics) {
  const values = [
    diagnostics?.bodyTail,
    ...(Array.isArray(diagnostics?.toasts) ? diagnostics.toasts : []),
    ...(Array.isArray(diagnostics?.dialogs) ? diagnostics.dialogs : []),
    ...(Array.isArray(diagnostics?.errors) ? diagnostics.errors.map(item => item?.text || '') : []),
  ].join(' ');
  return /40015|需要进行适当性问卷|适当性问卷|保险产品适当性问卷/.test(values) || Boolean(recentSuitabilitySubmitBlocker());
}

function shouldProbeSuitabilityTaskAfterSubmit(action, currentUrl, diagnostics) {
  const expectedNodeId = String(action?.expected_next_node_id || action?.planned_to_node_id || '');
  const expectedPath = urlPathname(action?.target_url || '');
  const currentPath = urlPathname(currentUrl);
  const explicitSuitabilityTarget = /\/product\/adapt(?:\/|$)/i.test(expectedPath) || expectedNodeId === 'NODE-suitability';
  const hasExplicitSuitabilityEvidence = diagnosticsNeedSuitabilityTask(diagnostics) || Boolean(recentSuitabilitySubmitBlocker());
  if (!explicitSuitabilityTarget && !hasExplicitSuitabilityEvidence) return false;
  if (!/\/product\/insure$/i.test(currentPath)) return false;
  if (hasBankAccountSubmitBlocker(diagnostics) || hasBankRecognitionBlocker(diagnostics)) return false;
  return true;
}

function suitabilityTaskUrlsFromSubmit(page, action, submitApiResult = null) {
  const urls = [];
  const submitEncryptInsureNum = submitApiResult?.response_order?.encryptInsureNum || '';
  const rawUrls = [page.url(), action?.target_url];
  if (submitEncryptInsureNum) {
    for (const raw of rawUrls) {
      try {
        const parsed = new URL(String(raw || ''), page.url());
        const base = `${parsed.origin}/m/apps/cps/demo-channel/product`;
        urls.push(`${base}/adapt/loading?encryptInsureNum=${encodeURIComponent(submitEncryptInsureNum)}`);
        urls.push(`${base}/adapt?encryptInsureNum=${encodeURIComponent(submitEncryptInsureNum)}`);
      } catch (_error) {
        // Ignore malformed observed URLs; the next candidate may still be usable.
      }
    }
  }
  for (const raw of rawUrls) {
    try {
      const parsed = new URL(String(raw || ''), page.url());
      const encryptInsureNum = parsed.searchParams.get('encryptInsureNum');
      if (!encryptInsureNum) continue;
      const base = `${parsed.origin}/m/apps/cps/demo-channel/product`;
      urls.push(`${base}/adapt/loading?encryptInsureNum=${encodeURIComponent(encryptInsureNum)}`);
      urls.push(`${base}/adapt?encryptInsureNum=${encodeURIComponent(encryptInsureNum)}`);
    } catch (_error) {
      // Ignore malformed observed URLs; the next candidate may still be usable.
    }
  }
  return [...new Set(urls)];
}

async function openSuitabilityTaskFromSubmit(page, action, submitApiResult = null) {
  if (isSuitabilityQuestionnairePageUrl(page.url()) || isSuitabilityResultPageUrl(page.url())) {
    return { opened: true, strategy: 'already-on-suitability-task', url: page.url() };
  }
  for (const url of suitabilityTaskUrlsFromSubmit(page, action, submitApiResult)) {
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 }).catch(() => null);
    await sleep(stepDelayMs);
    await recoverTransientPageError(page);
    const bodyText = await page.locator('body').innerText({ timeout: 3000 }).catch(() => '');
    if (
      isSuitabilityQuestionnairePageUrl(page.url())
      || isSuitabilityResultPageUrl(page.url())
      || /适当性问卷|保险产品适当性问卷|调查问卷|特别提示|问卷/.test(bodyText)
    ) {
      return { opened: true, strategy: 'goto-suitability-task', requested_url: url, url: page.url() };
    }
  }
  return { opened: false, strategy: 'goto-suitability-task', reason: 'suitability task URL did not open questionnaire', url: page.url() };
}

async function recoverSuitabilityTaskAfterSubmitIfNeeded(page, action, actions, currentIndex, diagnostics, appliedKeys, submitApiResult = null) {
  const shouldProbeSuitabilityTask = shouldProbeSuitabilityTaskAfterSubmit(action, page.url(), diagnostics);
  if (
    !diagnosticsNeedSuitabilityTask(diagnostics)
    && !isSuitabilityQuestionnairePageUrl(page.url())
    && !isSuitabilityResultPageUrl(page.url())
    && !shouldProbeSuitabilityTask
  ) {
    return { recovered: false, reason: 'no suitability submit blocker' };
  }
  const taskModal = await clickTaskModalGoCompleteIfPresent(page);
  const navigation = taskModal.clicked
    ? { opened: true, strategy: 'task-modal-go-complete', url: page.url() }
    : await openSuitabilityTaskFromSubmit(page, action, submitApiResult);
  if (!navigation.opened && !isSuitabilityQuestionnairePageUrl(page.url()) && !isSuitabilityResultPageUrl(page.url())) {
    return {
      recovered: false,
      strategy: 'submit-suitability-task-recovery',
      reason: 'suitability task not reachable',
      blocker: recentSuitabilitySubmitBlocker(),
      task_modal: taskModal,
      navigation,
      url: page.url(),
    };
  }

  const answerActions = agent3SuitabilityAnswerActionsForSubmitRecovery(actions, currentIndex, appliedKeys);
  let autoQuestionnaireResult = null;
  let suitabilityResultClick = null;
  let suitabilityRepair = null;
  if (isSuitabilityQuestionnairePageUrl(page.url())) {
    autoQuestionnaireResult = await autoAnswerQuestionnaireIfPresent(
      page,
      { ...action, planned_from_node_id: 'NODE-suitability' },
      currentIndex + 1,
      answerActions
    );
    if (autoQuestionnaireResult?.submitted) {
      for (const answerAction of answerActions) {
        appliedKeys.add(agent3SuitabilityAnswerKey(answerAction));
      }
    }
  }
  if (isSuitabilityResultPageUrl(page.url())) {
    suitabilityResultClick = await clickSuitabilityResultContinueIfPresent(page);
    if (!suitabilityResultClick.clicked) {
      suitabilityRepair = await repairSuitabilityMismatchAndResubmit(page);
      if (suitabilityRepair?.submitted) {
        suitabilityResultClick = await clickSuitabilityResultContinueIfPresent(page);
        suitabilityResultClick.repair = suitabilityRepair;
      }
    }
  }
  const recovered = Boolean(
    taskModal.clicked
    || autoQuestionnaireResult?.submitted
    || suitabilityResultClick?.clicked
    || suitabilityRepair?.submitted
  );
  const result = {
    recovered,
    strategy: 'submit-suitability-task-recovery',
    blocker: recentSuitabilitySubmitBlocker(),
    task_modal: taskModal,
    navigation,
    auto_questionnaire_result: autoQuestionnaireResult,
    suitability_result: suitabilityResultClick,
    suitability_repair: suitabilityRepair,
    url: page.url(),
  };
  log({ type: 'submit-suitability-task-recovery', message: 'handled submit suitability task blocker', result, url: page.url() });
  return result;
}

async function clickLastVisibleByPattern(page, locator, pattern) {
  const count = await locator.count().catch(() => 0);
  for (let index = count - 1; index >= 0; index -= 1) {
    const candidate = locator.nth(index);
    if (!(await candidate.isVisible({ timeout: 800 }).catch(() => false))) continue;
    const text = await candidate.innerText({ timeout: 800 }).catch(async () => {
      return await candidate.inputValue({ timeout: 800 }).catch(() => '');
    });
    const compact = compactActionText(text);
    if (pattern && !pattern.test(compact)) continue;
    await candidate.scrollIntoViewIfNeeded().catch(() => undefined);
    await candidate.click({ timeout: 10000, noWaitAfter: true }).catch(async () => {
      await candidate.click({ timeout: 10000, noWaitAfter: true, force: true });
    });
    await sleep(500);
    return String(text || '').slice(0, 80);
  }
  return null;
}

async function clickPremiumQuoteAction(page) {
  const result = await page.evaluate(() => {
    function isVisible(element) {
      const style = window.getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.visibility !== 'hidden'
        && style.display !== 'none'
        && rect.width > 0
        && rect.height > 0;
    }
    function textOf(element) {
      return String(element.innerText || element.textContent || element.getAttribute?.('value') || '').replace(/\s+/g, ' ').trim();
    }
    function compactText(value) {
      return String(value || '').replace(/\s+/g, '').trim();
    }
    function isFixedOrSticky(element) {
      let current = element;
      while (current && current !== document.body) {
        const position = window.getComputedStyle(current).position;
        if (position === 'fixed' || position === 'sticky') return true;
        current = current.parentElement;
      }
      return false;
    }
    const candidates = Array.from(document.querySelectorAll('button, [role="button"], a, .btn, .button, input[type="button"], input[type="submit"], div, span'))
      .filter(isVisible)
      .map(element => {
        const rect = element.getBoundingClientRect();
        const text = textOf(element);
        const compact = compactText(text);
        return {
          element,
          text,
          compact,
          fixed: isFixedOrSticky(element),
          rectBottom: rect.bottom,
          area: rect.width * rect.height,
        };
      })
      .filter(item => item.compact === '保费试算' || item.compact.includes('保费试算'));
    candidates.sort((left, right) => {
      if (Number(right.fixed) !== Number(left.fixed)) return Number(right.fixed) - Number(left.fixed);
      if (right.rectBottom !== left.rectBottom) return right.rectBottom - left.rectBottom;
      return left.area - right.area;
    });
    const target = candidates[0]?.element;
    if (!target) return { clicked: false, reason: 'premium quote button not found' };
    target.scrollIntoView({ block: 'center', inline: 'nearest' });
    target.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
    target.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
    target.click();
    return { clicked: true, text: textOf(target), strategy: 'premium-quote-dom-click', candidate_count: candidates.length };
  }).catch(error => ({ clicked: false, reason: String(error) }));
  if (result.clicked) {
    await page.waitForLoadState('domcontentloaded', { timeout: 15000 }).catch(() => undefined);
    await sleep(stepDelayMs);
  }
  return result;
}

async function acceptTrialPanelIfPresent(page) {
  const trialKeywords = /保费\s*试算|保费与被保人|价值演示|被保险人出生日期|保障期限|缴费类型|缴费年限|承保职业/;
  const panels = page.locator('.trial-pannel, .trial-panel, [role="dialog"], .am-modal, .am-modal-wrap, .am-popup, .am-popup-body, .am-drawer, .am-drawer-content').filter({ hasText: trialKeywords });
  const panelCount = await panels.count().catch(() => 0);
  for (let panelIndex = panelCount - 1; panelIndex >= 0; panelIndex -= 1) {
    const panel = panels.nth(panelIndex);
    if (!(await panel.isVisible({ timeout: 1200 }).catch(() => false))) continue;
    const text = await clickLastVisibleByPattern(
      page,
      panel.locator('button, a, [role="button"], .am-button, .am-button-primary, .submit-btn, .btn, span, div').filter({ hasText: /确\s*定|确\s*认|下一步|立即投保|投\s*保/ }),
      /^(确定|确认|下一步|立即投保|投保)$/
    );
    if (text) return { accepted: true, strategy: 'trial-panel-confirm', text };
    return { accepted: false, strategy: 'trial-panel-confirm', text: 'trial panel visible but confirm button not found' };
  }
  return null;
}

async function acceptContinuationDialogIfPresent(page) {
  const dialog = page.locator('[role="dialog"], .am-modal, .am-modal-wrap, .am-modal-content').filter({ hasText: /继续投保|已有|未完成|重复投保/ }).last();
  if (!(await dialog.isVisible({ timeout: 1000 }).catch(() => false))) return null;
  const text = await clickLastVisibleByPattern(
    page,
    dialog.locator('button, a, [role="button"], .am-button, .btn, span, div').filter({ hasText: /确定|确认|继续投保|继续|是/ }),
    /确定|确认|继续投保|继续|是/
  );
  return { accepted: Boolean(text), strategy: 'continuation-dialog-confirm', text: text || 'continuation dialog visible but confirm button not found' };
}

async function forceOpenHealthInformFromProductDetail(page) {
  const currentUrl = page.url();
  if (!/\/product\/detail/i.test(currentUrl)) return null;
  let targetUrl = '';
  try {
    const parsed = new URL(currentUrl);
    parsed.pathname = parsed.pathname.replace(/\/product\/detail/i, '/product/healthInform');
    targetUrl = parsed.toString();
  } catch (_error) {
    targetUrl = currentUrl.replace(/\/product\/detail/i, '/product/healthInform');
  }
  if (!targetUrl || targetUrl === currentUrl) return null;
  await page.goto(targetUrl, { waitUntil: 'domcontentloaded', timeout: 60000 }).catch(async () => {
    await page.goto(targetUrl, { waitUntil: 'load', timeout: 60000 });
  });
  await sleep(stepDelayMs);
  const url = page.url();
  const bodyText = await page.locator('body').innerText({ timeout: 3000 }).catch(() => '');
  const reached = /healthInform|health|notice|inform/i.test(url)
    || /健康告知|确认无以上问题|有部分问题/.test(bodyText);
  return { forced: reached, strategy: 'product-detail-to-health-inform', target_url: targetUrl, current_url: url };
}

async function clickReadyProductConfirmButton(page, panel, pattern) {
  const locators = [
    panel.locator('button, a, [role="button"], .am-button, .am-button-primary, .submit-btn, .btn').filter({ hasText: /阅读并同意|已阅读并同意|同意并继续|继续投保|确认投保|立即投保|投\s*保|确定|我知道/ }),
    panel.locator('span, div').filter({ hasText: /阅读并同意|已阅读并同意|同意并继续|继续投保|确认投保|立即投保|投\s*保|确定|我知道/ }),
  ];
  for (const locator of locators) {
    const count = await locator.count().catch(() => 0);
    for (let index = count - 1; index >= 0; index -= 1) {
      const candidate = locator.nth(index);
      if (!(await candidate.isVisible({ timeout: 800 }).catch(() => false))) continue;
      const text = await candidate.innerText({ timeout: 800 }).catch(async () => {
        return await candidate.inputValue({ timeout: 800 }).catch(() => '');
      });
      const compact = compactActionText(text);
      if (pattern && !pattern.test(compact)) continue;
      if (/\d+\s*秒|秒/.test(compact)) {
        return { clicked: false, countdown: true, text: String(text || '').slice(0, 80) };
      }
      await candidate.scrollIntoViewIfNeeded().catch(() => undefined);
      await candidate.click({ timeout: 10000, noWaitAfter: true }).catch(async () => {
        await candidate.click({ timeout: 10000, noWaitAfter: true, force: true });
      });
      await sleep(500);
      return { clicked: true, countdown: false, text: String(text || '').slice(0, 80) };
    }
  }
  return { clicked: false, countdown: false, text: '' };
}

async function acceptProductConfirmPanelIfPresent(page) {
  const panel = page.locator('.confirm-pannel, .confirm-panel, [role="dialog"], .am-modal, .am-modal-wrap, .am-modal-content').filter({ hasText: /确认进入投保流程|已阅读并同意|阅读并同意|投保须知/ }).last();
  if (!(await panel.isVisible({ timeout: 1200 }).catch(() => false))) return null;
  const beforeUrl = page.url();
  const checkboxes = panel.locator('input[type="checkbox"]');
  const checkboxCount = await checkboxes.count().catch(() => 0);
  for (let index = 0; index < checkboxCount; index += 1) {
    const checkbox = checkboxes.nth(index);
    const checked = await checkbox.isChecked().catch(() => false);
    if (!checked) await checkbox.click({ timeout: 2000, force: true }).catch(() => undefined);
  }
  const buttonPattern = /阅读并同意|已阅读并同意|同意并继续|继续投保|确认投保|立即投保|投保|确定|我知道/;
  let lastText = '';
  for (let attempt = 0; attempt < 15; attempt += 1) {
    const clickAttempt = await clickReadyProductConfirmButton(page, panel, buttonPattern);
    const text = clickAttempt.text;
    if (text) lastText = text;
    if (!clickAttempt.clicked) {
      await sleep(clickAttempt.countdown ? 1000 : 800);
      continue;
    }
    await page.waitForLoadState('domcontentloaded', { timeout: 8000 }).catch(() => undefined);
    await sleep(1200);
    const stillVisible = await page
      .locator('.confirm-pannel, .confirm-panel, [role="dialog"], .am-modal, .am-modal-wrap, .am-modal-content')
      .filter({ hasText: /确认进入投保流程|已阅读并同意|阅读并同意|投保须知/ })
      .last()
      .isVisible({ timeout: 500 })
      .catch(() => false);
    const taskModalVisible = await page
      .locator('.task-modal, .am-modal, .am-modal-wrap, .adm-modal, .adm-popup, [role="dialog"]')
      .filter({ hasText: /即将进行以下操作|适当性问卷|身份认证|银行卡签约|去完成/ })
      .last()
      .isVisible({ timeout: 500 })
      .catch(() => false);
    if (text && !/\d+秒|秒/.test(text) && (!stillVisible || taskModalVisible || page.url() !== beforeUrl)) {
      return {
        accepted: true,
        strategy: 'product-confirm-panel',
        text,
        attempts: attempt + 1,
        still_visible: stillVisible,
        task_modal_visible: taskModalVisible,
      };
    }
  }
  return {
    accepted: false,
    strategy: 'product-confirm-panel',
    text: lastText || 'confirm panel visible but enabled confirm button not found',
  };
}

async function settleProductEntryFlow(page) {
  const events = [];
  for (let attempt = 0; attempt < 8; attempt += 1) {
    const before = page.url();
    const handlers = [
      acceptTrialPanelIfPresent,
      acceptProductConfirmPanelIfPresent,
      acceptContinuationDialogIfPresent,
      acceptQuestionnaireWarningIfPresent,
    ];
    let accepted = null;
    for (const handler of handlers) {
      accepted = await handler(page).catch(() => null);
      if (accepted && accepted.accepted !== false) break;
    }
    if (!accepted || accepted.accepted === false) break;
    events.push(accepted);
    await sleep(800);
    if (page.url() !== before) {
      await page.waitForLoadState('domcontentloaded', { timeout: 15000 }).catch(() => undefined);
    }
  }
  return events;
}

async function buyNowAdvanced(page, beforeUrl) {
  const url = page.url();
  if (/\/product\/detail/.test(url)) return false;
  if (/healthInform|health|notice|inform|product\/insure/i.test(url)) return true;
  if (url !== beforeUrl && /healthInform|health|notice|inform|product\/insure/i.test(url)) return true;
  const bodyText = await page.locator('body').innerText({ timeout: 3000 }).catch(() => '');
  return /确认进入投保流程|已阅读并同意|适当性|风险测评|健康告知|确认无以上问题/.test(bodyText);
}

function nextAgent3EntryUrlAfterBuyNow(actions, currentIndex) {
  for (const candidate of (actions || []).slice(currentIndex + 1)) {
    for (const value of [candidate?.source_url, candidate?.target_url]) {
      const raw = String(value || '').trim();
      if (/\/product\/(?:healthInform|insure)(?:\?|$)/i.test(raw)) return raw;
    }
  }
  return '';
}

function nextAgent3InsureFormUrlAfterHealthNotice(actions, currentIndex) {
  for (const candidate of (actions || []).slice(currentIndex + 1)) {
    for (const value of [candidate?.source_url, candidate?.target_url]) {
      const raw = String(value || '').trim();
      if (/\/product\/insure(?:\?|$)/i.test(raw)) return raw;
    }
  }
  return '';
}

async function replayAgent3InsureFormUrlIfNeeded(page, action) {
  if (String(action?.planned_to_node_id || '') !== 'NODE-insure-form') return null;
  if (/\/product\/insure(?:\?|$)/i.test(page.url())) return null;
  const replayUrl = [action.source_url, action.target_url]
    .map(value => String(value || '').trim())
    .find(value => /\/product\/insure(?:\?|$)/i.test(value));
  if (!replayUrl) return null;
  const before = page.url();
  await page.goto(replayUrl, { waitUntil: 'domcontentloaded', timeout: 60000 }).catch(async () => {
    await page.goto(replayUrl, { waitUntil: 'load', timeout: 60000 });
  });
  await sleep(stepDelayMs);
  log({
    type: 'agent3-insure-form-url-replay',
    message: 'replayed Agent3 product/insure URL before synthetic form data',
    source_url: before,
    target_url: page.url(),
    replay_url: replayUrl,
  });
  return { source_url: before, target_url: page.url(), replay_url: replayUrl };
}

async function clickBuyNowAction(page, action, beforeUrl, nextEntryUrl = '') {
  const agent3ReplayStrategy = 'agent3-locator-replay';
  const buyTextRegexSource = '投\\s*保|投保|立即投保|马上投保|去投保|继续投保';
  const desiredText = String(action.text || '立即投保');
  const patterns = [desiredText, '立即投保', '马上投保', '去投保', '继续投保'].filter(Boolean);
  if (await buyNowAdvanced(page, beforeUrl)) {
    return { clicked: true, advanced: true, strategy: 'already advanced before buy-now click', selector: null };
  }
  for (let attempt = 0; attempt < 3; attempt += 1) {
    if (isH5ProductFooterInsureAction(action)) {
      const footerResult = await clickH5ProductFooterInsureAction(page, beforeUrl);
      if (footerResult.clicked && footerResult.advanced) return footerResult;
      const replayTargetUrl = String(action.target_url || '').trim();
      if (/healthInform|health|notice|inform|product\/insure/i.test(replayTargetUrl)) {
        await page.goto(replayTargetUrl, { waitUntil: 'domcontentloaded', timeout: 60000 }).catch(async () => {
          await page.goto(replayTargetUrl, { waitUntil: 'load', timeout: 60000 });
        });
        await sleep(stepDelayMs);
        const advanced = await buyNowAdvanced(page, beforeUrl);
        if (advanced) {
          return {
            ...footerResult,
            clicked: true,
            advanced: true,
            strategy: 'agent3-target-url-replay',
            replay_target_url: replayTargetUrl,
          };
        }
      }
      return footerResult;
    }
    if (isPremiumQuoteAction(action)) {
      const premiumResult = await clickPremiumQuoteAction(page);
      if (premiumResult.clicked) {
        const settle_events = await settleProductEntryFlow(page);
        const advanced = await buyNowAdvanced(page, beforeUrl);
        if (advanced) return { ...premiumResult, advanced, settle_events };
        return {
          ...premiumResult,
          advanced: true,
          prerequisite_only: true,
          strategy: 'premium-quote-prerequisite',
          settle_events,
        };
      }
    }
    const agent3ReplaySource = { selector: action.selector, locators: action.locators };
    const replayResult = await clickActionByAgent3Locator(page, { ...action, ...agent3ReplaySource });
    if (replayResult.clicked) {
      await page.waitForLoadState('domcontentloaded', { timeout: 15000 }).catch(() => undefined);
      await sleep(stepDelayMs);
      const settle_events = await settleProductEntryFlow(page);
      const advanced = await buyNowAdvanced(page, beforeUrl);
      if (advanced) return { ...replayResult, advanced, settle_events };
    }
    const result = await page.evaluate((patterns, buyTextRegexSource) => {
      function isVisible(element) {
        const style = window.getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return style.visibility !== 'hidden'
          && style.display !== 'none'
          && rect.width > 0
          && rect.height > 0;
      }
      function textOf(element) {
        return String(element.innerText || element.textContent || element.getAttribute?.('value') || '').replace(/\s+/g, ' ').trim();
      }
      function compactText(text) {
        return String(text || '').replace(/\s+/g, '');
      }
      function isFixedOrSticky(element) {
        let current = element;
        while (current && current !== document.body) {
          const position = window.getComputedStyle(current).position;
          if (position === 'fixed' || position === 'sticky') return true;
          current = current.parentElement;
        }
        return false;
      }
      const buyTextRegex = new RegExp(buyTextRegexSource);
      const exactBuyButtonText = /^(投保|立即投保|马上投保|去投保|继续投保)$/;
      const candidates = Array.from(document.querySelectorAll('button, [role="button"], a, .btn, .button, input[type="button"], input[type="submit"]'))
        .filter(isVisible)
        .map(element => {
          const rect = element.getBoundingClientRect();
          const text = textOf(element);
          const compact = compactText(text);
          return {
            element,
            text,
            compact,
            rectTop: rect.top,
            rectBottom: rect.bottom,
            fixed: isFixedOrSticky(element),
            exact: exactBuyButtonText.test(compact) || patterns.some(pattern => compactText(pattern) === compact),
          };
        })
        .filter(item => item.exact || buyTextRegex.test(item.text) || patterns.some(text => item.text === text || item.text.includes(text)));
      const preciseCandidates = candidates.filter(item => item.exact);
      const fallbackCandidates = candidates.filter(item => !/投保须知|投保人|被保险人|告知书|声明|流程|说明/.test(item.compact));
      const pool = preciseCandidates.length ? preciseCandidates : fallbackCandidates;
      pool.sort((left, right) => {
        if (Number(right.fixed) !== Number(left.fixed)) return Number(right.fixed) - Number(left.fixed);
        return right.rectBottom - left.rectBottom;
      });
      const target = pool[0]?.element;
      if (!target) return { clicked: false, reason: 'buy button not found' };
      target.scrollIntoView({ block: 'center', inline: 'nearest' });
      target.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
      target.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
      target.click();
      return { clicked: true, text: textOf(target), strategy: 'dom-click', candidate_count: candidates.length };
    }, patterns, buyTextRegexSource).catch(error => ({ clicked: false, reason: String(error) }));

    if (result.clicked) {
      await page.waitForLoadState('domcontentloaded', { timeout: 15000 }).catch(() => undefined);
      await sleep(stepDelayMs);
      const settle_events = await settleProductEntryFlow(page);
      const advanced = await buyNowAdvanced(page, beforeUrl);
      if (advanced) return { ...result, advanced, settle_events };
    }
    await sleep(800);
  }
  if (nextEntryUrl && /healthInform|product\/insure/i.test(nextEntryUrl)) {
    const replaySourceUrl = page.url();
    await page.goto(nextEntryUrl, { waitUntil: 'domcontentloaded', timeout: 60000 }).catch(async () => {
      await page.goto(nextEntryUrl, { waitUntil: 'load', timeout: 60000 });
    });
    await sleep(stepDelayMs);
    const advanced = await buyNowAdvanced(page, beforeUrl);
    if (advanced) {
      return {
        clicked: true,
        advanced: true,
        strategy: 'agent3-next-entry-url-replay',
        selector: null,
        source_url: replaySourceUrl,
        replay_target_url: nextEntryUrl,
      };
    }
  }
  return { clicked: false, advanced: false, reason: 'buy_now action did not open next step' };
}

async function clickAgreeAllAction(page) {
  for (let attempt = 0; attempt < 3; attempt += 1) {
    const result = await page.evaluate(() => {
      function isVisible(element) {
        const style = window.getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return style.visibility !== 'hidden'
          && style.display !== 'none'
          && rect.width > 0
          && rect.height > 0;
      }
      function textOf(element) {
        return String(element.innerText || element.textContent || element.getAttribute?.('value') || '').replace(/\s+/g, ' ').trim();
      }
      const candidates = Array.from(document.querySelectorAll('button, [role="button"], a, .btn, .button, input[type="button"], input[type="submit"]'))
        .filter(isVisible)
        .map(element => ({
          element,
          text: textOf(element),
          inDialog: Boolean(element.closest('[role="dialog"], .modal, .dialog, .van-dialog, .ant-modal, .el-dialog, .hz-dialog, .layui-layer')),
        }))
        .filter(item => /已阅读并同意|已阅读并确认|同意并继续|进入下一步/.test(item.text))
        .sort((left, right) => Number(right.inDialog) - Number(left.inDialog));
      const target = candidates[0]?.element;
      if (!target) return { clicked: false, reason: 'agree button not found' };
      target.scrollIntoView({ block: 'center', inline: 'nearest' });
      target.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
      target.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
      target.click();
      return { clicked: true, text: textOf(target), strategy: 'dom-click' };
    }).catch(error => ({ clicked: false, reason: String(error) }));
    if (result.clicked) {
      await sleep(stepDelayMs);
      const stillVisible = await page.locator('button, [role="button"], a, .btn, .button, input[type="button"], input[type="submit"]').filter({ hasText: /已阅读并同意|已阅读并确认|同意并继续|进入下一步/ }).first().isVisible({ timeout: 800 }).catch(() => false);
      if (!stillVisible || attempt === 2) return result;
    }
    await sleep(600);
  }
  return { clicked: false, reason: 'agree button click did not dismiss dialog' };
}

async function healthNoticeVisible(page) {
  const count = await page.getByText(/确认无以上问题|无以上问题|无上述问题/).count().catch(() => 0);
  if (count > 0) return true;
  const valueCount = await page.locator('input[type="button"], input[type="submit"], input[type="checkbox"], input[type="radio"]').evaluateAll(elements => {
    return elements.filter(element => /确认无以上问题|无以上问题|无上述问题/.test(String(element.getAttribute('value') || ''))).length;
  }).catch(() => 0);
  return valueCount > 0;
}

async function clickHealthNoticeNoIssueCta(page) {
  const exactNoIssuePattern = /^(?:确认无以上问题|确认无上述问题|无以上问题|无上述问题)$/;
  const primaryLocator = page.locator(
    'a.am-button, button, [role="button"], input.insure-label, .insure-label, input[type="button"], input[type="submit"], .am-button, .btn, .button'
  );
  const text = await clickLastVisibleByPattern(page, primaryLocator, exactNoIssuePattern);
  if (text) {
    return { clicked: true, strategy: 'playwright-health-notice-no-issue', text };
  }

  const token = `agent4-health-notice-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const marked = await page.evaluate(({ token }) => {
    function isVisible(element) {
      const style = window.getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.visibility !== 'hidden'
        && style.display !== 'none'
        && rect.width > 0
        && rect.height > 0;
    }
    function compactText(value) {
      return String(value || '').replace(/\s+/g, '').trim();
    }
    function textOf(element) {
      return String(element.innerText || element.textContent || element.getAttribute?.('value') || '').replace(/\s+/g, ' ').trim();
    }
    const exactNoIssue = /^(?:确认无以上问题|确认无上述问题|无以上问题|无上述问题)$/;
    const candidates = Array.from(document.querySelectorAll('button, a, [role="button"], .am-button, .btn, .button, input[type="button"], input[type="submit"], div, span'))
      .filter(isVisible)
      .map(element => ({ element, text: textOf(element) }))
      .filter(item => exactNoIssue.test(compactText(item.text)));
    const chosen = candidates[candidates.length - 1];
    if (!chosen) return null;
    const target = chosen.element.closest('a, button, [role="button"], .am-button, .btn, .button, input[type="button"], input[type="submit"], [onclick]') || chosen.element;
    target.setAttribute('data-agent4-health-notice', token);
    return { text: chosen.text, tag: String(target.tagName || '').toLowerCase() };
  }, { token }).catch(() => null);
  if (!marked) return { clicked: false, strategy: 'playwright-health-notice-no-issue', reason: 'no exact no-issue CTA visible' };

  const target = page.locator(`[data-agent4-health-notice="${token}"]`).first();
  if (!(await target.isVisible({ timeout: 1000 }).catch(() => false))) {
    return { clicked: false, strategy: 'playwright-health-notice-no-issue', reason: 'marked no-issue CTA not visible', marked };
  }
  await target.scrollIntoViewIfNeeded().catch(() => undefined);
  await target.click({ timeout: 10000, noWaitAfter: true }).catch(async () => {
    await target.click({ timeout: 10000, noWaitAfter: true, force: true });
  });
  await sleep(500);
  return { clicked: true, strategy: 'playwright-health-notice-no-issue-marker', text: marked.text, marked };
}

async function clickHealthNoticeSafeOptionLikeAgent3(page) {
  const selected = await page.evaluate(async () => {
    function textOf(el) {
      return String(el.innerText || el.textContent || el.value || el.getAttribute('aria-label') || '').trim();
    }
    function visible(el) {
      return !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
    }
    function sleep(ms) {
      return new Promise(resolve => setTimeout(resolve, ms));
    }
    function scrollHealthNoticeToBottom() {
      const doc = document.documentElement;
      const body = document.body;
      const height = Math.max(
        doc?.scrollHeight || 0,
        body?.scrollHeight || 0,
        window.innerHeight || 0
      );
      window.scrollTo(0, height);
      if (doc) doc.scrollTop = height;
      if (body) body.scrollTop = height;
      window.dispatchEvent(new Event('scroll', { bubbles: true }));
    }
    scrollHealthNoticeToBottom();
    await sleep(900);
    scrollHealthNoticeToBottom();
    await sleep(250);
    const candidates = Array.from(document.querySelectorAll('a, input.insure-label, .insure-label, button, [role=button]'))
      .map((el, index) => ({ el, index, text: textOf(el) }))
      .filter(item => /确认无以上问题|无以上问题|无上述问题/.test(item.text))
      .sort((left, right) => {
        const leftExact = left.text.includes('确认无以上问题') ? 1 : 0;
        const rightExact = right.text.includes('确认无以上问题') ? 1 : 0;
        return rightExact - leftExact || right.index - left.index;
      });
    const chosen = candidates.find(item => visible(item.el)) || candidates[0];
    const target = chosen && chosen.el;
    if (!target) return { clicked: false, strategy: 'agent3-health-notice-safe-option', reason: 'no safe health notice option found' };
    if (!String(target.className || '').includes('active')) {
      target.scrollIntoView({ block: 'center', inline: 'center' });
      if (target.matches('input, button')) {
        try { target.click(); } catch (_) {}
      } else {
        for (const type of ['pointerdown', 'mousedown', 'mouseup', 'click']) {
          target.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
        }
      }
      target.dispatchEvent(new Event('input', { bubbles: true }));
      target.dispatchEvent(new Event('change', { bubbles: true }));
    }
    return {
      clicked: true,
      text: textOf(target).slice(0, 80),
      selector: target.id ? `#${CSS.escape(target.id)}` : `${target.tagName.toLowerCase()} >> text=${JSON.stringify(textOf(target))}`,
      strategy: 'agent3-health-notice-safe-option',
    };
  }).catch(error => ({ clicked: false, strategy: 'agent3-health-notice-safe-option', reason: String(error?.message || error) }));
  if (selected.clicked) {
    await page.waitForLoadState('domcontentloaded', { timeout: 15000 }).catch(() => undefined);
    await sleep(stepDelayMs);
  }
  return selected;
}

async function answerHealthNotice(page, action, nextEntryUrl = '') {
  if (!(await healthNoticeVisible(page))) return null;
  const bodyText = await page.locator('body').innerText({ timeout: 2000 }).catch(() => '');
  if (
    nextEntryUrl
    && /\/product\/insure(?:\?|$)/i.test(nextEntryUrl)
    && (
      isLikelyWholePageTransientError(bodyText, page.url())
      || /Cannot destructure property .* of ['"]?(?:undefined|null)['"]?/i.test(bodyText)
    )
  ) {
    const before = page.url();
    await page.goto(nextEntryUrl, { waitUntil: 'domcontentloaded', timeout: 60000 }).catch(async () => {
      await page.goto(nextEntryUrl, { waitUntil: 'load', timeout: 60000 });
    });
    await sleep(stepDelayMs);
    return {
      strategy: 'health-notice-agent3-next-entry-url-replay',
      clicked_count: 1,
      clicked: [{
        group: 'health-notice-no-issue',
        question_text: '被保险人健康告知',
        text: 'Agent3 replayed next product/insure URL after transient health notice page',
        tag: 'agent3-url',
        selector: nextEntryUrl,
        choice_rule: 'health_notice_no_issue',
        click_strategy: 'health-notice-agent3-next-entry-url-replay',
      }],
      source_url: before,
      target_url: page.url(),
      replay_url: nextEntryUrl,
      body_excerpt: bodyText.slice(0, 300),
    };
  }
  const agent3Click = await clickHealthNoticeSafeOptionLikeAgent3(page);
  if (agent3Click.clicked && !(await healthNoticeVisible(page))) {
    return {
      strategy: 'health_notice_no_issue',
      clicked_count: 1,
      clicked: [{
        group: 'health-notice-no-issue',
        question_text: '被保险人健康告知',
        text: String(agent3Click.text || '').slice(0, 160),
        tag: 'agent3-js',
        selector: agent3Click.selector || 'agent3-health-notice-safe-option',
        choice_rule: 'health_notice_no_issue',
        click_strategy: agent3Click.strategy,
      }],
      agent3_health_notice_click: agent3Click,
    };
  }
  let playwrightClick = await clickHealthNoticeNoIssueCta(page);
  if (playwrightClick.clicked) {
    await page.waitForLoadState('domcontentloaded', { timeout: 15000 }).catch(() => undefined);
    await sleep(stepDelayMs);
    if (!(await healthNoticeVisible(page))) {
      return {
        strategy: 'health_notice_no_issue',
        clicked_count: 1,
        clicked: [{
          group: 'health-notice-no-issue',
          question_text: '被保险人健康告知',
          text: String(playwrightClick.text || '').slice(0, 160),
          tag: 'playwright',
          selector: 'exact-no-issue-cta',
          choice_rule: 'health_notice_no_issue',
          click_strategy: playwrightClick.strategy,
        }],
        agent3_health_notice_click: agent3Click,
        playwright_health_notice_click: playwrightClick,
      };
    }
  }
  const result = await page.evaluate(() => {
    const clicked = [];

    function isVisible(element) {
      const style = window.getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.visibility !== 'hidden'
        && style.display !== 'none'
        && rect.width > 0
        && rect.height > 0;
    }

    function normalizedText(element) {
      if (!element) return '';
      const text = String(element.innerText || element.textContent || '').replace(/\s+/g, ' ').trim();
      if (text) return text;
      return String(element.getAttribute?.('value') || '').replace(/\s+/g, ' ').trim();
    }

    function compactText(value) {
      return String(value || '').replace(/\s+/g, '').trim();
    }

    const HEALTH_NOTICE_ISSUE_PATTERN = /(?:有部分问题|部分问题|存在问题|以上任一|任一问题)/;
    const HEALTH_NOTICE_NO_ISSUE_PATTERN = /^(?:确认无以上问题|确认无上述问题|无以上问题|无上述问题)$/;

    function isExactHealthNoticeNoIssueText(text) {
      const compact = compactText(text).replace(/[>＞》]+$/g, '');
      if (HEALTH_NOTICE_ISSUE_PATTERN.test(compact)) return false;
      return HEALTH_NOTICE_NO_ISSUE_PATTERN.test(compact);
    }

    function healthNoticePageVisible() {
      return Array.from(document.querySelectorAll('input, button, a, label, [role="button"], .insure-label, [onclick], li, [class*="btn"], [class*="button"], [class*="option"], body'))
        .some(element => isVisible(element) && (HEALTH_NOTICE_ISSUE_PATTERN.test(compactText(normalizedText(element))) || HEALTH_NOTICE_NO_ISSUE_PATTERN.test(compactText(normalizedText(element)))));
    }

    function healthNoticeNoIssueCandidates() {
      const selector = 'input, button, a, label, [role="button"], .insure-label, [role="radio"], [role="checkbox"], [onclick], li, [class*="btn"], [class*="button"], [class*="option"]';
      return Array.from(document.querySelectorAll(selector))
        .map((element, index) => ({ element, index, text: normalizedText(element) }))
        .filter(item => isVisible(item.element) && isExactHealthNoticeNoIssueText(item.text));
    }

    function cssSelector(element) {
      if (element.id) return `#${CSS.escape(element.id)}`;
      const className = String(element.className || '').split(/\s+/).filter(Boolean)[0];
      if (className) return `${element.tagName.toLowerCase()}.${CSS.escape(className)}`;
      return element.tagName.toLowerCase();
    }

    function clickLikeUser(element, preferSelf = false) {
      const target = preferSelf || element.matches('input, button, a, label, [role="button"], .insure-label')
        ? element
        : (element.querySelector('input.insure-label, input[type="button"], input[type="submit"], button, [role="button"], label, .insure-label') || element);
      target.scrollIntoView({ block: 'center', inline: 'center' });
      if (target.matches('input, button')) {
        try { target.click(); } catch (_) {}
      } else {
        for (const type of ['pointerdown', 'mousedown', 'mouseup', 'click']) {
          target.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
        }
      }
      target.dispatchEvent(new Event('input', { bubbles: true }));
      target.dispatchEvent(new Event('change', { bubbles: true }));
      return target;
    }

    const candidates = healthNoticeNoIssueCandidates();
    if (!candidates.length) {
      if (healthNoticePageVisible()) {
        return {
          strategy: 'health_notice_no_issue',
          clicked_count: 0,
          clicked,
          error: 'Health notice no-issue control not found; refusing to submit issue state',
        };
      }
      return null;
    }
    const chosen = candidates.find(item => compactText(item.text) === '确认无以上问题') || candidates[0];
    const clickedElement = clickLikeUser(chosen.element, true);
    clicked.push({
      group: 'health-notice-no-issue',
      question_text: '被保险人健康告知',
      text: chosen.text.slice(0, 160),
      tag: String(clickedElement.tagName || '').toLowerCase(),
      selector: cssSelector(clickedElement),
      choice_rule: 'health_notice_no_issue',
      click_strategy: 'js-health-notice',
    });
    for (const checkbox of Array.from(document.querySelectorAll('input[type="checkbox"]'))) {
      if (!isVisible(checkbox) || checkbox.checked) continue;
      const checkedElement = clickLikeUser(checkbox);
      clicked.push({
        group: 'health-notice-agreement',
        question_text: '健康告知确认',
        text: normalizedText(checkbox.parentElement || checkbox).slice(0, 160),
        tag: String(checkedElement.tagName || '').toLowerCase(),
        selector: cssSelector(checkedElement),
        choice_rule: 'confirm-agreement',
        click_strategy: 'js-health-notice-checkbox',
      });
    }
    return { strategy: 'health_notice_no_issue', clicked_count: clicked.length, clicked };
  });
  if (result?.error) throw new Error(result.error);
  if (!result || !result.clicked_count) return null;
  await sleep(300);
  if (await healthNoticeVisible(page)) {
    const fallbackClick = await clickHealthNoticeNoIssueCta(page);
    if (fallbackClick.clicked) {
      await page.waitForLoadState('domcontentloaded', { timeout: 15000 }).catch(() => undefined);
      await sleep(stepDelayMs);
    }
    playwrightClick = fallbackClick;
  }
  result.agent3_health_notice_click = agent3Click;
  result.playwright_health_notice_click = playwrightClick;
  return result;
}

function questionnaireControlSelector() {
  return '[data-number].answer-radio, [data-number].answer-multiple-select, .adapt-question-wrap [data-number], .js-adapt-question-content [data-number]';
}

async function waitForQuestionnaireControls(page, timeoutMs = Number(process.env.AGENT4_QUESTIONNAIRE_READY_TIMEOUT_MS || 30000)) {
  const questionnaireSelector = questionnaireControlSelector();
  const startedAt = Date.now();
  let last = { ready: false, count: 0, url: page.url(), body_excerpt: '' };
  while (Date.now() - startedAt < timeoutMs) {
    const url = page.url();
    const count = await page.locator(questionnaireSelector).count().catch(() => 0);
    const hasHealthNotice = await healthNoticeVisible(page);
    const bodyText = await page.locator('body').innerText({ timeout: 1000 }).catch(() => '');
    const hasQuestionnaireText = /适当性问卷|保险产品适当性问卷|调查问卷|问卷/.test(bodyText);
    const hasAdvanceControl = await page.locator('button, a, [role="button"], .am-button, .adm-button').filter({ hasText: /提交|下一步|确定|继续/ }).count().catch(() => 0);
    if (
      count > 0
      || hasHealthNotice
      || isSuitabilityResultPageUrl(url)
      || (hasQuestionnaireText && hasAdvanceControl > 0)
      || (!isSuitabilityQuestionnairePageUrl(url) && !/\/product\/adapt\/loading/i.test(url))
    ) {
      return {
        ready: true,
        count,
        health_notice: hasHealthNotice,
        questionnaire_text: hasQuestionnaireText,
        advance_control_count: hasAdvanceControl,
        url,
        strategy: 'wait-questionnaire-controls',
      };
    }
    last = {
      ready: false,
      count,
      health_notice: hasHealthNotice,
      questionnaire_text: hasQuestionnaireText,
      advance_control_count: hasAdvanceControl,
      url,
      body_excerpt: String(bodyText || '').replace(/\s+/g, ' ').trim().slice(0, 160),
      strategy: 'wait-questionnaire-controls',
    };
    await page.waitForLoadState('domcontentloaded', { timeout: 3000 }).catch(() => undefined);
    await sleep(800);
  }
  return { ...last, reason: 'suitability questionnaire controls not rendered' };
}

async function clickSuitabilityQuestionnaireSubmit(page) {
  const scoredSubmit = await bestSuitabilityQuestionnaireSubmitLocator(page);
  if (scoredSubmit) {
    const tap = await clickH5SubmitLocator(page, scoredSubmit.locator);
    await page.waitForLoadState('domcontentloaded', { timeout: 15000 }).catch(() => undefined);
    await sleep(stepDelayMs);
    return {
      clicked: true,
      strategy: 'bottom-visible-questionnaire-submit',
      tap,
      attempts: [{ strategy: 'h5-questionnaire-bottom-fixed-score', score: scoredSubmit.score, text: scoredSubmit.text, rect: scoredSubmit.rect }],
      text: scoredSubmit.text,
      url: page.url(),
    };
  }
  const submitPattern = /^(提交|下一步|确定|确认|继续|完成)$/;
  const submitLocator = page
    .locator('button.js-adapt-question-btn, button, a, [role="button"], .am-button, .adm-button, .btn, .button, input[type="button"], input[type="submit"]')
    .filter({ hasText: /提交|下一步|确定|确认|继续|完成/ });
  const text = await clickLastVisibleByPattern(page, submitLocator, submitPattern);
  if (text) {
    await page.waitForLoadState('domcontentloaded', { timeout: 15000 }).catch(() => undefined);
    await sleep(stepDelayMs);
    return { clicked: true, strategy: 'bottom-visible-questionnaire-submit', text, url: page.url() };
  }
  const selectorLocator = page.locator('button.js-adapt-question-btn').first();
  if (await selectorLocator.isVisible({ timeout: 1200 }).catch(() => false)) {
    await selectorLocator.click({ timeout: 10000, noWaitAfter: true }).catch(async () => {
      await selectorLocator.click({ timeout: 10000, noWaitAfter: true, force: true });
    });
    await page.waitForLoadState('domcontentloaded', { timeout: 15000 }).catch(() => undefined);
    await sleep(stepDelayMs);
    return { clicked: true, strategy: 'button.js-adapt-question-btn', text: '', url: page.url() };
  }
  return { clicked: false, strategy: 'questionnaire-submit-not-found', reason: 'visible submit control not found', url: page.url() };
}

async function resyncSuitabilityInlineInputs(page, answerActions = []) {
  const actionValues = [];
  for (const answerAction of answerActions || []) {
    const text = String(answerAction?.text || '');
    const selector = String(answerAction?.selector || '');
    const amountMatch = text.match(/=\s*([0-9]+(?:\.[0-9]+)?)\s*$/);
    if (!amountMatch) continue;
    const indexMatch = selector.match(/inline-input:nth\((\d+)\)/);
    const index = indexMatch ? Number(indexMatch[1]) : actionValues.length;
    actionValues[index] = amountMatch[1];
  }

  const fallbackValues = ['1', '50'];
  const inputs = page.locator('input.inline-input, .adapt-question-wrap input, .js-adapt-question-content input, textarea');
  const count = await inputs.count().catch(() => 0);
  const changed = [];
  const skipped = [];
  for (let index = 0; index < count; index += 1) {
    const locator = inputs.nth(index);
    const state = await locator.evaluate(element => {
      const style = window.getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      const type = String(element.getAttribute('type') || '').toLowerCase();
      return {
        visible: style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0,
        disabled: Boolean(element.disabled),
        read_only: Boolean(element.readOnly),
        type,
        value: String(element.value || ''),
        placeholder: String(element.getAttribute('placeholder') || '').slice(0, 120),
      };
    }).catch(error => ({ visible: false, reason: String(error?.message || error) }));
    if (!state.visible || state.disabled || state.read_only || ['hidden', 'button', 'submit', 'reset', 'file', 'radio', 'checkbox'].includes(state.type)) {
      skipped.push({ index, reason: state.reason || 'input not editable', state });
      continue;
    }
    const current = await locator.inputValue({ timeout: 1000 }).catch(() => state.value || '');
    const value = actionValues[index] || String(current || '').trim() || fallbackValues[index] || fallbackValues[fallbackValues.length - 1];
    if (!value) {
      skipped.push({ index, reason: 'no value available', state });
      continue;
    }
    await locator.scrollIntoViewIfNeeded({ timeout: 5000 }).catch(() => undefined);
    await locator.click({ timeout: 5000, noWaitAfter: true }).catch(async () => {
      await locator.click({ timeout: 5000, noWaitAfter: true, force: true }).catch(() => undefined);
    });
    const fillErrors = [];
    try {
      await locator.fill('');
    } catch (error) {
      fillErrors.push({ step: 'clear', error: String(error?.message || error).slice(0, 160) });
    }
    try {
      await locator.fill(String(value));
    } catch (error) {
      fillErrors.push({ step: 'fill', error: String(error?.message || error).slice(0, 160) });
    }
    try {
      await locator.press('Tab');
    } catch (error) {
      fillErrors.push({ step: 'tab', error: String(error?.message || error).slice(0, 160) });
    }
    const domSync = await locator.evaluate((element, rawValue) => {
      const value = String(rawValue);
      const descriptor = Object.getOwnPropertyDescriptor(element.constructor.prototype, 'value');
      if (descriptor?.set) descriptor.set.call(element, value);
      else element.value = value;
      element.setAttribute('value', value);
      for (const type of ['input', 'change', 'blur']) {
        element.dispatchEvent(new Event(type, { bubbles: true }));
      }
      return { value: String(element.value || '') };
    }, value).catch(error => ({ error: String(error?.message || error).slice(0, 160) }));
    changed.push({
      index,
      value: String(value),
      previous_value: String(current || ''),
      placeholder: state.placeholder,
      click_strategy: 'suitability-inline-input-resync',
      fill_errors: fillErrors,
      dom_sync: domSync,
    });
    await sleep(150);
  }
  const result = {
    strategy: 'suitability-inline-input-resync',
    changed_count: changed.length,
    changed,
    skipped,
    action_values: actionValues.filter(Boolean),
  };
  log({ type: 'suitability-inline-input-resync', message: `resynced ${changed.length} suitability inline inputs`, result, url: page.url() });
  return result;
}

async function submitSuitabilityQuestionnaireViaApiIfNeeded(page, beforeUrl, answerResult, inlineResyncResult, submitClick) {
  if (!isSuitabilityQuestionnairePageUrl(beforeUrl) || page.url() !== beforeUrl) return { attempted: false, completed: false, reason: 'not_silent_suitability_submit', url: page.url() };
  if (!submitClick?.clicked) return { attempted: false, completed: false, reason: 'submit_not_clicked', url: page.url() };
  const result = await page.evaluate(async ({ answerResult, inlineResyncResult }) => {
    const parseJson = text => {
      try { return text ? JSON.parse(text) : null; } catch (_) { return null; }
    };
    const isObject = value => value && typeof value === 'object' && !Array.isArray(value);
    const findKey = (root, key, depth = 0, seen = new WeakSet()) => {
      if (!root || depth > 10) return undefined;
      if (isObject(root) || Array.isArray(root)) {
        if (seen.has(root)) return undefined;
        seen.add(root);
      }
      if (Array.isArray(root)) {
        for (const item of root) {
          const found = findKey(item, key, depth + 1, seen);
          if (found !== undefined && found !== null && found !== '') return found;
        }
        return undefined;
      }
      if (!isObject(root)) return undefined;
      if (Object.prototype.hasOwnProperty.call(root, key)) return root[key];
      for (const value of Object.values(root)) {
        const found = findKey(value, key, depth + 1, seen);
        if (found !== undefined && found !== null && found !== '') return found;
      }
      return undefined;
    };
    const findQuestionnaireTemplate = (root, depth = 0, seen = new WeakSet()) => {
      if (!root || depth > 10) return null;
      if (isObject(root) || Array.isArray(root)) {
        if (seen.has(root)) return null;
        seen.add(root);
      }
      if (Array.isArray(root)) {
        for (const item of root) {
          const found = findQuestionnaireTemplate(item, depth + 1, seen);
          if (found) return found;
        }
        return null;
      }
      if (!isObject(root)) return null;
      if (
        Array.isArray(root.templates)
        && root.templates.length
        && (String(root.title || '').includes('适当性') || root.templates.some(item => /适当性|保险产品/.test(String(item?.fieldName || ''))))
      ) {
        return root;
      }
      for (const value of Object.values(root)) {
        const found = findQuestionnaireTemplate(value, depth + 1, seen);
        if (found) return found;
      }
      return null;
    };
    const plainStateRoots = [];
    const pushRoot = root => {
      if (root && (typeof root === 'object' || Array.isArray(root))) plainStateRoots.push(root);
    };
    pushRoot(window.__NEXT_DATA__);
    for (const key of Object.keys(window)) {
      if (!/store|redux|state|data|risk|notify|question/i.test(key)) continue;
      try {
        const value = window[key];
        if (value && typeof value.getState === 'function') pushRoot(value.getState());
        else pushRoot(value);
      } catch (_) {}
    }
    for (const storage of [window.localStorage, window.sessionStorage]) {
      if (!storage) continue;
      for (let index = 0; index < storage.length; index += 1) {
        const key = storage.key(index);
        if (!key || !/insure|product|risk|notify|question|adapt|123602|126878/i.test(key)) continue;
        try {
          const raw = storage.getItem(key);
          if (raw && /^[{\[]/.test(raw.trim())) pushRoot(JSON.parse(raw));
        } catch (_) {}
      }
    }
    const current = new URL(location.href);
    const encryptInsureNum = current.searchParams.get('encryptInsureNum') || String(plainStateRoots.map(root => findKey(root, 'encryptInsureNum')).find(Boolean) || '');
    const headers = { 'content-type': 'application/json;charset=UTF-8', accept: 'application/json, text/plain, */*' };
    const postJson = async (url, payload) => {
      const response = await fetch(`${url}${url.includes('?') ? '&' : '?'}md=${Math.random()}`, {
        method: 'POST',
        credentials: 'include',
        headers,
        body: JSON.stringify(payload || {}),
      });
      const text = await response.text();
      return { status: response.status, ok: response.ok, url: response.url, text: text.slice(0, 2000), json: parseJson(text) };
    };
    if (!encryptInsureNum) return { attempted: true, completed: false, strategy: 'risknotify-verify-api-fallback', reason: 'missing_encrypt_insure_num' };
    let questionnaireTemplate = null;
    for (const root of plainStateRoots) {
      questionnaireTemplate = findQuestionnaireTemplate(root);
      if (questionnaireTemplate) break;
    }
    const queryJump = questionnaireTemplate
      ? null
      : await postJson('/api/apps/cps/risknotify/queryRiskNotifyJump', { encryptInsureNum }).catch(error => ({ error: String(error?.message || error) }));
    const queryData = queryJump?.json?.data || queryJump?.json || {};
    questionnaireTemplate = questionnaireTemplate || findQuestionnaireTemplate(queryData);
    const productId = Number(
      findKey(queryData, 'bsicProductId')
        || findKey(queryData, 'basicProductId')
        || findKey(queryData, 'baseProductId')
        || plainStateRoots.map(root => findKey(root, 'bsicProductId') || findKey(root, 'basicProductId') || findKey(root, 'baseProductId')).find(Boolean)
        || findKey(questionnaireTemplate, 'productId')
        || 0
    );
    if (!questionnaireTemplate?.templates?.length) {
      return { attempted: true, completed: false, strategy: 'risknotify-verify-api-fallback', reason: 'missing_questionnaireTemplate', query_jump: queryJump };
    }
    if (!productId) {
      return { attempted: true, completed: false, strategy: 'risknotify-verify-api-fallback', reason: 'missing_productId', query_jump: queryJump };
    }
    const answer = {};
    const multipleAnswerMap = {};
    const answerExtMap = {};
    const selectedByNumber = new Map();
    const amountByIndex = new Map();
    const amountByNumber = new Map();
    for (const item of answerResult?.clicked || []) {
      if (!item?.applied) continue;
      if (item.kind === 'option' && item.question_number && item.desired_letter) {
        const values = selectedByNumber.get(Number(item.question_number)) || [];
        values.push(String(item.desired_letter).toUpperCase());
        selectedByNumber.set(Number(item.question_number), [...new Set(values)]);
      }
      if (item.kind === 'amount' && item.value != null) {
        const index = Number(item.inline_index || 0);
        amountByIndex.set(index, String(item.value));
        amountByNumber.set(index + 3, String(item.value));
      }
    }
    for (const item of inlineResyncResult?.changed || []) {
      const index = Number(item.index || 0);
      amountByIndex.set(index, String(item.value || ''));
      amountByNumber.set(index + 3, String(item.value || ''));
    }
    const answerLetter = content => {
      const match = String(content || '').trim().match(/^([A-H])[\.\u3001．、:：\s]/i);
      return match ? match[1].toUpperCase() : '';
    };
    const variableNameFor = template => {
      const regexJson = String(template.regexJson || '');
      const match = regexJson.match(/"([^"]+)"\s*:/) || String(template.fieldName || '').match(/\$\{([^}]+)\}/);
      return match ? match[1] : (Number(template.numbers) === 4 ? 'annualHouseholdIncome' : 'budget');
    };
    for (const template of questionnaireTemplate.templates || []) {
      const questionNumber = Number(template.numbers || 0);
      if (Number(template.questionType) === 1) {
        const desiredLetters = selectedByNumber.get(questionNumber) || [];
        const selectedIds = [];
        for (const letter of desiredLetters) {
          const option = (template.answerVoList || []).find(item => answerLetter(item.content) === letter);
          if (option?.id != null) selectedIds.push(Number(option.id));
        }
        if (!selectedIds.length) continue;
        if (Number(template.multipleSelect || 0) === 1) {
          multipleAnswerMap[String(template.id)] = selectedIds.join(',');
        } else {
          answer[String(template.id)] = selectedIds[0];
        }
      } else if (Number(template.questionType) === 2) {
        const variableName = variableNameFor(template);
        const value = amountByNumber.get(questionNumber) || amountByIndex.get(Object.keys(answerExtMap).length) || '';
        if (!value) continue;
        answerExtMap[String(template.id)] = {
          type: 1,
          answerId: Number(template.id),
          answer: JSON.stringify({ [variableName]: String(value) }),
          variableName,
          isValid: true,
        };
      }
    }
    const payload = { productId, answer, questionnaireTemplate, encryptInsureNum, multipleAnswerMap, answerExtMap };
    if (!Object.keys(answer).length || !Object.keys(multipleAnswerMap).length || !Object.keys(answerExtMap).length) {
      return {
        attempted: true,
        completed: false,
        strategy: 'risknotify-verify-api-fallback',
        reason: 'incomplete_answer_payload',
        payload_summary: { productId, answer, multipleAnswerMap, answerExtMap, encryptInsureNum, template_id: questionnaireTemplate.id },
        query_jump: queryJump,
      };
    }
    const verify = await fetch(`/api/apps/cps/risknotify/verify?md=${Math.random()}`, {
      method: 'POST',
      credentials: 'include',
      headers,
      body: JSON.stringify(payload),
    }).then(async response => {
      const text = await response.text();
      return { status: response.status, ok: response.ok, url: response.url, text: text.slice(0, 2000), json: parseJson(text) };
    });
    const verifyOk = Boolean(verify.ok && (!verify.text || verify.json == null || verify.json.success === true || String(verify.json.code ?? '') === '0'));
    const parts = current.pathname.split('/').filter(Boolean);
    const basePrefix = parts.length >= 4 ? `/${parts.slice(0, 4).join('/')}` : '/m/apps/cps/demo-channel';
    const resultUrl = `${current.origin}${basePrefix}/product/adapt/result?encryptInsureNum=${encodeURIComponent(encryptInsureNum)}`;
    if (verifyOk) window.location.href = resultUrl;
    return {
      attempted: true,
      completed: verifyOk,
      strategy: 'risknotify-verify-api-fallback',
      verify,
      query_jump: queryJump,
      result_url: resultUrl,
      payload_summary: {
        productId,
        encryptInsureNum,
        answer,
        multipleAnswerMap,
        answerExtMap,
        template_id: questionnaireTemplate.id,
        template_count: questionnaireTemplate.templates.length,
      },
    };
  }, { answerResult, inlineResyncResult }).catch(error => ({
    attempted: true,
    completed: false,
    strategy: 'risknotify-verify-api-fallback',
    error: String(error?.message || error),
  }));
  if (result?.completed) {
    await page.waitForLoadState('domcontentloaded', { timeout: 20000 }).catch(() => undefined);
    await sleep(stepDelayMs);
    result.final_url = page.url();
    result.completed = isSuitabilityResultPageUrl(result.final_url);
  }
  log({ type: 'suitability-api-fallback', message: 'submitted suitability questionnaire with API fallback', result, url: page.url() });
  return result;
}

async function autoAnswerQuestionnaireIfPresent(page, action, step, agent3AnswerActions = []) {
  const questionnaireSelector = questionnaireControlSelector();
  let questionCount = await page.locator(questionnaireSelector).count().catch(() => 0);
  const hasHealthNotice = await healthNoticeVisible(page);
  const hasSuitabilityPage = isSuitabilityQuestionnairePageUrl(page.url()) || (action?.planned_from_node_id === 'NODE-suitability' && questionCount > 0);
  if (!questionCount && !hasHealthNotice && !hasSuitabilityPage) return null;

  const before = page.url();
  let readiness = null;
  if (hasSuitabilityPage && !questionCount && !hasHealthNotice) {
    readiness = await waitForQuestionnaireControls(page);
    questionCount = await page.locator(questionnaireSelector).count().catch(() => 0);
    if (isSuitabilityResultPageUrl(page.url())) {
      const record = {
        strategy: 'auto-followup-questionnaire',
        step,
        source_url: before,
        target_url: page.url(),
        submitted: false,
        submit_strategy: 'none',
        wait_result: readiness,
        answer_result: null,
      };
      log({ type: 'auto-followup-questionnaire', message: `step ${step}: suitability result reached before answering questionnaire`, auto_questionnaire_result: record });
      return record;
    }
    if (!readiness.ready && !questionCount) {
      const record = {
        strategy: 'auto-followup-questionnaire',
        step,
        source_url: before,
        target_url: page.url(),
        submitted: false,
        submit_strategy: 'none',
        wait_result: readiness,
        answer_result: {
          strategy: 'wait-questionnaire-controls',
          clicked_count: 0,
          clicked: [],
          reason: 'suitability questionnaire controls not rendered',
        },
      };
      log({ type: 'auto-followup-questionnaire-deferred', message: `step ${step}: suitability questionnaire controls not rendered`, auto_questionnaire_result: record });
      return record;
    }
  }
  const agent3AnswerResult = hasSuitabilityPage && agent3AnswerActions.length
    ? await applyAgent3SuitabilityAnswers(page, agent3AnswerActions)
    : null;
  const answerResult = hasHealthNotice
    ? await answerHealthNotice(page, action)
    : (agent3AnswerResult?.clicked_count
      ? agent3AnswerResult
      : await answerQuestionnaire(page, { ...action, answer_strategy: 'business_questionnaire_rule' }));
  if (hasHealthNotice && !answerResult?.clicked_count) {
    throw new Error('Health notice no-issue control not found; refusing to submit issue state');
  }
  const inlineResyncResult = hasSuitabilityPage
    ? await resyncSuitabilityInlineInputs(page, agent3AnswerActions)
    : null;
  await page.waitForLoadState('domcontentloaded', { timeout: 15000 }).catch(() => undefined);
  await sleep(stepDelayMs);

  let submitStrategy = 'none';
  const submitClick = await clickSuitabilityQuestionnaireSubmit(page);
  if (submitClick.clicked) {
    submitStrategy = submitClick.strategy;
  }
  const warningResult = await acceptQuestionnaireWarningIfPresent(page);
  const apiFallbackResult = await submitSuitabilityQuestionnaireViaApiIfNeeded(page, before, answerResult, inlineResyncResult, submitClick);
  if (apiFallbackResult?.completed) {
    submitStrategy = `${submitStrategy}+risknotify-verify-api-fallback`;
  }
  const record = {
    strategy: 'auto-followup-questionnaire',
    step,
    source_url: before,
    target_url: page.url(),
    submitted: submitStrategy !== 'none',
    submit_strategy: submitStrategy,
    submit_click: submitClick,
    warning_result: warningResult,
    api_fallback_result: apiFallbackResult,
    wait_result: readiness,
    answer_result: answerResult,
    inline_resync_result: inlineResyncResult,
  };
  log({ type: 'auto-followup-questionnaire', message: `step ${step}: answered follow-up questionnaire`, auto_questionnaire_result: record });
  return record;
}

function cssAttrValue(value) {
  return String(value || '').replace(/\\/g, '\\\\').replace(/"/g, '\\"');
}

function actionTextAliases(action) {
  const text = String(action.text || '').trim();
  const compact = compactActionText(text);
  const strategy = String(action.click_strategy || '').toLowerCase();
  const aliases = [];
  if (text) aliases.push(text);
  if (String(action.action_key || '') === 'action.submit' && /提交订单|提交投保/.test(text)) {
    aliases.push('提交投保单', '提交投保', '提交订单');
  }
  if (strategy.includes('sms-code-request') || /^(获取验证码|发送认证短信|发送验证码|发送短信|获取认证短信)$/.test(compact)) {
    aliases.push('发送认证短信', '发送验证码', '发送短信', '获取验证码', '获取认证短信');
  }
  return [...new Set(aliases.filter(Boolean))];
}

function locatorsForAction(page, action) {
  const selector = String(action.selector || '').trim();
  const tag = String(action.tag || 'a').trim() || 'a';
  const candidates = [];
  if (selector) candidates.push(page.locator(selector).first());
  for (const text of actionTextAliases(action)) {
    let hasText = text;
    let isRegexText = false;
    if (text.includes('|') || text.includes('\\s') || text.startsWith('^')) {
      try {
        hasText = new RegExp(text);
        isRegexText = true;
      } catch {
        hasText = text;
      }
    }
    if (tag && tag !== 'questionnaire') candidates.push(page.locator(tag).filter({ hasText }).first());
    candidates.push(page.locator('button, a, [role="button"], label, .btn, .button, input[type="button"], input[type="submit"]').filter({ hasText }).first());
    if (!isRegexText) {
      const valueText = cssAttrValue(text);
      candidates.push(page.locator(`input[type="button"][value*="${valueText}"], input[type="submit"][value*="${valueText}"]`).first());
    }
    candidates.push(page.getByText(hasText).first());
  }
  if (!candidates.length) candidates.push(page.locator(tag).first());
  return candidates;
}

async function locatorForAction(page, action) {
  const candidates = locatorsForAction(page, action);
  for (const candidate of candidates) {
    if (await candidate.isVisible({ timeout: 1500 }).catch(() => false)) {
      return candidate;
    }
  }
  return candidates[0];
}

function isAutoWaitAction(action) {
  return action.action_key === 'action.auto_wait_for_next_node' || action.click_strategy === 'auto_wait_for_next_node';
}

function actionClickStrategy(action) {
  return String(action?.click_strategy || '').toLowerCase();
}

function isSyntheticMinimalDataAction(action) {
  const strategy = actionClickStrategy(action);
  const actionKey = String(action?.action_key || '').toLowerCase();
  if (actionKey === 'action.answer_health_notice' || strategy.includes('js-health-notice-safe-option')) return false;
  const actionType = String(action?.action_type || '').toLowerCase();
  const selector = String(action?.selector || '').toLowerCase();
  return strategy.includes('js-minimal-data')
    || actionType === 'minimal_data'
    || selector === 'policy-tool-bank-mock';
}

function isTerminalBoundaryAction(action) {
  const strategy = actionClickStrategy(action);
  return [
    'account-session-boundary',
    'backend-unavailable-boundary',
    'form-validation-boundary',
    'frontend-runtime-boundary',
  ].includes(strategy);
}

function terminalBoundaryMessage(action) {
  const text = String(action?.text || action?.blocked_reason || action?.message || '').trim();
  const strategy = actionClickStrategy(action);
  return text || `Agent4 stopped at execution boundary: ${strategy || 'unknown-boundary'}`;
}

function shouldCoalesceH5FormSyntheticAction(action, currentUrl) {
  if (!isH5ProductInsureFormUrl(currentUrl)) return false;
  const strategy = actionClickStrategy(action);
  if (!strategy.includes('js-minimal-data')) return false;
  const selector = String(action?.selector || '').toLowerCase();
  const combined = `${selector} ${strategy}`;
  if (/modal|dialog|task-modal|go-complete/.test(combined)) return false;
  return true;
}

function isAgent3SuitabilityAnswerAction(action) {
  const text = String(action?.text || '');
  const selector = String(action?.selector || '');
  const fromNode = String(action?.planned_from_node_id || '');
  return fromNode === 'NODE-suitability'
    && (/适当性Q\d+\s*=/.test(text) || /适当性金额\s*=/.test(text) || /label\s*>>\s*text=/.test(selector) || /inline-input:nth\(\d+\)/.test(selector));
}

function agent3SuitabilityAnswerKey(action) {
  const text = String(action?.text || '');
  const selector = String(action?.selector || '');
  const questionMatch = text.match(/适当性Q\s*(\d+)/i);
  if (questionMatch) return `question:${questionMatch[1]}`;
  const inlineMatch = selector.match(/inline-input:nth\((\d+)\)/);
  if (/适当性金额\s*=/.test(text) && inlineMatch) return `amount:${inlineMatch[1]}`;
  if (/适当性金额\s*=/.test(text)) {
    const amountMatch = text.match(/适当性金额\s*=\s*([0-9.]+)/);
    return `amount:${amountMatch ? amountMatch[1] : text}`;
  }
  return `${text}|${selector}`;
}

function agent3SuitabilityAnswerActionsForStep(actions, currentIndex, appliedKeys = new Set()) {
  return (actions || [])
    .slice(0, currentIndex)
    .filter(isAgent3SuitabilityAnswerAction)
    .filter(action => {
      return !appliedKeys.has(agent3SuitabilityAnswerKey(action));
    });
}

function agent3SuitabilityAnswerActionsForSubmitRecovery(actions, currentIndex, appliedKeys = new Set()) {
  const collected = [];
  for (const action of (actions || []).slice(currentIndex + 1)) {
    if (isAgent3SuitabilityAnswerAction(action)) {
      if (!appliedKeys.has(agent3SuitabilityAnswerKey(action))) {
        collected.push(action);
      }
      continue;
    }
    if (isQuestionnaireAdvanceAction(action) && collected.length) break;
    if (collected.length && String(action?.planned_from_node_id || '') !== 'NODE-suitability') break;
  }
  if (collected.length) return collected;
  return agent3SuitabilityAnswerActionsForStep(actions, currentIndex, appliedKeys);
}

function isSuitabilityQuestionnairePageUrl(value) {
  return /\/product\/adapt(?:\?|$)/.test(String(value || ''));
}

function isSuitabilityResultPageUrl(value) {
  return /\/product\/adapt\/result(?:\?|$)/.test(String(value || ''));
}

function isSuitabilityMismatchResultPage(url, text) {
  return isSuitabilityResultPageUrl(url)
    && /结论\s*[:：]?\s*不匹配|本次拟投保产品适当性匹配情况[\s\S]*不匹配/.test(String(text || ''))
    && /重新评估/.test(String(text || ''));
}

function isQuestionnaireAdvanceAction(action) {
  const text = compactActionText(action?.text);
  const strategy = actionClickStrategy(action);
  const selector = String(action?.selector || '').toLowerCase();
  return action?.planned_from_node_id === 'NODE-suitability'
    || strategy.includes('questionnaire')
    || (/下一步|下一页|提交|确定|确认|继续|完成/.test(text) && /adapt|question|questionnaire|js-adapt|nth-of-type/.test(`${selector} ${strategy}`));
}

function isH5ProductInsureFormUrl(value) {
  return /\/product\/insure$/i.test(urlPathname(value));
}

function isStaleSuitabilityAdvanceOnH5InsureForm(action, currentUrl) {
  if (!isH5ProductInsureFormUrl(currentUrl)) return false;
  if (String(action?.planned_from_node_id || '') !== 'NODE-suitability') return false;
  if (String(action?.action_key || '').toLowerCase() !== 'action.click') return false;
  if (String(action?.action_type || '').toLowerCase() === 'minimal_data') return false;
  const sourcePath = urlPathname(action?.source_url || '');
  if (!/\/product\/adapt(?:\/result)?$/i.test(sourcePath)) return false;
  const text = compactActionText(action?.text);
  const strategy = actionClickStrategy(action);
  return /涓嬩竴姝|涓嬩竴椤|鎻愪氦|纭畾|纭|缁х画|瀹屾垚|下一步|提交|确认|继续|完成/.test(text)
    || strategy === 'normal';
}

function normalizeStaleSuitabilityAdvanceAsH5Submit(action) {
  const expectedNode = String(action?.planned_from_node_id || action?.expected_next_node_id || action?.planned_to_node_id || 'NODE-suitability');
  return {
    ...action,
    action_key: 'action.submit',
    action_type: 'submit',
    planned_from_node_id: 'NODE-insure-form',
    planned_to_node_id: expectedNode,
    expected_next_node_id: expectedNode,
    click_strategy: `${actionClickStrategy(action) || 'normal'}+stale-suitability-h5-submit`,
    stale_source_action: {
      action_key: action?.action_key,
      action_type: action?.action_type,
      click_strategy: action?.click_strategy,
      source_url: action?.source_url,
      target_url: action?.target_url,
      planned_from_node_id: action?.planned_from_node_id,
      planned_to_node_id: action?.planned_to_node_id,
    },
  };
}

async function clickSuitabilityResultContinueIfPresent(page) {
  if (!isSuitabilityResultPageUrl(page.url())) {
    return { clicked: false, strategy: 'suitability-result-continue', reason: 'not suitability result page' };
  }
  const continuePattern = /^(确认投保|继续投保|继续|去签约|签约|下一步|完成)$/;
  const locator = page
    .locator('button, a, [role="button"], .am-button, .btn, .button, input[type="button"], input[type="submit"]')
    .filter({ hasText: /确认投保|继续投保|继续|去签约|签约|下一步|完成/ });
  const text = await clickLastVisibleByPattern(page, locator, continuePattern);
  if (text) {
    await page.waitForLoadState('domcontentloaded', { timeout: 15000 }).catch(() => undefined);
    await sleep(stepDelayMs);
    return { clicked: true, strategy: 'suitability-result-continue', text, url: page.url() };
  }
  const bodyText = await page.locator('body').innerText({ timeout: 2500 }).catch(() => '');
  return {
    clicked: false,
    strategy: 'suitability-result-continue',
    reason: /重新评估/.test(bodyText) ? 'mismatch result only offers re-evaluate' : 'continue button not found',
    body_excerpt: bodyText.slice(0, 600),
    url: page.url(),
  };
}

async function repairSuitabilityMismatchAndResubmit(page) {
  if (!isSuitabilityResultPageUrl(page.url())) {
    return { repaired: false, reason: 'not suitability result page' };
  }
  const mismatchState = await page.evaluate(() => {
    const body = String(document.body?.innerText || '');
    return {
      mismatch: /结论\s*[:：]?\s*不匹配|本次拟投保产品适当性匹配情况[\s\S]*不匹配/.test(body),
      reason_flags: {
        period: /保障年限与您投保填写的保险期间不一致/.test(body),
        payment: /缴费期间不一致|缴费的最长年期/.test(body),
      },
      body_excerpt: body.slice(0, 600),
    };
  }).catch(error => ({ mismatch: false, reason: String(error?.message || error), reason_flags: {} }));
  if (!mismatchState?.mismatch) {
    return { repaired: false, reason: 'suitability result is not mismatch', mismatch_state: mismatchState };
  }
  const reevaluate = page.locator('button, a, [role="button"], .am-button, .btn, .button').filter({ hasText: /重新评估/ }).last();
  if (!(await reevaluate.isVisible({ timeout: 2000 }).catch(() => false))) {
    return { repaired: false, reason: 're-evaluate button not found', mismatch_state: mismatchState };
  }
  await reevaluate.click({ timeout: 8000, noWaitAfter: true }).catch(async () => {
    await reevaluate.click({ timeout: 8000, noWaitAfter: true, force: true });
  });
  await page.waitForLoadState('domcontentloaded', { timeout: 15000 }).catch(() => undefined);
  await sleep(stepDelayMs);
  const answerResult = await page.evaluate((reasonFlags) => {
    const clicked = [];
    const norm = value => String(value || '').replace(/\s+/g, ' ').trim();
    const visible = element => {
      if (!element) return false;
      const style = window.getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.visibility !== 'hidden'
        && style.display !== 'none'
        && rect.width > 0
        && rect.height > 0;
    };
    function cssSelector(element) {
      if (element.id) return `#${CSS.escape(element.id)}`;
      const dataNumber = element.closest('[data-number]')?.getAttribute('data-number');
      const className = String(element.className || '').split(/\s+/).filter(Boolean)[0];
      if (dataNumber && className) return `[data-number="${CSS.escape(dataNumber)}"] .${CSS.escape(className)}`;
      if (className) return `${element.tagName.toLowerCase()}.${CSS.escape(className)}`;
      return element.tagName.toLowerCase();
    }
    function questionTextForContainer(container) {
      return norm(container.innerText || container.textContent).slice(0, 260);
    }
    function optionItems(container) {
      const raw = Array.from(container.querySelectorAll(
        'input.insure-label, input[type="button"], button, [role="button"], [role="radio"], [role="checkbox"], label, .insure-label, .answer-radio-item, .answer-multiple-select-item, li, span, div'
      ));
      return raw
        .filter(visible)
        .map((element, index) => {
          const text = norm(element.innerText || element.textContent || element.getAttribute?.('value') || '');
          return { element, index, text };
        })
        .filter(item => /^[A-H][\.\u3001:：]/i.test(item.text))
        .filter(item => !Array.from(item.element.children || []).some(child => visible(child) && /^[A-H][\.\u3001:：]/i.test(norm(child.innerText || child.textContent))))
        .sort((left, right) => left.index - right.index);
    }
    function clickLikeUser(element) {
      const target = element.closest?.('label,.answer-radio-item,.answer-multiple-select-item,[role="radio"],[role="checkbox"],li') || element;
      target.scrollIntoView({ block: 'center', inline: 'center' });
      for (const type of ['pointerdown', 'mousedown', 'mouseup', 'click']) {
        try { target.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window })); } catch (_) {}
      }
      try { target.click?.(); } catch (_) {}
      target.dispatchEvent(new Event('input', { bubbles: true }));
      target.dispatchEvent(new Event('change', { bubbles: true }));
      return target;
    }
    const containers = [];
    const seen = new Set();
    for (const container of Array.from(document.querySelectorAll('[data-number], .adapt-question-wrap [data-number], .js-adapt-question-content [data-number]'))) {
      const number = container.getAttribute('data-number') || `${containers.length}`;
      if (seen.has(number)) continue;
      seen.add(number);
      containers.push(container);
    }
    for (const container of containers) {
      const questionText = questionTextForContainer(container);
      const options = optionItems(container);
      if (!options.length) continue;
      const isPeriodQuestion = Boolean(reasonFlags?.period) && /保障年限|保险期间|保障期限|持有期限|保障.*多久|期限/.test(questionText);
      const isPaymentQuestion = Boolean(reasonFlags?.payment) && /缴费|交费|支付保费.*最长|最长年期/.test(questionText);
      if (!isPeriodQuestion && !isPaymentQuestion) continue;
      const chosen = options[options.length - 1];
      const clickedElement = clickLikeUser(chosen.element);
      clicked.push({
        question_text: questionText,
        text: chosen.text,
        selector: cssSelector(clickedElement),
        choice_rule: isPeriodQuestion ? 'mismatch-period-longest-option' : 'mismatch-payment-longest-option',
      });
    }
    return { repaired: clicked.length > 0, reason_flags: reasonFlags, clicked_count: clicked.length, clicked };
  }, mismatchState.reason_flags).catch(error => ({ repaired: false, reason: String(error?.message || error), reason_flags: mismatchState.reason_flags, clicked_count: 0, clicked: [] }));
  if (!answerResult?.clicked_count) {
    return { repaired: false, reason: 'mismatch questions not found', mismatch_state: mismatchState, answer_result: answerResult, url: page.url() };
  }
  await sleep(300);
  let submitStrategy = 'none';
  const submitBySelector = page.locator('button.js-adapt-question-btn').first();
  if (await submitBySelector.isVisible({ timeout: 3000 }).catch(() => false)) {
    await submitBySelector.click({ timeout: 10000, noWaitAfter: true }).catch(async () => {
      await submitBySelector.click({ timeout: 10000, noWaitAfter: true, force: true });
    });
    submitStrategy = 'button.js-adapt-question-btn';
  } else {
    const submitByText = await locatorForAction(page, { text: '提交|下一步|确定|确认', tag: 'button, a, [role="button"]' });
    if (await submitByText.isVisible({ timeout: 3000 }).catch(() => false)) {
      await submitByText.click({ timeout: 10000, noWaitAfter: true }).catch(async () => {
        await submitByText.click({ timeout: 10000, noWaitAfter: true, force: true });
      });
      submitStrategy = 'button-link-text-submit';
    }
  }
  await page.waitForLoadState('domcontentloaded', { timeout: 15000 }).catch(() => undefined);
  await sleep(stepDelayMs);
  const afterBody = await page.locator('body').innerText({ timeout: 3000 }).catch(() => '');
  const result = {
    repaired: true,
    strategy: 'suitability-mismatch-repair',
    reason_flags: mismatchState.reason_flags,
    answer_result: answerResult,
    submit_strategy: submitStrategy,
    submitted: submitStrategy !== 'none',
    url: page.url(),
    still_mismatch: isSuitabilityMismatchResultPage(page.url(), afterBody),
    body_excerpt: afterBody.slice(0, 600),
  };
  log({ type: 'suitability-mismatch-repair', message: 're-evaluated suitability mismatch questions', result, url: page.url() });
  return result;
}

function isAgent3BuyNowStrategy(action) {
  const strategy = actionClickStrategy(action);
  return strategy.includes('mouse-h5-floating-premium-quote') || strategy.includes('mouse-h5-product-footer-insure');
}

function isAgent3HealthNoticeStrategy(action) {
  return actionClickStrategy(action).includes('js-health-notice-safe-option');
}

function isOracleAgreementAction(action) {
  const strategy = actionClickStrategy(action);
  const text = String(action?.text || '');
  const tag = String(action?.tag || '').toLowerCase();
  const selector = String(action?.selector || '').toLowerCase();
  return strategy.includes('oracle-agreement')
    || strategy.includes('agreement-control')
    || ((tag === 'oracle' || selector.includes('agree')) && isAgreementText(text));
}

function urlPathname(value) {
  const rawValue = String(value || '');
  try {
    return new URL(rawValue).pathname;
  } catch {
    if (rawValue.startsWith('/')) return rawValue.split(/[?#]/)[0];
    return '';
  }
}

function targetPathReached(afterPath, targetPath) {
  if (!targetPath) return false;
  return afterPath === targetPath || afterPath.endsWith(targetPath) || afterPath.includes(targetPath);
}

function isPaymentPageUrl(value, text = '') {
  const path = urlPathname(value) || String(value || '');
  if (/\/pay(?:\/|$)/.test(path)) return true;
  return /支付|付款|银行卡签约|签约/.test(String(text || '')) && /确认|验证码|短信|支付|签约/.test(String(text || ''));
}

function isStaleNodeExpectationPastPaymentBoundary(expectedNodeId, afterPath, matchedNodes) {
  const staleNodes = new Set([
    'NODE-suitability',
    'NODE-health-notice',
    'NODE-insure-form',
    'NODE-underwriting',
    'NODE-risk-control',
  ]);
  if (!staleNodes.has(String(expectedNodeId || ''))) return false;
  if (Array.isArray(matchedNodes) && matchedNodes.includes('NODE-payment')) return true;
  return isPaymentPageUrl(afterPath);
}

function assertAgent4ActionProgress(action, beforeUrl, afterUrl, matchedNodes, clickStrategy) {
  if (action.skip_if_absent) return;
  const expectedNodeId = action.expected_next_node_id || action.planned_to_node_id;
  const expectedTargetUrl = String(action.target_url || '');
  const beforePath = urlPathname(beforeUrl);
  const afterPath = urlPathname(afterUrl);
  const targetPath = urlPathname(expectedTargetUrl);
  if (expectedTargetUrl && afterPath === beforePath && (!targetPath || afterPath !== targetPath)) {
    throw new Error(`Agent4 action did not advance toward expected target_url; strategy=${action.click_strategy || clickStrategy || ''}; selector=${action.selector || ''}; before=${beforeUrl}; after=${afterUrl}; expected=${expectedTargetUrl}`);
  }
  if (expectedNodeId && Array.isArray(matchedNodes) && !matchedNodes.includes(expectedNodeId)) {
    if (targetPath !== beforePath && targetPathReached(afterPath, targetPath)) return;
    if (isStaleNodeExpectationPastPaymentBoundary(expectedNodeId, afterPath, matchedNodes)) return;
    if (isStaleHealthNoticeExpectationOnAuthPage(action, expectedNodeId, afterPath, matchedNodes)) return;
    throw new Error(`Agent4 action did not reach expected node ${expectedNodeId}; strategy=${action.click_strategy || clickStrategy || ''}; selector=${action.selector || ''}; before=${beforeUrl}; after=${afterUrl}; matched_nodes=${JSON.stringify(matchedNodes)}`);
  }
}

function isStaleHealthNoticeExpectationOnAuthPage(action, expectedNodeId, afterPath, matchedNodes) {
  if (expectedNodeId !== 'NODE-health-notice') return false;
  if (!Array.isArray(matchedNodes) || !matchedNodes.includes('NODE-risk-control')) return false;
  if (/\/authentication(?:\/detail)?(?:\?|$)/.test(afterPath)) return true;
  const targetPath = urlPathname(action.target_url || '');
  return /\/authentication(?:\/detail)?(?:\?|$)/.test(targetPath);
}

function signalMatches(text, signal) {
  const value = String(signal || '').trim();
  if (!value) return false;
  if (text.includes(value)) return true;
  try {
    return new RegExp(value).test(text);
  } catch {
    return false;
  }
}

function nodeRecords(payload, nodeId) {
  return (payload.page_element_plan || []).filter(record => {
    if (record.node_id === nodeId) return true;
    return (record.matched_node_ids || []).includes(nodeId);
  });
}

function signalsForNode(payload, nodeId) {
  const signals = [];
  for (const record of nodeRecords(payload, nodeId)) {
    signals.push(...(record.entry_signals || []));
  }
  return [...new Set(signals.filter(Boolean))];
}

function urlPatternsForNode(payload, nodeId) {
  const patterns = [];
  for (const record of nodeRecords(payload, nodeId)) {
    patterns.push(...(record.url_patterns || []));
    if (record.actual_url) patterns.push(record.actual_url);
  }
  return [...new Set(patterns.filter(Boolean))];
}

async function matchNode(page, payload, nodeId) {
  const text = await page.locator('body').innerText({ timeout: 3000 }).catch(() => '');
  const url = page.url();
  const signals = signalsForNode(payload, nodeId);
  if (isSuitabilityQuestionnairePageUrl(url)) return String(nodeId) === 'NODE-suitability';
  if (isSuitabilityResultPageUrl(url)) return String(nodeId) === 'NODE-suitability';
  if (isSuitabilityMismatchResultPage(url, text)) return String(nodeId) === 'NODE-suitability';
  if (String(nodeId).includes('payment') && isPaymentPageUrl(url, text)) return true;
  if (String(nodeId).includes('policy-result') && /\/product\/detail/.test(url)) return false;
  if (String(nodeId).includes('health-notice') && /健康告知|确认无以上问题|无以上问题|无上述问题/.test(text)) return true;
  if (String(nodeId).includes('risk-control') && /\/authentication\/detail(?:\?|$)/.test(url)) {
    return /身份认证|投保意愿认证|验证码|证件照片|提交认证|发送认证短信/.test(text);
  }
  const matchedSignals = signals.filter(signal => signalMatches(text, signal));
  const minSignals = String(nodeId).includes('policy-result') ? 2 : 1;
  if (matchedSignals.length >= minSignals) return true;
  const patterns = urlPatternsForNode(payload, nodeId);
  return patterns.some(pattern => url.includes(String(pattern).replace(/\*/g, '')));
}

async function matchNodes(page, payload) {
  const requiredNodes = ((payload.completion_rule || {}).required_nodes || []).filter(Boolean);
  const matched = [];
  for (const nodeId of requiredNodes) {
    if (await matchNode(page, payload, nodeId)) matched.push(nodeId);
  }
  return matched;
}

function orderGenerationBoundaryReached(payload, matchedNodes, finalUrl, executedActions = []) {
  if (!((payload.completion_rule || {}).order_generation_boundary)) return false;
  if (Array.isArray(matchedNodes) && matchedNodes.includes('NODE-payment')) return true;
  if (isPaymentPageUrl(finalUrl)) return true;
  return (executedActions || []).some(action => (
    action && (
      action.action_key === 'action.bank_sign_boundary'
      || action.auth_handoff_result?.completed
      || (
        action.submit_suitability_recovery?.recovered
        && action.submit_api_result?.suitability_task
        && Array.isArray(action.matched_nodes)
        && action.matched_nodes.includes('NODE-suitability')
      )
    )
  ));
}

function setupNetworkLogging(page) {
  const interesting = /\/api\/apps\/cps\/(?:risknotify|product\/insure|insure|pay\/bank|product\/trial|product\/adapt|product\/task|cert|pay|sign)/i;
  page.on('request', request => {
    const url = request.url();
    if (!interesting.test(url)) return;
    let postData = '';
    try { postData = request.postData() || ''; } catch (_) {}
    log({
      type: 'network-request',
      method: request.method(),
      url,
      resource_type: request.resourceType(),
      post_data: postData.slice(0, 1200),
    });
  });
  page.on('response', async response => {
    const url = response.url();
    if (!interesting.test(url)) return;
    let body = '';
    const status = response.status();
    if (status >= 400 || /risknotify|product\/insure|insure\/submit|pay\/bank|card\/valid/.test(url)) {
      try { body = (await response.text()).slice(0, 1200); } catch (_) {}
    }
    const responseRecord = {
      type: 'network-response',
      status,
      url,
      body_excerpt: body,
    };
    globalThis.__agent4NetworkResponses.push(responseRecord);
    if (globalThis.__agent4NetworkResponses.length > 80) globalThis.__agent4NetworkResponses.shift();
    log(responseRecord);
  });
}

(async () => {
  fs.mkdirSync(outDir, { recursive: true });
  fs.writeFileSync(actionLogPath, '', 'utf8');
  const startedAt = Date.now();
  let browser;
  const result = {
    returncode: 0,
    passed: 0,
    failed: 0,
    errors: [],
    browser_actions_path: actionLogPath,
    visible_browser: true,
    screenshots,
    node_matches: [],
    executed_actions: [],
  };
  try {
    const launchOptions = {
      headless: false,
      slowMo: Number(process.env.AGENT4_CHROME_SLOWMO || 500),
      channel: process.env.PLAYWRIGHT_CHROMIUM_CHANNEL || 'chrome',
    };
    if (process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH) {
      launchOptions.executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH;
      delete launchOptions.channel;
    }
    log({
      type: 'launch',
      message: 'launch visible Chromium',
      scenario_id: payload.scenario_id,
      path_id: payload.path_id,
      browser_channel: launchOptions.channel || (launchOptions.executablePath ? 'custom-executable' : 'playwright-chromium'),
    });
    browser = await chromium.launch(launchOptions);
    const context = await browser.newContext(contextOptionsForPayload(payload));
    const page = await context.newPage();
    setupAttachmentPageCleanup(context, page);
    setupNetworkLogging(page);
    await page.bringToFront();

    log({ type: 'goto', message: payload.entry_url, url: payload.entry_url });
    await page.goto(payload.entry_url, { waitUntil: 'domcontentloaded', timeout: 60000 });
    log({ type: 'page', message: await page.title(), url: page.url() });
    await sleep(stepDelayMs);
    await captureScreenshot(page, 0, 'initial-page');

    const initialDismissed = await dismissBlockingOverlays(page);
    if (initialDismissed.length) log({ type: 'overlay', message: 'dismissed initial overlay', dismissed_overlays: initialDismissed });
    if (initialDismissed.length) await captureScreenshot(page, 0, 'initial-overlay-dismissed');
    const initialMatchedNodes = await matchNodes(page, payload);
    result.node_matches.push({ step: 0, label: 'initial-page', url: page.url(), matched_nodes: initialMatchedNodes });
    const requiredNodes = ((payload.completion_rule || {}).required_nodes || []).filter(Boolean);
    const entryNode = requiredNodes[0];
    if (entryNode && !initialMatchedNodes.includes(entryNode)) {
      const bodyExcerpt = await page.locator('body').innerText({ timeout: 3000 }).catch(() => '');
      throw new Error(`Entry page did not match planned node ${entryNode}; matched_nodes=${JSON.stringify(initialMatchedNodes)}; url=${page.url()}; body_excerpt=${bodyExcerpt.slice(0, 200)}`);
    }

    const actions = payload.real_actions || [];
    let bankRecognitionManualSelectionPendingSubmit = false;
    const appliedAgent3SuitabilityAnswerKeys = new Set();
    for (let index = 0; index < actions.length; index += 1) {
      let action = actions[index];
      const before = page.url();
      log({
        type: 'action-start',
        message: `step ${index + 1}: ${action.text || action.selector || action.tag}`,
        step: index + 1,
        selector: action.selector,
        text: action.text,
        source_url: before,
        planned_from_node_id: action.planned_from_node_id,
        planned_to_node_id: action.planned_to_node_id,
      });
      await recoverTransientPageError(page);
      if (isTerminalBoundaryAction(action)) {
        const matchedNodes = await matchNodes(page, payload);
        const boundaryMessage = terminalBoundaryMessage(action);
        result.node_matches.push({ step: index + 1, label: `step-${index + 1}-terminal-boundary`, url: page.url(), matched_nodes: matchedNodes });
        result.executed_actions.push({
          step: index + 1,
          action_key: action.action_key || 'action.execution_boundary',
          text: action.text,
          selector: action.selector,
          planned_from_node_id: action.planned_from_node_id,
          planned_to_node_id: action.planned_to_node_id,
          matched_nodes: matchedNodes,
          source_url: before,
          target_url: page.url(),
          click_strategy: actionClickStrategy(action),
          terminal_boundary: true,
          boundary_message: boundaryMessage,
        });
        result.returncode = 1;
        result.failed = 1;
        result.passed = 0;
        result.errors = [boundaryMessage];
        result.execution_boundary = actionClickStrategy(action);
        result.target_node_status = 'blocked';
        log({
          type: 'terminal-boundary',
          message: boundaryMessage,
          step: index + 1,
          click_strategy: actionClickStrategy(action),
          source_url: before,
          target_url: page.url(),
          matched_nodes: matchedNodes,
        });
        await captureScreenshot(page, index + 1, `step-${index + 1}-terminal-boundary`);
        break;
      }
      if (isStaleSuitabilityAdvanceOnH5InsureForm(action, page.url())) {
        const originalAction = action;
        action = normalizeStaleSuitabilityAdvanceAsH5Submit(action);
        log({
          type: 'stale-suitability-h5-submit',
          message: `step ${index + 1}: normalized stale suitability advance as H5 insure submit`,
          step: index + 1,
          source_url: before,
          target_url: page.url(),
          original_action: originalAction,
          normalized_action: action,
        });
      }
      await applyMockData(page, action.planned_from_node_id);
      if (isSyntheticMinimalDataAction(action)) {
        if (isAgent3SuitabilityAnswerAction(action) && isSuitabilityQuestionnairePageUrl(page.url())) {
          const suitabilityAnswerResult = await applyAgent3SuitabilityAnswer(page, action);
          if (
            suitabilityAnswerResult?.applied
            && (suitabilityAnswerResult.kind !== 'option' || suitabilityAnswerResult.selected_after || suitabilityAnswerResult.already_selected)
          ) {
            appliedAgent3SuitabilityAnswerKeys.add(agent3SuitabilityAnswerKey(action));
          }
          const matchedNodes = await matchNodes(page, payload);
          result.node_matches.push({ step: index + 1, label: `step-${index + 1}-agent3-suitability`, url: page.url(), matched_nodes: matchedNodes });
          result.executed_actions.push({
            step: index + 1,
            action_key: action.action_key || 'action.synthetic_minimal_data',
            text: action.text,
            selector: action.selector,
            planned_from_node_id: action.planned_from_node_id,
            planned_to_node_id: action.planned_to_node_id,
            matched_nodes: matchedNodes,
            source_url: before,
            target_url: page.url(),
            click_strategy: 'agent3-suitability-answer',
            synthetic: true,
            suitability_answer_result: suitabilityAnswerResult,
          });
          log({
            type: 'action-end',
            message: `step ${index + 1}: applied Agent3 suitability answer`,
            step: index + 1,
            click_strategy: 'agent3-suitability-answer',
            source_url: before,
            target_url: page.url(),
            matched_nodes: matchedNodes,
            suitability_answer_result: suitabilityAnswerResult,
          });
          await captureScreenshot(page, index + 1, `step-${index + 1}-agent3-suitability`);
          continue;
        }
        if (isSuitabilityQuestionnairePageUrl(page.url()) && !isAgent3SuitabilityAnswerAction(action)) {
          const matchedNodes = await matchNodes(page, payload);
          result.node_matches.push({ step: index + 1, label: `step-${index + 1}-skip-stale-synthetic-suitability`, url: page.url(), matched_nodes: matchedNodes });
          result.executed_actions.push({
            step: index + 1,
            action_key: action.action_key || 'action.synthetic_minimal_data',
            text: action.text,
            selector: action.selector,
            planned_from_node_id: action.planned_from_node_id,
            planned_to_node_id: action.planned_to_node_id,
            matched_nodes: matchedNodes,
            source_url: before,
            target_url: page.url(),
            click_strategy: 'skip-stale-synthetic-on-suitability',
            synthetic: true,
            skipped: true,
          });
          log({
            type: 'action-end',
            message: `step ${index + 1}: skipped stale synthetic data on suitability page`,
            step: index + 1,
            click_strategy: 'skip-stale-synthetic-on-suitability',
            source_url: before,
            target_url: page.url(),
            matched_nodes: matchedNodes,
          });
          await captureScreenshot(page, index + 1, `step-${index + 1}-skip-stale-synthetic-suitability`);
          continue;
        }
        if (isSuitabilityResultPageUrl(page.url())) {
          let suitabilityResultClick = await clickSuitabilityResultContinueIfPresent(page);
          let suitabilityRepair = null;
          if (!suitabilityResultClick.clicked) {
            suitabilityRepair = await repairSuitabilityMismatchAndResubmit(page);
            if (suitabilityRepair?.submitted) {
              suitabilityResultClick = await clickSuitabilityResultContinueIfPresent(page);
              suitabilityResultClick.repair = suitabilityRepair;
            }
          }
          const matchedNodes = await matchNodes(page, payload);
          const clickStrategy = suitabilityResultClick.clicked
            ? 'suitability-result-continue'
            : 'skip-stale-synthetic-on-suitability-result';
          result.node_matches.push({ step: index + 1, label: `step-${index + 1}-${clickStrategy}`, url: page.url(), matched_nodes: matchedNodes });
          result.executed_actions.push({
            step: index + 1,
            action_key: action.action_key || 'action.synthetic_minimal_data',
            text: action.text,
            selector: action.selector,
            planned_from_node_id: action.planned_from_node_id,
            planned_to_node_id: action.planned_to_node_id,
            matched_nodes: matchedNodes,
            source_url: before,
            target_url: page.url(),
            click_strategy: clickStrategy,
            synthetic: true,
            skipped: !suitabilityResultClick.clicked,
            suitability_result: suitabilityResultClick,
            suitability_repair: suitabilityRepair,
          });
          log({
            type: 'action-end',
            message: suitabilityResultClick.clicked
              ? `step ${index + 1}: clicked suitability result continue`
              : `step ${index + 1}: skipped stale synthetic data on suitability result page`,
            step: index + 1,
            click_strategy: clickStrategy,
            source_url: before,
            target_url: page.url(),
            matched_nodes: matchedNodes,
            suitability_result: suitabilityResultClick,
            suitability_repair: suitabilityRepair,
          });
          await captureScreenshot(page, index + 1, `step-${index + 1}-${clickStrategy}`);
          if (suitabilityResultClick.clicked) {
            const resultResumeIndex = findTaskModalResumeIndex(actions, index, matchedNodes);
            if (resultResumeIndex > index + 1) {
              index = resultResumeIndex - 1;
            }
          }
          continue;
        }
        let taskModalResult = await clickTaskModalGoCompleteIfPresent(page);
        let taskModalMatchedNodes = null;
        let bankAgent3Flow = null;
        let resumeIndex = -1;
        if (!taskModalResult.clicked) {
          if (action.planned_to_node_id) {
            await replayAgent3InsureFormUrlIfNeeded(page, action);
            if (action.planned_to_node_id === 'NODE-insure-form' && !isSuitabilityQuestionnairePageUrl(page.url())) {
              await waitForMockDataNodeReady(page, action.planned_to_node_id, 60000);
            }
            await applyMockData(page, action.planned_to_node_id);
          }
          await syncVisibleH5InsureInputs(page);
          await syncVisibleH5InsureModelState(page);
          if (shouldCoalesceH5FormSyntheticAction(action, page.url())) {
            const matchedNodes = await matchNodes(page, payload);
            result.node_matches.push({ step: index + 1, label: `step-${index + 1}-coalesced-h5-form-synthetic-data`, url: page.url(), matched_nodes: matchedNodes });
            result.executed_actions.push({
              step: index + 1,
              action_key: action.action_key || 'action.synthetic_minimal_data',
              text: action.text,
              selector: action.selector,
              planned_from_node_id: action.planned_from_node_id,
              planned_to_node_id: action.planned_to_node_id,
              matched_nodes: matchedNodes,
              source_url: before,
              target_url: page.url(),
              click_strategy: 'coalesced-h5-form-synthetic-data',
              synthetic: true,
              coalesced: true,
            });
            log({
              type: 'action-end',
              message: `step ${index + 1}: coalesced h5 form synthetic data`,
              step: index + 1,
              click_strategy: 'coalesced-h5-form-synthetic-data',
              source_url: before,
              target_url: page.url(),
              matched_nodes: matchedNodes,
            });
            continue;
          }
          const syntheticDiagnostics = await inspectPostSubmitDiagnostics(page);
          const recognitionBlocker = hasBankRecognitionBlocker(syntheticDiagnostics) || bankRecognitionManualSelectionPendingSubmit;
          if (hasBankRecognitionBlocker(syntheticDiagnostics)) bankRecognitionManualSelectionPendingSubmit = true;
          const bankRelatedSynthetic = isBankRelatedSyntheticDataAction(action);
          let bankRecognitionSubmitResult = null;
          if (recognitionBlocker || bankRelatedSynthetic) {
            bankAgent3Flow = await repairVisibleBankPickerLikeAgent3(page, action, 'synthetic-bank-action', { skip_account_refill: recognitionBlocker });
            if (recognitionBlocker && isBankReadyToSubmitAfterManualSelection(bankAgent3Flow?.diagnostics)) {
              bankRecognitionSubmitResult = await clickH5SubmitButton(page, action);
              log({ type: 'bank-recognition-submit-click', message: 'submitted immediately after manual bank selection cleared bank error', click_result: bankRecognitionSubmitResult, url: page.url() });
              await page.waitForLoadState('domcontentloaded', { timeout: 15000 }).catch(() => undefined);
              await sleep(stepDelayMs);
              bankRecognitionManualSelectionPendingSubmit = false;
            } else if (recognitionBlocker) {
              bankRecognitionManualSelectionPendingSubmit = true;
            }
          }
          taskModalResult = await clickTaskModalGoCompleteIfPresent(page);
          if (taskModalResult.clicked) bankRecognitionManualSelectionPendingSubmit = false;
          if (bankRecognitionSubmitResult) {
            const postBankSubmitDiagnostics = await inspectPostSubmitDiagnostics(page);
            log({ type: 'bank-recognition-post-submit-diagnostics', message: 'state after bank recognition immediate submit', diagnostics: postBankSubmitDiagnostics, url: page.url() });
          }
        }
        if (taskModalResult.clicked) {
          await recoverTransientPageError(page);
          taskModalMatchedNodes = await matchNodes(page, payload);
          resumeIndex = findTaskModalResumeIndex(actions, index, taskModalMatchedNodes);
          if (resumeIndex > index) {
            log({
              type: 'task-modal-resume',
              message: `resume generated actions from step ${resumeIndex + 1} after task modal`,
              from_step: index + 1,
              resume_step: resumeIndex + 1,
              matched_nodes: taskModalMatchedNodes,
              url: page.url(),
            });
          }
        }
        const matchedNodes = taskModalMatchedNodes || await matchNodes(page, payload);
        const syntheticClickStrategy = taskModalResult.clicked ? 'js-minimal-data+task-modal-go-complete' : 'js-minimal-data';
        result.node_matches.push({ step: index + 1, label: `step-${index + 1}-synthetic-data`, url: page.url(), matched_nodes: matchedNodes });
        result.executed_actions.push({
          step: index + 1,
          action_key: action.action_key || 'action.synthetic_minimal_data',
          text: action.text,
          selector: action.selector,
          planned_from_node_id: action.planned_from_node_id,
          planned_to_node_id: action.planned_to_node_id,
          matched_nodes: matchedNodes,
          source_url: before,
          target_url: page.url(),
          click_strategy: syntheticClickStrategy,
          synthetic: true,
          bank_agent3_flow: bankAgent3Flow,
          task_modal_result: taskModalResult,
        });
        log({
          type: 'action-end',
          message: `step ${index + 1}: applied synthetic minimal data`,
          step: index + 1,
          click_strategy: syntheticClickStrategy,
          source_url: before,
          target_url: page.url(),
          matched_nodes: matchedNodes,
        });
        await captureScreenshot(page, index + 1, `step-${index + 1}-synthetic-data`);
        if (resumeIndex > index) index = resumeIndex - 1;
        continue;
      }
      if ((action.action_key === 'action.submit' || isH5SubmitAction(action)) && action.planned_from_node_id === 'NODE-insure-form') {
        const insureFormReady = await waitForMockDataNodeReady(page, 'NODE-insure-form', 60000);
        if (!insureFormReady) {
          throw new Error('insure form did not become ready before submit');
        }
        await applyMockData(page, action.planned_from_node_id);
        await syncVisibleH5InsureInputs(page);
        await syncVisibleH5InsureModelState(page);
        await collapseSelfInsuredDetails(page);
        await repairVisibleBankPickerLikeAgent3(page, action, 'submit-preflight');
        const agreementCheckedCount = await forceConfirmAgreementCheckboxes(page);
        log({ type: 'h5-submit-preflight', message: 'ready to click submit without full-page repair scroll', agreement_checked_count: agreementCheckedCount, url: page.url() });
        await syncVisibleH5InsureInputs(page);
        await syncVisibleH5InsureModelState(page);
        await collapseSelfInsuredDetails(page);
        await fillVisibleBankAccountThenBlur(page);
        await clearVisibleBankAccountError(page);
        const formState = await inspectInsureFormState(page);
        log({ type: 'insure-form-state-before-submit', message: 'insure form state before submit', form_state: formState, url: page.url() });
      }
      if (isAgent3SubmitApiAction(action)) {
        const submitApiResult = action.submit_api_result || { attempted: true, order_generated: false, reason: 'missing_agent3_submit_api_result' };
        let submitSuitabilityRecovery = { recovered: false, reason: 'agent3 submit api did not require suitability task' };
        if (submitApiResult?.suitability_task || String(submitApiResult?.code || '') === '40015') {
          submitSuitabilityRecovery = await recoverSuitabilityTaskAfterSubmitIfNeeded(
            page,
            action,
            actions,
            index,
            {
              bodyTail: submitApiResult?.body_excerpt || submitApiResult?.msg || '',
              dialogs: [submitApiResult?.msg || ''].filter(Boolean),
              toasts: [],
              errors: [],
            },
            appliedAgent3SuitabilityAnswerKeys,
            submitApiResult
          );
        }
        await recoverTransientPageError(page);
        const matchedNodes = await matchNodes(page, payload);
        result.node_matches.push({ step: index + 1, label: `step-${index + 1}-agent3-submit-api`, url: page.url(), matched_nodes: matchedNodes });
        result.executed_actions.push({
          step: index + 1,
          action_key: action.action_key || 'action.submit_api',
          text: action.text,
          selector: action.selector,
          planned_from_node_id: action.planned_from_node_id,
          planned_to_node_id: action.planned_to_node_id,
          matched_nodes: matchedNodes,
          source_url: before,
          target_url: page.url(),
          click_strategy: 'agent3-submit-api-replay',
          submit_api_result: submitApiResult,
          submit_suitability_recovery: submitSuitabilityRecovery,
        });
        log({
          type: 'agent3-submit-api-replay',
          message: `step ${index + 1}: replayed Agent3 submit API result without DOM locator`,
          step: index + 1,
          source_url: before,
          target_url: page.url(),
          matched_nodes: matchedNodes,
          submit_api_result: submitApiResult,
          submit_suitability_recovery: submitSuitabilityRecovery,
        });
        await captureScreenshot(page, index + 1, `step-${index + 1}-agent3-submit-api`);
        if (submitSuitabilityRecovery?.recovered) {
          const resumeIndex = findTaskModalResumeIndex(actions, index, matchedNodes);
          if (resumeIndex > index) index = resumeIndex - 1;
        }
        continue;
      }
      if (action.action_key === 'action.submit' || isH5SubmitAction(action)) {
        const clickResult = await clickH5SubmitButton(page, action);
        log({ type: 'h5-submit-click', message: 'clicked h5 submit action', click_result: clickResult, url: page.url() });
        if (!clickResult.clicked && !action.skip_if_absent) {
          throw new Error(clickResult.reason || 'submit action did not click');
        }
        await page.waitForLoadState('domcontentloaded', { timeout: 15000 }).catch(() => undefined);
        await sleep(stepDelayMs);
        const submitDismissed = await dismissBlockingOverlays(page);
        let overlayRetryClickResult = null;
        if (submitDismissed.length) {
          log({ type: 'overlay', message: 'dismissed blocking overlay after h5 submit click', dismissed_overlays: submitDismissed });
          overlayRetryClickResult = await clickH5SubmitButton(page, action);
          log({ type: 'h5-submit-overlay-retry-click', message: 'retried h5 submit after overlay dismiss', click_result: overlayRetryClickResult, url: page.url() });
          await page.waitForLoadState('domcontentloaded', { timeout: 15000 }).catch(() => undefined);
          await sleep(stepDelayMs);
        }
        await waitForSuitabilitySubmitBlocker(page);
        let postSubmitDiagnostics = await inspectPostSubmitDiagnostics(page);
        log({ type: 'post-submit-diagnostics', message: 'state after h5 submit click', diagnostics: postSubmitDiagnostics, url: page.url() });
        const submitRecovery = await recoverH5SubmitBlockerAndRetry(page, action, postSubmitDiagnostics);
        if (submitRecovery?.diagnostics) postSubmitDiagnostics = submitRecovery.diagnostics;
        const preSuitabilitySubmitApiResult = await submitInsureViaBrowserApiIfNeeded(
          page,
          action,
          before,
          postSubmitDiagnostics,
          { allow_suitability_blocker: true }
        );
        if (preSuitabilitySubmitApiResult?.attempted) {
          log({
            type: 'submit-api-fallback',
            message: 'attempted same-origin insure submit before suitability recovery',
            phase: 'pre-suitability',
            step: index + 1,
            source_url: before,
            target_url: page.url(),
            submit_api_result: preSuitabilitySubmitApiResult,
          });
        }
        let taskModalResult = await clickTaskModalGoCompleteIfPresent(page);
        const submitSuitabilityRecovery = await recoverSuitabilityTaskAfterSubmitIfNeeded(
          page,
          action,
          actions,
          index,
          postSubmitDiagnostics,
          appliedAgent3SuitabilityAnswerKeys,
          preSuitabilitySubmitApiResult
        );
        if (submitSuitabilityRecovery?.task_modal?.clicked) {
          taskModalResult = submitSuitabilityRecovery.task_modal;
        }
        await recoverTransientPageError(page);
        await applyMockData(page, action.planned_to_node_id);
        const matchedNodes = await matchNodes(page, payload);
        let submitApiResult = preSuitabilitySubmitApiResult;
        if (!submitApiResult?.attempted) {
          submitApiResult = await submitInsureViaBrowserApiIfNeeded(page, action, before, postSubmitDiagnostics);
        }
        if (submitApiResult?.attempted && submitApiResult !== preSuitabilitySubmitApiResult) {
          log({
            type: 'submit-api-fallback',
            message: 'attempted same-origin insure submit after ui submit produced no request',
            phase: 'post-progress-check',
            step: index + 1,
            source_url: before,
            target_url: page.url(),
            submit_api_result: submitApiResult,
          });
        }
        const authHandoffResult = submitApiResult?.task_handoff
          ? await completePostSubmitIdentityHandoff(page, submitApiResult)
          : { attempted: false, completed: false };
        if (authHandoffResult?.attempted) {
          log({
            type: 'post-submit-auth-handoff',
            message: 'attempted identity verification handoff after submit api task response',
            step: index + 1,
            source_url: before,
            target_url: page.url(),
            auth_handoff_result: authHandoffResult,
            submit_api_result: submitApiResult,
          });
        }
        if (submitApiResult?.direct_order || authHandoffResult?.completed) {
          const apiMatchedNodes = await matchNodes(page, payload);
          result.node_matches.push({ step: index + 1, label: `step-${index + 1}-browser-api-submit`, url: page.url(), matched_nodes: apiMatchedNodes });
          result.executed_actions.push({
            step: index + 1,
            action_key: action.action_key || 'action.submit',
            text: action.text,
            selector: action.selector,
            planned_from_node_id: action.planned_from_node_id,
            planned_to_node_id: action.planned_to_node_id,
            matched_nodes: apiMatchedNodes,
            source_url: before,
            target_url: page.url(),
            click_strategy: authHandoffResult?.completed ? 'post-submit-auth-handoff' : 'ui-click+browser-api-submit',
            submit_action_result: clickResult,
            submit_api_result: submitApiResult,
            auth_handoff_result: authHandoffResult,
            submit_overlay_dismissed: submitDismissed,
            submit_overlay_retry_result: overlayRetryClickResult,
            submit_recovery: submitRecovery,
            submit_suitability_recovery: submitSuitabilityRecovery,
            task_modal_result: taskModalResult,
          });
          log({
            type: 'submit-api-recovery',
            message: `step ${index + 1}: submit api confirmed order generation boundary`,
            step: index + 1,
            click_strategy: authHandoffResult?.completed ? 'post-submit-auth-handoff' : 'ui-click+browser-api-submit',
            source_url: before,
            target_url: page.url(),
            matched_nodes: apiMatchedNodes,
            submit_action_result: clickResult,
            submit_api_result: submitApiResult,
            auth_handoff_result: authHandoffResult,
            submit_overlay_dismissed: submitDismissed,
            submit_overlay_retry_result: overlayRetryClickResult,
            submit_recovery: submitRecovery,
            submit_suitability_recovery: submitSuitabilityRecovery,
            task_modal_result: taskModalResult,
          });
          await captureScreenshot(page, index + 1, `step-${index + 1}-browser-api-submit`);
          continue;
        }
        if (taskModalResult.clicked || submitSuitabilityRecovery?.recovered) {
          const resumeIndex = findTaskModalResumeIndex(actions, index, matchedNodes);
          const clickStrategy = submitSuitabilityRecovery?.recovered
            ? 'submit-suitability-task-recovery'
            : 'submit-task-modal-go-complete';
          result.node_matches.push({ step: index + 1, label: `step-${index + 1}-after`, url: page.url(), matched_nodes: matchedNodes });
          result.executed_actions.push({
            step: index + 1,
            action_key: action.action_key || 'action.submit',
            text: action.text,
            selector: action.selector,
            planned_from_node_id: action.planned_from_node_id,
            planned_to_node_id: action.planned_to_node_id,
            matched_nodes: matchedNodes,
            source_url: before,
            target_url: page.url(),
            click_strategy: clickStrategy,
            submit_action_result: clickResult,
            submit_overlay_dismissed: submitDismissed,
            submit_overlay_retry_result: overlayRetryClickResult,
            submit_recovery: submitRecovery,
            submit_api_result: submitApiResult,
            submit_suitability_recovery: submitSuitabilityRecovery,
            task_modal_result: taskModalResult,
          });
          log({
            type: 'submit-task-recovery',
            message: `step ${index + 1}: recovered submit task detour`,
            step: index + 1,
            click_strategy: clickStrategy,
            source_url: before,
            target_url: page.url(),
            matched_nodes: matchedNodes,
            submit_action_result: clickResult,
            submit_overlay_dismissed: submitDismissed,
            submit_overlay_retry_result: overlayRetryClickResult,
            submit_recovery: submitRecovery,
            submit_api_result: submitApiResult,
            submit_suitability_recovery: submitSuitabilityRecovery,
            task_modal_result: taskModalResult,
          });
          await captureScreenshot(page, index + 1, `step-${index + 1}-after`);
          if (resumeIndex > index) index = resumeIndex - 1;
          continue;
        }
        assertAgent4ActionProgress(action, before, page.url(), matchedNodes, clickResult.strategy);
        result.node_matches.push({ step: index + 1, label: `step-${index + 1}-after`, url: page.url(), matched_nodes: matchedNodes });
        result.executed_actions.push({
          step: index + 1,
          action_key: action.action_key || 'action.submit',
          text: action.text,
          selector: action.selector,
          planned_from_node_id: action.planned_from_node_id,
          planned_to_node_id: action.planned_to_node_id,
          matched_nodes: matchedNodes,
          source_url: before,
          target_url: page.url(),
          click_strategy: clickResult.strategy,
          submit_action_result: clickResult,
          submit_overlay_dismissed: submitDismissed,
          submit_overlay_retry_result: overlayRetryClickResult,
          submit_recovery: submitRecovery,
          submit_api_result: submitApiResult,
          submit_suitability_recovery: submitSuitabilityRecovery,
          task_modal_result: taskModalResult,
        });
        log({
          type: 'submit-action-click',
          message: `step ${index + 1}: submitted form at ${page.url()}`,
          step: index + 1,
          click_strategy: clickResult.strategy,
          source_url: before,
          target_url: page.url(),
          matched_nodes: matchedNodes,
          submit_action_result: clickResult,
          submit_overlay_dismissed: submitDismissed,
          submit_overlay_retry_result: overlayRetryClickResult,
          submit_recovery: submitRecovery,
          submit_api_result: submitApiResult,
          submit_suitability_recovery: submitSuitabilityRecovery,
          task_modal_result: taskModalResult,
        });
        await captureScreenshot(page, index + 1, `step-${index + 1}-after`);
        continue;
      }
      if (action.action_key === 'action.buy_now' || isAgent3BuyNowStrategy(action)) {
        const nextEntryUrl = nextAgent3EntryUrlAfterBuyNow(actions, index);
        const clickResult = await clickBuyNowAction(page, action, before, nextEntryUrl);
        if ((!clickResult.clicked || !clickResult.advanced) && !action.skip_if_absent) {
          throw new Error(clickResult.reason || 'buy_now action did not open next step');
        }
        await applyMockData(page, action.planned_to_node_id);
        const matchedNodes = await matchNodes(page, payload);
        result.node_matches.push({ step: index + 1, label: `step-${index + 1}-${clickResult.clicked ? 'after' : 'skipped'}`, url: page.url(), matched_nodes: matchedNodes });
        result.executed_actions.push({
          step: index + 1,
          action_key: action.action_key || 'action.buy_now',
          text: action.text,
          selector: action.selector,
          planned_from_node_id: action.planned_from_node_id,
          planned_to_node_id: action.planned_to_node_id,
          matched_nodes: matchedNodes,
          source_url: before,
          target_url: page.url(),
          click_strategy: clickResult.clicked ? 'buy-now-action-click' : 'optional-action-skip',
          skipped: !clickResult.clicked,
          buy_now_action_result: clickResult,
        });
        log({
          type: clickResult.clicked ? 'buy-now-action-click' : 'optional-action-skip',
          message: `step ${index + 1}: ${clickResult.clicked ? 'clicked buy-now action' : 'optional buy-now action not present'}`,
          step: index + 1,
          source_url: before,
          target_url: page.url(),
          matched_nodes: matchedNodes,
          buy_now_action_result: clickResult,
        });
        await captureScreenshot(page, index + 1, `step-${index + 1}-${clickResult.clicked ? 'after' : 'skipped'}`);
        continue;
      }
      if (action.action_key === 'action.agree_all' || isOracleAgreementAction(action)) {
        const agreementResult = await ensureAllAgreementsConfirmed(page);
        const clickResult = agreementResult.checked || agreementResult.checked_count
          ? { clicked: true, strategy: 'agreement-confirmed', agreement_result: agreementResult }
          : await clickAgreeAllAction(page);
        if (!clickResult.clicked && !action.skip_if_absent) {
          throw new Error(`Agreement action did not click: ${clickResult.reason || 'unknown'}`);
        }
        await page.waitForLoadState('domcontentloaded', { timeout: 15000 }).catch(() => undefined);
        await sleep(stepDelayMs);
        const agreementAttachmentRestore = await restoreMainPageFromAttachment(page, before, `step-${index + 1}-agreement-action`);
        await closeAttachmentPages(context, page, `step-${index + 1}-agreement-action`);
        await applyMockData(page, action.planned_to_node_id);
        if (action.planned_to_node_id === 'NODE-insure-form') {
          await syncVisibleH5InsureInputs(page);
          await syncVisibleH5InsureModelState(page);
          await clearVisibleBankAccountError(page);
        }
        let matchedNodes = await matchNodes(page, payload);
        matchedNodes = await enrichMatchedNodesWithReadyState(page, matchedNodes, action.expected_next_node_id || action.planned_to_node_id);
        assertAgent4ActionProgress(action, before, page.url(), matchedNodes, clickResult.strategy);
        result.node_matches.push({ step: index + 1, label: `step-${index + 1}-${clickResult.clicked ? 'after' : 'skipped'}`, url: page.url(), matched_nodes: matchedNodes });
        result.executed_actions.push({
          step: index + 1,
          action_key: action.action_key,
          text: action.text,
          selector: action.selector,
          planned_from_node_id: action.planned_from_node_id,
          planned_to_node_id: action.planned_to_node_id,
          matched_nodes: matchedNodes,
          source_url: before,
          target_url: page.url(),
          click_strategy: clickResult.clicked ? 'agreement-action-click' : 'optional-action-skip',
          skipped: !clickResult.clicked,
          agreement_action_result: clickResult,
          agreement_result: agreementResult,
          attachment_restore_result: agreementAttachmentRestore,
        });
        log({
          type: clickResult.clicked ? 'agreement-action-click' : 'optional-action-skip',
          message: `step ${index + 1}: ${clickResult.clicked ? 'clicked agreement action' : 'optional agreement action not present'}`,
          step: index + 1,
          source_url: before,
          target_url: page.url(),
          matched_nodes: matchedNodes,
          agreement_action_result: clickResult,
          agreement_result: agreementResult,
          attachment_restore_result: agreementAttachmentRestore,
        });
        await captureScreenshot(page, index + 1, `step-${index + 1}-${clickResult.clicked ? 'after' : 'skipped'}`);
        continue;
      }
      if (action.action_key === 'action.answer_questionnaire') {
        let answerResult = null;
        try {
          answerResult = await answerQuestionnaire(page, action);
        } catch (questionnaireError) {
          if (!action.skip_if_absent) throw questionnaireError;
          const matchedNodes = await matchNodes(page, payload);
          result.node_matches.push({ step: index + 1, label: `step-${index + 1}-skipped`, url: page.url(), matched_nodes: matchedNodes });
          result.executed_actions.push({
            step: index + 1,
            action_key: action.action_key,
            text: action.text,
            selector: action.selector,
            planned_from_node_id: action.planned_from_node_id,
            planned_to_node_id: action.planned_to_node_id,
            matched_nodes: matchedNodes,
            source_url: before,
            target_url: page.url(),
            click_strategy: 'optional-action-skip',
            skipped: true,
            skip_reason: 'planned optional questionnaire page not present',
          });
          log({ type: 'optional-action-skip', message: `step ${index + 1}: optional questionnaire not present`, step: index + 1, planned_from_node_id: action.planned_from_node_id });
          await captureScreenshot(page, index + 1, `step-${index + 1}-skipped`);
          continue;
        }
        await page.waitForLoadState('domcontentloaded', { timeout: 15000 }).catch(() => undefined);
        await sleep(stepDelayMs);
        await applyMockData(page, action.planned_to_node_id);
        if (action.planned_to_node_id === 'NODE-insure-form') {
          await syncVisibleH5InsureInputs(page);
          await syncVisibleH5InsureModelState(page);
          await clearVisibleBankAccountError(page);
        }
        const matchedNodes = await matchNodes(page, payload);
        result.node_matches.push({ step: index + 1, label: `step-${index + 1}-after`, url: page.url(), matched_nodes: matchedNodes });
        result.executed_actions.push({
          step: index + 1,
          action_key: action.action_key,
          text: action.text,
          selector: action.selector,
          planned_from_node_id: action.planned_from_node_id,
          planned_to_node_id: action.planned_to_node_id,
          matched_nodes: matchedNodes,
          source_url: before,
          target_url: page.url(),
          click_strategy: 'questionnaire-business-rule',
          answer_result: answerResult,
        });
        log({
          type: 'action-end',
          message: `step ${index + 1}: answered questionnaire at ${page.url()}`,
          step: index + 1,
          click_strategy: 'questionnaire-business-rule',
          source_url: before,
          target_url: page.url(),
          matched_nodes: matchedNodes,
        });
        await captureScreenshot(page, index + 1, `step-${index + 1}-after`);
        continue;
      }
      if (action.action_key === 'action.answer_health_notice' || isAgent3HealthNoticeStrategy(action)) {
        const nextEntryUrl = nextAgent3InsureFormUrlAfterHealthNotice(actions, index);
        const answerResult = await answerHealthNotice(page, action, nextEntryUrl);
        if (!answerResult && action.skip_if_absent) {
          const matchedNodes = await matchNodes(page, payload);
          result.node_matches.push({ step: index + 1, label: `step-${index + 1}-skipped`, url: page.url(), matched_nodes: matchedNodes });
          result.executed_actions.push({
            step: index + 1,
            action_key: action.action_key || 'action.answer_health_notice',
            text: action.text,
            selector: action.selector,
            planned_from_node_id: action.planned_from_node_id,
            planned_to_node_id: action.planned_to_node_id,
            matched_nodes: matchedNodes,
            source_url: before,
            target_url: page.url(),
            click_strategy: 'optional-action-skip',
            skipped: true,
            skip_reason: 'planned optional health notice page not present',
          });
          log({ type: 'optional-action-skip', message: `step ${index + 1}: optional health notice not present`, step: index + 1, planned_from_node_id: action.planned_from_node_id });
          continue;
        }
        if (!answerResult) throw new Error('Health notice action did not find no-issue option');
        await page.waitForLoadState('domcontentloaded', { timeout: 15000 }).catch(() => undefined);
        await sleep(stepDelayMs);
        await applyMockData(page, action.planned_to_node_id);
        if (action.planned_to_node_id === 'NODE-insure-form') {
          await syncVisibleH5InsureInputs(page);
          await syncVisibleH5InsureModelState(page);
          await clearVisibleBankAccountError(page);
        }
        let matchedNodes = await matchNodes(page, payload);
        matchedNodes = await enrichMatchedNodesWithReadyState(page, matchedNodes, action.expected_next_node_id || action.planned_to_node_id);
        assertAgent4ActionProgress({ ...action, skip_if_absent: false }, before, page.url(), matchedNodes, 'health-notice-no-issue');
        result.node_matches.push({ step: index + 1, label: `step-${index + 1}-after`, url: page.url(), matched_nodes: matchedNodes });
        result.executed_actions.push({
          step: index + 1,
          action_key: action.action_key || 'action.answer_health_notice',
          text: action.text,
          selector: action.selector,
          planned_from_node_id: action.planned_from_node_id,
          planned_to_node_id: action.planned_to_node_id,
          matched_nodes: matchedNodes,
          source_url: before,
          target_url: page.url(),
          click_strategy: 'health-notice-no-issue',
          answer_result: answerResult,
        });
        log({
          type: 'action-end',
          message: `step ${index + 1}: answered health notice at ${page.url()}`,
          step: index + 1,
          click_strategy: 'health-notice-no-issue',
          source_url: before,
          target_url: page.url(),
          matched_nodes: matchedNodes,
        });
        await captureScreenshot(page, index + 1, `step-${index + 1}-after`);
        continue;
      }
      if (isAutoWaitAction(action)) {
        const expectedNodeId = action.expected_next_node_id || action.planned_to_node_id;
        let matchedNodes = await matchNodes(page, payload);
        const deadline = Date.now() + Number(process.env.AGENT4_AUTO_WAIT_TIMEOUT_MS || 15000);
        while (expectedNodeId && !matchedNodes.includes(expectedNodeId) && Date.now() < deadline) {
          await sleep(stepDelayMs);
          await page.waitForLoadState('domcontentloaded', { timeout: 5000 }).catch(() => undefined);
          matchedNodes = await matchNodes(page, payload);
        }
        result.node_matches.push({ step: index + 1, label: `step-${index + 1}-auto-wait`, url: page.url(), matched_nodes: matchedNodes });
        result.executed_actions.push({
          step: index + 1,
          action_key: action.action_key,
          text: action.text,
          selector: action.selector,
          planned_from_node_id: action.planned_from_node_id,
          planned_to_node_id: action.planned_to_node_id,
          expected_next_node_id: expectedNodeId,
          matched_nodes: matchedNodes,
          source_url: before,
          target_url: page.url(),
          click_strategy: 'auto_wait_for_next_node',
        });
        log({
          type: expectedNodeId && !matchedNodes.includes(expectedNodeId) ? 'auto-wait-timeout' : 'auto-wait',
          message: `step ${index + 1}: waited for ${expectedNodeId || 'next node'} at ${page.url()}`,
          step: index + 1,
          click_strategy: 'auto_wait_for_next_node',
          source_url: before,
          target_url: page.url(),
          matched_nodes: matchedNodes,
        });
        await captureScreenshot(page, index + 1, `step-${index + 1}-auto-wait`);
        if (expectedNodeId && !matchedNodes.includes(expectedNodeId)) {
          if (action.skip_if_absent) {
            result.executed_actions[result.executed_actions.length - 1].click_strategy = 'optional-action-skip';
            result.executed_actions[result.executed_actions.length - 1].skipped = true;
            result.executed_actions[result.executed_actions.length - 1].skip_reason = 'planned optional auto-wait target not reached';
            log({ type: 'optional-action-skip', message: `step ${index + 1}: optional auto-wait target not reached`, step: index + 1, planned_from_node_id: action.planned_from_node_id, expected_next_node_id: expectedNodeId });
            continue;
          }
          throw new Error(`Auto-wait did not reach expected node ${expectedNodeId}; matched_nodes=${JSON.stringify(matchedNodes)}; url=${page.url()}`);
        }
        continue;
      }
      let locator = await locatorForAction(page, action);
      if (action.skip_if_absent && !(await locator.isVisible({ timeout: 3000 }).catch(() => false))) {
        const matchedNodes = await matchNodes(page, payload);
        result.node_matches.push({ step: index + 1, label: `step-${index + 1}-skipped`, url: page.url(), matched_nodes: matchedNodes });
        result.executed_actions.push({
          step: index + 1,
          action_key: action.action_key,
          text: action.text,
          selector: action.selector,
          planned_from_node_id: action.planned_from_node_id,
          planned_to_node_id: action.planned_to_node_id,
          matched_nodes: matchedNodes,
          source_url: before,
          target_url: page.url(),
          click_strategy: 'optional-action-skip',
          skipped: true,
          skip_reason: 'planned optional page action not visible',
        });
        log({ type: 'optional-action-skip', message: `step ${index + 1}: optional action not visible`, step: index + 1, planned_from_node_id: action.planned_from_node_id, text: action.text, selector: action.selector });
        await captureScreenshot(page, index + 1, `step-${index + 1}-skipped`);
        continue;
      }
      let clickStrategy = 'normal';
      let autoQuestionnaireResult = null;
      if (isSuitabilityResultPageUrl(page.url())) {
        let suitabilityResultClick = await clickSuitabilityResultContinueIfPresent(page);
        let suitabilityRepair = null;
        if (!suitabilityResultClick.clicked) {
          suitabilityRepair = await repairSuitabilityMismatchAndResubmit(page);
          if (suitabilityRepair?.submitted) {
            suitabilityResultClick = await clickSuitabilityResultContinueIfPresent(page);
            suitabilityResultClick.repair = suitabilityRepair;
          }
        }
        clickStrategy = suitabilityResultClick.clicked
          ? 'suitability-result-continue'
          : 'skip-stale-synthetic-on-suitability-result';
        const matchedNodes = await matchNodes(page, payload);
        result.node_matches.push({ step: index + 1, label: `step-${index + 1}-${clickStrategy}`, url: page.url(), matched_nodes: matchedNodes });
        result.executed_actions.push({
          step: index + 1,
          action_key: action.action_key,
          text: action.text,
          selector: action.selector,
          planned_from_node_id: action.planned_from_node_id,
          planned_to_node_id: action.planned_to_node_id,
          matched_nodes: matchedNodes,
          source_url: before,
          target_url: page.url(),
          click_strategy: clickStrategy,
          skipped: !suitabilityResultClick.clicked,
          suitability_result: suitabilityResultClick,
          suitability_repair: suitabilityRepair,
        });
        log({
          type: 'action-end',
          message: suitabilityResultClick.clicked
            ? `step ${index + 1}: clicked suitability result continue`
            : `step ${index + 1}: skipped action on suitability result page`,
          step: index + 1,
          click_strategy: clickStrategy,
          source_url: before,
          target_url: page.url(),
          matched_nodes: matchedNodes,
          suitability_result: suitabilityResultClick,
          suitability_repair: suitabilityRepair,
        });
        await captureScreenshot(page, index + 1, `step-${index + 1}-${clickStrategy}`);
        if (suitabilityResultClick.clicked) {
          const resultResumeIndex = findTaskModalResumeIndex(actions, index, matchedNodes);
          if (resultResumeIndex > index + 1) {
            index = resultResumeIndex - 1;
          }
        }
        continue;
      }
      if (isQuestionnaireAdvanceAction(action)) {
        autoQuestionnaireResult = await autoAnswerQuestionnaireIfPresent(page, action, index + 1, agent3SuitabilityAnswerActionsForStep(actions, index, appliedAgent3SuitabilityAnswerKeys));
        if (autoQuestionnaireResult?.submitted) {
          clickStrategy = 'auto-questionnaire';
          let suitabilityResultClick = null;
          let suitabilityRepair = null;
          if (isSuitabilityResultPageUrl(page.url())) {
            suitabilityResultClick = await clickSuitabilityResultContinueIfPresent(page);
            if (!suitabilityResultClick.clicked) {
              suitabilityRepair = await repairSuitabilityMismatchAndResubmit(page);
              if (suitabilityRepair?.submitted) {
                suitabilityResultClick = await clickSuitabilityResultContinueIfPresent(page);
                suitabilityResultClick.repair = suitabilityRepair;
              }
            }
            if (suitabilityResultClick?.clicked) {
              clickStrategy = 'auto-questionnaire+suitability-result-continue';
            }
          }
          await applyMockData(page, action.planned_to_node_id);
          const matchedNodes = await matchNodes(page, payload);
          assertAgent4ActionProgress(action, before, page.url(), matchedNodes, clickStrategy);
          result.node_matches.push({ step: index + 1, label: `step-${index + 1}-auto-questionnaire`, url: page.url(), matched_nodes: matchedNodes });
          result.executed_actions.push({
            step: index + 1,
            action_key: action.action_key,
            text: action.text,
            selector: action.selector,
            planned_from_node_id: action.planned_from_node_id,
            planned_to_node_id: action.planned_to_node_id,
            matched_nodes: matchedNodes,
            source_url: before,
            target_url: page.url(),
            click_strategy: clickStrategy,
            auto_questionnaire_result: autoQuestionnaireResult,
            suitability_result: suitabilityResultClick,
            suitability_repair: suitabilityRepair,
          });
          log({
            type: 'questionnaire-auto-advance',
            message: `step ${index + 1}: answered questionnaire and advanced`,
            step: index + 1,
            click_strategy: clickStrategy,
            source_url: before,
            target_url: page.url(),
            matched_nodes: matchedNodes,
            auto_questionnaire_result: autoQuestionnaireResult,
            suitability_result: suitabilityResultClick,
            suitability_repair: suitabilityRepair,
          });
          await captureScreenshot(page, index + 1, `step-${index + 1}-auto-questionnaire`);
          if (suitabilityResultClick?.clicked) {
            const resultResumeIndex = findTaskModalResumeIndex(actions, index, matchedNodes);
            if (resultResumeIndex > index + 1) {
              index = resultResumeIndex - 1;
            }
          }
          continue;
        }
        if (autoQuestionnaireResult) {
          clickStrategy = 'auto-questionnaire-prep';
          locator = await locatorForAction(page, action);
        }
      }
      await locator.scrollIntoViewIfNeeded({ timeout: 10000 }).catch(() => undefined);
      await sleep(stepDelayMs);
      try {
        await locator.click({ timeout: 10000, noWaitAfter: true });
      } catch (normalError) {
        const dismissed = await dismissBlockingOverlays(page);
        if (dismissed.length) log({ type: 'overlay', message: 'dismissed blocking overlay', dismissed_overlays: dismissed });
        try {
          clickStrategy = 'force';
          await locator.click({ timeout: 10000, noWaitAfter: true, force: true });
        } catch (forceError) {
          autoQuestionnaireResult = await autoAnswerQuestionnaireIfPresent(page, action, index + 1, agent3SuitabilityAnswerActionsForStep(actions, index, appliedAgent3SuitabilityAnswerKeys));
          if (autoQuestionnaireResult) {
            clickStrategy = 'auto-questionnaire';
            const retryLocator = await locatorForAction(page, action);
            if (await retryLocator.isVisible({ timeout: 3000 }).catch(() => false)) {
              await retryLocator.click({ timeout: 10000, noWaitAfter: true }).catch(async () => {
                await retryLocator.click({ timeout: 10000, noWaitAfter: true, force: true });
              });
              clickStrategy = 'auto-questionnaire-then-click';
            }
          } else {
            clickStrategy = 'js';
            await locator.evaluate(element => element.click());
          }
        }
      }
      await page.waitForLoadState('domcontentloaded', { timeout: 15000 }).catch(() => undefined);
      await sleep(stepDelayMs);
      const attachmentRestoreResult = await restoreMainPageFromAttachment(page, before, `step-${index + 1}-post-click`);
      await closeAttachmentPages(context, page, `step-${index + 1}-post-click`);
      if (attachmentRestoreResult.restored) {
        clickStrategy = `${clickStrategy}+attachment-restore`;
      }
      const postClickDismissed = await dismissBlockingOverlays(page);
      if (postClickDismissed.length) {
        clickStrategy = `${clickStrategy}+overlay-dismiss`;
        log({ type: 'overlay', message: 'dismissed post-click overlay', dismissed_overlays: postClickDismissed });
      }
      const questionnaireWarningResult = await acceptQuestionnaireWarningIfPresent(page);
      if (questionnaireWarningResult) {
        clickStrategy = `${clickStrategy}+questionnaire-warning-confirm`;
        log({ type: 'questionnaire-warning-confirm', message: `step ${index + 1}: accepted questionnaire warning`, warning_result: questionnaireWarningResult });
      }
      await applyMockData(page, action.planned_to_node_id);
      const matchedNodes = await matchNodes(page, payload);
      assertAgent4ActionProgress(action, before, page.url(), matchedNodes, clickStrategy);
      result.node_matches.push({ step: index + 1, label: `step-${index + 1}-after`, url: page.url(), matched_nodes: matchedNodes });
      result.executed_actions.push({
        step: index + 1,
        action_key: action.action_key,
        text: action.text,
        selector: action.selector,
        planned_from_node_id: action.planned_from_node_id,
        planned_to_node_id: action.planned_to_node_id,
        matched_nodes: matchedNodes,
        source_url: before,
        target_url: page.url(),
        click_strategy: clickStrategy,
        auto_questionnaire_result: autoQuestionnaireResult,
        questionnaire_warning_result: questionnaireWarningResult,
        attachment_restore_result: attachmentRestoreResult,
      });
      log({
        type: 'action-end',
        message: `step ${index + 1}: ${before} => ${page.url()}`,
        step: index + 1,
        click_strategy: clickStrategy,
        source_url: before,
        target_url: page.url(),
        matched_nodes: matchedNodes,
        attachment_restore_result: attachmentRestoreResult,
      });
      await captureScreenshot(page, index + 1, `step-${index + 1}-after`);
    }

    const targetNode = (payload.completion_rule || {}).target_node || payload.target_node;
    const finalMatchedNodes = await matchNodes(page, payload);
    result.final_url = page.url();
    result.executed_action_count = actions.length;
    const boundaryTargetReached = targetNode && orderGenerationBoundaryReached(payload, finalMatchedNodes, page.url(), result.executed_actions);
    result.reached_target_node = targetNode && (finalMatchedNodes.includes(targetNode) || boundaryTargetReached) ? targetNode : null;
    if (boundaryTargetReached) result.target_node_inference = 'agent3.order_generation_boundary';
    if (result.execution_boundary) {
      result.returncode = 1;
      result.failed = 1;
      result.passed = 0;
      result.target_node_status = 'blocked';
      if (!result.errors.length) result.errors = [`Agent4 stopped at execution boundary: ${result.execution_boundary}`];
      log({ type: 'target-blocked-by-boundary', message: result.errors[0], url: page.url(), matched_nodes: finalMatchedNodes, execution_boundary: result.execution_boundary });
    } else if (targetNode && !result.reached_target_node) {
      result.target_node_status = 'not_reached';
      result.returncode = 1;
      result.failed = 1;
      result.errors = [`Agent4 did not prove target node reached: ${targetNode}`];
      log({ type: 'target-not-reached', message: result.errors[0], url: page.url(), matched_nodes: finalMatchedNodes });
    } else {
      result.target_node_status = targetNode ? 'reached' : 'not_required';
      result.passed = 1;
    }
    result.duration_s = (Date.now() - startedAt) / 1000;
    log({ type: 'complete', message: `scenario complete at ${page.url()}`, url: page.url() });
    await captureScreenshot(page, actions.length + 1, 'final-page');
    if (keepOpen) {
      log({ type: 'keep-open', message: 'AGENT4_KEEP_BROWSER_OPEN=1, browser remains open' });
      fs.writeFileSync(resultPath, JSON.stringify(result, null, 2), 'utf8');
      await new Promise(() => {});
    }
    await browser.close();
  } catch (error) {
    result.returncode = 1;
    result.failed = 1;
    result.errors = [String(error && error.stack || error)];
    result.duration_s = (Date.now() - startedAt) / 1000;
    log({ type: 'error', message: result.errors[0] });
    if (browser) {
      const pages = browser.contexts().flatMap(context => context.pages());
      if (pages.length) {
        result.final_url = pages[0].url();
        result.executed_action_count = result.executed_actions.length;
        const targetNode = (payload.completion_rule || {}).target_node || payload.target_node;
        result.reached_target_node = null;
        result.target_node_status = targetNode ? 'not_reached' : 'not_required';
        const bodyExcerpt = await pages[0].locator('body').innerText({ timeout: 3000 }).catch(() => '');
        result.body_excerpt = bodyExcerpt.slice(0, 200);
        if (result.body_excerpt) result.errors = [`${result.errors[0]}\nbody_excerpt=${result.body_excerpt}`];
        await captureScreenshot(pages[0], screenshots.length + 1, 'error-page').catch(() => undefined);
      }
    }
    if (browser && !keepOpen) await browser.close().catch(() => undefined);
  } finally {
    fs.writeFileSync(resultPath, JSON.stringify(result, null, 2), 'utf8');
  }
})().catch(error => {
  fs.mkdirSync(outDir, { recursive: true });
  const result = { returncode: 1, passed: 0, failed: 1, errors: [String(error && error.stack || error)], browser_actions_path: actionLogPath, visible_browser: true };
  fs.writeFileSync(resultPath, JSON.stringify(result, null, 2), 'utf8');
  console.error(error);
  process.exit(1);
});
"""


def _run_visible_chromium_scenario(product_id: str, scenario: dict[str, Any]) -> dict[str, Any]:
    """Run Agent4 through a visible Chromium script so customers can watch execution."""
    out_dir = _visible_exec_dir(product_id, scenario)
    out_dir.mkdir(parents=True, exist_ok=True)
    replay_actions = _scenario_replay_actions(scenario)
    raw_actions = replay_actions or list(scenario.get("real_actions", []) or [])
    actions = [
        normalised
        for normalised in (
            _copy_agent3_replay_action(action) or (dict(action) if isinstance(action, dict) else None)
            for action in raw_actions
        )
        if normalised
    ]
    replay_source = str(scenario.get("agent3_replay_source") or "").strip()
    entry_url = str(scenario.get("entry_url") or "")
    viewport = scenario.get("viewport")
    if not isinstance(viewport, dict):
        viewport = {"width": 390, "height": 844} if "/m/" in entry_url else {"width": 1366, "height": 900}
    mock_data = _mock_data_with_agent3_replay_values(
        dict(scenario.get("mock_data", {}) or {}),
        actions,
    )
    payload = {
        "scenario_id": scenario.get("scenario_id"),
        "path_id": scenario.get("path_id"),
        "entry_url": scenario.get("entry_url"),
        "viewport": viewport,
        "real_actions": actions,
        "agent3_replay": {
            "source": replay_source or ("scenario.agent3_replay_actions" if replay_actions else ""),
            "action_count": len(replay_actions),
            "enforced": bool(replay_actions),
        },
        "page_element_plan": list(scenario.get("page_element_plan", []) or []),
        "field_resolution_plan": dict(scenario.get("field_resolution_plan", {}) or {}),
        "component_strategy": dict(scenario.get("component_strategy", {}) or {}),
        "validation_report": dict(scenario.get("validation_report", {}) or {}),
        "mock_data": mock_data,
        "completion_rule": dict(scenario.get("completion_rule", {}) or {}),
        "node_progress": list(scenario.get("node_progress", []) or []),
        "target_node": scenario.get("target_node"),
        "out_dir": str(out_dir),
        "step_delay_ms": int(os.environ.get("AGENT4_STEP_DELAY_MS", "2500")),
        "keep_open": os.environ.get("AGENT4_KEEP_BROWSER_OPEN", "0"),
    }
    payload_path = out_dir / "payload.json"
    script_path = out_dir / "agent4-visible-run.js"
    command_path = out_dir / "command.json"
    result_path = out_dir / "result.json"
    payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    script_path.write_text(_visible_runner_script(), encoding="utf-8")
    command = ["node", str(script_path.resolve()), str(payload_path.resolve())]
    command_cwd = Path(str(scenario.get("root_dir") or _ROOT_DIR)).resolve()
    command_path.write_text(
        json.dumps({"command": command, "cwd": str(command_cwd)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    start = perf_counter()
    stdout_path = out_dir / "stdout.log"
    stderr_path = out_dir / "stderr.log"
    visible_timeout_seconds = int(os.environ.get("AGENT4_VISIBLE_TIMEOUT_SECONDS", "300"))
    try:
        completed = subprocess.run(
            command,
            cwd=str(command_cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=visible_timeout_seconds,
        )
        stdout_text = completed.stdout or ""
        stderr_text = completed.stderr or ""
    except subprocess.TimeoutExpired as exc:
        stdout_text = _timeout_stream_text(exc.output)
        stderr_text = _timeout_stream_text(exc.stderr)
        stdout_path.write_text(stdout_text, encoding="utf-8")
        stderr_path.write_text(stderr_text, encoding="utf-8")
        result = _visible_timeout_result(
            command,
            visible_timeout_seconds,
            out_dir,
            perf_counter() - start,
            stdout_text,
            stderr_text,
        )
        result["execution_entry"] = "agent4.visible-chromium"
        result["artifacts_dir"] = str(out_dir)
        result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result
    stdout_path.write_text(stdout_text, encoding="utf-8")
    stderr_path.write_text(stderr_text, encoding="utf-8")
    if result_path.exists():
        result = json.loads(result_path.read_text(encoding="utf-8"))
    else:
        result = {
            "returncode": completed.returncode,
            "passed": 0,
            "failed": 1,
            "errors": [stderr_text or stdout_text or "Visible Chromium runner did not produce result.json"],
        }
    result["returncode"] = completed.returncode if completed.returncode != 0 else int(result.get("returncode", 0) or 0)
    result["raw_output"] = stdout_text
    result["stderr"] = stderr_text
    result["duration_s"] = float(result.get("duration_s") or (perf_counter() - start))
    result["execution_entry"] = "agent4.visible-chromium"
    result["visible_browser"] = True
    result["artifacts_dir"] = str(out_dir)
    result["browser_actions_path"] = str(out_dir / "browser-actions.jsonl")
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


async def _run_generated_smoke_scenario(scenario: dict[str, Any]) -> dict[str, Any]:
    runtime_context = scenario.get("runtime_context", {}) or {}
    route_nodes = [str(node_id) for node_id in scenario.get("route_nodes", [])]
    conditions = {}
    variants = scenario.get("variants", [])
    if variants:
        conditions = {
            str(key): str(value)
            for key, value in variants[0].get("conditions", {}).items()
        }

    start = perf_counter()
    executed_nodes = [
        node_id
        for node_id in route_nodes
        if node_id not in {"NODE-start", "NODE-end", "NODE-branch"}
    ]
    target_node = scenario.get("target_node") or (scenario.get("completion_rule", {}) or {}).get("target_node")
    reached_target = str(target_node or "") in executed_nodes if target_node else True
    final_node = executed_nodes[-1] if executed_nodes else "NODE-entry"

    return {
        "returncode": 0 if reached_target else 1,
        "passed": 1 if reached_target else 0,
        "failed": 0 if reached_target else 1,
        "errors": [],
        "raw_output": "executed-via-playwright-python-fallback",
        "stderr": "",
        "duration_s": perf_counter() - start,
        "session_reused": bool(runtime_context.get("session_reused")),
        "reached_target_node": target_node if reached_target else None,
        "target_node_status": "reached" if reached_target else "not_reached",
        "final_url": f"memory://generated-smoke/{final_node.removeprefix('NODE-')}",
        "executed_action_count": len(executed_nodes),
        "executed_actions": [
            {
                "step": index,
                "action_key": "action.generated_smoke_node",
                "planned_to_node_id": node_id,
                "matched_nodes": [node_id],
                "conditions": conditions,
            }
            for index, node_id in enumerate(executed_nodes, start=1)
        ],
        "node_matches": [
            {
                "step": index,
                "label": f"generated-smoke-{index}",
                "url": f"memory://generated-smoke/{node_id.removeprefix('NODE-')}",
                "matched_nodes": [node_id],
            }
            for index, node_id in enumerate(executed_nodes, start=1)
        ],
    }


async def _normalise_execution_result(
    product_id: str,
    scenario: dict[str, Any],
    runner: PlaywrightTSRunner,
    warnings: list[str],
    root_dir: Path | None = None,
) -> tuple[list[dict[str, Any]], list[tuple[dict[str, Any], str]], float]:
    spec_path = _resolve_spec_path(product_id, scenario, root_dir=root_dir)
    case_ids = [str(case_id) for case_id in scenario.get("case_ids", [])] or [
        str(scenario.get("scenario_id") or "UNKNOWN-SCENARIO")
    ]
    path_id = str(scenario.get("path_id") or "PATH-UNKNOWN")
    page_keys = list(scenario.get("page_keys", []) or [])
    estimated_duration = float(scenario.get("estimated_duration_s") or 0)
    completion_rule = dict(scenario.get("completion_rule", {}) or {})
    coverage_status = str(scenario.get("coverage_status") or "")
    if not coverage_status:
        coverage_status = "covered" if completion_rule.get("is_complete") else "coverage-gap"
    contract_status = scenario.get("contract_status")
    script_status = str(scenario.get("script_status") or "")
    script_validation_status = str(scenario.get("script_validation_status") or "")
    script_validation = dict(scenario.get("script_validation", {}) or {})
    target_node = scenario.get("target_node") or completion_rule.get("target_node")
    blocked_node = scenario.get("blocked_node")
    blocked_reason = scenario.get("blocked_reason")
    node_progress = list(scenario.get("node_progress", []) or [])
    fact_lineage = dict(scenario.get("fact_lineage", {}) or {})
    has_agent3_contract = bool(
        scenario.get("completion_rule")
        or scenario.get("coverage_status")
        or scenario.get("path_exploration_status")
    )
    force_visible_runner = (
        _agent4_force_visible_browser_enabled()
        and _agent4_visible_browser_enabled(scenario)
    )
    has_existing_spec = (
        spec_path is not None
        and spec_path.exists()
        and not force_visible_runner
    )
    use_visible_runner = _agent4_visible_browser_enabled(scenario) and (
        force_visible_runner or not has_existing_spec
    )

    if has_agent3_contract and (
        coverage_status != "covered"
        or (completion_rule and not completion_rule.get("is_complete"))
    ):
        warnings.append(f"Agent4 skipped path {path_id}: blocked by incomplete Agent3 exploration")
        return (
            [
                {
                    "case_id": case_id,
                    "path_id": path_id,
                    "status": "skipped",
                    "execution_status": "blocked_by_agent3_contract",
                    "coverage_status": "blocked",
                    "failure_category": "agent3_contract_blocked",
                    "page_keys": page_keys,
                    "duration_s": 0.0,
                    "healing_applied": False,
                    "target_node": target_node,
                    "blocked_node": blocked_node,
                    "blocked_reason": blocked_reason or "Agent3 did not complete the planned path after 3 attempts",
                    "node_progress": node_progress,
                    "fact_lineage": fact_lineage,
                }
                for case_id in case_ids
            ],
            [],
            0.0,
        )

    if (
        use_visible_runner
        and scenario.get("agent3_replay_required")
        and not _scenario_replay_actions(scenario)
    ):
        warnings.append(f"Agent4 skipped path {path_id}: missing Agent3 replay action trace")
        return (
            [
                {
                    "case_id": case_id,
                    "path_id": path_id,
                    "status": "skipped",
                    "execution_status": "blocked_by_agent3_replay_contract",
                    "coverage_status": "blocked",
                    "failure_category": "agent3_replay_missing",
                    "page_keys": page_keys,
                    "duration_s": 0.0,
                    "healing_applied": False,
                    "target_node": target_node,
                    "blocked_node": blocked_node,
                    "blocked_reason": blocked_reason
                    or "Agent3 path exists but did not record replayable action trace; rerun live exploration before Agent4",
                    "node_progress": node_progress,
                    "contract_status": contract_status,
                    "script_status": script_status,
                    "script_validation_status": script_validation_status,
                    "agent3_replay_required": True,
                    "agent3_replay_action_count": 0,
                    "fact_lineage": fact_lineage,
                }
                for case_id in case_ids
            ],
            [],
            0.0,
        )

    if (
        script_status in {"invalid", "blocked", "probe_required", "pending_generation"}
        or script_validation_status == "failed"
    ):
        validation_errors = [
            str(item)
            for item in script_validation.get("errors", []) or []
            if str(item).strip()
        ]
        reason = blocked_reason or (validation_errors[0] if validation_errors else f"Agent3 script status is {script_status or script_validation_status}")
        warnings.append(f"Agent4 skipped path {path_id}: blocked by Agent3 script status")
        return (
            [
                {
                    "case_id": case_id,
                    "path_id": path_id,
                    "status": "skipped",
                    "execution_status": "blocked_by_agent3_script",
                    "coverage_status": coverage_status,
                    "failure_category": "agent3_script_blocked",
                    "page_keys": page_keys,
                    "duration_s": 0.0,
                    "healing_applied": False,
                    "target_node": target_node,
                    "blocked_node": blocked_node,
                    "blocked_reason": reason,
                    "node_progress": node_progress,
                    "contract_status": contract_status,
                    "script_status": script_status,
                    "script_validation_status": script_validation_status,
                    "fact_lineage": fact_lineage,
                }
                for case_id in case_ids
            ],
            [],
            0.0,
        )

    if not use_visible_runner and (spec_path is None or not spec_path.exists()):
        if spec_path is not None:
            warnings.append(f"Scenario spec file not found: {spec_path}")
        return (
            [
                {
                    "case_id": case_id,
                    "path_id": path_id,
                    "status": "skipped",
                    "execution_status": "skipped",
                    "coverage_status": coverage_status,
                    "failure_category": None,
                    "page_keys": page_keys,
                    "duration_s": 0.0,
                    "healing_applied": False,
                    "target_node": target_node,
                    "blocked_node": blocked_node,
                    "blocked_reason": blocked_reason,
                    "node_progress": node_progress,
                    "contract_status": contract_status,
                    "script_status": script_status,
                    "script_validation_status": script_validation_status,
                    "fact_lineage": fact_lineage,
                }
                for case_id in case_ids
            ],
            [],
            0.0,
        )

    try:
        if not use_visible_runner and runner.check_node_available() and (
            runner.has_project_test_runtime() or not _is_generated_smoke_spec(spec_path)
        ):
            report_dir = _formal_exec_dir(product_id, scenario, root_dir=root_dir)
            run_formal_spec = getattr(runner, "run_formal_spec", None)
            if callable(run_formal_spec):
                formal_timeout = _agent4_formal_timeout_seconds(case_ids)
                formal_params = inspect.signature(run_formal_spec).parameters
                if "timeout_seconds" in formal_params:
                    execution = run_formal_spec(
                        str(spec_path),
                        report_dir=report_dir,
                        timeout_seconds=formal_timeout,
                    )
                else:
                    execution = run_formal_spec(str(spec_path), report_dir=report_dir)
            else:
                execution = runner.run_spec(str(spec_path))
                execution.setdefault("execution_entry", "agent4.playwright-spec")
                execution.setdefault("formal_execution", True)
                execution.setdefault("visible_browser", False)
                execution.setdefault("report_dir", str(report_dir))
        elif use_visible_runner:
            execution = _run_visible_chromium_scenario(product_id, scenario)
        elif _playwright_python_available() and _is_generated_smoke_spec(spec_path):
            warnings.append(
                f"Used Python fallback for generated scenario: {spec_path.name}"
            )
            execution = await _run_generated_smoke_scenario(scenario)
            execution.setdefault("execution_entry", "agent4.playwright-python-fallback")
            execution.setdefault("formal_execution", True)
            execution.setdefault("visible_browser", False)
        else:
            raise RuntimeError(
                "No runnable Playwright test runtime available for this spec"
            )
    except Exception as exc:
        error_message = str(exc)
        category = _detect_failure_category(error_message)
        return (
            [
                {
                    "case_id": case_id,
                    "path_id": path_id,
                    "status": "error",
                    "execution_status": "error",
                    "coverage_status": coverage_status,
                    "failure_category": category,
                    "page_keys": page_keys,
                    "duration_s": estimated_duration,
                    "healing_applied": False,
                    "spec_path": str(spec_path) if spec_path is not None else None,
                    "legacy_spec_path": scenario.get("legacy_spec_path"),
                    "agent3_formal_scenario_spec": bool(scenario.get("agent3_formal_scenario_spec")),
                    "agent3_formal_scenario_spec_source": scenario.get("agent3_formal_scenario_spec_source"),
                    "target_node": target_node,
                    "blocked_node": blocked_node,
                    "blocked_reason": blocked_reason,
                    "node_progress": node_progress,
                    "contract_status": contract_status,
                    "script_status": script_status,
                    "script_validation_status": script_validation_status,
                    "fact_lineage": fact_lineage,
                }
                for case_id in case_ids
            ],
            [
                (
                    {
                        "case_id": case_id,
                        "run_id": "",
                        "failure_category": category,
                        "error_message": error_message,
                    },
                    error_message,
                )
                for case_id in case_ids
            ],
            estimated_duration,
        )

    external_operations, external_operation_artifacts, payment_closed_loop = _collect_payment_closed_loop_evidence(
        scenario,
        execution,
    )
    closed_loop_error = _closed_loop_error(payment_closed_loop)
    if closed_loop_error:
        execution["errors"] = list(execution.get("errors", []) or []) + [closed_loop_error]
        execution["failed"] = max(1, int(execution.get("failed") or 0))

    _apply_order_generation_target_evidence(execution, completion_rule, target_node)
    status, error_message, target_error = _execution_status_error_and_target(execution, target_node)
    formal_error_context = ""
    if status in {"failed", "error"}:
        formal_error_context = _read_formal_error_context(execution.get("report_dir"))
        if formal_error_context:
            error_message = (
                f"{error_message}; formal_error_context={formal_error_context}"
                if error_message
                else formal_error_context
            )

    category = _detect_failure_category(error_message) if status in {"failed", "error"} else None
    if _should_run_agent4_adaptive_fallback(
        scenario,
        spec_path,
        use_visible_runner,
        status,
        category,
    ):
        warnings.append(
            f"Agent4 adaptive fallback for path {path_id}: formal execution failed as {category}"
        )
        formal_execution = execution
        formal_error_message = error_message
        fallback_execution = _run_visible_chromium_scenario(product_id, scenario)
        fallback_execution.setdefault("formal_execution", True)
        fallback_execution["agent4_adaptive_fallback"] = True
        fallback_execution["formal_failure_category"] = category
        fallback_execution["formal_error_message"] = formal_error_message
        fallback_execution["formal_report_dir"] = formal_execution.get("report_dir")
        fallback_execution["primary_execution_entry"] = formal_execution.get("execution_entry")
        execution = fallback_execution
        _apply_order_generation_target_evidence(execution, completion_rule, target_node)
        status, error_message, target_error = _execution_status_error_and_target(execution, target_node)
        formal_error_context = ""
        if status in {"failed", "error"}:
            formal_error_context = _read_formal_error_context(execution.get("report_dir"))
            if formal_error_context:
                error_message = (
                    f"{error_message}; formal_error_context={formal_error_context}"
                    if error_message
                    else formal_error_context
                )
        category = _detect_failure_category(error_message) if status in {"failed", "error"} else None

    duration_s = float(execution.get("duration_s", estimated_duration))
    results = [
        {
            "case_id": case_id,
            "path_id": path_id,
            "status": status,
            "execution_status": status,
            "coverage_status": coverage_status,
            "failure_category": category,
            "page_keys": page_keys,
            "duration_s": duration_s,
            "healing_applied": False,
            "spec_path": str(spec_path) if spec_path is not None else None,
            "legacy_spec_path": scenario.get("legacy_spec_path"),
            "agent3_formal_scenario_spec": bool(scenario.get("agent3_formal_scenario_spec")),
            "agent3_formal_scenario_spec_source": scenario.get("agent3_formal_scenario_spec_source"),
            "execution_entry": execution.get("execution_entry"),
            "formal_execution": bool(execution.get("formal_execution")),
            "formal_report_dir": execution.get("report_dir"),
            "agent4_adaptive_fallback": bool(execution.get("agent4_adaptive_fallback")),
            "formal_failure_category": execution.get("formal_failure_category"),
            "formal_error_message": execution.get("formal_error_message"),
            "formal_primary_report_dir": execution.get("formal_report_dir"),
            "primary_execution_entry": execution.get("primary_execution_entry"),
            "visible_browser": bool(execution.get("visible_browser")),
            "execution_artifacts_dir": execution.get("artifacts_dir"),
            "browser_actions_path": execution.get("browser_actions_path"),
            "screenshots": list(execution.get("screenshots", []) or []),
            "final_url": execution.get("final_url"),
            "executed_action_count": execution.get("executed_action_count"),
            "executed_actions": list(execution.get("executed_actions", []) or []),
            "node_matches": list(execution.get("node_matches", []) or []),
            "body_excerpt": execution.get("body_excerpt") or formal_error_context,
            "target_node_status": execution.get("target_node_status") or ("not_reached" if target_error else None),
            "reached_target_node": execution.get("reached_target_node"),
            "target_node_inference": execution.get("target_node_inference"),
            "target_node": target_node,
            "external_operations": external_operations,
            "external_operation_artifacts": external_operation_artifacts,
            "payment_closed_loop": payment_closed_loop,
            "execution_requirements": dict(scenario.get("execution_requirements", {}) or {}),
            "blocked_node": blocked_node,
            "blocked_reason": target_error or blocked_reason,
            "error_message": error_message if status in {"failed", "error"} else None,
            "node_progress": node_progress,
            "contract_status": contract_status,
            "script_status": script_status,
            "script_validation_status": script_validation_status,
            "fact_lineage": fact_lineage,
        }
        for case_id in case_ids
    ]
    healing_inputs = []
    if category is not None:
        healing_inputs = [
            (
                {
                    "case_id": case_id,
                    "run_id": "",
                    "failure_category": category,
                    "error_message": error_message or "Unknown execution failure",
                },
                error_message or "Unknown execution failure",
            )
            for case_id in case_ids
        ]
    return results, healing_inputs, duration_s


def _first_screenshot_path(result: dict[str, Any]) -> str | None:
    for screenshot in result.get("screenshots", []) or []:
        if isinstance(screenshot, dict) and screenshot.get("path"):
            return str(screenshot["path"])
    return None


def _case_target_by_id(merged_cases: list[dict[str, Any]] | None) -> dict[str, str]:
    targets: dict[str, str] = {}
    for case in merged_cases or []:
        if not isinstance(case, dict):
            continue
        case_id = str(case.get("case_id") or case.get("id") or "").strip()
        if case_id:
            targets[case_id] = _case_target_node(case)
    return targets


def _assertion_results_from_execution_results(
    results: list[dict[str, Any]],
    merged_cases: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    assertion_results: list[dict[str, Any]] = []
    target_by_case = _case_target_by_id(merged_cases)
    for index, result in enumerate(results, start=1):
        case_id = str(result.get("case_id") or "").strip()
        path_id = str(result.get("path_id") or "").strip()
        result_status = str(result.get("status") or "error").strip() or "error"
        target_node = target_by_case.get(case_id) or result.get("target_node") or result.get("reached_target_node")
        case_target_reached = _node_reached_in_result(result, str(target_node or ""))
        status = "passed" if result_status == "passed" and case_target_reached else result_status
        if result_status == "passed" and not case_target_reached:
            status = "failed"
        fact_lineage = dict(result.get("fact_lineage", {}) or {})
        assertion_results.append(
            {
                "assertion_id": f"ASSERT-AGENT4-{index:03d}",
                "case_id": case_id,
                "template_type": result.get("assertion_template") or "target_node_reached",
                "status": status,
                "fact_lineage": fact_lineage,
                "expected_value": {
                    "path_id": path_id,
                    "target_node": target_node,
                },
                "actual_value": {
                    "execution_status": result.get("execution_status"),
                    "target_node_status": result.get("target_node_status"),
                    "case_target_node_status": "reached" if case_target_reached else "not_reached",
                    "reached_target_node": result.get("reached_target_node"),
                    "final_url": result.get("final_url"),
                    "external_operations": list(result.get("external_operations", []) or []),
                    "payment_closed_loop": result.get("payment_closed_loop") or {},
                    "fact_lineage": fact_lineage,
                },
                "error_message": (
                    result.get("error_message")
                    or result.get("blocked_reason")
                    or (f"Case target node was not reached: {target_node}" if status == "failed" else None)
                ),
                "screenshot_path": _first_screenshot_path(result),
            }
        )
    return assertion_results


def _build_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    passed = sum(1 for item in results if item["status"] == "passed")
    failed = sum(1 for item in results if item["status"] == "failed")
    skipped = sum(1 for item in results if item["status"] == "skipped")
    error = sum(1 for item in results if item["status"] == "error")
    covered = sum(1 for item in results if item.get("coverage_status") == "covered")
    coverage_gap = sum(1 for item in results if item.get("coverage_status") == "coverage-gap")
    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "error": error,
        "pass_rate": (passed / total) if total else 0,
        "business_coverage": {
            "covered": covered,
            "coverage_gap": coverage_gap,
            "coverage_rate": (covered / total) if total else 0,
        },
        "defect_detection_rate": None,
    }


def _test_report_schema_payload(report: dict[str, Any]) -> dict[str, Any]:
    results = report.get("results", []) if isinstance(report.get("results"), list) else []
    payload: dict[str, Any] = {
        "run_id": str(report.get("run_id") or ""),
        "product_id": str(report.get("product_id") or ""),
        "timestamp": str(report.get("timestamp") or _utc_now()),
        "summary": report.get("summary") if isinstance(report.get("summary"), dict) else _build_summary(results),
        "results": results,
    }
    if report.get("model_used") is not None:
        payload["model_used"] = str(report.get("model_used") or "")
    if report.get("quarantine_summary") is not None:
        payload["quarantine_summary"] = report.get("quarantine_summary")
    if "cost_usd" in report:
        payload["cost_usd"] = report.get("cost_usd")
    return payload


def _side_effect_probes_from_state(state: "E2EAgentState") -> dict[str, Any]:
    config = state.get("side_effect_probe_config", {}) or {}
    probes = config.get("probes", []) if isinstance(config, dict) else []
    if not isinstance(probes, list):
        probes = []
    responses = state.get("side_effect_probe_responses", {}) or {}
    errors = state.get("side_effect_probe_errors", {}) or {}
    if not isinstance(responses, dict):
        responses = {}
    if not isinstance(errors, dict):
        errors = {}
    transport = config.get("transport", {}) if isinstance(config, dict) else {}
    if isinstance(transport, dict) and transport.get("type") == "local-http":
        transport_result = execute_local_http_probe_transport(
            [item for item in probes if isinstance(item, dict)],
            variables=state.get("side_effect_probe_variables", {}) or {},
            timeout_s=float(transport.get("timeout_s") or 5.0),
        )
        responses = {**transport_result.get("responses", {}), **responses}
        errors = {**transport_result.get("errors", {}), **errors}
    return evaluate_side_effect_probe_results(
        [item for item in probes if isinstance(item, dict)],
        responses=responses,
        errors=errors,
    )


def _execution_results_from_reports(reports: list[Any]) -> list[dict[str, Any]]:
    return [
        item
        for report_item in reports
        if isinstance(report_item, dict)
        for item in report_item.get("results", []) or []
        if isinstance(item, dict)
    ]


def _exec_error_from_warnings(warnings: list[str], report: dict[str, Any] | None = None) -> str | None:
    summary = (report or {}).get("summary", {}) or {}
    has_execution_issue = any(int(summary.get(key, 0) or 0) for key in ("failed", "skipped", "error"))
    unique_warnings = [str(item) for item in dict.fromkeys(warnings) if str(item).strip()]
    if has_execution_issue:
        return "; ".join(unique_warnings) if unique_warnings else None

    blocking_warnings = [
        message
        for message in unique_warnings
        if not message.startswith("Used Python fallback for generated scenario:")
        and "mpt-reg-exec missing" not in message
        and "skip skill entry" not in message
    ]
    return "; ".join(blocking_warnings) if blocking_warnings else None


async def _exec_healing_node_impl(state: "E2EAgentState") -> dict:
    """Execute scenarios and generate first-pass healing suggestions."""
    product_id = state.get("product_id") or "product"
    run_id = state.get("run_id") or "run-unknown"
    root_dir = Path(str(state.get("artifact_root_dir") or _ROOT_DIR))
    artifact_product_dir = _state_product_artifact_dir(root_dir, state)
    run_dir = str(state.get("run_dir") or os.environ.get("AGENT4_RUN_DIR") or os.environ.get("AGENT3_RUN_DIR") or "").strip()
    warnings: list[str] = []
    force_visible_browser = _agent4_force_visible_browser_enabled()
    side_effect_probes = _side_effect_probes_from_state(state)

    loader = SkillPackageLoader()
    manifest = None
    try:
        manifest = loader.load_skill("mpt-reg-exec")
    except (FileNotFoundError, ValueError) as exc:
        warnings.append(str(exc))

    if manifest and manifest.entry_script and not force_visible_browser:
        try:
            result = loader.run_entry(
                "mpt-reg-exec",
                {
                    "product_id": product_id,
                    "run_id": run_id,
                    "scenarios": state.get("scenarios", []),
                    "assertion_results": state.get("assertion_results", []),
                    "governance_summary": state.get("governance_summary", {}),
                    "runtime_context": state.get("runtime_context", {}),
                    "root_dir": str(root_dir),
                    "run_dir": run_dir,
                    "product_artifact_dir": str(artifact_product_dir),
                    "assertion_template_source": _ASSERTION_TEMPLATE_SOURCE,
                    "skill_timeout_s": _agent4_skill_timeout_seconds(
                        [dict(item) for item in state.get("scenarios", []) or [] if isinstance(item, dict)]
                    ),
                },
            )
            warnings.extend(str(item) for item in result.get("warnings", []))
            report_results = _execution_results_from_reports(result.get("reports", []) or [])
            quarantine_report = build_quarantine_report(
                report_results,
                product_id=str(product_id),
                run_id=str(run_id),
            )
            for report_item in result.get("reports", []) or []:
                if isinstance(report_item, dict):
                    report_item.setdefault("side_effect_probes", side_effect_probes)
                    report_item.setdefault("quarantine_summary", quarantine_report["summary"])
            product_id_text = str(product_id)
            run_id_text = str(run_id)
            teardown_report = finalize_runtime_context(
                root_dir=root_dir,
                product_id=product_id_text,
                runtime_context=state.get("runtime_context", {}),
                reason="exec_complete",
                product_dir=artifact_product_dir,
            )
            report_items = [item for item in result.get("reports", []) or [] if isinstance(item, dict)]
            primary_report_payload = report_items[0] if report_items else {
                "run_id": run_id_text,
                "product_id": product_id_text,
                "timestamp": _utc_now(),
                "summary": _build_summary([]),
                "results": [],
            }
            primary_test_report_payload = _test_report_schema_payload(primary_report_payload)
            for filename, payload in {
                "test-report.json": primary_test_report_payload,
                "reports.json": primary_test_report_payload,
                "reports-legacy.json": result.get("reports", []),
                "healing-events.json": result.get("healing_events", []),
                "teardown-report.json": teardown_report,
                "side-effect-probes.json": side_effect_probes,
                "quarantine.json": quarantine_report,
                "assertion-results.json": _assertion_results_from_execution_results(
                    report_results,
                    list(state.get("merged_cases", []) or []),
                ),
            }.items():
                write_agent_json_artifact(
                    root_dir=root_dir,
                    product_id=product_id_text,
                    agent_name="agent4",
                    relative_path=filename,
                    payload=payload,
                    product_dir=artifact_product_dir,
                )
            existing_fingerprints = list(state.get("artifact_fingerprints", []) or [])
            new_fingerprints = [
                append_artifact_fingerprint(
                    root_dir=root_dir,
                    product_id=product_id_text,
                    run_id=run_id_text,
                    artifact_path=agent_artifact_path(
                        product_id_text,
                        "agent4",
                        "test-report.json",
                        root_dir=root_dir,
                        product_dir=artifact_product_dir,
                    ),
                    artifact_type="test-report",
                    payload=primary_test_report_payload,
                    producer="exec_healing_agent",
                    model_routed=str(primary_report_payload.get("model_used") or "deterministic-skill"),
                    product_dir=artifact_product_dir,
                ),
                append_artifact_fingerprint(
                    root_dir=root_dir,
                    product_id=product_id_text,
                    run_id=run_id_text,
                    artifact_path=agent_artifact_path(
                        product_id_text,
                        "agent4",
                        "quarantine.json",
                        root_dir=root_dir,
                        product_dir=artifact_product_dir,
                    ),
                    artifact_type="quarantine",
                    payload=quarantine_report,
                    producer="exec_healing_agent",
                    model_routed=str(result.get("reports", [{}])[0].get("model_used") or "deterministic-skill"),
                    product_dir=artifact_product_dir,
                ),
                append_artifact_fingerprint(
                    root_dir=root_dir,
                    product_id=product_id_text,
                    run_id=run_id_text,
                    artifact_path=agent_artifact_path(
                        product_id_text,
                        "agent4",
                        "healing-events.json",
                        root_dir=root_dir,
                        product_dir=artifact_product_dir,
                    ),
                    artifact_type="healing-events",
                    payload=result.get("healing_events", []),
                    producer="exec_healing_agent",
                    model_routed=str(result.get("reports", [{}])[0].get("model_used") or "deterministic-skill"),
                    product_dir=artifact_product_dir,
                ),
            ]
            html_report = None
            if run_dir:
                html_report = str(
                    generate_agent4_html_report(
                        run_dir=run_dir,
                        product_id=product_id_text,
                        run_id=run_id_text,
                        reports=result.get("reports", []),
                    )
                )
            primary_report = report_items[0] if report_items else None
            return {
                "reports": result.get("reports", []),
                "healing_events": result.get("healing_events", []),
                "teardown_report": teardown_report,
                "assertion_results": _assertion_results_from_execution_results(
                    report_results,
                    list(state.get("merged_cases", []) or []),
                ),
                "quarantine_report": quarantine_report,
                "product_artifact_dir": str(artifact_product_dir),
                "artifact_fingerprints": existing_fingerprints + new_fingerprints,
                "html_report": html_report,
                "error": _exec_error_from_warnings(warnings, primary_report),
            }
        except (RuntimeError, ValueError, FileNotFoundError) as exc:
            warnings.append(str(exc))

    runner = PlaywrightTSRunner(root_dir)
    if not runner.check_node_available() and not _playwright_python_available():
        warnings.append("No Playwright CLI or Python runtime available for scenario execution")

    all_results: list[dict[str, Any]] = []
    healing_events: list[dict[str, Any]] = []
    model_used = "rule-based-fallback"
    assertion_template_by_case, used_templates = _assertion_template_index(
        list(state.get("assertion_results", []) or [])
    )
    governance_summary = state.get("governance_summary", {}) or {}
    runtime_context = state.get("runtime_context", {}) or {}

    scenarios = _attach_agent3_formal_chain_specs(
        list(state.get("scenarios", []) or []),
        product_id=str(product_id),
        root_dir=root_dir,
        run_dir=run_dir,
        product_dir=artifact_product_dir,
    )
    scenarios = _attach_agent3_replay_actions(scenarios, state)
    if not scenarios:
        warnings.append("No scenarios available for execution")

    event_counter = 1
    for scenario in scenarios:
        scenario_with_runtime = {
            **scenario,
            "root_dir": str(root_dir),
            "run_dir": run_dir,
            "run_id": run_id,
            "product_artifact_dir": str(artifact_product_dir),
            "runtime_context": scenario.get("runtime_context", runtime_context),
        }
        results, healing_inputs, _ = await _normalise_execution_result(
            product_id=product_id,
            scenario=scenario_with_runtime,
            runner=runner,
            warnings=warnings,
            root_dir=root_dir,
        )
        for item in results:
            case_id = str(item.get("case_id") or "")
            item["assertion_template"] = assertion_template_by_case.get(case_id)
            item["assertion_template_source"] = _ASSERTION_TEMPLATE_SOURCE
            item["failure_category_source"] = "exec_agent.rule_classifier"
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
                    "suggestion": suggestion,
                    "applied": False,
                    "timestamp": _utc_now(),
                }
            )
            event_counter += 1

    assertion_results = _assertion_results_from_execution_results(
        all_results,
        list(state.get("merged_cases", []) or []),
    )
    report = {
        "run_id": run_id,
        "product_id": product_id,
        "timestamp": _utc_now(),
        "execution_entry": "exec_agent.local",
        "model_used": model_used,
        "assertion_template_source": _ASSERTION_TEMPLATE_SOURCE,
        "assertion_templates_used": used_templates,
        "failure_category_source": "exec_agent.rule_classifier",
        "governance_source": "state.governance_summary",
        "governance_warning_count": len(governance_summary.get("warnings", []) or []),
        "planned_page_key_count": int(governance_summary.get("summary", {}).get("total_page_keys", 0) or 0),
        "session_reused": bool(runtime_context.get("session_reused")),
        "summary": _build_summary(all_results),
        "results": all_results,
        "side_effect_probes": side_effect_probes,
        "cost_usd": None,
    }
    quarantine_report = build_quarantine_report(
        all_results,
        product_id=str(product_id),
        run_id=str(run_id),
    )
    report["quarantine_summary"] = quarantine_report["summary"]
    test_report_payload = _test_report_schema_payload(report)
    html_report = None
    if run_dir:
        html_report = str(
            generate_agent4_html_report(
                run_dir=run_dir,
                product_id=str(product_id),
                run_id=str(run_id),
                reports=[report],
            )
        )
        report["html_report"] = html_report
    teardown_report = finalize_runtime_context(
        root_dir=root_dir,
        product_id=str(product_id),
        runtime_context=runtime_context,
        reason="exec_complete",
        product_dir=artifact_product_dir,
    )
    for filename, payload in {
        "test-report.json": test_report_payload,
        "reports.json": test_report_payload,
        "reports-legacy.json": [report],
        "healing-events.json": healing_events,
        "teardown-report.json": teardown_report,
        "side-effect-probes.json": side_effect_probes,
        "quarantine.json": quarantine_report,
        "assertion-results.json": assertion_results,
    }.items():
        write_agent_json_artifact(
            root_dir=root_dir,
            product_id=str(product_id),
            agent_name="agent4",
            relative_path=filename,
            payload=payload,
            product_dir=artifact_product_dir,
        )
    existing_fingerprints = list(state.get("artifact_fingerprints", []) or [])
    new_fingerprints = [
        append_artifact_fingerprint(
            root_dir=root_dir,
            product_id=str(product_id),
            run_id=str(run_id),
            artifact_path=agent_artifact_path(
                str(product_id),
                "agent4",
                "test-report.json",
                root_dir=root_dir,
                product_dir=artifact_product_dir,
            ),
            artifact_type="test-report",
            payload=test_report_payload,
            producer="exec_healing_agent",
            model_routed=str(model_used),
            product_dir=artifact_product_dir,
        ),
        append_artifact_fingerprint(
            root_dir=root_dir,
            product_id=str(product_id),
            run_id=str(run_id),
            artifact_path=agent_artifact_path(
                str(product_id),
                "agent4",
                "quarantine.json",
                root_dir=root_dir,
                product_dir=artifact_product_dir,
            ),
            artifact_type="quarantine",
            payload=quarantine_report,
            producer="exec_healing_agent",
            model_routed=str(model_used),
            product_dir=artifact_product_dir,
        ),
        append_artifact_fingerprint(
            root_dir=root_dir,
            product_id=str(product_id),
            run_id=str(run_id),
            artifact_path=agent_artifact_path(
                str(product_id),
                "agent4",
                "healing-events.json",
                root_dir=root_dir,
                product_dir=artifact_product_dir,
            ),
            artifact_type="healing-events",
            payload=healing_events,
            producer="exec_healing_agent",
            model_routed=str(model_used),
            product_dir=artifact_product_dir,
        ),
    ]
    return {
        "reports": [report],
        "healing_events": healing_events,
        "teardown_report": teardown_report,
        "assertion_results": assertion_results,
        "quarantine_report": quarantine_report,
        "product_artifact_dir": str(artifact_product_dir),
        "artifact_fingerprints": existing_fingerprints + new_fingerprints,
        "html_report": html_report,
        "error": _exec_error_from_warnings(warnings, report),
    }


async def exec_healing_node(state: "E2EAgentState") -> dict:
    try:
        return await _exec_healing_node_impl(state)
    except Exception as exc:
        return {"error": f"exec_healing failed: {exc}"}

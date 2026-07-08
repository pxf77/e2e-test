from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse


_KEY_API_TOKENS = (
    "/api/apps/cps/insure/submit",
    "/api/apps/cps/insure/task/next/do",
    "/api/apps/cps/pay/bank/card/verif",
    "/api/apps/cps/insure/task/approve",
)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _jsonl_records(path: Path, *, limit: int = 80) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict):
                    records.append(record)
    except OSError:
        return []
    return records[-limit:]


def _clean_text(value: Any) -> str:
    text = str(value or "")
    if not text:
        return ""
    mojibake_tokens = ("绯荤粺", "闇€", "閿欒", "鏍镐繚")
    if any(token in text for token in mojibake_tokens):
        try:
            repaired = text.encode("gbk", errors="ignore").decode("utf-8", errors="ignore")
            if repaired.strip():
                return repaired
        except Exception:
            pass
    return text


def _html(value: Any) -> str:
    return escape(_clean_text(value), quote=True)


def _short_json(payload: Any, *, limit: int = 1200) -> str:
    if isinstance(payload, str):
        try:
            parsed = json.loads(payload)
        except Exception:
            text = payload
        else:
            text = json.dumps(parsed, ensure_ascii=False, indent=2)
    else:
        text = json.dumps(payload, ensure_ascii=False, indent=2)
    text = _clean_text(text)
    return text[:limit] + ("..." if len(text) > limit else "")


def _relative_path(path_value: Any, base_dir: Path) -> str:
    if not path_value:
        return ""
    path = Path(str(path_value))
    try:
        resolved_base = base_dir.resolve()
        if path.is_absolute():
            resolved_path = path.resolve()
        elif path.exists():
            resolved_path = path.resolve()
        else:
            resolved_path = (resolved_base / path).resolve()
        return os.path.relpath(resolved_path, resolved_base)
    except Exception:
        return str(path_value)


def _status_class(status: Any) -> str:
    text = str(status or "").lower()
    if text in {"passed", "success"}:
        return "passed"
    if text in {"failed", "fail", "error"}:
        return "failed"
    if text in {"skipped", "na", "missing"}:
        return "skipped"
    return "unknown"


def _first_report(reports: list[dict[str, Any]] | None) -> dict[str, Any]:
    if reports and isinstance(reports[0], dict):
        return reports[0]
    return {}


def _summary_from_reports(reports: list[dict[str, Any]] | None) -> dict[str, int]:
    report = _first_report(reports)
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    return {
        "total": int(summary.get("total", 0) or 0),
        "passed": int(summary.get("passed", 0) or 0),
        "failed": int(summary.get("failed", 0) or 0),
        "skipped": int(summary.get("skipped", 0) or 0),
        "error": int(summary.get("error", 0) or 0),
    }


def _results_from_reports(reports: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    report = _first_report(reports)
    results = report.get("results", [])
    return [
        _strip_agent3_submit_diagnostics(item)
        for item in results
        if isinstance(item, dict)
    ]


def _strip_agent3_submit_diagnostics(value: Any) -> Any:
    if isinstance(value, list):
        return [_strip_agent3_submit_diagnostics(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _strip_agent3_submit_diagnostics(item)
            for key, item in value.items()
            if key != "submit_diagnostics"
        }
    return value


def _collect_formal_screenshots(results: list[dict[str, Any]], base_dir: Path) -> list[dict[str, Any]]:
    screenshots: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    result_by_path: dict[str, dict[str, Any]] = {
        str(result.get("path_id") or ""): result
        for result in results
        if str(result.get("path_id") or "")
    }
    for result in results:
        for shot in result.get("screenshots", []) or []:
            if not isinstance(shot, dict):
                continue
            key = (
                str(result.get("path_id") or ""),
                str(shot.get("step") or ""),
                str(shot.get("path") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            screenshots.append(
                {
                    "case_id": result.get("case_id"),
                    "path_id": result.get("path_id"),
                    "step": shot.get("step"),
                    "label": shot.get("label"),
                    "url": shot.get("url"),
                    "path": shot.get("path"),
                    "relative_path": _relative_path(shot.get("path"), base_dir),
                }
            )

    tc_exec_dir = base_dir / "agent4" / "tc-exec"
    test_results_dirs: list[Path] = []
    legacy_test_results_dir = tc_exec_dir / "test-results"
    if legacy_test_results_dir.exists():
        test_results_dirs.append(legacy_test_results_dir)
    if tc_exec_dir.exists():
        for candidate in sorted(tc_exec_dir.rglob("test-results")):
            if candidate.is_dir() and candidate not in test_results_dirs:
                test_results_dirs.append(candidate)

    for test_results_dir in test_results_dirs:
        for path in sorted(test_results_dir.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                continue
            sidecar: dict[str, Any] = {}
            sidecar_path = path.with_suffix(".json")
            if sidecar_path.exists():
                try:
                    parsed = _read_json(sidecar_path)
                except Exception:
                    parsed = None
                if isinstance(parsed, dict):
                    sidecar = parsed
            relative = _relative_path(path, base_dir)
            key = ("artifact", "", relative)
            if key in seen:
                continue
            seen.add(key)
            path_match = re.search(r"PATH-\d+", str(path), flags=re.IGNORECASE)
            path_id = str(sidecar.get("path_id") or "").upper() or (path_match.group(0).upper() if path_match else "")
            result = result_by_path.get(path_id) or (results[0] if len(results) == 1 else {})
            screenshots.append(
                {
                    "case_id": result.get("case_id"),
                    "path_id": path_id or result.get("path_id"),
                    "step": sidecar.get("step") if sidecar else len(screenshots) + 1,
                    "label": sidecar.get("label") or path.stem,
                    "url": sidecar.get("url") or result.get("final_url") or "",
                    "path": str(path),
                    "relative_path": relative,
                    "phase": sidecar.get("phase"),
                    "action_text": sidecar.get("action_text"),
                    "planned_from_node_id": sidecar.get("planned_from_node_id"),
                    "planned_to_node_id": sidecar.get("planned_to_node_id"),
                }
            )
    return screenshots


def _collect_exploration_evidence(base_dir: Path) -> list[dict[str, Any]]:
    evidence_dir = base_dir / "submit-screenshots"
    if not evidence_dir.exists():
        return []
    evidence: list[dict[str, Any]] = []
    seen: set[str] = set()
    for state_path in sorted(evidence_dir.glob("*.json")):
        try:
            payload = _read_json(state_path)
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        screenshot = payload.get("screenshot")
        image_path = Path(str(screenshot)) if screenshot else state_path.with_suffix(".png")
        if not image_path.is_absolute():
            image_path = base_dir / image_path
        if not image_path.exists() or image_path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            continue
        relative = _relative_path(image_path, base_dir)
        if relative in seen:
            continue
        seen.add(relative)
        state = payload.get("state") if isinstance(payload.get("state"), dict) else {}
        evidence.append(
            {
                "source": "Agent3",
                "phase": payload.get("phase"),
                "action_text": payload.get("action_text"),
                "url": state.get("url") or payload.get("url"),
                "path": str(image_path),
                "relative_path": relative,
                "state_json": str(state_path),
                "state_relative_path": _relative_path(state_path, base_dir),
                "popups": state.get("popups") if isinstance(state.get("popups"), list) else [],
            }
        )
    return evidence


def _normalise_merged_cases(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("merged_cases", "cases", "test_cases", "items"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _collect_merged_cases(run_dir: Path) -> list[dict[str, Any]]:
    candidates = [
        run_dir / "agent1" / "merged-cases.json",
        run_dir / "merged-cases.json",
    ]
    if run_dir.parent.name == "runs":
        candidates.append(run_dir.parent.parent / "agent1" / "merged-cases.json")

    seen_paths: set[Path] = set()
    for path in candidates:
        resolved = path.resolve()
        if resolved in seen_paths:
            continue
        seen_paths.add(resolved)
        if not path.exists():
            continue
        try:
            return _normalise_merged_cases(_read_json(path))
        except Exception:
            continue
    return []


def _step_number(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _url_matches_pattern(url: Any, pattern: Any) -> bool:
    url_text = str(url or "")
    pattern_text = str(pattern or "")
    return bool(pattern_text and pattern_text in url_text)


def _node_from_url(nodes: list[dict[str, Any]], url: Any) -> str:
    for node in nodes:
        if _url_matches_pattern(url, node.get("url_pattern")):
            return str(node.get("node_id") or "")
    return ""


def _chain_node(
    *,
    node_id: str,
    page_key: Any = "",
    url_pattern: Any = "",
) -> dict[str, Any]:
    return {
        "node_id": node_id,
        "page_key": page_key,
        "url_pattern": url_pattern,
        "status": "pending",
        "actual_url": "",
        "actions": [],
        "screenshots": [],
    }


def _planned_nodes_from_result(result: dict[str, Any]) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for page in result.get("page_keys", []) or []:
        if not isinstance(page, dict):
            continue
        node_id = str(page.get("node_id") or "").strip()
        if not node_id or node_id in seen:
            continue
        seen.add(node_id)
        nodes.append(
            _chain_node(
                node_id=node_id,
                page_key=page.get("page_key"),
                url_pattern=page.get("url_pattern"),
            )
        )
    if nodes:
        return nodes

    for progress in result.get("node_progress", []) or []:
        if not isinstance(progress, dict):
            continue
        node_id = str(progress.get("node_id") or "").strip()
        if not node_id or node_id in seen:
            continue
        seen.add(node_id)
        nodes.append(
            _chain_node(
                node_id=node_id,
                page_key=progress.get("planned_page_key"),
                url_pattern=progress.get("planned_url_pattern"),
            )
        )
    return nodes


def _apply_node_progress(nodes: list[dict[str, Any]], result: dict[str, Any]) -> None:
    by_id = {str(node.get("node_id") or ""): node for node in nodes}
    for progress in result.get("node_progress", []) or []:
        if not isinstance(progress, dict):
            continue
        node = by_id.get(str(progress.get("node_id") or ""))
        if not node:
            continue
        node["status"] = progress.get("status") or node["status"]
        node["actual_url"] = progress.get("actual_url") or node["actual_url"]
        if progress.get("blocked_reason"):
            node["blocked_reason"] = progress.get("blocked_reason")


def _append_action_to_chain(nodes: list[dict[str, Any]], action: dict[str, Any]) -> None:
    if not nodes:
        return
    target_node = str(action.get("planned_to_node_id") or "").strip()
    if not target_node:
        target_node = _node_from_url(nodes, action.get("target_url"))
    if not target_node:
        target_node = str(action.get("planned_from_node_id") or "").strip()
    node = next((item for item in nodes if item.get("node_id") == target_node), nodes[0])
    node["actions"].append(
        {
            "step": action.get("step"),
            "text": action.get("text") or action.get("selector"),
            "source_url": action.get("source_url"),
            "target_url": action.get("target_url"),
            "click_strategy": action.get("click_strategy"),
        }
    )
    node["status"] = "executed" if node.get("status") == "pending" else node.get("status")
    if action.get("target_url"):
        node["actual_url"] = action.get("target_url")


def _append_screenshot_to_chain(
    nodes: list[dict[str, Any]],
    screenshot: dict[str, Any],
    action_by_step: dict[int, dict[str, Any]],
    result: dict[str, Any],
    *,
    source: str,
) -> None:
    if not nodes:
        return
    step = _step_number(screenshot.get("step"))
    label = str(screenshot.get("label") or screenshot.get("phase") or "")
    node_by_id = {str(node.get("node_id") or ""): node for node in nodes}
    node_id = ""
    planned_to_node_id = _normalise_screenshot_target_node_id(screenshot, result, node_by_id)
    if planned_to_node_id == "NODE-policy-result" and _is_payment_gateway_handoff_screenshot(screenshot):
        node_id = "NODE-payment" if "NODE-payment" in node_by_id else ""
    elif planned_to_node_id and planned_to_node_id in node_by_id:
        node_id = planned_to_node_id
    elif step == 0 or label == "initial-page":
        node_id = str(nodes[0].get("node_id") or "")
    elif "error" in label.lower() and result.get("blocked_node"):
        node_id = str(result.get("blocked_node") or "")
    elif step is not None and step in action_by_step:
        action = action_by_step[step]
        node_id = str(action.get("planned_to_node_id") or "") or _node_from_url(nodes, action.get("target_url"))
    if not node_id:
        node_id = _node_from_url(nodes, screenshot.get("url"))
    if not node_id and "error" in label.lower():
        node_id = str(result.get("target_node") or result.get("reached_target_node") or "")
    if node_id == "NODE-policy-result" and _is_payment_gateway_handoff_screenshot(screenshot):
        node_id = "NODE-payment" if "NODE-payment" in node_by_id else ""
    if not node_id:
        return
    node = node_by_id.get(node_id) or nodes[-1]
    node["screenshots"].append(
        {
            "source": source,
            "step": screenshot.get("step"),
            "label": screenshot.get("label") or screenshot.get("phase"),
            "url": screenshot.get("url"),
            "relative_path": screenshot.get("relative_path"),
        }
    )
    if screenshot.get("url"):
        node["actual_url"] = screenshot.get("url")


def _url_path(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return urlparse(text).path.rstrip("/")
    except Exception:
        return ""


def _screenshot_ref(screenshot: dict[str, Any], *, source: str) -> dict[str, Any]:
    return {
        "source": source,
        "step": screenshot.get("step"),
        "label": screenshot.get("label") or screenshot.get("phase"),
        "url": screenshot.get("url"),
        "relative_path": screenshot.get("relative_path"),
    }


def _normalise_screenshot_target_node_id(
    screenshot: dict[str, Any],
    result: dict[str, Any],
    node_by_id: dict[str, dict[str, Any]],
) -> str:
    planned_to_node_id = str(screenshot.get("planned_to_node_id") or "").strip()
    if (
        planned_to_node_id == "NODE-policy-service"
        and "NODE-policy-result" in node_by_id
        and _looks_like_policy_result_screenshot(screenshot, result)
    ):
        return "NODE-policy-result"
    return planned_to_node_id


def _looks_like_policy_result_screenshot(screenshot: dict[str, Any], result: dict[str, Any]) -> bool:
    label = str(screenshot.get("label") or screenshot.get("phase") or "").lower()
    if not any(token in label for token in ("final", "result", "success", "closed-loop")):
        return False
    url = str(screenshot.get("url") or "").lower()
    if _is_payment_gateway_handoff_screenshot(screenshot):
        return False
    if any(token in url for token in ("pay/success", "/result", "success")):
        return True
    payment_closed_loop = result.get("payment_closed_loop")
    if isinstance(payment_closed_loop, dict):
        status = str(payment_closed_loop.get("status") or "").lower()
        issue_status = payment_closed_loop.get("issue_status")
        if status.startswith("passed") or issue_status in {1, "1"}:
            return True
    for operation in result.get("external_operations", []) or []:
        if not isinstance(operation, dict):
            continue
        if operation.get("issue_status") in {1, "1"}:
            return True
    return False


def _is_payment_gateway_handoff_screenshot(screenshot: dict[str, Any]) -> bool:
    url = str(screenshot.get("url") or "").lower()
    return (
        "checkmweb" in url
        or "wx.tenpay.com" in url
        or "/v2/return/wechat" in url
        or ("wechat" in url and any(token in url for token in ("return", "callback", "gateway", "payment")))
    )


def _backfill_matched_node_screenshots(nodes: list[dict[str, Any]], screenshots: list[dict[str, Any]]) -> None:
    if not nodes or not screenshots:
        return
    screenshots_by_path: dict[str, list[dict[str, Any]]] = {}
    for screenshot in screenshots:
        path = _url_path(screenshot.get("url"))
        if path:
            screenshots_by_path.setdefault(path, []).append(screenshot)

    for node in nodes:
        if node.get("status") != "matched" or node.get("screenshots"):
            continue
        candidates: list[dict[str, Any]] = []
        actual_path = _url_path(node.get("actual_url"))
        if actual_path:
            candidates.extend(screenshots_by_path.get(actual_path, []))
        if not candidates:
            pattern = str(node.get("url_pattern") or "").rstrip("/")
            candidates.extend(
                screenshot
                for screenshot in screenshots
                if pattern and _url_matches_pattern(screenshot.get("url"), pattern)
            )
        if not candidates:
            continue
        if str(node.get("node_id") or "") == "NODE-policy-result":
            candidates = [screenshot for screenshot in candidates if _is_policy_result_screenshot_url(screenshot)]
            if not candidates:
                continue
        candidate = candidates[-1]
        node["screenshots"].append(_screenshot_ref(candidate, source="Agent4-backfill"))
        if not node.get("actual_url") and candidate.get("url"):
            node["actual_url"] = candidate.get("url")


def _is_policy_result_screenshot_url(screenshot: dict[str, Any]) -> bool:
    if _is_payment_gateway_handoff_screenshot(screenshot):
        return False
    url = str(screenshot.get("url") or "").lower()
    return any(token in url for token in ("pay/success", "/result", "success"))


def _backfill_same_planned_page_screenshots(nodes: list[dict[str, Any]]) -> None:
    for node in nodes:
        if node.get("screenshots"):
            continue
        page_key = str(node.get("page_key") or "")
        url_pattern = str(node.get("url_pattern") or "")
        if not page_key or not url_pattern:
            continue
        candidates = [
            other
            for other in nodes
            if other is not node
            and str(other.get("page_key") or "") == page_key
            and str(other.get("url_pattern") or "") == url_pattern
            and other.get("screenshots")
        ]
        if not candidates:
            continue
        donor = candidates[-1]
        candidate = (donor.get("screenshots") or [])[-1]
        if isinstance(candidate, dict):
            node["screenshots"].append(
                {
                    "source": "Agent4-backfill",
                    "step": candidate.get("step"),
                    "label": candidate.get("label"),
                    "url": candidate.get("url"),
                    "relative_path": candidate.get("relative_path"),
                }
            )
            if not node.get("actual_url"):
                node["actual_url"] = donor.get("actual_url") or candidate.get("url") or ""
            if node.get("status") == "pending":
                node["status"] = "matched"


def _node_has_observed_replay_evidence(node: dict[str, Any]) -> bool:
    if node.get("actions") or node.get("screenshots") or node.get("blocked_reason"):
        return True
    return str(node.get("status") or "") not in {"", "pending", "unknown"}


def _filter_unobserved_planned_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    policy_result_observed = any(
        str(node.get("node_id") or "") == "NODE-policy-result"
        and _node_has_observed_replay_evidence(node)
        for node in nodes
    )
    for index, node in enumerate(nodes):
        if _node_has_observed_replay_evidence(node):
            filtered.append(node)
            continue
        node_id = str(node.get("node_id") or "")
        if node_id == "NODE-health-notice" and _is_unobserved_static_health_notice_placeholder(nodes, index):
            continue
        if node_id == "NODE-policy-service" and policy_result_observed:
            continue
        filtered.append(node)
    return filtered


def _is_unobserved_static_health_notice_placeholder(nodes: list[dict[str, Any]], index: int) -> bool:
    node = nodes[index]
    if str(node.get("page_key") or "") != "PK-insure-health-notice":
        return False
    if str(node.get("url_pattern") or "").rstrip("/") != "/insure/health-notice":
        return False
    return any(_node_has_observed_replay_evidence(other) for other in nodes[index + 1 :])


def _is_static_health_notice_page_key(page: dict[str, Any]) -> bool:
    return (
        str(page.get("node_id") or "") == "NODE-health-notice"
        and str(page.get("page_key") or "") == "PK-insure-health-notice"
        and str(page.get("url_pattern") or "").rstrip("/") == "/insure/health-notice"
    )


def _drop_health_notice_route_token(value: Any) -> Any:
    if isinstance(value, list):
        return [
            _drop_health_notice_route_token(item)
            for item in value
            if str(item) != "NODE-health-notice"
        ]
    if isinstance(value, dict):
        return {
            key: _drop_health_notice_route_token(item)
            for key, item in value.items()
        }
    return value


def _copy_result_without_unobserved_static_health_notice(
    result: dict[str, Any],
    observed_nodes_by_path: Mapping[str, set[str]],
) -> dict[str, Any]:
    path_id = str(result.get("path_id") or "PATH-UNKNOWN")
    observed_nodes = observed_nodes_by_path.get(path_id, set())
    if "NODE-health-notice" in observed_nodes:
        return result
    if _has_actual_health_notice_evidence(result):
        return result
    changed = False
    copied = dict(result)
    page_keys = result.get("page_keys")
    if isinstance(page_keys, list):
        filtered_page_keys = [
            page
            for page in page_keys
            if not (isinstance(page, dict) and _is_static_health_notice_page_key(page))
        ]
        if len(filtered_page_keys) != len(page_keys):
            copied["page_keys"] = filtered_page_keys
            changed = True
    node_progress = result.get("node_progress")
    if isinstance(node_progress, list):
        filtered_progress = [
            progress
            for progress in node_progress
            if not (isinstance(progress, dict) and str(progress.get("node_id") or "") == "NODE-health-notice")
        ]
        if len(filtered_progress) != len(node_progress):
            copied["node_progress"] = _drop_health_notice_route_token(filtered_progress)
            changed = True
        elif any("NODE-health-notice" in json.dumps(progress, ensure_ascii=False) for progress in node_progress if isinstance(progress, dict)):
            copied["node_progress"] = _drop_health_notice_route_token(node_progress)
            changed = True
    return copied if changed else result


def _results_without_unobserved_static_health_notice(
    results: list[dict[str, Any]],
    path_chains: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    observed_nodes_by_path = {
        str(chain.get("path_id") or "PATH-UNKNOWN"): {
            str(node.get("node_id") or "")
            for node in chain.get("nodes", []) or []
            if isinstance(node, dict)
        }
        for chain in path_chains
        if isinstance(chain, dict)
    }
    return [
        _copy_result_without_unobserved_static_health_notice(result, observed_nodes_by_path)
        for result in results
    ]


def _normalise_chain_target_node(result: dict[str, Any], nodes: list[dict[str, Any]]) -> str:
    target_node = str(result.get("target_node") or result.get("reached_target_node") or "")
    if target_node != "NODE-policy-service":
        return target_node
    node_ids = {str(node.get("node_id") or "") for node in nodes}
    if "NODE-policy-service" not in node_ids and "NODE-policy-result" in node_ids:
        return "NODE-policy-result"
    return target_node


def _build_path_chains(
    *,
    results: list[dict[str, Any]],
    formal_screenshots: list[dict[str, Any]],
    blocked_paths: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        path_id = str(result.get("path_id") or "PATH-UNKNOWN")
        grouped.setdefault(path_id, []).append(result)
    for blocked in blocked_paths or []:
        if isinstance(blocked, dict):
            path_id = str(blocked.get("path_id") or "PATH-UNKNOWN")
            grouped.setdefault(path_id, [])

    chains: list[dict[str, Any]] = []
    for path_id, path_results in grouped.items():
        result = path_results[0] if path_results else {}
        nodes = _planned_nodes_from_result(result)
        if not nodes and blocked_paths:
            blocked = next((item for item in blocked_paths if isinstance(item, dict) and str(item.get("path_id") or "") == path_id), {})
            blocked_node = str(blocked.get("blocked_node") or "blocked")
            nodes = [_chain_node(node_id=blocked_node, page_key="blocked", url_pattern="")]
        _apply_node_progress(nodes, result)

        actions = [item for item in result.get("executed_actions", []) or [] if isinstance(item, dict)]
        action_by_step = {
            step: action
            for action in actions
            if (step := _step_number(action.get("step"))) is not None
        }
        for action in actions:
            _append_action_to_chain(nodes, action)

        path_screenshots: list[dict[str, Any]] = []
        for screenshot in formal_screenshots:
            if str(screenshot.get("path_id") or "") != path_id:
                continue
            path_screenshots.append(screenshot)
            _append_screenshot_to_chain(nodes, screenshot, action_by_step, result, source="Agent4")
        _backfill_matched_node_screenshots(nodes, path_screenshots)
        _backfill_same_planned_page_screenshots(nodes)
        if path_results:
            nodes = _filter_unobserved_planned_nodes(nodes)

        chains.append(
            {
                "path_id": path_id,
                "case_ids": sorted({str(item.get("case_id")) for item in path_results if item.get("case_id")}),
                "status": result.get("status") or ("blocked" if not path_results else "unknown"),
                "execution_status": result.get("execution_status"),
                "target_node": _normalise_chain_target_node(result, nodes),
                "final_url": result.get("final_url"),
                "blocked_reason": result.get("blocked_reason") or result.get("error_message"),
                "nodes": nodes,
            }
        )
    return chains


def _body_json(record: dict[str, Any]) -> dict[str, Any] | None:
    body = record.get("body")
    if not isinstance(body, str) or not body:
        return None
    try:
        parsed = json.loads(body)
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def _api_summary(record: dict[str, Any]) -> str:
    payload = _body_json(record)
    if not payload:
        return _short_json(record.get("body") or record, limit=500)
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    parts = [
        f"code={payload.get('code')}",
        f"success={payload.get('success')}",
    ]
    if data:
        for key in ("taskType", "taskStatus", "canPay", "errorCode"):
            if key in data:
                parts.append(f"{key}={data.get(key)}")
    message = payload.get("msg") or payload.get("message")
    if message:
        parts.append(f"msg={_clean_text(message)}")
    return ", ".join(parts)


def _collect_api_records(run_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    error_records = _jsonl_records(run_dir / "api-errors.jsonl", limit=80)
    trace_records = _jsonl_records(run_dir / "api-trace.jsonl", limit=300)
    key_records: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for record in [*error_records, *trace_records]:
        url = str(record.get("url") or "")
        body = str(record.get("body") or "")
        if not any(token in url for token in _KEY_API_TOKENS):
            continue
        if record.get("event") != "response":
            continue
        if record in key_records:
            continue
        key = (url, body[:300])
        if key in seen:
            continue
        seen.add(key)
        enriched = {
            **record,
            "summary": _api_summary(record),
            "body_pretty": _short_json(body or record, limit=1600),
        }
        key_records.append(enriched)
    return key_records[-40:], error_records


def _blocking_reason(results: list[dict[str, Any]], api_records: list[dict[str, Any]], blocked_paths: list[dict[str, Any]] | None) -> str:
    for result in results:
        for key in ("error_message", "blocked_reason", "body_excerpt"):
            value = result.get(key)
            if value:
                return _clean_text(value)
    if blocked_paths:
        first = blocked_paths[0]
        return _clean_text(first.get("blocked_reason") or first.get("reason") or first)
    for record in api_records:
        summary = str(record.get("summary") or "")
        if "41011" in summary or "41001" in summary or "system error" in summary.lower() or "系统错误" in _clean_text(summary):
            return _clean_text(summary)
    return ""


def _render_summary_cards(summary: dict[str, int]) -> str:
    cards = []
    for key in ("total", "passed", "failed", "skipped", "error"):
        cards.append(f"<div class='card {key}'><b>{summary.get(key, 0)}</b><span>{key}</span></div>")
    return "\n".join(cards)


_CASE_INTENT_TARGET_NODES = {
    "health_notice": "NODE-health-notice",
    "tax_identity": "NODE-insure-form",
    "underwriting": "NODE-underwriting",
    "payment": "NODE-payment",
    "policy": "NODE-policy-result",
    "surrender": "NODE-policy-result",
    "main_flow": "NODE-policy-result",
}

_ORDER_GENERATION_BOUNDARY_NODES = {
    "NODE-premium-calculation",
    "NODE-suitability",
    "NODE-health-notice",
    "NODE-insure-form",
    "NODE-underwriting",
    "NODE-risk-control",
    "NODE-payment",
    "NODE-policy-result",
    "NODE-policy-service",
}


def _passed_order_generation_boundary(result: dict[str, Any]) -> bool:
    reached_node = str(result.get("reached_target_node") or result.get("target_node") or "")
    return (
        str(result.get("status") or "") == "passed"
        and str(result.get("execution_status") or "") == "passed"
        and str(result.get("target_node_status") or "") == "reached"
        and str(result.get("target_node_inference") or "") == "agent3.order_generation_boundary"
        and not _has_unresolved_task_handoff(result)
        and reached_node in _ORDER_GENERATION_BOUNDARY_NODES
    )


def _has_unresolved_task_handoff(result: dict[str, Any]) -> bool:
    for action in result.get("executed_actions", []) or []:
        if not isinstance(action, dict):
            continue
        submit_api_result = action.get("submit_api_result")
        if not isinstance(submit_api_result, dict):
            continue
        if not submit_api_result.get("task_handoff") or submit_api_result.get("direct_order"):
            continue
        auth_handoff_result = action.get("auth_handoff_result")
        if isinstance(auth_handoff_result, dict) and auth_handoff_result.get("completed"):
            continue
        suitability_recovery = action.get("submit_suitability_recovery")
        if (
            submit_api_result.get("suitability_task")
            and isinstance(suitability_recovery, dict)
            and suitability_recovery.get("recovered")
        ):
            continue
        return True
    return False


def _case_id(case: dict[str, Any]) -> str:
    return str(case.get("case_id") or case.get("id") or "").strip()


def _case_target_node(case: dict[str, Any]) -> str:
    explicit = str(case.get("target_node") or case.get("target_node_id") or "").strip()
    if explicit:
        return explicit
    intent = str(case.get("business_intent") or case.get("scenario_type") or "").strip()
    return _CASE_INTENT_TARGET_NODES.get(intent, "NODE-policy-result")


def _node_reached_in_result(result: dict[str, Any], node_id: str) -> bool:
    if not node_id:
        return False
    if _has_unresolved_task_handoff(result) and node_id in {
        "NODE-underwriting",
        "NODE-risk-control",
        "NODE-payment",
        "NODE-policy-result",
        "NODE-policy-service",
    }:
        return False
    if result.get("reached_target_node") == node_id and result.get("target_node_status") == "reached":
        return True
    if node_id in _ORDER_GENERATION_BOUNDARY_NODES and _passed_order_generation_boundary(result):
        return True
    for action in result.get("executed_actions", []) or []:
        if not isinstance(action, dict):
            continue
        if str(action.get("planned_to_node_id") or "") == node_id:
            return True
        if _url_matches_pattern(action.get("target_url"), "healthInform") and node_id == "NODE-health-notice":
            return True
        if _url_matches_pattern(action.get("target_url"), "product/insure") and node_id == "NODE-insure-form":
            return True
    for match in result.get("node_matches", []) or []:
        if not isinstance(match, dict):
            continue
        matched_nodes = match.get("matched_nodes")
        if isinstance(matched_nodes, list) and node_id in matched_nodes:
            return True
    for progress in result.get("node_progress", []) or []:
        if not isinstance(progress, dict):
            continue
        if str(progress.get("node_id") or "") == node_id and str(progress.get("status") or "") in {
            "passed",
            "reached",
            "executed",
            "completed",
        }:
            return True
        if (
            str(progress.get("node_id") or "") == node_id
            and str(progress.get("status") or "") == "matched"
            and not progress.get("blocked_reason")
        ):
            return True
    if node_id == "NODE-health-notice" and _url_matches_pattern(result.get("final_url"), "healthInform"):
        return True
    if node_id == "NODE-insure-form" and _url_matches_pattern(result.get("final_url"), "product/insure"):
        return True
    evidence_text = _clean_text(
        "\n".join(
            str(result.get(key) or "")
            for key in ("body_excerpt", "error_message", "blocked_reason")
        )
    )
    if node_id == "NODE-health-notice" and (
        "product/insure" in evidence_text
        or "isHealthSuccess=true" in evidence_text
        or ("投保人信息" in evidence_text and "提交订单" in evidence_text)
        or ("投保人信息" in evidence_text and "起保日期" in evidence_text)
    ):
        return True
    if node_id == "NODE-insure-form" and (
        "product/insure" in evidence_text
        or ("投保人信息" in evidence_text and "提交订单" in evidence_text)
        or ("投保人信息" in evidence_text and "起保日期" in evidence_text)
    ):
        return True
    return False


def _has_actual_health_notice_evidence(result: dict[str, Any]) -> bool:
    health_patterns = ("healthInform", "/insure/health-notice", "insure/health-notice")

    def matches_health_url(value: Any) -> bool:
        return any(_url_matches_pattern(value, pattern) for pattern in health_patterns)

    if matches_health_url(result.get("final_url")):
        return True
    for action in result.get("executed_actions", []) or []:
        if not isinstance(action, dict):
            continue
        if matches_health_url(action.get("source_url")) or matches_health_url(action.get("target_url")):
            return True
    for match in result.get("node_matches", []) or []:
        if not isinstance(match, dict):
            continue
        matched_nodes = match.get("matched_nodes")
        if isinstance(matched_nodes, list) and "NODE-health-notice" in matched_nodes and (
            matches_health_url(match.get("url"))
            or matches_health_url(match.get("actual_url"))
            or matches_health_url(match.get("target_url"))
        ):
            return True
    for progress in result.get("node_progress", []) or []:
        if not isinstance(progress, dict):
            continue
        if str(progress.get("node_id") or "") != "NODE-health-notice":
            continue
        if str(progress.get("status") or "") not in {
            "matched",
            "passed",
            "reached",
            "executed",
            "completed",
        }:
            continue
        if matches_health_url(progress.get("actual_url")) or matches_health_url(progress.get("url")):
            return True
    return False


def _case_display_target_node(target_node: str, case_results: list[dict[str, Any]]) -> str:
    if target_node != "NODE-health-notice":
        return target_node
    if any(_has_actual_health_notice_evidence(result) for result in case_results):
        return target_node
    for fallback_node in ("NODE-insure-form", "NODE-suitability", "NODE-policy-result"):
        if any(_node_reached_in_result(result, fallback_node) for result in case_results):
            return fallback_node
    return target_node


def _derive_merged_case_statuses(
    merged_cases: list[dict[str, Any]],
    results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_case: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        case_id = str(result.get("case_id") or "").strip()
        if case_id:
            by_case.setdefault(case_id, []).append(result)

    statuses: list[dict[str, Any]] = []
    for case in merged_cases:
        case_id = _case_id(case)
        target_node = _case_target_node(case)
        case_results = by_case.get(case_id, [])
        reached = any(_node_reached_in_result(result, target_node) for result in case_results)
        status = "passed" if reached else "failed" if case_results else "skipped"
        display_target_node = _case_display_target_node(target_node, case_results) if reached else target_node
        statuses.append(
            {
                "case_id": case_id,
                "status": status,
                "target_node": display_target_node,
                "evidence": "target node reached" if reached else "target node not reached",
            }
        )
    return statuses


def _summary_from_case_statuses(statuses: list[dict[str, Any]]) -> dict[str, int]:
    summary = {"total": len(statuses), "passed": 0, "failed": 0, "skipped": 0, "error": 0}
    for item in statuses:
        status = str(item.get("status") or "failed")
        if status in summary:
            summary[status] += 1
        else:
            summary["failed"] += 1
    return summary


def _case_status_lookup(statuses: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(item.get("case_id") or ""): item for item in statuses}


def _render_results(results: list[dict[str, Any]]) -> str:
    if not results:
        return "<p class='muted'>No Agent4 execution results were recorded.</p>"
    rows = []
    for result in results:
        status = result.get("status")
        rows.append(
            "<tr>"
            f"<td>{_html(result.get('path_id'))}</td>"
            f"<td>{_html(result.get('case_id'))}</td>"
            f"<td><span class='pill {_status_class(status)}'>{_html(status)}</span></td>"
            f"<td>{_html(result.get('execution_status'))}</td>"
            f"<td>{_html(result.get('final_url'))}</td>"
            f"<td>{_html(result.get('blocked_reason') or result.get('error_message'))}</td>"
            "</tr>"
        )
    return (
        "<table><thead><tr><th>Path</th><th>Case</th><th>Status</th><th>Execution</th>"
        "<th>Final URL</th><th>Blocking / Error</th></tr></thead><tbody>"
        + "\n".join(rows)
        + "</tbody></table>"
    )


def _list_text(value: Any, *, limit: int = 4) -> str:
    if isinstance(value, list):
        parts: list[str] = []
        for item in value[:limit]:
            if isinstance(item, dict):
                parts.append(_clean_text(item.get("case_id") or item.get("id") or item.get("title") or item))
            else:
                parts.append(_clean_text(item))
        if len(value) > limit:
            parts.append(f"+{len(value) - limit}")
        return ", ".join(part for part in parts if part)
    return _clean_text(value)


def _render_merged_cases(merged_cases: list[dict[str, Any]]) -> str:
    return _render_merged_cases_with_statuses(merged_cases, [])


def _render_merged_cases_with_statuses(
    merged_cases: list[dict[str, Any]],
    statuses: list[dict[str, Any]],
) -> str:
    if not merged_cases:
        return "<p class='muted'>No merged test cases were found for this run.</p>"
    status_by_case = _case_status_lookup(statuses)
    rows = []
    for case in merged_cases:
        case_id = case.get("case_id") or case.get("id")
        case_status = status_by_case.get(str(case_id or ""), {})
        status = case_status.get("status") or ""
        rows.append(
            "<tr>"
            f"<td>{_html(case_id)}</td>"
            f"<td><span class='pill {_status_class(status)}'>{_html(status)}</span></td>"
            f"<td>{_html(case_status.get('target_node'))}</td>"
            f"<td>{_html(case.get('title') or case.get('name') or case.get('business_goal'))}</td>"
            f"<td>{_html(case.get('priority'))}</td>"
            f"<td>{_html(case.get('business_intent') or case.get('scenario_type'))}</td>"
            f"<td>{_html(_list_text(case.get('steps')))}</td>"
            f"<td>{_html(_list_text(case.get('coverage_refs') or case.get('source_case_ids') or case.get('manual_case_refs')))}</td>"
            "</tr>"
        )
    return (
        "<table><thead><tr><th>Case</th><th>Status</th><th>Target Node</th><th>Title</th><th>Priority</th><th>Intent</th>"
        "<th>Key Steps</th><th>Coverage</th></tr></thead><tbody>"
        + "\n".join(rows)
        + "</tbody></table>"
    )


def _render_actions(results: list[dict[str, Any]]) -> str:
    actions: list[dict[str, Any]] = []
    for result in results:
        for action in result.get("executed_actions", []) or []:
            if isinstance(action, dict):
                actions.append({**action, "case_id": result.get("case_id"), "path_id": result.get("path_id")})
    if not actions:
        return "<p class='muted'>No Agent4 action log was attached to results.</p>"
    rows = []
    for action in actions:
        rows.append(
            "<tr>"
            f"<td>{_html(action.get('path_id'))}</td>"
            f"<td>{_html(action.get('step'))}</td>"
            f"<td>{_html(action.get('text') or action.get('selector'))}</td>"
            f"<td>{_html(action.get('source_url'))}</td>"
            f"<td>{_html(action.get('target_url'))}</td>"
            "</tr>"
        )
    return "<table><thead><tr><th>Path</th><th>Step</th><th>Action</th><th>From</th><th>To</th></tr></thead><tbody>" + "\n".join(rows) + "</tbody></table>"


def _render_screenshot_grid(items: list[dict[str, Any]], *, title_key: str = "label") -> str:
    if not items:
        return "<p class='muted'>No screenshots were recorded.</p>"
    cards = []
    for item in items:
        path = str(item.get("relative_path") or "")
        meta = " | ".join(
            part
            for part in (
                f"path={_clean_text(item.get('path_id'))}" if item.get("path_id") else "",
                f"case={_clean_text(item.get('case_id'))}" if item.get("case_id") else "",
                f"step={_clean_text(item.get('step'))}" if item.get("step") is not None else "",
                _clean_text(item.get("phase")),
            )
            if part
        )
        popup_text = ""
        popups = item.get("popups")
        if isinstance(popups, list) and popups:
            popup_text = "; ".join(_clean_text(popup.get("text")) for popup in popups if isinstance(popup, dict))[:500]
        cards.append(
            "<figure class='shot'>"
            f"<figcaption><b>{_html(item.get(title_key) or item.get('action_text') or item.get('phase'))}</b>"
            f"<span>{_html(meta)}</span></figcaption>"
            f"<a href='{_html(path)}'><img src='{_html(path)}' alt='screenshot'></a>"
            f"<p>{_html(item.get('url'))}</p>"
            + (f"<p class='error-text'>{_html(popup_text)}</p>" if popup_text else "")
            + "</figure>"
        )
    return "<div class='shot-grid'>" + "\n".join(cards) + "</div>"


def _render_path_chains(path_chains: list[dict[str, Any]]) -> str:
    if not path_chains:
        return "<p class='muted'>No path chain data was recorded.</p>"
    blocks = []
    for chain in path_chains:
        nodes_html = []
        for index, node in enumerate(chain.get("nodes", []) or []):
            action_items = []
            for action in node.get("actions", []) or []:
                action_items.append(
                    "<li>"
                    f"<b>step {_html(action.get('step'))}</b> {_html(action.get('text'))}"
                    f"<small>{_html(action.get('source_url'))} -> {_html(action.get('target_url'))}</small>"
                    + (f"<em>{_html(action.get('click_strategy'))}</em>" if action.get("click_strategy") else "")
                    + "</li>"
                )
            screenshots = []
            for shot in node.get("screenshots", []) or []:
                path = str(shot.get("relative_path") or "")
                if not path:
                    continue
                screenshots.append(
                    "<figure class='chain-shot'>"
                    f"<a href='{_html(path)}'><img src='{_html(path)}' alt='path screenshot'></a>"
                    f"<figcaption>{_html(shot.get('source'))} | {_html(shot.get('label'))}</figcaption>"
                    "</figure>"
                )
            nodes_html.append(
                "<article class='chain-node'>"
                f"<div class='node-index'>{index + 1}</div>"
                "<div class='node-body'>"
                f"<h3>{_html(node.get('node_id'))}</h3>"
                f"<p><b>{_html(node.get('page_key'))}</b> <span>{_html(node.get('url_pattern'))}</span></p>"
                f"<p class='node-status'>{_html(node.get('status'))}</p>"
                + (f"<p class='node-url'>{_html(node.get('actual_url'))}</p>" if node.get("actual_url") else "")
                + (f"<p class='error-text'>{_html(node.get('blocked_reason'))}</p>" if node.get("blocked_reason") else "")
                + ("<ul class='node-actions'>" + "".join(action_items) + "</ul>" if action_items else "<p class='muted'>No action recorded on this page.</p>")
                + ("<div class='chain-shots'>" + "".join(screenshots) + "</div>" if screenshots else "<p class='muted'>No screenshot mapped to this page.</p>")
                + "</div>"
                "</article>"
            )
        blocks.append(
            "<details class='path-chain' open>"
            f"<summary><b>{_html(chain.get('path_id'))}</b>"
            f"<span>{_html(', '.join(chain.get('case_ids', []) or []))}</span>"
            f"<span class='pill {_status_class(chain.get('status'))}'>{_html(chain.get('status'))}</span>"
            "</summary>"
            f"<p class='chain-meta'>target={_html(chain.get('target_node'))} | final={_html(chain.get('final_url'))}</p>"
            + (f"<p class='error-text'>{_html(chain.get('blocked_reason'))}</p>" if chain.get("blocked_reason") else "")
            + "<div class='chain-nodes'>"
            + "".join(nodes_html)
            + "</div></details>"
        )
    return "\n".join(blocks)


def _render_api_records(records: list[dict[str, Any]]) -> str:
    if not records:
        return "<p class='muted'>No key API responses were captured for this run.</p>"
    blocks = []
    for record in records:
        summary = _clean_text(record.get("summary"))
        severity = "api failed" if any(token in summary for token in ("41011", "41001", "37002", "success=False", "success=false")) else "api"
        blocks.append(
            f"<details class='{severity}' open>"
            f"<summary>{_html(summary)}<br><small>{_html(record.get('url'))}</small></summary>"
            f"<pre>{_html(record.get('body_pretty'))}</pre>"
            "</details>"
        )
    return "\n".join(blocks)


def _empty_side_effect_probe_report() -> dict[str, Any]:
    return {"summary": {"total": 0, "success": 0, "fail": 0, "na": 0}, "results": []}


def _normalise_side_effect_probe_report(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return _empty_side_effect_probe_report()
    raw_results = payload.get("results", [])
    results = [item for item in raw_results if isinstance(item, dict)] if isinstance(raw_results, list) else []
    raw_summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    summary = {
        "total": int(raw_summary.get("total", len(results)) or 0),
        "success": int(raw_summary.get("success", 0) or 0),
        "fail": int(raw_summary.get("fail", 0) or 0),
        "na": int(raw_summary.get("na", 0) or 0),
    }
    if not raw_summary and results:
        summary = {"total": len(results), "success": 0, "fail": 0, "na": 0}
        for result in results:
            status = str(result.get("status") or "na")
            summary[status if status in {"success", "fail", "na"} else "na"] += 1
    return {"summary": summary, "results": results}


def _collect_side_effect_probes(
    run_dir: Path,
    reports: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    for report in reports or []:
        if isinstance(report, dict) and isinstance(report.get("side_effect_probes"), dict):
            return _normalise_side_effect_probe_report(report.get("side_effect_probes"))

    path = run_dir / "side-effect-probes.json"
    if path.exists():
        try:
            return _normalise_side_effect_probe_report(_read_json(path))
        except Exception:
            return _empty_side_effect_probe_report()
    return _empty_side_effect_probe_report()


def _render_side_effect_probes(report: dict[str, Any]) -> str:
    results = report.get("results", []) if isinstance(report, dict) else []
    if not results:
        return "<p class='muted'>No Side-effect Probe results were recorded.</p>"
    rows = []
    for result in results:
        status = result.get("status")
        rows.append(
            "<tr>"
            f"<td>{_html(result.get('probe_id'))}</td>"
            f"<td><span class='pill {_status_class(status)}'>{_html(status)}</span></td>"
            f"<td><pre>{_html(_short_json(result.get('evidence') or {}, limit=500))}</pre></td>"
            f"<td><pre>{_html(_short_json(result.get('failures') or [], limit=500))}</pre></td>"
            f"<td>{_html(result.get('downgrade_reason'))}</td>"
            "</tr>"
        )
    return (
        "<table><thead><tr><th>Probe</th><th>Status</th><th>Evidence</th>"
        "<th>Failures</th><th>Downgrade</th></tr></thead><tbody>"
        + "\n".join(rows)
        + "</tbody></table>"
    )


def _normalise_external_operation_status(status: object) -> str:
    normalized = str(status or "").strip().lower()
    if normalized in {"passed", "passed-after-resume", "success", "succeeded"}:
        return "passed"
    if normalized in {"failed", "fail", "error"}:
        return "failed"
    if normalized in {"missing", "not-found", "not_found"}:
        return "missing"
    return normalized or "missing"


def _collect_external_operations(results: list[dict[str, Any]]) -> dict[str, Any]:
    operations: list[dict[str, Any]] = []
    summary = {"total": 0, "passed": 0, "failed": 0, "missing": 0}
    for result in results:
        case_id = result.get("case_id")
        path_id = result.get("path_id")
        payment_closed_loop = (
            result.get("payment_closed_loop")
            if isinstance(result.get("payment_closed_loop"), dict)
            else {}
        )
        for raw in result.get("external_operations", []) or []:
            if not isinstance(raw, dict):
                continue
            status = _normalise_external_operation_status(raw.get("status"))
            item = {
                "case_id": case_id,
                "path_id": path_id,
                "operation_id": raw.get("operation_id") or raw.get("operationId"),
                "operation_type": raw.get("operation_type") or raw.get("operationType"),
                "status": status,
                "payment_method": raw.get("payment_method") or raw.get("paymentMethod"),
                "gateway_pay_num_source": raw.get("gateway_pay_num_source") or raw.get("gatewayPayNumSource"),
                "issue_status": raw.get("issue_status", raw.get("issueStatus")),
                "artifact": raw.get("artifact") or payment_closed_loop.get("artifact"),
                "payment_closed_loop_status": payment_closed_loop.get("status"),
            }
            operations.append(item)
            summary["total"] += 1
            summary[status if status in {"passed", "failed", "missing"} else "missing"] += 1
    return {"summary": summary, "results": operations}


def _render_external_operations(report: dict[str, Any]) -> str:
    results = report.get("results", []) if isinstance(report, dict) else []
    if not results:
        return "<p class='muted'>No payment closed-loop operations were recorded.</p>"
    rows = []
    for result in results:
        status = result.get("status")
        rows.append(
            "<tr>"
            f"<td>{_html(result.get('case_id'))}</td>"
            f"<td>{_html(result.get('path_id'))}</td>"
            f"<td>{_html(result.get('operation_id'))}</td>"
            f"<td>{_html(result.get('operation_type'))}</td>"
            f"<td><span class='pill {_status_class(status)}'>{_html(status)}</span></td>"
            f"<td>{_html(result.get('payment_method'))}</td>"
            f"<td>{_html(result.get('issue_status'))}</td>"
            f"<td>{_html(result.get('payment_closed_loop_status'))}</td>"
            f"<td>{_html(result.get('artifact'))}</td>"
            "</tr>"
        )
    return (
        "<table><thead><tr><th>Case</th><th>Path</th><th>Operation</th>"
        "<th>Type</th><th>Status</th><th>Method</th><th>Issue Status</th>"
        "<th>Closed Loop</th><th>Artifact</th></tr></thead><tbody>"
        + "\n".join(rows)
        + "</tbody></table>"
    )


def _render_html(
    *,
    product_id: str,
    run_id: str,
    summary: dict[str, int],
    merged_case_summary: dict[str, int],
    results: list[dict[str, Any]],
    merged_cases: list[dict[str, Any]],
    merged_case_statuses: list[dict[str, Any]],
    formal_screenshots: list[dict[str, Any]],
    exploration_evidence: list[dict[str, Any]],
    path_chains: list[dict[str, Any]],
    api_records: list[dict[str, Any]],
    side_effect_probes: dict[str, Any],
    external_operations: dict[str, Any],
    blocking_reason: str,
    generated_at: str,
) -> str:
    probe_summary = side_effect_probes.get("summary", {}) if isinstance(side_effect_probes, dict) else {}
    external_summary = external_operations.get("summary", {}) if isinstance(external_operations, dict) else {}
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Agent4 Execution Report - {_html(run_id)}</title>
  <style>
    body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif; background: #f5f7fb; color: #172033; }}
    header {{ padding: 28px 36px; background: #172033; color: #fff; }}
    header h1 {{ margin: 0 0 8px; font-size: 26px; }}
    header p {{ margin: 4px 0; color: #d8deea; }}
    main {{ padding: 28px 36px 48px; }}
    section {{ margin: 0 0 28px; background: #fff; border: 1px solid #e3e8f2; border-radius: 8px; padding: 22px; }}
    h2 {{ margin: 0 0 16px; font-size: 20px; }}
    .cards {{ display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 12px; }}
    .card {{ min-width: 104px; padding: 14px 16px; border-radius: 8px; background: #f1f5fb; border: 1px solid #e1e8f4; }}
    .card b {{ display: block; font-size: 24px; }}
    .card span {{ color: #667085; }}
    .failed, .error {{ color: #b42318; }}
    .card.failed, .card.error {{ background: #fff1f0; border-color: #ffccc7; color: #b42318; }}
    .card.passed {{ background: #ecfdf3; border-color: #abefc6; color: #067647; }}
    .blocking {{ border-color: #ffb4a8; background: #fff6f4; }}
    .blocking pre {{ white-space: pre-wrap; color: #b42318; }}
    .pill {{ display: inline-block; padding: 3px 8px; border-radius: 999px; background: #eef2f7; }}
    .pill.passed {{ background: #d1fadf; color: #067647; }}
    .pill.failed, .pill.error {{ background: #fee4e2; color: #b42318; }}
    .pill.skipped {{ background: #fef0c7; color: #93370d; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ border-bottom: 1px solid #edf1f7; padding: 10px 8px; text-align: left; vertical-align: top; font-size: 13px; }}
    th {{ color: #475467; background: #f8fafc; }}
    .shot-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 16px; }}
    .shot {{ margin: 0; border: 1px solid #e3e8f2; border-radius: 8px; overflow: hidden; background: #fbfdff; }}
    .shot figcaption {{ padding: 10px 12px; border-bottom: 1px solid #e3e8f2; }}
    .shot figcaption span {{ display: block; margin-top: 3px; color: #667085; font-size: 12px; }}
    .shot img {{ display: block; width: 100%; max-height: 560px; object-fit: contain; background: #f4f6f9; }}
    .shot p {{ margin: 8px 12px 12px; color: #667085; font-size: 12px; overflow-wrap: anywhere; }}
    .path-chain {{ border: 1px solid #d7e0eb; border-radius: 8px; margin-bottom: 14px; background: #fbfdff; }}
    .path-chain > summary {{ display: flex; gap: 12px; align-items: center; padding: 14px 16px; border-bottom: 1px solid #e3e8f2; }}
    .path-chain > summary span {{ color: #667085; font-size: 12px; }}
    .chain-meta, .node-url {{ color: #667085; overflow-wrap: anywhere; }}
    .chain-nodes {{ padding: 14px 16px 18px; }}
    .chain-node {{ display: grid; grid-template-columns: 34px minmax(0, 1fr); gap: 12px; position: relative; padding-bottom: 18px; }}
    .chain-node:not(:last-child)::after {{ content: ""; position: absolute; left: 16px; top: 34px; bottom: 0; width: 2px; background: #d7e0eb; }}
    .node-index {{ position: relative; z-index: 1; width: 34px; height: 34px; border-radius: 50%; background: #172033; color: #fff; display: grid; place-items: center; font-weight: 700; }}
    .node-body {{ border: 1px solid #e3e8f2; border-radius: 8px; padding: 12px; background: #fff; }}
    .node-body h3 {{ margin: 0 0 6px; font-size: 16px; }}
    .node-body p {{ margin: 5px 0; }}
    .node-status {{ display: inline-block; padding: 2px 8px; border-radius: 999px; background: #eef2f7; color: #475467; font-size: 12px; }}
    .node-actions {{ margin: 10px 0; padding-left: 18px; }}
    .node-actions li {{ margin: 6px 0; }}
    .node-actions small {{ display: block; color: #667085; overflow-wrap: anywhere; }}
    .node-actions em {{ display: block; color: #475467; font-size: 12px; }}
    .chain-shots {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 10px; margin-top: 10px; }}
    .chain-shot {{ margin: 0; border: 1px solid #e3e8f2; border-radius: 6px; overflow: hidden; background: #f8fafc; }}
    .chain-shot img {{ display: block; width: 100%; max-height: 320px; object-fit: contain; background: #eef2f7; }}
    .chain-shot figcaption {{ padding: 6px 8px; color: #667085; font-size: 12px; }}
    .error-text {{ color: #b42318 !important; }}
    details.api {{ border: 1px solid #e3e8f2; border-radius: 8px; padding: 10px 12px; margin-bottom: 10px; }}
    details.failed {{ border-color: #ffb4a8; background: #fff6f4; }}
    summary {{ cursor: pointer; font-weight: 600; overflow-wrap: anywhere; }}
    summary small {{ color: #667085; font-weight: 400; }}
    pre {{ white-space: pre-wrap; overflow-x: auto; background: #101828; color: #f8fafc; border-radius: 6px; padding: 12px; font-size: 12px; }}
    .muted {{ color: #667085; }}
  </style>
</head>
<body>
  <header>
    <h1>Agent4 Execution Report</h1>
    <p>product_id: {_html(product_id)}</p>
    <p>run_id: {_html(run_id)}</p>
    <p>generated_at: {_html(generated_at)}</p>
  </header>
  <main>
    <section>
      <h2>Run Summary</h2>
      <div class="cards">{_render_summary_cards(summary)}</div>
    </section>
    <section>
      <h2>Merged Case Summary</h2>
      <div class="cards">{_render_summary_cards(merged_case_summary)}</div>
      <p class="muted">Case status is evaluated at each case boundary node, so a later path failure does not fail an already satisfied case.</p>
    </section>
    <section>
      <h2>Merged Test Cases</h2>
      {_render_merged_cases_with_statuses(merged_cases, merged_case_statuses)}
    </section>
    <section class="blocking">
      <h2>Final Blocking Reason</h2>
      <pre>{_html(blocking_reason or "No blocking reason recorded.")}</pre>
    </section>
    <section>
      <h2>Path Replay Chains (Agent4 Official)</h2>
      {_render_path_chains(path_chains)}
    </section>
    <section>
      <h2>Path Results</h2>
      {_render_results(results)}
    </section>
    <section>
      <h2>Agent4 Key Actions</h2>
      {_render_actions(results)}
    </section>
    <section>
      <h2>Agent4 Official Screenshots</h2>
      {_render_screenshot_grid(formal_screenshots)}
    </section>
    <section>
      <h2>Agent3 Exploration Evidence</h2>
      {_render_screenshot_grid(exploration_evidence, title_key="action_text")}
    </section>
    <section>
      <h2>Key API Responses</h2>
      {_render_api_records(api_records)}
    </section>
    <section>
      <h2>Payment Closed Loop</h2>
      <div class="cards">
        <div class='card'><b>{_html(external_summary.get('total', 0))}</b><span>total</span></div>
        <div class='card passed'><b>{_html(external_summary.get('passed', 0))}</b><span>passed</span></div>
        <div class='card failed'><b>{_html(external_summary.get('failed', 0))}</b><span>failed</span></div>
        <div class='card skipped'><b>{_html(external_summary.get('missing', 0))}</b><span>missing</span></div>
      </div>
      {_render_external_operations(external_operations)}
    </section>
    <section>
      <h2>Side-effect Probes</h2>
      <div class="cards">
        <div class='card'><b>{_html(probe_summary.get('total', 0))}</b><span>total</span></div>
        <div class='card passed'><b>{_html(probe_summary.get('success', 0))}</b><span>success</span></div>
        <div class='card failed'><b>{_html(probe_summary.get('fail', 0))}</b><span>fail</span></div>
        <div class='card skipped'><b>{_html(probe_summary.get('na', 0))}</b><span>na</span></div>
      </div>
      {_render_side_effect_probes(side_effect_probes)}
    </section>
  </main>
</body>
</html>
"""


def generate_agent4_html_report(
    *,
    run_dir: str | Path,
    product_id: str,
    run_id: str,
    reports: list[dict[str, Any]] | None = None,
    blocked_paths: list[dict[str, Any]] | None = None,
) -> Path:
    run_path = Path(run_dir)
    run_path.mkdir(parents=True, exist_ok=True)
    results = _results_from_reports(reports)
    summary = _summary_from_reports(reports)
    merged_cases = _collect_merged_cases(run_path)
    merged_case_statuses = _derive_merged_case_statuses(merged_cases, results)
    merged_case_summary = _summary_from_case_statuses(merged_case_statuses)
    formal_screenshots = _collect_formal_screenshots(results, run_path)
    exploration_evidence = _collect_exploration_evidence(run_path)
    api_records, api_errors = _collect_api_records(run_path)
    side_effect_probes = _collect_side_effect_probes(run_path, reports)
    external_operations = _collect_external_operations(results)
    blocking_reason = _blocking_reason(results, api_records, blocked_paths)
    path_chains = _build_path_chains(
        results=results,
        formal_screenshots=formal_screenshots,
        blocked_paths=blocked_paths,
    )
    results = _results_without_unobserved_static_health_notice(results, path_chains)
    merged_case_statuses = _derive_merged_case_statuses(merged_cases, results)
    merged_case_summary = _summary_from_case_statuses(merged_case_statuses)
    external_operations = _collect_external_operations(results)
    blocking_reason = _blocking_reason(results, api_records, blocked_paths)
    generated_at = _now_iso()
    data = {
        "product_id": product_id,
        "run_id": run_id,
        "generated_at": generated_at,
        "summary": summary,
        "merged_case_summary": merged_case_summary,
        "merged_cases": merged_cases,
        "merged_case_count": len(merged_cases),
        "merged_case_statuses": merged_case_statuses,
        "results": results,
        "path_chains": path_chains,
        "formal_screenshots": formal_screenshots,
        "formal_screenshot_count": len(formal_screenshots),
        "exploration_evidence": exploration_evidence,
        "exploration_evidence_count": len(exploration_evidence),
        "api_records": api_records,
        "api_errors": api_errors,
        "api_error_count": len(api_errors),
        "external_operations": external_operations,
        "side_effect_probes": side_effect_probes,
        "blocked_paths": blocked_paths or [],
        "blocking_reason": blocking_reason,
    }
    (run_path / "report-data.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    html = _render_html(
        product_id=product_id,
        run_id=run_id,
        summary=summary,
        merged_case_summary=merged_case_summary,
        results=results,
        merged_cases=merged_cases,
        merged_case_statuses=merged_case_statuses,
        formal_screenshots=formal_screenshots,
        exploration_evidence=exploration_evidence,
        path_chains=path_chains,
        api_records=api_records,
        side_effect_probes=side_effect_probes,
        external_operations=external_operations,
        blocking_reason=blocking_reason,
        generated_at=generated_at,
    )
    report_path = run_path / "report.html"
    report_path.write_text(html, encoding="utf-8")
    return report_path

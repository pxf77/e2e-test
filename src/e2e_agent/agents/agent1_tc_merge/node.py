"""tc_merge_agent: Build and merge stage-one regression cases.

LangGraph node responsible for:
- Running the Agent 1 chain: prd-ana -> tc-gen(stage1) -> reg-case-merge
- Loading manual test cases from manual_cases_path
- Preserving intermediate artifacts for auditability
- Surfacing merged cases, conflicts, and merge trace data

Reads:  state.prd_path, state.manual_cases_path
Writes: state.prd_analysis, state.test_case_skeleton, state.merged_cases,
        state.conflicts, state.merge_trace, state.error
Gate:   R1 (human review of merged cases)
"""
from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import TYPE_CHECKING, Any

from e2e_agent.artifacts.fingerprint import append_artifact_fingerprint
from e2e_agent.artifacts.paths import (
    agent_artifact_path,
    product_artifact_dir,
    write_agent_json_artifact,
    write_agent_text_artifact,
)
from e2e_agent.core.case_merge import (
    load_manual_cases as load_manual_cases_runtime,
    merge_cases as merge_cases_runtime,
    normalise_stage1_cases as normalise_stage1_cases_runtime,
    select_regression_cases as select_regression_cases_runtime,
)
from e2e_agent.core.conflict_closer import render_conflict_closures
from e2e_agent.core.prd_analysis import parse_prd_analysis_from_path
from e2e_agent.skills.loader import SkillPackageLoader
from e2e_agent.core.case_skeleton import build_stage1_skeleton as build_stage1_skeleton_runtime

if TYPE_CHECKING:
    from e2e_agent.graph.state import E2EAgentState

def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").exists():
            return parent
    return Path(__file__).resolve().parents[4]


_ROOT_DIR = _repo_root()
_SUPPORTED_TEXT_SUFFIXES = {".json", ".md", ".markdown", ".txt"}
_TITLE_LINE_RE = re.compile(r"^(#{1,6}\s+.+|(?:TC|CASE|用例)[-:\s].+)$", re.IGNORECASE)
_BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_PRIORITY_RE = re.compile(r"\b(P0|P1|P2)\b", re.IGNORECASE)


def _normalise_text(value: str) -> str:
    lowered = value.strip().lower()
    collapsed = re.sub(r"\s+", "", lowered)
    return re.sub(r"[^\w\u4e00-\u9fff]", "", collapsed)


def _unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        item = value.strip()
        if not item:
            continue
        key = _normalise_text(item)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _text_similarity(left: str, right: str) -> float:
    left_norm = _normalise_text(left)
    right_norm = _normalise_text(right)
    if not left_norm or not right_norm:
        return 0.0
    if left_norm == right_norm:
        return 1.0
    ratio = SequenceMatcher(None, left_norm, right_norm).ratio()
    shorter, longer = sorted((left_norm, right_norm), key=len)
    contains_score = len(shorter) / len(longer) if shorter in longer else 0.0
    chars_left = set(left_norm)
    chars_right = set(right_norm)
    overlap = len(chars_left & chars_right) / max(len(chars_left | chars_right), 1)
    return max(ratio, contains_score * 0.95, overlap * 0.85)


def _list_similarity(left: list[str], right: list[str]) -> float:
    if not left or not right:
        return 0.0
    joined_left = " ".join(left)
    joined_right = " ".join(right)
    return _text_similarity(joined_left, joined_right)


def _parse_priority(*values: str) -> str:
    for value in values:
        match = _PRIORITY_RE.search(value or "")
        if match:
            return match.group(1).upper()
    return "P0"


def _split_blocks(text: str) -> list[tuple[str, list[str]]]:
    title = "Untitled Case"
    buffer: list[str] = []
    blocks: list[tuple[str, list[str]]] = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if _TITLE_LINE_RE.match(line.strip()):
            if buffer:
                blocks.append((title, buffer))
            title = re.sub(r"^#{1,6}\s*", "", line).strip()
            buffer = []
            continue
        buffer.append(line)
    if buffer or not blocks:
        blocks.append((title, buffer))
    return blocks


def _extract_list_items(lines: list[str], aliases: tuple[str, ...]) -> list[str]:
    results: list[str] = []
    current_section = False
    for raw_line in lines:
        stripped = raw_line.strip()
        normalised = _normalise_text(stripped)
        if not stripped:
            current_section = False
            continue
        if any(alias in normalised for alias in aliases):
            inline = re.split(r"[:：]", stripped, maxsplit=1)
            if len(inline) == 2 and inline[1].strip():
                results.append(inline[1].strip())
            current_section = True
            continue
        if current_section and _BULLET_RE.match(stripped):
            results.append(_BULLET_RE.sub("", stripped).strip())
            continue
        if current_section and not _TITLE_LINE_RE.match(stripped):
            results.append(stripped)
    return _unique_strings(results)


def _fallback_steps(lines: list[str]) -> list[str]:
    items = [
        _BULLET_RE.sub("", line.strip()).strip()
        for line in lines
        if _BULLET_RE.match(line.strip())
    ]
    return _unique_strings(items)


def _parse_markdown_case_blocks(text: str, source_path: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for index, (title, lines) in enumerate(_split_blocks(text), start=1):
        preconditions = _extract_list_items(lines, ("前置条件", "precondition"))
        steps = _extract_list_items(lines, ("步骤", "step", "操作"))
        assertions = _extract_list_items(lines, ("预期", "断言", "assert", "expected"))
        if not steps:
            steps = _fallback_steps(lines)
        if not assertions:
            assertions = [f"Expected outcome matches case intent: {title}"]

        case_title = title or f"{source_path.stem}-{index}"
        cases.append(
            {
                "case_id": f"MANUAL-{source_path.stem}-{index:03d}",
                "title": case_title,
                "source": "manual",
                "priority": _parse_priority(case_title, "\n".join(lines)),
                "steps": steps or [f"执行人工场景：{case_title}"],
                "assertions": assertions,
                "preconditions": preconditions,
                "tags": [f"manual-file:{source_path.name}"],
            }
        )
    return cases


def _parse_json_cases(payload: Any, source_path: Path) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        if isinstance(payload.get("cases"), list):
            raw_cases = payload["cases"]
        else:
            raw_cases = [payload]
    elif isinstance(payload, list):
        raw_cases = payload
    else:
        return []

    cases: list[dict[str, Any]] = []
    for index, item in enumerate(raw_cases, start=1):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("name") or f"{source_path.stem}-{index}")
        raw_steps = item.get("steps", [])
        if isinstance(raw_steps, str):
            raw_steps = [raw_steps]
        steps = [str(step) for step in raw_steps if str(step).strip()]

        raw_assertions = item.get("assertions", item.get("expected", []))
        if isinstance(raw_assertions, str):
            raw_assertions = [raw_assertions]
        assertions = [str(assertion) for assertion in raw_assertions if str(assertion).strip()]
        preconditions = [
            str(value)
            for value in item.get("preconditions", item.get("precondition", []))
            if str(value).strip()
        ]
        tags = [str(value) for value in item.get("tags", []) if str(value).strip()]
        if not isinstance(item.get("preconditions"), list) and item.get("precondition"):
            preconditions = [str(item["precondition"])]
        if not assertions:
            expected = item.get("expected")
            if isinstance(expected, str) and expected.strip():
                assertions = [expected.strip()]
        cases.append(
            {
                "case_id": str(item.get("case_id") or f"MANUAL-{source_path.stem}-{index:03d}"),
                "title": title,
                "source": "manual",
                "priority": _parse_priority(str(item.get("priority", "")), title),
                "steps": _unique_strings(steps) or [f"执行人工场景：{title}"],
                "assertions": _unique_strings(assertions)
                or [f"Expected outcome matches case intent: {title}"],
                "preconditions": _unique_strings(preconditions),
                "tags": _unique_strings(tags + [f"manual-file:{source_path.name}"]),
            }
        )
    return cases


def _load_manual_cases(path_value: str | None) -> tuple[list[dict[str, Any]], list[str]]:
    cases, _, warnings = load_manual_cases_runtime(path_value)
    return cases, warnings


def _load_manual_cases_with_trace(
    path_value: str | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    return load_manual_cases_runtime(path_value)


def _extract_bullets(lines: list[str]) -> list[str]:
    items = []
    for line in lines:
        stripped = line.strip()
        if _BULLET_RE.match(stripped):
            items.append(_BULLET_RE.sub("", stripped).strip())
    return _unique_strings(items)


def _parse_prd_analysis(prd_path: str, product_id: str) -> tuple[dict[str, Any], list[str]]:
    return parse_prd_analysis_from_path(prd_path, product_id)


def _build_ac_cases(prd_analysis: dict[str, Any]) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for feature in prd_analysis.get("features", []):
        feature_name = str(feature.get("name") or "Unnamed Feature")
        priority = _parse_priority(str(feature.get("priority", "")), feature_name)
        criteria = feature.get("acceptance_criteria", [])
        for index, criterion in enumerate(criteria, start=1):
            criterion_text = str(criterion).strip()
            if not criterion_text:
                continue
            cases.append(
                {
                    "case_id": f"AC-{feature.get('feature_id', 'FEAT')}-{index:03d}",
                    "title": f"{feature_name} - {criterion_text[:48]}",
                    "source": "ac",
                    "priority": priority,
                    "steps": [
                        f"Navigate to feature context: {feature_name}",
                        f"Execute the acceptance scenario: {criterion_text}",
                    ],
                    "assertions": [criterion_text],
                    "preconditions": [f"Feature ready: {feature_name}"],
                    "tags": [f"feature:{feature_name}", "origin:prd-ac"],
                }
            )
    return cases


def _build_stage1_skeleton(prd_analysis: dict[str, Any]) -> list[dict[str, Any]]:
    return build_stage1_skeleton_runtime(prd_analysis)


def _normalise_stage1_cases(skeleton_cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return normalise_stage1_cases_runtime(skeleton_cases)


def _stage_stats(stage: str, source: str, warnings: list[str], **extra: Any) -> dict[str, Any]:
    return {
        "stage": stage,
        "source": source,
        "warnings": list(dict.fromkeys(str(item) for item in warnings if str(item).strip())),
        **extra,
    }


def _build_merge_trace(
    *,
    product_id: str,
    prd_path: str | None,
    manual_cases_path: str | None,
    prd_analysis: dict[str, Any],
    skeleton_cases: list[dict[str, Any]],
    manual_cases: list[dict[str, Any]],
    parse_trace: list[dict[str, Any]],
    candidate_cases: list[dict[str, Any]],
    merged_cases: list[dict[str, Any]],
    excluded_cases: list[dict[str, Any]],
    conflicts: list[dict[str, Any]],
    selection_trace: dict[str, Any],
    prd_stage: dict[str, Any],
    tc_gen_stage: dict[str, Any],
    merge_stage: dict[str, Any],
) -> dict[str, Any]:
    return {
        "product_id": product_id,
        "chain": ["mpt-ins-prd-ana", "mpt-ins-tc-gen", "mpt-reg-case-merge"],
        "inputs": {
            "prd_path": prd_path,
            "manual_cases_path": manual_cases_path,
        },
        "artifacts": {
            "prd_analysis": "state.prd_analysis",
            "test_case_skeleton": "state.test_case_skeleton",
            "parse_trace": parse_trace,
            "candidate_cases": "merge_trace.artifacts.candidate_cases",
            "merged_cases": "state.merged_cases",
            "excluded_cases": "merge_trace.artifacts.excluded_cases",
            "conflicts": "state.conflicts",
            "selection_trace": selection_trace,
        },
        "summary": {
            "feature_count": len(prd_analysis.get("features", [])),
            "skeleton_count": len(skeleton_cases),
            "manual_case_count": len(manual_cases),
            "parse_trace_count": len(parse_trace),
            "candidate_case_count": len(candidate_cases),
            "merged_case_count": len(merged_cases),
            "excluded_case_count": len(excluded_cases),
            "conflict_count": len(conflicts),
        },
        "stages": {
            "prd_ana": prd_stage,
            "tc_gen_stage1": tc_gen_stage,
            "reg_case_merge": merge_stage,
        },
    }


def _coerce_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        items: list[str] = []
        for item in value:
            items.extend(_coerce_text_list(item))
        return _unique_strings(items)
    if isinstance(value, dict):
        text = value.get("text") or value.get("title") or value.get("name") or value.get("description")
        return _coerce_text_list(text) if text else []
    text = str(value).strip()
    return [text] if text else []


def _render_markdown_list(values: Any, empty_text: str = "无") -> list[str]:
    items = _coerce_text_list(values)
    if not items:
        return [f"- {empty_text}"]
    return [f"- {item}" for item in items]


_CASE_SOURCE_DISPLAY = {
    "merged": "合并用例",
    "manual": "人工用例",
    "ac": "PRD 骨架用例",
    "prd-ac": "PRD 骨架用例",
}

_CONFLICT_TYPE_DISPLAY = {
    "assertion_mismatch": "断言不一致",
    "step_contradiction": "步骤路径冲突",
    "precondition_conflict": "前置条件冲突",
    "scope_overlap": "范围相近但不可自动合并",
    "missing_coverage": "人工用例缺失覆盖",
}

_CONFLICT_POLICY_DISPLAY = {
    "temporary-override": "本轮临时保留并人工复核",
    "update-human-case": "更新人工用例",
    "update-prd": "补充或澄清 PRD",
    "update-knowledge-base": "更新领域知识库规则",
    "needs-product-confirmation": "产品或资深 QA 确认",
}

_TAG_DISPLAY = {
    "origin:tc-gen-stage1": "来源: 阶段一骨架用例",
    "origin:prd-ac": "来源: PRD 骨架",
    "selection:business-scenario-orchestration": "筛选: 业务场景编排",
}

_BUSINESS_INTENT_DISPLAY = {
    "main_flow": "主流程",
    "health_notice": "健康告知",
    "tax_identity": "税务与身份认证",
    "underwriting": "核保",
    "payment": "支付",
    "policy": "保单服务",
    "surrender": "退保",
}

_SCENARIO_TYPE_DISPLAY = {
    "main_flow": "主流程",
    "core_branch": "核心分支",
    "important_branch": "重要分支",
    "optional_branch": "可选分支",
}

_MANUAL_FORMAT_DISPLAY = {
    "converted-json": "Excel 转换结构化数据",
    "json": "结构化数据",
    "markdown": "Markdown 文档",
}


def _display_case_source(source: object) -> str:
    value = str(source or "merged").strip()
    return _CASE_SOURCE_DISPLAY.get(value, value or "合并用例")


def _display_conflict_type(conflict_type: object) -> str:
    value = str(conflict_type or "").strip()
    return _CONFLICT_TYPE_DISPLAY.get(value, value or "未分类冲突")


def _display_conflict_policy(policy: object) -> str:
    value = str(policy or "").strip()
    return _CONFLICT_POLICY_DISPLAY.get(value, value or "待人工确认")


def _display_tag(tag: object) -> str:
    value = str(tag or "").strip()
    if not value:
        return ""
    if value in _TAG_DISPLAY:
        return _TAG_DISPLAY[value]
    if value.startswith("manual-file:"):
        return f"人工用例文件: {value.removeprefix('manual-file:')}"
    if value.startswith("manual-format:"):
        raw_format = value.removeprefix("manual-format:")
        return f"人工用例格式: {_MANUAL_FORMAT_DISPLAY.get(raw_format, raw_format)}"
    if value.startswith("business-intent:"):
        intent = value.removeprefix("business-intent:")
        return f"业务意图: {_BUSINESS_INTENT_DISPLAY.get(intent, intent)}"
    if value.startswith("scenario-type:"):
        scenario_type = value.removeprefix("scenario-type:")
        return f"场景类型: {_SCENARIO_TYPE_DISPLAY.get(scenario_type, scenario_type)}"
    if value.startswith("module:"):
        return f"模块: {value.removeprefix('module:')}"
    if value.startswith("feature:"):
        return f"功能: {value.removeprefix('feature:')}"
    return value


def _display_tags(tags: Any) -> str:
    values = [_display_tag(tag) for tag in _coerce_text_list(tags)]
    values = [tag for tag in values if tag]
    return ", ".join(_unique_strings(values)) or "无"


def _render_merged_cases_markdown(
    *,
    product_id: str,
    merged_cases: list[dict[str, Any]],
    conflicts: list[dict[str, Any]],
    merge_trace: dict[str, Any],
    warnings: list[str],
) -> str:
    summary = merge_trace.get("summary", {})
    lines = [
        "# Agent1 合并后用例说明",
        "",
        "本文档由 Agent1 根据 PRD 骨架用例、人工用例和合并策略生成，供 R1 人工评审直接阅读。",
        "",
        "## 合并概览",
        "",
        f"- 产品 ID: `{product_id}`",
        f"- PRD 功能数: {summary.get('feature_count', 0)}",
        f"- 骨架用例数: {summary.get('skeleton_count', 0)}",
        f"- 人工用例数: {summary.get('manual_case_count', 0)}",
        f"- 候选用例数: {summary.get('candidate_case_count', len(merged_cases))}",
        f"- 合并后用例数: {summary.get('merged_case_count', len(merged_cases))}",
        f"- 排除用例数: {summary.get('excluded_case_count', 0)}",
        f"- 待确认冲突数: {summary.get('conflict_count', len(conflicts))}",
        "",
    ]

    if warnings:
        lines.extend(["## 生成提示", ""])
        lines.extend(_render_markdown_list(warnings))
        lines.append("")

    lines.extend(["## 合并用例", ""])
    if not merged_cases:
        lines.extend(["当前没有可进入后续回归链路的合并用例。", ""])
        return "\n".join(lines).rstrip() + "\n"

    for index, case in enumerate(merged_cases, start=1):
        case_id = case.get("case_id") or f"CASE-{index:03d}"
        title = case.get("title") or "未命名用例"
        priority = case.get("priority") or "P0"
        source = _display_case_source(case.get("source"))
        tags = _display_tags(case.get("tags"))
        conflict_ref = case.get("conflict_ref")
        lines.extend(
            [
                f"### {index}. {title}",
                "",
                f"- 用例编号: `{case_id}`",
                f"- 优先级: {priority}",
                f"- 来源: {source}",
                f"- 标签: {tags}",
            ]
        )
        if conflict_ref:
            lines.append(f"- 关联冲突: `{conflict_ref}`")
        lines.extend(
            [
                "",
                "前置条件:",
                *_render_markdown_list(case.get("preconditions")),
                "",
                "步骤:",
                *_render_markdown_list(case.get("steps"), empty_text="按用例标题执行对应业务流程"),
                "",
                "预期结果:",
                *_render_markdown_list(case.get("assertions"), empty_text="业务结果符合用例意图"),
                "",
            ]
        )

    return "\n".join(lines).rstrip() + "\n"


def _write_merged_cases_markdown(
    *,
    root_dir: Path,
    product_id: str,
    markdown: str,
    product_dir: Path | str | None = None,
) -> Path:
    return write_agent_text_artifact(
        root_dir=root_dir,
        product_id=product_id,
        agent_name="agent1",
        relative_path="merged-cases.md",
        text=markdown,
        product_dir=product_dir,
    )


def _pair_score(manual_case: dict[str, Any], ac_case: dict[str, Any]) -> float:
    title_score = _text_similarity(manual_case["title"], ac_case["title"])
    assertion_score = _list_similarity(
        manual_case.get("assertions", []),
        ac_case.get("assertions", []),
    )
    step_score = _list_similarity(manual_case.get("steps", []), ac_case.get("steps", []))
    return title_score * 0.55 + assertion_score * 0.3 + step_score * 0.15


def _select_pairs(
    manual_cases: list[dict[str, Any]],
    ac_cases: list[dict[str, Any]],
    threshold: float = 0.58,
) -> tuple[list[tuple[int, int, float]], dict[int, tuple[int, float]]]:
    candidates: list[tuple[float, int, int]] = []
    best_manual_for_ac: dict[int, tuple[int, float]] = {}
    for manual_index, manual_case in enumerate(manual_cases):
        for ac_index, ac_case in enumerate(ac_cases):
            score = _pair_score(manual_case, ac_case)
            if score > best_manual_for_ac.get(ac_index, (-1, -1.0))[1]:
                best_manual_for_ac[ac_index] = (manual_index, score)
            candidates.append((score, manual_index, ac_index))

    matches: list[tuple[int, int, float]] = []
    used_manual: set[int] = set()
    used_ac: set[int] = set()
    for score, manual_index, ac_index in sorted(candidates, reverse=True):
        if score < threshold or manual_index in used_manual or ac_index in used_ac:
            continue
        used_manual.add(manual_index)
        used_ac.add(ac_index)
        matches.append((manual_index, ac_index, score))
    return matches, best_manual_for_ac


def _merge_field(manual_values: list[str], ac_values: list[str]) -> list[str]:
    return _unique_strings(list(manual_values) + list(ac_values))


def _conflict_policy(conflict_type: str) -> str:
    return {
        "assertion_mismatch": "needs-product-confirmation",
        "step_contradiction": "temporary-override",
        "precondition_conflict": "needs-product-confirmation",
        "scope_overlap": "update-human-case",
        "missing_coverage": "update-human-case",
    }[conflict_type]


def _next_conflict_id(counter: int) -> str:
    return f"CONFLICT-{counter:03d}"


def _build_conflict(
    counter: int,
    conflict_type: str,
    description: str,
    manual_case_id: str | None,
    ac_case_id: str | None,
) -> dict[str, Any]:
    return {
        "conflict_id": _next_conflict_id(counter),
        "type": conflict_type,
        "policy": _conflict_policy(conflict_type),
        "manual_case_id": manual_case_id,
        "ac_case_id": ac_case_id,
        "description": description,
        "resolution_note": None,
        "resolved": False,
    }


def _build_final_case_id(product_id: str, sequence: int) -> str:
    safe_product = re.sub(r"[^A-Za-z0-9]+", "-", product_id).strip("-") or "product"
    return f"TC-{safe_product}-{sequence:03d}"


def _merge_cases(
    product_id: str,
    manual_cases: list[dict[str, Any]],
    ac_cases: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    return merge_cases_runtime(product_id, manual_cases, ac_cases)


def _select_regression_cases(
    candidate_cases: list[dict[str, Any]],
    conflicts: list[dict[str, Any]],
    *,
    product_id: str | None = None,
    product_name: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    return select_regression_cases_runtime(
        candidate_cases,
        conflicts,
        product_id=product_id,
        product_name=product_name,
    )


async def _tc_merge_node_impl(state: "E2EAgentState") -> dict:
    """Run the Agent 1 chain and merge manual test cases with stage-one cases.

    Prefer the dedicated skill entries when available, with local heuristic
    implementations retained as fallbacks for minimal environments.
    """
    product_id = state.get("product_id") or "product"
    global_warnings: list[str] = []

    loader = SkillPackageLoader()
    manifests: dict[str, Any] = {}
    for skill_name in ("mpt-ins-prd-ana", "mpt-ins-tc-gen", "mpt-reg-case-merge"):
        try:
            manifests[skill_name] = loader.load_skill(skill_name)
        except (FileNotFoundError, ValueError) as exc:
            global_warnings.append(str(exc))

    prd_warnings: list[str] = []
    prd_source = "fallback"
    prd_analysis: dict[str, Any]
    prd_manifest = manifests.get("mpt-ins-prd-ana")
    if prd_manifest and prd_manifest.entry_script:
        try:
            prd_source = "skill"
            prd_analysis = loader.run_entry(
                "mpt-ins-prd-ana",
                {
                    "product_id": product_id,
                    "prd_path": state.get("prd_path"),
                    "materialise": False,
                },
            )
        except (RuntimeError, ValueError, FileNotFoundError) as exc:
            prd_source = "fallback"
            prd_warnings.append(str(exc))
            prd_analysis, fallback_warnings = _parse_prd_analysis(state.get("prd_path", ""), product_id)
            prd_warnings.extend(fallback_warnings)
    else:
        prd_analysis, prd_warnings = _parse_prd_analysis(state.get("prd_path", ""), product_id)

    skeleton_warnings: list[str] = []
    skeleton_source = "fallback"
    skeleton_cases: list[dict[str, Any]]
    tc_gen_manifest = manifests.get("mpt-ins-tc-gen")
    if tc_gen_manifest and tc_gen_manifest.entry_script:
        try:
            skeleton_source = "skill"
            tc_gen_result = loader.run_entry(
                "mpt-ins-tc-gen",
                {
                    "product_id": product_id,
                    "prd_analysis": prd_analysis,
                    "materialise": False,
                },
            )
            skeleton_cases = list(tc_gen_result.get("skeleton", []))
        except (RuntimeError, ValueError, FileNotFoundError) as exc:
            skeleton_source = "fallback"
            skeleton_warnings.append(str(exc))
            skeleton_cases = _build_stage1_skeleton(prd_analysis)
    else:
        skeleton_cases = _build_stage1_skeleton(prd_analysis)

    manual_cases, parse_trace, manual_warnings = _load_manual_cases_with_trace(state.get("manual_cases_path"))
    merge_warnings: list[str] = []
    merge_source = "fallback"
    candidate_cases: list[dict[str, Any]]
    excluded_cases: list[dict[str, Any]]
    selection_trace: dict[str, Any]
    reg_manifest = manifests.get("mpt-reg-case-merge")
    if reg_manifest and reg_manifest.entry_script:
        try:
            merge_source = "skill"
            result = loader.run_entry(
                "mpt-reg-case-merge",
                {
                    "product_id": product_id,
                    "product_name": state.get("product_name"),
                    "prd_path": state.get("prd_path"),
                    "manual_cases_path": state.get("manual_cases_path"),
                    "prd_analysis": prd_analysis,
                    "skeleton_cases": skeleton_cases,
                },
            )
            merge_warnings.extend(str(item) for item in result.get("warnings", []))
            candidate_cases = list(result.get("candidate_cases", result.get("merged_cases", [])))
            merged_cases = list(result.get("merged_cases", []))
            excluded_cases = list(result.get("excluded_cases", []))
            conflicts = result.get("conflicts", [])
            selection_trace = dict(result.get("selection_trace", {}))
            parse_trace = list(result.get("parse_trace", parse_trace))
        except (RuntimeError, ValueError, FileNotFoundError) as exc:
            merge_source = "fallback"
            merge_warnings.append(str(exc))
            candidate_cases, conflicts = _merge_cases(
                product_id,
                manual_cases,
                _normalise_stage1_cases(skeleton_cases),
            )
            merged_cases, excluded_cases, selection_trace = _select_regression_cases(
                candidate_cases,
                conflicts,
                product_id=product_id,
                product_name=state.get("product_name"),
            )
    else:
        candidate_cases, conflicts = _merge_cases(
            product_id,
            manual_cases,
            _normalise_stage1_cases(skeleton_cases),
        )
        merged_cases, excluded_cases, selection_trace = _select_regression_cases(
            candidate_cases,
            conflicts,
            product_id=product_id,
            product_name=state.get("product_name"),
        )

    cases_by_id = {
        str(case.get("case_id")): case
        for case in [*manual_cases, *_normalise_stage1_cases(skeleton_cases), *candidate_cases]
        if case.get("case_id")
    }
    conflicts = render_conflict_closures(conflicts, cases_by_id=cases_by_id)

    global_warnings.extend(prd_warnings)
    global_warnings.extend(skeleton_warnings)
    global_warnings.extend(manual_warnings)
    global_warnings.extend(merge_warnings)

    merge_trace = _build_merge_trace(
        product_id=product_id,
        prd_path=state.get("prd_path"),
        manual_cases_path=state.get("manual_cases_path"),
        prd_analysis=prd_analysis,
        skeleton_cases=skeleton_cases,
        manual_cases=manual_cases,
        parse_trace=parse_trace,
        candidate_cases=candidate_cases,
        merged_cases=merged_cases,
        excluded_cases=excluded_cases,
        conflicts=conflicts,
        selection_trace=selection_trace,
        prd_stage=_stage_stats(
            "prd_ana",
            prd_source,
            prd_warnings,
            feature_count=len(prd_analysis.get("features", [])),
            application_flow_count=len(prd_analysis.get("application_flow", [])),
        ),
        tc_gen_stage=_stage_stats(
            "tc_gen_stage1",
            skeleton_source,
            skeleton_warnings,
            skeleton_count=len(skeleton_cases),
        ),
        merge_stage=_stage_stats(
            "reg_case_merge",
            merge_source,
            merge_warnings + manual_warnings,
            manual_case_count=len(manual_cases),
            parse_trace_count=len(parse_trace),
            candidate_case_count=len(candidate_cases),
            merged_case_count=len(merged_cases),
            excluded_case_count=len(excluded_cases),
            conflict_count=len(conflicts),
            selection_policy=selection_trace.get("selection_policy", {}).get("name"),
        ),
    )

    run_id = str(state.get("run_id") or "run-unknown")
    artifact_root_dir = Path(str(state.get("artifact_root_dir") or _ROOT_DIR))
    artifact_product_dir = product_artifact_dir(
        artifact_root_dir,
        product_id,
        product_dir=state.get("product_artifact_dir"),
        source_paths=[state.get("prd_path"), state.get("manual_cases_path"), state.get("product_source_dir")],
    )
    merged_cases_markdown = _render_merged_cases_markdown(
        product_id=product_id,
        merged_cases=merged_cases,
        conflicts=conflicts,
        merge_trace=merge_trace,
        warnings=global_warnings,
    )
    merged_cases_markdown_path = _write_merged_cases_markdown(
        root_dir=artifact_root_dir,
        product_id=product_id,
        markdown=merged_cases_markdown,
        product_dir=artifact_product_dir,
    )
    merge_trace["artifacts"]["merged_cases_markdown"] = agent_artifact_path(
        product_id,
        "agent1",
        merged_cases_markdown_path.name,
        root_dir=artifact_root_dir,
        product_dir=artifact_product_dir,
    )
    for filename, payload in {
        "prd-analysis.json": prd_analysis,
        "test-case-skeleton.json": skeleton_cases,
        "candidate-cases.json": candidate_cases,
        "merged-cases.json": merged_cases,
        "excluded-cases.json": excluded_cases,
        "conflicts.json": conflicts,
        "merge-trace.json": merge_trace,
    }.items():
        write_agent_json_artifact(
            root_dir=artifact_root_dir,
            product_id=product_id,
            agent_name="agent1",
            relative_path=filename,
            payload=payload,
            product_dir=artifact_product_dir,
        )
    existing_fingerprints = list(state.get("artifact_fingerprints", []) or [])
    new_fingerprints = [
        append_artifact_fingerprint(
            root_dir=artifact_root_dir,
            product_id=product_id,
            run_id=run_id,
            artifact_path=agent_artifact_path(
                product_id,
                "agent1",
                "prd-analysis.json",
                root_dir=artifact_root_dir,
                product_dir=artifact_product_dir,
            ),
            artifact_type="prd-analysis",
            payload=prd_analysis,
            producer="tc_merge_agent",
            product_dir=artifact_product_dir,
        ),
        append_artifact_fingerprint(
            root_dir=artifact_root_dir,
            product_id=product_id,
            run_id=run_id,
            artifact_path=agent_artifact_path(
                product_id,
                "agent1",
                "test-case-skeleton.json",
                root_dir=artifact_root_dir,
                product_dir=artifact_product_dir,
            ),
            artifact_type="test-case-skeleton",
            payload=skeleton_cases,
            producer="tc_merge_agent",
            product_dir=artifact_product_dir,
        ),
        append_artifact_fingerprint(
            root_dir=artifact_root_dir,
            product_id=product_id,
            run_id=run_id,
            artifact_path=agent_artifact_path(
                product_id,
                "agent1",
                "merged-cases.json",
                root_dir=artifact_root_dir,
                product_dir=artifact_product_dir,
            ),
            artifact_type="merged-cases",
            payload=merged_cases,
            producer="tc_merge_agent",
            product_dir=artifact_product_dir,
        ),
        append_artifact_fingerprint(
            root_dir=artifact_root_dir,
            product_id=product_id,
            run_id=run_id,
            artifact_path=agent_artifact_path(
                product_id,
                "agent1",
                "merged-cases.md",
                root_dir=artifact_root_dir,
                product_dir=artifact_product_dir,
            ),
            artifact_type="merged-cases",
            payload=merged_cases_markdown,
            producer="tc_merge_agent",
            product_dir=artifact_product_dir,
        ),
        append_artifact_fingerprint(
            root_dir=artifact_root_dir,
            product_id=product_id,
            run_id=run_id,
            artifact_path=agent_artifact_path(
                product_id,
                "agent1",
                "conflicts.json",
                root_dir=artifact_root_dir,
                product_dir=artifact_product_dir,
            ),
            artifact_type="conflicts",
            payload=conflicts,
            producer="tc_merge_agent",
            product_dir=artifact_product_dir,
        ),
        append_artifact_fingerprint(
            root_dir=artifact_root_dir,
            product_id=product_id,
            run_id=run_id,
            artifact_path=agent_artifact_path(
                product_id,
                "agent1",
                "merge-trace.json",
                root_dir=artifact_root_dir,
                product_dir=artifact_product_dir,
            ),
            artifact_type="merge-trace",
            payload=merge_trace,
            producer="tc_merge_agent",
            product_dir=artifact_product_dir,
        ),
    ]

    return {
        "prd_analysis": prd_analysis,
        "test_case_skeleton": skeleton_cases,
        "candidate_cases": candidate_cases,
        "merged_cases": merged_cases,
        "excluded_cases": excluded_cases,
        "conflicts": conflicts,
        "merge_trace": merge_trace,
        "product_artifact_dir": str(artifact_product_dir),
        "artifact_fingerprints": existing_fingerprints + new_fingerprints,
        "error": "; ".join(dict.fromkeys(global_warnings)) if global_warnings else None,
    }


async def tc_merge_node(state: "E2EAgentState") -> dict:
    try:
        return await _tc_merge_node_impl(state)
    except Exception as exc:
        return {"error": f"tc_merge failed: {exc}"}

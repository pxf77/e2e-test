"""Core stage-one test case skeleton generation."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _case_type(name: str, criteria: list[str]) -> str:
    joined = " ".join([name, *criteria])
    if any(token in joined for token in ("阻断", "失败", "不支持", "拒绝", "不可", "仅接受")):
        return "negative"
    if any(token in joined for token in ("长度", "范围", "组合", "至少", "最多", "上限", "校验")):
        return "boundary"
    return "happy_path"


def _step_limit(items: list[str], limit: int = 3) -> list[str]:
    return [str(item).strip() for item in items if str(item).strip()][:limit]


def _as_text_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def build_skeleton_from_workflow_cases(workflow_cases: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(workflow_cases, dict):
        return []
    raw_cases = workflow_cases.get("cases")
    if not isinstance(raw_cases, list):
        return []

    skeleton: list[dict[str, Any]] = []
    for index, raw_case in enumerate(raw_cases, start=1):
        if not isinstance(raw_case, dict):
            continue
        case_id = str(raw_case.get("case_id") or raw_case.get("id") or f"KLG-CASE-{index:03d}").strip()
        title = str(raw_case.get("title") or raw_case.get("name") or case_id).strip()
        actions = _as_text_list(raw_case.get("steps")) or [title]
        assertions = _as_text_list(raw_case.get("assertions") or raw_case.get("expected"))
        steps = []
        for step_index, action in enumerate(actions, start=1):
            expected = assertions[min(step_index - 1, len(assertions) - 1)] if assertions else f"{action} completes"
            steps.append({"step": step_index, "action": action, "expected": expected})

        skeleton.append(
            {
                "id": case_id,
                "feature_id": str(raw_case.get("feature_id") or f"knowledge:{case_id}"),
                "title": title,
                "type": str(raw_case.get("type") or _case_type(title, assertions)),
                "priority": str(raw_case.get("priority") or "P1"),
                "steps": steps,
                "preconditions": _as_text_list(raw_case.get("preconditions")),
                "test_data_hints": {
                    "source": "knowledge.workflow_cases",
                    "knowledge_case_id": case_id,
                    "assertion_count": len(assertions),
                },
            }
        )
    return skeleton


def build_stage1_skeleton(
    prd_analysis: dict[str, Any],
    workflow_cases: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    skeleton: list[dict[str, Any]] = []
    for feature_index, feature in enumerate(prd_analysis.get("features", []), start=1):
        feature_name = str(feature.get("name") or "Feature").strip()
        criteria = _step_limit(list(feature.get("acceptance_criteria", [])), limit=4)
        if " / " in feature_name:
            node_name, scenario_name = [part.strip() for part in feature_name.split(" / ", 1)]
        else:
            node_name, scenario_name = feature_name, feature_name

        title = f"{node_name}-{scenario_name} 用例"
        steps = [
            {
                "step": 1,
                "action": f"进入 {node_name} 节点，定位到 {scenario_name} 场景",
                "expected": criteria[0] if criteria else f"{scenario_name} 可正常进入",
            }
        ]
        for step_index, item in enumerate(criteria[1:], start=2):
            steps.append(
                {
                    "step": step_index,
                    "action": f"校验规则点：{item}",
                    "expected": item,
                }
            )

        skeleton.append(
            {
                "id": f"TC-{feature_index:03d}",
                "feature_id": str(feature.get("feature_id") or f"FEAT-{feature_index:03d}"),
                "title": title,
                "type": _case_type(feature_name, criteria),
                "priority": str(feature.get("priority") or "P0"),
                "steps": steps,
                "preconditions": [f"已进入 {node_name} 对应业务流程"],
                "test_data_hints": {
                    "feature_name": feature_name,
                    "node_name": node_name,
                    "scenario_name": scenario_name,
                    "criteria_count": len(criteria),
                },
            }
        )
    skeleton.extend(build_skeleton_from_workflow_cases(workflow_cases))
    return skeleton


def render_skeleton_markdown(skeleton: list[dict[str, Any]]) -> str:
    type_names = {
        "happy_path": "正向主流程",
        "negative": "异常/反向场景",
        "boundary": "边界场景",
    }
    lines = ["# 测试用例骨架", ""]
    for case in skeleton:
        lines.append(f"## {case['id']} {case['title']}")
        lines.append(f"- 功能编号: {case['feature_id']}")
        lines.append(f"- 优先级: {case['priority']}")
        lines.append(f"- 用例类型: {type_names.get(str(case['type']), case['type'])}")
        lines.append("- 步骤:")
        for step in case["steps"]:
            lines.append(f"  - {step['step']}. {step['action']}；预期：{step.get('expected', '')}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def materialise_skeleton(root_dir: Path, product_id: str, skeleton: list[dict[str, Any]]) -> None:
    output_dir = root_dir / "products" / product_id / "tc-gen"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "test-cases-skeleton.json").write_text(
        json.dumps(skeleton, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "test-cases-skeleton.md").write_text(
        render_skeleton_markdown(skeleton),
        encoding="utf-8",
    )

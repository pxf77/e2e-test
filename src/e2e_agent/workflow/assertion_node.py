from __future__ import annotations

from typing import Any

from e2e_agent.assertions import AssertionEngine

from .registry import NodeResult
from .state import WorkflowRuntimeState


def assertion_node(state: WorkflowRuntimeState, node_spec: dict[str, Any]) -> NodeResult:
    artifacts = state.get("artifacts") or {}
    assertion_pack = ((state.get("domain") or {}).get("assertion_pack") or {})
    engine = AssertionEngine(assertion_pack)

    page_types: list[str] = []
    for page in (artifacts.get("page_registry") or {}).get("pages") or []:
        if isinstance(page, dict) and page.get("page_type"):
            page_types.append(str(page["page_type"]))
    for path in artifacts.get("regression_paths") or []:
        for node in path.get("nodes") or []:
            if isinstance(node, dict) and node.get("page_type"):
                page_types.append(str(node["page_type"]))

    text_parts: list[str] = []
    for case in artifacts.get("merged_cases") or []:
        text_parts.append(str(case.get("title") or ""))
        text_parts.extend(str(item) for item in case.get("assertions") or [])

    inputs = state.get("inputs") or {}
    supplied = inputs.get("assertion_context") or {}
    context = {
        "page": supplied.get("page") or {},
        "business": supplied.get("business") or {},
        "expected": supplied.get("expected") or {},
        "execution": artifacts.get("execution_result") or {},
        "data": (state.get("runtime_data") or {}).get("test_data") or {},
        "artifacts": artifacts,
        **{key: value for key, value in supplied.items() if key not in {"page", "business", "expected"}},
    }
    requested = inputs.get("assertion_templates")
    template_ids = [str(item) for item in requested] if isinstance(requested, list) else None
    report = engine.run(
        page_types=list(dict.fromkeys(page_types)),
        text=" ".join(text_parts),
        context=context,
        template_ids=template_ids,
    )
    return NodeResult(
        outputs={"assertion_report": report},
        metrics={
            "assertion_check_count": report["summary"]["checks"],
            "assertion_failed": report["summary"]["failed"],
        },
    )

from __future__ import annotations

from typing import Any

from typing_extensions import TypedDict


class WorkflowRuntimeState(TypedDict, total=False):
    """State schema shared by all v2 workflows.

    Dynamic node outputs are stored in ``artifacts`` rather than becoming
    top-level state fields. ``legacy_state`` temporarily contains the v1
    E2EAgentState payload while existing agents are migrated incrementally.
    """

    run_id: str
    app_id: str
    domain_id: str
    workflow_id: str
    env: str
    app: dict[str, Any]
    domain: dict[str, Any]
    inputs: dict[str, Any]
    artifacts: dict[str, Any]
    gates: dict[str, dict[str, Any]]
    metadata: dict[str, Any]
    errors: list[dict[str, Any]]
    legacy_state: dict[str, Any]
    node_trace: list[dict[str, Any]]

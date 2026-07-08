"""LangGraph state definition for the E2E Agent pipeline."""
from __future__ import annotations

from typing import Literal

from typing_extensions import NotRequired, TypedDict


class GateStatus(TypedDict):
    status: Literal["pending", "approved", "rejected"]
    operator: str
    timestamp: str
    note: str


class E2EAgentState(TypedDict):
    # ── Input ──────────────────────────────────────────────────────────────
    product_id: str
    product_name: NotRequired[str | None]
    prd_path: str
    manual_cases_path: str | None
    entry_url: str | None

    # ── tc_merge_agent outputs ─────────────────────────────────────────────
    prd_analysis: dict
    test_case_skeleton: list[dict]
    candidate_cases: list[dict]
    merged_cases: list[dict]
    excluded_cases: list[dict]
    conflicts: list[dict]
    merge_trace: dict

    # ── path_extract_agent outputs ─────────────────────────────────────────
    regression_flow: dict
    regression_paths: list[dict]
    governance_summary: dict

    # ── explore_agent outputs ──────────────────────────────────────────────
    page_registry: dict
    explore_trace: dict
    runtime_context: dict
    page_functions: list[dict]
    scenarios: list[dict]
    element_set: dict
    assertion_results: list[dict]

    # ── exec_healing_agent outputs ─────────────────────────────────────────
    reports: list[dict]
    healing_events: list[dict]
    teardown_report: dict
    quarantine_report: NotRequired[dict]

    # ── Gate states (R1-R4) ───────────────────────────────────────────────
    r1_gate: GateStatus
    r2_gate: GateStatus
    r3_gate: GateStatus
    r4_gate: GateStatus

    # ── Metadata ──────────────────────────────────────────────────────────
    artifact_fingerprints: list[dict]
    run_id: str
    error: str | None
    artifact_root_dir: NotRequired[str]
    product_source_dir: NotRequired[str]
    product_artifact_dir: NotRequired[str]
    gate_checkpoint_dir: NotRequired[str]
    run_dir: NotRequired[str]
    html_report: NotRequired[str | None]

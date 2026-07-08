from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _schema(name: str) -> dict:
    return json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))


def test_settings_default_paths_resolve_repo_config():
    from e2e_agent.config.settings import load_model_routing

    load_model_routing.cache_clear()

    assert load_model_routing()["version"] == "1.0"


def test_agent3_entry_ready_selector_has_no_product_name_hardcode():
    from e2e_agent.core import page_exploration

    selector = page_exploration._ENTRY_READY_SELECTOR

    assert "小青龙" not in selector
    assert "立即投保" in selector
    assert "保费" in selector


def test_agent3_page_registry_schema_covers_current_contract_fields():
    schema = _schema("page-registry.schema.json")
    properties = schema["properties"]
    path_result_properties = properties["path_exploration_results"]["items"]["properties"]
    contract_properties = properties["exploration_contract"]["properties"]

    assert "planned_page_catalog" in properties
    assert "page_content_records" in properties
    assert "path_exploration_results" in properties
    assert "exploration_contract" in properties
    assert "phase1_contract" in contract_properties
    assert path_result_properties["path_status"]["enum"] == ["explored", "partial", "blocked"]
    assert "node_execution_trace" in path_result_properties
    assert "terminal_boundary" in path_result_properties


def test_page_function_schema_covers_trace_first_generation_fields():
    schema = _schema("page-functions.schema.json")
    properties = schema["items"]["properties"]

    for key in (
        "source",
        "fields",
        "actions",
        "entry_signals",
        "exit_signals",
        "next_node_id",
        "next_signals",
        "trace_events",
    ):
        assert key in properties
    assert properties["source"]["enum"] == ["agent3.trace", "agent2.flow"]


def test_test_report_schema_separates_execution_and_business_coverage():
    schema = _schema("test-report.schema.json")
    summary_properties = schema["properties"]["summary"]["properties"]
    result_properties = schema["properties"]["results"]["items"]["properties"]

    assert "business_coverage" in summary_properties
    assert "execution_status" in result_properties
    assert "coverage_status" in result_properties
    assert "fact_lineage" in result_properties
    assert "blocked_by_agent3_contract" in result_properties["execution_status"]["enum"]
    assert "agent3_contract_blocked" in result_properties["failure_category"]["enum"]


def test_scenario_schema_accepts_current_scenario_list_artifact_shape():
    schema = _schema("scenario-definitions.schema.json")

    assert schema["oneOf"][0]["type"] == "array"
    scenario_properties = schema["definitions"]["scenario"]["properties"]
    assert "completion_rule" in scenario_properties
    assert "coverage_status" in scenario_properties
    assert "page_element_plan" in scenario_properties
    assert "terminal_boundary" in scenario_properties
    assert "resume_condition" in scenario_properties
    assert "evidence_source" in scenario_properties
    assert "fact_lineage" in scenario_properties


def test_r3_gate_reviews_agent3_trace_contract_artifacts():
    import yaml

    gate_config = yaml.safe_load((ROOT / "config" / "gate-operator.yaml").read_text(encoding="utf-8"))
    description = gate_config["gates"]["R3"]["description"]

    assert "coverage-report.md" not in description
    assert "exploration-contract.json" in description
    assert "path-exploration-results.json" in description
    assert "action-trace.json" in description

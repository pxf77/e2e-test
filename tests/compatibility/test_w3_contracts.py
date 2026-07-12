from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft7Validator


ROOT = Path(__file__).resolve().parents[2]
W3_MOCK_RUN_DIR = ROOT / "products" / "test-product" / "runs" / "w3-fullstack-mock-pilot-20260507"


def _schema(name: str) -> dict:
    return json.loads((ROOT / "schemas" / "v1" / name).read_text(encoding="utf-8"))


def test_w3_mock_pilot_artifacts_do_not_contain_placeholder_question_marks():
    offenders = []
    for path in sorted(W3_MOCK_RUN_DIR.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".json", ".jsonl", ".md"}:
            continue
        text = path.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), start=1):
            if "??" in line:
                rel_path = path.relative_to(ROOT).as_posix()
                offenders.append(f"{rel_path}:{line_number}: {line.strip()[:120]}")
                break

    assert offenders == []


@pytest.mark.asyncio
async def test_regression_paths_schema_accepts_page_key_governance_output(tmp_path, monkeypatch):
    from e2e_agent.legacy.agents import path_extract_agent

    monkeypatch.setattr(path_extract_agent, "_ROOT_DIR", tmp_path)

    result = await path_extract_agent.path_extract_node(
        {
            "product_id": "demo-product",
            "entry_url": "https://example.com/product/detail",
            "merged_cases": [
                {
                    "case_id": "CASE-001",
                    "title": "计划21主链路",
                    "priority": "P0",
                    "business_intent": "main_flow",
                    "scenario_type": "main_flow",
                    "steps": ["condition: productCode=DD0950", "主链路投保成功"],
                    "assertions": ["计划21可出单"],
                    "coverage_refs": [{"case_id": "MANUAL-001"}],
                    "data_variants": [{"type": "plan", "value": "计划21"}],
                }
            ],
        }
    )

    schema = _schema("regression-paths.schema.json")
    validator = Draft7Validator(schema)
    errors = sorted(validator.iter_errors(result["regression_paths"]), key=lambda err: err.path)

    assert errors == []
    path = result["regression_paths"][0]
    assert path["page_keys"][0]["matched_whitelist_patterns"]
    assert path["condition_sets"] == [path["conditions"]]
    assert path["source_path_count"] == 1


def test_artifact_fingerprint_record_matches_schema(tmp_path):
    from e2e_agent.artifacts.fingerprint import append_artifact_fingerprint

    record = append_artifact_fingerprint(
        root_dir=tmp_path,
        product_id="demo-product",
        run_id="run-w3",
        artifact_path="products/demo-product/agent3/page-registry.json",
        artifact_type="page-registry",
        payload={"pages": []},
        producer="explore_agent",
    )

    schema = _schema("artifact-fingerprint.schema.json")
    validator = Draft7Validator(schema)
    errors = sorted(validator.iter_errors(record), key=lambda err: err.path)

    assert errors == []
    assert record["model_routed"] == "deterministic-skill"
    assert record["model_primary"] is None
    assert record["is_fallback"] is False
    assert record["fallback_chain"] is None
    assert record["token_usage"] is None
    assert record["cost_usd"] is None
    assert record["content_hash"]


def test_artifact_fingerprint_schema_accepts_quarantine_artifact(tmp_path):
    from e2e_agent.artifacts.fingerprint import append_artifact_fingerprint

    record = append_artifact_fingerprint(
        root_dir=tmp_path,
        product_id="demo-product",
        run_id="run-w3",
        artifact_path="products/demo-product/agent4/quarantine.json",
        artifact_type="quarantine",
        payload={"summary": {"total": 0}, "items": []},
        producer="exec_healing_agent",
    )

    schema = _schema("artifact-fingerprint.schema.json")
    validator = Draft7Validator(schema)
    errors = sorted(validator.iter_errors(record), key=lambda err: err.path)

    assert errors == []


def test_dom_signature_bundle_defines_three_layers_and_output_path():
    from e2e_agent.core.dom_signature import build_dom_signature_bundle

    bundle = build_dom_signature_bundle(
        {
            "page_content_record_id": "PCR-001",
            "actual_url": "https://example.com/product/detail?_t=1700000000",
            "actual_page_key": "PK-product-detail",
            "body_text_excerpt": "保费 1234 元 2026-05-07 12:30:00 立即投保",
            "field_map": [
                {"field_key": "name", "tag": "input", "type": "text", "selector": "#name"}
            ],
            "selector_map": {
                "primary": {"selector": "#submit", "text": "立即投保", "tag": "button"}
            },
        },
        product_id="demo-product",
    )

    assert bundle["signature_version"] == "dom-signature-v1"
    assert bundle["structure_signature"]["hash"]
    assert bundle["component_fingerprints"][0]["component_type"] == "field"
    assert bundle["text_signature"]["normalized_text"] == "保费 <number> 元 <date> <time> 立即投保"
    assert bundle["output_path"] == "products/demo-product/agent3/dom-signatures/PCR-001.json"


def test_dom_signature_writer_sanitizes_record_id_for_filename(tmp_path):
    from e2e_agent.core.dom_signature import write_dom_signature_bundles

    index = write_dom_signature_bundles(
        root_dir=tmp_path,
        product_id="demo-product",
        records=[
            {
                "page_content_record_id": "../PCR:001",
                "actual_url": "https://example.com/product/detail",
                "body_text_excerpt": "产品详情",
            }
        ],
    )

    output_dir = tmp_path / "products" / "demo-product" / "agent3" / "dom-signatures"
    assert index["signatures"][0]["output_path"] == (
        "products/demo-product/agent3/dom-signatures/PCR-001.json"
    )
    assert (output_dir / "PCR-001.json").exists()
    assert not (tmp_path / "products" / "demo-product" / "agent3" / "PCR:001.json").exists()


def test_gate_operator_config_has_w3_operational_commands():
    import yaml

    config = yaml.safe_load((ROOT / "config" / "gate-operator.yaml").read_text(encoding="utf-8"))
    gates = config["gates"]

    assert gates["R1"]["owner"] != "TODO: 高级测试工程师"
    assert gates["R2"]["owner"] != "TODO: 高级测试工程师"
    assert gates["R3"]["owner"] != "TODO: AI 测试工程师"
    assert "resume_command" in gates["R1"]
    assert "approve_command" in gates["R2"]
    assert gates["R4"]["blocking"] is False

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_static_package(root: Path, product_id: str = "demo-product") -> None:
    base = root / "products" / product_id / "automation"
    _write_json(
        base / "product.config.json",
        {
            "product_id": product_id,
            "product_name": "Demo Product",
            "platform": "pc",
            "entry_url": "https://example.com/product/detail",
            "agent3_mode": "static-first",
            "default_flow_id": "main-purchase",
        },
    )
    _write_json(
        base / "page-models" / "product-detail.json",
        {
            "page_model_id": "PM-product-detail",
            "node_id": "NODE-product-detail",
            "page_key_pattern": "PK-product-detail",
            "url_patterns": ["/product/detail"],
            "match_contract": {"entry_signals": ["Product"], "exit_signals": ["Buy"]},
            "fields": [],
            "actions": [
                {
                    "action_key": "action.buy_now",
                    "text": "Buy",
                    "tag": "a",
                    "locators": [{"by": "selector", "value": "#buy-now"}],
                    "required": True,
                }
            ],
        },
    )
    _write_json(
        base / "page-models" / "insure-form.json",
        {
            "page_model_id": "PM-insure-form",
            "node_id": "NODE-insure-form",
            "page_key_pattern": "PK-product-insure",
            "url_patterns": ["/product/insure"],
            "match_contract": {"entry_signals": ["Applicant"], "exit_signals": ["Submit"]},
            "fields": [
                {
                    "field_key": "applicant.name",
                    "locators": [{"by": "selector", "value": "input[name='applicantName']"}],
                    "required": True,
                }
            ],
            "actions": [
                {
                    "action_key": "action.submit",
                    "text": "Submit",
                    "tag": "button",
                    "locators": [{"by": "selector", "value": "#submit"}],
                    "required": True,
                }
            ],
        },
    )
    _write_json(
        base / "page-models" / "policy-result.json",
        {
            "page_model_id": "PM-policy-result",
            "node_id": "NODE-policy-result",
            "page_key_pattern": "PK-result",
            "url_patterns": ["/result"],
            "match_contract": {"entry_signals": ["Success"], "exit_signals": ["Policy"]},
            "fields": [],
            "actions": [],
        },
    )
    _write_json(
        base / "flows" / "main-purchase.flow.json",
        {
            "flow_id": "main-purchase",
            "business_intent": "main_flow",
            "node_sequence": [
                "NODE-product-detail",
                "NODE-insure-form",
                "NODE-policy-result",
            ],
        },
    )
    _write_json(
        base / "scenarios" / "scenario-map.json",
        {
            "version": "1.0",
            "path_bindings": [
                {
                    "path_id": "PATH-001",
                    "flow_id": "main-purchase",
                    "test_data_profile_id": "default-main",
                    "expected_target_node": "NODE-policy-result",
                }
            ],
        },
    )
    _write_json(
        base / "test-data" / "default-main.json",
        {
            "profile_id": "default-main",
            "values": {"applicant.name": "Test User"},
        },
    )


def _state(product_id: str = "demo-product") -> dict:
    return {
        "product_id": product_id,
        "entry_url": "https://example.com/product/detail",
        "regression_flow": {
            "nodes": [
                {"node_id": "NODE-start", "type": "start"},
                {"node_id": "NODE-product-detail", "type": "form", "page_name": "Product Detail"},
                {"node_id": "NODE-insure-form", "type": "form", "page_name": "Insure Form"},
                {"node_id": "NODE-policy-result", "type": "result", "page_name": "Policy Result"},
                {"node_id": "NODE-end", "type": "end"},
            ]
        },
        "regression_paths": [
            {
                "path_id": "PATH-001",
                "case_ids": ["TC-001"],
                "nodes": [
                    "NODE-start",
                    "NODE-product-detail",
                    "NODE-insure-form",
                    "NODE-policy-result",
                    "NODE-end",
                ],
                "conditions": {},
                "page_keys": [
                    {"node_id": "NODE-product-detail", "page_key": "PK-product-detail", "state": {}},
                    {"node_id": "NODE-insure-form", "page_key": "PK-product-insure", "state": {}},
                    {"node_id": "NODE-policy-result", "page_key": "PK-result", "state": {}},
                ],
                "priority": "P0",
            }
        ],
    }


def test_static_product_package_compiles_agent4_contract(tmp_path) -> None:
    from e2e_agent.core.static_contract_builder import build_static_agent4_contract
    from e2e_agent.core.static_product_package import load_static_product_package

    _write_static_package(tmp_path)

    package = load_static_product_package(tmp_path, "demo-product")
    contract = build_static_agent4_contract(package, _state())

    page_registry = contract["page_registry"]
    path_result = page_registry["path_exploration_results"][0]
    scenario = contract["scenarios"][0]

    assert page_registry["generated_by"] == "explore_agent.static-first"
    assert path_result["path_status"] == "explored"
    assert path_result["completion_rule"]["source"] == "agent3.static-contract"
    assert path_result["completion_rule"]["missing_nodes"] == []
    assert scenario["coverage_status"] == "covered"
    # build_scenarios seeds mock_data from page_element_plan; profile values overlay.
    assert scenario["mock_data"]["applicant.name"] == "Test User"


def test_static_product_package_outputs_missing_report_and_targeted_probe_plan(tmp_path) -> None:
    from e2e_agent.core.static_contract_builder import build_static_agent4_contract
    from e2e_agent.core.static_product_package import load_static_product_package

    _write_static_package(tmp_path)
    insure_form_path = tmp_path / "products" / "demo-product" / "automation" / "page-models" / "insure-form.json"
    insure_form = json.loads(insure_form_path.read_text(encoding="utf-8"))
    insure_form["fields"][0]["locators"] = []
    insure_form["actions"][0]["locators"] = []
    _write_json(insure_form_path, insure_form)

    state = _state()
    state["regression_paths"][0]["nodes"].insert(3, "NODE-tax-info")
    state["regression_paths"][0]["page_keys"].append(
        {
            "node_id": "NODE-tax-info",
            "page_key": "PK-tax-info",
            "url_pattern": "/tax-info",
            "state": {},
        }
    )

    package = load_static_product_package(tmp_path, "demo-product")
    contract = build_static_agent4_contract(package, state)

    registry = contract["page_registry"]
    path_result = registry["path_exploration_results"][0]
    missing_report = registry["static_contract"]["missing_report"]

    assert path_result["path_status"] == "blocked"
    assert registry["targeted_probe_plan"]["summary"]["request_count"] == 3
    assert registry["static_contract"]["requires_targeted_probe"] is True
    assert registry["selector_override_priority"][0]["source"] == "targeted_probe_verified"
    assert missing_report["summary"] == {
        "missing_count": 3,
        "required_field_count": 1,
        "required_action_count": 1,
        "page_model_count": 1,
        "transition_action_count": 0,
    }
    assert missing_report["required_fields"][0]["owner"] == "ai-testing"
    assert missing_report["required_fields"][0]["probe_scope"] == "current_node_only"
    assert missing_report["required_actions"][0]["probe_strategy"] == "resolve_action_by_text_role_or_selector"
    assert missing_report["page_models"][0]["owner"] == "ai-fullstack"
    assert missing_report["page_models"][0]["probe_strategy"] == "discover_page_model_by_agent2_node"


def test_static_product_package_attaches_knowledge_evidence_to_missing_page_probe(tmp_path) -> None:
    from e2e_agent.core.static_contract_builder import build_static_agent4_contract
    from e2e_agent.core.static_product_package import load_static_product_package

    _write_static_package(tmp_path)
    state = _state()
    state["regression_paths"][0]["nodes"].insert(3, "NODE-payment")
    state["regression_paths"][0]["page_keys"].append(
        {
            "node_id": "NODE-payment",
            "page_key": "PK-payment",
            "url_pattern": "/payment",
            "state": {},
        }
    )
    knowledge_hints = {
        "source": "knowledge.agent3-hints",
        "version": "1.0",
        "product_id": "demo-product",
        "available": True,
        "summary": {
            "page_hint_count": 1,
            "observed_page_hint_count": 1,
            "field_hint_count": 0,
            "action_hint_count": 1,
        },
        "pages": [
            {
                "hint_id": "KAH-MCP-001",
                "node_id": "NODE-payment",
                "page_name": "支付页",
                "actual_url": "https://example.test/payment",
                "title": "支付",
                "evidence_status": "observed",
                "confidence": 0.8,
                "source": "mcp-page-snapshot",
                "entry_signals": ["支付"],
                "observed_actions": [
                    {
                        "action_key": "action.pay",
                        "text": "立即支付",
                        "locators": [{"by": "selector", "value": "#pay"}],
                        "source": "mcp-page-snapshot",
                        "evidence_status": "observed",
                    }
                ],
            }
        ],
        "field_hints": [],
        "warnings": [],
    }

    package = load_static_product_package(tmp_path, "demo-product")
    contract = build_static_agent4_contract(package, state, knowledge_hints=knowledge_hints)

    registry = contract["page_registry"]
    payment_request = [
        item for item in registry["targeted_probe_plan"]["requests"] if item.get("node_id") == "NODE-payment"
    ][0]

    assert registry["static_contract"]["knowledge_assist"]["available"] is True
    assert registry["static_contract"]["knowledge_assist"]["observed_page_hints"][0]["actual_url"] == (
        "https://example.test/payment"
    )
    assert registry["targeted_probe_plan"]["summary"]["knowledge_evidence_request_count"] == 1
    assert payment_request["reason"] == "missing_page_model"
    assert payment_request["knowledge_evidence"]["page_hints"][0]["actual_url"] == "https://example.test/payment"
    assert payment_request["knowledge_evidence"]["candidate_actions"][0]["action_key"] == "action.pay"
    assert registry["path_exploration_results"][0]["path_status"] == "blocked"
    assert registry["path_exploration_results"][0]["completion_rule"]["is_complete"] is False


def test_explore_static_first_contract_loads_knowledge_hints_from_product_root(monkeypatch, tmp_path) -> None:
    from e2e_agent.legacy.agents import explore_agent

    _write_static_package(tmp_path)
    state = _state()
    state["regression_paths"][0]["nodes"].insert(3, "NODE-payment")
    state["regression_paths"][0]["page_keys"].append(
        {
            "node_id": "NODE-payment",
            "page_key": "PK-payment",
            "url_pattern": "/payment",
            "state": {},
        }
    )
    knowledge_root = tmp_path / "knowledge" / "demo-product" / "mcp"
    _write_json(
        knowledge_root / "page-snapshots.json",
        {
            "schema_version": "1.0",
            "product_id": "demo-product",
            "snapshots": [
                {
                    "status": "completed",
                    "url": "https://example.test/payment",
                    "title": "支付",
                    "body_text_excerpt": "支付 立即支付 微信 支付宝",
                    "actions": [
                        {
                            "selector": "#pay",
                            "text": "立即支付",
                            "tag": "button",
                            "visible": True,
                        }
                    ],
                }
            ],
        },
    )

    monkeypatch.setattr(explore_agent, "_ROOT_DIR", tmp_path)

    contract = explore_agent._static_first_contract(tmp_path, state)

    assert contract is not None
    registry = contract["page_registry"]
    request = [
        item for item in registry["targeted_probe_plan"]["requests"] if item.get("node_id") == "NODE-payment"
    ][0]
    assert registry["static_contract"]["knowledge_assist"]["available"] is True
    assert request["knowledge_evidence"]["page_hints"][0]["actual_url"] == "https://example.test/payment"


def test_explore_static_first_contract_uses_source_dir_knowledge_alias(monkeypatch, tmp_path) -> None:
    from e2e_agent.legacy.agents import explore_agent

    product_source_dir = tmp_path / "products" / "demo-product" / "eman"
    _write_static_package(tmp_path, product_id="demo-product/eman")
    state = _state("demo-product")
    state["product_source_dir"] = str(product_source_dir)
    state["regression_paths"][0]["nodes"].insert(3, "NODE-payment")
    state["regression_paths"][0]["page_keys"].append(
        {
            "node_id": "NODE-payment",
            "page_key": "PK-payment",
            "url_pattern": "/payment",
            "state": {},
        }
    )
    _write_json(
        tmp_path / "knowledge" / "demo-product" / "eman" / "mcp" / "page-snapshots.json",
        {
            "schema_version": "1.0",
            "product_id": "demo-product/eman",
            "snapshots": [
                {
                    "status": "completed",
                    "url": "https://example.test/payment",
                    "title": "支付",
                    "body_text_excerpt": "支付 立即支付",
                    "actions": [{"selector": "#pay", "text": "立即支付", "tag": "button"}],
                }
            ],
        },
    )

    monkeypatch.setattr(explore_agent, "_ROOT_DIR", tmp_path)

    contract = explore_agent._static_first_contract(tmp_path, state)

    assert contract is not None
    registry = contract["page_registry"]
    request = [
        item for item in registry["targeted_probe_plan"]["requests"] if item.get("node_id") == "NODE-payment"
    ][0]
    assert registry["static_contract"]["knowledge_assist"]["product_id"] == "demo-product/eman"
    assert request["knowledge_evidence"]["page_hints"][0]["actual_url"] == "https://example.test/payment"


def test_static_product_package_requires_selector_probe_for_label_only_fields(tmp_path) -> None:
    from e2e_agent.core.static_contract_builder import build_static_agent4_contract
    from e2e_agent.core.static_product_package import load_static_product_package

    _write_static_package(tmp_path)
    insure_form_path = tmp_path / "products" / "demo-product" / "automation" / "page-models" / "insure-form.json"
    insure_form = json.loads(insure_form_path.read_text(encoding="utf-8"))
    insure_form["fields"][0]["locators"] = [{"by": "label_text", "value": "Applicant Name"}]
    _write_json(insure_form_path, insure_form)

    package = load_static_product_package(tmp_path, "demo-product")
    contract = build_static_agent4_contract(package, _state())

    registry = contract["page_registry"]
    request = registry["targeted_probe_plan"]["requests"][0]

    assert registry["path_exploration_results"][0]["path_status"] == "blocked"
    assert request["kind"] == "field"
    assert request["field_key"] == "applicant.name"
    assert request["current_locator_status"] == "candidate_only"
    assert request["candidate_locators"] == [{"by": "label_text", "value": "Applicant Name"}]
    assert registry["missing_report"]["summary"]["required_field_count"] == 1


def test_static_product_package_loads_single_element_set(tmp_path) -> None:
    from e2e_agent.core.static_product_package import load_static_product_package

    base = tmp_path / "products" / "test-product" / "automation"
    _write_json(
        base / "element-set.json",
        {
            "product_config": {
                "product_id": "test-product",
                "agent3_mode": "static-first",
                "default_flow_id": "main-purchase",
            },
            "page_models": {
                "product-detail": {
                    "page_model_id": "PM-product-detail",
                    "node_id": "NODE-product-detail",
                    "fields": [],
                    "actions": [],
                }
            },
        },
    )
    _write_json(
        base / "flows" / "main-purchase.flow.json",
        {
            "flow_id": "main-purchase",
            "node_sequence": ["NODE-product-detail"],
        },
    )

    package = load_static_product_package(tmp_path, "test-product")

    assert package.config["agent3_mode"] == "static-first"
    assert package.page_models[0]["node_id"] == "NODE-product-detail"
    assert package.flows[0]["flow_id"] == "main-purchase"


def test_static_product_package_can_load_from_source_dir(tmp_path) -> None:
    from e2e_agent.core.static_product_package import load_static_product_package

    source_dir = tmp_path / "products" / "demo-product" / "eman"
    base = source_dir / "automation"
    _write_json(
        base / "product.config.json",
        {
            "product_id": "demo-product",
            "agent3_mode": "static-first",
            "default_flow_id": "main-purchase",
        },
    )
    _write_json(
        base / "page-models" / "product-detail.json",
        {
            "page_model_id": "PM-product-detail",
            "node_id": "NODE-product-detail",
            "fields": [],
            "actions": [],
        },
    )

    package = load_static_product_package(
        tmp_path,
        "demo-product",
        product_source_dir=source_dir,
    )

    assert package.product_id == "demo-product"
    assert package.automation_dir == source_dir / "automation"
    assert package.page_models


def test_static_product_package_auto_discovers_source_package(tmp_path) -> None:
    from e2e_agent.core.static_product_package import load_static_product_package

    source_dir = tmp_path / "products" / "test-product" / "eman"
    base = source_dir / "automation"
    (source_dir / "product-input.json").parent.mkdir(parents=True)
    (source_dir / "product-input.json").write_text('{"product_id": "test-product"}', encoding="utf-8")
    _write_json(
        base / "product.config.json",
        {
            "product_id": "test-product",
            "agent3_mode": "static-first",
            "default_flow_id": "main-purchase",
        },
    )
    _write_json(
        base / "page-models" / "product-detail.json",
        {
            "page_model_id": "PM-product-detail",
            "node_id": "NODE-product-detail",
            "fields": [],
            "actions": [],
        },
    )

    package = load_static_product_package(tmp_path, "test-product")

    assert package.automation_dir == source_dir / "automation"
    assert package.page_models[0]["node_id"] == "NODE-product-detail"


def test_static_product_package_accepts_utf8_bom_json(tmp_path) -> None:
    from e2e_agent.core.static_product_package import load_static_product_package

    source_dir = tmp_path / "products" / "demo-product" / "eman"
    base = source_dir / "automation"
    _write_json(
        base / "product.config.json",
        {
            "product_id": "demo-product",
            "agent3_mode": "static-first",
        },
    )
    page_model = {
        "page_model_id": "PM-product-detail",
        "node_id": "NODE-product-detail",
        "page_key_pattern": "PK-product-detail",
        "url_patterns": ["/product/detail"],
        "fields": [],
        "actions": [],
    }
    page_model_path = base / "page-models" / "product-detail.json"
    page_model_path.parent.mkdir(parents=True, exist_ok=True)
    page_model_path.write_bytes(
        b"\xef\xbb\xbf" + json.dumps(page_model, ensure_ascii=False).encode("utf-8")
    )

    package = load_static_product_package(
        tmp_path,
        "demo-product",
        product_source_dir=source_dir,
    )

    assert package.page_models[0]["node_id"] == "NODE-product-detail"


def test_live_agent3_mode_ignores_static_product_package(tmp_path) -> None:
    from e2e_agent.legacy.agents import explore_agent

    _write_static_package(tmp_path)

    assert explore_agent._static_first_contract(
        tmp_path,
        {
            **_state(),
            "agent3_mode": "live",
            "product_config": {"agent3_mode": "live"},
        },
    ) is None


@pytest.mark.asyncio
async def test_explore_node_static_first_uses_package_without_live_browser(tmp_path, monkeypatch) -> None:
    from e2e_agent.legacy.agents import explore_agent

    _write_static_package(tmp_path)

    async def fail_live_explore(**_: object) -> dict:
        raise AssertionError("static-first should not open the browser")

    monkeypatch.setattr(explore_agent, "_ROOT_DIR", tmp_path)
    monkeypatch.setattr(explore_agent, "run_live_exploration", fail_live_explore)

    result = await explore_agent.explore_node(
        {
            **_state(),
            "artifact_root_dir": str(tmp_path),
            "run_id": "run-static",
        }
    )

    assert result["page_registry"]["generated_by"] == "explore_agent.static-first"
    assert result["scenarios"][0]["coverage_status"] == "covered"
    assert result["artifact_fingerprints"][-1]["artifact_type"] == "assertion-results"


@pytest.mark.asyncio
async def test_explore_node_falls_back_to_live_when_static_package_incomplete_and_live_supported(
    tmp_path,
    monkeypatch,
) -> None:
    from e2e_agent.legacy.agents import explore_agent

    _write_static_package(tmp_path)
    config_path = tmp_path / "products" / "demo-product" / "automation" / "product.config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["supports"] = {"live_probe": True}
    _write_json(config_path, config)
    (tmp_path / "products" / "demo-product" / "automation" / "page-models" / "policy-result.json").unlink()

    async def fake_live_explore(**kwargs: object) -> dict:
        return {
            "page_registry": {
                "product_id": "demo-product",
                "entry_url": kwargs.get("entry_url"),
                "generated_by": "explore_agent.live",
                "pages": [],
                "path_exploration_results": [
                    {
                        "path_id": "PATH-001",
                        "path_status": "explored",
                        "completion_rule": {"source": "agent3.live", "is_complete": True},
                    }
                ],
                "exploration_contract": {
                    "version": "agent3-path-contract-v2",
                    "is_complete": True,
                    "blocked_count": 0,
                    "blocked_paths": [],
                },
            },
            "explore_trace": {"warnings": []},
            "warnings": [],
        }

    monkeypatch.setattr(explore_agent, "_ROOT_DIR", tmp_path)
    monkeypatch.setattr(explore_agent, "run_live_exploration", fake_live_explore)

    state = {
        **_state(),
        "artifact_root_dir": str(tmp_path),
        "product_config": config,
        "run_id": "run-static-fallback-live",
    }
    result = await explore_agent.explore_node(state)

    assert result["page_registry"]["generated_by"] == "explore_agent.live"
    assert result["error"] is None


@pytest.mark.asyncio
async def test_explore_node_reads_static_package_from_source_and_writes_assets(tmp_path, monkeypatch) -> None:
    from e2e_agent.legacy.agents import explore_agent

    product_source_dir = tmp_path / "products" / "demo-product" / "eman"
    product_artifact_dir = tmp_path / "products" / "demo-product" / "demo.assets"
    base = product_source_dir / "automation"
    _write_json(
        base / "product.config.json",
        {
            "product_id": "demo-product",
            "product_name": "Demo Product",
            "platform": "pc",
            "entry_url": "https://example.com/product/detail",
            "agent3_mode": "static-first",
            "default_flow_id": "main-purchase",
        },
    )
    _write_json(
        base / "page-models" / "product-detail.json",
        {
            "page_model_id": "PM-product-detail",
            "node_id": "NODE-product-detail",
            "page_key_pattern": "PK-product-detail",
            "url_patterns": ["/product/detail"],
            "match_contract": {"entry_signals": ["Product"], "exit_signals": ["Buy"]},
            "fields": [],
            "actions": [
                {
                    "action_key": "action.buy_now",
                    "text": "Buy",
                    "tag": "a",
                    "locators": [{"by": "selector", "value": "#buy-now"}],
                    "required": True,
                }
            ],
        },
    )
    _write_json(
        base / "page-models" / "insure-form.json",
        {
            "page_model_id": "PM-insure-form",
            "node_id": "NODE-insure-form",
            "page_key_pattern": "PK-product-insure",
            "url_patterns": ["/product/insure"],
            "match_contract": {"entry_signals": ["Applicant"], "exit_signals": ["Submit"]},
            "fields": [],
            "actions": [
                {
                    "action_key": "action.submit",
                    "text": "Submit",
                    "tag": "button",
                    "locators": [{"by": "selector", "value": "#submit"}],
                    "required": True,
                }
            ],
        },
    )
    _write_json(
        base / "page-models" / "policy-result.json",
        {
            "page_model_id": "PM-policy-result",
            "node_id": "NODE-policy-result",
            "page_key_pattern": "PK-result",
            "url_patterns": ["/result"],
            "match_contract": {"entry_signals": ["Success"], "exit_signals": ["Policy"]},
            "fields": [],
            "actions": [],
        },
    )
    _write_json(
        base / "flows" / "main-purchase.flow.json",
        {
            "flow_id": "main-purchase",
            "business_intent": "main_flow",
            "node_sequence": [
                "NODE-product-detail",
                "NODE-insure-form",
                "NODE-policy-result",
            ],
        },
    )
    _write_json(
        base / "scenarios" / "scenario-map.json",
        {
            "version": "1.0",
            "path_bindings": [
                {
                    "path_id": "PATH-001",
                    "flow_id": "main-purchase",
                    "test_data_profile_id": "default-main",
                    "expected_target_node": "NODE-policy-result",
                }
            ],
        },
    )

    async def fail_live_explore(**_: object) -> dict:
        raise AssertionError("static-first should not open the browser")

    monkeypatch.setattr(explore_agent, "_ROOT_DIR", tmp_path)
    monkeypatch.setattr(explore_agent, "run_live_exploration", fail_live_explore)

    state = _state("demo-product")
    state["product_source_dir"] = str(product_source_dir)
    state["product_artifact_dir"] = str(product_artifact_dir)
    state["artifact_root_dir"] = str(tmp_path)
    state["run_id"] = "run-static-assets"

    result = await explore_agent.explore_node(state)

    assert result["product_artifact_dir"] == str(product_artifact_dir)
    assert result["page_registry"]["generated_by"] == "explore_agent.static-first"
    assert (product_artifact_dir / "agent3" / "page-registry.json").exists()
    assert (product_artifact_dir / "agent3" / "ts-gen" / "tc-execution-plan.json").exists()
    assert (product_artifact_dir / "agent3" / "script-bundle.json").exists()
    assert (product_artifact_dir / "artifact-fingerprints.jsonl").exists()
    assert not (tmp_path / "products" / "demo-product" / "agent3").exists()
    assert not (product_source_dir / "agent3").exists()
    assert result["scenarios"][0]["script_status"] in {"generated", "blocked", "invalid"}
    assert result["scenarios"][0]["script_status"] != "pending_generation"
    assert result["script_bundle"]["scenario_count"] == 1
    assert result["script_bundle"]["root_dir"] == str(product_artifact_dir / "agent3" / "ts-gen")
    script_bundle = json.loads((product_artifact_dir / "agent3" / "script-bundle.json").read_text(encoding="utf-8"))
    assert script_bundle["scenario_count"] == 1
    assert script_bundle["root_dir"] == str(product_artifact_dir / "agent3" / "ts-gen")
    chain_spec = product_artifact_dir / "agent3" / "ts-gen" / ".artifacts" / "chain-to-policy-result-pc.spec.ts"
    assert "products/demo-product/demo.assets/agent3/ts-gen/.artifacts/chain-to-policy-result-pc.spec.ts" in (
        chain_spec.read_text(encoding="utf-8")
    )
    assert result["artifact_fingerprints"][-1]["artifact_path"] == (
        "products/demo-product/demo.assets/agent3/assertion-results.json"
    )


def test_static_product_package_loader_reads_temp_fixture(tmp_path: Path) -> None:
    from e2e_agent.core.static_product_package import load_static_product_package

    root = tmp_path
    _write_static_package(root, product_id="test-product")

    package = load_static_product_package(root, "test-product")

    assert package.config["agent3_mode"] == "static-first"
    assert package.page_models
    assert package.flows


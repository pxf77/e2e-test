from __future__ import annotations

import json


def _sample_page_registry() -> dict:
    return {
        "product_id": "demo-product",
        "entry_url": "https://example.com/apps/cps/product/insure",
        "platform": "pc",
        "generated_by": "explore_agent.live",
        "page_content_records": [
            {
                "page_content_record_id": "PCR-DEMO",
                "actual_url": "https://example.com/apps/cps/product/insure",
                "actual_page_key": "apps-cps-product-insure",
                "title": "投保信息页",
                "dom_signature": "sha256:demo",
                "body_text_excerpt": "投保人信息 被保险人信息 手机号码 提交投保单",
                "matched_node_ids": ["NODE-insure-form"],
                "source_path_ids": ["PATH-001"],
                "field_map": [
                    {
                        "field_key": "partnerDomain",
                        "selector": "#partnerDomain",
                        "source": "dom",
                        "required": False,
                        "raw": {
                            "name": "partnerDomain",
                            "tag": "input",
                            "type": "hidden",
                            "required": False,
                        },
                    },
                    {
                        "field_key": "moblie_10",
                        "selector": "input[name=\"moblie_10\"]",
                        "source": "dom",
                        "required": False,
                        "raw": {
                            "name": "moblie_10",
                            "label": "*手机号码",
                            "placeholder": "",
                            "tag": "input",
                            "type": "tel",
                            "required": True,
                        },
                    },
                    {
                        "field_key": "insured_noProblem",
                        "selector": "input[name=\"insured_noProblem\"]",
                        "source": "dom",
                        "required": False,
                        "raw": {
                            "name": "insured_noProblem",
                            "tag": "input",
                            "type": "button",
                            "required": False,
                        },
                    }
                ],
                "selector_map": {
                    "actions": [
                        {
                            "text": "保险首页",
                            "selector": "a:nth-of-type(1)",
                            "tag": "a",
                        },
                        {
                            "text": "《关系声明确认书》",
                            "selector": "a:nth-of-type(2)",
                            "tag": "a",
                        },
                        {
                            "text": "A.我希望购买保障型产品",
                            "selector": "input:nth-of-type(1)",
                            "tag": "input",
                        },
                        {
                            "text": "提交投保单",
                            "selector": ".js-adapt-question-btn",
                            "tag": "button",
                        }
                    ]
                },
            }
        ],
    }


def test_build_element_set_bundle_from_page_registry():
    from e2e_agent.core.element_set_generation import build_element_set_bundle

    bundle = build_element_set_bundle(_sample_page_registry())

    assert bundle["product_config"]["agent3_mode"] == "static-first"
    assert bundle["product_config"]["product_id"] == "demo-product"
    assert len(bundle["field_semantic"]["fields"]) == 1
    assert bundle["field_semantic"]["fields"][0]["field_key"] == "applicant.mobile"
    assert [item["action_key"] for item in bundle["action_semantic"]["actions"]] == [
        "action.answer_option",
        "action.submit",
    ]

    page_model = bundle["page_models"][0]["content"]
    assert bundle["page_models"][0]["filename"] == "insure-form.json"
    assert page_model["page_model_id"] == "PM-insure-form"
    assert page_model["node_id"] == "NODE-insure-form"
    assert page_model["fields"][0]["field_key"] == "applicant.mobile"
    assert page_model["fields"][0]["locators"][0] == {
        "by": "selector",
        "value": "input[name=\"moblie_10\"]",
    }
    assert {"by": "label_text", "value": "*手机号码"} in page_model["fields"][0]["locators"]
    assert page_model["fields"][0]["required"] is True
    assert page_model["actions"][0]["action_key"] == "action.answer_option"
    assert page_model["actions"][0]["required"] is False
    assert page_model["actions"][1]["action_key"] == "action.submit"
    assert page_model["actions"][1]["required"] is True


def test_agent3_static_element_set_asset_loads_from_agent_package():
    from e2e_agent.agents.agent3_explore.element_set import load_static_element_set

    element_set = load_static_element_set()

    assert element_set["layout"]["runtime_dependency"] == "builtin generic element set"
    assert element_set["summary"]["embedded_legacy_ts_count"] == 0
    assert "NODE-insure-form" in element_set["quick_lookup"]["by_node"]


def test_static_contract_builder_compiles_page_registry_without_browser():
    from e2e_agent.agents.agent3_explore.element_set import load_static_element_set
    from e2e_agent.agents.agent3_explore.static_contract import build_static_explore_artifacts

    artifacts = build_static_explore_artifacts(
        product_id="demo-product",
        entry_url="https://example.com/product/detail",
        regression_paths=[
            {
                "path_id": "PATH-001",
                "case_ids": ["CASE-001"],
                "nodes": [
                    "NODE-start",
                    "NODE-product-detail",
                    "NODE-insure-form",
                    "NODE-policy-result",
                    "NODE-end",
                ],
            }
        ],
        element_set=load_static_element_set(),
    )

    page_registry = artifacts["page_registry"]
    assert page_registry["generated_by"] == "agent3.static-first"
    assert page_registry["page_content_records"]
    assert page_registry["path_exploration_results"][0]["path_status"] == "explored"
    assert page_registry["path_exploration_results"][0]["completion_rule"]["source"] == "agent3.static-contract"
    assert page_registry["path_exploration_results"][0]["completion_rule"]["is_complete"] is True
    assert "NODE-insure-form" in {
        node_id
        for record in page_registry["page_content_records"]
        for node_id in record["matched_node_ids"]
    }


def test_static_contract_builder_compiles_ordered_main_chain_actions():
    from e2e_agent.agents.agent3_explore.element_set import load_static_element_set
    from e2e_agent.agents.agent3_explore.static_contract import build_static_explore_artifacts

    artifacts = build_static_explore_artifacts(
        product_id="demo-product",
        entry_url="https://example.com/product/detail",
        regression_paths=[
            {
                "path_id": "PATH-001",
                "case_ids": ["CASE-001"],
                "nodes": [
                    "NODE-start",
                    "NODE-product-detail",
                    "NODE-insure-form",
                    "NODE-policy-result",
                    "NODE-end",
                ],
            }
        ],
        element_set=load_static_element_set(),
    )

    action_chain = artifacts["page_registry"]["path_exploration_results"][0]["action_chain"]

    assert len(action_chain) >= 3
    assert action_chain[0]["planned_from_node_id"] == "NODE-product-detail"
    assert action_chain[0]["planned_to_node_id"] == "NODE-insure-form"
    assert action_chain[0]["action_key"] == "action.buy_now"
    assert action_chain[0]["selector"] == "#submit-by"
    assert action_chain[1]["planned_from_node_id"] == "NODE-product-detail"
    assert action_chain[1]["action_key"] == "action.agree_all"
    assert action_chain[1]["text"] == "Agree and Continue"
    assert any(
        action["planned_from_node_id"] == "NODE-insure-form"
        and action["planned_to_node_id"] == "NODE-policy-result"
        and action["action_key"] == "action.submit"
        for action in action_chain
    )
    form_actions = [
        action
        for action in action_chain
        if action["planned_from_node_id"] == "NODE-insure-form"
    ]
    assert "Agree" in form_actions[0]["text"]
    assert form_actions[-1]["action_key"] == "action.submit"
    assert all(action.get("script_step_id") for action in action_chain)


def test_static_contract_builder_inserts_questionnaire_answer_before_continue():
    from e2e_agent.agents.agent3_explore.element_set import load_static_element_set
    from e2e_agent.agents.agent3_explore.static_contract import build_static_explore_artifacts

    artifacts = build_static_explore_artifacts(
        product_id="demo-product",
        entry_url="https://example.com/product/detail",
        regression_paths=[
            {
                "path_id": "PATH-001",
                "case_ids": ["CASE-001"],
                "nodes": [
                    "NODE-start",
                    "NODE-product-detail",
                    "NODE-suitability",
                    "NODE-insure-form",
                    "NODE-policy-result",
                    "NODE-end",
                ],
            }
        ],
        element_set=load_static_element_set(),
    )

    action_chain = artifacts["page_registry"]["path_exploration_results"][0]["action_chain"]
    suitability_actions = [
        action
        for action in action_chain
        if action["planned_from_node_id"] == "NODE-suitability"
    ]

    assert suitability_actions[0]["action_key"] == "action.answer_questionnaire"
    assert suitability_actions[0]["answer_strategy"] == "business_questionnaire_rule"
    assert suitability_actions[0]["expected_next_node_id"] == "NODE-suitability"
    assert suitability_actions[1]["text"] == "Submit"
    assert suitability_actions[1]["expected_next_node_id"] == "NODE-insure-form"
    assert suitability_actions[2]["text"] == "Continue Application"
    assert suitability_actions[2]["expected_next_node_id"] == "NODE-insure-form"


def test_materialise_element_set_writes_single_product_automation_file(tmp_path):
    from e2e_agent.core.element_set_generation import materialise_element_set_from_page_registry

    result = materialise_element_set_from_page_registry(
        root_dir=tmp_path,
        product_id="demo-product",
        page_registry=_sample_page_registry(),
    )

    automation_dir = tmp_path / "products" / "demo-product" / "automation"
    assert result["page_model_count"] == 1
    assert result["field_count"] == 1
    assert result["action_count"] == 2
    assert result["generated_files"] == [str(automation_dir / "element-set.json")]
    assert (automation_dir / "element-set.json").exists()
    assert not (automation_dir / "product.config.json").exists()
    assert not (automation_dir / "semantic-library").exists()
    assert not (automation_dir / "page-models").exists()
    assert not (automation_dir / "probe").exists()
    assert not (automation_dir / "element-set.index.json").exists()

    element_set = json.loads((automation_dir / "element-set.json").read_text(encoding="utf-8"))
    page_model = element_set["page_models"]["insure-form"]
    assert page_model["fields"][0]["field_key"] == "applicant.mobile"
    assert element_set["quick_lookup"]["by_node"]["NODE-insure-form"] == "#/page_models/insure-form"


def test_materialise_element_set_does_not_overwrite_with_empty_registry(tmp_path):
    from e2e_agent.core.element_set_generation import materialise_element_set_from_page_registry

    materialise_element_set_from_page_registry(
        root_dir=tmp_path,
        product_id="demo-product",
        page_registry=_sample_page_registry(),
    )
    element_set_path = tmp_path / "products" / "demo-product" / "automation" / "element-set.json"
    before = element_set_path.read_text(encoding="utf-8")

    result = materialise_element_set_from_page_registry(
        root_dir=tmp_path,
        product_id="demo-product",
        page_registry={"product_id": "demo-product", "page_content_records": []},
    )

    assert result["page_model_count"] == 0
    assert result["generated_files"] == []
    assert element_set_path.read_text(encoding="utf-8") == before

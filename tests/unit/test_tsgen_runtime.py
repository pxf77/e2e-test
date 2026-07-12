from __future__ import annotations
import json
import re
from pathlib import Path


def _single_path_state() -> dict:
    return {
        "product_id": "demo-product",
        "entry_url": "https://example.com/apps/cps/product/detail",
        "regression_flow": {
            "nodes": [
                {"node_id": "NODE-start", "type": "start"},
                {"node_id": "NODE-product-detail", "type": "form", "page_name": "Product Detail"},
                {"node_id": "NODE-end", "type": "end"},
            ]
        },
        "regression_paths": [
            {
                "path_id": "PATH-001",
                "nodes": ["NODE-start", "NODE-product-detail", "NODE-end"],
                "case_ids": ["TC-001"],
            }
        ],
        "page_registry": {
            "page_content_records": [
                {
                    "page_content_record_id": "PCR-001",
                    "actual_url": "https://example.com/apps/cps/product/detail",
                    "matched_node_ids": ["NODE-product-detail"],
                    "field_map": [
                        {
                            "field_key": "form.keyword",
                            "selector": "input[name='keyWord']",
                            "required": False,
                            "raw": {"tag": "input", "type": "text", "label": "keyword"},
                        }
                    ],
                    "selector_map": {
                        "actions": [
                            {
                                "action_key": "action.buy_now",
                                "text": "buy now",
                                "selector": "#submit-by",
                                "tag": "button",
                                "required": True,
                                "source_url": "https://example.com/apps/cps/product/detail",
                            }
                        ]
                    },
                }
            ],
            "path_exploration_results": [
                {
                    "path_id": "PATH-001",
                    "path_status": "explored",
                    "target_node": "NODE-product-detail",
                    "planned_page_refs": ["product-detail"],
                    "page_content_refs": ["PCR-001"],
                    "node_progress": [{"node_id": "NODE-product-detail", "status": "matched"}],
                    "completion_rule": {
                        "source": "agent3.static-contract",
                        "target_node": "NODE-product-detail",
                        "required_nodes": ["NODE-product-detail"],
                        "matched_nodes": ["NODE-product-detail"],
                        "missing_nodes": [],
                        "is_complete": True,
                    },
                }
            ],
            "exploration_contract": {
                "completed_paths": [{"path_id": "PATH-001"}],
                "blocked_paths": [],
            },
        },
    }


def test_tsgen_generates_coverage_gap_scenarios_for_blocked_agent3_paths():
    from e2e_agent.core import script_generation as runtime

    state = {
        "product_id": "demo-product",
        "entry_url": "https://example.com/apps/cps/product/detail",
        "regression_paths": [
            {
                "path_id": "PATH-000",
                "nodes": ["NODE-start", "NODE-detail", "NODE-end"],
                "case_ids": ["TC-000"],
            },
            {
                "path_id": "PATH-001",
                "nodes": ["NODE-start", "NODE-detail", "NODE-confirm", "NODE-end"],
                "case_ids": ["TC-001"],
            }
        ],
        "page_registry": {
            "page_content_records": [],
            "planned_page_catalog": [],
            "exploration_contract": {
                "version": "agent3-path-contract-v1",
                "policy": "complete_paths_only_enter_agent4",
                "retry_limit": 3,
                "phase1_contract": {
                    "exploration_mode": "path-driven",
                    "leaf_contract_mode": "observe",
                },
                "completed_paths": [{"path_id": "PATH-000"}],
                "blocked_paths": [
                    {
                        "path_id": "PATH-001",
                        "target_node": "NODE-confirm",
                        "blocked_node": "NODE-confirm",
                        "blocked_reason": "No executable primary action found for Agent2 planned path",
                        "missing_nodes": ["NODE-confirm"],
                        "terminal_boundary": {
                            "boundary_node": "NODE-confirm",
                            "classification": "coverage_gap",
                        },
                        "resume_condition": "rerun Agent3 exploration after adding a reachable action",
                        "evidence_source": "agent3-live-browser",
                    }
                ],
            },
            "path_exploration_results": [
                {
                    "path_id": "PATH-000",
                    "path_status": "explored",
                    "target_node": "NODE-detail",
                    "node_progress": [{"node_id": "NODE-detail", "status": "matched"}],
                    "completion_rule": {
                        "source": "agent2.nodes",
                        "target_node": "NODE-detail",
                        "required_nodes": ["NODE-detail"],
                        "matched_nodes": ["NODE-detail"],
                        "missing_nodes": [],
                        "is_complete": True,
                    },
                },
                {
                    "path_id": "PATH-001",
                    "path_status": "blocked",
                    "target_node": "NODE-confirm",
                    "blocked_node": "NODE-confirm",
                    "blocked_reason": "No executable primary action found for Agent2 planned path",
                    "terminal_boundary": {
                        "boundary_node": "NODE-confirm",
                        "classification": "coverage_gap",
                    },
                    "resume_condition": "rerun Agent3 exploration after adding a reachable action",
                    "evidence_source": "agent3-live-browser",
                    "node_progress": [
                        {"node_id": "NODE-detail", "status": "matched"},
                        {"node_id": "NODE-confirm", "status": "blocked"},
                    ],
                    "completion_rule": {
                        "source": "agent2.nodes",
                        "target_node": "NODE-confirm",
                        "required_nodes": ["NODE-detail", "NODE-confirm"],
                        "matched_nodes": ["NODE-detail"],
                        "missing_nodes": ["NODE-confirm"],
                        "is_complete": False,
                    },
                }
            ],
        },
    }

    scenarios = runtime.build_scenarios(state)
    plan = runtime.build_script_plan(state, scenarios)

    assert [scenario["path_id"] for scenario in scenarios] == ["PATH-000", "PATH-001"]
    assert scenarios[0]["target_node"] == "NODE-detail"
    assert scenarios[0]["coverage_status"] == "covered"
    assert scenarios[1]["target_node"] == "NODE-confirm"
    assert scenarios[1]["coverage_status"] == "coverage-gap"
    assert scenarios[1]["blocked_node"] == "NODE-confirm"
    assert scenarios[1]["terminal_boundary"]["classification"] == "coverage_gap"
    assert scenarios[1]["resume_condition"] == "rerun Agent3 exploration after adding a reachable action"
    assert scenarios[1]["evidence_source"] == "agent3-live-browser"
    assert plan["scenario_plans"][0]["completion_rule"]["is_complete"] is True
    assert plan["scenario_plans"][0]["coverage_status"] == "covered"
    assert plan["scenario_plans"][1]["coverage_status"] == "coverage-gap"
    assert plan["scenario_plans"][1]["terminal_boundary"]["boundary_node"] == "NODE-confirm"
    assert plan["scenario_plans"][1]["resume_condition"] == "rerun Agent3 exploration after adding a reachable action"
    assert plan["agent3_contract"]["retry_limit"] == 3
    assert plan["agent3_contract"]["phase1_contract"]["leaf_contract_mode"] == "observe"
    assert plan["blocked_path_plans"][0]["path_id"] == "PATH-001"


def test_tsgen_quotes_non_identifier_param_names():
    from e2e_agent.core import script_generation as runtime

    rendered = runtime.render_param_interface(
        "fillProductDetail",
        [{"name": "form.keyword", "type": "string", "required": False}],
    )

    assert '"form.keyword"?: string;' in rendered
    assert "form.keyword?: string;" not in rendered


def test_tsgen_builds_final_agent3_script_contract(tmp_path):
    from e2e_agent.core import script_generation as runtime

    result = runtime.build_ts_gen_bundle(
        _single_path_state(),
        root_dir=tmp_path,
        materialise=True,
    )

    scenario = result["scenarios"][0]
    script_bundle = result["script_bundle"]

    assert scenario["contract_status"] == "compiled"
    assert scenario["script_status"] == "generated"
    assert scenario["script_validation_status"] == "not_run"
    assert scenario["runtime_status"] == "not_executed"
    assert script_bundle["status"] == "generated"
    assert script_bundle["validation"]["status"] == "not_run"
    assert script_bundle["spec_files"][0]["exists"] is True
    assert script_bundle["page_function_files"][0]["exists"] is True
    assert Path(script_bundle["spec_files"][0]["absolute_path"]).is_absolute()
    assert Path(script_bundle["page_function_files"][0]["absolute_path"]).is_absolute()


def test_tsgen_materialises_e2e_test_style_chain_artifacts(tmp_path):
    from e2e_agent.core import script_generation as runtime

    runtime.build_ts_gen_bundle(
        _single_path_state(),
        root_dir=tmp_path,
        materialise=True,
    )

    ts_root = tmp_path / "products" / "demo-product" / "agent3" / "ts-gen"
    chain_manifest = ts_root / ".artifacts" / "chain-manifest.json"
    chain_spec = ts_root / ".artifacts" / "chain-to-product-detail-pc.spec.ts"

    assert (ts_root / "fixtures.ts").exists()
    assert (ts_root / "generate-test-data.ts").exists()
    assert (ts_root / "tc-execution-plan.json").exists()
    assert (ts_root / "spec-tc-index.json").exists()
    assert chain_manifest.exists()
    assert chain_spec.exists()
    rendered = chain_spec.read_text(encoding="utf-8")
    assert "@chain       chain-to-product-detail-pc" in rendered
    assert "import { test, expect } from '../fixtures';" in rendered
    assert "../pc/page-functions/01-product-detail" in rendered
    execution_plan = json.loads((ts_root / "tc-execution-plan.json").read_text(encoding="utf-8"))
    assert execution_plan["scenarios"][0]["chain_spec_path"] == ".artifacts/chain-to-product-detail-pc.spec.ts"
    assert execution_plan["scenarios"][0]["chain_spec_product_path"] == (
        "products/demo-product/agent3/ts-gen/.artifacts/chain-to-product-detail-pc.spec.ts"
    )


def test_tsgen_generates_huize_payment_closed_loop_for_wechat_boundary(tmp_path):
    from e2e_agent.core import script_generation as runtime

    state = {
        "product_id": "demo-product",
        "entry_url": "https://commerce.example.test/m/apps/cps/demo-channel/product/detail",
        "regression_flow": {
            "nodes": [
                {"node_id": "NODE-start", "type": "start"},
                {"node_id": "NODE-payment", "type": "payment", "page_name": "Payment"},
                {"node_id": "NODE-policy-result", "type": "result", "page_name": "Policy Result"},
                {"node_id": "NODE-end", "type": "end"},
            ]
        },
        "regression_paths": [
            {
                "path_id": "PATH-001",
                "nodes": ["NODE-start", "NODE-payment", "NODE-policy-result", "NODE-end"],
                "case_ids": ["TC-001"],
            }
        ],
        "page_registry": {
            "page_content_records": [
                {
                    "page_content_record_id": "PCR-PAY",
                    "actual_url": "https://commerce.example.test/m/apps/cps/demo-channel/pay?gatewayPayNum=P0612605070000000119",
                    "matched_node_ids": ["NODE-payment"],
                    "selector_map": {
                        "actions": [
                            {
                                "action_key": "action.click_payment",
                                "text": "立即支付",
                                "selector": "#submitToPay",
                                "tag": "a",
                                "source_url": "https://commerce.example.test/m/apps/cps/demo-channel/pay?gatewayPayNum=P0612605070000000119",
                            }
                        ]
                    },
                    "payment_boundary_evidence": {
                        "paymentMethod": "wechat",
                        "insureNum": "20260531013475",
                        "gatewayPayNum": "P0612605070000000119",
                        "gatewayPayNum_source": "wechat-url-trade_no",
                        "paymentUrlHost": "payments.example.test",
                        "paymentUrlPath": "/v2/wechat_pay",
                        "cashierOwner": "generic-insurance",
                    },
                }
            ],
            "path_exploration_results": [
                {
                    "path_id": "PATH-001",
                    "path_status": "explored",
                    "target_node": "NODE-policy-result",
                    "page_content_refs": ["PCR-PAY"],
                    "planned_page_refs": ["payment", "policy-result"],
                    "node_progress": [{"node_id": "NODE-payment", "status": "matched"}],
                    "completion_rule": {
                        "source": "agent3.static-contract",
                        "target_node": "NODE-policy-result",
                        "required_nodes": ["NODE-payment", "NODE-policy-result"],
                        "matched_nodes": ["NODE-payment"],
                        "missing_nodes": [],
                        "is_complete": True,
                    },
                }
            ],
        },
        "explore_trace": {
            "action_trace": [
                {
                    "path_id": "PATH-001",
                    "action_key": "action.click_payment",
                    "text": "立即支付",
                    "selector": "#submitToPay",
                    "tag": "a",
                    "source_url": "https://commerce.example.test/m/apps/cps/demo-channel/pay?gatewayPayNum=P0612605070000000119",
                    "target_url": "https://payments.example.test/v2/wechat_pay?trade_no=P0612605070000000119",
                    "planned_from_node_id": "NODE-payment",
                    "planned_to_node_id": "NODE-policy-result",
                    "click_strategy": "touchscreen-payment-btn",
                }
            ]
        },
    }

    result = runtime.build_ts_gen_bundle(state, root_dir=tmp_path, materialise=True)

    scenario = result["scenarios"][0]
    assert scenario["external_operations"] == [
        {
            "operation_id": "SCN-001-PATH-001-huize-pay-success",
            "operation_type": "huize-pay-success",
            "status": "pending",
            "payment_method": "wechat",
            "gateway_pay_num_source": "wechat-url-trade_no",
        },
        {
            "operation_id": "SCN-001-PATH-001-huize-issue-status",
            "operation_type": "huize-issue-status",
            "status": "pending",
            "payment_method": "wechat",
            "gateway_pay_num_source": "wechat-url-trade_no",
        },
    ]

    spec = (
        tmp_path
        / "products"
        / "demo-product"
        / "agent3"
        / "ts-gen"
        / "h5"
        / "scenarios"
        / "01-path-001.spec.ts"
    ).read_text(encoding="utf-8")
    assert "import { paySuccess as huizePaySuccess } from '../../external-ops/huize-pay-success.cjs';" in spec
    assert "import { waitForIssueStatus as huizeWaitForIssueStatus } from '../../external-ops/huize-issue-status.cjs';" in spec
    assert "async function runHuizePaymentClosedLoop" in spec
    assert "await runHuizePaymentClosedLoop(page, test.info(), {" in spec
    assert "async function waitForHuizePaymentBoundary(page: any" in spec
    assert "await waitForHuizePaymentBoundary(page, config, extraEvidence);" in spec
    assert "async function navigateToHuizePolicySuccessPage(page: any" in spec
    assert "await navigateToHuizePolicySuccessPage(page, evidence);" in spec
    assert "async function waitForVisibleBodyOrBlankPaymentRedirect(page: any)" in spec
    assert "await waitForVisibleBodyOrBlankPaymentRedirect(page);" in spec
    assert "payload.startDate = policyStartDate;" in spec
    assert "policy.insuranceDate = policyStartDate;" in spec
    assert spec.index("await runHuizePaymentClosedLoop(page, test.info(), {") < spec.index(
        "await waitForVisibleBodyOrBlankPaymentRedirect(page);"
    )
    assert "const embeddedMockData = {" in spec
    assert "const mockData = withAgent4RuntimeMockDataOverrides(embeddedMockData);" in spec
    assert "process.env.AGENT4_POLICY_START_DATE" in spec
    assert "process.env.AGENT4_POLICY_START_OFFSET_DAYS" in spec
    assert "function huizePayResultSummary(result: any): Record<string, unknown>" in spec
    assert "function huizeIssueResultSummary(result: any): Record<string, unknown>" in spec
    assert "payResult: huizePayResultSummary(payResult)" in spec
    assert "issueResult: huizeIssueResultSummary(issueResult)" in spec
    config_block = spec.split("const huizePaymentClosedLoopConfig = ", 1)[1].split(";", 1)[0]
    assert "P0612605070000000119" not in config_block
    assert "20260531013475" not in config_block
    assert (
        tmp_path
        / "products"
        / "demo-product"
        / "agent3"
        / "ts-gen"
        / "external-ops"
        / "huize-pay-success.cjs"
    ).exists()
    assert (
        tmp_path
        / "products"
        / "demo-product"
        / "agent3"
        / "ts-gen"
        / "external-ops"
        / "huize-issue-status.cjs"
    ).exists()
    issue_helper = (
        tmp_path
        / "products"
        / "demo-product"
        / "agent3"
        / "ts-gen"
        / "external-ops"
        / "huize-issue-status.cjs"
    ).read_text(encoding="utf-8")
    assert "const DEFAULT_TIMEOUT_MS = 600000;" in issue_helper
    assert "HUIZE_ISSUE_TIMEOUT_MS" in issue_helper

    execution_plan = json.loads(
        (
            tmp_path
            / "products"
            / "demo-product"
            / "agent3"
            / "ts-gen"
            / "tc-execution-plan.json"
        ).read_text(encoding="utf-8")
    )
    assert execution_plan["externalOperations"] == scenario["external_operations"]
    assert execution_plan["scenarios"][0]["external_operations"] == scenario["external_operations"]


def test_tsgen_records_agent4_execution_requirements_for_zurich_passport_payment_flow(tmp_path):
    from e2e_agent.core import script_generation as runtime

    state = {
        "product_id": "travel-product",
        "entry_url": "https://commerce.example.test/m/apps/cps/demo-channel/product/detail",
        "regression_paths": [
            {
                "path_id": "PATH-003",
                "nodes": [
                    "NODE-start",
                    "NODE-product-detail",
                    "NODE-insure-form",
                    "NODE-payment",
                    "NODE-policy-result",
                    "NODE-end",
                ],
                "case_ids": ["TC-travel-product-003"],
            }
        ],
        "page_registry": {
            "page_content_records": [
                {
                    "page_content_record_id": "PCR-DETAIL",
                    "actual_url": "https://commerce.example.test/m/apps/cps/demo-channel/product/detail",
                    "matched_node_ids": ["NODE-product-detail"],
                },
                {
                    "page_content_record_id": "PCR-PAY",
                    "actual_url": "https://commerce.example.test/m/apps/cps/demo-channel/pay?gatewayPayNum=P0612605070000000119",
                    "matched_node_ids": ["NODE-payment"],
                    "payment_boundary_evidence": {
                        "paymentMethod": "wechat",
                        "gatewayPayNum": "P0612605070000000119",
                        "gatewayPayNum_source": "wechat-url-trade_no",
                        "cashierOwner": "generic-insurance",
                    },
                },
            ],
            "path_exploration_results": [
                {
                    "path_id": "PATH-003",
                    "path_status": "explored",
                    "target_node": "NODE-policy-result",
                    "effective_nodes": [
                        "NODE-product-detail",
                        "NODE-insure-form",
                        "NODE-payment",
                        "NODE-policy-result",
                    ],
                    "page_content_refs": ["PCR-DETAIL", "PCR-PAY"],
                    "planned_page_refs": ["product-detail", "insure-form", "payment", "policy-result"],
                    "node_progress": [
                        {"node_id": "NODE-product-detail", "status": "matched"},
                        {"node_id": "NODE-insure-form", "status": "matched"},
                        {"node_id": "NODE-payment", "status": "matched"},
                    ],
                    "completion_rule": {
                        "source": "agent3.static-contract",
                        "target_node": "NODE-policy-result",
                        "required_nodes": [
                            "NODE-product-detail",
                            "NODE-insure-form",
                            "NODE-payment",
                            "NODE-policy-result",
                        ],
                        "matched_nodes": ["NODE-product-detail", "NODE-insure-form", "NODE-payment"],
                        "missing_nodes": [],
                        "is_complete": True,
                    },
                }
            ],
        },
        "explore_trace": {
            "action_trace": [
                {
                    "path_id": "PATH-003",
                    "text": "立即投保",
                    "selector": ".footer .insure",
                    "tag": "button",
                    "source_url": "https://commerce.example.test/m/apps/cps/demo-channel/product/detail",
                    "target_url": "https://commerce.example.test/m/apps/cps/demo-channel/product/insure",
                    "planned_from_node_id": "NODE-product-detail",
                    "planned_to_node_id": "NODE-insure-form",
                    "click_strategy": "mouse-h5-product-footer-insure",
                },
                {
                    "path_id": "PATH-003",
                    "text": "立即支付",
                    "selector": "#submitToPay",
                    "tag": "a",
                    "source_url": "https://commerce.example.test/m/apps/cps/demo-channel/pay?gatewayPayNum=P0612605070000000119",
                    "target_url": "https://payments.example.test/v2/wechat_pay?trade_no=P0612605070000000119",
                    "planned_from_node_id": "NODE-payment",
                    "planned_to_node_id": "NODE-policy-result",
                    "click_strategy": "touchscreen-payment-btn",
                },
            ]
        },
    }

    result = runtime.build_ts_gen_bundle(state, root_dir=tmp_path, materialise=True)
    scenario = result["scenarios"][0]

    requirements = scenario["execution_requirements"]
    assert requirements == {
        "mock_user_required": True,
        "mock_user_id_type": "护照",
        "policy_start_offset_days": 1,
        "product_plan": "全球完美计划",
        "requires_identity_auth_recovery": True,
        "requires_payment_closure": True,
        "expected_result_node": "NODE-policy-result",
    }
    assert scenario["mock_data"]["applicant.id_type"] == "护照"
    assert scenario["mock_data"]["applicant.id_no"] == "EA1342046"

    spec = (
        tmp_path
        / "products"
        / "travel-product"
        / "agent3"
        / "ts-gen"
        / "h5"
        / "scenarios"
        / "01-path-003.spec.ts"
    ).read_text(encoding="utf-8")
    metadata = json.loads(re.search(r"@scenario\s+({.*})", spec).group(1))
    assert metadata["execution_requirements"] == requirements
    assert 'const productDetailPlan = "全球完美计划";' in spec

    execution_plan = json.loads(
        (
            tmp_path
            / "products"
            / "travel-product"
            / "agent3"
            / "ts-gen"
            / "tc-execution-plan.json"
        ).read_text(encoding="utf-8")
    )
    assert execution_plan["scenarios"][0]["execution_requirements"] == requirements


def test_tsgen_product_detail_plan_can_be_driven_by_execution_requirements():
    from e2e_agent.core import script_generation as runtime

    scenario = {
        "scenario_id": "SCN-001",
        "path_id": "PATH-777",
        "entry_url": "https://example.com/product/detail",
        "route_nodes": ["NODE-product-detail", "NODE-insure-form"],
        "case_ids": ["TC-contract-plan"],
        "target_node": "NODE-insure-form",
        "execution_requirements": {"product_plan": "全球探索计划"},
        "real_actions": [
            {
                "path_id": "PATH-777",
                "source_url": "https://example.com/product/detail",
                "target_url": "https://example.com/product/insure",
                "planned_from_node_id": "NODE-product-detail",
                "planned_to_node_id": "NODE-insure-form",
                "click_strategy": "mouse-h5-product-footer-insure",
                "text": "立即投保",
                "tag": "button",
            }
        ],
        "variants": [{"conditions": {}}],
    }

    spec = runtime.render_scenario_spec(scenario, [], generated_by="test")

    assert 'const productDetailPlan = "全球探索计划";' in spec


def test_tsgen_enables_huize_closed_loop_from_runtime_wechat_redirect(tmp_path):
    from e2e_agent.core import script_generation as runtime

    wechat_url = (
        "https://wx.tenpay.com/cgi-bin/mmpayweb-bin/checkmweb"
        "?prepay_id=wx0419423030112540901853abdd8bde0001"
        "&redirect_url=https%3A%2F%2Fpayments.example.test%2Fv2%2Freturn%2Fwechat%3Ftrade_no%3DP0612606040000000118"
    )
    assert runtime._extract_gateway_pay_num_hint(wechat_url) == "P0612606040000000118"
    assert runtime._is_huize_payment_boundary(
        {
            "paymentUrl": "https://wx.tenpay.com/cgi-bin/mmpayweb-bin/checkmweb?prepay_id=wx0419423030112540901853",
            "sourceUrl": "https://commerce.example.test/m/demo-channel/pay/?id=pHLW2eXm7CBzpsVzXKMrcA",
            "gatewayPayNum_source": "payment-action-trade_no",
        }
    )

    state = {
        "product_id": "demo-product",
        "entry_url": "https://commerce.example.test/m/apps/cps/demo-channel/product/detail",
        "regression_flow": {
            "nodes": [
                {"node_id": "NODE-start", "type": "start"},
                {"node_id": "NODE-payment", "type": "payment"},
                {"node_id": "NODE-policy-result", "type": "result"},
                {"node_id": "NODE-end", "type": "end"},
            ]
        },
        "regression_paths": [
            {
                "path_id": "PATH-001",
                "nodes": ["NODE-start", "NODE-payment", "NODE-policy-result", "NODE-end"],
                "case_ids": ["TC-001"],
            }
        ],
        "page_registry": {
            "page_content_records": [
                {
                    "page_content_record_id": "PCR-FORM",
                    "actual_url": "https://commerce.example.test/m/apps/cps/demo-channel/product/insure",
                    "matched_node_ids": ["NODE-insure-form"],
                },
            ],
            "path_exploration_results": [
                {
                    "path_id": "PATH-001",
                    "path_status": "explored",
                    "target_node": "NODE-policy-result",
                    "page_content_refs": ["PCR-FORM"],
                    "planned_page_refs": ["payment", "policy-result"],
                    "node_progress": [{"node_id": "NODE-payment", "status": "matched"}],
                    "completion_rule": {
                        "source": "agent3.static-contract",
                        "target_node": "NODE-policy-result",
                        "required_nodes": ["NODE-payment", "NODE-policy-result"],
                        "matched_nodes": ["NODE-payment"],
                        "missing_nodes": [],
                        "is_complete": True,
                    },
                }
            ],
        },
        "explore_trace": {
            "action_trace": [
                {
                    "path_id": "PATH-001",
                    "action_key": "action.click_payment",
                    "text": "立即支付",
                    "selector": "#submitToPay",
                    "tag": "a",
                    "source_url": "https://commerce.example.test/m/demo-channel/pay/?id=pHLW2eXm7CBzpsVzXKMrcA",
                    "target_url": wechat_url,
                    "planned_from_node_id": "NODE-payment",
                    "planned_to_node_id": "NODE-policy-result",
                    "click_strategy": "touchscreen-payment-btn",
                }
            ]
        },
    }

    result = runtime.build_ts_gen_bundle(state, root_dir=tmp_path, materialise=True)

    scenario = result["scenarios"][0]
    assert scenario["external_operations"] == [
        {
            "operation_id": "SCN-001-PATH-001-huize-pay-success",
            "operation_type": "huize-pay-success",
            "status": "pending",
            "payment_method": "wechat",
            "gateway_pay_num_source": "runtime-payment-boundary",
        },
        {
            "operation_id": "SCN-001-PATH-001-huize-issue-status",
            "operation_type": "huize-issue-status",
            "status": "pending",
            "payment_method": "wechat",
            "gateway_pay_num_source": "runtime-payment-boundary",
        },
    ]
    spec = (
        tmp_path
        / "products"
        / "demo-product"
        / "agent3"
        / "ts-gen"
        / "h5"
        / "scenarios"
        / "01-path-001.spec.ts"
    ).read_text(encoding="utf-8")
    assert "huizeExpandedPaymentText" in spec
    assert "await runHuizePaymentClosedLoop(page, test.info(), {" in spec
    assert "async function waitForHuizePaymentBoundary(page: any" in spec
    assert "await waitForHuizePaymentBoundary(page, config, extraEvidence);" in spec
    assert "await navigateToHuizePolicySuccessPage(page, evidence);" in spec


def test_tsgen_chain_spec_normalises_windows_page_function_import_paths():
    from e2e_agent.core import script_generation as runtime

    rendered = runtime.render_chain_spec(
        {
            "scenario_id": "SCN-001",
            "path_id": "PATH-001",
            "entry_url": "https://example.com/apps/cps/product/detail",
            "route_nodes": ["NODE-start", "NODE-product-detail", "NODE-end"],
            "case_ids": ["TC-001"],
            "target_node": "NODE-product-detail",
            "coverage_status": "covered",
            "contract_status": "compiled",
        },
        [
            {
                "page_id": "product-detail",
                "function_name": "fillProductDetail",
                "file_path": r"pc\page-functions\01-product-detail.ts",
            }
        ],
        product_id="demo-product",
        generated_by="test",
    )

    assert "import { fillProductDetail } from '../pc/page-functions/01-product-detail';" in rendered
    assert r"pc\page-functions" not in rendered


def test_tsgen_scenario_carries_agent3_readiness_artifacts():
    from e2e_agent.core import script_generation as runtime

    state = _single_path_state()
    path = state["page_registry"]["path_exploration_results"][0]
    path["field_resolution_plan"] = {
        "fields": [
            {
                "node_id": "NODE-product-detail",
                "field_key": "form.keyword",
                "locator_status": "verified_static",
                "selected_locator": {"by": "selector", "value": "input[name='keyWord']"},
                "mock_status": "mapped",
            }
        ],
        "summary": {"required_field_count": 1, "verified_required_field_count": 1},
    }
    path["component_strategy"] = {
        "field_strategies": [
            {
                "node_id": "NODE-product-detail",
                "field_key": "form.keyword",
                "control_type": "input_text",
                "fill_strategy": "fill_text",
                "strategy_status": "supported",
            }
        ],
        "summary": {"unsupported_required_component_count": 0},
    }
    path["validation_report"] = {
        "status": "passed",
        "agent4_ready": True,
        "gates": [
            {"gate": "page_recognition", "status": "passed"},
            {"gate": "required_field_location", "status": "passed"},
            {"gate": "action_clickability", "status": "passed"},
            {"gate": "component_strategy_coverage", "status": "passed"},
            {"gate": "mock_data_mapping", "status": "passed"},
            {"gate": "transition_reachability", "status": "passed"},
        ],
    }

    scenarios = runtime.build_scenarios(state)
    scenario = scenarios[0]

    assert scenario["field_resolution_plan"]["summary"]["verified_required_field_count"] == 1
    assert scenario["component_strategy"]["field_strategies"][0]["fill_strategy"] == "fill_text"
    assert scenario["validation_report"]["agent4_ready"] is True
    assert scenario["page_element_plan"][0]["fields"][0]["field_resolution"]["locator_status"] == "verified_static"
    assert scenario["page_element_plan"][0]["fields"][0]["component_strategy"]["control_type"] == "input_text"


def test_tsgen_scenario_builds_fact_lineage_from_existing_contracts():
    from e2e_agent.core import script_generation as runtime

    state = _single_path_state()
    state["regression_paths"][0]["conditions"] = {"payment": "required"}
    state["regression_paths"][0]["assertions"] = [{"expect": "policy result reached"}]
    state["explore_trace"] = {
        "action_trace": [
            {
                "path_id": "PATH-001",
                "action_key": "action.buy_now",
                "selector": "#submit-by",
                "planned_from_node_id": "NODE-product-detail",
                "planned_to_node_id": "NODE-policy-result",
            }
        ]
    }

    scenario = runtime.build_scenarios(state)[0]

    assert scenario["fact_lineage"] == {
        "version": "fact-lineage-v1",
        "scenario_id": "SCN-001",
        "path_id": "PATH-001",
        "case_ids": ["TC-001"],
        "source_case_ids": ["TC-001"],
        "condition_keys": ["payment"],
        "test_data_profile_ids": ["TDP-path-001-payment-required"],
        "page_content_refs": ["PCR-001"],
        "planned_page_refs": ["product-detail"],
        "action_evidence": {
            "source": "agent3.trace",
            "action_count": 1,
            "action_keys": ["action.buy_now"],
            "planned_to_node_ids": ["NODE-policy-result"],
        },
        "assertion_refs": ["ASSERT-001-001"],
        "terminal_boundary": {},
        "coverage_status": "covered",
        "contract_status": "compiled",
    }


def test_tsgen_blocks_agent4_when_agent3_validation_report_is_not_ready():
    from e2e_agent.core import script_generation as runtime

    state = _single_path_state()
    path = state["page_registry"]["path_exploration_results"][0]
    path["completion_rule"]["is_complete"] = True
    path["path_status"] = "explored"
    path["validation_report"] = {
        "status": "failed",
        "agent4_ready": False,
        "gates": [
            {"gate": "required_field_location", "status": "failed", "failed_count": 1}
        ],
    }

    scenario = runtime.build_scenarios(state)[0]

    assert scenario["contract_status"] == "blocked_by_agent3_validation"
    assert scenario["coverage_status"] == "coverage-gap"
    assert scenario["script_status"] == "blocked"
    assert scenario["blocked_reason"] == "Agent3 validation report is not ready for Agent4"


def test_tsgen_prefers_agent3_repaired_path_over_agent2_path():
    from e2e_agent.core import script_generation as runtime

    state = _single_path_state()
    state["regression_paths"][0]["nodes"] = [
        "NODE-start",
        "NODE-product-detail",
        "NODE-applicant-info",
        "NODE-insured-info",
        "NODE-policy-result",
        "NODE-end",
    ]
    state["regression_paths"][0]["page_keys"] = [
        {"node_id": "NODE-product-detail", "page_key": "product-detail", "url_pattern": "/product/detail"},
        {"node_id": "NODE-policy-result", "page_key": "policy-result", "url_pattern": "/policy/result"},
    ]
    path = state["page_registry"]["path_exploration_results"][0]
    path["effective_nodes"] = ["NODE-product-detail", "NODE-insure-form", "NODE-policy-result"]
    path["repaired_nodes"] = ["NODE-product-detail", "NODE-insure-form", "NODE-policy-result"]
    path["repaired_page_keys"] = [
        {"node_id": "NODE-product-detail", "page_key": "product-detail", "url_pattern": "/product/detail"},
        {"node_id": "NODE-insure-form", "page_key": "insure-form", "url_pattern": "/product/insure"},
        {"node_id": "NODE-policy-result", "page_key": "policy-result", "url_pattern": "/policy/result"},
    ]
    path["path_repaired"] = True

    scenario = runtime.build_scenarios(state)[0]

    assert scenario["route_nodes"] == ["NODE-product-detail", "NODE-insure-form", "NODE-policy-result"]
    assert scenario["agent2_route_nodes"] == state["regression_paths"][0]["nodes"]
    assert [item["node_id"] for item in scenario["page_keys"]] == [
        "NODE-product-detail",
        "NODE-insure-form",
        "NODE-policy-result",
    ]
    assert scenario["path_repaired"] is True


def test_tsgen_scenario_carries_targeted_probe_plan_when_agent3_blocks_execution():
    from e2e_agent.core import script_generation as runtime

    state = _single_path_state()
    path = state["page_registry"]["path_exploration_results"][0]
    path["path_status"] = "blocked"
    path["blocked_reason"] = "Agent3 validation failed"
    path["targeted_probe_plan"] = {
        "status": "required",
        "requests": [
            {
                "probe_id": "TP-PATH-001-001",
                "path_id": "PATH-001",
                "kind": "field",
                "node_id": "NODE-product-detail",
                "field_key": "form.keyword",
                "reason": "required_field_needs_verified_locator",
            }
        ],
        "summary": {"request_count": 1, "field_request_count": 1, "action_request_count": 0},
    }

    scenario = runtime.build_scenarios(state)[0]
    script_plan = runtime.build_script_plan(state, [scenario])

    assert scenario["targeted_probe_plan"]["summary"]["request_count"] == 1
    assert scenario["targeted_probe_plan"]["requests"][0]["probe_id"] == "TP-PATH-001-001"
    assert script_plan["scenario_plans"][0]["targeted_probe_request_count"] == 1


def test_tsgen_real_actions_prefer_executable_selector_actions():
    from e2e_agent.core import script_generation as runtime

    state = {
        "page_registry": {
            "primary_actions": [
                {
                    "action_key": "action.buy_now",
                    "text": "确认进入投保流程",
                    "selector": None,
                    "tag": "button",
                    "source_url": "https://example.com/detail",
                    "required": True,
                    "locators": [{"by": "role:heading", "value": "确认进入投保流程"}],
                },
                {
                    "action_key": "action.buy_now",
                    "text": "立即投保",
                    "selector": "#submit-by",
                    "tag": "button",
                    "source_url": "https://example.com/detail",
                    "required": True,
                    "locators": [{"by": "selector", "value": "#submit-by"}],
                },
                {
                    "action_key": "action.fillproductdetailh5",
                    "text": "fillProductDetailH5",
                    "selector": None,
                    "tag": "button",
                    "source_url": "https://example.com/detail",
                    "required": True,
                    "locators": [{"by": "function", "value": "fillProductDetailH5"}],
                },
            ]
        }
    }

    actions = runtime._scenario_real_actions(state, {"path_id": "PATH-001"})

    assert [action["selector"] for action in actions] == ["#submit-by"]
    assert actions[0]["text"] == "立即投保"


def test_tsgen_real_actions_prefer_agent3_explore_trace_over_registry_fallback():
    from e2e_agent.core import script_generation as runtime

    state = {
        "explore_trace": {
            "action_trace": [
                {
                    "path_id": "PATH-001",
                    "action_key": "action.buy_now",
                    "text": "立即投保",
                    "selector": "#agent3-buy",
                    "tag": "a",
                    "source_url": "https://example.com/detail",
                    "planned_from_node_id": "NODE-product-detail",
                    "planned_to_node_id": "NODE-insure-form",
                },
                {
                    "path_id": "PATH-001",
                    "action_key": "action.diagnostic",
                    "text": "agreement_scan_miss: 用户协议",
                    "tag": "diagnostic",
                    "click_strategy": "agreement-scan-miss",
                },
                {
                    "path_id": "PATH-001",
                    "action_key": "action.submit",
                    "text": "提交订单",
                    "selector": "a.submit-btn",
                    "tag": "a",
                    "source_url": "https://example.com/insure",
                    "planned_from_node_id": "NODE-insure-form",
                    "planned_to_node_id": "NODE-pay-success",
                },
            ]
        },
        "page_registry": {
            "path_exploration_results": [
                {
                    "path_id": "PATH-001",
                    "action_chain": [
                        {
                            "action_key": "action.buy_now",
                            "text": "错误兜底",
                            "selector": "#registry-fallback",
                            "tag": "button",
                        }
                    ],
                }
            ]
        },
    }

    actions = runtime._scenario_real_actions(state, {"path_id": "PATH-001"})

    assert [action["action_key"] for action in actions] == ["action.buy_now", "action.submit"]
    assert [action["selector"] for action in actions] == ["#agent3-buy", "a.submit-btn"]
    assert actions[0]["source"] == "agent3.explore_trace.action_trace"


def test_tsgen_real_actions_expand_agent3_action_trace_artifact_shape():
    from e2e_agent.core import script_generation as runtime

    state = {
        "explore_trace": {
            "action_trace": [
                {
                    "path_id": "PATH-001",
                    "path_status": "explored",
                    "action_count": 2,
                    "action_chain": [
                        {
                            "action_key": "action.buy_now",
                            "text": "立即投保",
                            "selector": "#agent3-buy",
                            "source_url": "https://example.com/detail",
                        },
                        {
                            "action_key": "action.submit",
                            "text": "提交订单",
                            "selector": ".submit",
                            "source_url": "https://example.com/insure",
                        },
                    ],
                }
            ]
        },
        "page_registry": {
            "path_exploration_results": [
                {
                    "path_id": "PATH-001",
                    "action_chain": [{"action_key": "action.buy_now", "text": "fallback", "selector": "#fallback"}],
                }
            ]
        },
    }

    actions = runtime._scenario_real_actions(state, {"path_id": "PATH-001"})

    assert [action["action_key"] for action in actions] == ["action.buy_now", "action.submit"]
    assert [action["selector"] for action in actions] == ["#agent3-buy", ".submit"]


def test_tsgen_real_actions_follow_path_action_chain_order():
    from e2e_agent.core import script_generation as runtime

    state = {
        "page_registry": {
            "primary_actions": [
                {
                    "action_key": "action.buy_now",
                    "text": "全局兜底动作",
                    "selector": "#wrong",
                    "tag": "button",
                    "source_url": "https://example.com/detail",
                }
            ],
            "path_exploration_results": [
                {
                    "path_id": "PATH-001",
                    "action_chain": [
                        {
                            "script_step_id": "STEP-001",
                            "action_key": "action.buy_now",
                            "text": "立即投保",
                            "selector": "#submit-by",
                            "tag": "a",
                            "source_url": "https://example.com/detail",
                            "planned_from_node_id": "NODE-product-detail",
                            "planned_to_node_id": "NODE-insure-form",
                        },
                        {
                            "script_step_id": "STEP-002",
                            "action_key": "action.agree_all",
                            "text": "已阅读并同意",
                            "selector": None,
                            "tag": "button",
                            "source_url": "https://example.com/detail",
                            "planned_from_node_id": "NODE-product-detail",
                            "planned_to_node_id": "NODE-insure-form",
                        },
                        {
                            "script_step_id": "STEP-003",
                            "action_key": "action.submit",
                            "text": "提交订单",
                            "selector": "button.submit",
                            "tag": "button",
                            "source_url": "https://example.com/insure",
                            "planned_from_node_id": "NODE-insure-form",
                            "planned_to_node_id": "NODE-policy-result",
                        },
                    ],
                }
            ],
        }
    }

    actions = runtime._scenario_real_actions(state, {"path_id": "PATH-001"})

    assert [action["action_key"] for action in actions] == [
        "action.buy_now",
        "action.agree_all",
        "action.submit",
    ]
    assert [action["planned_from_node_id"] for action in actions] == [
        "NODE-product-detail",
        "NODE-product-detail",
        "NODE-insure-form",
    ]
    assert [action["selector"] for action in actions] == ["#submit-by", None, "button.submit"]


def test_tsgen_script_plan_records_full_real_action_chain():
    from e2e_agent.core import script_generation as runtime

    scenario = {
        "scenario_id": "SCN-PATH-001",
        "path_id": "PATH-001",
        "route_nodes": ["NODE-start", "NODE-product-detail", "NODE-insure-form", "NODE-end"],
        "planned_page_refs": [],
        "page_content_refs": [],
        "target_node": "NODE-insure-form",
        "node_progress": [],
        "completion_rule": {"is_complete": True},
        "mock_data": {},
        "real_actions": [
            {
                "action_key": "action.buy_now",
                "text": "立即投保",
                "selector": "#agent3-buy",
                "source": "agent3.explore_trace.action_trace",
            }
        ],
    }

    plan = runtime.build_script_plan({"page_registry": {}}, [scenario])

    assert plan["scenario_plans"][0]["real_action_count"] == 1
    assert plan["scenario_plans"][0]["real_actions"][0]["selector"] == "#agent3-buy"
    assert plan["scenario_plans"][0]["real_actions"][0]["source"] == "agent3.explore_trace.action_trace"


def test_tsgen_rendered_spec_replays_full_agent3_action_chain_in_order():
    from e2e_agent.core import script_generation as runtime

    rendered = runtime.render_scenario_spec(
        {
            "scenario_id": "SCN-001",
            "path_id": "PATH-001",
            "entry_url": "https://example.com/detail",
            "route_nodes": ["NODE-start", "NODE-product-detail", "NODE-insure-form", "NODE-policy-result", "NODE-end"],
            "case_ids": ["TC-001"],
            "planned_page_refs": [],
            "page_content_refs": [],
            "target_node": "NODE-policy-result",
            "coverage_status": "covered",
            "completion_rule": {"source": "agent3.live", "is_complete": True},
            "node_progress": [],
            "mock_data": {},
            "page_element_plan": [],
            "real_actions": [
                {
                    "action_key": "action.buy_now",
                    "text": "立即投保",
                    "selector": "#buy",
                    "source_url": "https://example.com/detail",
                    "planned_from_node_id": "NODE-product-detail",
                    "planned_to_node_id": "NODE-insure-form",
                },
                {
                    "action_key": "action.agree_all",
                    "text": "已阅读并同意",
                    "selector": ".agree",
                    "source_url": "https://example.com/insure",
                    "planned_from_node_id": "NODE-insure-form",
                    "planned_to_node_id": "NODE-insure-form",
                },
                {
                    "action_key": "action.submit",
                    "text": "提交订单",
                    "selector": ".submit",
                    "source_url": "https://example.com/insure",
                    "planned_from_node_id": "NODE-insure-form",
                    "planned_to_node_id": "NODE-policy-result",
                },
            ],
        },
        [],
        generated_by="test",
    )

    assert 'const action1 = await replayActionLocator(page, "#buy", "a", "立即投保");' in rendered
    assert 'const action2 = await replayActionLocator(page, ".agree", "a", "已阅读并同意");' in rendered
    assert 'const action3 = await replayActionLocator(page, ".submit", "a", "提交订单");' in rendered
    assert "test.setTimeout(720_000);" in rendered
    assert rendered.index("const action1") < rendered.index("const action2") < rendered.index("const action3")
    assert "step 3:" in rendered


def test_tsgen_rendered_spec_stops_after_order_generation_boundary():
    from e2e_agent.core import script_generation as runtime

    rendered = runtime.render_scenario_spec(
        {
            "scenario_id": "SCN-004",
            "path_id": "PATH-004",
            "entry_url": "https://example.com/product/detail",
            "route_nodes": [
                "NODE-product-detail",
                "NODE-insure-form",
                "NODE-payment",
                "NODE-policy-result",
            ],
            "case_ids": ["TC-007"],
            "planned_page_refs": [],
            "page_content_refs": [],
            "target_node": "NODE-policy-service",
            "coverage_status": "covered",
            "completion_rule": {
                "source": "agent3.live",
                "is_complete": True,
                "order_generation_boundary": True,
            },
            "node_progress": [],
            "mock_data": {},
            "page_element_plan": [],
            "real_actions": [
                {
                    "action_key": "action.pay",
                    "text": "立即支付",
                    "selector": "#submitToPay",
                    "tag": "a",
                    "source_url": "https://example.com/pay/?id=ORDER-1",
                    "target_url": "https://example.com/pay/?id=ORDER-1",
                    "planned_from_node_id": "NODE-payment",
                    "planned_to_node_id": "NODE-payment",
                },
                {
                    "action_key": "action.fill_sms_code",
                    "text": "验证码=1111",
                    "selector": "input.sms-code-input",
                    "tag": "field",
                    "source_url": "https://example.com/pay/success?id=ORDER-1",
                    "target_url": "https://example.com/pay/success?id=ORDER-1",
                    "planned_from_node_id": "NODE-payment",
                    "planned_to_node_id": "NODE-policy-result",
                },
                {
                    "action_key": "action.pay_again",
                    "text": "立即支付",
                    "selector": "a:nth-of-type(5)",
                    "tag": "a",
                    "source_url": "https://example.com/pay/success?id=ORDER-1",
                    "target_url": "https://example.com/pay/success?id=ORDER-1",
                    "planned_from_node_id": "NODE-payment",
                    "planned_to_node_id": "NODE-policy-result",
                },
                {
                    "action_key": "action.refresh_payment_result",
                    "text": "刷新支付结果",
                    "selector": "a:nth-of-type(5)",
                    "tag": "a",
                    "source_url": "https://example.com/pay/success?id=ORDER-1",
                    "target_url": "https://example.com/pay/success?id=ORDER-1",
                    "planned_from_node_id": "NODE-payment",
                    "planned_to_node_id": "NODE-policy-result",
                },
            ],
        },
        [],
        generated_by="test",
    )

    assert 'const action1 = await replayActionLocator(page, "#submitToPay", "a", "立即支付");' in rendered
    assert 'waitForObservedUrlTransition(page, beforeUrl1, "https://example.com/pay/success?id=ORDER-1", ' in rendered
    assert "input.sms-code-input" not in rendered
    assert 'const action2 = await replayActionLocator(page, "a:nth-of-type(5)", "a", "立即支付");' not in rendered
    assert "刷新支付结果" not in rendered


def test_tsgen_synthesizes_payment_click_from_payment_page_elements():
    from e2e_agent.core import script_generation as runtime

    scenario = {
        "scenario_id": "SCN-001",
        "path_id": "PATH-001",
        "entry_url": "https://example.com/product/detail",
        "route_nodes": [
            "NODE-product-detail",
            "NODE-insure-form",
            "NODE-underwriting",
            "NODE-payment",
            "NODE-policy-result",
        ],
        "case_ids": ["TC-001"],
        "planned_page_refs": [],
        "page_content_refs": [],
        "target_node": "NODE-policy-result",
        "coverage_status": "covered",
        "completion_rule": {
            "source": "agent3.live",
            "is_complete": True,
            "order_generation_boundary": True,
        },
        "node_progress": [],
        "mock_data": {},
        "page_element_plan": [
            {
                "node_id": "NODE-payment",
                "actual_url": "https://example.com/pay/?id=ORDER-1",
                "matched_node_ids": ["NODE-payment", "NODE-policy-result"],
                "actions": [
                    {
                        "text": "立即支付",
                        "selector": "#submitToPay",
                        "tag": "a",
                        "href": "javascript:;",
                    }
                ],
            }
        ],
        "real_actions": [
            {
                "action_key": "action.submit",
                "text": "提交订单",
                "selector": ".submit",
                "tag": "div",
                "source_url": "https://example.com/product/insure",
                "target_url": "https://example.com/pay/?id=ORDER-1",
                "click_strategy": "touchscreen-submit-btn",
                "planned_from_node_id": "NODE-insure-form",
                "planned_to_node_id": "NODE-underwriting",
            }
        ],
    }

    rendered = runtime.render_scenario_spec(scenario, [], generated_by="test")

    assert "replayH5PaymentButton" in rendered
    assert 'replayH5PaymentButton(page, "#submitToPay", "a", "立即支付")' in rendered
    assert "step-2-before-payment" in rendered
    assert 'planned_from_node_id: "NODE-payment", planned_to_node_id: "NODE-payment"' in rendered
    assert 'planned_from_node_id: "NODE-payment", planned_to_node_id: "NODE-policy-result"' in rendered
    assert rendered.index("replayH5SubmitButton") < rendered.index("replayH5PaymentButton")
    assert "waitForVisibleBodyOrBlankPaymentRedirect" not in rendered


def test_tsgen_synthesizes_payment_click_when_repaired_route_skips_payment_node():
    from e2e_agent.core import script_generation as runtime

    rendered = runtime.render_scenario_spec(
        {
            "scenario_id": "SCN-002",
            "path_id": "PATH-002",
            "entry_url": "https://example.com/product/detail",
            "route_nodes": [
                "NODE-product-detail",
                "NODE-insure-form",
                "NODE-policy-result",
            ],
            "case_ids": ["TC-002"],
            "target_node": "NODE-policy-result",
            "coverage_status": "covered",
            "completion_rule": {"source": "agent3.live", "is_complete": True, "order_generation_boundary": True},
            "node_progress": [],
            "mock_data": {},
            "page_element_plan": [
                {
                    "node_id": "NODE-payment",
                    "actual_url": "https://example.com/pay/?id=ORDER-2",
                    "matched_node_ids": ["NODE-payment", "NODE-policy-result"],
                    "actions": [{"text": "立即支付", "selector": "#submitToPay", "tag": "a"}],
                }
            ],
            "real_actions": [
                {
                    "text": "提交订单",
                    "selector": ".submit",
                    "tag": "div",
                    "source_url": "https://example.com/product/insure",
                    "target_url": "https://example.com/pay/?id=ORDER-2",
                    "click_strategy": "touchscreen-submit-btn",
                    "planned_from_node_id": "NODE-insure-form",
                    "planned_to_node_id": "NODE-policy-result",
                }
            ],
        },
        [],
        generated_by="test",
    )

    assert 'replayH5PaymentButton(page, "#submitToPay", "a", "立即支付")' in rendered
    assert 'planned_from_node_id: "NODE-payment", planned_to_node_id: "NODE-policy-result"' in rendered


def test_tsgen_enables_huize_closed_loop_for_synthetic_payment_action(tmp_path):
    from e2e_agent.core import script_generation as runtime

    state = {
        "product_id": "demo-product",
        "entry_url": "https://commerce.example.test/m/apps/cps/demo-channel/product/detail",
        "regression_flow": {
            "nodes": [
                {"node_id": "NODE-product-detail", "type": "page"},
                {"node_id": "NODE-insure-form", "type": "form"},
                {"node_id": "NODE-payment", "type": "payment"},
                {"node_id": "NODE-policy-result", "type": "result"},
            ]
        },
        "regression_paths": [
            {
                "path_id": "PATH-002",
                "nodes": ["NODE-product-detail", "NODE-insure-form", "NODE-policy-result"],
                "case_ids": ["TC-002"],
            }
        ],
        "page_registry": {
            "page_content_records": [
                {
                    "page_content_record_id": "PCR-FORM",
                    "actual_url": "https://commerce.example.test/m/apps/cps/demo-channel/product/insure",
                    "matched_node_ids": ["NODE-insure-form"],
                    "network_evidence": {"response": "{\"data\":{\"insureNum\":\"20260604001234\"}}"},
                },
                {
                    "page_content_record_id": "PCR-PAY",
                    "actual_url": "https://commerce.example.test/m/demo-channel/pay/?id=PAY-ID",
                    "matched_node_ids": ["NODE-payment", "NODE-policy-result"],
                    "selector_map": {
                        "actions": [
                            {
                                "text": "立即支付",
                                "selector": "#submitToPay",
                                "tag": "a",
                                "source_url": "https://commerce.example.test/m/demo-channel/pay/?id=PAY-ID",
                            }
                        ]
                    },
                },
            ],
            "path_exploration_results": [
                {
                    "path_id": "PATH-002",
                    "path_status": "explored",
                    "target_node": "NODE-policy-result",
                    "business_intent": "surrender",
                    "effective_nodes": ["NODE-product-detail", "NODE-insure-form", "NODE-policy-result"],
                    "page_content_refs": ["PCR-FORM", "PCR-PAY"],
                    "planned_page_refs": ["product", "insure", "result"],
                    "completion_rule": {"source": "agent3.live", "is_complete": True},
                }
            ],
        },
        "explore_trace": {
            "action_trace": [
                {
                    "path_id": "PATH-002",
                    "text": "提交订单",
                    "selector": ".submit",
                    "tag": "div",
                    "source_url": "https://commerce.example.test/m/apps/cps/demo-channel/product/insure",
                    "target_url": "https://commerce.example.test/m/demo-channel/pay/?id=PAY-ID",
                    "click_strategy": "touchscreen-submit-btn",
                    "planned_from_node_id": "NODE-insure-form",
                    "planned_to_node_id": "NODE-policy-result",
                }
            ]
        },
    }

    result = runtime.build_ts_gen_bundle(state, root_dir=tmp_path, materialise=True)
    scenario = result["scenarios"][0]

    assert scenario["huize_payment_closed_loop"]["enabled"] is True
    assert len(scenario["external_operations"]) == 2
    spec = (
        tmp_path
        / "products"
        / "demo-product"
        / "agent3"
        / "ts-gen"
        / "h5"
        / "scenarios"
        / "01-path-002.spec.ts"
    ).read_text(encoding="utf-8")
    assert "await runHuizePaymentClosedLoop(page, test.info(), {" in spec


def test_tsgen_rendered_specs_dedupe_imports_for_revisited_nodes():
    from e2e_agent.core import script_generation as runtime

    scenario = {
        "scenario_id": "SCN-004",
        "path_id": "PATH-004",
        "entry_url": "https://example.com/detail",
        "route_nodes": ["NODE-policy-result", "NODE-policy-service", "NODE-policy-result"],
        "case_ids": ["TC-007"],
        "planned_page_refs": [],
        "page_content_refs": [],
        "target_node": "NODE-policy-result",
        "coverage_status": "covered",
        "contract_status": "compiled",
        "completion_rule": {"source": "agent3.live", "is_complete": True},
        "node_progress": [],
        "mock_data": {},
        "page_element_plan": [],
    }
    page_functions = [
        {
            "page_id": "policy-result",
            "function_name": "fillPolicyResult",
            "file_path": "page-functions/04-policy-result.ts",
        },
        {
            "page_id": "policy-service",
            "function_name": "fillPolicyService",
            "file_path": "page-functions/05-policy-service.ts",
        },
    ]

    spec = runtime.render_scenario_spec(scenario, page_functions, generated_by="test")
    chain = runtime.render_chain_spec(
        scenario,
        page_functions,
        product_id="demo-product",
        generated_by="test",
    )

    assert spec.count("import { fillPolicyResult }") == 1
    assert spec.count("await fillPolicyResult") == 2
    assert chain.count("import { fillPolicyResult }") == 1
    assert chain.count("await fillPolicyResult") == 2


def test_tsgen_rendered_spec_uploads_id_card_sides_to_matching_file_inputs():
    from e2e_agent.core import script_generation as runtime

    rendered = runtime.render_scenario_spec(
        {
            "scenario_id": "SCN-001",
            "path_id": "PATH-001",
            "entry_url": "https://example.com/auth",
            "route_nodes": ["NODE-risk-control", "NODE-end"],
            "case_ids": ["TC-001"],
            "planned_page_refs": [],
            "page_content_refs": [],
            "target_node": "NODE-end",
            "coverage_status": "covered",
            "completion_rule": {"source": "agent3.live", "is_complete": True},
            "node_progress": [],
            "mock_data": {},
            "page_element_plan": [],
            "real_actions": [
                {
                    "action_key": "action.upload_id_card",
                    "text": "人像面 上传照片",
                    "selector": "input[type='file']",
                    "source_url": "https://example.com/auth",
                    "planned_from_node_id": "NODE-risk-control",
                    "planned_to_node_id": "NODE-risk-control",
                },
                {
                    "action_key": "action.upload_id_card",
                    "text": "国徽面 上传照片",
                    "selector": "input[type='file']",
                    "source_url": "https://example.com/auth",
                    "planned_from_node_id": "NODE-risk-control",
                    "planned_to_node_id": "NODE-risk-control",
                },
            ],
        },
        [],
        generated_by="test",
    )

    assert "uploadIdCardFixtureToInput" in rendered
    assert 'uploadIdCardFixtureToInput(page, "input[type=\'file\']", "人像面 上传照片")' in rendered
    assert 'uploadIdCardFixtureToInput(page, "input[type=\'file\']", "国徽面 上传照片")' in rendered
    assert "const fixture = resolveIdCardFixture(text);" in rendered
    assert "国徽|反面|背面" in rendered


def test_tsgen_id_card_upload_helper_falls_back_across_file_input_selectors():
    from e2e_agent.core.script_generation import real_action_helper_lines

    content = "\n".join(real_action_helper_lines())

    assert "async function advanceToIdCardUploadIfNeeded" in content
    assert "证件照片|上传证件照|上传照片|下一步|继续|提交认证" in content
    assert "async function uploadIdCardFixtureToInput" in content
    assert "input[type=file]" in content
    assert "input[accept*=\"image\"]" in content
    assert "await advanceToIdCardUploadIfNeeded(page);" in content
    assert "setInputFiles(fixture, { timeout: 5000 })" in content
    assert "String(candidateSelector).trim() === 'input'" not in content
    assert "id-card-upload" in content


def test_tsgen_id_card_upload_helper_completes_auth_detail_before_file_inputs():
    from e2e_agent.core.script_generation import real_action_helper_lines

    content = "\n".join(real_action_helper_lines())

    assert "async function completeAuthSmsBeforeIdCardUploadIfNeeded" in content
    assert "auth-id-card-preflight" in content
    assert "投保意愿认证" in content
    assert "提交认证" in content
    assert "获取验证码|发送认证短信|发送验证码|发送短信|获取认证短信" in content
    advance_helper = content[
        content.index("async function advanceToIdCardUploadIfNeeded") : content.index(
            "async function uploadIdCardFixtureToInput"
        )
    ]
    assert "await completeAuthSmsBeforeIdCardUploadIfNeeded(page, fileInputSelector)" in advance_helper
    assert advance_helper.index("completeAuthSmsBeforeIdCardUploadIfNeeded") < advance_helper.index(
        "const advanceText"
    )


def test_tsgen_helpers_bound_scroll_into_view_waits():
    from e2e_agent.core.script_generation import questionnaire_answer_helper_lines, real_action_helper_lines

    content = "\n".join(questionnaire_answer_helper_lines() + real_action_helper_lines())

    assert "scrollIntoViewIfNeeded().catch" not in content
    assert "scrollIntoViewIfNeeded({ timeout: 2000 }).catch" in content


def test_tsgen_chain_spec_replays_agent3_action_chain_in_order():
    from e2e_agent.core import script_generation as runtime

    rendered = runtime.render_chain_spec(
        {
            "scenario_id": "SCN-001",
            "path_id": "PATH-001",
            "entry_url": "https://example.com/detail",
            "route_nodes": ["NODE-start", "NODE-product-detail", "NODE-insure-form", "NODE-policy-result", "NODE-end"],
            "case_ids": ["TC-001"],
            "planned_page_refs": [],
            "page_content_refs": [],
            "target_node": "NODE-policy-result",
            "coverage_status": "covered",
            "contract_status": "compiled",
            "completion_rule": {"source": "agent3.live", "is_complete": True},
            "node_progress": [],
            "mock_data": {},
            "page_element_plan": [],
            "real_actions": [
                {
                    "action_key": "action.buy_now",
                    "text": "立即投保",
                    "selector": "#buy",
                    "source_url": "https://example.com/detail",
                    "planned_from_node_id": "NODE-product-detail",
                    "planned_to_node_id": "NODE-insure-form",
                },
                {
                    "action_key": "action.submit",
                    "text": "提交订单",
                    "selector": ".submit",
                    "source_url": "https://example.com/insure",
                    "planned_from_node_id": "NODE-insure-form",
                    "planned_to_node_id": "NODE-policy-result",
                },
            ],
        },
        [],
        product_id="demo-product",
        generated_by="test",
    )

    assert "import { test, expect } from '../fixtures';" in rendered
    assert 'const action1 = await replayActionLocator(page, "#buy", "a", "立即投保");' in rendered
    assert 'const action2 = await replayActionLocator(page, ".submit", "a", "提交订单");' in rendered
    assert rendered.index("const action1") < rendered.index("const action2")
    assert "../page-functions/" not in rendered
    assert "@chain       chain-to-policy-result-pc" in rendered


def test_tsgen_agent3_replay_filters_probe_actions_and_maps_semantic_steps():
    from e2e_agent.core import script_generation as runtime

    state = {
        "explore_trace": {
            "action_trace": [
                {
                    "path_id": "PATH-001",
                    "action_chain": [
                        {
                            "text": "投 保",
                            "selector": "div:nth-of-type(7)",
                            "tag": "div",
                            "source_url": "https://example.com/detail",
                            "target_url": "https://example.com/health",
                            "click_strategy": "mouse-h5-product-footer-insure",
                            "planned_from_node_id": "NODE-product-detail",
                            "planned_to_node_id": "NODE-health-notice",
                        },
                        {
                            "text": "确认无以上问题",
                            "selector": 'a >> text="确认无以上问题"',
                            "tag": "option",
                            "source_url": "https://example.com/health",
                            "click_strategy": "js-health-notice-safe-option",
                            "action_type": "minimal_data",
                            "planned_from_node_id": "NODE-health-notice",
                            "planned_to_node_id": "NODE-insure-form",
                        },
                        {
                            "text": "bank mock source=builtin-fallback",
                            "selector": "policy-tool-bank-mock",
                            "tag": "field",
                            "click_strategy": "js-minimal-data",
                            "action_type": "minimal_data",
                        },
                        {
                            "text": "投保人姓名=张三",
                            "selector": "input",
                            "tag": "field",
                            "click_strategy": "js-minimal-data",
                            "action_type": "minimal_data",
                        },
                        {
                            "text": "提交订单",
                            "selector": "div:nth-of-type(1)",
                            "tag": "div",
                            "source_url": "https://example.com/insure",
                            "click_strategy": "touchscreen-submit-btn",
                            "planned_from_node_id": "NODE-insure-form",
                            "planned_to_node_id": "NODE-suitability",
                        },
                        {
                            "text": "适当性Q1=C.未来生活规划",
                            "selector": "label >> text=C.未来生活规划",
                            "tag": "option",
                            "source_url": "https://example.com/adapt",
                            "click_strategy": "playwright-adapt-questionnaire",
                            "planned_from_node_id": "NODE-suitability",
                            "planned_to_node_id": "NODE-suitability",
                        },
                        {
                            "text": "下一步",
                            "selector": "a:nth-of-type(1)",
                            "tag": "a",
                            "source_url": "https://example.com/adapt",
                            "click_strategy": "normal",
                            "planned_from_node_id": "NODE-suitability",
                            "planned_to_node_id": "NODE-risk-control",
                        },
                        {
                            "text": "识别银行卡签约弹窗: 短信验证",
                            "selector": "[role=dialog], .am-modal",
                            "tag": "dialog",
                            "source_url": "https://example.com/authentication",
                            "click_strategy": "bank-sign-dialog-detected",
                            "planned_from_node_id": "NODE-risk-control",
                            "planned_to_node_id": "NODE-payment",
                        },
                        {
                            "text": "确定",
                            "selector": None,
                            "tag": "button",
                            "source_url": "https://example.com/authentication",
                            "click_strategy": "bank-sign-confirm",
                        },
                    ],
                }
            ]
        },
        "page_registry": {},
    }

    actions = runtime._scenario_real_actions(state, {"path_id": "PATH-001"})

    assert [action["action_key"] for action in actions] == [
        None,
        "action.answer_health_notice",
        None,
        "action.answer_questionnaire",
        None,
        "action.bank_sign_boundary",
    ]
    assert "policy-tool-bank-mock" not in [action.get("selector") for action in actions]
    assert actions[-1]["click_strategy"] == "bank-sign-dialog-detected"


def test_tsgen_chain_spec_does_not_render_agent3_probe_selectors_as_clicks():
    from e2e_agent.core import script_generation as runtime

    rendered = runtime.render_chain_spec(
        {
            "scenario_id": "SCN-001",
            "path_id": "PATH-001",
            "entry_url": "https://example.com/detail",
            "route_nodes": ["NODE-product-detail", "NODE-health-notice", "NODE-insure-form", "NODE-payment"],
            "case_ids": ["TC-001"],
            "target_node": "NODE-payment",
            "coverage_status": "covered",
            "contract_status": "compiled",
            "mock_data": {"risk_control_check.smscode": "1111"},
            "page_element_plan": [],
            "real_actions": [
                {
                    "text": "确认无以上问题",
                    "selector": 'a >> text="确认无以上问题"',
                    "tag": "option",
                    "source_url": "https://example.com/health",
                    "click_strategy": "js-health-notice-safe-option",
                    "action_key": "action.answer_health_notice",
                    "planned_from_node_id": "NODE-health-notice",
                    "planned_to_node_id": "NODE-insure-form",
                    "skip_if_absent": True,
                },
                {
                    "text": "bank mock source=builtin-fallback",
                    "selector": "policy-tool-bank-mock",
                    "tag": "field",
                    "source_url": "https://example.com/insure",
                    "click_strategy": "js-minimal-data",
                },
                {
                    "text": "识别银行卡签约弹窗: 短信验证",
                    "selector": "[role=dialog], .am-modal",
                    "tag": "dialog",
                    "source_url": "https://example.com/authentication",
                    "click_strategy": "bank-sign-dialog-detected",
                    "action_key": "action.bank_sign_boundary",
                },
            ],
        },
        [],
        product_id="demo-product",
        generated_by="test",
    )

    assert "answerHealthNotice(page)" in rendered
    assert "assertBankSignBoundary(page)" in rendered
    assert "policy-tool-bank-mock" not in rendered


def test_tsgen_real_actions_keeps_auto_wait_transition():
    from e2e_agent.core import script_generation as runtime

    state = {
        "page_registry": {
            "path_exploration_results": [
                {
                    "path_id": "PATH-001",
                    "action_chain": [
                        {
                            "script_step_id": "STEP-001",
                            "action_key": "action.auto_wait_for_next_node",
                            "text": None,
                            "selector": None,
                            "tag": "auto-wait",
                            "click_strategy": "auto_wait_for_next_node",
                            "planned_from_node_id": "NODE-underwriting-callback",
                            "planned_to_node_id": "NODE-risk-control",
                            "expected_next_node_id": "NODE-risk-control",
                        }
                    ],
                }
            ]
        }
    }

    actions = runtime._scenario_real_actions(state, {"path_id": "PATH-001"})

    assert len(actions) == 1
    assert actions[0]["action_key"] == "action.auto_wait_for_next_node"
    assert actions[0]["click_strategy"] == "auto_wait_for_next_node"
    assert actions[0]["expected_next_node_id"] == "NODE-risk-control"


def test_tsgen_rendered_spec_waits_for_auto_wait_transition_without_locator_click():
    from e2e_agent.core import script_generation as runtime

    rendered = runtime.render_scenario_spec(
        {
            "scenario_id": "SCN-001",
            "path_id": "PATH-001",
            "entry_url": "https://example.com/detail",
            "route_nodes": ["NODE-start", "NODE-underwriting-callback", "NODE-risk-control", "NODE-end"],
            "case_ids": ["TC-001"],
            "planned_page_refs": [],
            "page_content_refs": [],
            "target_node": "NODE-risk-control",
            "coverage_status": "covered",
            "completion_rule": {
                "source": "agent3.static-contract",
                "is_complete": True,
                "required_nodes": ["NODE-underwriting-callback", "NODE-risk-control"],
            },
            "node_progress": [],
            "mock_data": {},
            "page_element_plan": [],
            "real_actions": [
                {
                    "action_key": "action.auto_wait_for_next_node",
                    "text": "auto_wait_for_next_node",
                    "tag": "auto-wait",
                    "selector": None,
                    "click_strategy": "auto_wait_for_next_node",
                    "source_url": "https://example.com/detail",
                    "planned_from_node_id": "NODE-underwriting-callback",
                    "planned_to_node_id": "NODE-risk-control",
                    "expected_next_node_id": "NODE-risk-control",
                }
            ],
        },
        [],
        generated_by="test",
    )

    assert "type: 'auto-wait'" in rendered
    assert "const action1 = page.locator" not in rendered
    assert "page.locator('auto-wait')" not in rendered


def test_tsgen_build_page_functions_uses_unique_names_when_page_names_are_missing():
    from e2e_agent.core import script_generation as runtime

    functions = runtime.build_page_functions(
        {
            "entry_url": "https://example.com/product/detail",
            "regression_flow": {
                "nodes": [
                    {"node_id": "NODE-start", "type": "start"},
                    {"node_id": "NODE-product-detail", "type": "form"},
                    {"node_id": "NODE-premium-calculation", "type": "form"},
                    {"node_id": "NODE-end", "type": "end"},
                ]
            },
            "regression_paths": [
                {
                    "path_id": "PATH-001",
                    "nodes": ["NODE-start", "NODE-product-detail", "NODE-premium-calculation", "NODE-end"],
                }
            ],
            "page_registry": {},
        }
    )

    names = [item["function_name"] for item in functions]
    assert names == ["fillProductDetail", "fillPremiumCalculation"]
    assert len(names) == len(set(names))


def test_tsgen_build_page_functions_falls_back_to_node_id_when_chinese_page_names_collapse():
    from e2e_agent.core import script_generation as runtime

    functions = runtime.build_page_functions(
        {
            "entry_url": "https://example.com/product/detail",
            "regression_flow": {
                "nodes": [
                    {"node_id": "NODE-start", "type": "start"},
                    {"node_id": "NODE-product-detail", "type": "form", "page_name": "首页/产品详情"},
                    {"node_id": "NODE-premium-calculation", "type": "form", "page_name": "保费试算弹窗"},
                    {"node_id": "NODE-end", "type": "end"},
                ]
            },
            "regression_paths": [
                {
                    "path_id": "PATH-001",
                    "nodes": ["NODE-start", "NODE-product-detail", "NODE-premium-calculation", "NODE-end"],
                }
            ],
            "page_registry": {},
        }
    )

    names = [item["function_name"] for item in functions]
    assert names == ["fillProductDetail", "fillPremiumCalculation"]
    assert len(names) == len(set(names))


def test_tsgen_rendered_spec_answers_questionnaire_by_business_rule():
    from e2e_agent.core import script_generation as runtime

    rendered = runtime.render_scenario_spec(
        {
            "scenario_id": "SCN-001",
            "path_id": "PATH-001",
            "entry_url": "https://example.com/detail",
            "route_nodes": ["NODE-start", "NODE-suitability", "NODE-insure-form", "NODE-end"],
            "case_ids": ["TC-001"],
            "planned_page_refs": [],
            "page_content_refs": [],
            "target_node": "NODE-insure-form",
            "coverage_status": "covered",
            "completion_rule": {"source": "agent3.static-contract", "is_complete": True},
            "node_progress": [{"node_id": "NODE-suitability", "status": "matched"}],
            "page_element_plan": [],
            "real_actions": [
                {
                    "script_step_id": "STEP-001",
                    "action_key": "action.answer_questionnaire",
                    "text": "business_questionnaire_rule",
                    "tag": "questionnaire",
                    "answer_strategy": "business_questionnaire_rule",
                    "planned_from_node_id": "NODE-suitability",
                    "planned_to_node_id": "NODE-insure-form",
                },
                {
                    "script_step_id": "STEP-HEALTH",
                    "action_key": "action.answer_health_notice",
                    "text": "确认无以上问题",
                    "tag": "health-notice",
                    "planned_from_node_id": "NODE-health-notice",
                    "planned_to_node_id": "NODE-insure-form",
                    "skip_if_absent": True,
                },
                {
                    "script_step_id": "STEP-002",
                    "action_key": "action.next",
                    "text": "继续投保",
                    "tag": "button",
                    "planned_from_node_id": "NODE-suitability",
                    "planned_to_node_id": "NODE-insure-form",
                },
            ],
            "variants": [{"conditions": {}}],
            "mock_data": {},
        },
        [],
        generated_by="mpt-ins-ts-gen",
    )

    assert "async function answerQuestionnaire" in rendered
    assert "await answerQuestionnaire(page)" in rendered
    assert "async function answerHealthNotice" in rendered
    assert "action.answer_health_notice" in rendered
    assert "skip_if_absent" in rendered
    assert "const answerResult2 = await answerHealthNotice(page).catch(() => null);" in rendered
    assert "const answerResult2 = await answerQuestionnaire(page).catch(() => null);" not in rendered
    assert "playwright-health-notice-no-issue" in rendered
    assert "playwright-health-notice-submit" in rendered
    assert "await acceptQuestionnaireWarningIfPresent(page)" in rendered
    assert "business_questionnaire_rule" in rendered
    assert "input-name-${inputName}" in rendered
    assert "page.locator(\"questionnaire\")" not in rendered


def test_tsgen_rendered_spec_target_probes_fields_without_static_selector():
    from e2e_agent.core import script_generation as runtime

    rendered = runtime.render_scenario_spec(
        {
            "scenario_id": "SCN-001",
            "path_id": "PATH-001",
            "entry_url": "https://example.com/detail",
            "route_nodes": ["NODE-start", "NODE-insure-form", "NODE-end"],
            "case_ids": ["TC-001"],
            "planned_page_refs": [],
            "page_content_refs": [],
            "target_node": "NODE-insure-form",
            "coverage_status": "covered",
            "completion_rule": {"source": "agent3.static-contract", "is_complete": True},
            "node_progress": [{"node_id": "NODE-insure-form", "status": "matched"}],
            "page_element_plan": [
                {
                    "node_id": "NODE-insure-form",
                    "fields": [
                        {
                            "field_key": "insure_form.applicantname",
                            "selector": None,
                            "type": "string",
                            "label": "姓名",
                            "mock_value": "测试用户",
                            "locators": [{"by": "label_text", "value": "姓名"}],
                        },
                        {
                            "field_key": "insure_form.applicantidno",
                            "selector": None,
                            "type": "string",
                            "label": "applicantIdNo",
                            "mock_value": "110101199001011234",
                            "locators": [{"by": "param", "value": "applicantIdNo"}],
                        },
                    ],
                    "actions": [],
                }
            ],
            "field_resolution_plan": {
                "fields": [
                    {
                        "node_id": "NODE-insure-form",
                        "field_key": "insure_form.applicantname",
                        "selected_locator": {"by": "selector", "value": "#applicant-name"},
                        "locator_candidates": [{"by": "selector", "value": "#applicant-name"}],
                        "locator_status": "verified_static",
                    }
                ],
                "summary": {"verified_required_field_count": 1},
            },
            "component_strategy": {
                "field_strategies": [
                    {
                        "node_id": "NODE-insure-form",
                        "field_key": "insure_form.applicantname",
                        "control_type": "input_text",
                        "fill_strategy": "fill_text",
                        "strategy_status": "supported",
                    }
                ]
            },
            "validation_report": {"agent4_ready": True, "status": "passed"},
            "real_actions": [
                {
                    "script_step_id": "STEP-001",
                    "action_key": "action.submit",
                    "text": "提交订单",
                    "tag": "button",
                    "planned_from_node_id": "NODE-insure-form",
                    "planned_to_node_id": "NODE-end",
                    "skip_if_absent": True,
                }
            ],
            "variants": [{"conditions": {}}],
            "mock_data": {},
        },
        [],
        generated_by="mpt-ins-ts-gen",
    )

    assert "async function resolveFieldLocator" in rendered
    assert "targetProbeFieldSelector" in rendered
    assert "field.locators" in rendered
    assert "label_text" in rendered
    assert "data-agent-field-target" in rendered
    assert "target-probe" in rendered
    assert "const fieldResolutionPlan" in rendered
    assert "const componentStrategy" in rendered
    assert "function fieldsForNodeFromContract(nodeId: unknown): any[]" in rendered
    assert "field.field_resolution?.selected_locator" in rendered
    assert "async function fillByStrategy(page: any, locator: any, field: any, value: unknown)" in rendered
    assert "date_picker_select_or_fill" in rendered
    assert "check_agreement" in rendered
    assert "function shouldProbeFieldsForNode(nodeId: unknown): boolean" in rendered
    assert "record.node_id === nodeId" in rendered
    assert "(record.matched_node_ids ?? []).includes(nodeId)" in rendered
    assert "async function syncH5InsureFormFromMock(page: any, options: { mode?: 'initial' | 'retry' } = {}): Promise<number>" in rendered
    assert "h5-insure-form-sync" in rendered
    assert "mockData.payAccount_107" in rendered
    assert "mockData.bankName_107" in rendered
    assert "const bankAccountLabel = /银行账号|银行卡号|银行账户|卡号|payAccount/i;" in rendered
    assert "银行卡账号跳过非账号写入" in rendered
    assert "async function selectH5BankPickerByMock(page: any): Promise<Record<string, unknown>>" in rendered
    assert "await selectH5BankPickerByMock(page).catch(() => ({ selected: false }));" in rendered
    assert "bank-blocker-front-select" in rendered
    assert "dismissPickerModal(/职业选择|职业大类|职业/)" in rendered
    assert "currentRow && norm(currentRow.innerText || currentRow.textContent).includes(jobText)" in rendered
    assert "const isSelfInsured = /^(100|本人)$/.test(forWhoValue);" in rendered
    assert "const insuredName = isSelfInsured ? applicantName : rawInsuredName;" in rendered
    assert "const canonicalRow = (row: any) => row?.closest?.('.insure-filed-wrapper,.am-list-item,.module-period-picker,li,dd,section,article') || row;" in rendered
    assert "const precise = preciseRowByLabel(regex, occurrence);" in rendered
    assert "rowRoots = () => Array.from(document.querySelectorAll('.am-list-item,.am-list-line,.insure-filed-wrapper,li,dd,label,section,article'))" in rendered
    assert "setAllRowExtraText(/居住省市|省市区|省市|地区/, regionText);" in rendered
    assert "setAllRowExtraText(/职业/, jobText);" in rendered
    assert "setRowExtraText(/居住省市|省市区|省市|地区/, regionText, 1);" in rendered
    assert "setRowExtraText(/职业/, jobText, 1);" in rendered
    assert "const modulesByHeader = (headerRegex: RegExp)" in rendered
    assert "const setModulePeriod = (moduleRoot: any, start: string, end: string, label: string)" in rendered
    assert "const syncVisibleInsureModules = (phase: string)" in rendered
    assert "syncVisibleInsureModules('after-pickers');" in rendered
    assert "syncVisibleInsureModules('after-render-wait');" in rendered
    assert "await selectRegion(1);" in rendered
    assert "await selectOccupation(1);" in rendered
    assert "setModuleRowExtraText(moduleRoot, /居住省市|省市区|省市|地区/, regionText, label);" in rendered
    assert "const startNodes = Array.from(document.querySelectorAll('.date-picker-wrapper.start-picker .date span')) as any[];" in rendered
    assert "const placeholderStartNodes = Array.from(document.querySelectorAll('.date span')).filter((node: any) => /起始日期|开始日期/.test(norm(node.textContent || node.innerText))) as any[];" in rendered
    assert "fillByLabel(/姓名|被保人.*姓名|被保险人.*姓名/, insuredName, 1, '被保人姓名');" in rendered
    assert "fillByLabel(/证件号码|身份证号/, insuredIdNo, 1, '被保人证件号码');" in rendered
    assert "insured.cardPeriod = { ...(insured.cardPeriod || {}), ...clear(`${startValue}|${endValue}`) };" in rendered
    assert "next = next.setIn([...base, 'cName', 'value'], insuredName);" in rendered
    assert "next = next.setIn([...base, 'provCityText', 'value'], regionValue);" in rendered
    assert "const alreadyFilled = filledMockDataNodes.has(key);" in rendered
    assert "const forceResync = key === 'NODE-insure-form';" in rendered
    assert "if (!key || (!forceResync && alreadyFilled)) return;" in rendered
    assert "const syncMode = alreadyFilled ? 'retry' : 'initial';" in rendered
    assert "const syncedCount = await syncH5InsureFormFromMock(page, { mode: syncMode });" in rendered
    assert "if (filledCount > 0) filledMockDataNodes.add(key);" in rendered
    assert "await applyMockData(page);" not in rendered
    assert 'await applyMockData(page, "NODE-insure-form");' in rendered
    assert 'await applyMockData(page, "NODE-end");' in rendered


def test_tsgen_questionnaire_helper_supports_adapt_questionnaire_dom():
    from e2e_agent.core.script_generation import questionnaire_answer_helper_lines

    content = "\n".join(questionnaire_answer_helper_lines())

    assert "customQuestionNodes" in content
    assert ".adapt-question-wrap [data-number]" in content
    assert "[data-number].answer-radio" in content
    assert "input.insure-label" in content
    assert "getAttribute?.('value')" in content
    assert "fillQuestionnaireInlineInputs" in content
    assert "js-questionnaire-inline-input" in content
    assert "const values = ['1', '50', '10', '20'];" in content


def test_tsgen_health_notice_helper_prefers_actionable_no_issue_button():
    from e2e_agent.core.script_generation import questionnaire_answer_helper_lines

    content = "\n".join(questionnaire_answer_helper_lines())

    assert "querySelectorAll('input[type=\"button\"], input[type=\"submit\"], button, a, label, [role=\"button\"], .insure-label')" in content
    assert "querySelectorAll('input, button, a, label, span, div')" not in content
    assert "const chosen = candidates.find(item => item.text === '确认无以上问题')" in content


def test_tsgen_replay_locator_maps_sms_code_aliases():
    from e2e_agent.core.script_generation import real_action_helper_lines

    content = "\n".join(real_action_helper_lines())

    assert "smsCodeButtonText" in content
    assert "获取验证码|发送认证短信|发送验证码|发送短信|获取认证短信" in content
    assert "page.locator(actionableSelector).filter({ hasText: smsCodeButtonText }).last()" in content
    assert "process.env.AGENT3_MOCK_DATA_PATH" in content
    assert "path.resolve(mockDataDir, '..', '.tmp', 'id-card-preview')" in content


def test_tsgen_agent3_product_insure_replay_prefers_progressing_click():
    from e2e_agent.core import script_generation as runtime

    state = {
        "explore_trace": {
            "action_trace": [
                {
                    "path_id": "PATH-001",
                    "text": "投 保",
                    "selector": "div:nth-of-type(7)",
                    "tag": "div",
                    "source_url": "https://example.com/product/detail",
                    "target_url": "https://example.com/product/detail",
                    "click_strategy": "mouse-h5-product-footer-insure",
                },
                {
                    "path_id": "PATH-001",
                    "text": "投 保",
                    "selector": "a:nth-of-type(8)",
                    "tag": "a",
                    "source_url": "https://example.com/product/detail",
                    "target_url": "https://example.com/product/detail",
                    "click_strategy": "mouse-h5-product-footer-insure",
                },
                {
                    "path_id": "PATH-001",
                    "text": "投 保",
                    "selector": "a:nth-of-type(8)",
                    "tag": "a",
                    "source_url": "https://example.com/product/detail",
                    "target_url": "https://example.com/product/healthInform",
                    "click_strategy": "mouse-h5-product-footer-insure",
                },
                {
                    "path_id": "PATH-001",
                    "text": "确认无以上问题",
                    "selector": "a >> text=\"确认无以上问题\"",
                    "tag": "option",
                    "source_url": "https://example.com/product/healthInform",
                    "target_url": "https://example.com/product/healthInform",
                    "click_strategy": "js-health-notice-safe-option",
                    "action_type": "minimal_data",
                },
            ]
        },
        "page_registry": {},
    }

    actions = runtime._scenario_real_actions(state, {"path_id": "PATH-001"})

    assert [action["action_key"] for action in actions] == [None, "action.answer_health_notice"]
    assert actions[0]["selector"] == "a:nth-of-type(8)"
    assert actions[0]["target_url"] == "https://example.com/product/healthInform"


def test_tsgen_agent3_replay_skips_non_progressing_premium_quote_probe():
    from e2e_agent.core import script_generation as runtime

    state = {
        "explore_trace": {
            "action_trace": [
                {
                    "path_id": "PATH-001",
                    "text": "保费\n试算",
                    "selector": "div:nth-of-type(1)",
                    "tag": "div",
                    "source_url": "https://example.com/product/detail",
                    "target_url": "https://example.com/product/detail",
                    "click_strategy": "mouse-h5-floating-premium-quote+js-fallback",
                },
                {
                    "path_id": "PATH-001",
                    "text": "投 保",
                    "selector": "a.buy",
                    "tag": "a",
                    "source_url": "https://example.com/product/detail",
                    "target_url": "https://example.com/product/insure",
                    "click_strategy": "mouse-h5-product-footer-insure",
                },
            ]
        },
        "page_registry": {},
    }

    actions = runtime._scenario_real_actions(state, {"path_id": "PATH-001"})

    assert [action["text"] for action in actions] == ["投 保"]
    assert actions[0]["selector"] == "a.buy"


def test_tsgen_agent3_replay_skips_redundant_agreement_protocol_actions():
    from e2e_agent.core import script_generation as runtime

    state = {
        "explore_trace": {
            "action_trace": [
                {
                    "path_id": "PATH-001",
                    "text": "投 保",
                    "selector": "div:nth-of-type(24)",
                    "tag": "div",
                    "source_url": "https://example.com/m/apps/cps/product/detail",
                    "target_url": "https://example.com/m/apps/cps/product/insure",
                    "click_strategy": "mouse-h5-product-footer-insure",
                    "planned_from_node_id": "NODE-product-detail",
                    "planned_to_node_id": "NODE-premium-calculation",
                },
                {
                    "path_id": "PATH-001",
                    "text": "本人充分阅读、理解并同意《投保条件》《投保重要告知》《投保人声明及确认》",
                    "selector": "div.am-checkbox-agree",
                    "tag": "checkbox",
                    "source_url": "https://example.com/m/apps/cps/product/insure",
                    "target_url": "https://example.com/m/apps/cps/product/insure",
                    "action_type": "agreement",
                    "click_strategy": "js-agreement-line-control-check",
                    "planned_from_node_id": "NODE-premium-calculation",
                    "planned_to_node_id": "NODE-suitability",
                },
                {
                    "path_id": "PATH-001",
                    "text": "本人充分阅读、理解并同意《投保条件》《投保重要告知》《投保人声明及确认》",
                    "selector": "label.am-checkbox-wrapper",
                    "tag": "option",
                    "source_url": "https://example.com/m/apps/cps/product/insure",
                    "target_url": "https://example.com/m/apps/cps/product/insure",
                    "action_type": "minimal_data",
                    "click_strategy": "js-minimal-data",
                    "planned_from_node_id": "NODE-premium-calculation",
                    "planned_to_node_id": "NODE-suitability",
                },
                {
                    "path_id": "PATH-001",
                    "text": "《投保人声明及确认》",
                    "selector": "label.am-checkbox-wrapper",
                    "tag": "option",
                    "source_url": "https://example.com/m/apps/cps/product/insure",
                    "target_url": "https://example.com/m/apps/cps/product/insure",
                    "action_type": "minimal_data",
                    "click_strategy": "js-minimal-data",
                    "planned_from_node_id": "NODE-premium-calculation",
                    "planned_to_node_id": "NODE-suitability",
                },
                {
                    "path_id": "PATH-001",
                    "text": "《投保条件》",
                    "selector": "a:nth-of-type(1)",
                    "tag": "a",
                    "source_url": "https://example.com/m/apps/cps/product/insure",
                    "target_url": "https://example.com/m/apps/cps/product/insure",
                    "click_strategy": "normal+post-overlay-retry",
                    "planned_from_node_id": "NODE-premium-calculation",
                    "planned_to_node_id": "NODE-suitability",
                },
                {
                    "path_id": "PATH-001",
                    "text": "提交订单",
                    "selector": "div:nth-of-type(10)",
                    "tag": "div",
                    "source_url": "https://example.com/m/apps/cps/product/insure",
                    "target_url": "https://example.com/m/demo-channel/pay/?id=abc",
                    "click_strategy": "touchscreen-submit-btn",
                    "planned_from_node_id": "NODE-insure-form",
                    "planned_to_node_id": "NODE-underwriting",
                },
            ]
        },
        "page_registry": {},
    }

    actions = runtime._scenario_real_actions(state, {"path_id": "PATH-001"})

    assert [action["text"] for action in actions] == ["投 保", "提交订单"]


def test_tsgen_real_action_helper_accepts_product_notice_modal():
    from e2e_agent.core.script_generation import real_action_helper_lines

    content = "\n".join(real_action_helper_lines())

    assert "acceptProductNoticeIfPresent" in content
    assert "投保须知" in content
    assert "投保前请您仔细阅读" in content
    assert "product-notice-confirm" in content


def test_tsgen_real_action_helper_accepts_continuation_dialog():
    from e2e_agent.core.script_generation import real_action_helper_lines

    content = "\n".join(real_action_helper_lines())

    assert "acceptContinuationDialogIfPresent" in content
    assert "继续投保" in content
    assert "hasNotText: /投保人声明及确认|投保条件|投保重要告知|保险条款|免责条款|已阅读并同意/" in content
    assert "继续投保|已有|重复投保|确定|确认" not in content
    assert "continuation-dialog-confirm" in content


def test_tsgen_real_action_helper_completes_product_detail_trial_panels():
    from e2e_agent.core.script_generation import real_action_helper_lines

    content = "\n".join(real_action_helper_lines())

    assert "acceptTrialPanelIfPresent" in content
    assert "clickLastVisible(page: any" in content
    assert ".trial-pannel" in content
    assert '[role="dialog"]' in content
    assert ".am-popup" in content
    assert "保费\\s*试算" in content
    assert "保费与被保人" in content
    assert "确\\s*定" in content
    assert "panel.getByRole('button'" in content
    assert (
        "clickLastVisible(page, panel.locator('button, a, [role=\"button\"], .am-button, "
        ".am-button-primary, .submit-btn, .btn')"
    ) in content
    assert "acceptProductConfirmPanelIfPresent" in content
    assert "确认进入投保流程" in content
    assert "settlePostClickFlow" in content


def test_tsgen_real_action_replay_prefers_actionable_text_before_div_selector():
    from e2e_agent.core.script_generation import real_action_helper_lines

    content = "\n".join(real_action_helper_lines())

    assert "const actionableSelector" in content
    assert "visibleByExactText(actionableSelector)" in content
    assert "const actionableByText = await visibleByText(actionableSelector)" in content
    assert "visibleByExactText(broadSelector)" in content
    assert "const broadByText = await visibleByText(broadSelector)" in content


def test_tsgen_replay_action_locator_returns_stable_marked_candidate():
    from e2e_agent.core.script_generation import real_action_helper_lines

    content = "\n".join(real_action_helper_lines())

    assert "async function stableReplayLocator(page: any, locator: any): Promise<any>" in content
    assert "data-agent4-replay-target" in content
    assert "return await stableReplayLocator(page, candidate);" in content
    assert "return page.locator(`[data-agent4-replay-target=\"${token}\"]`).first();" in content


def test_tsgen_h5_footer_helper_stops_when_settle_reaches_next_page():
    from e2e_agent.core.script_generation import real_action_helper_lines

    content = "\n".join(real_action_helper_lines())

    assert "h5ProductInsureProgressed" in content
    assert "healthInform|health|notice|inform|product\\/insure" in content
    assert "post-click-progressed" in content


def test_tsgen_rendered_chain_waits_for_agent3_observed_next_page():
    from e2e_agent.core import script_generation as runtime

    rendered = runtime.render_chain_spec(
        {
            "scenario_id": "SCN-001",
            "path_id": "PATH-001",
            "entry_url": "https://example.com/product/detail",
            "target_node": "NODE-bank-signing",
            "route_nodes": ["NODE-product-detail", "NODE-health-notice", "NODE-insure-form"],
            "agent2_route_nodes": [],
            "case_ids": ["TC-001"],
            "mock_data": {},
            "page_element_plan": [],
            "real_actions": [
                {
                    "text": "投 保",
                    "selector": "div:nth-of-type(7)",
                    "tag": "div",
                    "source_url": "https://example.com/product/detail",
                    "target_url": "https://example.com/product/healthInform?old=1",
                    "click_strategy": "mouse-h5-product-footer-insure",
                },
                {
                    "action_key": "action.answer_health_notice",
                    "text": "answer health notice",
                    "source_url": "https://example.com/product/healthInform?new=1",
                    "target_url": "https://example.com/product/healthInform?new=1",
                    "click_strategy": "js-health-notice-safe-option",
                },
                {
                    "text": "提交订单",
                    "selector": ".submit-btn",
                    "tag": "a",
                    "source_url": "https://example.com/product/insure?new=1",
                    "target_url": "https://example.com/product/adapt",
                    "click_strategy": "touchscreen-submit-btn",
                },
            ],
        },
        [],
        product_id="demo-product",
        generated_by="test",
    )

    assert "await settlePostClickFlow(page);" in rendered
    assert 'waitForObservedUrlTransition(page, beforeUrl1, "https://example.com/product/healthInform?old=1", ' in rendered
    assert 'waitForObservedUrlTransition(page, beforeUrl2, "https://example.com/product/insure?new=1", ' in rendered
    assert 'waitForObservedUrlTransition(page, beforeUrl3, "https://example.com/product/adapt", ' in rendered


def test_tsgen_rendered_chain_preserves_h5_product_footer_strategy():
    from e2e_agent.core import script_generation as runtime

    rendered = runtime.render_chain_spec(
        {
            "scenario_id": "SCN-001",
            "path_id": "PATH-001",
            "entry_url": "https://example.com/m/apps/cps/demo/product/detail",
            "target_node": "NODE-health-notice",
            "route_nodes": ["NODE-product-detail", "NODE-health-notice"],
            "agent2_route_nodes": [],
            "case_ids": ["TC-001"],
            "mock_data": {},
            "page_element_plan": [],
            "real_actions": [
                {
                    "text": "投 保",
                    "selector": "div:nth-of-type(7)",
                    "tag": "div",
                    "source_url": "https://example.com/m/apps/cps/demo/product/detail",
                    "target_url": "https://example.com/m/apps/cps/demo/product/healthInform",
                    "click_strategy": "mouse-h5-product-footer-insure",
                }
            ],
        },
        [],
        product_id="demo-product",
        generated_by="test",
    )

    assert 'await replayH5ProductFooterInsure(page, "div:nth-of-type(7)", "div", "投 保");' in rendered
    assert 'const action1 = await replayActionLocator(page, "div:nth-of-type(7)", "div", "投 保");' not in rendered
    assert "await action1.click" not in rendered
    assert ".product-detail-footer" in rendered
    assert "page.touchscreen.tap" in rendered
    assert "async function recoverH5TransientPageError(page: any)" in rendered
    assert "系统正在维护|页面暂时无法访问|系统内部发生错误" in rendered
    assert "服务器出问题啦|请刷新重试" in rendered
    assert "502|Bad Gateway" in rendered
    assert "await recoverH5TransientPageError(page)" in rendered


def test_tsgen_zurich_product_detail_plan_selection_varies_by_path():
    from e2e_agent.core import script_generation as runtime

    def render(path_id: str) -> str:
        return runtime.render_chain_spec(
            {
                "scenario_id": path_id.replace("PATH", "SCN"),
                "path_id": path_id,
                "entry_url": "https://commerce.example.test/m/apps/cps/demo-channel/product/detail?prodId=123670",
                "target_node": "NODE-insure-form",
                "route_nodes": ["NODE-product-detail", "NODE-insure-form"],
                "agent2_route_nodes": [],
                "case_ids": ["TC-travel-product-001"],
                "mock_data": {},
                "page_element_plan": [],
                "real_actions": [
                    {
                        "text": "投 保",
                        "selector": "div:nth-of-type(24)",
                        "tag": "div",
                        "source_url": "https://commerce.example.test/m/apps/cps/demo-channel/product/detail?prodId=123670",
                        "target_url": "https://commerce.example.test/m/apps/cps/demo-channel/product/insure",
                        "planned_from_node_id": "NODE-product-detail",
                        "planned_to_node_id": "NODE-insure-form",
                        "click_strategy": "mouse-h5-product-footer-insure",
                    }
                ],
            },
            [],
            product_id="travel-product",
            generated_by="test",
        )

    exploration_plan = render("PATH-002")
    premium_plan = render("PATH-003")

    assert 'const productDetailPlan = "全球探索计划";' in exploration_plan
    assert 'const productDetailPlan = "全球完美计划";' in premium_plan
    assert exploration_plan.index("selectProductDetailCoveragePlan(page, productDetailPlan)") < exploration_plan.index(
        "const replayResult1 = await replayH5ProductFooterInsure(page"
    )
    assert "ul.condition-slide li.condition" in exploration_plan


def test_tsgen_zurich_tc003_uses_passport_identity_mock():
    from e2e_agent.core import script_generation as runtime

    rendered = runtime.render_chain_spec(
        {
            "scenario_id": "SCN-003",
            "path_id": "PATH-003",
            "entry_url": "https://commerce.example.test/m/apps/cps/demo-channel/product/detail?prodId=123670",
            "target_node": "NODE-policy-service",
            "route_nodes": [
                "NODE-product-detail",
                "NODE-premium-calculation",
                "NODE-insure-form",
                "NODE-underwriting",
                "NODE-payment",
                "NODE-policy-result",
            ],
            "agent2_route_nodes": [],
            "case_ids": ["TC-travel-product-003"],
            "node_progress": [],
            "mock_data": {},
            "page_element_plan": [],
            "real_actions": [
                {
                    "text": "投 保",
                    "selector": "div:nth-of-type(24)",
                    "tag": "div",
                    "source_url": "https://commerce.example.test/m/apps/cps/demo-channel/product/detail?prodId=123670",
                    "target_url": "https://commerce.example.test/m/apps/cps/demo-channel/product/insure",
                    "planned_from_node_id": "NODE-product-detail",
                    "planned_to_node_id": "NODE-insure-form",
                    "click_strategy": "mouse-h5-product-footer-insure",
                },
                {
                    "text": "提交订单",
                    "selector": ".submit-btn",
                    "tag": "a",
                    "source_url": "https://commerce.example.test/m/apps/cps/demo-channel/product/insure",
                    "target_url": "https://commerce.example.test/m/demo-channel/pay/?id=demo",
                    "planned_from_node_id": "NODE-insure-form",
                    "planned_to_node_id": "NODE-payment",
                    "click_strategy": "touchscreen-submit-btn",
                },
            ],
        },
        [],
        product_id="travel-product",
        generated_by="test",
    )

    assert '"applicant.id_type": "护照"' in rendered
    assert '"applicant.english_name": "eson"' in rendered
    assert '"applicant.pinyin": "eson"' in rendered
    assert '"applicant.id_no": "EA1342046"' in rendered
    assert '"insured.id_no": "EA1342046"' in rendered
    assert "const applicantCardTypeName = String(mockData['applicant.id_type']" in rendered
    assert "applicant.cardTypeName = { ...(applicant.cardTypeName || {}), ...clear(applicantCardTypeValue), text: applicantCardTypeName" in rendered
    assert "next = next.setIn([...base, 'cardTypeName', 'value'], applicantCardTypeValue);" in rendered
    assert "投保人证件类型" in rendered
    assert "护照" in rendered


def test_tsgen_product_footer_merges_confirm_panel_followup_into_entry_action():
    from e2e_agent.core import script_generation as runtime

    rendered = runtime.render_chain_spec(
        {
            "scenario_id": "SCN-001",
            "path_id": "PATH-001",
            "entry_url": "https://example.com/m/apps/cps/product/detail",
            "target_node": "NODE-health-notice",
            "route_nodes": ["NODE-product-detail", "NODE-health-notice"],
            "agent2_route_nodes": [],
            "case_ids": ["TC-001"],
            "mock_data": {},
            "page_element_plan": [],
            "real_actions": [
                {
                    "text": "投 保",
                    "selector": "a:nth-of-type(8)",
                    "tag": "a",
                    "source_url": "https://example.com/m/apps/cps/product/detail",
                    "target_url": "https://example.com/m/apps/cps/product/detail",
                    "click_strategy": "mouse-h5-product-footer-insure",
                    "planned_from_node_id": "NODE-premium-calculation",
                    "planned_to_node_id": "NODE-suitability",
                },
                {
                    "text": "已阅读并同意",
                    "selector": "a:nth-of-type(12)",
                    "tag": "a",
                    "source_url": "https://example.com/m/apps/cps/product/detail",
                    "target_url": "https://example.com/m/apps/cps/product/healthInform?encryptInsureNum=abc",
                    "click_strategy": "js",
                    "planned_from_node_id": "NODE-premium-calculation",
                    "planned_to_node_id": "NODE-suitability",
                },
                {
                    "action_key": "action.answer_health_notice",
                    "text": "确认无以上问题",
                    "source_url": "https://example.com/m/apps/cps/product/healthInform?encryptInsureNum=abc",
                    "target_url": "https://example.com/m/apps/cps/product/insure?encryptInsureNum=abc",
                    "click_strategy": "js-health-notice-safe-option",
                    "planned_from_node_id": "NODE-health-notice",
                    "planned_to_node_id": "NODE-insure-form",
                },
            ],
        },
        [],
        product_id="demo-product",
        generated_by="test",
    )

    assert 'await replayH5ProductFooterInsure(page, "a:nth-of-type(8)", "a", "投 保");' in rendered
    assert 'replayActionLocator(page, "a:nth-of-type(12)", "a", "已阅读并同意")' not in rendered
    assert (
        'waitForObservedUrlTransition(page, beforeUrl1, '
        '"https://example.com/m/apps/cps/product/healthInform?encryptInsureNum=abc", '
    ) in rendered
    assert "const answerResult2 = await answerHealthNotice(page).catch(() => null);" in rendered


def test_tsgen_product_footer_replay_retries_non_progressing_clicks():
    from e2e_agent.core.script_generation import real_action_helper_lines

    content = "\n".join(real_action_helper_lines())

    assert "attempts.push(`${clickName}-no-progress`);" in content
    assert "afterClickProgress('footer-text')" in content
    assert "afterClickProgress('page-text')" in content
    assert "afterClickProgress('footer-coordinate')" in content
    assert "afterClickProgress('agent3-selector-fallback')" in content


def test_tsgen_wait_for_observed_transition_recovers_server_retry_page():
    from e2e_agent.core.script_generation import real_action_helper_lines

    content = "\n".join(real_action_helper_lines())

    assert "服务器出问题啦" in content
    assert "transient-transition-reload" in content
    assert "retryExpectedUrlTransitionAfterTransientError" in content
    assert "await retryExpectedUrlTransitionAfterTransientError(page, expectedPath)" in content


def test_tsgen_wait_for_observed_transition_recovers_auth_final_service_dialog():
    from e2e_agent.core.script_generation import real_action_helper_lines

    content = "\n".join(real_action_helper_lines())

    assert "async function recoverAuthFinalPayTransitionAfterServiceDialog" in content
    assert "请求太平人寿异常" in content
    assert "修改投保信息" in content
    assert "auth-final-pay-recovery" in content
    wait_helper = content[
        content.index("async function waitForObservedUrlTransition") : content.index("async function fillFirstVisible")
    ]
    assert "const authFinalRecovery = await recoverAuthFinalPayTransitionAfterServiceDialog(page, expectedUrl, expectedPath)" in wait_helper
    assert wait_helper.index("const authFinalRecovery") < wait_helper.index(
        "throw new Error(`Agent3 replay transition failed"
    )


def test_tsgen_wait_for_observed_transition_recovers_h5_form_detail_pay_confirmation():
    from e2e_agent.core.script_generation import real_action_helper_lines

    content = "\n".join(real_action_helper_lines())

    assert "async function recoverH5FormDetailPayTransition" in content
    assert "isFormDetail=1" in content
    assert "submit-form-detail-pay-recovery" in content
    assert "function h5PayUrlWithCurrentEncryptInsureNum(page: any, expectedUrl: string, seedUrl = '')" in content
    recovery_helper = content[
        content.index("async function recoverH5FormDetailPayTransition")
        : content.index("async function retryExpectedUrlTransitionAfterTransientError")
    ]
    assert recovery_helper.index("const targetUrl = h5PayUrlWithCurrentEncryptInsureNum") < recovery_helper.index(
        "for (let attempt = 0; attempt < 3; attempt += 1)"
    )
    assert (
        "const canRecoverFromSeed = /\\/m\\/error\\/?$/i.test(currentPath) && "
        "/\\/product\\/insure|isFormDetail=1/i.test(beforeUrl || '')"
        in recovery_helper
    )
    assert "pay-query-error-retry" in recovery_helper
    wait_helper = content[
        content.index("async function waitForObservedUrlTransition") : content.index("async function fillFirstVisible")
    ]
    assert (
        "const formDetailPayRecovery = await recoverH5FormDetailPayTransition(page, expectedUrl, expectedPath, beforeUrl)"
        in wait_helper
    )
    assert wait_helper.index("const formDetailPayRecovery") < wait_helper.index(
        "throw new Error(`Agent3 replay transition failed"
    )


def test_tsgen_wait_for_observed_transition_recovers_identity_pay_error_before_form_detail_retry():
    from e2e_agent.core.script_generation import real_action_helper_lines

    content = "\n".join(real_action_helper_lines())
    wait_helper = content[
        content.index("async function waitForObservedUrlTransition") : content.index("async function fillFirstVisible")
    ]

    assert (
        "const identityTaskRecovery = await recoverIdentityTaskAfterSubmitIfNeeded(page, expectedUrl)"
        in wait_helper
    )
    assert "submit-identity-task-recovery" in wait_helper
    assert wait_helper.index("const identityTaskRecovery") < wait_helper.index("const formDetailPayRecovery")
    assert wait_helper.index("const identityTaskRecovery") < wait_helper.index(
        "throw new Error(`Agent3 replay transition failed"
    )


def test_tsgen_rendered_chain_preserves_h5_submit_strategy():
    from e2e_agent.core import script_generation as runtime

    rendered = runtime.render_chain_spec(
        {
            "scenario_id": "SCN-001",
            "path_id": "PATH-001",
            "entry_url": "https://example.com/m/apps/cps/demo/product/insure",
            "target_node": "NODE-underwriting",
            "route_nodes": ["NODE-insure-form", "NODE-underwriting"],
            "agent2_route_nodes": [],
            "case_ids": ["TC-001"],
            "mock_data": {},
            "page_element_plan": [],
            "real_actions": [
                {
                    "text": "提交订单",
                    "selector": "div:nth-of-type(1)",
                    "tag": "div",
                    "source_url": "https://example.com/m/apps/cps/demo/product/insure",
                    "target_url": "https://example.com/m/apps/cps/demo/product/adapt/loading",
                    "click_strategy": "touchscreen-submit-btn",
                }
            ],
        },
        [],
        product_id="demo-product",
        generated_by="test",
    )

    assert (
        'await replayH5SubmitButton(page, "div:nth-of-type(1)", "div", "提交订单", '
        '"https://example.com/m/apps/cps/demo/product/adapt/loading");'
    ) in rendered
    assert 'const action1 = await replayActionLocator(page, "div:nth-of-type(1)", "div", "提交订单");' not in rendered
    assert ".insure-footer .submit-btn" in rendered
    assert "page.touchscreen.tap" in rendered
    assert "async function clickH5SubmitCandidate(page: any, locator: any): Promise<boolean>" in rendered
    assert "async function triggerH5SubmitDomClick(page: any): Promise<boolean>" in rendered
    assert "bank-row-error-icon" in rendered
    assert "for (let submitAttempt = 0; submitAttempt < 12" in rendered
    assert "clickTaskModalGoCompleteIfPresent" in rendered
    assert "task-modal-go-complete" in rendered
    assert "async function syncH5InsureFormFromMock(page: any, options: { mode?: 'initial' | 'retry' } = {}): Promise<number>" in rendered
    assert "await syncH5InsureFormFromMock(page, { mode: 'initial' }).catch(() => 0);" in rendered
    assert "await syncH5InsureFormFromMock(page, { mode: 'retry' }).catch(() => 0);" in rendered
    assert "const skipPayAccountWrites = syncMode === 'retry' || !!(window as any).__agent3SkipPayAccountWrites;" in rendered
    assert "const alreadyFilled = filledMockDataNodes.has(key);" in rendered
    assert "const syncMode = alreadyFilled ? 'retry' : 'initial';" in rendered
    assert "const syncedCount = await syncH5InsureFormFromMock(page, { mode: syncMode });" in rendered
    assert "async function ensureH5AgreementCheckedBeforeSubmit(page: any): Promise<number>" in rendered
    assert "await ensureH5AgreementCheckedBeforeSubmit(page).catch(() => 0);" in rendered
    assert ".am-checkbox-agree" in rendered
    assert "const tapped = await tapH5AgreementControls(page).catch(() => 0);" in rendered
    assert "const tapLeftControl = (root: any) =>" not in rendered
    assert "async function tapH5AgreementControls(page: any): Promise<number>" in rendered
    submit_helper = rendered[
        rendered.index("async function replayH5SubmitButton") : rendered.index(
            "async function acceptTrialPanelIfPresent"
        )
    ]
    assert "await tapH5AgreementControls(page).catch(() => 0);" not in submit_helper
    assert (
        "await settlePostClickFlow(page, { includeAgreementDetails: false }).catch(() => []);\n"
        "    await ensureH5AgreementCheckedBeforeSubmit(page).catch(() => 0);\n"
        "    await assertH5AgreementCheckedBeforeSubmit(page);\n"
        "    let clickedThisAttempt = false;"
    ) in submit_helper
    assert "await settlePostClickFlow(page, { includeAgreementDetails: false }).catch(() => []);" in submit_helper
    assert "if (options.includeAgreementDetails !== false) handlers.push(acceptAgreementDetailDialogIfPresent);" in rendered
    assert "await page.mouse.click(point.x, point.y).catch(async () => {" in rendered
    assert "await page.touchscreen.tap(point.x, point.y).catch(() => undefined);" in rendered
    assert "const seenPoints = new Set<string>();" in rendered
    assert "(control as HTMLElement).scrollIntoView({ block: 'center', inline: 'center' });" in rendered
    assert "if ((control as HTMLElement).matches?.('a,.diy_color,[href]')) return null;" in rendered
    assert "target: String((control as HTMLElement).tagName || '').toLowerCase()," in rendered
    assert "className: String((control as HTMLElement).className || '').slice(0, 80)," in rendered
    for helper_name in (
        "ensureH5AgreementCheckedBeforeSubmit",
        "tapH5AgreementControls",
        "assertH5AgreementCheckedBeforeSubmit",
    ):
        helper_start = rendered.index(f"async function {helper_name}")
        helper_end = rendered.find("\nasync function ", helper_start + 1)
        helper_body = rendered[helper_start : helper_end if helper_end != -1 else len(rendered)]
        assert "本人充分阅读" in helper_body
    assert "const dispatchMouse = (node: any) =>" not in rendered
    assert "if (target !== root) dispatchMouse(target);" not in rendered
    assert "descriptor.set.call(input, true)" not in rendered
    assert "input.setAttribute('checked', 'checked')" not in rendered
    assert "if (tapped > 0) await settlePostClickFlow(page).catch(() => []);" in rendered
    assert "throw new Error('H5 agreement checkbox is still unchecked before submit');" in rendered
    ensure_helper = rendered[
        rendered.index("async function ensureH5AgreementCheckedBeforeSubmit")
        : rendered.index("async function tapH5AgreementControls")
    ]
    assert "const settled = await settlePostClickFlow(page).catch(() => []);" in ensure_helper
    assert "const tapped = await tapH5AgreementControls(page).catch(() => 0);" in ensure_helper
    assert "tapLeftControl(root);" not in ensure_helper
    assert "for (let attempt = 0; attempt < 12; attempt += 1)" in rendered
    assert "const jobValue = String(mockData.jobText_10 || mockData['applicant.occupation_code'] || '6546010-6546043-6546243-1');" in rendered
    assert "const hasVisibleOccupationControl = () =>" in rendered
    assert "const shouldPatchOccupation = (entity: any) => hasOccupationState(entity) || hasVisibleOccupationControl();" in rendered
    assert "const regionValue = String(mockData.provCityText_10 || mockData['applicant.region_code'] || '110000-110105');" in rendered
    assert "if (shouldPatchOccupation(applicant)) applicant.jobText = { ...(applicant.jobText || {}), ...clear(jobValue), text: jobText, label: jobText, name: jobText };" in rendered
    assert "applicant.provCityText = { ...(applicant.provCityText || {}), ...clear(regionValue), text: regionText, label: regionText, name: regionText };" in rendered
    assert "if (shouldPatchImmutableOccupation) {" in rendered
    assert "next = next.setIn([...base, 'jobText', 'value'], jobValue);" in rendered
    assert "next = next.setIn([...base, 'provCityText', 'value'], regionValue);" in rendered


def test_tsgen_h5_submit_syncs_travel_fields_and_english_name_state():
    from e2e_agent.core import script_generation as runtime

    rendered = runtime.render_chain_spec(
        {
            "scenario_id": "SCN-001",
            "path_id": "PATH-001",
            "entry_url": "https://example.com/m/apps/cps/demo/product/insure",
            "target_node": "NODE-underwriting",
            "route_nodes": ["NODE-insure-form", "NODE-underwriting"],
            "agent2_route_nodes": [],
            "case_ids": ["TC-001"],
                "mock_data": {
                    "applicant.name": "陈雨欣",
                    "applicant.english_name": "chenyuxin",
                    "applicant.birthdate": "1994-01-09",
                    "policy.start_date": "2026-06-07",
                    "travel.destination": "中国澳门",
                },
            "page_element_plan": [],
            "real_actions": [
                {
                    "text": "提交订单",
                    "selector": "div:nth-of-type(1)",
                    "tag": "div",
                    "source_url": "https://example.com/m/apps/cps/demo/product/insure",
                    "target_url": "https://example.com/m/demo-channel/pay/?id=abc",
                    "click_strategy": "touchscreen-submit-btn",
                }
            ],
        },
        [],
        product_id="demo-product",
        generated_by="test",
    )

    assert "const applicantEnglishName = englishNameFor('applicant', applicantName);" in rendered
    assert "const romanizeChineseName = (name: string, fallback: string) =>" in rendered
    assert '"陈": "chen"' in rendered
    assert "return romanizeChineseName(fallbackName, prefix === 'insured' ? 'lisi' : 'zhangsan');" in rendered
    assert "fillByPlaceholder(/eName|english|pinyin|英文|拼音/i, applicantEnglishName, '投保人拼音/英文名');" in rendered
    assert "fillByPlaceholder(/真实姓名|姓名/, applicantName, '投保人姓名');" in rendered
    assert "applicant.eName = { ...(applicant.eName || {}), ...clear(applicantEnglishName) };" in rendered
    assert "next = next.setIn([...base, 'eName', 'value'], applicantEnglishName);" in rendered
    assert "const policyStartDate = formatDate(" in rendered
    assert "mockData['policy.start_date']" in rendered
    assert "mockData['insure_form.insurancedate']" in rendered
    assert "const embeddedMockData = {" in rendered
    assert "const mockData = withAgent4RuntimeMockDataOverrides(embeddedMockData);" in rendered
    assert "function withAgent4RuntimeMockDataOverrides(base: Record<string, unknown>): Record<string, unknown>" in rendered
    assert "function parseAgent4MockDataOverrides(): Record<string, unknown>" in rendered
    assert "process.env.AGENT4_MOCK_DATA_OVERRIDES" in rendered
    assert "process.env.AGENT4_POLICY_START_DATE" in rendered
    assert "process.env.AGENT4_POLICY_START_OFFSET_DAYS" in rendered
    assert "dateFromAgent4OffsetDays(process.env.AGENT4_POLICY_START_OFFSET_DAYS || '1')" in rendered
    assert "'insure_form.insurancedate': policyStartDate" in rendered
    assert "const applicantBirthdate = String(mockData['applicant.birthdate'] || mockData['applicant.birthday'] || '').trim();" in rendered
    assert "const insuredBirthdate = isSelfInsured ? applicantBirthdate : rawInsuredBirthdate;" in rendered
    assert "const patchedTrialGenesValue = (value: any) =>" in rendered
    assert "const insuredAgeBand = ageBandForBirthdate(insuredBirthdate, policyStartDate);" in rendered
    assert "if (gene.key === 'insurantDate' || gene.geneKey === 'insurantDate')" in rendered
    assert "return { ...gene, value: insuredAgeBand || insuredBirthdate };" in rendered
    assert "patchTrialGenesInsurantDate(obj);" in rendered
    assert "async function installH5SubmitPayloadPatch(page: any)" in rendered
    assert "page.route('**/api/apps/cps/insure/submit**'" in rendered
    assert "page.route('**/api/apps/cps/product/trial/insured**'" in rendered
    assert "let agent4LatestTrialInsuredResult: Record<string, unknown> | null = null;" in rendered
    assert "/\\/api\\/apps\\/cps\\/product\\/trial\\/insured/i.test(url)" in rendered
    assert "agent4LatestTrialInsuredResult = payload.data;" in rendered
    assert "function agent4LatestTrialResultForSubmit()" in rendered
    assert "agent4TrialInsuredAgeBand" in rendered
    assert "(globalThis as any).__agent4ExpectedTrialAgeBand = insuredAgeBand;" in rendered
    assert "const trialPriceFromLatestResult = (): number | null =>" in rendered
    assert "payload.trialGenes = trialGenesFromLatestResult(patchTrialGenesValue(payload.trialGenes));" in rendered
    assert "if (latestTrialPrice !== null) payload.price = latestTrialPrice;" in rendered
    assert "await waitForAgent4TrialInsuredResultForSubmit(5000).catch(() => null);" in rendered
    assert "patchAgent3SubmitPayload" in rendered
    assert "const applicantName = String(submitMockData['applicant.name']" in rendered
    assert "const applicantIdNo = String(submitMockData['applicant.id_no']" in rendered
    assert "const applicantMobile = String(submitMockData['applicant.mobile']" in rendered
    assert "const applicantEmail = String(submitMockData['applicant.email']" in rendered
    assert "applicant.cName = applicantName;" in rendered
    assert "applicant.eName = applicantEnglishName;" in rendered
    assert "applicant.cardNumber = applicantIdNo;" in rendered
    assert "applicant.moblie = applicantMobile;" in rendered
    assert "applicant.email = applicantEmail;" in rendered
    assert "insured.cName = insuredName;" in rendered
    assert "insured.eName = insuredEnglishName;" in rendered
    assert "insured.cardNumber = insuredIdNo;" in rendered
    assert "insured.moblie = insuredMobile;" in rendered
    assert "insured.email = insuredEmail;" in rendered
    assert "if (method === 'POST' && postData) {" in rendered
    assert "async function captureAgent4BusinessScreenshot(page: any, label: string, meta: Record<string, unknown> = {})" in rendered
    assert "agent4-business-" in rendered
    assert "fs.writeFileSync(outputPath.replace(/\\.[^.]+$/, '.json'), JSON.stringify(metadata, null, 2), 'utf-8');" in rendered
    assert "await captureAgent4BusinessScreenshot(page, 'initial-page'," in rendered
    initial_capture = rendered[
        rendered.index("await captureAgent4BusinessScreenshot(page, 'initial-page',")
        : rendered.index("await applyMockData(page")
    ]
    assert "phase: 'initial-page'" in initial_capture
    assert 'planned_to_node_id: "NODE-insure-form"' in initial_capture
    assert "await captureAgent4BusinessScreenshot(page, 'step-1'," in rendered
    assert 'path_id: "PATH-001"' in rendered
    assert "planned_to_node_id:" in rendered
    assert "await captureAgent4BusinessScreenshot(page, 'step-1-before-submit'," in rendered
    assert "phase: 'before-submit'" in rendered
    assert "planned_to_node_id: \"NODE-insure-form\"" in rendered
    assert "await captureAgent4BusinessScreenshot(page, 'final'" in rendered
    assert "phase: 'final'" in rendered
    assert not re.search(r"(?<![A-Z0-9_])AGENT3_MOCK_DATA(?![A-Z0-9_])", rendered)
    assert "applicant.birthdate = { ...(applicant.birthdate || {}), ...clear(applicantBirthdate) };" in rendered
    assert "insured.birthdate = { ...(insured.birthdate || {}), ...clear(insuredBirthdate) };" in rendered
    assert "next = patchImmutableTrialGenes(next, ['product', 'insure', 'data', 'trialGenes']);" in rendered
    assert "const setBusinessRowText = (label: string, labelRegex: RegExp, displayText: string) =>" in rendered
    assert "let changed = setBusinessRowText(label, labelRegex, displayText);" in rendered
    assert "let changed = setAllRowExtraText(labelRegex, displayText);" not in rendered
    assert "setBusinessField('起保日期', 'insuranceDate', policyStartDate, '102')" in rendered
    assert "setBusinessField('出行目的', 'purpose', travelPurposeValue, '40')" in rendered
    assert "setBusinessField('出行目的地', 'tripDestination', travelDestination, '40')" in rendered


def test_tsgen_rendered_spec_uses_safe_click_helper_for_generic_replay_actions():
    from e2e_agent.core import script_generation as runtime

    rendered = runtime.render_scenario_spec(
        {
            "scenario_id": "SCN-001",
            "path_id": "PATH-001",
            "entry_url": "https://example.com/insure",
            "route_nodes": ["NODE-insure-form"],
            "case_ids": ["TC-001"],
            "planned_page_refs": [],
            "page_content_refs": [],
            "target_node": "NODE-insure-form",
            "coverage_status": "covered",
            "completion_rule": {"source": "agent3.static-contract", "is_complete": True},
            "node_progress": [{"node_id": "NODE-insure-form", "status": "matched"}],
            "real_actions": [
                {
                    "text": "继续",
                    "selector": "button.next",
                    "tag": "button",
                    "source_url": "https://example.com/insure",
                    "target_url": "https://example.com/insure",
                    "planned_from_node_id": "NODE-insure-form",
                    "planned_to_node_id": "NODE-insure-form",
                }
            ],
        },
        [],
        generated_by="test",
    )

    assert "async function clickReplayAction(page: any, locator: any): Promise<boolean>" in rendered
    assert "await clickReplayAction(page, action1);" in rendered
    assert "await action1.click({ timeout: 10000, noWaitAfter: true });" not in rendered


def test_tsgen_h5_submit_recovers_suitability_task_blocker():
    from e2e_agent.core import script_generation as runtime

    rendered = runtime.render_chain_spec(
        {
            "scenario_id": "SCN-001",
            "path_id": "PATH-001",
            "entry_url": "https://example.com/m/apps/cps/demo/product/insure",
            "target_node": "NODE-underwriting",
            "route_nodes": ["NODE-insure-form", "NODE-underwriting"],
            "agent2_route_nodes": [],
            "case_ids": ["TC-001"],
            "mock_data": {},
            "page_element_plan": [],
            "real_actions": [
                {
                    "text": "提交订单",
                    "selector": "div:nth-of-type(1)",
                    "tag": "div",
                    "source_url": "https://example.com/m/apps/cps/demo/product/insure",
                    "target_url": "https://example.com/m/apps/cps/demo/product/adapt/loading?encryptInsureNum=abc",
                    "click_strategy": "touchscreen-submit-btn",
                }
            ],
        },
        [],
        product_id="demo-product",
        generated_by="test",
    )

    assert "const agent4NetworkResponses: Array<Record<string, unknown>> = [];" in rendered
    assert "function recentSuitabilitySubmitBlocker()" in rendered
    assert "async function waitForSuitabilitySubmitBlocker(page: any, timeoutMs = 12000)" in rendered
    assert "async function recoverSuitabilityTaskAfterSubmitIfNeeded" in rendered
    assert "40015|需要进行适当性问卷|适当性问卷" in rendered
    assert "waitForSuitabilitySubmitBlocker(page, 6000)" in rendered
    assert "waitForIdentitySubmitBlocker(page, 6000)" in rendered
    assert "await Promise.all([" in rendered
    assert "const suitabilityRecovery" in rendered
    assert "submit-suitability-task-recovery" in rendered
    assert rendered.index("const suitabilityRecovery") < rendered.index(
        'await waitForObservedUrlTransition(page, beforeUrl1, "https://example.com/m/apps/cps/demo/product/adapt/loading?encryptInsureNum=abc", '
    )


def test_tsgen_h5_submit_recovers_identity_auth_task_blocker():
    from e2e_agent.core import script_generation as runtime

    rendered = runtime.render_chain_spec(
        {
            "scenario_id": "SCN-001",
            "path_id": "PATH-001",
            "entry_url": "https://example.com/m/apps/cps/demo/product/insure",
            "target_node": "NODE-policy-result",
            "route_nodes": ["NODE-insure-form", "NODE-policy-result"],
            "agent2_route_nodes": [],
            "case_ids": ["TC-001"],
            "mock_data": {},
            "page_element_plan": [],
            "real_actions": [
                {
                    "text": "提交订单",
                    "selector": "div:nth-of-type(1)",
                    "tag": "div",
                    "source_url": "https://example.com/m/apps/cps/demo/product/insure?encryptInsureNum=abc",
                    "target_url": "https://example.com/m/demo-channel/pay/?id=abc",
                    "click_strategy": "touchscreen-submit-btn",
                }
            ],
        },
        [],
        product_id="demo-product",
        generated_by="test",
    )

    assert "function recentIdentitySubmitBlocker()" in rendered
    assert "async function waitForIdentitySubmitBlocker(page: any, timeoutMs = 12000)" in rendered
    assert "async function recoverIdentityTaskAfterSubmitIfNeeded" in rendered
    assert "37009|taskType" in rendered
    assert "/api/apps/cps/insure/task/approve/process" in rendered
    assert "/api/apps/cps/insure/task/approve/list" in rendered
    assert "/api/apps/cps/insure/task/next/do" in rendered
    assert "function latestIdentitySubmitEncryptInsureNum" in rendered
    assert "function h5CpsTaskBasePath" in rendered
    assert "}/product/task`" in rendered
    assert "}/authentication/detail`" in rendered
    assert "target.searchParams.set('encryptInsureNum', encryptInsureNum);" in rendered
    assert "async function clickIdentityTaskGoCompleteIfPresent" in rendered
    assert "identity-task-go-complete" in rendered
    assert "async function completeIdentityAuthTaskIfNeeded" in rendered
    assert "auth-identity-upload" in rendered
    assert "auth-identity-confirm-next" in rendered
    assert "submit-identity-task-recovery" in rendered
    assert "const identityRecovery" in rendered
    assert "test.info().annotations.push({ type: 'submit-identity-task-recovery'" in rendered
    assert rendered.index("const identityRecovery") < rendered.index(
        'await waitForObservedUrlTransition(page, beforeUrl1, "https://example.com/m/demo-channel/pay/?id=abc", '
    )


def test_tsgen_h5_submit_accepts_customer_suitability_evaluation_modal():
    from e2e_agent.core import script_generation as runtime

    rendered = runtime.render_chain_spec(
        {
            "scenario_id": "SCN-001",
            "path_id": "PATH-001",
            "entry_url": "https://example.com/m/apps/cps/demo/product/insure",
            "target_node": "NODE-policy-result",
            "route_nodes": ["NODE-insure-form", "NODE-policy-result"],
            "agent2_route_nodes": [],
            "case_ids": ["TC-001"],
            "mock_data": {},
            "page_element_plan": [],
            "real_actions": [
                {
                    "text": "提交订单",
                    "selector": "div:nth-of-type(1)",
                    "tag": "div",
                    "source_url": "https://example.com/m/apps/cps/demo/product/insure",
                    "target_url": "https://example.com/m/demo-channel/pay/?id=abc",
                    "click_strategy": "touchscreen-submit-btn",
                }
            ],
        },
        [],
        product_id="demo-product",
        generated_by="test",
    )

    assert "async function acceptCustomerSuitabilityEvaluationIfPresent(page: any)" in rendered
    assert "客户适当性评估" in rendered
    assert "已阅读并同意" in rendered
    assert "accept-customer-suitability-evaluation" in rendered
    assert "async function acceptAgreementDetailDialogIfPresent(page: any)" in rendered
    assert "agreement-detail-dialog" in rendered
    settle_helper = rendered[
        rendered.index("async function settlePostClickFlow") : rendered.index("function observedPath")
    ]
    customer_helper = rendered[
        rendered.index("async function acceptCustomerSuitabilityEvaluationIfPresent")
        : rendered.index("async function acceptAgreementDetailDialogIfPresent")
    ]
    agreement_helper = rendered[
        rendered.index("async function acceptAgreementDetailDialogIfPresent")
        : rendered.index("async function settlePostClickFlow")
    ]
    continuation_helper = rendered[
        rendered.index("async function acceptContinuationDialogIfPresent")
        : rendered.index("async function clickLastVisible")
    ]
    assert ", body'" not in customer_helper
    assert "span, div" in customer_helper
    assert "let sawVisiblePanel = false;" in customer_helper
    assert "sawVisiblePanel = true;" in customer_helper
    assert "clickLastVisibleShortText(page" in customer_helper
    assert "panel.getByRole('button'" in customer_helper
    assert "panel.getByRole('button'" in agreement_helper
    assert "span, div" in agreement_helper
    assert ", body'" not in agreement_helper
    assert ".am-drawer-content" in agreement_helper
    assert "bottom-sheet-agreement-detail" in agreement_helper
    assert "clickAgreementButtonByDom" in agreement_helper
    assert "document.querySelectorAll" in agreement_helper
    assert "[class*=\"drawer\"]" in agreement_helper
    assert "new MouseEvent('click'" in agreement_helper
    assert "let sawVisiblePanel = false;" in agreement_helper
    assert "sawVisiblePanel = true;" in agreement_helper
    assert "clickLastVisibleShortText(page" in agreement_helper
    assert "acceptCustomerSuitabilityEvaluationIfPresent" in settle_helper
    assert "acceptAgreementDetailDialogIfPresent" in settle_helper
    assert "acceptContinuationDialogIfPresent" in settle_helper
    assert "订单已存在" in continuation_helper
    assert "是否要再次提交" in continuation_helper
    assert "提交|确定|确认|继续投保|继续|是" in continuation_helper
    assert "continuation-dialog-dom-confirm" in continuation_helper
    assert "document.querySelectorAll" in continuation_helper
    assert "am-modal-content" in continuation_helper
    assert "adm-dialog" in continuation_helper
    assert "查看|取消|关闭|返回" in continuation_helper
    assert "new MouseEvent('click'" in continuation_helper
    assert settle_helper.index("acceptCustomerSuitabilityEvaluationIfPresent") < settle_helper.index(
        "acceptAgreementDetailDialogIfPresent"
    )
    assert settle_helper.index("acceptAgreementDetailDialogIfPresent") < settle_helper.index(
        "acceptContinuationDialogIfPresent"
    )
    assert settle_helper.index("acceptAgreementDetailDialogIfPresent") < settle_helper.index(
        "acceptQuestionnaireWarningIfPresent"
    )


def test_tsgen_h5_submit_retries_after_post_submit_agreement_dialog():
    from e2e_agent.core import script_generation as runtime

    rendered = runtime.render_chain_spec(
        {
            "scenario_id": "SCN-001",
            "path_id": "PATH-001",
            "entry_url": "https://example.com/m/apps/cps/demo/product/insure",
            "target_node": "NODE-policy-result",
            "route_nodes": ["NODE-insure-form", "NODE-policy-result"],
            "agent2_route_nodes": [],
            "case_ids": ["TC-001"],
            "mock_data": {},
            "page_element_plan": [],
            "real_actions": [
                {
                    "text": "提交订单",
                    "selector": "div:nth-of-type(1)",
                    "tag": "div",
                    "source_url": "https://example.com/m/apps/cps/demo/product/insure",
                    "target_url": "https://example.com/m/demo-channel/pay/?id=abc",
                    "click_strategy": "touchscreen-submit-btn",
                }
            ],
        },
        [],
        product_id="demo-product",
        generated_by="test",
    )

    submit_helper = rendered[
        rendered.index("async function replayH5SubmitButton") : rendered.index(
            "async function acceptTrialPanelIfPresent"
        )
    ]

    assert "post-submit-dialog-settled" in submit_helper
    assert "continuation-dialog-settled" in submit_helper
    assert "agreement-detail-dialog" in submit_helper
    assert "bottom-sheet-agreement-detail" in submit_helper
    assert "accept-customer-suitability-evaluation" in submit_helper
    assert "continuation-dialog" in submit_helper
    assert "await Promise.all([" in submit_helper
    assert "waitForSuitabilitySubmitBlocker(page, 6000)" in submit_helper
    assert "waitForIdentitySubmitBlocker(page, 6000)" in submit_helper
    assert submit_helper.index("settled = await settlePostClickFlow(page).catch(() => []);") < submit_helper.index(
        "waitForSuitabilitySubmitBlocker(page, 6000)"
    )
    assert submit_helper.index("const currentPath = observedPath(page.url());") < submit_helper.index(
        "post-submit-dialog-settled"
    )


def test_tsgen_h5_submit_recovers_health_notice_redirect_before_accepting_progress():
    from e2e_agent.core import script_generation as runtime

    rendered = runtime.render_chain_spec(
        {
            "scenario_id": "SCN-001",
            "path_id": "PATH-001",
            "entry_url": "https://example.com/m/apps/cps/demo/product/insure",
            "target_node": "NODE-underwriting",
            "route_nodes": ["NODE-insure-form", "NODE-underwriting"],
            "agent2_route_nodes": [],
            "case_ids": ["TC-001"],
            "mock_data": {},
            "page_element_plan": [],
            "real_actions": [
                {
                    "text": "提交订单",
                    "selector": "div:nth-of-type(1)",
                    "tag": "div",
                    "source_url": "https://example.com/m/apps/cps/demo/product/insure",
                    "target_url": "https://example.com/m/apps/cps/demo/product/adapt?encryptInsureNum=abc",
                    "click_strategy": "touchscreen-submit-btn",
                }
            ],
        },
        [],
        product_id="demo-product",
        generated_by="test",
    )

    assert "function isHealthNoticePageUrl" in rendered
    assert "async function recoverHealthNoticeAfterSubmitIfNeeded" in rendered
    assert "submit-health-notice-recovery" in rendered
    assert "const healthNoticeRecovery = await recoverHealthNoticeAfterSubmitIfNeeded(page, expectedUrl)" in rendered
    submit_helper = rendered[
        rendered.index("async function replayH5SubmitButton") : rendered.index(
            "async function acceptTrialPanelIfPresent"
        )
    ]
    assert submit_helper.index("const healthNoticeRecovery") < submit_helper.index(
        "const currentPath = observedPath(page.url());"
    )


def test_tsgen_questionnaire_helper_accepts_warning_modal():
    from e2e_agent.core.script_generation import questionnaire_answer_helper_lines

    content = "\n".join(questionnaire_answer_helper_lines())

    assert "acceptQuestionnaireWarningIfPresent" in content
    assert "投保风险警示确认书" in content
    assert "阅读并同意" in content
    assert "questionnaire-warning-confirm" in content
    assert "async function advanceSuitabilityIntroIfPresent(page: any)" in content
    assert "特别提示|填写本调查问卷前|评估问卷" in content
    assert "await advanceSuitabilityIntroIfPresent(page)" in content


def test_tsgen_questionnaire_helper_uses_business_questionnaire_rule():
    from e2e_agent.core.script_generation import questionnaire_answer_helper_lines

    content = "\n".join(questionnaire_answer_helper_lines())

    assert "business_questionnaire_rule" in content
    assert "isPurposeQuestion" in content
    assert "保障需求" in content
    assert "保险需求" in content
    assert "目的" in content
    assert "为了什么" in content
    assert "想通过" not in content
    assert "主要解决" not in content
    assert "担心" not in content
    assert "options.length - 1" in content
    assert "preferredBusinessQuestionnaireChoice" in content
    assert "business-safe-option" in content
    assert "未来生活规划|养老|子女教育|退休收入|保单利益" in content
    assert "20%及以下" in content
    assert "一次性支付|一次性" in content


def test_tsgen_builds_page_functions_from_agent3_trace_and_elements():
    from e2e_agent.core import script_generation as runtime

    state = {
        "product_id": "demo-product",
        "entry_url": "https://example.com/detail",
        "regression_flow": {
            "nodes": [
                {"node_id": "NODE-start", "type": "start"},
                {"node_id": "NODE-applicant-info", "type": "form", "page_name": "Applicant Info"},
                {"node_id": "NODE-end", "type": "end"},
            ]
        },
        "regression_paths": [
            {
                "path_id": "PATH-001",
                "nodes": ["NODE-start", "NODE-applicant-info", "NODE-end"],
            }
        ],
        "page_registry": {
            "page_content_records": [
                {
                    "page_content_record_id": "PCR-001",
                    "matched_node_ids": ["NODE-applicant-info"],
                    "field_map": [
                        {
                            "field_key": "applicantName",
                            "selector": "#applicantName",
                            "required": True,
                            "raw": {"tag": "input", "type": "text", "label": "投保人姓名"},
                        }
                    ],
                    "selector_map": {
                        "actions": [
                            {
                                "text": "下一步",
                                "selector": "button.next",
                                "tag": "button",
                            }
                        ]
                    },
                }
            ],
            "path_exploration_results": [
                {
                    "path_id": "PATH-001",
                    "node_progress": [{"node_id": "NODE-applicant-info", "status": "matched"}],
                    "node_execution_trace": [
                        {
                            "phase": "verify",
                            "target_node_id": "NODE-applicant-info",
                            "target_matched": True,
                        },
                        {
                            "phase": "act",
                            "node_id": "NODE-applicant-info",
                            "target_node_id": "NODE-insured-info",
                            "text": "下一步",
                            "selector": "button.next",
                        }
                    ],
                }
            ],
        },
    }

    functions = runtime.build_page_functions(state)

    assert functions[0]["source"] == "agent3.trace"
    assert functions[0]["verified"] is True
    assert functions[0]["params"][0]["name"] == "applicantName"
    assert functions[0]["fields"][0]["selector"] == "#applicantName"
    assert functions[0]["actions"][0]["source"] == "agent3.action_trace"

    rendered = runtime.render_page_function_file(functions[0])
    assert "page.setContent" not in rendered
    assert "expectApplicantInfoPage" in rendered
    assert "fillByFallback" in rendered
    assert "clickApplicantInfoNext" in rendered
    assert "assertApplicantInfoNextPage" in rendered

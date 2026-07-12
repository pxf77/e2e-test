from __future__ import annotations

import asyncio
import inspect
import json
import types
from pathlib import Path

import pytest


def test_agent3_path_attempt_limit_defaults_to_self_healing_budget(monkeypatch):
    from e2e_agent.core.page_exploration import _agent3_path_attempt_limit

    monkeypatch.delenv("AGENT3_PATH_ATTEMPTS", raising=False)
    monkeypatch.delenv("AGENT3_FIRST_PATH_ONLY", raising=False)

    assert _agent3_path_attempt_limit() == 5


def test_agent3_first_path_only_does_not_disable_self_healing(monkeypatch):
    from e2e_agent.core.page_exploration import _agent3_path_attempt_limit

    monkeypatch.delenv("AGENT3_PATH_ATTEMPTS", raising=False)
    monkeypatch.setenv("AGENT3_FIRST_PATH_ONLY", "1")

    assert _agent3_path_attempt_limit() == 5


def test_agent3_path_attempt_limit_supports_until_complete_with_hard_cap(monkeypatch):
    from e2e_agent.core.page_exploration import _agent3_path_attempt_limit

    monkeypatch.setenv("AGENT3_PATH_ATTEMPTS", "until_complete")

    assert _agent3_path_attempt_limit() == 10


def test_agent3_path_attempt_limit_clamps_numeric_budget(monkeypatch):
    from e2e_agent.core.page_exploration import _agent3_path_attempt_limit

    monkeypatch.setenv("AGENT3_PATH_ATTEMPTS", "99")

    assert _agent3_path_attempt_limit() == 10


def test_agent3_trial_gene_patch_routes_include_start_insure():
    from e2e_agent.core import page_exploration as runtime

    assert "**/api/apps/cps/insure/start/insure**" in runtime._TRIAL_GENES_ROUTE_PATTERNS


def test_agent3_bank_state_patches_guard_optional_module_arrays():
    from e2e_agent.core import page_exploration as runtime

    apply_source = inspect.getsource(runtime._apply_pay_account_user_like_once)
    repair_source = inspect.getsource(runtime._repair_bank_selection_after_account_input)

    for source in (apply_source, repair_source):
        assert "const moduleHasRows" in source
        assert "if (!moduleHasRows(base.slice(0, -1))) continue;" in source
        assert "Array.isArray(rows) ? rows[0] : rows" not in source


def test_agent3_storage_patch_keys_are_product_agnostic():
    from e2e_agent.core import page_exploration as runtime

    assert "123602|126878" not in inspect.getsource(runtime)


def test_agent3_live_exploration_can_be_disabled_with_env(monkeypatch):
    from e2e_agent.agents import explore_agent

    monkeypatch.delenv("AGENT3_DISABLE_LIVE", raising=False)
    assert explore_agent._live_exploration_disabled() is False

    monkeypatch.setenv("AGENT3_DISABLE_LIVE", "1")
    assert explore_agent._live_exploration_disabled() is True


def test_agent3_treats_authentication_as_policy_result_handoff():
    from e2e_agent.core.page_exploration import _is_external_payment_handoff

    assert _is_external_payment_handoff(
        {"url": "https://example.test/m/apps/cps/authentication/detail?x=1", "body_text_excerpt": ""},
        "NODE-policy-result",
    )


def test_agent3_action_trace_artifact_records_full_path_action_chain():
    from e2e_agent.core.page_exploration import _action_trace_from_path_results

    trace = _action_trace_from_path_results(
        [
            {
                "path_id": "PATH-001",
                "path_status": "explored",
                "action_chain": [
                    {
                        "step": 1,
                        "action_key": "action.buy_now",
                        "selector": "#buy",
                        "source_url": "https://example.test/detail",
                        "target_url": "https://example.test/insure",
                    },
                    {
                        "step": 2,
                        "action_key": "action.submit",
                        "selector": ".submit",
                        "source_url": "https://example.test/insure",
                        "target_url": "https://example.test/result",
                    },
                ],
                "node_execution_trace": [
                    {"phase": "act", "selector": "#buy"},
                ],
            }
        ]
    )

    assert trace[0]["path_id"] == "PATH-001"
    assert trace[0]["action_count"] == 2
    assert [action["action_key"] for action in trace[0]["action_chain"]] == [
        "action.buy_now",
        "action.submit",
    ]
    assert trace[0]["action_chain"][1]["target_url"] == "https://example.test/result"


def test_agent3_loads_product_element_set_before_builtin(tmp_path):
    from e2e_agent.agents.agent3_explore.element_set import load_element_set_for_product

    product_dir = tmp_path / "products" / "demo-product" / "automation"
    product_dir.mkdir(parents=True)
    product_element_set = {
        "source": "product-fixture",
        "quick_lookup": {"by_node": {"NODE-product-detail": "#/page_models/product-detail"}},
        "page_models": {
            "product-detail": {
                "page_model_id": "PM-product-detail-product",
                "node_id": "NODE-product-detail",
                "fields": [],
                "actions": [],
            }
        },
    }
    (product_dir / "element-set.json").write_text(
        json.dumps(product_element_set, ensure_ascii=False),
        encoding="utf-8",
    )

    loaded = load_element_set_for_product(tmp_path, "demo-product")

    assert loaded["source"] == "product-automation"
    assert loaded["path"] == str(product_dir / "element-set.json")
    assert loaded["element_set"]["page_models"]["product-detail"]["page_model_id"] == "PM-product-detail-product"
    assert loaded["warning"] is None


def test_agent3_falls_back_to_builtin_element_set_when_product_file_missing(tmp_path):
    from e2e_agent.agents.agent3_explore.element_set import STATIC_ELEMENT_SET_PATH, load_element_set_for_product

    loaded = load_element_set_for_product(tmp_path, "missing-product")

    assert loaded["source"] == "agent3-builtin"
    assert loaded["path"] == str(STATIC_ELEMENT_SET_PATH)
    assert loaded["element_set"]["page_models"]
    assert loaded["warning"] is None


def test_agent3_merges_builtin_models_when_product_element_set_is_partial(tmp_path):
    from e2e_agent.agents.agent3_explore.element_set import load_element_set_for_product

    product_dir = tmp_path / "products" / "demo-product" / "automation"
    product_dir.mkdir(parents=True)
    (product_dir / "element-set.json").write_text(
        json.dumps(
            {
                "quick_lookup": {"by_node": {"NODE-product-only": "#/page_models/product-only"}},
                "page_models": {
                    "product-only": {
                        "page_model_id": "PM-product-only",
                        "node_id": "NODE-product-only",
                        "fields": [],
                        "actions": [],
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    loaded = load_element_set_for_product(tmp_path, "demo-product")

    assert loaded["source"] == "product-automation"
    assert "product-only" in loaded["element_set"]["page_models"]
    assert "health-notice" in loaded["element_set"]["page_models"]
    assert loaded["element_set"]["quick_lookup"]["by_node"]["NODE-product-only"] == "#/page_models/product-only"
    assert "NODE-health-notice" in loaded["element_set"]["quick_lookup"]["by_node"]


def test_agent3_loads_product_element_set_from_source_dir(tmp_path):
    from e2e_agent.agents.agent3_explore.element_set import load_element_set_for_product

    product_source_dir = tmp_path / "products" / "demo-product" / "eman"
    product_dir = product_source_dir / "automation"
    product_dir.mkdir(parents=True)
    (product_dir / "element-set.json").write_text(
        json.dumps(
            {
                "page_models": {
                    "source-only": {
                        "page_model_id": "PM-source-only",
                        "node_id": "NODE-source-only",
                        "fields": [],
                        "actions": [],
                    }
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    loaded = load_element_set_for_product(
        tmp_path,
        "demo-product",
        product_source_dir=product_source_dir,
    )

    assert loaded["source"] == "product-automation"
    assert loaded["path"] == str(product_dir / "element-set.json")
    assert loaded["element_set"]["page_models"]["source-only"]["page_model_id"] == "PM-source-only"


def test_explore_node_uses_product_element_set_before_builtin(monkeypatch, tmp_path):
    from e2e_agent.agents import explore_agent

    monkeypatch.setattr(explore_agent, "_ROOT_DIR", tmp_path)
    monkeypatch.setattr(explore_agent, "_playwright_python_available", lambda: True)
    product_dir = tmp_path / "products" / "demo-product" / "automation"
    product_dir.mkdir(parents=True)
    (product_dir / "element-set.json").write_text(
        json.dumps(
            {
                "quick_lookup": {"by_node": {"NODE-product-only": "#/page_models/product-only"}},
                "page_models": {
                    "product-only": {
                        "page_model_id": "PM-product-only",
                        "node_id": "NODE-product-only",
                        "page_key_pattern": "product-only",
                        "match_contract": {
                            "entry_signals": ["产品元素集专属页面"],
                            "url_patterns": ["/product-only"],
                        },
                        "fields": [],
                        "actions": [],
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    def fake_list_spec_tests(self, spec_path, timeout_seconds=60):
        return {
            "returncode": 0,
            "listed": 1,
            "errors": [],
            "raw_output": "Listing tests:\n  01-path-001.spec.ts:1:1 › SCN-001 PATH-001\nTotal: 1 test in 1 file",
            "stderr": "",
            "command": ["playwright", "test", str(spec_path), "--list"],
            "cwd": str(tmp_path),
        }

    monkeypatch.setattr(
        explore_agent.PlaywrightTSRunner,
        "list_spec_tests",
        fake_list_spec_tests,
        raising=False,
    )
    monkeypatch.setattr(
        explore_agent.PlaywrightTSRunner,
        "has_project_test_runtime",
        lambda _self: True,
        raising=False,
    )

    async def fail_live_explore(**_: object) -> dict:
        raise AssertionError("product element-set should avoid live exploration")

    monkeypatch.setattr(explore_agent, "run_live_exploration", fail_live_explore)

    result = asyncio.run(
        explore_agent.explore_node(
            {
                "product_id": "demo-product",
                "entry_url": "https://example.com/product-only",
                "regression_flow": {
                    "nodes": [
                        {"node_id": "NODE-start", "type": "start"},
                        {"node_id": "NODE-product-only", "type": "form", "page_name": "Product Only"},
                        {"node_id": "NODE-end", "type": "end"},
                    ]
                },
                "regression_paths": [
                    {
                        "path_id": "PATH-001",
                        "case_ids": ["CASE-001"],
                        "nodes": ["NODE-start", "NODE-product-only", "NODE-end"],
                    }
                ],
                "governance_summary": {},
            }
        )
    )

    assert result["page_registry"]["generated_by"] == "agent3.static-first"
    assert result["page_registry"]["planned_page_catalog"][0]["page_model_id"] == "PM-product-only"
    assert result["element_set"]["source"] == "product-automation"
    assert result["element_set"]["path"] == str(product_dir / "element-set.json")
    assert result["scenarios"][0]["script_status"] == "generated"
    assert not result["error"]


def test_explore_node_runs_live_explore_before_ts_gen(monkeypatch, tmp_path):
    from e2e_agent.agents import explore_agent

    monkeypatch.setattr(explore_agent, "_ROOT_DIR", tmp_path)
    monkeypatch.setattr(explore_agent, "_playwright_python_available", lambda: True)

    async def fake_live_explore(**_: object) -> dict:
        return {
            "page_registry": {
                "product_id": "demo-product",
                "entry_url": "https://example.com/pc",
                "pages": [
                    {
                        "page_key": "product-detail",
                        "url": "https://example.com/pc",
                        "title": "Product Detail",
                        "field_count": 2,
                        "action_count": 1,
                        "fields": [],
                        "actions": [],
                        "candidate_links": [],
                    }
                ],
            },
            "explore_trace": {
                "product_id": "demo-product",
                "visited_urls": ["https://example.com/pc"],
                "discovered_page_count": 1,
                "warnings": [],
            },
            "warnings": [],
        }

    monkeypatch.setattr(explore_agent, "run_live_exploration", fake_live_explore)

    result = asyncio.run(
        explore_agent.explore_node(
            {
                "product_id": "demo-product",
                "entry_url": "https://example.com/pc",
                "regression_flow": {
                    "nodes": [
                        {"node_id": "NODE-start", "type": "start"},
                        {"node_id": "NODE-product", "type": "form", "page_name": "Product Detail"},
                        {"node_id": "NODE-end", "type": "end"},
                    ]
                },
                "regression_paths": [
                    {
                        "path_id": "PATH-001",
                        "case_ids": ["CASE-001"],
                        "nodes": ["NODE-start", "NODE-product", "NODE-end"],
                        "conditions": {"plan": "standard"},
                        "page_keys": [
                            {
                                "node_id": "NODE-product",
                                "url_pattern": "https://example.com/pc",
                                "page_key": "PK-product-detail-plan-standard",
                                "state": {"plan": "standard"},
                                "allowed_state_keys": ["plan"],
                                "matched_whitelist_patterns": ["/product/*"],
                            }
                        ],
                        "priority": "P0",
                }
            ],
            "governance_summary": {
                    "paths": [
                        {
                            "path_id": "PATH-001",
                            "matched_whitelist_patterns": ["/product/*"],
                            "page_keys": [
                                {
                                    "node_id": "NODE-product",
                                    "url_pattern": "https://example.com/pc",
                                    "page_key": "PK-product-detail-plan-standard",
                                    "state": {"plan": "standard"},
                                    "allowed_state_keys": ["plan"],
                                    "matched_whitelist_patterns": ["/product/*"],
                                }
                            ],
                            "warnings": [],
                        }
                    ],
                    "summary": {"total_page_keys": 1},
                "warnings": [],
            },
            "agent3_mode": "live",
        }
    )
    )

    assert result["page_registry"]["pages"][0]["page_key"] == "product-detail"
    assert result["page_registry"]["pages"][0]["planned_page_keys"] == [
        "PK-product-detail-plan-standard"
    ]
    assert result["runtime_context"]["session_key"] == "demo-product:pc"
    assert result["artifact_fingerprints"][-1]["artifact_type"] == "assertion-results"
    assert result["artifact_fingerprints"][-1]["artifact_path"] == (
        "products/demo-product/agent3/assertion-results.json"
    )
    agent3_dir = tmp_path / "products" / "demo-product" / "agent3"
    assert (agent3_dir / "page-registry.json").exists()
    assert (agent3_dir / "explore-trace.json").exists()
    assert (agent3_dir / "page-functions.json").exists()
    assert (agent3_dir / "scenarios.json").exists()
    assert (agent3_dir / "script-plan.json").exists()
    assert (agent3_dir / "assertion-results.json").exists()
    assert (agent3_dir / "ts-gen" / "tc-execution-plan.json").exists()
    assert not (tmp_path / "products" / "demo-product" / "reg" / "script-plan").exists()
    assert result["explore_trace"]["discovered_page_count"] == 1
    assert result["page_functions"]
    assert result["scenarios"]
    assert result["assertion_results"]


def test_explore_governance_maps_pay_success_to_policy_result_without_warning():
    from e2e_agent.agents.agent3_explore.node import _annotate_page_registry_with_governance

    page_registry = {
        "pages": [
            {
                "page_key": "policy-result",
                "url": "https://cps.example.com/m/demo-channel/pay/success?fromFlag=daikou&id=ORDER!!",
            }
        ]
    }
    state = {
        "governance_summary": {
            "paths": [
                {
                    "path_id": "PATH-001",
                    "page_keys": [
                        {
                            "node_id": "NODE-policy-result",
                            "url_pattern": "/result",
                            "page_key": "PK-result",
                            "allowed_state_keys": ["orderId", "policyNo"],
                        }
                    ],
                }
            ]
        }
    }

    annotated, warnings = _annotate_page_registry_with_governance(page_registry, state)

    assert warnings == []
    assert annotated["pages"][0]["planned_page_keys"] == ["PK-result"]
    assert annotated["pages"][0]["planned_state_keys"] == ["orderId", "policyNo"]


def test_explore_result_keeps_successful_self_healing_warnings_out_of_error():
    from e2e_agent.agents.agent3_explore.node import _agent3_error_from_warnings

    error = _agent3_error_from_warnings(
        [
            "Path PATH-002 reused completed Agent3 chain from PATH-001; same entry and target page path",
            "Path PATH-004 exploration attempt 1/5 incomplete; self-healing retrying",
            "No planned page-key matched explored page: https://example.com/pay/success?id=ORDER",
        ],
        {
            "exploration_contract": {
                "is_complete": True,
                "blocked_count": 0,
            }
        },
        {
            "script_validation": {
                "status": "passed",
                "errors": [],
            }
        },
    )

    assert error is None


def test_explore_node_materialises_element_set_from_live_registry(monkeypatch, tmp_path):
    from e2e_agent.agents import explore_agent

    monkeypatch.setattr(explore_agent, "_ROOT_DIR", tmp_path)
    monkeypatch.setattr(explore_agent, "_playwright_python_available", lambda: True)

    async def fake_live_explore(**_: object) -> dict:
        return {
            "page_registry": {
                "product_id": "demo-product",
                "entry_url": "https://example.com/apps/cps/product/insure",
                "platform": "pc",
                "generated_by": "explore_agent.live",
                "pages": [],
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
                                "field_key": "moblie_10",
                                "selector": "input[name=\"moblie_10\"]",
                                "source": "dom",
                                "required": False,
                                "raw": {
                                    "name": "moblie_10",
                                    "label": "*手机号码",
                                    "tag": "input",
                                    "type": "tel",
                                    "required": True,
                                },
                            }
                        ],
                        "selector_map": {
                            "actions": [
                                {
                                    "text": "提交投保单",
                                    "selector": ".js-adapt-question-btn",
                                    "tag": "button",
                                }
                            ]
                        },
                    }
                ],
            },
            "explore_trace": {
                "product_id": "demo-product",
                "visited_urls": ["https://example.com/apps/cps/product/insure"],
                "discovered_page_count": 1,
                "warnings": [],
            },
            "warnings": [],
        }

    monkeypatch.setattr(explore_agent, "run_live_exploration", fake_live_explore)

    result = asyncio.run(
        explore_agent.explore_node(
            {
                "product_id": "demo-product",
                "entry_url": "https://example.com/apps/cps/product/insure",
                "regression_paths": [],
                "regression_flow": {},
                "governance_summary": {},
            }
        )
    )

    element_set_path = (
        tmp_path
        / "products"
        / "demo-product"
        / "automation"
        / "element-set.json"
    )
    assert element_set_path.exists()
    assert result["element_set"]["page_model_count"] == 1


def test_materialise_live_explore_outputs_uses_product_artifact_dir(tmp_path):
    from e2e_agent.core import page_exploration as runtime

    artifact_dir = tmp_path / "products" / "demo-product" / "demo.assets"

    runtime._materialise_explore_outputs(
        tmp_path,
        "demo-product",
        "https://example.com/product/detail",
        {
            "product_id": "demo-product",
            "path_exploration_results": [],
            "page_content_records": [],
            "exploration_contract": {},
        },
        {"product_id": "demo-product", "visited_urls": []},
        product_dir=artifact_dir,
    )

    assert (artifact_dir / "agent3" / "ts-gen" / "pc" / "page-registry.json").exists()
    assert (artifact_dir / "agent3" / "explore" / "explore-trace.json").exists()
    assert not (tmp_path / "products" / "demo-product" / "agent3").exists()


def test_explore_node_static_first_skips_live_exploration(monkeypatch, tmp_path):
    from e2e_agent.agents import explore_agent

    monkeypatch.setattr(explore_agent, "_ROOT_DIR", tmp_path)
    monkeypatch.setattr(explore_agent, "_playwright_python_available", lambda: True)

    def fake_list_spec_tests(self, spec_path, timeout_seconds=60):
        return {
            "returncode": 0,
            "listed": 1,
            "errors": [],
            "raw_output": "Listing tests:\n  01-path-001.spec.ts:1:1 › SCN-001 PATH-001\nTotal: 1 test in 1 file",
            "stderr": "",
            "command": ["playwright", "test", str(spec_path), "--list"],
            "cwd": str(tmp_path),
        }

    monkeypatch.setattr(
        explore_agent.PlaywrightTSRunner,
        "list_spec_tests",
        fake_list_spec_tests,
        raising=False,
    )
    monkeypatch.setattr(
        explore_agent.PlaywrightTSRunner,
        "has_project_test_runtime",
        lambda _self: True,
        raising=False,
    )

    async def fail_live_explore(**_: object) -> dict:
        raise AssertionError("static-first should not open browser exploration")

    monkeypatch.setattr(explore_agent, "run_live_exploration", fail_live_explore)

    result = asyncio.run(
        explore_agent.explore_node(
            {
                "product_id": "demo-product",
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
                        "case_ids": ["CASE-001"],
                        "nodes": [
                            "NODE-start",
                            "NODE-product-detail",
                            "NODE-insure-form",
                            "NODE-policy-result",
                            "NODE-end",
                        ],
                        "priority": "P0",
                    }
                ],
                "governance_summary": {},
            }
        )
    )

    assert result["page_registry"]["generated_by"] == "agent3.static-first"
    assert result["explore_trace"]["mode"] == "static-first"
    assert result["scenarios"]
    assert result["scenarios"][0]["coverage_status"] == "covered"
    assert result["scenarios"][0]["contract_status"] == "compiled"
    assert result["scenarios"][0]["script_status"] == "generated"
    assert result["scenarios"][0]["script_validation_status"] == "passed"
    assert result["scenarios"][0]["runtime_status"] == "not_executed"
    assert result["scenarios"][0]["page_element_plan"]
    assert result["script_bundle"]["status"] == "generated"
    assert result["script_validation"]["status"] == "passed"
    assert result["script_validation"]["listed_test_count"] == 1
    assert result["element_set"]["source"] == "agent3-builtin"
    assert not result["error"]


def test_validate_script_bundle_skips_when_project_node_runtime_is_missing(tmp_path):
    from e2e_agent.core.script_generation import validate_script_bundle

    spec_path = tmp_path / "products" / "demo" / "agent3" / "ts-gen" / "pc" / "scenarios" / "01-path.spec.ts"
    spec_path.parent.mkdir(parents=True)
    spec_path.write_text("import { test } from 'playwright/test';\ntest('demo', async () => {});\n", encoding="utf-8")

    class GlobalOnlyRunner:
        def has_project_test_runtime(self) -> bool:
            return False

        def list_spec_tests(self, *_args, **_kwargs):
            raise AssertionError("script discovery should not run without project Node runtime")

    result = validate_script_bundle(
        {
            "spec_files": [
                {
                    "contract_status": "compiled",
                    "absolute_path": str(spec_path),
                    "exists": True,
                }
            ]
        },
        GlobalOnlyRunner(),
    )

    assert result["status"] == "skipped"
    assert result["errors"] == ["Project Node Playwright runtime is not installed"]


def test_explore_node_static_first_keeps_covered_paths_when_other_paths_miss_models(monkeypatch, tmp_path):
    from e2e_agent.agents import explore_agent

    monkeypatch.setattr(explore_agent, "_ROOT_DIR", tmp_path)
    monkeypatch.setattr(explore_agent, "_playwright_python_available", lambda: True)

    def fake_list_spec_tests(self, spec_path, timeout_seconds=60):
        return {
            "returncode": 0,
            "listed": 1,
            "errors": [],
            "raw_output": "Listing tests:\n  01-path-001.spec.ts:1:1 › SCN-001 PATH-001\nTotal: 1 test in 1 file",
            "stderr": "",
            "command": ["playwright", "test", str(spec_path), "--list"],
            "cwd": str(tmp_path),
        }

    monkeypatch.setattr(
        explore_agent.PlaywrightTSRunner,
        "list_spec_tests",
        fake_list_spec_tests,
        raising=False,
    )
    monkeypatch.setattr(
        explore_agent.PlaywrightTSRunner,
        "has_project_test_runtime",
        lambda _self: True,
        raising=False,
    )

    async def fail_live_explore(**_: object) -> dict:
        raise AssertionError("static-first should not discard covered paths and open live exploration")

    monkeypatch.setattr(explore_agent, "run_live_exploration", fail_live_explore)

    result = asyncio.run(
        explore_agent.explore_node(
            {
                "product_id": "demo-product",
                "entry_url": "https://example.com/product/detail",
                "regression_flow": {
                    "nodes": [
                        {"node_id": "NODE-start", "type": "start"},
                        {"node_id": "NODE-product-detail", "type": "form", "page_name": "Product Detail"},
                        {"node_id": "NODE-insure-form", "type": "form", "page_name": "Insure Form"},
                        {"node_id": "NODE-policy-result", "type": "result", "page_name": "Policy Result"},
                        {"node_id": "NODE-policy-archive", "type": "form", "page_name": "Policy Archive"},
                        {"node_id": "NODE-end", "type": "end"},
                    ]
                },
                "regression_paths": [
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
                        "priority": "P0",
                    },
                    {
                        "path_id": "PATH-002",
                        "case_ids": ["CASE-002"],
                        "nodes": [
                            "NODE-start",
                            "NODE-policy-archive",
                            "NODE-policy-result",
                            "NODE-end",
                        ],
                        "priority": "P1",
                    },
                ],
                "governance_summary": {},
            }
        )
    )

    scenarios = {item["path_id"]: item for item in result["scenarios"]}
    assert result["page_registry"]["generated_by"] == "agent3.static-first"
    assert scenarios["PATH-001"]["coverage_status"] == "covered"
    assert scenarios["PATH-001"]["script_status"] == "generated"
    assert scenarios["PATH-002"]["coverage_status"] == "coverage-gap"
    assert scenarios["PATH-002"]["script_status"] == "blocked"
    assert result["script_validation"]["listed_test_count"] == 1


def test_explore_node_static_first_keeps_targeted_probe_contract_without_full_live_probe(monkeypatch, tmp_path):
    from e2e_agent.agents import explore_agent

    monkeypatch.setattr(explore_agent, "_ROOT_DIR", tmp_path)
    monkeypatch.setattr(explore_agent, "_playwright_python_available", lambda: True)
    monkeypatch.setattr(
        explore_agent,
        "validate_script_bundle",
        lambda *_args, **_kwargs: {
            "status": "passed",
            "checked_by": "test",
            "listed_test_count": 0,
            "errors": [],
        },
    )
    monkeypatch.setattr(
        explore_agent,
        "load_element_set_for_product",
        lambda *_args, **_kwargs: {
            "source": "agent3-builtin",
            "path": "test-element-set.json",
            "warning": None,
            "element_set": {
                "quick_lookup": {"by_node": {"NODE-insure-form": "#/page_models/insure-form"}},
                "page_models": {
                    "insure-form": {
                    "page_model_id": "PM-insure-form",
                    "node_id": "NODE-insure-form",
                    "page_key_pattern": "insure-form",
                    "match_contract": {
                        "entry_signals": ["投保人信息"],
                        "url_patterns": ["/insure"],
                    },
                    "fields": [
                        {
                            "field_key": "applicant.name",
                            "label": "投保人姓名",
                            "locators": [{"by": "label_text", "value": "投保人姓名"}],
                            "required": True,
                            "value_type": "string",
                            "mock_strategy": "person_name",
                        }
                    ],
                        "actions": [],
                    }
                },
            },
        },
    )

    async def fail_live_explore(**_: object) -> dict:
        raise AssertionError("targeted probe plan should not trigger full live exploration")

    monkeypatch.setattr(explore_agent, "run_live_exploration", fail_live_explore)

    result = asyncio.run(
        explore_agent.explore_node(
            {
                "product_id": "demo-product",
                "entry_url": "https://example.com/product/detail",
                "regression_flow": {
                    "nodes": [
                        {"node_id": "NODE-start", "type": "start"},
                        {"node_id": "NODE-insure-form", "type": "form", "page_name": "Insure Form"},
                        {"node_id": "NODE-end", "type": "end"},
                    ]
                },
                "regression_paths": [
                    {
                        "path_id": "PATH-001",
                        "case_ids": ["CASE-001"],
                        "nodes": ["NODE-start", "NODE-insure-form", "NODE-end"],
                    }
                ],
                "governance_summary": {},
            }
        )
    )

    assert result["page_registry"]["generated_by"] == "agent3.static-first"
    assert result["page_registry"]["targeted_probe_plan"]["summary"]["request_count"] == 1
    assert result["page_registry"]["static_contract"]["requires_targeted_probe"] is True
    assert result["scenarios"][0]["script_status"] == "blocked"
    assert result["scenarios"][0]["targeted_probe_plan"]["summary"]["request_count"] == 1
    targeted_probe_plan_path = (
        tmp_path / "products" / "demo-product" / "agent3" / "explore" / "targeted-probe-plan.json"
    )
    assert targeted_probe_plan_path.exists()
    targeted_probe_plan = json.loads(targeted_probe_plan_path.read_text(encoding="utf-8"))
    assert targeted_probe_plan["summary"]["request_count"] == 1


def test_explore_node_preserves_static_contract_for_long_path_without_live_fallback(monkeypatch, tmp_path):
    from e2e_agent.agents import explore_agent

    monkeypatch.setattr(explore_agent, "_ROOT_DIR", tmp_path)
    monkeypatch.setattr(explore_agent, "_playwright_python_available", lambda: True)

    async def fail_live_explore(**_: object) -> dict:
        raise AssertionError("matched static contracts should not fall back to live exploration")

    monkeypatch.setattr(explore_agent, "run_live_exploration", fail_live_explore)

    result = asyncio.run(
        explore_agent.explore_node(
            {
                "product_id": "demo-product",
                "entry_url": "https://example.com/product/detail",
                "regression_flow": {
                    "nodes": [
                        {"node_id": "NODE-start", "type": "start"},
                        {"node_id": "NODE-product-detail", "type": "form", "page_name": "Product Detail"},
                        {"node_id": "NODE-premium-calculation", "type": "form", "page_name": "Premium"},
                        {"node_id": "NODE-health-notice", "type": "form", "page_name": "Health Notice"},
                        {"node_id": "NODE-insure-form", "type": "form", "page_name": "Insure Form"},
                        {"node_id": "NODE-underwriting-callback", "type": "form", "page_name": "Underwriting"},
                        {"node_id": "NODE-risk-control", "type": "form", "page_name": "Risk Control"},
                        {"node_id": "NODE-payment", "type": "form", "page_name": "Payment"},
                        {"node_id": "NODE-policy-result", "type": "result", "page_name": "Policy Result"},
                        {"node_id": "NODE-end", "type": "end"},
                    ]
                },
                "regression_paths": [
                    {
                        "path_id": "PATH-001",
                        "case_ids": ["CASE-001"],
                        "nodes": [
                            "NODE-start",
                            "NODE-product-detail",
                            "NODE-premium-calculation",
                            "NODE-health-notice",
                            "NODE-insure-form",
                            "NODE-underwriting-callback",
                            "NODE-risk-control",
                            "NODE-payment",
                            "NODE-policy-result",
                            "NODE-end",
                        ],
                    }
                ],
                "governance_summary": {},
            }
        )
    )

    path = result["page_registry"]["path_exploration_results"][0]
    assert result["page_registry"]["generated_by"] == "agent3.static-first"
    assert path["node_progress"][0]["status"] == "matched"
    assert path["path_status"] == "explored"
    assert path["blocked_reason"] is None
    assert any(
        action.get("click_strategy") == "auto_wait_for_next_node"
        for action in path["action_chain"]
    )
    assert "Live browser exploration failed" not in str(result.get("error") or "")


def test_static_contract_uses_submit_after_suitability_questionnaire():
    from e2e_agent.agents.agent3_explore.element_set import load_static_element_set
    from e2e_agent.agents.agent3_explore.static_contract import build_static_explore_artifacts

    artifacts = build_static_explore_artifacts(
        product_id="test-product",
        entry_url="https://commerce.example.test/apps/cps/demo-channel/product/detail?prodId=123626&planId=126944",
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

    path = artifacts["page_registry"]["path_exploration_results"][0]
    action_chain = path["action_chain"]

    assert path["path_status"] == "explored"
    assert [action["action_key"] for action in action_chain[:5]] == [
        "action.buy_now",
        "action.agree_all",
        "action.answer_questionnaire",
        "action.submit",
        "action.buy_now",
    ]
    assert action_chain[2]["text"] == "business_questionnaire_rule"
    assert action_chain[2]["answer_strategy"] == "business_questionnaire_rule"
    assert action_chain[3]["text"] == "Submit"
    assert action_chain[3]["selector"] == "button.js-adapt-question-btn"
    assert action_chain[4]["text"] == "Continue Application"


def test_static_contract_clicks_premium_agree_button_before_questionnaire_answer():
    from e2e_agent.agents.agent3_explore.element_set import load_static_element_set
    from e2e_agent.agents.agent3_explore.static_contract import build_static_explore_artifacts

    artifacts = build_static_explore_artifacts(
        product_id="test-product",
        entry_url="https://commerce.example.test/apps/cps/demo-channel/product/detail?prodId=123626&planId=126944",
        regression_paths=[
            {
                "path_id": "PATH-001",
                "case_ids": ["CASE-001"],
                "nodes": [
                    "NODE-start",
                    "NODE-product-detail",
                    "NODE-premium-calculation",
                    "NODE-suitability",
                    "NODE-health-notice",
                    "NODE-end",
                ],
            }
        ],
        element_set=load_static_element_set(),
    )

    action_chain = artifacts["page_registry"]["path_exploration_results"][0]["action_chain"]
    compact = [
        (
            action["planned_from_node_id"],
            action["action_key"],
            action.get("text"),
        )
        for action in action_chain
        if action["planned_from_node_id"] in {"NODE-premium-calculation", "NODE-suitability"}
    ]

    assert compact[:3] == [
        ("NODE-premium-calculation", "action.agree_all", "Agree and Continue"),
        ("NODE-suitability", "action.answer_questionnaire", "business_questionnaire_rule"),
        ("NODE-suitability", "action.submit", "Submit"),
    ]
    assert not any(
        action["planned_from_node_id"] == "NODE-premium-calculation"
        and action.get("text") == "Confirm Application Flow"
        for action in action_chain
    )


def test_static_contract_generates_auto_wait_for_underwriting_callback_transition():
    from e2e_agent.agents.agent3_explore.element_set import load_static_element_set
    from e2e_agent.agents.agent3_explore.static_contract import build_static_explore_artifacts

    artifacts = build_static_explore_artifacts(
        product_id="test-product",
        entry_url="https://commerce.example.test/apps/cps/demo-channel/product/detail?prodId=123626&planId=126944",
        regression_paths=[
            {
                "path_id": "PATH-001",
                "case_ids": ["CASE-001"],
                "nodes": [
                    "NODE-start",
                    "NODE-product-detail",
                    "NODE-premium-calculation",
                    "NODE-health-notice",
                    "NODE-insure-form",
                    "NODE-underwriting-callback",
                    "NODE-risk-control",
                    "NODE-payment",
                    "NODE-policy-result",
                    "NODE-end",
                ],
            }
        ],
        element_set=load_static_element_set(),
    )

    path = artifacts["page_registry"]["path_exploration_results"][0]
    auto_wait_actions = [
        action
        for action in path["action_chain"]
        if action.get("click_strategy") == "auto_wait_for_next_node"
    ]

    assert auto_wait_actions
    assert auto_wait_actions[0]["action_key"] == "action.auto_wait_for_next_node"
    assert auto_wait_actions[0]["planned_from_node_id"] == "NODE-underwriting-callback"
    assert auto_wait_actions[0]["planned_to_node_id"] == "NODE-risk-control"
    assert not any(
        item["node_id"] == "NODE-underwriting-callback"
        for item in artifacts["page_registry"]["static_contract"]["missing_transition_actions"]
    )
    assert path["path_status"] == "explored"


def test_static_contract_marks_max_path_optional_nodes_and_answers_health_notice():
    from e2e_agent.agents.agent3_explore.element_set import load_static_element_set
    from e2e_agent.agents.agent3_explore.static_contract import build_static_explore_artifacts

    artifacts = build_static_explore_artifacts(
        product_id="test-product",
        entry_url="https://commerce.example.test/apps/cps/demo-channel/product/detail?prodId=123626&planId=126944",
        regression_paths=[
            {
                "path_id": "PATH-001",
                "case_ids": ["CASE-001"],
                "nodes": [
                    "NODE-start",
                    "NODE-product-detail",
                    "NODE-premium-calculation",
                    "NODE-suitability",
                    "NODE-health-notice",
                    "NODE-insure-form",
                    "NODE-underwriting",
                    "NODE-risk-control",
                    "NODE-payment",
                    "NODE-policy-result",
                    "NODE-end",
                ],
                "optional_nodes": [
                    "NODE-premium-calculation",
                    "NODE-suitability",
                    "NODE-health-notice",
                    "NODE-underwriting",
                    "NODE-risk-control",
                    "NODE-payment",
                ],
                "execution_policy": {"skip_absent_optional_nodes": True},
            }
        ],
        element_set=load_static_element_set(),
    )

    path = artifacts["page_registry"]["path_exploration_results"][0]
    action_chain = path["action_chain"]

    assert path["path_status"] == "explored"
    assert "NODE-underwriting" in path["completion_rule"]["optional_nodes"]
    assert any(action["action_key"] == "action.answer_health_notice" for action in action_chain)
    assert any(
        action["planned_from_node_id"] == "NODE-health-notice"
        and action["text"] == "确认无以上问题"
        and action["skip_if_absent"] is True
        for action in action_chain
    )
    assert any(
        action["planned_from_node_id"] == "NODE-payment"
        and action["skip_if_absent"] is True
        for action in action_chain
    )
    assert any(
        action["planned_from_node_id"] in {"NODE-underwriting", "NODE-underwriting-callback"}
        and action["action_key"] == "action.auto_wait_for_next_node"
        and action["skip_if_absent"] is True
        for action in action_chain
    )


def test_static_contract_outputs_agent4_readiness_artifacts():
    from e2e_agent.agents.agent3_explore.static_contract import build_static_explore_artifacts

    element_set = {
        "quick_lookup": {
            "by_node": {
                "NODE-product-detail": "#/page_models/product-detail",
                "NODE-insure-form": "#/page_models/insure-form",
                "NODE-policy-result": "#/page_models/policy-result",
            }
        },
        "page_models": {
            "product-detail": {
                "page_model_id": "PM-product-detail",
                "node_id": "NODE-product-detail",
                "page_key_pattern": "product-detail",
                "match_contract": {
                    "entry_signals": ["产品详情"],
                    "url_patterns": ["/product/detail"],
                },
                "fields": [],
                "actions": [
                    {
                        "action_key": "action.buy_now",
                        "text": "立即投保",
                        "locators": [{"by": "selector", "value": "#buy-now"}],
                        "required": True,
                    }
                ],
            },
            "insure-form": {
                "page_model_id": "PM-insure-form",
                "node_id": "NODE-insure-form",
                "page_key_pattern": "insure-form",
                "match_contract": {
                    "entry_signals": ["投保人信息"],
                    "url_patterns": ["/insure"],
                },
                "fields": [
                    {
                        "field_key": "applicant.name",
                        "label": "投保人姓名",
                        "locators": [{"by": "selector", "value": "#applicant-name"}],
                        "required": True,
                        "value_type": "string",
                        "mock_strategy": "person_name",
                    },
                    {
                        "field_key": "applicant.relation",
                        "label": "关系",
                        "locators": [{"by": "selector", "value": "#relation"}],
                        "required": True,
                        "value_type": "enum",
                        "mock_strategy": "default_first_option",
                    },
                    {
                        "field_key": "insured.birthdate",
                        "label": "出生日期",
                        "locators": [{"by": "selector", "value": "#birthdate"}],
                        "required": True,
                        "value_type": "date",
                        "mock_strategy": "birthdate",
                    },
                    {
                        "field_key": "applicant.occupation",
                        "label": "职业",
                        "locators": [{"by": "selector", "value": "#occupation"}],
                        "required": True,
                        "value_type": "string",
                        "mock_strategy": "occupation_keyword",
                    },
                    {
                        "field_key": "applicant.region",
                        "label": "所在地区",
                        "locators": [{"by": "selector", "value": "#region"}],
                        "required": True,
                        "value_type": "string",
                        "mock_strategy": "region_cn",
                    },
                    {
                        "field_key": "agreement.confirm",
                        "label": "本人已阅读并同意",
                        "locators": [{"by": "selector", "value": "#agreement"}],
                        "required": True,
                        "value_type": "boolean",
                        "mock_strategy": "true",
                    },
                ],
                "actions": [
                    {
                        "action_key": "action.submit",
                        "text": "提交订单",
                        "locators": [{"by": "selector", "value": "#submit-order"}],
                        "required": True,
                    }
                ],
            },
            "policy-result": {
                "page_model_id": "PM-policy-result",
                "node_id": "NODE-policy-result",
                "page_key_pattern": "policy-result",
                "match_contract": {"entry_signals": ["投保成功"]},
                "fields": [],
                "actions": [],
            },
        },
    }

    artifacts = build_static_explore_artifacts(
        product_id="test-product",
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
        element_set=element_set,
    )

    registry = artifacts["page_registry"]
    path = registry["path_exploration_results"][0]
    validation_report = path["validation_report"]

    assert registry["static_contract"]["agent4_ready"] is True
    assert registry["field_resolution_plan"]["summary"]["required_field_count"] >= 6
    assert registry["field_resolution_plan"]["summary"]["verified_required_field_count"] >= 6
    assert registry["component_strategy"]["summary"]["unsupported_required_component_count"] == 0
    assert validation_report["status"] == "passed"
    assert validation_report["agent4_ready"] is True
    assert {gate["gate"] for gate in validation_report["gates"]} == {
        "page_recognition",
        "required_field_location",
        "action_clickability",
        "component_strategy_coverage",
        "mock_data_mapping",
        "transition_reachability",
    }
    assert all(gate["status"] == "passed" for gate in validation_report["gates"])

    strategies = {
        item["field_key"]: item["control_type"]
        for item in registry["component_strategy"]["field_strategies"]
        if item["node_id"] == "NODE-insure-form"
    }
    assert strategies["applicant.name"] == "input_text"
    assert strategies["applicant.relation"] == "select"
    assert strategies["insured.birthdate"] == "date_picker"
    assert strategies["applicant.occupation"] == "occupation_picker"
    assert strategies["applicant.region"] == "region_picker"
    assert strategies["agreement.confirm"] == "agreement_checkbox"

    transition_proofs = validation_report["proofs"]["transition_reachability"]
    assert {
        (item["planned_from_node_id"], item["planned_to_node_id"], item["status"])
        for item in transition_proofs
    } == {
        ("NODE-product-detail", "NODE-insure-form", "passed"),
        ("NODE-insure-form", "NODE-policy-result", "passed"),
    }


def test_static_contract_outputs_targeted_probe_plan_for_unverified_required_fields():
    from e2e_agent.agents.agent3_explore.static_contract import build_static_explore_artifacts

    element_set = {
        "quick_lookup": {"by_node": {"NODE-insure-form": "#/page_models/insure-form"}},
        "page_models": {
            "insure-form": {
                "page_model_id": "PM-insure-form",
                "node_id": "NODE-insure-form",
                "page_key_pattern": "insure-form",
                "match_contract": {
                    "entry_signals": ["投保人信息"],
                    "url_patterns": ["/insure"],
                },
                "fields": [
                    {
                        "field_key": "applicant.name",
                        "label": "投保人姓名",
                        "locators": [
                            {"by": "label_text", "value": "投保人姓名"},
                            {"by": "param", "value": "applicantName"},
                        ],
                        "required": True,
                        "value_type": "string",
                        "mock_strategy": "person_name",
                    }
                ],
                "actions": [],
            }
        },
    }

    artifacts = build_static_explore_artifacts(
        product_id="test-product",
        entry_url="https://example.com/product/detail",
        regression_paths=[
            {
                "path_id": "PATH-001",
                "case_ids": ["CASE-001"],
                "nodes": ["NODE-start", "NODE-insure-form", "NODE-end"],
            }
        ],
        element_set=element_set,
    )

    registry = artifacts["page_registry"]
    path = registry["path_exploration_results"][0]
    path_probe_plan = path["targeted_probe_plan"]
    registry_probe_plan = registry["targeted_probe_plan"]

    assert path["path_status"] == "blocked"
    assert path["blocked_reason"] == "Agent3 validation failed"
    assert path["validation_report"]["agent4_ready"] is False
    assert registry["static_contract"]["requires_targeted_probe"] is True
    assert registry["static_contract"]["targeted_probe_request_count"] == 1
    assert registry["selector_override_priority"][0]["source"] == "targeted_probe_verified"
    assert path_probe_plan["status"] == "required"
    assert registry_probe_plan["summary"]["request_count"] == 1
    missing_report = registry["static_contract"]["missing_report"]
    assert missing_report["summary"]["required_field_count"] == 1
    assert missing_report["required_fields"][0]["owner"] == "ai-testing"
    assert missing_report["required_fields"][0]["probe_scope"] == "current_node_only"
    assert missing_report["required_fields"][0]["probe_strategy"] == "resolve_field_by_label_context"

    request = path_probe_plan["requests"][0]
    assert request["kind"] == "field"
    assert request["path_id"] == "PATH-001"
    assert request["node_id"] == "NODE-insure-form"
    assert request["field_key"] == "applicant.name"
    assert request["reason"] == "required_field_needs_verified_locator"
    assert request["probe_scope"] == "current_node_only"
    assert request["probe_strategy"] == "resolve_field_by_label_context"
    assert request["control_type"] == "input_text"
    assert request["fill_strategy"] == "fill_text"
    assert request["mock_key"] == "applicant.name"
    assert request["candidate_locators"] == [
        {"by": "label_text", "value": "投保人姓名"},
        {"by": "param", "value": "applicantName"},
    ]
    assert "locator_status=verified_static" in request["acceptance_criteria"]
    assert "mock_status=mapped" in request["acceptance_criteria"]


def test_static_contract_outputs_targeted_probe_plan_for_missing_page_models():
    from e2e_agent.agents.agent3_explore.static_contract import build_static_explore_artifacts

    element_set = {
        "quick_lookup": {
            "by_node": {
                "NODE-product-detail": "#/page_models/product-detail",
                "NODE-policy-result": "#/page_models/policy-result",
            }
        },
        "page_models": {
            "product-detail": {
                "page_model_id": "PM-product-detail",
                "node_id": "NODE-product-detail",
                "page_key_pattern": "product-detail",
                "match_contract": {"entry_signals": ["产品详情"], "url_patterns": ["/product/detail"]},
                "fields": [],
                "actions": [],
            },
            "policy-result": {
                "page_model_id": "PM-policy-result",
                "node_id": "NODE-policy-result",
                "page_key_pattern": "policy-result",
                "match_contract": {"entry_signals": ["出单结果"], "url_patterns": ["/result"]},
                "fields": [],
                "actions": [],
            },
        },
    }

    artifacts = build_static_explore_artifacts(
        product_id="test-product",
        entry_url="https://example.com/product/detail",
        regression_paths=[
            {
                "path_id": "PATH-002",
                "case_ids": ["CASE-002"],
                "nodes": [
                    "NODE-start",
                    "NODE-product-detail",
                    "NODE-tax-info",
                    "NODE-policy-result",
                    "NODE-end",
                ],
                "page_keys": [
                    {
                        "node_id": "NODE-tax-info",
                        "url_pattern": "/insure/tax",
                        "page_key": "PK-insure-tax",
                    }
                ],
            }
        ],
        element_set=element_set,
    )

    registry = artifacts["page_registry"]
    path = registry["path_exploration_results"][0]
    path_probe_plan = path["targeted_probe_plan"]

    assert path["path_status"] == "blocked"
    assert path["blocked_reason"] == "Missing static page model"
    assert registry["static_contract"]["requires_targeted_probe"] is True
    assert registry["static_contract"]["targeted_probe_request_count"] == 2
    assert path_probe_plan["status"] == "required"
    assert registry["targeted_probe_plan"]["summary"]["request_count"] == 2
    missing_report = registry["static_contract"]["missing_report"]
    assert missing_report["summary"]["page_model_count"] == 1
    assert missing_report["page_models"][0]["owner"] == "ai-fullstack"
    assert missing_report["page_models"][0]["probe_scope"] == "current_or_reachable_node"
    assert missing_report["page_models"][0]["probe_strategy"] == "discover_page_model_by_agent2_node"

    request = next(item for item in path_probe_plan["requests"] if item["kind"] == "page_model")
    assert request["kind"] == "page_model"
    assert request["path_id"] == "PATH-002"
    assert request["node_id"] == "NODE-tax-info"
    assert request["reason"] == "missing_page_model"
    assert request["probe_scope"] == "current_or_reachable_node"
    assert request["probe_strategy"] == "discover_page_model_by_agent2_node"
    assert request["url_patterns"] == ["/insure/tax"]
    assert request["page_key"] == "PK-insure-tax"


def test_static_contract_attaches_knowledge_evidence_to_missing_page_model():
    from e2e_agent.agents.agent3_explore.static_contract import build_static_explore_artifacts

    artifacts = build_static_explore_artifacts(
        product_id="demo-product",
        entry_url="https://example.com/product/detail",
        regression_paths=[
            {
                "path_id": "PATH-001",
                "case_ids": ["CASE-001"],
                "nodes": ["NODE-start", "NODE-payment", "NODE-end"],
                "page_keys": [
                    {
                        "node_id": "NODE-payment",
                        "page_key": "PK-payment",
                        "url_pattern": "/payment",
                    }
                ],
            }
        ],
        element_set={"quick_lookup": {"by_node": {}}, "page_models": {}},
        knowledge_hints={
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
                    "actual_url": "https://example.test/payment",
                    "title": "Payment",
                    "source": "mcp-page-snapshot",
                    "evidence_status": "observed",
                    "confidence": 0.8,
                    "observed_actions": [
                        {
                            "action_key": "action.pay",
                            "text": "Pay now",
                            "locators": [{"by": "selector", "value": "#pay"}],
                            "source": "mcp-page-snapshot",
                            "evidence_status": "observed",
                        }
                    ],
                }
            ],
            "field_hints": [],
            "warnings": [],
        },
    )

    registry = artifacts["page_registry"]
    request = registry["targeted_probe_plan"]["requests"][0]

    assert request["node_id"] == "NODE-payment"
    assert request["knowledge_evidence"]["page_hints"][0]["actual_url"] == "https://example.test/payment"
    assert request["knowledge_evidence"]["usage_policy"] == "hint_only_requires_targeted_probe_verification"
    assert registry["static_contract"]["knowledge_assist"]["available"] is True
    assert registry["path_exploration_results"][0]["path_status"] == "blocked"


def test_static_contract_outputs_targeted_probe_plan_for_non_clickable_required_actions():
    from e2e_agent.agents.agent3_explore.static_contract import build_static_explore_artifacts

    element_set = {
        "quick_lookup": {
            "by_node": {
                "NODE-manual-step": "#/page_models/manual-step",
                "NODE-result": "#/page_models/result",
            }
        },
        "page_models": {
            "manual-step": {
                "page_model_id": "PM-manual-step",
                "node_id": "NODE-manual-step",
                "page_key_pattern": "manual-step",
                "match_contract": {
                    "entry_signals": ["人工确认页"],
                    "url_patterns": ["/manual"],
                },
                "fields": [],
                "actions": [
                    {
                        "action_key": "action.filllegacy",
                        "text": "fillLegacy",
                        "locators": [{"by": "function", "value": "fillLegacy"}],
                        "required": True,
                    }
                ],
            },
            "result": {
                "page_model_id": "PM-result",
                "node_id": "NODE-result",
                "page_key_pattern": "result",
                "match_contract": {
                    "entry_signals": ["结果页"],
                    "url_patterns": ["/result"],
                },
                "fields": [],
                "actions": [],
            },
        },
    }

    artifacts = build_static_explore_artifacts(
        product_id="test-product",
        entry_url="https://example.com/manual",
        regression_paths=[
            {
                "path_id": "PATH-001",
                "case_ids": ["CASE-001"],
                "nodes": ["NODE-start", "NODE-manual-step", "NODE-result", "NODE-end"],
            }
        ],
        element_set=element_set,
    )

    path = artifacts["page_registry"]["path_exploration_results"][0]
    request = path["targeted_probe_plan"]["requests"][0]
    missing_report = artifacts["page_registry"]["missing_report"]

    assert path["path_status"] == "blocked"
    assert path["blocked_reason"] == "Missing executable transition action"
    assert missing_report["summary"]["required_action_count"] == 1
    assert missing_report["required_actions"][0]["owner"] == "ai-testing"
    assert missing_report["required_actions"][0]["probe_scope"] == "current_node_only"
    assert missing_report["required_actions"][0]["probe_strategy"] == "resolve_action_by_text_role_or_selector"
    assert request["kind"] == "action"
    assert request["node_id"] == "NODE-manual-step"
    assert request["action_key"] == "action.filllegacy"
    assert request["reason"] == "required_action_needs_clickable_locator"
    assert request["probe_scope"] == "current_node_only"
    assert request["probe_strategy"] == "resolve_action_by_text_role_or_selector"


def test_static_contract_promotes_builtin_insure_form_core_fields_for_agent4():
    from e2e_agent.agents.agent3_explore.element_set import load_static_element_set
    from e2e_agent.agents.agent3_explore.static_contract import build_static_explore_artifacts

    artifacts = build_static_explore_artifacts(
        product_id="test-product",
        entry_url="https://example.com/product/detail",
        regression_paths=[
            {
                "path_id": "PATH-INSURE-FORM",
                "case_ids": ["CASE-001"],
                "nodes": ["NODE-start", "NODE-insure-form", "NODE-end"],
            }
        ],
        element_set=load_static_element_set(),
    )

    path = artifacts["page_registry"]["path_exploration_results"][0]
    fields = [
        item
        for item in path["field_resolution_plan"]["fields"]
        if item["node_id"] == "NODE-insure-form" and item["required"]
    ]
    required_keys = {item["field_key"] for item in fields}

    assert path["path_status"] == "explored"
    assert path["validation_report"]["agent4_ready"] is True
    assert path["field_resolution_plan"]["summary"]["required_field_count"] >= 12
    assert path["field_resolution_plan"]["summary"]["verified_required_field_count"] >= 12
    assert {
        "insure_form.applicantname",
        "insure_form.applicantidno",
        "insure_form.applicantphone",
        "insure_form.insuredname",
        "insure_form.insuredidno",
        "insure_form.insuredphone",
        "applicant.email",
        "applicant.address",
        "applicant.annual_income",
        "applicant.occupation",
        "applicant.region",
        "policy.start_date",
        "agreement.confirm",
    }.issubset(required_keys)
    assert all(item["selected_locator"].get("by") == "selector" for item in fields)


def test_url_pattern_matches_route_fragment_inside_absolute_url():
    from e2e_agent.agents.agent3_explore.node import _url_pattern_matches

    assert _url_pattern_matches(
        "https://commerce.example.test/apps/cps/demo-channel/product/detail?prodId=123626&planId=126944",
        "/product/detail",
    )


def test_explore_runtime_detects_choice_pages_and_forward_actions():
    from e2e_agent.core import page_exploration as runtime

    assert runtime._looks_like_choice_page(
        {
            "body_text_excerpt": "健康告知 请确认无以上问题",
            "actions": [],
        }
    )
    assert runtime._is_forward_action({"text": "下一步"})
    assert not runtime._is_forward_action({"text": "A.我希望购买保障型产品"})


def test_explore_runtime_uses_safe_transit_action_before_final_node():
    from e2e_agent.core import page_exploration as runtime

    action = runtime._best_transit_action(
        {
            "url": "https://example.com/intermediate",
            "actions": [
                {"index": 0, "text": "产品详情", "visible": True, "selector": "#detail"},
                {"index": 1, "text": "下一步", "visible": True, "selector": "#next"},
            ],
        },
        "NODE-unknown-next",
        set(),
    )

    assert action is not None
    assert action["selector"] == "#next"


def test_explore_runtime_treats_submit_as_insure_form_forward_action():
    from e2e_agent.core import page_exploration as runtime

    action = runtime._best_action_for_node(
        {
            "url": "https://example.com/product/to-insure",
            "actions": [
                {"index": 0, "text": "产品详情", "visible": True, "selector": "#detail"},
                {"index": 1, "text": "提交", "visible": True, "selector": ".js-adapt-question-btn"},
            ],
        },
        "NODE-insure-form",
        set(),
    )

    assert action is not None
    assert action["selector"] == ".js-adapt-question-btn"


def test_explore_runtime_treats_policy_result_submit_as_forward_action():
    from e2e_agent.core import page_exploration as runtime

    action = runtime._best_action_for_node(
        {
            "url": "https://example.com/product/insure",
            "actions": [
                {"index": 0, "text": "《投保声明》", "visible": True, "selector": "#doc"},
                {"index": 1, "text": "提交投保单", "visible": True, "selector": "#submit"},
            ],
        },
        "NODE-policy-result",
        set(),
    )

    assert action is not None
    assert action["selector"] == "#submit"


def test_explore_runtime_minimal_form_values_are_valid_for_insure_form():
    from e2e_agent.core import page_exploration as runtime

    assert runtime._minimal_form_value("yearlyIncome_10", "*年收入（万元）") == "20"
    assert runtime._minimal_form_value("height_10", "*身高 厘米") == "170"
    assert runtime._minimal_form_value("weight_20_default_1", "*体重 千克") == "60"
    phone = runtime._minimal_form_value("moblie_10", "*手机号码")
    assert len(phone) == 11
    assert phone[:3] in runtime._POLICY_INFO_MOBILE_PREFIXES
    assert runtime._minimal_form_value("cardNumber_10", "*证件号码") == "110101199001011237"
    assert runtime._minimal_form_value("cardNumber_20_default_1", "*证件号码") == "11010120150315123X"
    assert runtime._minimal_form_value("birthdate_10", "") == "1990-01-01"
    assert runtime._minimal_form_value("birthdate_20_default_1", "") == "2015-03-15"
    assert runtime._minimal_form_value("verifyCode", "请输入验证码") == "1111"
    assert runtime._minimal_form_value("cardPeriod_10", "开始") == "2026-01-01"
    assert runtime._minimal_form_value("cardPeriodEnd_10", "结束") == "2046-01-01"
    assert runtime._minimal_form_value("cardPeriod_20_default_1", "开始") == "2026-01-01"
    assert runtime._minimal_form_value("cardPeriodEnd_20_default_1", "结束") == "2031-01-01"
    assert len(runtime._minimal_form_value("contactAddress_10", "*联系地址")) >= 10


@pytest.mark.asyncio
async def test_explore_runtime_minimal_form_data_reaches_late_captcha_fields():
    from e2e_agent.core import page_exploration as runtime

    class FakePage:
        url = "https://example.com/apps/cps/product/insure"

        async def evaluate(self, script: str, arg: object = None) -> object:
            assert "records.length >= 40" not in script
            if "document.querySelectorAll('input, textarea, select')" in script:
                return [{"text": "请输入验证码=1111", "selector": "input:nth-of-type(88)"}]
            if "window.__agent3MockData" in script:
                return {}
            return []

        async def wait_for_timeout(self, _: int) -> None:
            return None

    actions = await runtime._apply_minimal_form_data(FakePage())

    captcha_actions = [action for action in actions if action["text"] == "请输入验证码=1111"]
    assert captcha_actions
    assert captcha_actions[0]["selector"] == "input:nth-of-type(88)"


@pytest.mark.asyncio
async def test_explore_runtime_minimal_form_data_has_dedicated_captcha_pass():
    from e2e_agent.core import page_exploration as runtime

    class FakePage:
        url = "https://example.com/apps/cps/product/insure"

        async def evaluate(self, script: str, arg: object = None) -> object:
            if "window.__agent3MockData" in script:
                return {}
            if "document.querySelectorAll('input, textarea, select')" in script:
                return []
            if "captchaInputs" in script:
                return [{"text": "请输入验证码=1111", "selector": "input:nth-of-type(88)"}]
            return []

        async def wait_for_timeout(self, _: int) -> None:
            return None

    actions = await runtime._apply_minimal_form_data(FakePage())

    captcha_actions = [action for action in actions if action["text"] == "请输入验证码=1111"]
    assert captcha_actions


@pytest.mark.asyncio
async def test_explore_runtime_requests_sms_code_then_refills_captcha():
    from e2e_agent.core import page_exploration as runtime

    class FakeSmsLocator:
        def __init__(self, page: "FakePage", text: str = "") -> None:
            self.page = page
            self.text = text

        @property
        def first(self) -> "FakeSmsLocator":
            return self

        def filter(self, has_text: str) -> "FakeSmsLocator":
            return FakeSmsLocator(self.page, has_text)

        async def is_visible(self, timeout: int = 0) -> bool:
            return self.text == "获取验证码"

        async def click(self, timeout: int = 0, no_wait_after: bool = False) -> None:
            self.page.sms_requested = True

    class FakePage:
        url = "https://example.com/apps/cps/product/insure"

        def __init__(self) -> None:
            self.evaluate_count = 0
            self.sms_requested = False

        async def evaluate(self, script: str, arg: object = None) -> object:
            if "window.__agent3MockData" in script:
                return {}
            if "document.querySelectorAll('input, textarea, select')" in script:
                return [{"text": "moblie_10 *手机号码=13800138000", "selector": "input[name=\"moblie_10\"]"}]
            if "captchaInputs" in script:
                assert self.sms_requested
                return [{"text": "请输入验证码=1111", "selector": "input.sms-code-input"}]
            return []

        def locator(self, selector: str) -> FakeSmsLocator:
            return FakeSmsLocator(self)

        async def wait_for_timeout(self, _: int) -> None:
            return None

    actions = await runtime._apply_minimal_form_data(FakePage())

    strategies = [action["click_strategy"] for action in actions]
    assert "js-minimal-data" in strategies
    assert strategies[-2:] == ["sms-code-request", "sms-captcha-refill"]


@pytest.mark.asyncio
async def test_explore_runtime_does_not_click_generic_form_labels_as_choice_data():
    from e2e_agent.core import page_exploration as runtime

    class FakeBodyLocator:
        async def inner_text(self, timeout: int = 0) -> str:
            return "投保人信息 被保险人信息 *姓名 *证件号码 *是否有纳税人识别号（一） 提交投保单"

    class FakePage:
        url = "https://example.com/apps/cps/product/insure"
        evaluated = False

        def locator(self, selector: str) -> FakeBodyLocator:
            assert selector == "body"
            return FakeBodyLocator()

        async def evaluate(self, script: str) -> list[dict[str, str]]:
            self.evaluated = True
            return [{"text": "*姓名", "selector": "label.label-item"}]

        async def wait_for_timeout(self, _: int) -> None:
            return None

    page = FakePage()
    actions = await runtime._apply_minimal_choice_data(page)

    assert actions == []
    assert page.evaluated is False


@pytest.mark.asyncio
async def test_explore_runtime_selects_custom_questionnaire_without_candidate_cap():
    from e2e_agent.core import page_exploration as runtime

    class FakePage:
        url = "https://example.com/product/to-insure"

        async def evaluate(self, script: str, arg: object = None) -> list[dict[str, str]]:
            assert "customQuestionNodes" in script
            assert arg is None
            return [
                {"text": f"A.第{index}题默认答案", "selector": ".js-answer-item", "click_strategy": "js-custom-questionnaire"}
                for index in range(1, 6)
            ] + [
                {"text": "确认无以上问题", "selector": ".insure-label", "click_strategy": "js-custom-questionnaire"}
            ]

        async def wait_for_timeout(self, _: int) -> None:
            return None

    selected = await runtime._apply_minimal_choice_data(FakePage())

    assert [item["text"] for item in selected] == [
        "A.第1题默认答案",
        "A.第2题默认答案",
        "A.第3题默认答案",
        "A.第4题默认答案",
        "A.第5题默认答案",
        "确认无以上问题",
    ]
    assert {item["click_strategy"] for item in selected} == {"js-custom-questionnaire"}


def test_explore_runtime_parses_questionnaire_validation_feedback():
    from e2e_agent.core import page_exploration as runtime

    assert runtime._unfilled_question_numbers_from_text(
        "回到顶部 第1题未填写，请全部完成后提交"
    ) == [1]
    assert runtime._unfilled_question_numbers_from_text(
        "第5题未填写，请全部完成后提交 第1题未填写，请全部完成后提交"
    ) == [5, 1]


@pytest.mark.asyncio
async def test_explore_runtime_repairs_unfilled_questionnaire_items():
    from e2e_agent.core import page_exploration as runtime

    calls: list[object] = []

    class FakePage:
        url = "https://example.com/product/to-insure"

        async def evaluate(self, script: str, arg: object = None) -> object:
            calls.append(arg)
            if "unfilledQuestionNumbers" in script:
                return [1, 5]
            assert "customQuestionNodes" in script
            assert arg == [1, 5]
            return [
                {"text": "A.第1题默认答案", "selector": ".question-1 .js-answer-item", "click_strategy": "js-questionnaire-repair"},
                {"text": "A.第5题默认答案", "selector": ".question-5 .js-answer-item", "click_strategy": "js-questionnaire-repair"},
            ]

        async def wait_for_timeout(self, _: int) -> None:
            return None

    repaired = await runtime._repair_unfilled_questionnaire_items(FakePage())

    assert [item["text"] for item in repaired] == ["A.第1题默认答案", "A.第5题默认答案"]
    assert calls == [None, [1, 5]]


def test_explore_runtime_prioritises_product_entry_action_over_documents():
    from e2e_agent.core import page_exploration as runtime

    action = runtime._best_product_entry_action(
        {
            "url": "https://example.com/product/detail",
            "actions": [
                {"index": 0, "text": "《关系声明确认书》", "visible": True, "selector": "#doc"},
                {"index": 1, "text": "《人身保险投保提示书》", "visible": True, "selector": "#notice"},
                {"index": 1, "text": "立即投保", "visible": True, "selector": "#submit"},
            ],
        },
        "NODE-insured-info",
        set(),
    )

    assert action is not None
    assert action["selector"] == "#submit"
    assert not runtime._is_safe_transit_action({"text": "《关系声明确认书》", "visible": True})
    assert not runtime._is_safe_transit_action({"text": "《人身保险投保提示书》", "visible": True})


def test_explore_runtime_rejects_faq_product_entry_matches():
    from e2e_agent.core import page_exploration as runtime

    action = runtime._best_product_entry_action(
        {
            "url": "https://example.com/product/detail",
            "body_text_excerpt": "产品详情 保费 立即投保 一次旅行想分成几次投保，或投保其中某一段是否可以？",
            "actions": [
                {
                    "index": 0,
                    "text": "一次旅行想分成几次投保，或投保其中某一段是否可以？",
                    "visible": True,
                    "selector": ".am-accordion-item.problem",
                },
                {"index": 1, "text": "立即投保", "visible": True, "selector": "#submit-by"},
            ],
        },
        "NODE-premium-calculation",
        set(),
    )

    assert action is not None
    assert action["selector"] == "#submit-by"
    assert runtime._best_product_entry_action(
        {
            "url": "https://example.com/product/detail",
            "actions": [
                {
                    "index": 0,
                    "text": "一次旅行想分成几次投保，或投保其中某一段是否可以？",
                    "visible": True,
                    "selector": ".am-accordion-item.problem",
                }
            ],
        },
        "NODE-premium-calculation",
        set(),
    ) is None


def test_click_primary_action_text_fallback_skips_faq_and_document_nodes():
    from e2e_agent.core import page_exploration as runtime

    source = inspect.getsource(runtime._click_primary_action)

    assert "isNoiseNode" in source
    assert ".am-accordion-item" in source
    assert ".problem" in source
    assert "[class*=\"provision\"]" in source
    assert ".closest('.product-detail-footer')" in source


def test_submit_action_repairs_h5_form_and_agreements_before_click():
    from e2e_agent.core import page_exploration as runtime

    source = inspect.getsource(runtime._click_primary_action)
    submit_branch = source[source.index("if is_submit_action:") : source.index("    if compact_text in")]

    assert "pre_submit_filled = await _apply_minimal_form_data(page)" in submit_branch
    assert "submit-pre-fill" in submit_branch
    assert "pre_submit_dismissed = await _dismiss_active_protocol_dialogs(page)" in submit_branch
    assert "agreement_checked = await _ensure_h5_agreement_checkbox_checked(page)" in submit_branch
    assert "post_agreement_dismissed = await _dismiss_active_protocol_dialogs(page)" in submit_branch
    assert "final_mock_data = await _load_agent3_mock_data_for_page(page)" in submit_branch
    assert "for final_select_attempt in range(2):" in submit_branch
    assert "final_select_filled = await _apply_h5_select_defaults(page, final_mock_data)" in submit_branch
    assert "submit-final-select-fill" in submit_branch
    assert "!el.closest('.am-modal,.adm-modal,[role=\"dialog\"],.layui-layer')" in submit_branch
    assert submit_branch.index("pre_submit_filled = await _apply_minimal_form_data(page)") < submit_branch.index("submit_rect = await page.evaluate")
    assert submit_branch.index("pre_submit_dismissed = await _dismiss_active_protocol_dialogs(page)") < submit_branch.index("submit_rect = await page.evaluate")
    assert submit_branch.index("agreement_checked = await _ensure_h5_agreement_checkbox_checked(page)") < submit_branch.index("submit_rect = await page.evaluate")
    assert submit_branch.index("post_agreement_dismissed = await _dismiss_active_protocol_dialogs(page)") < submit_branch.index("submit_rect = await page.evaluate")
    assert submit_branch.index("final_mock_data = await _load_agent3_mock_data_for_page(page)") < submit_branch.index("submit_rect = await page.evaluate")
    assert submit_branch.index("for final_select_attempt in range(2):") < submit_branch.index("submit_rect = await page.evaluate")
    assert "_click_protocol_list_agreements(page, pre_submit_body)" not in submit_branch
    assert "_dismiss_blocking_overlays(page)" not in submit_branch[: submit_branch.index("submit_rect = await page.evaluate")]


def test_h5_agreement_checkbox_sync_updates_dom_and_react_state():
    from e2e_agent.core import page_exploration as runtime

    source = inspect.getsource(runtime._ensure_h5_agreement_checkbox_checked)

    assert "HTMLInputElement.prototype, 'checked'" in source
    assert "input.dispatchEvent(new Event('change', { bubbles: true }))" in source
    assert "__reactEventHandlers" in source
    assert "onChange" in source
    assert "am-checkbox-wrapper-checked" in source
    assert "document.querySelectorAll('label,.am-checkbox-wrapper,.adm-checkbox,.agreement,.protocol')" in source
    assert "document.body" not in source


def test_active_protocol_dialog_dismisses_serial_modals_without_scanning_body():
    from e2e_agent.core import page_exploration as runtime

    source = inspect.getsource(runtime._dismiss_active_protocol_dialogs)

    assert "for _ in range(12):" in source
    assert "document.querySelectorAll('.am-modal,.adm-modal,[role=\"dialog\"],.layui-layer')" in source
    assert "const dialog = dialogs[0];" in source
    assert ".am-modal-button,.adm-button,.am-button" in source
    assert "b.score - a.score" in source
    assert "await page.wait_for_timeout(_POST_CLICK_SETTLE_MS)" in source
    assert "document.body" not in source
    assert "querySelectorAll('body" not in source


def test_coordinate_click_falls_back_to_mouse_when_touch_is_unavailable():
    from e2e_agent.core import page_exploration as runtime

    helper_source = inspect.getsource(runtime._tap_or_click_coordinates)
    click_source = inspect.getsource(runtime._click_primary_action)

    assert "await page.touchscreen.tap(x, y)" in helper_source
    assert "except Exception:" in helper_source
    assert "await page.mouse.click(x, y)" in helper_source
    assert "_tap_or_click_coordinates(page, submit_x, submit_y)" in click_source
    assert "hasattr(page, \"touchscreen\")" not in click_source


def test_h5_footer_insure_click_requires_observed_progress_before_returning():
    from e2e_agent.core import page_exploration as runtime

    source = inspect.getsource(runtime._click_primary_action)
    footer_branch = source[source.index('if compact_text == "投保":') : source.index("    if is_submit_action:")]

    assert "body_after_footer" in footer_branch
    assert "continue" not in footer_branch
    assert "product/footer-insure-no-progress" in footer_branch
    assert 'click_strategy": "mouse-h5-product-footer-insure",' in footer_branch
    assert footer_branch.index("body_after_footer") < footer_branch.index('click_strategy": "mouse-h5-product-footer-insure",')


def test_agent3_installs_missing_product_detail_analytics_shim():
    from e2e_agent.core import page_exploration as runtime

    shim_source = runtime._HZ_PAGE_ACTION_SHIM_SCRIPT
    shim_helper_source = inspect.getsource(runtime._install_hz_page_action_shim)
    submit_trace_source = inspect.getsource(runtime._install_submit_trace)
    click_source = inspect.getsource(runtime._click_primary_action)

    assert "var hzPageAction =" in shim_source
    assert "(0, eval)" not in shim_source
    assert "context.add_init_script" in shim_helper_source
    assert "page.add_script_tag" in shim_helper_source
    assert "await _install_hz_page_action_shim(page)" in submit_trace_source
    assert "await _install_hz_page_action_shim(page)" in click_source
    assert "agent3-hzPageAction-noop" in click_source


def test_premium_calculation_recognises_visible_trial_modal_text():
    from e2e_agent.core import page_exploration as runtime

    snapshot = {
        "url": "https://example.test/product/detail?prodId=1&planId=2",
        "body_text_excerpt": "产品详情 保费 立即投保",
        "field_count": 0,
        "page_state": {
            "blocking_overlays": [
                {
                    "text": "保费试算 保费与被保人性别、年龄等条件相关 162.00元 确 定",
                    "selector": ".am-modal",
                }
            ]
        },
    }

    assert runtime._matches_node_reach_contract(snapshot, "NODE-premium-calculation")
    assert runtime._infer_current_node_id(snapshot) == "NODE-premium-calculation"


def test_explore_runtime_allows_bounded_product_entry_cta_retry():
    from e2e_agent.core import page_exploration as runtime

    page = {
        "url": "https://example.com/product/detail",
        "actions": [{"index": 0, "text": "立即投保", "visible": True, "selector": "#submit-by"}],
    }

    assert runtime._best_product_entry_action(
        page,
        "NODE-applicant-info",
        {"https://example.com/product/detail|#submit-by|立即投保|NODE-applicant-info"},
    ) is not None
    assert runtime._best_product_entry_action(
        page,
        "NODE-applicant-info",
        {
            "https://example.com/product/detail|#submit-by|立即投保|NODE-applicant-info",
            "https://example.com/product/detail|#submit-by|立即投保|NODE-applicant-info|retry",
        },
    ) is None


def test_explore_runtime_does_not_match_downstream_nodes_on_product_detail_copy():
    from e2e_agent.core import page_exploration as runtime

    product_detail = {
        "url": "https://cps.example.com/apps/cps/product/detail?prodId=1&planId=2",
        "body_text_excerpt": (
            "保险首页 保单查询 产品详情 投保人出生日期 被保险人出生日期 "
            "保费 立即投保 《人身保险投保提示书》"
        ),
    }

    assert runtime._page_matches_node(
        product_detail,
        {"node_id": "NODE-product-detail", "url_pattern": "/product/detail"},
    )
    assert not runtime._page_matches_node(
        product_detail,
        {"node_id": "NODE-applicant-info", "url_pattern": "/insure/applicant"},
    )
    assert not runtime._page_matches_node(
        product_detail,
        {"node_id": "NODE-policy-result", "url_pattern": "/policy/result"},
    )


def test_explore_runtime_does_not_match_applicant_on_health_notice_copy():
    from e2e_agent.core import page_exploration as runtime

    health_notice = {
        "url": "https://cps.example.com/apps/cps/product/to-insure?encryptInsureNum=abc",
        "title": "健康告知",
        "body_text_excerpt": (
            "为了保障您的权益，请填写真实有效的信息。被保险人健康告知 "
            "本健康问卷必须由投保人亲自填写 确认无以上问题"
        ),
    }

    assert runtime._page_matches_node(
        health_notice,
        {"node_id": "NODE-health-notice", "url_pattern": "/health-notice"},
    )
    assert not runtime._page_matches_node(
        health_notice,
        {"node_id": "NODE-applicant-info", "url_pattern": "/insure/applicant"},
    )


def test_explore_runtime_infers_suitability_questionnaire_node():
    from e2e_agent.core import page_exploration as runtime

    questionnaire = {
        "url": "https://cps.example.com/apps/cps/product/to-insure?encryptInsureNum=abc",
        "title": "健康告知",
        "body_text_excerpt": (
            "特别提示 适当性评估问卷 1.【保险需求】您想通过这份保险，"
            "主要解决哪方面的担心? 2.【财务分析】您能够并愿意为购买保险支付"
            "的期交保费之和占家庭年收入的比例多少? 5.【风险偏好】以下哪项描述最符合"
        ),
    }

    assert runtime._infer_current_node_id(questionnaire) == "NODE-suitability"
    assert runtime._page_matches_node(
        questionnaire,
        {"node_id": "NODE-suitability", "url_pattern": "/suitability"},
    )
    assert not runtime._page_matches_node(
        questionnaire,
        {"node_id": "NODE-applicant-info", "url_pattern": "/insure/applicant"},
    )


def test_explore_runtime_aligns_planned_index_to_inferred_later_node():
    from e2e_agent.core import page_exploration as runtime

    planned_nodes = [
        {"node_id": "NODE-product-detail"},
        {"node_id": "NODE-applicant-info"},
        {"node_id": "NODE-insured-info"},
        {"node_id": "NODE-suitability"},
        {"node_id": "NODE-health-notice"},
    ]
    questionnaire = {
        "title": "健康告知",
        "body_text_excerpt": "适当性评估问卷 保险需求 财务分析 风险偏好",
    }

    assert runtime._align_planned_index(planned_nodes, 1, questionnaire) == 3


def test_explore_runtime_aligns_planned_index_back_to_observed_current_node():
    from e2e_agent.core import page_exploration as runtime

    planned_nodes = [
        {"node_id": "NODE-product-detail"},
        {"node_id": "NODE-applicant-info"},
        {"node_id": "NODE-insured-info"},
    ]
    product_detail = {
        "url": "https://cps.example.com/apps/cps/product/detail?prodId=1",
        "body_text_excerpt": "产品详情 保费 立即投保 投保人出生日期",
    }

    assert runtime._align_planned_index(planned_nodes, 1, product_detail) == 0


def test_explore_runtime_allows_transit_action_from_choice_page_to_terminal_target():
    from e2e_agent.core import page_exploration as runtime

    health_notice = {
        "body_text_excerpt": "健康告知 请确认无以上问题",
        "actions": [{"text": "提交", "visible": True, "selector": "#submit"}],
    }

    assert runtime._should_try_transit_action(health_notice, is_terminal_target=True)
    action = runtime._best_transit_action(health_notice, "NODE-policy-result", set())
    assert action is not None
    assert action["selector"] == "#submit"


def test_explore_runtime_rejects_composed_submit_container_for_policy_result():
    from e2e_agent.core import page_exploration as runtime

    assert (
        runtime._planned_action_score(
            {"text": "查看提交", "visible": True, "selector": "div:nth-of-type(1)"},
            "NODE-policy-result",
        )
        == 0
    )
    assert (
        runtime._planned_action_score(
            {"text": "提交投保单", "visible": True, "selector": ".submit_butTextx"},
            "NODE-policy-result",
        )
        > 0
    )


def test_explore_runtime_treats_payment_page_as_external_policy_result_handoff():
    from e2e_agent.core import page_exploration as runtime

    pay_page = {
        "url": "https://commerce.example.test/demo-channel/pay",
        "title": "杰瑞保险",
        "body_text_excerpt": "支付方式 应付额：￥26359.00 立即支付",
    }
    qr_pay_page = {
        "url": "https://commerce.example.test/demo-channel/pay/go",
        "title": "杰瑞保险",
        "body_text_excerpt": "使用微信扫一扫，扫描下方二维码完成支付 应付总额：26359.00",
    }

    assert runtime._matches_node_reach_contract(pay_page, "NODE-payment")
    assert runtime._infer_current_node_id(pay_page) == "NODE-payment"
    assert runtime._page_matches_node(qr_pay_page, {"node_id": "NODE-policy-result"})
    assert runtime._is_external_payment_handoff(qr_pay_page, "NODE-policy-result")


def test_explore_runtime_extracts_wechat_payment_boundary_evidence():
    from e2e_agent.core import page_exploration as runtime

    page = {
        "url": (
            "https://wx.tenpay.com/cgi-bin/mmpayweb-bin/checkmweb"
            "?prepay_id=wx0419423030112540901853abdd8bde0001"
            "&redirect_url=https%3A%2F%2Fpayments.example.test%2Fv2%2Freturn%2Fwechat%3Ftrade_no%3DP0612606040000000118"
        ),
        "title": "微信支付",
        "body_text_excerpt": "微信支付 收银台",
        "actions": [],
    }

    evidence = runtime._payment_boundary_evidence_from_page(page)

    assert evidence == {
        "paymentMethod": "wechat",
        "gatewayPayNum": "P0612606040000000118",
        "gatewayPayNum_source": "wechat-url-trade_no",
        "paymentUrlHost": "wx.tenpay.com",
        "paymentUrlPath": "/cgi-bin/mmpayweb-bin/checkmweb",
        "cashierOwner": "generic-insurance",
    }
    assert "paymentUrl" not in evidence


def test_explore_runtime_page_content_records_preserve_payment_boundary_evidence():
    from e2e_agent.core import page_exploration as runtime

    pages = [
        {
            "page_key": "v2-wechat-pay",
            "url": "https://payments.example.test/v2/wechat_pay?trade_no=P0612606040000000118",
            "title": "微信支付",
            "dom_signature": "wechat-pay-dom",
            "body_text_excerpt": "微信支付 收银台 insureNum=20260531013475",
            "field_count": 0,
            "action_count": 1,
            "fields": [],
            "actions": [{"text": "立即支付", "tag": "button", "selector": "#submitToPay"}],
            "payment_boundary_evidence": runtime._payment_boundary_evidence_from_page(
                {
                    "url": "https://payments.example.test/v2/wechat_pay?trade_no=P0612606040000000118",
                    "title": "微信支付",
                    "body_text_excerpt": "微信支付 收银台 insureNum=20260531013475",
                    "actions": [],
                }
            ),
        }
    ]

    records = runtime._build_page_content_records(pages, [])

    evidence = records[0]["payment_boundary_evidence"]
    assert evidence["paymentMethod"] == "wechat"
    assert evidence["gatewayPayNum"] == "P0612606040000000118"
    assert evidence["gatewayPayNum_source"] == "wechat-url-trade_no"
    assert evidence["insureNum"] == "20260531013475"
    assert evidence["paymentUrlHost"] == "payments.example.test"
    assert evidence["paymentUrlPath"] == "/v2/wechat_pay"
    assert evidence["cashierOwner"] == "generic-insurance"
    assert "paymentUrl" not in evidence


def test_explore_runtime_infers_pay_success_as_policy_result_boundary():
    from e2e_agent.core import page_exploration as runtime

    pay_success_page = {
        "url": "https://commerce.example.test/demo-channel/pay/success?fromFlag=daikou&id=ORDER-1",
        "title": "杰瑞保险",
        "body_text_excerpt": "请输入验证码 获取验证码 支付结果处理中",
    }
    pay_entry_page = {
        "url": "https://commerce.example.test/demo-channel/pay/?id=ORDER-1",
        "title": "杰瑞保险",
        "body_text_excerpt": "支付方式 应付额：￥26359.00 立即支付",
    }

    assert runtime._infer_current_node_id(pay_success_page) == "NODE-policy-result"
    assert runtime._infer_current_node_id(pay_entry_page) == "NODE-payment"


def test_explore_runtime_does_not_infer_product_detail_disclosure_as_policy_result():
    from e2e_agent.core import page_exploration as runtime

    product_detail_page = {
        "url": "https://commerce.example.test/m/apps/cps/demo-channel/product/detail?prodId=1",
        "title": "年金保险",
        "body_text_excerpt": "产品详情 保费 立即投保 电子保单服务说明",
    }

    assert runtime._infer_current_node_id(product_detail_page) == "NODE-product-detail"


def test_explore_runtime_uses_student_job_path_for_minor_insured():
    from e2e_agent.core import page_exploration as runtime

    assert runtime._JOB_DROPDOWN_DEFAULTS["jobText_20_default_1"] == {
        "job1": "文教行业人员",
        "job2": "教育机构从业人员",
        "job3": "一般学生",
    }


def test_explore_runtime_h5_occupation_picker_prefers_live_leaf_path():
    import inspect

    from e2e_agent.core import page_exploration as runtime

    source = inspect.getsource(runtime._apply_h5_select_defaults)

    assert "一般内勤人员" in source
    assert "机关团体公司" in source
    assert "occupationOptionScore" in source


def test_explore_runtime_waits_longer_for_processing_overlay():
    from e2e_agent.core import page_exploration as runtime

    assert runtime._PROCESSING_OVERLAY_WAIT_MS >= 60_000


def test_explore_runtime_uses_policy_generator_like_basic_defaults():
    from e2e_agent.core import page_exploration as runtime

    first_phone = runtime._minimal_form_value("mobile_10", "手机号")
    second_phone = runtime._minimal_form_value("mobile_20_default_1", "手机号")
    assert first_phone != second_phone
    assert len(first_phone) == 11
    assert len(second_phone) == 11
    assert first_phone[:3] in runtime._POLICY_INFO_MOBILE_PREFIXES
    assert second_phone[:3] in runtime._POLICY_INFO_MOBILE_PREFIXES
    assert runtime._minimal_form_value("height_20", "身高") == "170"
    assert runtime._minimal_form_value("weight_20", "体重") == "60"


def test_explore_runtime_detects_business_page_loading_state():
    from e2e_agent.core import page_exploration as runtime

    assert not runtime._business_page_ready_text(
        "https://cps.example.com/apps/cps/product/insure?encryptInsureNum=abc",
        "正在加载，请稍后... 本人充分阅读、理解并同意 《投保声明》 提交投保单",
    )
    assert runtime._business_page_ready_text(
        "https://cps.example.com/apps/cps/product/insure?encryptInsureNum=abc",
        "投保人信息 姓名 证件号码 手机号 被保险人信息 提交投保单",
    )
    assert runtime._business_page_ready_text(
        "https://cps.example.com/apps/cps/product/detail?prodId=1",
        "正在加载，请稍后...",
    )


def test_explore_runtime_recognises_blocking_overlay_dismiss_actions():
    from e2e_agent.core import page_exploration as runtime

    assert runtime._is_overlay_dismiss_text("确定")
    assert runtime._is_overlay_dismiss_text("继续投保")
    assert runtime._is_overlay_dismiss_text("确认")
    assert not runtime._is_overlay_dismiss_text("《投保声明》")


@pytest.mark.asyncio
async def test_explore_runtime_collects_full_form_profile_without_field_cap():
    from e2e_agent.core import page_exploration as runtime

    class FakeLocator:
        async def evaluate_all(self, script: str) -> list[dict[str, object]]:
            assert "slice(0, 20)" not in script
            return [
                {"index": index, "tag": "input", "type": "text", "name": f"field_{index}", "selector": f"#field_{index}"}
                for index in range(25)
            ]

    class FakePage:
        def locator(self, selector: str) -> FakeLocator:
            assert selector == "input, select, textarea"
            return FakeLocator()

    fields = await runtime._collect_fields(FakePage())

    assert len(fields) == 25
    assert fields[-1]["name"] == "field_24"


@pytest.mark.asyncio
async def test_explore_runtime_checks_required_agreements_from_validation_feedback():
    from e2e_agent.core import page_exploration as runtime

    class FakeBodyLocator:
        async def inner_text(self, timeout: int = 0) -> str:
            return "投保人信息 提交投保单 请先阅读并同意相关协议 《投保声明》"

    class FakePage:
        url = "https://example.com/apps/cps/product/insure"

        def locator(self, selector: str) -> FakeBodyLocator:
            assert selector == "body"
            return FakeBodyLocator()

        async def evaluate(self, script: str, arg: object = None) -> object:
            if "window.__agent3MockData" in script:
                return {}
            if "agreementCandidates" in script:
                return [
                    {
                        "text": "本人充分阅读、理解并同意《投保声明》",
                        "selector": "#agree",
                        "click_strategy": "js-agreement-check",
                    }
                ]
            return []

        async def wait_for_timeout(self, _: int) -> None:
            return None

    actions = await runtime._apply_minimal_transit_data(FakePage())
    agreement_actions = [action for action in actions if action.get("action_type") == "agreement"]

    assert [action["text"] for action in agreement_actions] == ["本人充分阅读、理解并同意《投保声明》"]
    assert agreement_actions[0]["click_strategy"] == "js-agreement-check"


@pytest.mark.asyncio
async def test_explore_runtime_records_protocol_documents_without_opening_tabs(monkeypatch):
    from e2e_agent.core import page_exploration as runtime

    class FakeBodyLocator:
        async def inner_text(self, timeout: int = 0) -> str:
            return (
                "请先阅读并同意相关协议 本人充分阅读、理解并同意 "
                "《投保声明》 《续期授权声明》 本人已逐页阅读并同意 "
                "《保险条款》 《责任免除》 《隐私政策声明》"
            )

    class FakeDocLocator:
        def __init__(self, page: "FakePage", token: str = "") -> None:
            self.page = page
            self.token = token

        @property
        def first(self) -> "FakeDocLocator":
            return self

        def filter(self, has_text: str) -> "FakeDocLocator":
            return FakeDocLocator(self.page, has_text)

        async def is_visible(self, timeout: int = 0) -> bool:
            return self.token in self.page.body_text

        async def click(self, timeout: int = 0, no_wait_after: bool = False) -> None:
            self.page.clicked_docs.append(self.token)

    class FakePage:
        url = "https://example.com/apps/cps/product/insure"

        def __init__(self) -> None:
            self.clicked_docs: list[str] = []
            self.body_text = (
                "请先阅读并同意相关协议 本人充分阅读、理解并同意 "
                "《投保声明》 《续期授权声明》 本人已逐页阅读并同意 "
                "《保险条款》 《责任免除》 《隐私政策声明》"
            )

        def locator(self, selector: str):
            if selector == "body":
                return FakeBodyLocator()
            return FakeDocLocator(self)

        async def evaluate(self, script: str) -> list[dict[str, str]]:
            return []

        async def wait_for_timeout(self, _: int) -> None:
            return None

    async def fake_dismiss(page: FakePage) -> list[dict[str, str]]:
        return [{"text": "已阅读并同意", "source_url": page.url, "target_url": page.url}]

    monkeypatch.setattr(runtime, "_dismiss_blocking_overlays", fake_dismiss)

    page = FakePage()
    actions = await runtime._read_required_agreement_documents(page, page.body_text)

    assert page.clicked_docs == []
    assert actions == []


@pytest.mark.asyncio
async def test_explore_runtime_clicks_custom_agreement_text_lines():
    from e2e_agent.core import page_exploration as runtime

    class FakeBodyLocator:
        async def inner_text(self, timeout: int = 0) -> str:
            return (
                "本人充分阅读、理解并同意 《投保声明》 《续期授权声明》 "
                "本人已逐页阅读并同意 《保险条款》 《责任免除》 《隐私政策声明》 "
                "请先阅读并同意相关协议"
            )

    class FakePage:
        url = "https://example.com/apps/cps/product/insure"

        def locator(self, selector: str) -> FakeBodyLocator:
            assert selector == "body"
            return FakeBodyLocator()

        async def evaluate(self, script: str) -> list[dict[str, str]]:
            assert "agreementLineTokens" in script
            return [
                {
                    "text": "本人充分阅读、理解并同意 《投保声明》 《续期授权声明》",
                    "selector": ".agree-line-primary",
                    "click_strategy": "js-agreement-line-check",
                },
                {
                    "text": "本人已逐页阅读并同意 《保险条款》 《责任免除》 《隐私政策声明》",
                    "selector": ".agree-line-terms",
                    "click_strategy": "js-agreement-line-check",
                },
            ]

        async def wait_for_timeout(self, _: int) -> None:
            return None

    actions = await runtime._check_required_agreements(FakePage())

    assert [action["click_strategy"] for action in actions] == [
        "js-agreement-line-check",
        "js-agreement-line-check",
    ]
    assert [action["selector"] for action in actions] == [".agree-line-primary", ".agree-line-terms"]


@pytest.mark.asyncio
async def test_explore_runtime_clicks_agreement_controls_next_to_document_lines():
    from e2e_agent.core import page_exploration as runtime

    class FakeBodyLocator:
        async def inner_text(self, timeout: int = 0) -> str:
            return (
                "请先阅读并同意相关协议 本人充分阅读、理解并同意 "
                "《投保声明》 《续期授权声明》 本人已逐页阅读并同意 "
                "《保险条款》 《责任免除》 《隐私政策声明》"
            )

    class FakePage:
        url = "https://example.com/apps/cps/product/insure"

        def locator(self, selector: str) -> FakeBodyLocator:
            assert selector == "body"
            return FakeBodyLocator()

        async def evaluate(self, script: str) -> list[dict[str, str]]:
            assert "agreementControlGroups" in script
            return [
                {
                    "text": "本人充分阅读、理解并同意 《投保声明》 《续期授权声明》",
                    "selector": ".primary-agreement-checkbox",
                    "click_strategy": "js-agreement-control-check",
                },
                {
                    "text": "本人已逐页阅读并同意 《保险条款》 《责任免除》 《隐私政策声明》",
                    "selector": ".terms-agreement-checkbox",
                    "click_strategy": "js-agreement-control-check",
                },
            ]

        async def wait_for_timeout(self, _: int) -> None:
            return None

    actions = await runtime._check_required_agreements(FakePage())

    assert [action["selector"] for action in actions] == [
        ".primary-agreement-checkbox",
        ".terms-agreement-checkbox",
    ]
    assert {action["click_strategy"] for action in actions} == {"js-agreement-control-check"}


def test_explorer_oracle_detects_unsatisfied_agreement_groups_once():
    from e2e_agent.core import page_exploration as runtime

    page_state = {
        "validation_feedback": ["请先阅读并同意相关协议"],
        "agreement_groups": [
            {
                "group_id": "read_and_agree_primary",
                "text": "本人充分阅读、理解并同意 《投保声明》 《续期授权声明》",
                "selector": ".primary-agreement-checkbox",
                "satisfied": False,
            },
            {
                "group_id": "read_and_agree_terms",
                "text": "本人已逐页阅读并同意 《保险条款》 《责任免除》 《隐私政策声明》",
                "selector": ".terms-agreement-checkbox",
                "satisfied": False,
            },
            {
                "group_id": "read_and_agree_primary",
                "text": "duplicate text for same group",
                "selector": ".primary-agreement-checkbox",
                "satisfied": False,
            },
        ],
        "controls": [],
    }

    requirements = runtime._detect_unresolved_requirements(page_state)

    assert [
        (item["type"], item["key"], item["selector"])
        for item in requirements
        if item["type"] == "agreement"
    ] == [
        ("agreement", "agreement:read_and_agree_primary", ".primary-agreement-checkbox"),
        ("agreement", "agreement:read_and_agree_terms", ".terms-agreement-checkbox"),
    ]


def test_explorer_oracle_detects_empty_required_fields_and_placeholder_dropdowns():
    from e2e_agent.core import page_exploration as runtime

    page_state = {
        "validation_feedback": ["请输入姓名", "请选择职业"],
        "agreement_groups": [],
        "controls": [
            {
                "kind": "input",
                "selector": "input[name=\"cName_10\"]",
                "name": "cName_10",
                "label": "*姓名",
                "value": "",
                "checked": False,
                "required_like": True,
                "placeholder_like": False,
                "visible": True,
                "disabled": False,
                "readonly": False,
            },
            {
                "kind": "select",
                "selector": ".job1[name=\"jobText_10\"]",
                "name": "jobText_10",
                "label": "*职业 请选择",
                "value": "请选择",
                "checked": False,
                "required_like": True,
                "placeholder_like": True,
                "visible": True,
                "disabled": False,
                "readonly": False,
            },
            {
                "kind": "input",
                "selector": "input[name=\"email_10\"]",
                "name": "email_10",
                "label": "*电子邮箱",
                "value": "test@example.com",
                "checked": False,
                "required_like": True,
                "placeholder_like": False,
                "visible": True,
                "disabled": False,
                "readonly": False,
            },
        ],
    }

    requirements = runtime._detect_unresolved_requirements(page_state)

    assert [
        (item["type"], item["key"], item["selector"])
        for item in requirements
    ] == [
        ("field", "field:input[name=\"cName_10\"]", "input[name=\"cName_10\"]"),
        ("field", "field:.job1[name=\"jobText_10\"]", ".job1[name=\"jobText_10\"]"),
    ]


def test_explorer_oracle_skips_repairs_that_already_repeated():
    from e2e_agent.core import page_exploration as runtime

    requirements = [
        {"type": "agreement", "key": "agreement:read_and_agree_primary", "selector": ".primary"},
        {"type": "field", "key": "field:input[name=\"cName_10\"]", "selector": "input[name=\"cName_10\"]"},
    ]

    planned = runtime._plan_requirement_repairs(
        requirements,
        repair_counts={"agreement:read_and_agree_primary": 2},
    )

    assert [item["key"] for item in planned] == ["field:input[name=\"cName_10\"]"]


def test_explore_runtime_extracts_blocking_submit_overlay_reason():
    from e2e_agent.core import page_exploration as runtime

    snapshot = {
        "body_text_excerpt": "投保人信息 提交投保单 信息 该单正在处理中，请稍后操作",
        "page_state": {
            "blocking_overlays": [
                {
                    "text": "信息 该单正在处理中，请稍后操作",
                    "selector": ".layui-layer",
                }
            ]
        },
    }

    assert runtime._blocking_overlay_reason(snapshot) == (
        "Blocking overlay after submit: 信息 该单正在处理中，请稍后操作"
    )
    assert runtime._is_processing_overlay_reason(runtime._blocking_overlay_reason(snapshot))


def test_explore_runtime_extracts_insured_age_overlay_reason():
    from e2e_agent.core import page_exploration as runtime

    snapshot = {
        "body_text_excerpt": "投保人信息 提交投保单",
        "page_state": {
            "blocking_overlays": [
                {
                    "text": "提示 被保人当前为 11 周岁，请确认是否继续",
                    "selector": ".layui-layer",
                }
            ]
        },
    }

    assert runtime._blocking_overlay_reason(snapshot) == (
        "Blocking overlay after submit: 提示 被保人当前为 11 周岁，请确认是否继续"
    )
    assert not runtime._is_processing_overlay_reason(runtime._blocking_overlay_reason(snapshot))


def test_explore_runtime_does_not_treat_protocol_modal_as_blocking_submit_overlay():
    from e2e_agent.core import page_exploration as runtime

    snapshot = {
        "body_text_excerpt": "投保人信息 提交订单",
        "page_state": {
            "blocking_overlays": [
                {
                    "text": (
                        "重要提示 投保条件 本保险合同由保险条款、保险单、投保申请组成 "
                        "请详细阅读保险合同 被保险人不满10周岁的保险金额限制 已阅读并同意"
                    ),
                    "selector": ".am-modal",
                }
            ]
        },
    }

    assert runtime._blocking_overlay_reason(snapshot) is None


@pytest.mark.asyncio
async def test_explore_runtime_uses_line_level_agreement_controls():
    from e2e_agent.core import page_exploration as runtime

    class FakeBodyLocator:
        async def inner_text(self, timeout: int = 0) -> str:
            return (
                "请先阅读并同意相关协议 本人充分阅读、理解并同意 "
                "《投保声明》 《续期授权声明》 本人已逐页阅读并同意 "
                "《保险条款》 《责任免除》 《隐私政策声明》"
            )

    class FakePage:
        url = "https://example.com/apps/cps/product/insure"

        def locator(self, selector: str) -> FakeBodyLocator:
            assert selector == "body"
            return FakeBodyLocator()

        async def evaluate(self, script: str) -> list[dict[str, str]]:
            assert "nearestAgreementLineControl" in script
            assert "agreement-line-control-already-checked" in script
            return [
                {
                    "text": "本人充分阅读、理解并同意",
                    "selector": "div.protocol-line:nth-of-type(1) > span.hz-check-item:nth-of-type(1)",
                    "click_strategy": "js-agreement-line-control-check",
                },
                {
                    "text": "本人已逐页阅读并同意",
                    "selector": "div.protocol-line:nth-of-type(2) > span.hz-check-item:nth-of-type(1)",
                    "click_strategy": "agreement-line-control-already-checked",
                    "action_type": "agreement_observed",
                },
            ]

        async def wait_for_timeout(self, _: int) -> None:
            return None

    actions = await runtime._check_required_agreements(FakePage())

    assert [action["click_strategy"] for action in actions] == [
        "js-agreement-line-control-check",
        "agreement-line-control-already-checked",
    ]
    assert actions[0]["action_type"] == "agreement"
    assert actions[1]["action_type"] == "agreement_observed"


@pytest.mark.asyncio
async def test_explore_runtime_falls_back_to_agreement_text_nodes():
    from e2e_agent.core import page_exploration as runtime

    class FakeBodyLocator:
        async def inner_text(self, timeout: int = 0) -> str:
            return "本人充分阅读、理解并同意 《投保声明》 请先阅读并同意相关协议"

    class FakePage:
        url = "https://example.com/apps/cps/product/insure"

        def locator(self, selector: str) -> FakeBodyLocator:
            assert selector == "body"
            return FakeBodyLocator()

        async def evaluate(self, script: str) -> list[dict[str, str]]:
            assert "createTreeWalker" in script
            return [
                {
                    "text": "本人充分阅读、理解并同意",
                    "selector": ".text-node-parent",
                    "click_strategy": "js-agreement-text-node-check",
                }
            ]

        async def wait_for_timeout(self, _: int) -> None:
            return None

    actions = await runtime._check_required_agreements(FakePage())

    assert actions[0]["click_strategy"] == "js-agreement-text-node-check"
    assert actions[0]["selector"] == ".text-node-parent"


@pytest.mark.asyncio
async def test_explore_runtime_scans_agreements_beyond_body_excerpt_limit():
    from e2e_agent.core import page_exploration as runtime

    class FakeBodyLocator:
        async def inner_text(self, timeout: int = 0) -> str:
            return "字段" * 700 + " 本人充分阅读、理解并同意 《投保声明》 请先阅读并同意相关协议"

    class FakePage:
        url = "https://example.com/apps/cps/product/insure"

        def locator(self, selector: str) -> FakeBodyLocator:
            assert selector == "body"
            return FakeBodyLocator()

        async def evaluate(self, script: str) -> list[dict[str, str]]:
            return [
                {
                    "text": "本人充分阅读、理解并同意",
                    "selector": ".late-agreement",
                    "click_strategy": "js-agreement-text-node-check",
                }
            ]

        async def wait_for_timeout(self, _: int) -> None:
            return None

    actions = await runtime._check_required_agreements(FakePage())

    assert actions[0]["selector"] == ".late-agreement"


def test_explore_runtime_infers_insure_form_as_aggregate_node():
    from e2e_agent.core import page_exploration as runtime

    insure_form = {
        "url": "https://cps.example.com/apps/cps/product/insure?encryptInsureNum=abc",
        "title": "",
        "body_text_excerpt": (
            "为了保障您的权益，请填写真实有效的信息。投保人信息 姓名 证件号码 手机号 "
            "被保险人信息 本人充分阅读、理解并同意 《投保声明》 提交投保单"
        ),
    }

    assert runtime._infer_current_node_id(insure_form) == "NODE-insure-form"
    assert runtime._page_matches_node(
        insure_form,
        {"node_id": "NODE-insure-form", "url_pattern": "/product/insure"},
    )


def test_explore_runtime_dedupes_planned_pages_and_content_records():
    from e2e_agent.core import page_exploration as runtime

    regression_paths = [
        {
            "path_id": "PATH-001",
            "nodes": ["NODE-start", "NODE-product-detail", "NODE-plan-selection", "NODE-end"],
            "page_keys": [
                {
                    "node_id": "NODE-product-detail",
                    "page_key": "PK-product-detail",
                    "url_pattern": "/product/detail",
                    "state": {},
                },
                {
                    "node_id": "NODE-plan-selection",
                    "page_key": "PK-insure-plan",
                    "url_pattern": "/insure/plan",
                    "state": {},
                },
            ],
        },
        {
            "path_id": "PATH-002",
            "nodes": ["NODE-start", "NODE-product-detail", "NODE-plan-selection", "NODE-end"],
            "page_keys": [
                {
                    "node_id": "NODE-product-detail",
                    "page_key": "PK-product-detail",
                    "url_pattern": "/product/detail",
                    "state": {},
                },
                {
                    "node_id": "NODE-plan-selection",
                    "page_key": "PK-insure-plan",
                    "url_pattern": "/insure/plan",
                    "state": {},
                },
            ],
        },
    ]
    pages = [
        {
            "page_key": "apps-cps-product-detail",
            "url": "https://example.com/apps/cps/product/detail?prodId=1",
            "title": "Product",
            "dom_signature": "same-dom",
            "body_text_excerpt": "产品详情 保障责任 购买",
            "field_count": 1,
            "action_count": 1,
            "fields": [{"index": 0, "tag": "input", "id": "prodId", "selector": "#prodId"}],
            "actions": [{"text": "购买", "tag": "a", "selector": "a:nth-of-type(1)"}],
        },
        {
            "page_key": "apps-cps-product-detail",
            "url": "https://example.com/apps/cps/product/detail?prodId=1",
            "title": "Product",
            "dom_signature": "same-dom",
            "body_text_excerpt": "产品详情 保障责任 购买",
            "field_count": 1,
            "action_count": 1,
            "fields": [{"index": 0, "tag": "input", "id": "prodId", "selector": "#prodId"}],
            "actions": [{"text": "购买", "tag": "a", "selector": "a:nth-of-type(1)"}],
        },
    ]

    catalog = runtime._build_planned_page_catalog(regression_paths)
    records = runtime._build_page_content_records(pages, catalog)
    planned_steps = {
        "PATH-001": [
            {
                "planned_node_id": "NODE-product-detail",
                "matched": True,
                "actual_url": "https://example.com/apps/cps/product/detail?prodId=1",
                "actual_page_key": "apps-cps-product-detail",
            },
            {
                "planned_node_id": "NODE-plan-selection",
                "matched": True,
                "actual_url": "https://example.com/apps/cps/product/detail?prodId=1",
                "actual_page_key": "apps-cps-product-detail",
                "action": {
                    "text": "购买",
                    "tag": "a",
                    "selector": "a:nth-of-type(1)",
                    "source_url": "https://example.com/apps/cps/product/detail?prodId=1",
                    "target_url": "https://example.com/apps/cps/insure/plan",
                    "click_strategy": "js",
                },
            },
        ]
    }
    results = runtime._build_path_exploration_results(
        regression_paths,
        pages,
        [],
        planned_steps,
        catalog,
        records,
    )

    assert len(catalog) == 2
    assert catalog[0]["path_ids"] == ["PATH-001", "PATH-002"]
    assert len(records) == 1
    assert results[0]["planned_page_refs"]
    assert results[0]["page_content_refs"] == [records[0]["page_content_record_id"]]
    assert results[0]["target_node"] == "NODE-plan-selection"
    assert results[0]["completion_rule"]["is_complete"] is True
    assert results[0]["reached_node"] == "NODE-plan-selection"
    assert results[0]["node_progress"][-1]["status"] == "matched"
    assert results[0]["action_chain"][0]["click_strategy"] == "js"
    assert results[0]["node_execution_trace"] == []
    assert "field_map" not in results[0]
    assert "selector_map" not in results[0]


def test_explore_runtime_requires_all_agent2_nodes_for_complete_path():
    from e2e_agent.core import page_exploration as runtime

    regression_paths = [
        {
            "path_id": "PATH-001",
            "nodes": ["NODE-start", "NODE-product-detail", "NODE-beneficiary", "NODE-policy-result", "NODE-end"],
            "page_keys": [
                {"node_id": "NODE-product-detail", "page_key": "PK-product-detail", "url_pattern": "/product/detail"},
                {"node_id": "NODE-beneficiary", "page_key": "PK-beneficiary", "url_pattern": "/beneficiary"},
                {"node_id": "NODE-policy-result", "page_key": "PK-result", "url_pattern": "/result"},
            ],
        }
    ]
    pages = [
        {
            "page_key": "result",
            "url": "https://example.com/result",
            "title": "Result",
            "dom_signature": "result-dom",
            "body_text_excerpt": "投保成功 电子保单",
            "field_count": 0,
            "action_count": 0,
            "fields": [],
            "actions": [],
        }
    ]
    catalog = runtime._build_planned_page_catalog(regression_paths)
    records = runtime._build_page_content_records(pages, catalog)
    results = runtime._build_path_exploration_results(
        regression_paths,
        pages,
        [],
        {
            "PATH-001": [
                {
                    "planned_node_id": "NODE-product-detail",
                    "matched": True,
                    "actual_url": "https://example.com/product/detail",
                },
                {
                    "planned_node_id": "NODE-policy-result",
                    "matched": True,
                    "actual_url": "https://example.com/result",
                },
            ]
        },
        catalog,
        records,
    )

    assert results[0]["target_node"] == "NODE-policy-result"
    assert results[0]["path_status"] == "partial"
    assert results[0]["completion_rule"]["is_complete"] is False
    assert "NODE-beneficiary" in results[0]["completion_rule"]["missing_nodes"]


def test_explore_runtime_completion_uses_path_specific_steps_not_global_pages():
    from e2e_agent.core import page_exploration as runtime

    regression_paths = [
        {
            "path_id": "PATH-001",
            "nodes": ["NODE-start", "NODE-product-detail", "NODE-policy-result", "NODE-end"],
            "page_keys": [
                {"node_id": "NODE-product-detail", "page_key": "PK-product-detail", "url_pattern": "/product/detail"},
                {"node_id": "NODE-policy-result", "page_key": "PK-result", "url_pattern": "/policy/result"},
            ],
        }
    ]
    pages = [
        {
            "url": "https://example.com/product/detail",
            "page_key": "product-detail",
            "body_text_excerpt": "产品详情 保费 立即投保",
        },
        {
            "url": "https://example.com/policy/result",
            "page_key": "policy-result",
            "body_text_excerpt": "出单成功 保单结果",
        },
    ]

    results = runtime._build_path_exploration_results(
        regression_paths,
        pages,
        [],
        {
            "PATH-001": [
                {
                    "planned_node_id": "NODE-product-detail",
                    "matched": True,
                    "actual_url": "https://example.com/product/detail",
                }
            ]
        },
        [],
        [],
    )

    assert results[0]["path_status"] == "partial"
    assert results[0]["completion_rule"]["matched_nodes"] == ["NODE-product-detail"]
    assert results[0]["blocked_node"] == "NODE-policy-result"


def test_explore_runtime_reports_observed_trace_and_agent2_alignment():
    from e2e_agent.core import page_exploration as runtime

    regression_paths = [
        {
            "path_id": "PATH-001",
            "nodes": [
                "NODE-start",
                "NODE-product-detail",
                "NODE-applicant-info",
                "NODE-insured-info",
                "NODE-suitability",
                "NODE-health-notice",
                "NODE-policy-result",
                "NODE-end",
            ],
            "page_keys": [],
        }
    ]

    results = runtime._build_path_exploration_results(
        regression_paths,
        [],
        [],
        {
            "PATH-001": [
                {
                    "planned_node_id": "NODE-product-detail",
                    "observed_node_id": "NODE-product-detail",
                    "matched": True,
                    "actual_url": "https://example.com/product/detail",
                },
                {
                    "planned_node_id": "NODE-suitability",
                    "observed_node_id": "NODE-suitability",
                    "matched": True,
                    "actual_url": "https://example.com/product/to-insure",
                },
                {
                    "planned_node_id": "NODE-health-notice",
                    "observed_node_id": "NODE-health-notice",
                    "matched": True,
                    "actual_url": "https://example.com/product/to-insure",
                },
                {
                    "planned_node_id": "NODE-policy-result",
                    "observed_node_id": "NODE-insure-form",
                    "matched": False,
                    "actual_url": "https://example.com/product/insure",
                },
            ]
        },
        [],
        [],
    )

    assert results[0]["observed_node_trace"] == [
        "NODE-product-detail",
        "NODE-suitability",
        "NODE-health-notice",
        "NODE-insure-form",
    ]
    assert results[0]["agent2_alignment"]["plan_mismatch"] is True
    assert results[0]["agent2_alignment"]["matched_nodes"] == [
        "NODE-product-detail",
        "NODE-suitability",
        "NODE-health-notice",
    ]
    assert results[0]["agent2_alignment"]["missing_nodes"] == [
        "NODE-applicant-info",
        "NODE-insured-info",
        "NODE-policy-result",
    ]
    assert results[0]["agent2_alignment"]["suggested_agent2_nodes"] == [
        "NODE-product-detail",
        "NODE-suitability",
        "NODE-health-notice",
        "NODE-insure-form",
        "NODE-policy-result",
    ]


def test_timeout_recovery_snapshot_preserves_current_insure_form_progress():
    from e2e_agent.core import page_exploration as runtime

    regression_paths = [
        {
            "path_id": "PATH-001",
            "nodes": [
                "NODE-start",
                "NODE-product-detail",
                "NODE-health-notice",
                "NODE-insure-form",
                "NODE-policy-result",
                "NODE-end",
            ],
            "page_keys": [
                {"node_id": "NODE-product-detail", "page_key": "PK-detail", "url_pattern": "/product/detail"},
                {"node_id": "NODE-health-notice", "page_key": "PK-health", "url_pattern": "/product/healthInform"},
                {"node_id": "NODE-insure-form", "page_key": "PK-insure", "url_pattern": "/product/insure"},
                {"node_id": "NODE-policy-result", "page_key": "PK-result", "url_pattern": "/result"},
            ],
        }
    ]
    recovered_snapshot = {
        "url": "https://example.test/product/insure?encryptInsureNum=abc",
        "page_key": "product-insure",
        "body_text_excerpt": "投保人信息 被保险人信息 受益人信息 续期缴费信息 提交订单",
        "title": "杰瑞保险",
        "dom_signature": "insure-form-dom",
        "field_count": 2,
        "action_count": 1,
        "fields": [],
        "actions": [],
        "primary_actions": [{"text": "提交订单"}],
        "path_id": "PATH-001",
    }

    snapshots, actions, steps, warnings = runtime._timeout_recovery_attempt(
        path_id="PATH-001",
        path_item=regression_paths[0],
        current_snapshot=recovered_snapshot,
        current_url=recovered_snapshot["url"],
        warning="Path PATH-001 exploration attempt 1/1 timed out after 420s",
        attempt_index=1,
    )

    results = runtime._build_path_exploration_results(
        regression_paths,
        snapshots,
        actions,
        {"PATH-001": steps},
        runtime._build_planned_page_catalog(regression_paths),
        [],
    )

    assert warnings == ["Path PATH-001 exploration attempt 1/1 timed out after 420s"]
    assert snapshots == [recovered_snapshot]
    assert results[0]["path_status"] == "partial"
    assert results[0]["blocked_node"] == "NODE-policy-result"
    assert "timed out" in results[0]["blocked_reason"]
    assert results[0]["completion_rule"]["matched_nodes"] == [
        "NODE-product-detail",
        "NODE-health-notice",
        "NODE-insure-form",
    ]
    assert results[0]["observed_node_trace"] == [
        "NODE-product-detail",
        "NODE-health-notice",
        "NODE-insure-form",
    ]
    assert results[0]["terminal_boundary"]["classification"] == "environment"


def test_explicit_block_uses_blocking_action_target_instead_of_first_missing_node():
    from e2e_agent.core import page_exploration as runtime

    regression_paths = [
        {
            "path_id": "PATH-001",
            "nodes": [
                "NODE-start",
                "NODE-product-detail",
                "NODE-premium-calculation",
                "NODE-suitability",
                "NODE-health-notice",
                "NODE-insure-form",
                "NODE-underwriting",
                "NODE-payment",
                "NODE-policy-result",
                "NODE-end",
            ],
            "page_keys": [],
        }
    ]

    results = runtime._build_path_exploration_results(
        regression_paths,
        [],
        [],
        {
            "PATH-001": [
                {
                    "planned_node_id": "NODE-premium-calculation",
                    "observed_node_id": "NODE-premium-calculation",
                    "matched": True,
                },
                {
                    "planned_node_id": "NODE-health-notice",
                    "observed_node_id": "NODE-health-notice",
                    "matched": True,
                },
                {
                    "planned_node_id": "NODE-insure-form",
                    "observed_node_id": "NODE-insure-form",
                    "matched": True,
                    "blocked_reason": (
                        "Bank card validation loop while waiting submit processing: "
                        "8 recent validations, card/valid=4, bank/card/verif=4"
                    ),
                    "action": {
                        "text": "提交订单",
                        "planned_from_node_id": "NODE-insure-form",
                        "planned_to_node_id": "NODE-underwriting",
                        "click_strategy": "touchscreen-submit-btn",
                    },
                },
            ]
        },
        [],
        [],
    )

    assert results[0]["reached_node"] == "NODE-insure-form"
    assert results[0]["blocked_node"] == "NODE-underwriting"
    assert results[0]["terminal_boundary"]["boundary_node"] == "NODE-underwriting"
    assert results[0]["terminal_boundary"]["classification"] == "blocking"


def test_explore_runtime_marks_agent3_repaired_path_complete():
    from e2e_agent.core import page_exploration as runtime

    regression_paths = [
        {
            "path_id": "PATH-001",
            "nodes": [
                "NODE-start",
                "NODE-product-detail",
                "NODE-applicant-info",
                "NODE-insured-info",
                "NODE-policy-result",
                "NODE-end",
            ],
            "page_keys": [
                {"node_id": "NODE-product-detail", "page_key": "product-detail", "url_pattern": "/product/detail"},
                {"node_id": "NODE-policy-result", "page_key": "policy-result", "url_pattern": "/policy/result"},
            ],
        }
    ]

    results = runtime._build_path_exploration_results(
        regression_paths,
        [],
        [],
        {
            "PATH-001": [
                {
                    "planned_node_id": "NODE-product-detail",
                    "observed_node_id": "NODE-product-detail",
                    "matched": True,
                    "actual_url": "https://example.com/product/detail",
                    "actual_page_key": "product-detail",
                },
                {
                    "planned_node_id": "NODE-insure-form",
                    "observed_node_id": "NODE-insure-form",
                    "matched": True,
                    "dynamic_path_repair": True,
                    "actual_url": "https://example.com/product/insure",
                    "actual_page_key": "insure-form",
                },
                {
                    "planned_node_id": "NODE-policy-result",
                    "observed_node_id": "NODE-policy-result",
                    "matched": True,
                    "actual_url": "https://example.com/policy/result",
                    "actual_page_key": "policy-result",
                },
            ]
        },
        [],
        [],
    )

    result = results[0]
    assert result["path_status"] == "explored"
    assert result["path_repaired"] is True
    assert result["planned_nodes"] == [
        "NODE-product-detail",
        "NODE-applicant-info",
        "NODE-insured-info",
        "NODE-policy-result",
    ]
    assert result["effective_nodes"] == [
        "NODE-product-detail",
        "NODE-insure-form",
        "NODE-policy-result",
    ]
    assert result["completion_rule"]["source"] == "agent3.repaired_path"
    assert result["completion_rule"]["missing_nodes"] == []
    assert [item["node_id"] for item in result["repaired_page_keys"]] == [
        "NODE-product-detail",
        "NODE-insure-form",
        "NODE-policy-result",
    ]


def test_explore_runtime_can_insert_observed_node_into_planned_path():
    from e2e_agent.core import page_exploration as runtime

    planned_nodes = [
        {"node_id": "NODE-product-detail", "page_key": "product-detail", "url_pattern": "/product/detail"},
        {"node_id": "NODE-policy-result", "page_key": "policy-result", "url_pattern": "/policy/result"},
    ]
    warnings: list[str] = []

    index, inserted = runtime._repair_planned_nodes_with_observed(
        planned_nodes,
        1,
        {"url": "https://example.com/product/adapt", "body_text_excerpt": ""},
        path_id="PATH-001",
        warnings=warnings,
    )

    assert inserted is True
    assert index == 1
    assert [item["node_id"] for item in planned_nodes] == [
        "NODE-product-detail",
        "NODE-suitability",
        "NODE-policy-result",
    ]
    assert planned_nodes[1]["source"] == "agent3.dynamic_path_repair"
    assert warnings and "NODE-suitability" in warnings[0]


@pytest.mark.asyncio
async def test_live_exploration_retries_path_until_complete(monkeypatch, tmp_path):
    from e2e_agent.core import page_exploration as runtime

    class FakePage:
        url = "https://example.com/detail"

        async def route(self, *_: object, **__: object) -> None:
            return None

        def on(self, *_: object, **__: object) -> None:
            return None

        async def add_init_script(self, *_: object, **__: object) -> None:
            return None

    class FakeBrowserSession:
        def __init__(self, **_: object) -> None:
            self.page = FakePage()
            pass

        async def __aenter__(self) -> "FakeBrowserSession":
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

    calls = {"count": 0}

    async def fake_drive_planned_path(*_: object, path_item: dict, **__: object):
        calls["count"] += 1
        if calls["count"] < 3:
            steps = [
                {
                    "planned_node_id": "NODE-detail",
                    "matched": True,
                    "actual_url": "https://example.com/detail",
                }
            ]
            actions = [{"text": "下一步"}] if calls["count"] == 2 else []
        else:
            steps = [
                {
                    "planned_node_id": "NODE-detail",
                    "matched": True,
                    "actual_url": "https://example.com/detail",
                },
                {
                    "planned_node_id": "NODE-confirm",
                    "matched": True,
                    "actual_url": "https://example.com/confirm",
                },
            ]
            actions = [{"text": "下一步"}]
        snapshots = [
            {
                "page_key": "detail",
                "url": "https://example.com/detail",
                "title": "Detail",
                "dom_signature": "detail-dom",
                "body_text_excerpt": "详情",
                "field_count": 0,
                "action_count": 0,
                "fields": [],
                "actions": [],
                "candidate_links": [],
                "primary_actions": [],
            }
        ]
        return snapshots, actions, steps, []

    monkeypatch.setattr(runtime, "BrowserSession", FakeBrowserSession)
    monkeypatch.setattr(runtime, "_drive_planned_path", fake_drive_planned_path)

    result = await runtime.run_live_exploration(
        product_id="demo-product",
        entry_url="https://example.com/detail",
        root_dir=tmp_path,
        regression_paths=[
            {
                "path_id": "PATH-001",
                "nodes": ["NODE-start", "NODE-detail", "NODE-confirm", "NODE-end"],
                "page_keys": [],
            }
        ],
        materialise=True,
    )

    contract = result["page_registry"]["exploration_contract"]
    assert calls["count"] == 3
    assert contract["retry_limit"] == 5
    assert contract["completed_paths"][0]["path_id"] == "PATH-001"
    assert contract["blocked_paths"] == []
    assert (tmp_path / "products" / "demo-product" / "agent3" / "explore" / "page-elements.json").exists()
    assert (tmp_path / "products" / "demo-product" / "agent3" / "explore" / "action-trace.json").exists()
    assert (tmp_path / "products" / "demo-product" / "agent3" / "explore" / "main-flow-progress.json").exists()


@pytest.mark.asyncio
async def test_live_exploration_does_not_retry_account_session_boundary(monkeypatch, tmp_path):
    from e2e_agent.core import page_exploration as runtime

    class FakePage:
        url = "https://example.com/detail"

        async def route(self, *_: object, **__: object) -> None:
            return None

        def on(self, *_: object, **__: object) -> None:
            return None

        async def add_init_script(self, *_: object, **__: object) -> None:
            return None

    class FakeBrowserSession:
        def __init__(self, **_: object) -> None:
            self.page = FakePage()

        async def __aenter__(self) -> "FakeBrowserSession":
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

    calls = {"count": 0}
    blocked_reason = (
        "Account/session boundary while waiting submit processing: "
        "3 recent failures, last=/api/apps/customer/customerInsure/query/uid/domain/demo-channel login expired"
    )

    async def fake_drive_planned_path(*_: object, path_item: dict, **__: object):
        calls["count"] += 1
        snapshots = [
            {
                "page_key": "detail",
                "url": "https://example.com/detail",
                "title": "Detail",
                "dom_signature": "detail-dom",
                "body_text_excerpt": "detail",
                "field_count": 0,
                "action_count": 0,
                "fields": [],
                "actions": [],
                "candidate_links": [],
                "primary_actions": [],
            }
        ]
        actions = [{"text": blocked_reason, "click_strategy": "account-session-boundary"}]
        steps = [
            {
                "planned_node_id": "NODE-detail",
                "matched": True,
                "actual_url": "https://example.com/detail",
            },
            {
                "planned_node_id": "NODE-confirm",
                "matched": False,
                "status": "blocked",
                "blocked_reason": blocked_reason,
                "action": actions[0],
            },
        ]
        return snapshots, actions, steps, []

    monkeypatch.setattr(runtime, "BrowserSession", FakeBrowserSession)
    monkeypatch.setattr(runtime, "_drive_planned_path", fake_drive_planned_path)

    result = await runtime.run_live_exploration(
        product_id="test-product",
        entry_url="https://example.com/detail",
        root_dir=tmp_path,
        regression_paths=[
            {
                "path_id": "PATH-001",
                "nodes": ["NODE-start", "NODE-detail", "NODE-confirm", "NODE-end"],
                "page_keys": [],
            }
        ],
        materialise=True,
    )

    contract = result["page_registry"]["exploration_contract"]
    assert calls["count"] == 1
    assert contract["blocked_paths"][0]["blocked_reason"] == blocked_reason
    assert contract["blocked_paths"][0]["terminal_boundary"]["classification"] == "blocking"


@pytest.mark.asyncio
async def test_live_exploration_does_not_retry_page_trace_boundary(monkeypatch, tmp_path):
    from e2e_agent.core import page_exploration as runtime

    class FakePage:
        url = "https://example.com/detail"

        def __init__(self):
            self._agent3_submit_trace_installed = True
            self._agent3_submit_trace = {
                "responses": [
                    {
                        "status": 200,
                        "url": "https://example.test/api/apps/customer/customerInsure/query/uid/domain/demo-channel?md=1",
                        "body": json.dumps({"code": -1, "success": False, "msg": "user not logged in"}),
                    },
                    {
                        "status": 200,
                        "url": "https://example.test/api/apps/customer/customerInsure/query/uid/domain/demo-channel?md=2",
                        "body": json.dumps({"code": -1, "success": False, "msg": "login channel expired"}),
                    },
                    {
                        "status": 200,
                        "url": "https://example.test/api/apps/customer/customerInsure/query/uid/domain/demo-channel?md=3",
                        "body": json.dumps({"code": -1, "success": False, "msg": "login channel expired"}),
                    },
                ]
            }

        async def route(self, *_: object, **__: object) -> None:
            return None

        def on(self, *_: object, **__: object) -> None:
            return None

        async def add_init_script(self, *_: object, **__: object) -> None:
            return None

    class FakeBrowserSession:
        def __init__(self, **_: object) -> None:
            self.page = FakePage()

        async def __aenter__(self) -> "FakeBrowserSession":
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

    calls = {"count": 0}

    async def fake_drive_planned_path(*_: object, path_item: dict, **__: object):
        calls["count"] += 1
        snapshots = [
            {
                "page_key": "detail",
                "url": "https://example.com/detail",
                "title": "Detail",
                "dom_signature": "detail-dom",
                "body_text_excerpt": "detail",
                "field_count": 0,
                "action_count": 0,
                "fields": [],
                "actions": [],
                "candidate_links": [],
                "primary_actions": [],
            }
        ]
        steps = [{"planned_node_id": "NODE-detail", "matched": True, "actual_url": "https://example.com/detail"}]
        return snapshots, [], steps, []

    monkeypatch.setattr(runtime, "BrowserSession", FakeBrowserSession)
    monkeypatch.setattr(runtime, "_drive_planned_path", fake_drive_planned_path)

    await runtime.run_live_exploration(
        product_id="test-product",
        entry_url="https://example.com/detail",
        root_dir=tmp_path,
        regression_paths=[
            {
                "path_id": "PATH-001",
                "nodes": ["NODE-start", "NODE-detail", "NODE-confirm", "NODE-end"],
                "page_keys": [],
            }
        ],
        materialise=False,
    )

    assert calls["count"] == 1


@pytest.mark.asyncio
async def test_live_exploration_reuses_completed_product_to_policy_chain(monkeypatch, tmp_path):
    from e2e_agent.core import page_exploration as runtime

    class FakePage:
        url = "https://example.com/product/detail"

        async def route(self, *_: object, **__: object) -> None:
            return None

        def on(self, *_: object, **__: object) -> None:
            return None

        async def add_init_script(self, *_: object, **__: object) -> None:
            return None

    class FakeBrowserSession:
        def __init__(self, **_: object) -> None:
            self.page = FakePage()

        async def __aenter__(self) -> "FakeBrowserSession":
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

    calls: list[str] = []

    async def fake_drive_planned_path(*_: object, path_item: dict, **__: object):
        calls.append(str(path_item.get("path_id")))
        steps = [
            {
                "planned_node_id": "NODE-product-detail",
                "observed_node_id": "NODE-product-detail",
                "matched": True,
                "actual_url": "https://example.com/product/detail",
                "action": {"path_id": str(path_item.get("path_id")), "text": "立即投保"},
            },
            {
                "planned_node_id": "NODE-insure-form",
                "observed_node_id": "NODE-insure-form",
                "matched": True,
                "actual_url": "https://example.com/product/insure",
                "dynamic_path_repair": True,
                "action": {"path_id": str(path_item.get("path_id")), "text": "提交订单"},
            },
            {
                "planned_node_id": "NODE-policy-result",
                "observed_node_id": "NODE-policy-result",
                "matched": True,
                "actual_url": "https://example.com/result",
                "dynamic_path_repair": True,
            },
        ]
        snapshots = [
            {
                "page_key": "product-detail",
                "url": "https://example.com/product/detail",
                "title": "Detail",
                "dom_signature": "detail-dom",
                "body_text_excerpt": "详情",
                "field_count": 0,
                "action_count": 0,
                "fields": [],
                "actions": [],
                "candidate_links": [],
                "primary_actions": [],
            },
            {
                "page_key": "policy-result",
                "url": "https://example.com/result",
                "title": "Result",
                "dom_signature": "result-dom",
                "body_text_excerpt": "投保成功",
                "field_count": 0,
                "action_count": 0,
                "fields": [],
                "actions": [],
                "candidate_links": [],
                "primary_actions": [],
            },
        ]
        return snapshots, [{"path_id": str(path_item.get("path_id")), "text": "立即投保"}], steps, []

    first_path = {
        "path_id": "PATH-001",
        "nodes": [
            "NODE-start",
            "NODE-product-detail",
            "NODE-premium-calculation",
            "NODE-insure-form",
            "NODE-payment",
            "NODE-policy-result",
            "NODE-end",
        ],
        "page_keys": [
            {"node_id": "NODE-product-detail", "page_key": "PK-detail", "url_pattern": "/product/detail"},
            {"node_id": "NODE-premium-calculation", "page_key": "PK-detail", "url_pattern": "/product/detail"},
            {"node_id": "NODE-insure-form", "page_key": "PK-insure", "url_pattern": "/product/insure"},
            {"node_id": "NODE-payment", "page_key": "PK-payment", "url_pattern": "/payment"},
            {"node_id": "NODE-policy-result", "page_key": "PK-result", "url_pattern": "/result"},
        ],
    }
    second_path = {
        "path_id": "PATH-002",
        "nodes": [
            "NODE-start",
            "NODE-product-detail",
            "NODE-suitability",
            "NODE-insure-form",
            "NODE-tax-info",
            "NODE-policy-result",
            "NODE-end",
        ],
        "page_keys": [
            {"node_id": "NODE-product-detail", "page_key": "PK-detail", "url_pattern": "/product/detail"},
            {"node_id": "NODE-suitability", "page_key": "PK-adapt", "url_pattern": "/product/adapt"},
            {"node_id": "NODE-insure-form", "page_key": "PK-insure", "url_pattern": "/product/insure"},
            {"node_id": "NODE-tax-info", "page_key": "PK-tax", "url_pattern": "/tax"},
            {"node_id": "NODE-policy-result", "page_key": "PK-result", "url_pattern": "/result"},
        ],
    }

    monkeypatch.setattr(runtime, "BrowserSession", FakeBrowserSession)
    monkeypatch.setattr(runtime, "_drive_planned_path", fake_drive_planned_path)

    result = await runtime.run_live_exploration(
        product_id="demo-product",
        entry_url="https://example.com/product/detail",
        root_dir=tmp_path,
        regression_paths=[first_path, second_path],
        materialise=True,
    )

    path_results = result["page_registry"]["path_exploration_results"]
    assert calls == ["PATH-001"]
    assert [item["path_status"] for item in path_results] == ["explored", "explored"]
    assert path_results[1]["planned_steps"][0]["reused_from_path_id"] == "PATH-001"


def test_explore_runtime_builds_blocked_path_contract():
    from e2e_agent.core import page_exploration as runtime

    contract = runtime._build_exploration_contract(
        [
            {
                "path_id": "PATH-001",
                "path_status": "blocked",
                "target_node": "NODE-confirm",
                "blocked_node": "NODE-confirm",
                "blocked_reason": "No executable primary action found for Agent2 planned path",
                "completion_rule": {
                    "required_nodes": ["NODE-detail", "NODE-confirm"],
                    "matched_nodes": ["NODE-detail"],
                    "missing_nodes": ["NODE-confirm"],
                    "is_complete": False,
                },
            }
        ]
    )

    assert contract["policy"] == "complete_paths_only_enter_agent4"
    assert contract["retry_limit"] == 5
    assert contract["blocked_paths"][0]["path_id"] == "PATH-001"
    assert contract["blocked_paths"][0]["missing_nodes"] == ["NODE-confirm"]


async def test_standard_underwriting_41011_converges_without_timeout(monkeypatch):
    from e2e_agent.core import page_exploration as runtime

    class FakePage:
        url = "https://example.test/authentication?encryptInsureNum=abc"

        def __init__(self):
            self.waits: list[int] = []

        async def wait_for_timeout(self, timeout_ms):
            self.waits.append(timeout_ms)

    async def fake_direct_next_task(page):
        return {
            "json": {
                "code": 41011,
                "success": False,
                "msg": "系统错误",
                "data": {"taskType": 4, "taskStatus": 0, "isNext": 1},
            }
        }

    page = FakePage()
    monkeypatch.setattr(runtime, "_direct_next_task", fake_direct_next_task)
    times = iter([0, 0, 301])
    monkeypatch.setattr(runtime, "time", types.SimpleNamespace(monotonic=lambda: next(times)))

    actions = await runtime._poll_standard_underwriting_task(
        page,
        response_record={
            "text": "后续任务接口: code=41011, success=False, taskType=4, taskStatus=0, isNext=1, msg=系统错误"
        },
        source_url=page.url,
    )

    assert page.waits == [12_000]
    assert not any(action["text"] == "standard-underwriting-poll-timeout" for action in actions)
    blocker = actions[-1]
    assert blocker["click_strategy"] == "standard-underwriting-backend-blocked"
    assert "taskType=4" in blocker["text"]
    assert "41011" in blocker["text"]


async def test_processing_overlay_stops_on_repeated_backend_unavailable(monkeypatch):
    from e2e_agent.core import page_exploration as runtime

    class FakePage:
        url = "https://example.test/product/insure?encryptInsureNum=abc"

        def __init__(self):
            self.waits: list[int] = []
            self._agent3_submit_trace = {
                "responses": [
                    {
                        "status": 200,
                        "url": "https://example.test/api/apps/cps/product/confirmItem/query?md=1",
                        "body": json.dumps(
                            {
                                "code": -1,
                                "success": False,
                                "msg": "request failed",
                                "exception": "Read timed out",
                            }
                        ),
                    },
                    {
                        "status": 200,
                        "url": "https://example.test/api/apps/cps/product/confirmItem/query?md=2",
                        "body": json.dumps(
                            {
                                "code": -1,
                                "success": False,
                                "msg": "request failed",
                                "exception": "Load balancer does not have available server",
                            }
                        ),
                    },
                    {
                        "status": 200,
                        "url": "https://example.test/api/apps/cps/product/confirmItem/query?md=3",
                        "body": json.dumps(
                            {
                                "code": -1,
                                "success": False,
                                "msg": "request failed",
                                "exception": "Load balancer does not have available server",
                            }
                        ),
                    },
                ]
            }

        async def wait_for_timeout(self, timeout_ms):
            self.waits.append(timeout_ms)

    async def fake_snapshot(page, entry_url):
        return {
            "url": page.url,
            "body_text_excerpt": "processing",
            "page_state": {"blocking_overlays": [{"text": "processing"}]},
        }

    page = FakePage()
    monkeypatch.setattr(runtime, "_snapshot_page", fake_snapshot)

    _snapshot, reason = await runtime._wait_for_policy_result_after_processing_overlay(
        page,
        entry_url="https://example.test/product/insure",
        target_node_id="NODE-policy-result",
        timeout_ms=2_000,
    )

    assert page.waits == []
    assert reason
    assert "Backend/API unavailable" in reason
    assert "confirmItem/query" in reason


def test_backend_unavailable_trips_after_single_connection_refused(monkeypatch, tmp_path):
    from e2e_agent.core import page_exploration as runtime

    errors_path = tmp_path / "api-errors.jsonl"
    response = {
        "event": "response",
        "status": 200,
        "url": "https://example.test/api/apps/cps/product/confirmItem/query?md=1",
        "body": json.dumps(
            {
                "code": -1,
                "success": False,
                "msg": "request failed",
                "exception": "Connection refused (Connection refused)",
            }
        ),
    }
    errors_path.write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("AGENT3_RUN_DIR", str(tmp_path))

    page = types.SimpleNamespace(url="https://example.test/product/task?encryptInsureNum=abc")
    reason = runtime._submit_trace_blocker_reason(page)

    assert reason
    assert "Backend/API unavailable" in reason
    assert "1 recent failures" in reason
    assert "confirmItem/query" in reason


def test_backend_unavailable_does_not_leak_into_health_notice(monkeypatch, tmp_path):
    from e2e_agent.core import page_exploration as runtime

    errors_path = tmp_path / "api-errors.jsonl"
    response = {
        "event": "response",
        "status": 200,
        "url": "https://example.test/api/apps/cps/product/confirmItem/query?md=1",
        "body": json.dumps(
            {
                "code": -1,
                "success": False,
                "msg": "request failed",
                "exception": "Connection refused (Connection refused)",
            }
        ),
    }
    errors_path.write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("AGENT3_RUN_DIR", str(tmp_path))

    page = types.SimpleNamespace(url="https://example.test/product/healthInform?encryptInsureNum=abc")

    assert runtime._submit_trace_blocker_reason(page) is None


def test_backend_unavailable_does_not_leak_into_product_insure(monkeypatch, tmp_path):
    from e2e_agent.core import page_exploration as runtime

    errors_path = tmp_path / "api-errors.jsonl"
    response = {
        "event": "response",
        "status": 200,
        "url": "https://example.test/api/apps/cps/product/confirmItem/query?md=1",
        "body": json.dumps(
            {
                "code": -1,
                "success": False,
                "msg": "request failed",
                "exception": "Connection refused (Connection refused)",
            }
        ),
    }
    errors_path.write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("AGENT3_RUN_DIR", str(tmp_path))

    page = types.SimpleNamespace(url="https://example.test/product/insure?encryptInsureNum=abc")

    assert runtime._submit_trace_blocker_reason(page) is None


def test_health_notify_failure_stops_as_non_retryable_boundary(monkeypatch, tmp_path):
    from e2e_agent.core import page_exploration as runtime

    errors_path = tmp_path / "api-errors.jsonl"
    response = {
        "event": "response",
        "status": 200,
        "url": "https://example.test/api/apps/cps/healthnotify/query/by/trial?md=1",
        "body": json.dumps(
            {
                "code": -1,
                "success": False,
                "msg": json.dumps({"code": -1, "msg": "健康告知获取失败!", "success": False}, ensure_ascii=False),
            },
            ensure_ascii=False,
        ),
    }
    errors_path.write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("AGENT3_RUN_DIR", str(tmp_path))

    page = types.SimpleNamespace(url="https://example.test/product/insure?encryptInsureNum=abc")
    reason = runtime._submit_trace_blocker_reason(page)

    assert reason
    assert "Health notice boundary" in reason
    assert "healthnotify/query/by/trial" in reason
    assert runtime._submit_trace_blocker_strategy(reason) == "health-notice-boundary"
    assert runtime._non_retryable_trace_boundary_reason(
        [],
        [{"text": reason, "click_strategy": "health-notice-boundary"}],
    ) == reason


def test_repeated_bank_card_validation_on_insure_form_stops_as_boundary():
    from e2e_agent.core import page_exploration as runtime

    responses = []
    for index in range(3):
        responses.extend(
            [
                {
                    "status": 200,
                    "url": (
                        "https://example.test/api/apps/cps/product/insure/card/valid"
                        "?cardNum=6217002352508817050"
                    ),
                    "body": json.dumps({"code": 0, "success": True, "data": {"valid": True}}),
                },
                {
                    "status": 200,
                    "url": "https://example.test/api/apps/cps/product/insure/card/valid?cardNum=",
                    "body": json.dumps({"code": 0, "success": True, "data": {"valid": True}}),
                },
                {
                    "status": 200,
                    "url": "https://example.test/api/apps/cps/pay/bank/card/verif",
                    "body": json.dumps({"code": 0, "success": True, "data": {"cardNum": ""}}),
                },
            ]
        )

    page = types.SimpleNamespace(
        url="https://example.test/product/insure?encryptInsureNum=abc",
        _agent3_submit_trace={"responses": responses},
    )

    reason = runtime._submit_trace_blocker_reason(
        page,
        allow_product_form_backend=True,
        allow_product_form_session=True,
    )

    assert reason
    assert "Bank card validation loop" in reason
    assert "card/valid" in reason
    assert runtime._submit_trace_blocker_strategy(reason) == "bank-card-validation-loop"
    assert runtime._non_retryable_trace_boundary_reason(
        [],
        [{"text": reason, "click_strategy": "bank-card-validation-loop"}],
    ) == reason


def test_repeated_filled_bank_card_validation_without_submit_stops_as_boundary():
    from e2e_agent.core import page_exploration as runtime

    responses = []
    for index in range(5):
        responses.extend(
            [
                {
                    "status": 200,
                    "url": (
                        "https://example.test/api/apps/cps/product/insure/card/valid"
                        f"?cardNum=62170023525088170{index}"
                    ),
                    "body": json.dumps({"code": 0, "success": True, "data": {"valid": True}}),
                },
                {
                    "status": 200,
                    "url": "https://example.test/api/apps/cps/pay/bank/card/verif",
                    "body": json.dumps(
                        {
                            "code": 0,
                            "success": True,
                            "data": {"cardNum": f"62170023525088170{index}", "valid": True},
                        }
                    ),
                },
            ]
        )

    page = types.SimpleNamespace(
        url="https://example.test/product/insure?encryptInsureNum=abc",
        _agent3_submit_trace={"responses": responses},
    )

    reason = runtime._submit_trace_blocker_reason(
        page,
        allow_product_form_backend=True,
        allow_product_form_session=True,
    )

    assert reason
    assert "Bank card validation loop" in reason
    assert "empty_cardNum=0" in reason
    assert "filled_cardNum=10" in reason
    assert runtime._submit_trace_blocker_strategy(reason) == "bank-card-validation-loop"


def test_bank_card_validation_loop_is_cleared_by_later_submit_response():
    from e2e_agent.core import page_exploration as runtime

    responses = []
    for index in range(5):
        responses.extend(
            [
                {
                    "status": 200,
                    "url": (
                        "https://example.test/api/apps/cps/product/insure/card/valid"
                        f"?cardNum=62170023525088170{index}"
                    ),
                    "body": json.dumps({"code": 0, "success": True, "data": {"valid": True}}),
                },
                {
                    "status": 200,
                    "url": "https://example.test/api/apps/cps/pay/bank/card/verif",
                    "body": json.dumps(
                        {
                            "code": 0,
                            "success": True,
                            "data": {"cardNum": f"62170023525088170{index}", "valid": True},
                        }
                    ),
                },
            ]
        )
    responses.append(
        {
            "status": 200,
            "url": "https://example.test/api/apps/cps/insure/submit?md=1",
            "body": json.dumps(
                {
                    "code": 37009,
                    "success": False,
                    "data": {"encryptInsureNum": "enc", "insureNum": "100000000000", "insureTaskList": []},
                }
            ),
        }
    )

    page = types.SimpleNamespace(
        url="https://example.test/product/insure?encryptInsureNum=abc",
        _agent3_submit_trace={"responses": responses},
    )

    assert (
        runtime._submit_trace_blocker_reason(
            page,
            allow_product_form_backend=True,
            allow_product_form_session=True,
        )
        is None
    )


@pytest.mark.asyncio
async def test_direct_submit_after_bank_validation_loop_records_submit_result():
    from e2e_agent.core import page_exploration as runtime

    class FakePage:
        url = "https://example.test/product/insure?encryptInsureNum=abc"

        def __init__(self):
            self._agent3_submit_trace = {
                "responses": [
                    {
                        "status": 200,
                        "url": (
                            "https://example.test/api/apps/cps/product/insure/card/valid"
                            f"?cardNum=62170023525088170{index}"
                        ),
                        "body": json.dumps({"code": 0, "success": True, "data": {"valid": True}}),
                    }
                    if index % 2 == 0
                    else {
                        "status": 200,
                        "url": "https://example.test/api/apps/cps/pay/bank/card/verif",
                        "body": json.dumps(
                            {
                                "code": 0,
                                "success": True,
                                "data": {"cardNum": f"62170023525088170{index}", "valid": True},
                            }
                        ),
                    }
                    for index in range(10)
                ]
            }
            self.gotos: list[str] = []

        async def evaluate(self, *_args, **_kwargs):
            return {
                "attempted": True,
                "order_generated": True,
                "task_handoff": True,
                "direct_order": False,
                "suitability_task": False,
                "status": 200,
                "ok": True,
                "url": "https://example.test/api/apps/cps/insure/submit?md=1",
                "code": "37009",
                "payload_summary": {"encryptInsureNum": "enc", "modules": ["10", "20", "107"]},
                "response_order": {"encryptInsureNum": "enc", "insureNum": "100000000000"},
                "body_excerpt": "{\"code\":37009}",
            }

        async def goto(self, url, **_kwargs):
            self.gotos.append(url)
            self.url = url

        async def wait_for_timeout(self, *_args, **_kwargs):
            return None

    page = FakePage()

    action = await runtime._direct_submit_after_bank_validation_loop(
        page,
        source_url=page.url,
    )

    assert action
    assert action["click_strategy"] == "direct-submit-after-bank-validation-loop"
    assert action["selector"] == "/api/apps/cps/insure/submit"
    assert action["submit_api_result"]["code"] == "37009"
    assert page.gotos == ["https://example.test/product/task?encryptInsureNum=enc"]
    assert runtime._submit_trace_blocker_reason(
        page,
        allow_product_form_backend=True,
        allow_product_form_session=True,
    ) is None


@pytest.mark.asyncio
async def test_direct_submit_runs_after_policy_start_date_window_toast():
    from e2e_agent.core import page_exploration as runtime

    class FakePage:
        url = "https://example.test/product/insure?encryptInsureNum=abc"

        def __init__(self):
            self._agent3_submit_trace = {"responses": []}
            self.gotos: list[str] = []

        async def evaluate(self, *_args, **_kwargs):
            return {
                "attempted": True,
                "order_generated": True,
                "task_handoff": False,
                "direct_order": False,
                "suitability_task": True,
                "status": 200,
                "ok": True,
                "url": "https://example.test/api/apps/cps/insure/submit?md=1",
                "code": "40015",
                "payload_summary": {
                    "encryptInsureNum": "enc",
                    "modules": ["10", "20", "102", "107"],
                    "retryReason": "policy-start-date-window",
                },
                "response_order": {"encryptInsureNum": "enc"},
                "body_excerpt": "{\"code\":40015}",
            }

        async def goto(self, url, **_kwargs):
            self.gotos.append(url)
            self.url = url

        async def wait_for_timeout(self, *_args, **_kwargs):
            return None

    diagnostics = [
        {
            "phase": "after-click",
            "state": {
                "bodyText": "起保日期 2026-06-15",
                "popups": [{"text": "[起保日期]可选区间为2026-06-09至2026-06-09"}],
            },
        }
    ]
    page = FakePage()

    action = await runtime._direct_submit_after_bank_validation_loop(
        page,
        source_url=page.url,
        submit_diagnostics=diagnostics,
    )

    assert action
    assert action["click_strategy"] == "direct-submit-after-bank-validation-loop"
    assert action["submit_api_result"]["code"] == "40015"
    assert action["blocked_reason_before_direct_submit"].startswith("Policy start date window")
    assert page.gotos == ["https://example.test/product/adapt/loading?encryptInsureNum=enc"]


def test_submit_trace_suitability_handoff_becomes_agent4_order_boundary():
    from e2e_agent.core import page_exploration as runtime

    regression_paths = [
        {
            "path_id": "PATH-001",
            "nodes": [
                "NODE-start",
                "NODE-product-detail",
                "NODE-premium-calculation",
                "NODE-suitability",
                "NODE-health-notice",
                "NODE-insure-form",
                "NODE-underwriting",
                "NODE-risk-control",
                "NODE-payment",
                "NODE-policy-result",
                "NODE-end",
            ],
            "page_keys": [],
        }
    ]
    submit_action = {
        "path_id": "PATH-001",
        "text": "submit trace order handoff: code=40015, suitability_task=True",
        "tag": "xhr",
        "selector": "/api/apps/cps/insure/submit",
        "source_url": "https://example.test/product/insure?encryptInsureNum=old",
        "target_url": "https://example.test/product/adapt/loading?encryptInsureNum=enc",
        "planned_from_node_id": "NODE-insure-form",
        "planned_to_node_id": "NODE-suitability",
        "click_strategy": "submit-trace-order-handoff",
        "action_type": "submit_api",
        "submit_api_result": {
            "attempted": True,
            "order_generated": True,
            "task_handoff": False,
            "direct_order": False,
            "suitability_task": True,
            "status": 200,
            "ok": True,
            "url": "https://example.test/api/apps/cps/insure/submit?md=1",
            "code": "40015",
            "msg": "need suitability",
            "response_order": {"encryptInsureNum": "enc", "insureNum": "20260609000001"},
            "body_excerpt": "{\"code\":40015}",
        },
    }

    results = runtime._build_path_exploration_results(
        regression_paths,
        [],
        [submit_action],
        {
            "PATH-001": [
                {
                    "planned_node_id": "NODE-product-detail",
                    "observed_node_id": "NODE-product-detail",
                    "matched": True,
                },
                {
                    "planned_node_id": "NODE-premium-calculation",
                    "observed_node_id": "NODE-premium-calculation",
                    "matched": True,
                },
                {
                    "planned_node_id": "NODE-suitability",
                    "observed_node_id": "NODE-suitability",
                    "matched": True,
                },
                {
                    "planned_node_id": "NODE-health-notice",
                    "observed_node_id": "NODE-health-notice",
                    "matched": True,
                },
                {
                    "planned_node_id": "NODE-insure-form",
                    "observed_node_id": "NODE-insure-form",
                    "matched": True,
                    "actual_url": "https://example.test/product/insure?encryptInsureNum=old",
                    "actual_page_key": "product-insure",
                    "status": "action_progress",
                    "action": submit_action,
                },
            ]
        },
        [],
        [],
    )

    path = results[0]
    assert path["path_status"] == "explored"
    assert path["completion_rule"]["order_generation_boundary"] is True
    assert path["completion_rule"]["missing_nodes"] == []
    assert path["action_chain"][0]["action_type"] == "submit_api"
    assert path["action_chain"][0]["submit_api_result"]["code"] == "40015"


def test_submit_trace_order_handoff_action_is_recovered_from_api_trace():
    from e2e_agent.core import page_exploration as runtime

    class FakePage:
        url = "https://example.test/product/insure?encryptInsureNum=old"

        def __init__(self):
            self._agent3_submit_trace = {
                "responses": [
                    {
                        "status": 200,
                        "url": "https://example.test/api/apps/cps/insure/submit?md=1",
                        "content_type": "application/json",
                        "body": json.dumps(
                            {
                                "code": 40015,
                                "success": False,
                                "msg": "need suitability",
                                "data": {
                                    "errorCode": "40015",
                                    "errorMessage": "need suitability",
                                    "insureNum": 20260609000001,
                                    "encryptInsureNum": "enc",
                                    "insureTaskList": [{"taskType": 9}],
                                },
                            }
                        ),
                    }
                ]
            }

    action = runtime._submit_trace_order_handoff_action(
        FakePage(),
        source_url="https://example.test/product/insure?encryptInsureNum=old",
    )

    assert action
    assert action["action_type"] == "submit_api"
    assert action["click_strategy"] == "submit-trace-order-handoff"
    assert action["planned_from_node_id"] == "NODE-insure-form"
    assert action["planned_to_node_id"] == "NODE-suitability"
    assert action["target_url"] == "https://example.test/product/adapt/loading?encryptInsureNum=enc"
    assert action["submit_api_result"]["order_generated"] is True
    assert action["submit_api_result"]["suitability_task"] is True


def test_submit_trace_order_handoff_action_is_recovered_from_run_trace_file(tmp_path, monkeypatch):
    from e2e_agent.core import page_exploration as runtime

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "api-trace.jsonl").write_text(
        json.dumps(
            {
                "event": "response",
                "status": 200,
                "url": "https://example.test/api/apps/cps/insure/submit?md=1",
                "body": json.dumps(
                    {
                        "code": 40015,
                        "success": False,
                        "data": {
                            "errorCode": "40015",
                            "insureNum": 20260609000001,
                            "encryptInsureNum": "enc",
                            "insureTaskList": [{"taskType": 9}],
                        },
                    }
                ),
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT3_RUN_DIR", str(run_dir))

    class FakePage:
        url = "https://example.test/product/insure?encryptInsureNum=old"

    action = runtime._submit_trace_order_handoff_action(
        FakePage(),
        source_url="https://example.test/product/insure?encryptInsureNum=old",
    )

    assert action
    assert action["click_strategy"] == "submit-trace-order-handoff"
    assert action["target_url"] == "https://example.test/product/adapt/loading?encryptInsureNum=enc"
    assert action["submit_api_result"]["code"] == "40015"


def test_submit_click_tries_browser_api_submit_before_bank_loop_boundary_return():
    from e2e_agent.core import page_exploration as runtime

    source = inspect.getsource(runtime._click_primary_action)
    submit_branch = source[source.index("if is_submit_action:") : source.index("    if compact_text in")]

    assert "_direct_submit_after_bank_validation_loop(" in submit_branch
    assert submit_branch.index("_direct_submit_after_bank_validation_loop(") < submit_branch.index(
        "if page.url != before_url or dismissed_overlays:"
    )


def test_live_exploration_attempts_direct_submit_before_path_level_bank_loop_boundary():
    from e2e_agent.core import page_exploration as runtime

    source = inspect.getsource(runtime.run_live_exploration)
    blocker_block = source[source.index("if page_trace_blocker_reason:") : source.index("for step in next_steps:")]

    assert "page_trace_blocker_reason.startswith(\"Bank card validation loop\")" in blocker_block
    assert "_direct_submit_after_bank_validation_loop(" in blocker_block
    assert blocker_block.index("_direct_submit_after_bank_validation_loop(") < blocker_block.index(
        "boundary_action = _submit_trace_blocker_action("
    )
    assert "direct_submit_action.update({**path_context, \"exploration_attempt\": attempt_index})" in blocker_block
    assert "_snapshot_page(session.page, entry_url)" in blocker_block


def test_direct_submit_payload_aligns_bank_module_with_native_submit_shape():
    from e2e_agent.core import page_exploration as runtime

    source = inspect.getsource(runtime._direct_submit_after_bank_validation_loop)

    assert "payload.autoRenewal = payload.autoRenewal === true" in source
    assert "payload.renewalCheck = 0;" in source
    assert "delete payload.data['107'];" not in source
    assert "delete payload.data[107];" not in source
    assert "normalizeBankNameForSubmit" in source
    assert "bankRow.bankName = bankName;" in source
    assert "insuredRow.addressIsSameApplicant = insuredRow.addressIsSameApplicant ?? '';" in source
    assert "payload.price = submitPriceFromTrialGenes(payload.trialGenes) || payload.price;" in source
    assert "payload.isEmptyData = !hasSubmitData;" not in source
    assert source.index("alignNativeSubmitPayload(payload);") < source.index(
        "let submitResult = await submitPayload(payload);"
    )
    assert "const response = await fetch(`/api/apps/cps/insure/submit" in source
    assert "const retryDate = extractAllowedStartDate(text);" in source
    assert "setPayloadStartDate(payload, retryDate);" in source
    assert "retry_reason: 'policy-start-date-window'" in source
    assert source.count("fetch(`/api/apps/cps/insure/submit") >= 1


def test_mocked_health_notify_failure_does_not_stop_exploration(monkeypatch, tmp_path):
    from e2e_agent.core import page_exploration as runtime

    errors_path = tmp_path / "api-errors.jsonl"
    responses = [
        {
            "event": "response",
            "status": 200,
            "url": "https://example.test/api/apps/cps/healthnotify/query/by/trial?md=1",
            "body": json.dumps(
                {
                    "code": -1,
                    "success": False,
                    "msg": json.dumps({"code": -1, "msg": "健康告知获取失败!", "success": False}, ensure_ascii=False),
                },
                ensure_ascii=False,
            ),
            "mocked_health_notify_failure": True,
        }
    ]
    errors_path.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in responses), encoding="utf-8")
    monkeypatch.setenv("AGENT3_RUN_DIR", str(tmp_path))

    page = types.SimpleNamespace(
        url="https://example.test/product/insure?encryptInsureNum=abc",
        _agent3_submit_trace={"responses": responses},
    )

    assert runtime._submit_trace_blocker_reason(page) is None


def test_submit_trace_session_boundary_is_ignored_after_payment_handoff():
    from e2e_agent.core import page_exploration as runtime

    page = types.SimpleNamespace(
        url="https://example.test/m/demo-channel/pay/?id=abc",
        _agent3_submit_trace={
            "responses": [
                {
                    "status": 200,
                    "url": "https://example.test/api/apps/customer/customerInsure/query/uid/domain/demo-channel?md=1",
                    "body": json.dumps({"code": -1, "success": False, "msg": "user not logged in"}),
                },
                {
                    "status": 200,
                    "url": "https://example.test/api/apps/customer/customerInsure/query/uid/domain/demo-channel?md=2",
                    "body": json.dumps({"code": -1, "success": False, "msg": "login channel expired"}),
                },
                {
                    "status": 200,
                    "url": "https://example.test/api/apps/customer/customerInsure/query/uid/domain/demo-channel?md=3",
                    "body": json.dumps({"code": -1, "success": False, "msg": "login channel expired"}),
                },
            ]
        },
    )

    assert runtime._submit_trace_blocker_reason(page) is None


async def test_processing_overlay_stops_on_repeated_session_expired(monkeypatch):
    from e2e_agent.core import page_exploration as runtime

    class FakePage:
        url = "https://example.test/product/insure?encryptInsureNum=abc"

        def __init__(self):
            self.waits: list[int] = []
            self._agent3_submit_trace = {
                "responses": [
                    {
                        "status": 200,
                        "url": "https://example.test/api/apps/customer/customerInsure/query/uid/domain/demo-channel?md=1",
                        "body": json.dumps({"code": -1, "success": False, "msg": "user not logged in"}),
                    },
                    {
                        "status": 200,
                        "url": "https://example.test/api/apps/customer/customerInsure/query/uid/domain/demo-channel?md=2",
                        "body": json.dumps({"code": -1, "success": False, "msg": "login channel expired"}),
                    },
                    {
                        "status": 200,
                        "url": "https://example.test/api/apps/customer/customerInsure/query/uid/domain/demo-channel?md=3",
                        "body": json.dumps({"code": -1, "success": False, "msg": "login channel expired"}),
                    },
                ]
            }

        async def wait_for_timeout(self, timeout_ms):
            self.waits.append(timeout_ms)

    async def fake_snapshot(page, entry_url):
        return {
            "url": page.url,
            "body_text_excerpt": "processing",
            "page_state": {"blocking_overlays": [{"text": "processing"}]},
        }

    page = FakePage()
    monkeypatch.setattr(runtime, "_snapshot_page", fake_snapshot)

    _snapshot, reason = await runtime._wait_for_policy_result_after_processing_overlay(
        page,
        entry_url="https://example.test/product/insure",
        target_node_id="NODE-policy-result",
        timeout_ms=2_000,
    )

    assert page.waits == []
    assert reason
    assert "Account/session boundary" in reason
    assert "customerInsure/query" in reason


async def test_bank_sign_wait_stops_on_repeated_session_expired(monkeypatch):
    from e2e_agent.core import page_exploration as runtime

    class FakePage:
        url = "https://example.test/product/task?encryptInsureNum=abc"

        def __init__(self):
            self.waits: list[int] = []
            self._agent3_submit_trace = {
                "responses": [
                    {
                        "status": 200,
                        "url": "https://example.test/api/apps/customer/customerInsure/query/uid/domain/demo-channel?md=1",
                        "body": json.dumps({"code": -1, "success": False, "msg": "user not logged in"}),
                    },
                    {
                        "status": 200,
                        "url": "https://example.test/api/apps/customer/customerInsure/query/uid/domain/demo-channel?md=2",
                        "body": json.dumps({"code": -1, "success": False, "msg": "login channel expired"}),
                    },
                    {
                        "status": 200,
                        "url": "https://example.test/api/apps/customer/customerInsure/query/uid/domain/demo-channel?md=3",
                        "body": json.dumps({"code": -1, "success": False, "msg": "login channel expired"}),
                    },
                ]
            }

        async def wait_for_timeout(self, timeout_ms):
            self.waits.append(timeout_ms)

    async def fake_body_text(page):
        return "bank sign task"

    async def fake_dialog_state(page):
        return {"visible": False, "text": "bank sign task"}

    async def fake_click_auth_button(*args, **kwargs):
        return None

    page = FakePage()
    monkeypatch.setattr(runtime, "_body_text_full", fake_body_text)
    monkeypatch.setattr(runtime, "_bank_sign_dialog_state", fake_dialog_state)
    monkeypatch.setattr(runtime, "_bank_sign_hint", lambda url, text: True)
    monkeypatch.setattr(runtime, "_click_auth_button", fake_click_auth_button)
    times = iter([0, 0, 91])
    monkeypatch.setattr(runtime, "time", types.SimpleNamespace(monotonic=lambda: next(times)))

    actions = await runtime._apply_bank_sign_task_data(page)

    assert page.waits == []
    assert actions
    assert actions[-1]["click_strategy"] == "account-session-boundary"
    assert "Account/session boundary" in actions[-1]["text"]


async def test_wait_after_task_next_click_stops_on_repeated_session_expired(monkeypatch):
    from e2e_agent.core import page_exploration as runtime

    class FakePage:
        url = "https://example.test/product/task?encryptInsureNum=abc"

        def __init__(self):
            self.waits: list[int] = []
            self._agent3_submit_trace = {
                "responses": [
                    {
                        "status": 200,
                        "url": "https://example.test/api/apps/customer/customerInsure/query/uid/domain/demo-channel?md=1",
                        "body": json.dumps({"code": -1, "success": False, "msg": "user not logged in"}),
                    },
                    {
                        "status": 200,
                        "url": "https://example.test/api/apps/customer/customerInsure/query/uid/domain/demo-channel?md=2",
                        "body": json.dumps({"code": -1, "success": False, "msg": "login channel expired"}),
                    },
                    {
                        "status": 200,
                        "url": "https://example.test/api/apps/customer/customerInsure/query/uid/domain/demo-channel?md=3",
                        "body": json.dumps({"code": -1, "success": False, "msg": "login channel expired"}),
                    },
                ]
            }

        async def wait_for_timeout(self, timeout_ms):
            self.waits.append(timeout_ms)

    async def fake_page_flow_settled(*args, **kwargs):
        raise AssertionError("should not wait for page flow after a trace blocker")

    page = FakePage()
    monkeypatch.setattr(runtime, "_wait_for_page_flow_settled", fake_page_flow_settled)

    actions = await runtime._wait_after_task_next_click(
        page,
        None,
        source_url=page.url,
        click_strategy="bank-sign-next-task-response",
        timeout_ms=60_000,
    )

    assert page.waits == []
    assert actions
    assert actions[-1]["click_strategy"] == "account-session-boundary"
    assert "Account/session boundary" in actions[-1]["text"]


async def test_page_flow_settle_stops_on_repeated_session_expired(monkeypatch):
    from e2e_agent.core import page_exploration as runtime

    class FakePage:
        url = "https://example.test/product/task?encryptInsureNum=abc"

        def __init__(self):
            self.waits: list[int] = []
            self._agent3_submit_trace = {
                "responses": [
                    {
                        "status": 200,
                        "url": "https://example.test/api/apps/customer/customerInsure/query/uid/domain/demo-channel?md=1",
                        "body": json.dumps({"code": -1, "success": False, "msg": "user not logged in"}),
                    },
                    {
                        "status": 200,
                        "url": "https://example.test/api/apps/customer/customerInsure/query/uid/domain/demo-channel?md=2",
                        "body": json.dumps({"code": -1, "success": False, "msg": "login channel expired"}),
                    },
                    {
                        "status": 200,
                        "url": "https://example.test/api/apps/customer/customerInsure/query/uid/domain/demo-channel?md=3",
                        "body": json.dumps({"code": -1, "success": False, "msg": "login channel expired"}),
                    },
                ]
            }

        async def wait_for_timeout(self, timeout_ms):
            self.waits.append(timeout_ms)

    page = FakePage()

    settled = await runtime._wait_for_page_flow_settled(
        page,
        previous_url=page.url,
        timeout_ms=60_000,
        min_wait_ms=1_200,
    )

    assert settled is True
    assert page.waits == []


async def test_page_flow_settle_stops_on_repeated_page_error():
    from e2e_agent.core import page_exploration as runtime

    class FakePage:
        url = "https://example.test/product/detail?prodId=123602"

        def __init__(self):
            self.waits: list[int] = []
            self._agent3_submit_trace = {
                "pageerrors": [
                    {
                        "text": "Cannot read properties of undefined (reading '0')",
                        "stack": "TypeError at /pages/product/detail.js:1:130634",
                    },
                    {
                        "text": "Cannot read properties of undefined (reading '0')",
                        "stack": "TypeError at /pages/product/detail.js:1:130634",
                    },
                    {
                        "text": "Cannot read properties of undefined (reading '0')",
                        "stack": "TypeError at /pages/product/detail.js:1:130634",
                    },
                ]
            }

        async def wait_for_timeout(self, timeout_ms):
            self.waits.append(timeout_ms)

    page = FakePage()

    settled = await runtime._wait_for_page_flow_settled(
        page,
        previous_url=page.url,
        timeout_ms=60_000,
        min_wait_ms=1_200,
    )

    assert settled is True
    assert page.waits == []


async def test_api_waiter_result_stops_on_repeated_session_expired():
    from e2e_agent.core import page_exploration as runtime

    class FakePage:
        url = "https://example.test/product/task?encryptInsureNum=abc"
        _agent3_submit_trace = {
            "responses": [
                {
                    "status": 200,
                    "url": "https://example.test/api/apps/customer/customerInsure/query/uid/domain/demo-channel?md=1",
                    "body": json.dumps({"code": -1, "success": False, "msg": "user not logged in"}),
                },
                {
                    "status": 200,
                    "url": "https://example.test/api/apps/customer/customerInsure/query/uid/domain/demo-channel?md=2",
                    "body": json.dumps({"code": -1, "success": False, "msg": "login channel expired"}),
                },
                {
                    "status": 200,
                    "url": "https://example.test/api/apps/customer/customerInsure/query/uid/domain/demo-channel?md=3",
                    "body": json.dumps({"code": -1, "success": False, "msg": "login channel expired"}),
                },
            ]
        }

    waiter = asyncio.create_task(asyncio.sleep(0))
    record = await runtime._record_api_waiter_result(
        FakePage(),
        waiter,
        text="bank sign next",
        selector="/api/apps/cps/insure/task/next/do",
        source_url="https://example.test/product/task",
        click_strategy="bank-sign-next-task-response",
    )

    assert record
    assert record["click_strategy"] == "account-session-boundary"
    assert "Account/session boundary" in record["text"]


def test_submit_session_boundary_reads_run_api_errors(monkeypatch, tmp_path):
    from e2e_agent.core import page_exploration as runtime

    errors_path = tmp_path / "api-errors.jsonl"
    responses = [
        {
            "event": "response",
            "status": 200,
            "url": f"https://example.test/api/apps/customer/customerInsure/query/uid/domain/demo-channel?md={index}",
            "body": json.dumps({"code": -1, "success": False, "msg": "用户未登录或登录渠道过时"}),
        }
        for index in range(3)
    ]
    errors_path.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in responses), encoding="utf-8")
    monkeypatch.setenv("AGENT3_RUN_DIR", str(tmp_path))

    page = types.SimpleNamespace(url="https://example.test/product/task?encryptInsureNum=abc")
    reason = runtime._submit_trace_blocker_reason(page)

    assert reason
    assert "Account/session boundary" in reason
    assert "customerInsure/query" in reason


def test_submit_session_boundary_trips_after_two_run_api_errors(monkeypatch, tmp_path):
    from e2e_agent.core import page_exploration as runtime

    errors_path = tmp_path / "api-errors.jsonl"
    responses = [
        {
            "event": "response",
            "status": 200,
            "url": f"https://example.test/api/apps/customer/customerInsure/query/uid/domain/demo-channel?md={index}",
            "body": json.dumps({"code": -1, "success": False, "msg": "用户未登录或登录渠道过时"}),
        }
        for index in range(2)
    ]
    errors_path.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in responses), encoding="utf-8")
    monkeypatch.setenv("AGENT3_RUN_DIR", str(tmp_path))

    page = types.SimpleNamespace(url="https://example.test/product/task?encryptInsureNum=abc")
    reason = runtime._submit_trace_blocker_reason(page)

    assert reason
    assert "2 recent failures" in reason
    assert "Account/session boundary" in reason


def test_submit_session_boundary_trips_after_single_run_api_error(monkeypatch, tmp_path):
    from e2e_agent.core import page_exploration as runtime

    errors_path = tmp_path / "api-errors.jsonl"
    response = {
        "event": "response",
        "status": 200,
        "url": "https://example.test/api/apps/customer/customerInsure/query/uid/domain/demo-channel?md=1",
        "body": json.dumps({"code": -1, "success": False, "msg": "用户未登录或登录渠道过时"}),
    }
    errors_path.write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("AGENT3_RUN_DIR", str(tmp_path))

    page = types.SimpleNamespace(url="https://example.test/product/task?encryptInsureNum=abc")
    reason = runtime._submit_trace_blocker_reason(page)

    assert reason
    assert "1 recent failures" in reason
    assert "Account/session boundary" in reason


def test_submit_session_boundary_does_not_leak_into_health_notice(monkeypatch, tmp_path):
    from e2e_agent.core import page_exploration as runtime

    errors_path = tmp_path / "api-errors.jsonl"
    response = {
        "event": "response",
        "status": 200,
        "url": "https://example.test/api/apps/customer/customerInsure/query/uid/domain/demo-channel?md=1",
        "body": json.dumps({"code": -1, "success": False, "msg": "用户未登录或登录渠道过时"}),
    }
    errors_path.write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("AGENT3_RUN_DIR", str(tmp_path))

    page = types.SimpleNamespace(url="https://example.test/product/healthInform?encryptInsureNum=abc")

    assert runtime._submit_trace_blocker_reason(page) is None


def test_submit_session_boundary_does_not_leak_into_insure_form(monkeypatch, tmp_path):
    from e2e_agent.core import page_exploration as runtime

    errors_path = tmp_path / "api-errors.jsonl"
    response = {
        "event": "response",
        "status": 200,
        "url": "https://example.test/api/apps/customer/customerInsure/query/uid/domain/demo-channel?md=1",
        "body": json.dumps({"code": -1, "success": False, "msg": "用户未登录或登录渠道过时"}),
    }
    errors_path.write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("AGENT3_RUN_DIR", str(tmp_path))

    page = types.SimpleNamespace(url="https://example.test/product/insure?encryptInsureNum=abc")

    assert runtime._submit_trace_blocker_reason(page) is None


async def test_standard_underwriting_poll_stops_on_run_session_boundary(monkeypatch, tmp_path):
    from e2e_agent.core import page_exploration as runtime

    errors_path = tmp_path / "api-errors.jsonl"
    responses = [
        {
            "event": "response",
            "status": 200,
            "url": f"https://example.test/api/apps/customer/customerInsure/query/uid/domain/demo-channel?md={index}",
            "body": json.dumps({"code": -1, "success": False, "msg": "用户未登录或登录渠道过时"}),
        }
        for index in range(3)
    ]
    errors_path.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in responses), encoding="utf-8")
    monkeypatch.setenv("AGENT3_RUN_DIR", str(tmp_path))

    class FakePage:
        url = "https://example.test/product/task?encryptInsureNum=abc"

        async def wait_for_timeout(self, timeout_ms):
            raise AssertionError("should not poll standard underwriting after a session boundary")

    async def fake_direct_next_task(page):
        raise AssertionError("should not call next task after a session boundary")

    monkeypatch.setattr(runtime, "_direct_next_task", fake_direct_next_task)

    actions = await runtime._poll_standard_underwriting_task(
        FakePage(),
        response_record={"text": "后续任务接口: code=41011, success=False, taskType=4"},
        source_url="https://example.test/product/task",
    )

    assert actions
    assert actions[-1]["click_strategy"] == "account-session-boundary"
    assert "Account/session boundary" in actions[-1]["text"]


async def test_minimal_transit_data_stops_on_run_session_boundary(monkeypatch, tmp_path):
    from e2e_agent.core import page_exploration as runtime

    errors_path = tmp_path / "api-errors.jsonl"
    responses = [
        {
            "event": "response",
            "status": 200,
            "url": f"https://example.test/api/apps/customer/customerInsure/query/uid/domain/demo-channel?md={index}",
            "body": json.dumps({"code": -1, "success": False, "msg": "用户未登录或登录渠道过时"}),
        }
        for index in range(3)
    ]
    errors_path.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in responses), encoding="utf-8")
    monkeypatch.setenv("AGENT3_RUN_DIR", str(tmp_path))

    async def fail_if_called(page):
        raise AssertionError("should not continue minimal transit after a session boundary")

    monkeypatch.setattr(runtime, "_apply_authentication_task_data", fail_if_called)
    monkeypatch.setattr(runtime, "_apply_bank_sign_task_data", fail_if_called)
    monkeypatch.setattr(runtime, "_apply_minimal_choice_data", fail_if_called)
    monkeypatch.setattr(runtime, "_apply_minimal_form_data", fail_if_called)
    monkeypatch.setattr(runtime, "_check_required_agreements", fail_if_called)

    page = types.SimpleNamespace(url="https://example.test/product/task?encryptInsureNum=abc")
    actions = await runtime._apply_minimal_transit_data(page)

    assert actions
    assert actions[-1]["click_strategy"] == "account-session-boundary"
    assert "Account/session boundary" in actions[-1]["text"]


async def test_minimal_transit_data_stops_on_authentication_boundary(monkeypatch):
    from e2e_agent.core import page_exploration as runtime

    async def fail_if_called(page):
        raise AssertionError("should not continue minimal transit after an authentication boundary")

    monkeypatch.setattr(runtime, "_apply_authentication_task_data", fail_if_called)
    monkeypatch.setattr(runtime, "_apply_bank_sign_task_data", fail_if_called)
    monkeypatch.setattr(runtime, "_apply_minimal_choice_data", fail_if_called)
    monkeypatch.setattr(runtime, "_apply_minimal_form_data", fail_if_called)
    monkeypatch.setattr(runtime, "_check_required_agreements", fail_if_called)

    page = types.SimpleNamespace(
        url="https://example.test/m/apps/cps/demo-channel/authentication/detail?encryptInsureNum=abc",
    )
    actions = await runtime._apply_minimal_transit_data(page)

    assert actions
    assert actions[-1]["click_strategy"] == "authentication-boundary"
    assert "Authentication boundary" in actions[-1]["text"]
    assert runtime._non_retryable_trace_boundary_reason([], actions) == actions[-1]["text"]


def test_exploration_cache_key_distinguishes_branch_conditions_and_full_route():
    from e2e_agent.core import page_exploration as runtime

    base_path = {
        "path_id": "PATH-001",
        "conditions": {"health_notice": "all-no", "plan": "standard"},
        "page_keys": [
            {"node_id": "NODE-product-detail", "page_key": "PK-detail", "url_pattern": "/product/detail"},
            {"node_id": "NODE-health-notice", "page_key": "PK-health", "url_pattern": "/product/to-insure"},
            {"node_id": "NODE-payment", "page_key": "PK-payment", "url_pattern": "/payment"},
        ],
    }
    same_branch_different_order = {
        **base_path,
        "path_id": "PATH-002",
        "conditions": {"plan": "standard", "health_notice": "all-no"},
    }
    different_branch = {
        **base_path,
        "path_id": "PATH-003",
        "conditions": {"health_notice": "partial-yes", "plan": "standard"},
    }
    different_later_route = {
        **base_path,
        "path_id": "PATH-004",
        "page_keys": [
            {"node_id": "NODE-product-detail", "page_key": "PK-detail", "url_pattern": "/product/detail"},
            {"node_id": "NODE-health-notice", "page_key": "PK-health", "url_pattern": "/product/to-insure"},
            {"node_id": "NODE-underwriting", "page_key": "PK-underwriting", "url_pattern": "/underwriting"},
        ],
    }

    assert runtime._exploration_cache_key(base_path) == runtime._exploration_cache_key(
        same_branch_different_order
    )
    assert runtime._exploration_cache_key(base_path) != runtime._exploration_cache_key(different_branch)
    assert runtime._exploration_cache_key(base_path) != runtime._exploration_cache_key(different_later_route)


def test_completed_path_reuse_key_groups_same_business_handoff_target():
    from e2e_agent.core import page_exploration as runtime

    completed_order_path = {
        "path_id": "PATH-001",
        "page_keys": [
            {"node_id": "NODE-product-detail", "page_key": "PK-detail", "url_pattern": "/product/detail"},
            {"node_id": "NODE-premium-calculation", "page_key": "PK-detail", "url_pattern": "/product/detail"},
            {"node_id": "NODE-insure-form", "page_key": "PK-insure", "url_pattern": "/product/insure"},
            {"node_id": "NODE-payment", "page_key": "PK-payment", "url_pattern": "/payment"},
            {"node_id": "NODE-policy-result", "page_key": "PK-result", "url_pattern": "/result"},
        ],
    }
    same_live_handoff = {
        "path_id": "PATH-002",
        "page_keys": [
            {"node_id": "NODE-product-detail", "page_key": "PK-detail", "url_pattern": "/product/detail"},
            {"node_id": "NODE-suitability", "page_key": "PK-adapt", "url_pattern": "/product/adapt"},
            {"node_id": "NODE-insure-form", "page_key": "PK-insure", "url_pattern": "/product/insure"},
            {"node_id": "NODE-tax-info", "page_key": "PK-tax", "url_pattern": "/tax"},
            {"node_id": "NODE-policy-result", "page_key": "PK-result", "url_pattern": "/result"},
        ],
    }
    post_policy_service_path = {
        "path_id": "PATH-004",
        "page_keys": [
            {"node_id": "NODE-policy-service", "page_key": "PK-service", "url_pattern": "/policy/service"},
            {"node_id": "NODE-surrender", "page_key": "PK-surrender", "url_pattern": "/policy/surrender"},
            {"node_id": "NODE-policy-result", "page_key": "PK-result", "url_pattern": "/result"},
        ],
    }

    assert runtime._exploration_cache_key(completed_order_path) != runtime._exploration_cache_key(same_live_handoff)
    assert runtime._completed_path_reuse_key(completed_order_path) == runtime._completed_path_reuse_key(same_live_handoff)
    assert runtime._completed_path_reuse_key(completed_order_path) != runtime._completed_path_reuse_key(post_policy_service_path)


def test_exploration_contract_marks_environment_boundary_and_resume_condition():
    from e2e_agent.core import page_exploration as runtime

    contract = runtime._build_exploration_contract(
        [
            {
                "path_id": "PATH-001",
                "path_status": "blocked",
                "target_node": "NODE-policy-result",
                "blocked_node": "NODE-payment",
                "blocked_reason": "Path PATH-001 exploration attempt 1/3 timed out after 90s",
                "evidence_source": "agent3-live-browser",
                "completion_rule": {
                    "required_nodes": ["NODE-product-detail", "NODE-payment", "NODE-policy-result"],
                    "matched_nodes": ["NODE-product-detail"],
                    "missing_nodes": ["NODE-payment", "NODE-policy-result"],
                    "is_complete": False,
                },
            }
        ]
    )

    blocked = contract["blocked_paths"][0]
    assert contract["phase1_contract"]["exploration_mode"] == "path-driven"
    assert blocked["terminal_boundary"]["classification"] == "environment"
    assert blocked["terminal_boundary"]["boundary_node"] == "NODE-payment"
    assert blocked["resume_condition"]
    assert blocked["evidence_source"] == "agent3-live-browser"


def test_exploration_contract_keeps_unreached_payment_as_coverage_gap():
    from e2e_agent.core import page_exploration as runtime

    contract = runtime._build_exploration_contract(
        [
            {
                "path_id": "PATH-001",
                "path_status": "blocked",
                "target_node": "NODE-payment",
                "blocked_node": "NODE-payment",
                "blocked_reason": "No executable primary action found for Agent2 planned path",
                "completion_rule": {
                    "required_nodes": ["NODE-product-detail", "NODE-payment"],
                    "matched_nodes": ["NODE-product-detail"],
                    "missing_nodes": ["NODE-payment"],
                    "is_complete": False,
                },
            }
        ]
    )

    blocked = contract["blocked_paths"][0]
    assert blocked["terminal_boundary"]["classification"] == "coverage_gap"
    assert blocked["evidence_source"] == "agent3-unknown"


def test_fallback_explore_artifacts_blocks_all_regression_paths():
    from e2e_agent.agents import explore_agent

    artifacts = explore_agent._fallback_explore_artifacts(
        {
            "product_id": "demo-product",
            "entry_url": "https://example.com/detail",
            "regression_flow": {
                "nodes": [
                    {"node_id": "NODE-start", "type": "start"},
                    {"node_id": "NODE-detail", "type": "form", "page_name": "Detail"},
                    {"node_id": "NODE-confirm", "type": "confirm", "page_name": "Confirm"},
                    {"node_id": "NODE-end", "type": "end"},
                ]
            },
            "regression_paths": [
                {
                    "path_id": "PATH-001",
                    "case_ids": ["TC-001"],
                    "nodes": ["NODE-start", "NODE-detail", "NODE-confirm", "NODE-end"],
                }
            ],
        }
    )

    contract = artifacts["page_registry"]["exploration_contract"]
    assert contract["completed_paths"] == []
    assert contract["blocked_paths"][0]["path_id"] == "PATH-001"
    assert contract["blocked_paths"][0]["blocked_node"] == "NODE-confirm"
    assert contract["blocked_paths"][0]["evidence_source"] == "agent3-fallback"
    assert artifacts["page_registry"]["path_exploration_results"][0]["completion_rule"]["is_complete"] is False


@pytest.mark.asyncio
async def test_explore_node_returns_error_on_unexpected_exception(monkeypatch):
    from e2e_agent.agents import explore_agent

    def explode_prepare_runtime_context(**_: object) -> dict:
        raise RuntimeError("runtime exploded")

    monkeypatch.setattr(
        explore_agent,
        "prepare_runtime_context",
        explode_prepare_runtime_context,
    )

    result = await explore_agent.explore_node({"product_id": "demo-product"})

    assert "explore failed: runtime exploded" == result["error"]

from __future__ import annotations

from pathlib import Path

import yaml


def _state_deps_config() -> dict:
    return {
        "version": "w4-test",
        "whitelist": {
            "/product/*": ["productCode", "planCode"],
            "/payment": ["orderId"],
        },
        "normalization": {
            "strip_timestamp": True,
            "lowercase_values": True,
        },
        "hot_values": {
            "planCode": ["BASIC", "PREMIUM"],
        },
        "cardinality": {
            "warn_threshold": 2,
            "max_combinations": 4,
        },
    }


def test_validate_state_deps_filters_non_whitelisted_fields() -> None:
    from e2e_agent.core.page_key_governance import validate_state_deps

    result = validate_state_deps(
        "/product/detail",
        {
            "productCode": "DD0950",
            "ageBand": "0-17",
            "_t": "1700000000",
        },
        _state_deps_config(),
    )

    assert result["matched_whitelist_patterns"] == ["/product/*"]
    assert result["allowed_state_keys"] == ["planCode", "productCode"]
    assert result["rejected_state_keys"] == ["_t", "ageBand"]
    assert result["states"] == [{"productCode": "dd0950"}]
    assert result["warnings"] == [
        "Path uses non-whitelisted state keys for /product/detail: _t, ageBand"
    ]


def test_normalize_state_signature_sorts_collection_values() -> None:
    from e2e_agent.core.page_key_governance import normalize_state_signature

    result = normalize_state_signature(
        {"planCode": ["PREMIUM", "basic", "BASIC"]},
        _state_deps_config(),
    )

    assert result == {"planCode": "basic|premium"}


def test_validate_state_deps_expands_hot_values() -> None:
    from e2e_agent.core.page_key_governance import validate_state_deps

    result = validate_state_deps(
        "/product/detail",
        {
            "productCode": "DD0950",
            "planCode": "__hot__",
        },
        _state_deps_config(),
    )

    assert result["states"] == [
        {"planCode": "basic", "productCode": "dd0950"},
        {"planCode": "premium", "productCode": "dd0950"},
    ]
    assert result["hot_value_expansions"] == {
        "planCode": ["basic", "premium"],
    }


def test_repository_state_deps_covers_pilot_business_states() -> None:
    from e2e_agent.core.page_key_governance import validate_state_deps

    root_dir = Path(__file__).resolve().parents[1]
    config_path = root_dir / "domains" / "insurance" / "state-deps.yaml"
    text = config_path.read_text(encoding="utf-8")
    config = yaml.safe_load(text)

    assert "TODO" not in text
    product_result = validate_state_deps(
        "/product/detail",
        {"productCode": "TP001", "planCode": "classic", "_t": "1700000000"},
        config,
    )
    insure_result = validate_state_deps(
        "/product/insure",
        {
            "productCode": "TP001",
            "planCode": "classic",
            "ageBand": "__hot__",
            "occupationCategory": "office",
            "debugTraceId": "trace-001",
        },
        config,
    )
    result_page = validate_state_deps(
        "/result",
        {"orderId": "ORD-1", "orderStatus": "issued", "underwritingResult": "standard"},
        config,
    )

    assert product_result["states"] == [{"planCode": "classic", "productCode": "TP001"}]
    assert product_result["rejected_state_keys"] == ["_t"]
    assert insure_result["hot_value_expansions"]["ageBand"] == ["adult", "child", "senior"]
    assert "debugTraceId" in insure_result["rejected_state_keys"]
    assert result_page["allowed_state_keys"] == ["orderId", "orderStatus", "policyNo", "underwritingResult"]


def test_page_key_cardinality_check_warns_above_threshold() -> None:
    from e2e_agent.core.page_key_governance import page_key_cardinality_check

    result = page_key_cardinality_check(
        {
            "/product/detail": [
                {"productCode": "p1", "planCode": "a"},
                {"productCode": "p1", "planCode": "b"},
                {"productCode": "p1", "planCode": "c"},
            ]
        },
        _state_deps_config(),
    )

    assert result["routes"]["/product/detail"]["combination_count"] == 3
    assert result["routes"]["/product/detail"]["severity"] == "warning"
    assert result["warnings"] == [
        "/product/detail approaches state combination limit: 3>2"
    ]


def test_path_governance_ignores_control_nodes_for_route_warnings() -> None:
    from e2e_agent.agents.path_extract_agent import _governance_for_path

    warnings, page_keys, matched_patterns = _governance_for_path(
        [
            {"node_id": "NODE-start", "type": "start", "url_pattern": None},
            {"node_id": "NODE-product", "type": "form", "url_pattern": "/product/detail"},
            {"node_id": "NODE-end", "type": "end", "url_pattern": None},
        ],
        {"productCode": "DD0950"},
        _state_deps_config(),
    )

    assert warnings == []
    assert matched_patterns == ["/product/*"]
    assert page_keys[0]["state"] == {"productCode": "dd0950"}


def test_agent3_governance_maps_observed_adapt_and_auth_routes() -> None:
    from e2e_agent.agents.agent3_explore.node import _annotate_page_registry_with_governance

    page_registry = {
        "pages": [
            {"url": "https://example.test/m/apps/cps/x/product/adapt?encryptInsureNum=abc"},
            {"url": "https://example.test/m/apps/cps/x/authentication/detail?encryptInsureNum=abc"},
        ]
    }
    state = {
        "governance_summary": {
            "paths": [
                {
                    "page_keys": [
                        {
                            "node_id": "NODE-suitability",
                            "page_key": "PK-product-to-insure",
                            "url_pattern": "/product/to-insure",
                            "allowed_state_keys": [],
                        },
                        {
                            "node_id": "NODE-risk-control",
                            "page_key": "PK-risk-control",
                            "url_pattern": "/risk-control",
                            "allowed_state_keys": [],
                        },
                    ]
                }
            ]
        }
    }

    annotated, warnings = _annotate_page_registry_with_governance(page_registry, state)

    assert warnings == []
    assert annotated["pages"][0]["planned_page_keys"] == ["PK-product-to-insure"]
    assert annotated["pages"][1]["planned_page_keys"] == ["PK-risk-control"]


def test_agent3_order_generation_accepts_bank_sign_success_boundary() -> None:
    from e2e_agent.core.page_exploration import _steps_look_like_order_generated

    assert _steps_look_like_order_generated(
        [
            {
                "node_execution_trace": [
                    {
                        "text": "standard-underwriting-probe: code=0, success=True, taskType=3, canPay=False"
                    },
                    {"text": "签约成功"},
                ]
            }
        ]
    )

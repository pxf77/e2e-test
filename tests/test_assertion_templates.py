from __future__ import annotations

from pathlib import Path

from e2e_agent.core.script_generation import build_assertion_results, build_ts_gen_bundle


def _write_catalog(tmp_path: Path) -> Path:
    catalog = tmp_path / "assertion-templates.yaml"
    catalog.write_text(
        """
version: "1.0"
templates:
  price_premium:
    name: "价格/保费断言"
    required: true
    match:
      route_keywords: ["product", "premium"]
      assertion_keywords: ["保费", "价格"]
  underwriting_result:
    name: "核保结果断言"
    required: true
    match:
      route_keywords: ["underwriting", "result"]
      assertion_keywords: ["核保", "承保", "拒保"]
  order_status:
    name: "订单状态断言"
    required: true
    match:
      route_keywords: ["payment", "order"]
      assertion_keywords: ["支付", "订单", "出单"]
""",
        encoding="utf-8",
    )
    return catalog


def test_match_assertion_template_uses_catalog_keywords(tmp_path: Path) -> None:
    from e2e_agent.core.assertion_templates import (
        load_assertion_template_catalog,
        match_assertion_template,
    )

    catalog = load_assertion_template_catalog(_write_catalog(tmp_path))
    match = match_assertion_template(
        {
            "path_id": "PATH-PRICE",
            "nodes": ["NODE-product-detail"],
            "assertions": ["展示保费金额"],
            "conditions": {"planCode": "BASIC"},
        },
        catalog,
    )

    assert match["template_type"] == "price_premium"
    assert match["assertion_strength"] == "strong"
    assert match["weak"] is False
    assert match["variables"]["planCode"] == "BASIC"
    assert "保费" in match["match_reason"]


def test_match_assertion_template_uses_builtin_keywords_for_repository_catalog() -> None:
    from e2e_agent.core.assertion_templates import (
        load_assertion_template_catalog,
        match_assertion_template,
    )

    root_dir = Path(__file__).resolve().parents[1]
    catalog = load_assertion_template_catalog(root_dir=root_dir)

    price = match_assertion_template(
        {
            "path_id": "PATH-PRICE",
            "nodes": ["NODE-product-detail"],
            "assertions": ["保费金额展示正确"],
        },
        catalog,
    )
    underwriting = match_assertion_template(
        {
            "path_id": "PATH-UW",
            "nodes": ["NODE-underwriting-result"],
            "assertions": ["核保结果为承保"],
        },
        catalog,
    )
    order = match_assertion_template(
        {
            "path_id": "PATH-ORDER",
            "nodes": ["NODE-payment-order"],
            "assertions": ["订单支付后出单"],
        },
        catalog,
    )

    assert price["template_type"] == "price_premium"
    assert underwriting["template_type"] == "underwriting_result"
    assert order["template_type"] == "order_status"
    assert all(not item["weak"] for item in (price, underwriting, order))


def test_repository_assertion_templates_are_business_filled() -> None:
    from e2e_agent.core.assertion_templates import load_assertion_template_catalog

    root_dir = Path(__file__).resolve().parents[1]
    config_path = root_dir / "domains" / "insurance" / "assertion-pack.yaml"
    text = config_path.read_text(encoding="utf-8")
    catalog = load_assertion_template_catalog(root_dir=root_dir)

    assert "TODO" not in text
    for template_type in ("price_premium", "underwriting_result", "order_status"):
        template = catalog["templates"][template_type]
        match = template.get("match", {})
        assert match.get("route_keywords"), template_type
        assert match.get("assertion_keywords"), template_type
        assert template.get("checks"), template_type


def test_match_assertion_template_falls_back_to_custom_with_justification(tmp_path: Path) -> None:
    from e2e_agent.core.assertion_templates import (
        load_assertion_template_catalog,
        match_assertion_template,
    )

    catalog = load_assertion_template_catalog(_write_catalog(tmp_path))
    match = match_assertion_template(
        {
            "path_id": "PATH-CUSTOM",
            "nodes": ["NODE-marketing-copy"],
            "assertions": ["宣传文案展示正确"],
        },
        catalog,
    )

    assert match["template_type"] == "custom"
    assert match["assertion_strength"] == "weak"
    assert match["weak"] is True
    assert match["missing_template_reason"] == "no_template_match"
    assert match["justification"]


def test_build_assertion_results_includes_template_metadata_and_summary(tmp_path: Path) -> None:
    catalog_path = _write_catalog(tmp_path)
    state = {
        "assertion_template_source": str(catalog_path),
        "regression_paths": [
            {
                "path_id": "PATH-001",
                "nodes": ["NODE-product-detail"],
                "case_ids": ["TC-001"],
                "assertions": ["展示保费金额"],
                "conditions": {"planCode": "BASIC"},
            },
            {
                "path_id": "PATH-002",
                "nodes": ["NODE-marketing-copy"],
                "case_ids": ["TC-002"],
                "assertions": ["宣传文案展示正确"],
            },
        ],
    }

    assertion_results, summary = build_assertion_results(
        state,
        root_dir=tmp_path,
        include_summary=True,
    )

    assert assertion_results[0]["template_type"] == "price_premium"
    assert assertion_results[0]["assertion_strength"] == "strong"
    assert assertion_results[1]["template_type"] == "custom"
    assert assertion_results[1]["weak"] is True
    assert summary["total_assertion_count"] == 2
    assert summary["template_coverage_rate"] == 0.5
    assert summary["missing_template_count"] == 1
    assert summary["weak_assertion_count"] == 1


def test_build_ts_gen_bundle_exposes_assertion_template_summary(tmp_path: Path) -> None:
    catalog_path = _write_catalog(tmp_path)
    bundle = build_ts_gen_bundle(
        {
            "product_id": "demo-product",
            "entry_url": "https://example.com/product",
            "assertion_template_source": str(catalog_path),
            "regression_flow": {
                "nodes": [
                    {"node_id": "NODE-start", "page_id": "start"},
                    {"node_id": "NODE-product-detail", "page_id": "product"},
                    {"node_id": "NODE-end", "page_id": "end"},
                ],
                "edges": [
                    {"from": "NODE-start", "to": "NODE-product-detail"},
                    {"from": "NODE-product-detail", "to": "NODE-end"},
                ],
            },
            "regression_paths": [
                {
                    "path_id": "PATH-001",
                    "nodes": ["NODE-product-detail"],
                    "case_ids": ["TC-001"],
                    "assertions": ["展示保费金额"],
                }
            ],
        },
        root_dir=tmp_path,
        materialise=False,
    )

    assert bundle["assertion_template_summary"]["total_assertion_count"] == 1
    assert bundle["assertion_template_summary"]["templates_used"] == {"price_premium": 1}


def test_finalize_explore_result_carries_template_summary_into_page_registry(tmp_path: Path) -> None:
    from e2e_agent.agents.agent3_explore.node import _finalize_explore_result

    result = _finalize_explore_result(
        state={
            "product_id": "demo-product",
            "run_id": "run-assertion-summary",
            "artifact_root_dir": str(tmp_path),
            "artifact_fingerprints": [],
        },
        root_dir=tmp_path,
        runtime_context={},
        live_artifacts={"page_registry": {"pages": []}, "explore_trace": {}},
        result={
            "page_functions": [],
            "scenarios": [],
            "script_plan": {},
            "script_bundle": {},
            "script_validation": {},
            "assertion_results": [],
            "assertion_template_summary": {
                "total_assertion_count": 1,
                "missing_template_count": 0,
            },
        },
        warnings=[],
    )

    assert result["page_registry"]["assertion_template_summary"]["total_assertion_count"] == 1
    assert result["assertion_template_summary"]["missing_template_count"] == 0
    summary_path = tmp_path / "products" / "demo-product" / "agent3" / "assertion-template-summary.json"
    assert summary_path.exists()

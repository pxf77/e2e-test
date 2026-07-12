from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_path_extract_node_emits_governance_summary_and_page_keys(tmp_path, monkeypatch):
    from e2e_agent.legacy.agents import path_extract_agent

    monkeypatch.setattr(path_extract_agent, "_ROOT_DIR", tmp_path)

    result = await path_extract_agent.path_extract_node(
        {
            "product_id": "demo-product",
            "entry_url": "https://example.com/product/detail",
            "merged_cases": [
                {
                    "case_id": "CASE-001",
                    "title": "产品详情投保流程",
                    "priority": "P0",
                    "steps": [
                        "url:/product/detail",
                        "condition: productCode=DD0950",
                        "查看产品详情并进入投保流程",
                    ],
                    "assertions": ["产品详情展示正常"],
                    "preconditions": [],
                    "tags": [],
                }
            ],
        }
    )

    assert result["regression_paths"]
    assert result["regression_paths"][0]["page_keys"]
    assert result["artifact_fingerprints"][-1]["artifact_type"] == "governance-summary"
    assert result["artifact_fingerprints"][-1]["artifact_path"] == (
        "products/demo-product/agent2/governance-summary.json"
    )
    agent2_dir = tmp_path / "products" / "demo-product" / "agent2"
    assert (agent2_dir / "regression-flow.json").exists()
    assert (agent2_dir / "regression-paths.json").exists()
    assert (agent2_dir / "governance-summary.json").exists()
    assert not (tmp_path / "products" / "demo-product" / "reg" / "regression-flow.json").exists()
    assert result["governance_summary"]["summary"]["total_page_keys"] >= 1
    assert (
        "/product/*"
        in result["governance_summary"]["paths"][0]["matched_whitelist_patterns"]
    )


@pytest.mark.asyncio
async def test_path_extract_node_uses_business_intent_planned_pages(tmp_path, monkeypatch):
    from e2e_agent.legacy.agents import path_extract_agent

    monkeypatch.setattr(path_extract_agent, "_ROOT_DIR", tmp_path)

    result = await path_extract_agent.path_extract_node(
        {
            "product_id": "demo-product",
            "entry_url": "https://example.com/product/detail",
            "merged_cases": [
                {
                    "case_id": "CASE-002",
                    "title": "少儿重疾标准投保成功主链路",
                    "priority": "P0",
                    "business_intent": "main_flow",
                    "scenario_type": "main_flow",
                    "steps": [
                        "覆盖人工路径：指定受益人 > 选择指定受益人可出单成功",
                    ],
                    "assertions": ["主链路可以出单"],
                    "rules": [{"rule_id": "RULE-001", "description": "字段规则"}],
                    "coverage_refs": [{"source": "manual", "case_id": "MANUAL-001"}],
                    "data_variants": [{"type": "plan", "value": "计划21"}],
                    "preconditions": [],
                    "tags": ["business-intent:main_flow"],
                }
            ],
        }
    )

    path = result["regression_paths"][0]
    assert path["nodes"] == [
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
    ]
    assert path["execution_policy"]["name"] == "max_new_business_order_path"
    assert "NODE-health-notice" in path["optional_nodes"]
    assert path["business_intent"] == "main_flow"
    assert path["assertions"] == ["主链路可以出单"]
    assert path["rules"][0]["rule_id"] == "RULE-001"
    assert path["coverage_refs"][0]["case_id"] == "MANUAL-001"
    url_patterns = [
        item.get("url_pattern")
        for item in result["governance_summary"]["paths"][0]["page_keys"]
    ]
    assert "/指定，" not in url_patterns
    assert "/product/to-insure" in url_patterns
    assert "/insure/health-notice" in url_patterns
    assert "/product/insure" in url_patterns
    assert "/underwriting" in url_patterns
    assert "/risk-control" in url_patterns
    assert "/payment" in url_patterns
    assert "/insure/applicant" not in url_patterns


@pytest.mark.asyncio
async def test_path_extract_policy_and_surrender_include_order_chain(tmp_path, monkeypatch):
    from e2e_agent.legacy.agents import path_extract_agent

    monkeypatch.setattr(path_extract_agent, "_ROOT_DIR", tmp_path)

    result = await path_extract_agent.path_extract_node(
        {
            "product_id": "demo-product",
            "entry_url": "https://example.com/product/detail",
            "merged_cases": [
                {
                    "case_id": "CASE-POLICY",
                    "title": "出单后保单文件与回访链路",
                    "priority": "P1",
                    "business_intent": "policy",
                    "scenario_type": "policy",
                    "steps": ["完成出单后进入保单服务"],
                    "assertions": ["可查看保单服务入口"],
                },
                {
                    "case_id": "CASE-SURRENDER",
                    "title": "撤单与退保关键链路",
                    "priority": "P1",
                    "business_intent": "surrender",
                    "scenario_type": "surrender",
                    "steps": ["完成出单后进入退保"],
                    "assertions": ["可进入退保流程"],
                },
            ],
        }
    )

    paths = {path["business_intent"]: path for path in result["regression_paths"]}

    assert paths["policy"]["nodes"] == [
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
        "NODE-policy-service",
        "NODE-end",
    ]
    assert paths["surrender"]["nodes"] == [
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
        "NODE-policy-service",
        "NODE-surrender",
        "NODE-policy-result",
        "NODE-end",
    ]


@pytest.mark.asyncio
async def test_path_extract_dedupes_equivalent_business_paths(tmp_path, monkeypatch):
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
                    "steps": ["主链路投保成功"],
                    "assertions": ["计划21可出单"],
                    "coverage_refs": [{"case_id": "MANUAL-001"}],
                    "data_variants": [{"type": "plan", "value": "计划21"}],
                },
                {
                    "case_id": "CASE-002",
                    "title": "计划22主链路",
                    "priority": "P0",
                    "business_intent": "main_flow",
                    "scenario_type": "main_flow",
                    "steps": ["主链路投保成功"],
                    "assertions": ["计划22可出单"],
                    "coverage_refs": [{"case_id": "MANUAL-002"}],
                    "data_variants": [{"type": "plan", "value": "计划22"}],
                },
            ],
        }
    )

    assert len(result["regression_paths"]) == 1
    path = result["regression_paths"][0]
    assert path["case_ids"] == ["CASE-001", "CASE-002"]
    assert path["source_path_count"] == 2
    assert [item["value"] for item in path["data_variants"]] == ["计划21", "计划22"]
    assert result["governance_summary"]["summary"]["path_count"] == 1


@pytest.mark.asyncio
async def test_path_extract_node_resolves_artifact_dir_from_product_source_dir(tmp_path, monkeypatch):
    from e2e_agent.legacy.agents import path_extract_agent

    monkeypatch.setattr(path_extract_agent, "_ROOT_DIR", tmp_path)

    source_dir = tmp_path / "products" / "demo-product" / "demo-plan"
    source_dir.mkdir(parents=True)
    (source_dir / "product-input.json").write_text('{"product_id": "demo-product"}', encoding="utf-8")
    external_inputs = tmp_path / "detached-inputs"
    external_inputs.mkdir()
    prd_path = external_inputs / "prd.md"
    manual_path = external_inputs / "manual.md"
    prd_path.write_text("# Demo PRD\n", encoding="utf-8")
    manual_path.write_text("# Manual Cases\n", encoding="utf-8")

    result = await path_extract_agent.path_extract_node(
        {
            "product_id": "demo-product",
            "entry_url": "https://example.com/product/detail",
            "prd_path": str(prd_path),
            "manual_cases_path": str(manual_path),
            "product_source_dir": str(source_dir),
            "artifact_root_dir": str(tmp_path),
            "merged_cases": [
                {
                    "case_id": "CASE-001",
                    "title": "浜у搧璇︽儏鎶曚繚娴佺▼",
                    "priority": "P0",
                    "steps": ["url:/product/detail"],
                    "assertions": ["浜у搧璇︽儏灞曠ず姝ｅ父"],
                }
            ],
        }
    )

    asset_dir = source_dir.with_name("demo-plan.assets")
    assert result["product_artifact_dir"] == str(asset_dir)
    assert (asset_dir / "agent2" / "regression-flow.json").exists()
    assert result["artifact_fingerprints"][-1]["artifact_path"] == (
        "products/demo-product/demo-plan.assets/agent2/governance-summary.json"
    )
    assert not (tmp_path / "products" / "demo-product" / "agent2").exists()


@pytest.mark.asyncio
async def test_path_extract_node_returns_error_on_unexpected_exception(monkeypatch):
    from e2e_agent.legacy.agents import path_extract_agent

    class ExplodingLoader:
        def load_skill(self, _: str) -> object:
            raise RuntimeError("loader exploded")

    monkeypatch.setattr(path_extract_agent, "SkillPackageLoader", lambda: ExplodingLoader())

    result = await path_extract_agent.path_extract_node({"product_id": "demo-product"})

    assert "path_extract failed: loader exploded" == result["error"]

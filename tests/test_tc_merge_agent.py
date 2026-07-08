from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_tc_merge_node_runs_three_stage_chain_and_emits_merge_trace(tmp_path, monkeypatch):
    from e2e_agent.agents import tc_merge_agent

    monkeypatch.setattr(tc_merge_agent, "_ROOT_DIR", tmp_path)

    prd_path = tmp_path / "prd.md"
    manual_path = tmp_path / "manual.md"
    prd_path.write_text(
        (
            "# Demo PRD\n\n"
            "## 产品展示\n"
            "- 用户查看产品说明\n"
            "- 用户进入投保流程\n\n"
            "## 支付结果\n"
            "- 用户完成支付\n"
            "- 系统展示结果页\n"
        ),
        encoding="utf-8",
    )
    manual_path.write_text(
        (
            "# CASE 产品展示场景\n\n"
            "- 步骤: 打开产品详情页\n"
            "- 步骤: 进入投保流程\n"
            "- 预期: 页面可以进入投保页\n"
        ),
        encoding="utf-8",
    )

    result = await tc_merge_agent.tc_merge_node(
        {
            "product_id": "demo-product",
            "prd_path": str(prd_path),
            "manual_cases_path": str(manual_path),
            "artifact_root_dir": str(tmp_path),
        }
    )

    assert result["prd_analysis"]["features"]
    assert result["test_case_skeleton"]
    assert result["candidate_cases"]
    assert result["merged_cases"]
    assert "excluded_cases" in result
    assert result["merge_trace"]["chain"] == [
        "mpt-ins-prd-ana",
        "mpt-ins-tc-gen",
        "mpt-reg-case-merge",
    ]
    assert len(result["artifact_fingerprints"]) == 6
    assert result["artifact_fingerprints"][0]["artifact_type"] == "prd-analysis"
    assert any(
        item["artifact_path"] == "products/demo-product/agent1/merged-cases.md"
        for item in result["artifact_fingerprints"]
    )
    assert result["merge_trace"]["summary"]["skeleton_count"] >= 1
    assert result["merge_trace"]["summary"]["parse_trace_count"] == 1
    assert result["merge_trace"]["summary"]["candidate_case_count"] >= len(result["merged_cases"])
    assert result["merge_trace"]["summary"]["excluded_case_count"] >= 0
    assert result["merge_trace"]["artifacts"]["selection_trace"]["selection_policy"]["name"] == "business-scenario-orchestration-v1"
    assert result["merge_trace"]["artifacts"]["parse_trace"][0]["format"] == "md"
    assert result["merge_trace"]["artifacts"]["merged_cases_markdown"] == "products/demo-product/agent1/merged-cases.md"
    agent1_dir = tmp_path / "products" / "demo-product" / "agent1"
    merged_cases_md = agent1_dir / "merged-cases.md"
    assert merged_cases_md.exists()
    assert (agent1_dir / "prd-analysis.json").exists()
    assert (agent1_dir / "test-case-skeleton.json").exists()
    assert (agent1_dir / "candidate-cases.json").exists()
    assert (agent1_dir / "merged-cases.json").exists()
    assert (agent1_dir / "excluded-cases.json").exists()
    assert (agent1_dir / "conflicts.json").exists()
    assert (agent1_dir / "merge-trace.json").exists()
    assert not (tmp_path / "products" / "demo-product" / "reg" / "merged-cases.md").exists()
    merged_cases_doc = merged_cases_md.read_text(encoding="utf-8")
    assert "# Agent1 合并后用例说明" in merged_cases_doc
    assert "## 合并用例" in merged_cases_doc
    assert "## 冲突与处理建议" not in merged_cases_doc
    assert "### CONFLICT-" not in merged_cases_doc
    assert "### " in merged_cases_doc
    assert "步骤" in merged_cases_doc
    assert "预期结果" in merged_cases_doc
    assert "来源: 合并用例" in merged_cases_doc or "来源: PRD 骨架用例" in merged_cases_doc
    assert "update-human-case" not in merged_cases_doc
    assert "missing_coverage" not in merged_cases_doc
    assert "AC case" not in merged_cases_doc
    assert "Update the manual case" not in merged_cases_doc
    assert "PRD-derived case" not in merged_cases_doc
    assert "business-intent:" not in merged_cases_doc
    assert "scenario-type:" not in merged_cases_doc
    assert "manual-format:" not in merged_cases_doc
    assert (tmp_path / "products" / "demo-product" / "artifact-fingerprints.jsonl").exists()


@pytest.mark.asyncio
async def test_tc_merge_node_materialises_under_product_asset_dir(tmp_path, monkeypatch):
    from e2e_agent.agents import tc_merge_agent

    monkeypatch.setattr(tc_merge_agent, "_ROOT_DIR", tmp_path)

    asset_dir = tmp_path / "products" / "demo-product" / "demo.assets"
    asset_dir.mkdir(parents=True)
    prd_path = asset_dir / "prd.md"
    manual_path = asset_dir / "manual.md"
    prd_path.write_text("# Demo PRD\n\n## 产品展示\n- 用户查看产品说明\n", encoding="utf-8")
    manual_path.write_text("# CASE 产品展示场景\n\n- 步骤: 打开产品详情页\n- 预期: 展示产品说明\n", encoding="utf-8")

    result = await tc_merge_agent.tc_merge_node(
        {
            "product_id": "demo-product",
            "prd_path": str(prd_path),
            "manual_cases_path": str(manual_path),
            "artifact_root_dir": str(tmp_path),
        }
    )

    agent1_dir = asset_dir / "agent1"
    assert result["product_artifact_dir"] == str(asset_dir)
    assert result["merge_trace"]["artifacts"]["merged_cases_markdown"] == (
        "products/demo-product/demo.assets/agent1/merged-cases.md"
    )
    assert (agent1_dir / "merged-cases.md").exists()
    assert (agent1_dir / "merged-cases.json").exists()
    assert (asset_dir / "artifact-fingerprints.jsonl").exists()
    assert not (tmp_path / "products" / "demo-product" / "agent1").exists()
    assert not (tmp_path / "products" / "demo-product" / "artifact-fingerprints.jsonl").exists()
    assert any(
        item["artifact_path"] == "products/demo-product/demo.assets/agent1/merged-cases.md"
        for item in result["artifact_fingerprints"]
    )


@pytest.mark.asyncio
async def test_tc_merge_node_resolves_artifact_dir_from_product_source_dir(tmp_path, monkeypatch):
    from e2e_agent.agents import tc_merge_agent

    monkeypatch.setattr(tc_merge_agent, "_ROOT_DIR", tmp_path)

    source_dir = tmp_path / "products" / "demo-product" / "demo-plan"
    source_dir.mkdir(parents=True)
    (source_dir / "product-input.json").write_text('{"product_id": "demo-product"}', encoding="utf-8")
    external_inputs = tmp_path / "detached-inputs"
    external_inputs.mkdir()
    prd_path = external_inputs / "prd.md"
    manual_path = external_inputs / "manual.md"
    prd_path.write_text("# Demo PRD\n\n## 浜у搧灞曠ず\n- 鐢ㄦ埛鏌ョ湅浜у搧璇存槑\n", encoding="utf-8")
    manual_path.write_text("# CASE 浜у搧灞曠ず鍦烘櫙\n\n- 姝ラ: 鎵撳紑浜у搧璇︽儏椤礬n- 棰勬湡: 灞曠ず浜у搧璇存槑\n", encoding="utf-8")

    result = await tc_merge_agent.tc_merge_node(
        {
            "product_id": "demo-product",
            "prd_path": str(prd_path),
            "manual_cases_path": str(manual_path),
            "product_source_dir": str(source_dir),
            "artifact_root_dir": str(tmp_path),
        }
    )

    asset_dir = source_dir.with_name("demo-plan.assets")
    assert result["product_artifact_dir"] == str(asset_dir)
    assert (asset_dir / "agent1" / "merged-cases.json").exists()
    assert any(
        item["artifact_path"] == "products/demo-product/demo-plan.assets/agent1/merged-cases.md"
        for item in result["artifact_fingerprints"]
    )
    assert not (tmp_path / "products" / "demo-product" / "agent1").exists()


@pytest.mark.asyncio
async def test_tc_merge_node_returns_error_on_unexpected_exception(monkeypatch):
    from e2e_agent.agents import tc_merge_agent

    class ExplodingLoader:
        def load_skill(self, _: str) -> object:
            raise RuntimeError("loader exploded")

    monkeypatch.setattr(tc_merge_agent, "SkillPackageLoader", lambda: ExplodingLoader())

    result = await tc_merge_agent.tc_merge_node({"product_id": "demo-product"})

    assert "tc_merge failed: loader exploded" == result["error"]

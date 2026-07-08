"""Unit tests for SkillPackageLoader."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
import yaml

from e2e_agent.skills.loader import SkillManifest, SkillPackageLoader


def _write_minimal_xlsx(path: Path, rows: list[list[str]]) -> None:
    def col_name(index: int) -> str:
        return chr(ord("A") + index)

    sheet_rows: list[str] = []
    for row_index, row in enumerate(rows, start=1):
        cells: list[str] = []
        for col_index, value in enumerate(row):
            ref = f"{col_name(col_index)}{row_index}"
            escaped = (
                value.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
            )
            cells.append(
                f'<c r="{ref}" t="inlineStr"><is><t>{escaped}</t></is></c>'
            )
        sheet_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')

    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{''.join(sheet_rows)}</sheetData>"
        "</worksheet>"
    )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets>'
        "</workbook>"
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/>'
        "</Relationships>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        "</Types>"
    )
    package_rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/>'
        "</Relationships>"
    )

    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", package_rels)
        archive.writestr("xl/workbook.xml", workbook_xml)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)


def _write_minimal_xmind(path: Path) -> None:
    content_xml = """<?xml version="1.0" encoding="UTF-8"?>
<xmap-content xmlns="urn:xmind:xmap:xmlns:content:2.0">
  <sheet id="sheet-1">
    <topic id="root">
      <title>Demo Product Cases</title>
      <children>
        <topics type="attached">
          <topic id="group-1">
            <title>投保流程</title>
            <children>
              <topics type="attached">
                <topic id="case-1">
                  <title>税收居民身份阻断场景</title>
                  <labels><label>用例</label></labels>
                  <marker-refs><marker-ref marker-id="priority-1"/></marker-refs>
                </topic>
              </topics>
            </children>
          </topic>
        </topics>
      </children>
    </topic>
  </sheet>
</xmap-content>
"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("content.xml", content_xml)


def _write_selection_xmind(path: Path) -> None:
    case_topics = "\n".join(
        [
            '<topic id="case-1"><title>投保成功P0用例</title><labels><label>用例</label></labels><marker-refs><marker-ref marker-id="priority-1"/></marker-refs></topic>',
            '<topic id="case-2"><title>支付失败P0用例</title><labels><label>用例</label></labels><marker-refs><marker-ref marker-id="priority-1"/></marker-refs></topic>',
            '<topic id="case-3"><title>税收居民P1场景</title><labels><label>用例</label></labels><marker-refs><marker-ref marker-id="priority-2"/></marker-refs></topic>',
            '<topic id="case-4"><title>列表展示P1场景</title><labels><label>用例</label></labels><marker-refs><marker-ref marker-id="priority-2"/></marker-refs></topic>',
            '<topic id="case-5"><title>文案展示P2场景</title><labels><label>用例</label></labels><marker-refs><marker-ref marker-id="priority-3"/></marker-refs></topic>',
            """
          <topic id="group-plan">
            <title>每个计划投保</title>
            <children>
              <topics type="attached">
                <topic id="case-plan-21"><title>计划21 0岁男宝20万保额</title><labels><label>用例</label></labels><marker-refs><marker-ref marker-id="priority-1"/></marker-refs></topic>
                <topic id="case-plan-22"><title>计划22 10岁女宝30万保额</title><labels><label>用例</label></labels><marker-refs><marker-ref marker-id="priority-1"/></marker-refs></topic>
              </topics>
            </children>
          </topic>
            """,
        ]
    )
    content_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<xmap-content xmlns="urn:xmind:xmap:xmlns:content:2.0">
  <sheet id="sheet-1">
    <topic id="root">
      <title>Selection Cases</title>
      <children>
        <topics type="attached">
          {case_topics}
        </topics>
      </children>
    </topic>
  </sheet>
</xmap-content>
"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("content.xml", content_xml)


@pytest.fixture
def tmp_skill_dir(tmp_path: Path) -> Path:
    """Creates a temporary skills directory with one valid skill."""
    skill_dir = tmp_path / "skills" / "test-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "MANIFEST.yaml").write_text(
        yaml.dump({
            "name": "test-skill",
            "version": "1.0",
            "description": "A test skill",
            "entry_script": None,
            "knowledge_files": ["references/knowledge/test.md"],
        }),
        encoding="utf-8",
    )
    return tmp_path / "skills"


def test_list_skills_real(tmp_skill_dir: Path):
    loader = SkillPackageLoader(base_dir=tmp_skill_dir)
    skills = loader.list_skills()
    assert "test-skill" in skills


def test_list_skills_empty_dir(tmp_path: Path):
    empty = tmp_path / "empty_skills"
    empty.mkdir()
    loader = SkillPackageLoader(base_dir=empty)
    assert loader.list_skills() == []


def test_list_skills_nonexistent_dir(tmp_path: Path):
    loader = SkillPackageLoader(base_dir=tmp_path / "does_not_exist")
    assert loader.list_skills() == []


def test_load_skill_success(tmp_skill_dir: Path):
    loader = SkillPackageLoader(base_dir=tmp_skill_dir)
    manifest = loader.load_skill("test-skill")
    assert isinstance(manifest, SkillManifest)
    assert manifest.name == "test-skill"
    assert manifest.version == "1.0"
    assert manifest.entry_script is None


def test_load_skill_not_found(tmp_skill_dir: Path):
    loader = SkillPackageLoader(base_dir=tmp_skill_dir)
    with pytest.raises(FileNotFoundError, match="not found"):
        loader.load_skill("nonexistent-skill")


def test_load_skill_missing_required_field(tmp_path: Path):
    skill_dir = tmp_path / "skills" / "bad-skill"
    skill_dir.mkdir(parents=True)
    # Missing 'version' field
    (skill_dir / "MANIFEST.yaml").write_text(
        yaml.dump({"name": "bad-skill", "description": "Missing version"}),
        encoding="utf-8",
    )
    loader = SkillPackageLoader(base_dir=tmp_path / "skills")
    with pytest.raises(ValueError, match="missing required fields"):
        loader.load_skill("bad-skill")


def test_run_entry_reports_invalid_json_with_skill_name(tmp_path: Path):
    skill_dir = tmp_path / "skill_packages" / "bad-json"
    scripts_dir = skill_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    (skill_dir / "MANIFEST.yaml").write_text(
        yaml.dump(
            {
                "name": "bad-json",
                "version": "1.0",
                "description": "Bad JSON skill",
                "entry_script": "scripts/bad_entry.py",
            }
        ),
        encoding="utf-8",
    )
    (scripts_dir / "bad_entry.py").write_text("print('not-json')\n", encoding="utf-8")

    loader = SkillPackageLoader(base_dir=tmp_path / "skill_packages")

    with pytest.raises(ValueError, match="Skill 'bad-json'.*invalid JSON"):
        loader.run_entry("bad-json", {})


def test_run_entry_allows_custom_timeout(tmp_path: Path):
    skill_dir = tmp_path / "skill_packages" / "slow-skill"
    scripts_dir = skill_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    (skill_dir / "MANIFEST.yaml").write_text(
        yaml.dump(
            {
                "name": "slow-skill",
                "version": "1.0",
                "description": "Slow skill",
                "entry_script": "scripts/slow_entry.py",
            }
        ),
        encoding="utf-8",
    )
    (scripts_dir / "slow_entry.py").write_text(
        "import time\n"
        "time.sleep(2)\n"
        "print('{}')\n",
        encoding="utf-8",
    )

    loader = SkillPackageLoader(base_dir=tmp_path / "skill_packages")

    with pytest.raises(RuntimeError, match="Skill 'slow-skill'.*timed out"):
        loader.run_entry("slow-skill", {}, timeout_seconds=1)


def test_list_skills_production_dir():
    """Integration test: check actual in-package skills directory has 7 skills."""
    # Only run if the production directory exists
    prod_dir = Path(__file__).parent.parent / "src" / "e2e_agent" / "skills"
    if not prod_dir.exists():
        pytest.skip("src/e2e_agent/skills/ not found")

    loader = SkillPackageLoader()
    skills = loader.list_skills()
    assert len(skills) == 7, f"Expected 7 skills, got {len(skills)}: {skills}"

    expected = {
        "mpt-ins-klg-gen",
        "mpt-ins-prd-ana",
        "mpt-ins-tc-gen",
        "mpt-ins-ts-gen",
        "mpt-reg-exec",
        "mpt-reg-case-merge",
        "mpt-reg-path-extract",
    }
    assert set(skills) == expected


def test_loads_repo_klg_gen_manifest():
    loader = SkillPackageLoader()
    manifest = loader.load_skill("mpt-ins-klg-gen")

    assert manifest.entry_script == "scripts/run_klg_gen.py"
    assert manifest.input_schema == "klg-gen-entry-input.schema.json"
    assert manifest.output_schema == "klg-gen-entry-output.schema.json"
    assert manifest.requires_node is False


def test_production_skill_manifest_schema_files_exist():
    loader = SkillPackageLoader()
    for skill_name in loader.list_skills():
        manifest = loader.load_skill(skill_name)
        skill_dir = loader.get_skill_dir(skill_name)
        if manifest.input_schema:
            assert (skill_dir / manifest.input_schema).exists()
        if manifest.output_schema:
            assert (skill_dir / manifest.output_schema).exists()


def test_run_entry_executes_repo_ts_gen(tmp_path: Path):
    loader = SkillPackageLoader()
    result = loader.run_entry(
        "mpt-ins-ts-gen",
        {
            "product_id": "test-product",
            "entry_url": "https://example.com/pc",
            "regression_flow": {
                "nodes": [
                    {"node_id": "NODE-start", "type": "start"},
                    {"node_id": "NODE-product", "type": "form", "page_name": "Product Detail"},
                    {"node_id": "NODE-result", "type": "result", "page_name": "Result"},
                    {"node_id": "NODE-end", "type": "end"},
                ]
            },
            "regression_paths": [
                {
                    "path_id": "PATH-001",
                    "case_ids": ["CASE-001"],
                    "nodes": ["NODE-start", "NODE-product", "NODE-result", "NODE-end"],
                    "conditions": {"plan": "standard"},
                    "priority": "P0",
                }
            ],
            "materialise": True,
            "root_dir": str(tmp_path),
        },
    )
    assert len(result["page_functions"]) == 2
    assert len(result["scenarios"]) == 1
    assert (tmp_path / "products" / "test-product" / "agent3" / "ts-gen" / "pc" / "scenarios").exists()
    assert (
        tmp_path
        / "products"
        / "test-product"
        / "agent3"
        / "script-plan"
        / "scenario-page-elements.json"
    ).exists()


def test_run_entry_executes_repo_reg_exec_without_scenarios(tmp_path: Path):
    loader = SkillPackageLoader()
    result = loader.run_entry(
        "mpt-reg-exec",
        {
            "product_id": "demo-product",
            "run_id": "run-001",
            "scenarios": [],
            "assertion_results": [],
            "root_dir": str(tmp_path),
        },
    )
    assert result["reports"][0]["execution_entry"] == "mpt-reg-exec"
    assert result["reports"][0]["failure_category_source"] == "mpt-reg-exec.rule_classifier"
    assert any("No scenarios available for execution" in item for item in result["warnings"])


def test_run_entry_executes_repo_case_merge(tmp_path: Path):
    prd_path = tmp_path / "prd.md"
    manual_path = tmp_path / "manual.md"
    prd_path.write_text(
        "# PRD\n\n## AC 投保成功\n- 用户可以成功提交投保申请并完成支付\n",
        encoding="utf-8",
    )
    manual_path.write_text(
        "# CASE 投保成功主链路\n\n- 步骤: 打开产品详情页\n- 步骤: 填写投保信息\n- 步骤: 完成支付\n- 预期: 出单结果页展示成功\n",
        encoding="utf-8",
    )
    loader = SkillPackageLoader()
    result = loader.run_entry(
        "mpt-reg-case-merge",
        {
            "product_id": "demo-product",
            "prd_path": str(prd_path),
            "manual_cases_path": str(manual_path),
        },
    )
    assert result["merged_cases"]
    assert result["candidate_cases"]
    assert result["selection_trace"]["selection_policy"]["name"] == "business-scenario-orchestration-v1"
    assert result["merged_cases"][0]["business_intent"]
    assert result["parse_trace"]
    assert "warnings" in result


def test_run_entry_executes_repo_case_merge_with_bom_json_manual_cases(tmp_path: Path):
    prd_path = tmp_path / "prd.md"
    manual_path = tmp_path / "manual.json"
    prd_path.write_text(
        "# PRD\n\n## AC 投保成功\n- 用户可以成功提交投保申请并完成支付\n",
        encoding="utf-8",
    )
    manual_path.write_bytes(
        b"\xef\xbb\xbf"
        + json.dumps(
            {
                "cases": [
                    {
                        "case_id": "MANUAL-001",
                        "title": "少儿重疾标准投保成功主链路 P0",
                        "priority": "P0",
                        "steps": ["打开产品详情页", "填写投保信息", "完成支付"],
                        "assertions": ["投保成功", "出单结果页展示成功"],
                    }
                ]
            },
            ensure_ascii=False,
        ).encode("utf-8")
    )
    loader = SkillPackageLoader()
    result = loader.run_entry(
        "mpt-reg-case-merge",
        {
            "product_id": "demo-product",
            "prd_path": str(prd_path),
            "manual_cases_path": str(manual_path),
        },
    )

    assert result["merged_cases"]
    assert not any("Failed to parse manual case JSON" in item for item in result["warnings"])


def test_run_entry_executes_repo_case_merge_with_travel_scenario_titles(tmp_path: Path):
    prd_path = tmp_path / "travel-prd.md"
    prd_path.write_text(
        (
            "# 示例旅行产品\n\n"
            "## AC 试算\n"
            "- 用户可以选择境外旅游目的地和旅行日期完成保费试算\n\n"
            "## AC 投保\n"
            "- 用户填写旅客证件、出行目的地和旅行期间后提交投保\n\n"
            "## AC 出单\n"
            "- 承保成功后生成包含旅行目的地和保障期间的电子保单\n"
        ),
        encoding="utf-8",
    )

    loader = SkillPackageLoader()
    result = loader.run_entry(
        "mpt-reg-case-merge",
        {
            "product_id": "travel-product",
            "product_name": "示例旅行产品-方案A",
            "prd_path": str(prd_path),
            "manual_cases_path": None,
        },
    )

    titles = [str(case.get("title") or "") for case in result["merged_cases"]]
    assert titles
    assert any("旅游" in title or "旅行" in title for title in titles)
    assert not any("少儿重疾" in title for title in titles)


def test_run_entry_executes_repo_case_merge_with_xlsx_manual_cases(tmp_path: Path):
    prd_path = tmp_path / "prd.md"
    manual_path = tmp_path / "manual.xlsx"
    prd_path.write_text(
        "# PRD\n\n## AC 税收居民\n- 支持仅为中国税收居民\n",
        encoding="utf-8",
    )
    _write_minimal_xlsx(
        manual_path,
        [
            ["title", "steps", "assertions", "preconditions", "priority"],
            ["税收居民场景", "打开投保页\n选择税收居民身份", "页面展示税收居民选项", "产品已上线", "P0"],
        ],
    )
    loader = SkillPackageLoader()
    result = loader.run_entry(
        "mpt-reg-case-merge",
        {
            "product_id": "demo-product",
            "prd_path": str(prd_path),
            "manual_cases_path": str(manual_path),
        },
    )
    assert result["merged_cases"]
    assert result["parse_trace"][0]["format"] == "xlsx"
    assert result["parse_trace"][0]["case_count"] == 1


def test_run_entry_executes_repo_case_merge_with_xmind_manual_cases(tmp_path: Path):
    prd_path = tmp_path / "prd.md"
    manual_path = tmp_path / "manual.xmind"
    prd_path.write_text(
        "# PRD\n\n## AC 税收居民\n- 税收居民身份阻断场景\n",
        encoding="utf-8",
    )
    _write_minimal_xmind(manual_path)
    loader = SkillPackageLoader()
    result = loader.run_entry(
        "mpt-reg-case-merge",
        {
            "product_id": "demo-product",
            "prd_path": str(prd_path),
            "manual_cases_path": str(manual_path),
        },
    )
    assert result["merged_cases"]
    assert result["parse_trace"][0]["format"] == "xmind"
    assert result["parse_trace"][0]["parser"] == "xmind-content"
    assert result["parse_trace"][0]["case_count"] == 1


def test_run_entry_case_merge_selects_final_regression_set_from_xmind_candidates(tmp_path: Path):
    prd_path = tmp_path / "prd.md"
    manual_path = tmp_path / "manual.xmind"
    prd_path.write_text(
        "# PRD\n\n## AC 投保支付\n- 投保成功\n- 支付失败\n- 税收居民身份阻断场景\n",
        encoding="utf-8",
    )
    _write_selection_xmind(manual_path)
    loader = SkillPackageLoader()
    result = loader.run_entry(
        "mpt-reg-case-merge",
        {
            "product_id": "demo-product",
            "prd_path": str(prd_path),
            "manual_cases_path": str(manual_path),
        },
    )
    assert result["parse_trace"][0]["case_count"] == 7
    assert len(result["candidate_cases"]) > len(result["merged_cases"])
    assert result["excluded_cases"]
    assert result["selection_trace"]["summary"]["candidate_case_count"] == len(result["candidate_cases"])
    assert result["selection_trace"]["summary"]["selected_case_count"] == len(result["merged_cases"])
    assert result["selection_trace"]["selection_policy"]["name"] == "business-scenario-orchestration-v1"
    assert all("计划21" not in case["title"] and "计划22" not in case["title"] for case in result["merged_cases"])
    main_flow = next(case for case in result["merged_cases"] if case["business_intent"] == "main_flow")
    assert {"计划21 0岁男宝20万保额", "计划22 10岁女宝30万保额"} <= {
        item["value"] for item in main_flow["data_variants"]
    }
    assert main_flow["coverage_refs"]
    assert main_flow["rules"]


def test_run_entry_case_merge_always_includes_electronic_policy_consistency(tmp_path: Path):
    prd_path = tmp_path / "prd.md"
    prd_path.write_text(
        "# PRD\n\n## AC 投保支付\n- 投保成功\n- 支付成功后展示出单结果\n",
        encoding="utf-8",
    )
    loader = SkillPackageLoader()
    result = loader.run_entry(
        "mpt-reg-case-merge",
        {
            "product_id": "demo-product",
            "prd_path": str(prd_path),
        },
    )

    policy_case = next(
        case for case in result["merged_cases"] if case["business_intent"] == "policy"
    )
    combined_text = "\n".join(
        [
            policy_case["title"],
            policy_case["business_goal"],
            *policy_case["steps"],
            *policy_case["assertions"],
        ]
    )
    assert "电子保单" in combined_text
    assert "投保提交数据" in combined_text
    assert "一致" in combined_text
    assert any(
        ref["source"] == "required-rule"
        for ref in policy_case["coverage_refs"]
    )


def test_run_entry_case_merge_always_includes_underwriting_path(tmp_path: Path):
    prd_path = tmp_path / "prd.md"
    prd_path.write_text(
        "# PRD\n\n## AC 投保支付\n- 投保成功\n- 支付成功后展示出单结果\n",
        encoding="utf-8",
    )
    loader = SkillPackageLoader()
    result = loader.run_entry(
        "mpt-reg-case-merge",
        {
            "product_id": "demo-product",
            "prd_path": str(prd_path),
        },
    )

    underwriting_case = next(
        case for case in result["merged_cases"] if case["business_intent"] == "underwriting"
    )
    combined_text = "\n".join(
        [
            underwriting_case["title"],
            underwriting_case["business_goal"],
            *underwriting_case["steps"],
            *underwriting_case["assertions"],
        ]
    )
    assert "智能核保" in combined_text
    assert "核保接口" in combined_text
    assert any(
        ref["source"] == "required-rule"
        for ref in underwriting_case["coverage_refs"]
    )
    assert "标体" not in combined_text
    assert "除外" not in combined_text
    assert "拒保" not in combined_text
    assert "转人工核保" not in combined_text
    assert "函件回销" not in combined_text
    assert "隔代投保授权书" not in combined_text


def test_run_entry_executes_repo_path_extract():
    loader = SkillPackageLoader()
    result = loader.run_entry(
        "mpt-reg-path-extract",
        {
            "product_id": "demo-product",
            "entry_url": "https://example.com/pc",
            "merged_cases": [
                {
                    "case_id": "CASE-001",
                    "title": "投保成功",
                    "priority": "P0",
                    "steps": ["打开产品页", "填写投保表单", "确认订单", "支付成功"],
                    "assertions": ["结果页可见"],
                    "preconditions": [],
                    "tags": [],
                }
            ],
        },
    )
    assert result["regression_flow"]["product_id"] == "demo-product"
    assert result["regression_paths"]


def test_run_entry_executes_repo_prd_ana_and_materialises_artifacts(tmp_path: Path):
    loader = SkillPackageLoader()
    prd_path = tmp_path / "minimal-prd.md"
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
    result = loader.run_entry(
        "mpt-ins-prd-ana",
        {
            "product_id": "demo-product",
            "prd_path": str(prd_path),
            "root_dir": str(tmp_path),
        },
    )
    assert result["product_id"] == "demo-product"
    assert result["features"]
    assert result["application_flow"]
    assert result["traceability_matrix"]
    output_dir = tmp_path / "products" / "demo-product" / "prd-ana"
    assert (output_dir / "analysis.json").exists()
    assert (output_dir / "analysis.md").exists()


def test_run_entry_executes_repo_prd_ana_with_real_prd_like_tables(tmp_path: Path):
    loader = SkillPackageLoader()
    prd_path = tmp_path / "real-prd.md"
    prd_path.write_text(
        (
            "# 需求描述\n\n"
            "## 需求概述\n"
            "| 节点 | 业务场景 | 业务规则 | 是否已支持 | 产品方案概要 |\n"
            "| --- | --- | --- | --- | --- |\n"
            "| 投保 | 税收居民身份 | 支持仅为中国税收居民、仅为非居民；若选择非居民则需填写税收详细信息 | 已支持 | PS调整配置 |\n"
            "| 智能认证 | 反洗钱认证 | 外籍证件触发反洗钱时，仅采集投保人正面影像 | 已支持 | 历史能力扩展 |\n\n"
            "## PS、天枢\n"
            "| 投保属性 | PS keyCode | 是否必填 | 组件类别 | 业务规则 |\n"
            "| --- | --- | --- | --- | --- |\n"
            "| 税收相关信息 | | | | |\n"
            "| 现居地址-国家 | currentResidence | 是 | 下拉框 | 选择中国时展示中文地址，选择非中国时展示英文地址 |\n\n"
            "### 新增通用约束\n"
            "1. 税收信息组合必填校验：A137\n"
            "    1. ps新增约束\n"
            "    2. 天枢前端校验：第二组、第三组税收信息必须组合必填\n"
        ),
        encoding="utf-8",
    )

    result = loader.run_entry(
        "mpt-ins-prd-ana",
        {
            "product_id": "demo-product",
            "prd_path": str(prd_path),
            "root_dir": str(tmp_path),
            "materialise": False,
        },
    )

    feature_names = [item["name"] for item in result["features"]]
    assert "投保 / 税收居民身份" in feature_names
    assert "税收相关信息 / 现居地址-国家" in feature_names
    assert "新增通用约束 / 税收信息组合必填校验：A137" in feature_names
    assert [step["page"] for step in result["application_flow"]] == ["投保", "智能认证"]
    assert result["traceability_matrix"][0]["requirement_ref"].endswith("-AC-01")


def test_run_entry_executes_repo_tc_gen_phase_one_only(tmp_path: Path):
    loader = SkillPackageLoader()
    prd_analysis = {
        "product_id": "demo-product",
        "analysis_version": "1.0",
        "features": [
            {
                "feature_id": "FEAT-001-product-display",
                "name": "产品展示",
                "description": "展示产品信息",
                "acceptance_criteria": ["用户查看产品说明", "用户进入投保流程"],
                "priority": "P0",
            }
        ],
        "application_flow": [
            {
                "step": 1,
                "page": "产品展示",
                "action": "用户查看产品说明",
                "branching": False,
            }
        ],
        "dependencies": [],
    }
    result = loader.run_entry(
        "mpt-ins-tc-gen",
        {
            "product_id": "demo-product",
            "prd_analysis": prd_analysis,
            "root_dir": str(tmp_path),
        },
    )
    assert result["skeleton"]
    assert result["skeleton"][0]["feature_id"] == "FEAT-001-product-display"
    assert result["skeleton"][0]["steps"]
    output_dir = tmp_path / "products" / "demo-product" / "tc-gen"
    assert (output_dir / "test-cases-skeleton.json").exists()
    assert (output_dir / "test-cases-skeleton.md").exists()
    assert not (output_dir / "test-paths.json").exists()
    assert not (output_dir / "test-paths.md").exists()


def test_run_entry_executes_repo_tc_gen_with_project_adapted_titles_and_types(tmp_path: Path):
    loader = SkillPackageLoader()
    prd_analysis = {
        "product_id": "demo-product",
        "analysis_version": "1.1",
        "features": [
            {
                "feature_id": "FEAT-001-tax-resident",
                "name": "投保 / 税收居民身份",
                "description": "真实 PRD 拆解后的业务规则",
                "acceptance_criteria": [
                    "支持仅为中国税收居民、仅为非居民",
                    "若选择非居民则需填写税收详细信息",
                    "英文姓+名长度不得超过32个字符，否则阻断并提示",
                ],
                "priority": "P0",
            }
        ],
        "application_flow": [],
        "dependencies": [],
    }
    result = loader.run_entry(
        "mpt-ins-tc-gen",
        {
            "product_id": "demo-product",
            "prd_analysis": prd_analysis,
            "root_dir": str(tmp_path),
            "materialise": False,
        },
    )

    case = result["skeleton"][0]
    assert case["title"] == "投保-税收居民身份 用例"
    assert case["type"] == "negative"
    assert case["steps"][0]["action"] == "进入 投保 节点，定位到 税收居民身份 场景"

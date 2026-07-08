from __future__ import annotations


def test_prd_analysis_markdown_uses_chinese_section_titles() -> None:
    from e2e_agent.core.prd_analysis import render_analysis_markdown

    markdown = render_analysis_markdown(
        {
            "product_id": "demo-product",
            "features": [
                {
                    "name": "产品展示",
                    "acceptance_criteria": ["用户可以查看产品说明"],
                }
            ],
            "application_flow": [
                {"step": 1, "page": "产品详情页", "action": "查看产品说明"}
            ],
            "traceability_matrix": [
                {
                    "requirement_ref": "FEAT-001",
                    "acceptance_criterion": "用户可以查看产品说明",
                    "downstream_hint": {"case_seed": "产品展示用例"},
                }
            ],
        }
    )

    assert "# demo-product PRD 分析" in markdown
    assert "## 功能清单" in markdown
    assert "## 应用流程" in markdown
    assert "## 需求追踪矩阵" in markdown
    assert "PRD Analysis" not in markdown
    assert "Features" not in markdown
    assert "Application Flow" not in markdown
    assert "Traceability Matrix" not in markdown


def test_case_skeleton_markdown_uses_chinese_labels() -> None:
    from e2e_agent.core.case_skeleton import render_skeleton_markdown

    markdown = render_skeleton_markdown(
        [
            {
                "id": "TC-001",
                "feature_id": "FEAT-001",
                "title": "产品展示用例",
                "type": "happy_path",
                "priority": "P0",
                "steps": [
                    {
                        "step": 1,
                        "action": "打开产品详情页",
                        "expected": "展示产品说明",
                    }
                ],
            }
        ]
    )

    assert "# 测试用例骨架" in markdown
    assert "- 功能编号: FEAT-001" in markdown
    assert "- 优先级: P0" in markdown
    assert "- 用例类型: 正向主流程" in markdown
    assert "- 步骤:" in markdown
    assert "Test Case Skeleton" not in markdown
    assert "feature_id" not in markdown
    assert "happy_path" not in markdown

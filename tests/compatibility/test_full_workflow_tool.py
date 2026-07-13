from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]


def test_full_workflow_tool_replaces_demo_runner() -> None:
    full_runner = ROOT_DIR / "tools" / "legacy" / "run_full_workflow.py"
    demo_runner = ROOT_DIR / "tools" / "run_demo_workflow.py"

    assert full_runner.exists()
    assert not demo_runner.exists()

    source = full_runner.read_text(encoding="utf-8").lower()
    forbidden_tokens = [
        "demo",
        "first_path_only",
        "agent3_gate_disabled",
        "ignored_errors",
        "demo_mode",
        "agent4_ignore_agent3_blocks",
    ]
    for token in forbidden_tokens:
        assert token not in source


def test_prepare_agent4_scenarios_filters_only_completed_paths() -> None:
    from tools.legacy.run_full_workflow import prepare_agent4_scenarios

    state = {
        "merged_cases": [
            {"case_id": "TC-001", "priority": "P0"},
            {"case_id": "TC-002", "priority": "P1"},
        ],
        "scenarios": [
            {
                "path_id": "PATH-001",
                "case_ids": ["TC-001"],
                "coverage_status": "covered",
                "completion_rule": {"is_complete": True},
                "entry_url": None,
            },
            {
                "path_id": "PATH-002",
                "case_ids": ["TC-002"],
                "coverage_status": "coverage-gap",
                "completion_rule": {"is_complete": False},
                "blocked_node": "NODE-policy-service",
            },
        ]
    }

    result = prepare_agent4_scenarios(
        state,
        "https://example.com/detail",
        required_case_ids={"TC-001"},
    )

    assert result == {
        "runnable": 1,
        "blocked": 1,
        "blocked_required": 0,
        "blocked_non_required": 1,
    }
    assert [item["path_id"] for item in state["scenarios"]] == ["PATH-001"]
    assert state["scenarios"][0]["entry_url"] == "https://example.com/detail"
    assert [item["path_id"] for item in state["agent4_blocked_scenarios"]] == ["PATH-002"]


def test_prepare_agent4_scenarios_flags_blocked_required_cases() -> None:
    from tools.legacy.run_full_workflow import prepare_agent4_scenarios

    state = {
        "scenarios": [
            {
                "path_id": "PATH-001",
                "case_ids": ["TC-001"],
                "coverage_status": "coverage-gap",
                "completion_rule": {"is_complete": False},
                "blocked_node": "NODE-payment",
            },
        ]
    }

    result = prepare_agent4_scenarios(
        state,
        "https://example.com/detail",
        required_case_ids={"TC-001"},
    )

    assert result["runnable"] == 0
    assert result["blocked"] == 1
    assert result["blocked_required"] == 1
    assert result["blocked_non_required"] == 0


def test_make_blocked_agent4_result_builds_quarantine_report() -> None:
    from tools.legacy.run_full_workflow import make_blocked_agent4_result

    result = make_blocked_agent4_result(
        [
            {
                "path_id": "PATH-001",
                "case_ids": ["TC-001"],
                "blocked_node": "NODE-payment",
                "blocked_reason": "Agent3 did not complete payment page",
            }
        ],
        product_id="demo-product",
        run_id="run-001",
    )

    assert result["quarantine_report"]["summary"]["total"] == 1
    assert result["quarantine_report"]["summary"]["blocking"] == 1
    assert result["quarantine_report"]["items"][0]["case_id"] == "TC-001"
    assert result["quarantine_report"]["items"][0]["failure_category"] == "agent3_contract_blocked"


def test_required_agent4_case_ids_defaults_to_p0_cases() -> None:
    from tools.legacy.run_full_workflow import required_agent4_case_ids

    state = {
        "merged_cases": [
            {"case_id": "TC-001", "priority": "P0"},
            {"case_id": "TC-002", "priority": "P1"},
        ]
    }

    assert required_agent4_case_ids(state, {"P0"}) == {"TC-001"}


def test_materialise_id_card_preview_assets_copies_front_and_back(monkeypatch, tmp_path) -> None:
    import tools.legacy.run_full_workflow as runner

    source_dir = tmp_path / "source"
    source_dir.mkdir()
    front = source_dir / "front-source.jpg"
    back = source_dir / "back-source.jpg"
    front.write_bytes(b"front-image")
    back.write_bytes(b"back-image")
    product_dir = tmp_path / "product"

    monkeypatch.setattr(runner, "_resolve_id_card_image_paths", lambda _mock_data: (front, back))

    result = runner.materialise_id_card_preview_assets(product_dir, {"applicant.name": "A"})

    assert [path.name for path in result] == ["id-card-front.jpg", "id-card-back.jpg"]
    assert (product_dir / ".tmp" / "id-card-preview" / "id-card-front.jpg").read_bytes() == b"front-image"
    assert (product_dir / ".tmp" / "id-card-preview" / "id-card-back.jpg").read_bytes() == b"back-image"


def test_materialise_id_card_preview_assets_reuses_single_source_for_back(monkeypatch, tmp_path) -> None:
    import tools.legacy.run_full_workflow as runner

    source = tmp_path / "source.jpg"
    source.write_bytes(b"single-image")
    product_dir = tmp_path / "product"

    monkeypatch.setattr(runner, "_resolve_id_card_image_paths", lambda _mock_data: (source,))

    result = runner.materialise_id_card_preview_assets(product_dir, {"applicant.name": "A"})

    assert [path.name for path in result] == ["id-card-front.jpg", "id-card-back.jpg"]
    assert (product_dir / ".tmp" / "id-card-preview" / "id-card-front.jpg").read_bytes() == b"single-image"
    assert (product_dir / ".tmp" / "id-card-preview" / "id-card-back.jpg").read_bytes() == b"single-image"


def test_materialise_agent_outputs_writes_quarantine_report(tmp_path) -> None:
    from tools.legacy.run_full_workflow import materialise_agent_outputs

    product_dir = tmp_path / "product"
    materialise_agent_outputs(
        product_dir,
        {
            "quarantine_report": {
                "summary": {"total": 1, "blocking": 1, "by_category": {"product_bug": 1}, "by_status": {"new": 1}},
                "items": [{"case_id": "TC-001"}],
            }
        },
    )

    quarantine_path = product_dir / "agent4" / "quarantine.json"
    assert quarantine_path.exists()
    assert "TC-001" in quarantine_path.read_text(encoding="utf-8")


def test_materialise_agent_outputs_skips_missing_quarantine_report(tmp_path) -> None:
    from tools.legacy.run_full_workflow import materialise_agent_outputs

    product_dir = tmp_path / "product"
    materialise_agent_outputs(product_dir, {})

    assert not (product_dir / "agent4" / "quarantine.json").exists()


def test_build_summary_includes_quarantine_counts(tmp_path) -> None:
    from tools.legacy.run_full_workflow import build_summary

    summary = build_summary(
        run_id="run-001",
        product_dir=tmp_path / "product",
        mock_data_path=tmp_path / "mock-data.json",
        state={
            "product_id": "demo-product",
            "run_dir": str(tmp_path / "run"),
            "reports": [{"summary": {"total": 3}}],
            "artifact_fingerprints": [
                {
                    "model_routed": "gpt-4o",
                    "is_fallback": False,
                    "token_usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
                    "cost_usd": 0.01,
                }
            ],
            "assertion_template_summary": {"template_coverage_rate": 1.0},
            "quarantine_report": {
                "summary": {"total": 2, "blocking": 1, "by_category": {}, "by_status": {}}
            },
        },
        regression_paths=[],
        final_blocked_paths=[],
        agent1_result={},
        agent2_result={},
        agent3_result={},
        agent4_result={},
        html_report=str(tmp_path / "report.html"),
    )

    assert summary["agent4"]["quarantine_total"] == 2
    assert summary["agent4"]["quarantine_blocking"] == 1
    assert summary["cost"]["total_cost_usd"] == 0.01
    assert summary["evaluation"]["template_coverage_rate"] == 1.0


def test_read_json_accepts_utf8_bom(tmp_path) -> None:
    from tools.legacy.run_full_workflow import read_json

    path = tmp_path / "product-input.json"
    path.write_bytes(b"\xef\xbb\xbf{\"product_id\":\"demo-product\"}")

    assert read_json(path) == {"product_id": "demo-product"}


def test_product_config_from_input_does_not_force_live_agent3_mode() -> None:
    from tools.legacy.run_full_workflow import product_config_from_input

    assert product_config_from_input({"product_id": "demo-product"}) == {}
    assert product_config_from_input(
        {"product_id": "demo-product"},
        {"agent3_mode": "static-first", "platform": "pc"},
    ) == {"agent3_mode": "static-first", "platform": "pc"}
    assert product_config_from_input(
        {
            "product_id": "demo-product",
            "product_config": {"agent3_mode": "live"},
        },
        {"agent3_mode": "static-first", "platform": "pc"},
    ) == {"agent3_mode": "live", "platform": "pc"}
    assert product_config_from_input({"product_id": "demo-product", "agent3_mode": "live"}) == {
        "agent3_mode": "live"
    }
    assert product_config_from_input(
        {
            "product_id": "demo-product",
            "product_config": {"agent3_mode": "static-first", "platform": "pc"},
        }
    ) == {"agent3_mode": "static-first", "platform": "pc"}


def test_read_source_product_config_accepts_utf8_bom(tmp_path) -> None:
    from tools.legacy.run_full_workflow import read_source_product_config

    config_path = tmp_path / "automation" / "product.config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_bytes(b"\xef\xbb\xbf{\"agent3_mode\":\"static-first\"}")

    assert read_source_product_config(tmp_path) == {"agent3_mode": "static-first"}


def test_resolve_product_dirs_splits_source_and_sibling_assets(tmp_path, monkeypatch) -> None:
    import tools.legacy.run_full_workflow as runner

    monkeypatch.setattr(runner, "ROOT_DIR", tmp_path)

    product_source_dir = tmp_path / "products" / "demo-product" / "demo-plan"
    product_source_dir.mkdir(parents=True)
    product_input_path = product_source_dir / "product-input.json"
    product_input_path.write_text('{"product_id": "demo-product"}', encoding="utf-8")

    dirs = runner.resolve_product_dirs(product_input_path)

    assert dirs["product_source_dir"] == product_source_dir
    assert dirs["product_artifact_dir"] == product_source_dir.with_name("demo-plan.assets")


def test_resolve_product_dirs_preserves_legacy_assets_input(tmp_path, monkeypatch) -> None:
    import tools.legacy.run_full_workflow as runner

    monkeypatch.setattr(runner, "ROOT_DIR", tmp_path)

    product_source_dir = tmp_path / "products" / "demo-product" / "demo.assets"
    product_source_dir.mkdir(parents=True)
    product_input_path = product_source_dir / "product-input.json"
    product_input_path.write_text('{"product_id": "demo-product"}', encoding="utf-8")

    dirs = runner.resolve_product_dirs(product_input_path)

    assert dirs["product_source_dir"] == product_source_dir
    assert dirs["product_artifact_dir"] == product_source_dir


def test_full_workflow_report_is_rendered_with_final_blocked_paths() -> None:
    full_runner = ROOT_DIR / "tools" / "legacy" / "run_full_workflow.py"
    source = full_runner.read_text(encoding="utf-8")

    assert 'agent4_result.get("html_report") or' not in source
    assert "blocked_paths=final_blocked_paths" in source

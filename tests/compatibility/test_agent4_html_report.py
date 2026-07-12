from __future__ import annotations

import json
from pathlib import Path


def test_agent4_html_report_includes_results_screenshots_and_api_errors(tmp_path: Path):
    from e2e_agent.legacy.agents.agent4_exec.report import generate_agent4_html_report

    run_dir = tmp_path / "runs" / "agent3-agent4-20260518-100000"
    merged_cases_path = tmp_path / "agent1" / "merged-cases.json"
    merged_cases_path.parent.mkdir(parents=True)
    merged_cases_path.write_text(
        json.dumps(
            [
                {
                    "case_id": "TC-MERGED-001",
                    "title": "合并用例-投保主链路",
                    "priority": "P0",
                    "business_intent": "main_flow",
                    "steps": ["进入产品详情页", "填写投保信息", "提交订单"],
                    "coverage_refs": [{"case_id": "TC-001"}, {"case_id": "TC-002"}],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    agent4_shots = run_dir / "agent4" / "screenshots"
    agent4_shots.mkdir(parents=True)
    formal_shot = agent4_shots / "01-submit.png"
    formal_shot.write_bytes(b"fake-png")

    submit_dir = run_dir / "submit-screenshots"
    submit_dir.mkdir(parents=True)
    exploration_shot = submit_dir / "1778991100000-after-submit.png"
    exploration_shot.write_bytes(b"fake-png")
    (submit_dir / "1778991100000-after-submit.json").write_text(
        json.dumps(
            {
                "phase": "after",
                "action_text": "submit",
                "screenshot": str(exploration_shot),
                "state": {
                    "url": "https://example.test/task",
                    "popups": [{"text": "system error"}],
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    (run_dir / "api-errors.jsonl").write_text(
        json.dumps(
            {
                "event": "response",
                "url": "https://example.test/api/apps/cps/insure/task/next/do",
                "body": json.dumps(
                    {
                        "code": 41011,
                        "data": {"taskType": 4, "canPay": False},
                        "msg": "system error",
                        "success": False,
                    }
                ),
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    reports = [
        {
            "run_id": "agent3-agent4-20260518-100000",
            "summary": {"total": 1, "passed": 0, "failed": 1, "skipped": 0, "error": 0},
            "results": [
                {
                    "case_id": "TC-001",
                    "path_id": "PATH-001",
                    "status": "failed",
                    "execution_status": "failed",
                    "blocked_reason": "standard underwriting taskType=4 code=41011 system error",
                    "error_message": "standard underwriting taskType=4 code=41011 system error",
                    "screenshots": [
                        {
                            "step": 0,
                            "label": "initial-page",
                            "path": str(formal_shot),
                            "url": "https://example.test/insure",
                        },
                        {
                            "step": 1,
                            "label": "after-submit",
                            "path": str(formal_shot),
                            "url": "https://example.test/task",
                        }
                    ],
                    "executed_actions": [
                        {
                            "step": 1,
                            "text": "submit order",
                            "source_url": "https://example.test/insure",
                            "target_url": "https://example.test/task",
                            "planned_from_node_id": "NODE-insure-form",
                            "planned_to_node_id": "NODE-underwriting",
                        }
                    ],
                    "node_progress": [
                        {
                            "node_id": "NODE-underwriting",
                            "status": "matched",
                            "action_used": {
                                "submit_diagnostics": [
                                    {
                                        "phase": "after-submit",
                                        "screenshot": str(exploration_shot),
                                    }
                                ]
                            },
                        }
                    ],
                    "page_keys": [
                        {
                            "node_id": "NODE-product-detail",
                            "page_key": "PK-product-detail",
                            "url_pattern": "/product/detail",
                        },
                        {
                            "node_id": "NODE-insure-form",
                            "page_key": "PK-product-insure",
                            "url_pattern": "/insure",
                        },
                        {
                            "node_id": "NODE-underwriting",
                            "page_key": "PK-underwriting",
                            "url_pattern": "/task",
                        },
                    ],
                }
            ],
        }
    ]

    report_path = generate_agent4_html_report(
        run_dir=run_dir,
        product_id="demo-product",
        run_id="agent3-agent4-20260518-100000",
        reports=reports,
    )

    html = report_path.read_text(encoding="utf-8")
    assert report_path == run_dir / "report.html"
    assert "Agent4 Execution Report" in html
    assert "PATH-001" in html
    assert "TC-001" in html
    assert "Merged Test Cases" in html
    assert "TC-MERGED-001" in html
    assert "合并用例-投保主链路" in html
    assert "Path Replay Chains" in html
    assert "Path Replay Chains (Agent4 Official)" in html
    assert "Agent3 Exploration Evidence" in html
    assert "NODE-product-detail" in html
    assert "NODE-insure-form" in html
    assert "NODE-underwriting" in html
    assert "submit order" in html
    assert "standard underwriting taskType=4 code=41011 system error" in html
    assert "api/apps/cps/insure/task/next/do" in html
    assert "taskType=4" in html
    assert "41011" in html
    assert "agent4/screenshots/01-submit.png" in html.replace("\\", "/")
    assert "submit-screenshots/1778991100000-after-submit.png" in html.replace("\\", "/")

    data = json.loads((run_dir / "report-data.json").read_text(encoding="utf-8"))
    data_text = json.dumps(data, ensure_ascii=False).replace("\\", "/")
    assert data["summary"]["failed"] == 1
    assert data["merged_case_count"] == 1
    assert data["merged_cases"][0]["case_id"] == "TC-MERGED-001"
    assert data["formal_screenshot_count"] == 2
    assert "1778991100000-after-submit.png" in data_text
    assert data["exploration_evidence_count"] == 1
    assert data["exploration_evidence"][0]["relative_path"].replace("\\", "/") == (
        "submit-screenshots/1778991100000-after-submit.png"
    )
    assert data["exploration_evidence"][0]["source"] == "Agent3"
    assert data["api_error_count"] == 1
    assert data["path_chains"][0]["path_id"] == "PATH-001"
    assert [node["node_id"] for node in data["path_chains"][0]["nodes"]] == [
        "NODE-product-detail",
        "NODE-insure-form",
        "NODE-underwriting",
    ]
    assert data["path_chains"][0]["nodes"][0]["screenshots"][0]["label"] == "initial-page"
    assert data["path_chains"][0]["nodes"][2]["screenshots"][0]["label"] == "after-submit"
    chain_shot_sources = [
        shot["source"]
        for chain in data["path_chains"]
        for node in chain["nodes"]
        for shot in node["screenshots"]
    ]
    assert chain_shot_sources
    assert set(chain_shot_sources) == {"Agent4"}


def test_agent4_html_report_includes_side_effect_probe_results(tmp_path: Path):
    from e2e_agent.legacy.agents.agent4_exec.report import generate_agent4_html_report

    run_dir = tmp_path / "runs" / "agent4-probe-run"
    run_dir.mkdir(parents=True)

    reports = [
        {
            "run_id": "agent4-probe-run",
            "summary": {"total": 1, "passed": 1, "failed": 0, "skipped": 0, "error": 0},
            "side_effect_probes": {
                "summary": {"total": 3, "success": 1, "fail": 1, "na": 1},
                "results": [
                    {
                        "probe_id": "order-issued",
                        "status": "success",
                        "evidence": {"data.orderStatus": "issued"},
                        "failures": [],
                        "downgrade_reason": None,
                    },
                    {
                        "probe_id": "underwriting-standard",
                        "status": "fail",
                        "evidence": {"data.canPay": False},
                        "failures": [
                            {
                                "field": "data.canPay",
                                "operator": "equals",
                                "expected": True,
                                "actual": False,
                            }
                        ],
                        "downgrade_reason": None,
                    },
                    {
                        "probe_id": "payment-query",
                        "status": "na",
                        "evidence": {},
                        "failures": [],
                        "downgrade_reason": "permission: missing backend permission",
                    },
                ],
            },
            "results": [
                {
                    "case_id": "TC-001",
                    "path_id": "PATH-001",
                    "status": "passed",
                    "execution_status": "passed",
                }
            ],
        }
    ]

    report_path = generate_agent4_html_report(
        run_dir=run_dir,
        product_id="demo-product",
        run_id="agent4-probe-run",
        reports=reports,
    )

    data = json.loads((run_dir / "report-data.json").read_text(encoding="utf-8"))
    html = report_path.read_text(encoding="utf-8")

    assert data["side_effect_probes"]["summary"] == {"total": 3, "success": 1, "fail": 1, "na": 1}
    assert data["side_effect_probes"]["results"][1]["failures"][0]["field"] == "data.canPay"
    assert "Side-effect Probes" in html
    assert "order-issued" in html
    assert "underwriting-standard" in html
    assert "class='pill passed'>success" in html
    assert "class='pill failed'>fail" in html
    assert "class='pill skipped'>na" in html
    assert "permission: missing backend permission" in html


def test_agent4_html_report_includes_payment_closed_loop_operations(tmp_path: Path):
    from e2e_agent.legacy.agents.agent4_exec.report import generate_agent4_html_report

    run_dir = tmp_path / "runs" / "agent4-payment-run"
    run_dir.mkdir(parents=True)

    reports = [
        {
            "run_id": "agent4-payment-run",
            "summary": {"total": 1, "passed": 1, "failed": 0, "skipped": 0, "error": 0},
            "results": [
                {
                    "case_id": "TC-001",
                    "path_id": "PATH-001",
                    "status": "passed",
                    "execution_status": "passed",
                    "payment_closed_loop": {
                        "required": True,
                        "status": "passed-after-resume",
                        "payment_method": "wechat",
                        "issue_status": 1,
                        "artifact": str(run_dir / "agent4" / "tc-exec" / "01-path-001" / "external-ops" / "issue.json"),
                    },
                    "external_operations": [
                        {
                            "operation_id": "SCN-001-PATH-001-huize-pay-success",
                            "operation_type": "huize-pay-success",
                            "status": "passed",
                            "payment_method": "wechat",
                            "gateway_pay_num_source": "runtime-payment-boundary",
                        },
                        {
                            "operation_id": "SCN-001-PATH-001-huize-issue-status",
                            "operation_type": "huize-issue-status",
                            "status": "passed",
                            "payment_method": "wechat",
                            "gateway_pay_num_source": "runtime-payment-boundary",
                            "issue_status": 1,
                        },
                    ],
                }
            ],
        }
    ]

    report_path = generate_agent4_html_report(
        run_dir=run_dir,
        product_id="demo-product",
        run_id="agent4-payment-run",
        reports=reports,
    )

    data = json.loads((run_dir / "report-data.json").read_text(encoding="utf-8"))
    html = report_path.read_text(encoding="utf-8")

    assert data["external_operations"]["summary"] == {
        "total": 2,
        "passed": 2,
        "failed": 0,
        "missing": 0,
    }
    assert data["external_operations"]["results"][1]["issue_status"] == 1
    assert "Payment Closed Loop" in html
    assert "SCN-001-PATH-001-huize-pay-success" in html
    assert "SCN-001-PATH-001-huize-issue-status" in html
    assert "passed-after-resume" in html


def test_agent4_html_report_scores_merged_cases_by_their_own_boundary(tmp_path: Path):
    from e2e_agent.legacy.agents.agent4_exec.report import generate_agent4_html_report

    run_dir = tmp_path / "runs" / "agent3-agent4-20260519-160000"
    merged_cases_path = tmp_path / "agent1" / "merged-cases.json"
    merged_cases_path.parent.mkdir(parents=True)
    merged_cases_path.write_text(
        json.dumps(
            [
                {
                    "case_id": "TC-test-product-001",
                    "title": "main flow",
                    "business_intent": "main_flow",
                    "steps": ["reach policy result"],
                },
                {
                    "case_id": "TC-test-product-002",
                    "title": "health notice",
                    "business_intent": "health_notice",
                    "steps": ["reach health notice"],
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    reports = [
        {
            "run_id": "agent3-agent4-20260519-160000",
            "summary": {"total": 2, "passed": 0, "failed": 2, "skipped": 0, "error": 0},
            "results": [
                {
                    "case_id": "TC-test-product-001",
                    "path_id": "PATH-001",
                    "status": "failed",
                    "execution_status": "failed",
                    "target_node": "NODE-policy-result",
                    "final_url": "https://example.test/product/insure",
                    "executed_actions": [
                        {
                            "step": 1,
                            "text": "buy now",
                            "source_url": "https://example.test/product/detail",
                            "target_url": "https://example.test/product/healthInform",
                            "planned_from_node_id": "NODE-product-detail",
                            "planned_to_node_id": "NODE-health-notice",
                        },
                        {
                            "step": 2,
                            "text": "answer health notice",
                            "source_url": "https://example.test/product/healthInform",
                            "target_url": "https://example.test/product/insure",
                            "planned_from_node_id": "NODE-health-notice",
                            "planned_to_node_id": "NODE-insure-form",
                        },
                    ],
                    "node_matches": [
                        {
                            "step": 1,
                            "matched_nodes": ["NODE-health-notice"],
                            "url": "https://example.test/product/healthInform",
                        }
                    ],
                    "page_keys": [
                        {
                            "node_id": "NODE-product-detail",
                            "page_key": "PK-product-detail",
                            "url_pattern": "/product/detail",
                        },
                        {
                            "node_id": "NODE-health-notice",
                            "page_key": "PK-health-notice",
                            "url_pattern": "/product/healthInform",
                        },
                        {
                            "node_id": "NODE-policy-result",
                            "page_key": "PK-policy-result",
                            "url_pattern": "/policy/result",
                        },
                    ],
                },
                {
                    "case_id": "TC-test-product-002",
                    "path_id": "PATH-001",
                    "status": "failed",
                    "execution_status": "failed",
                    "target_node": "NODE-policy-result",
                    "final_url": "https://example.test/product/insure",
                    "body_excerpt": "投保人信息 被保险人信息 提交订单",
                    "executed_actions": [
                        {
                            "step": 1,
                            "text": "buy now",
                            "target_url": "https://example.test/product/healthInform",
                            "planned_to_node_id": "NODE-health-notice",
                        }
                    ],
                    "node_matches": [
                        {
                            "step": 1,
                            "matched_nodes": ["NODE-health-notice"],
                            "url": "https://example.test/product/healthInform",
                        }
                    ],
                    "page_keys": [],
                },
            ],
        }
    ]

    report_path = generate_agent4_html_report(
        run_dir=run_dir,
        product_id="test-product",
        run_id="agent3-agent4-20260519-160000",
        reports=reports,
    )

    data = json.loads((run_dir / "report-data.json").read_text(encoding="utf-8"))
    case_status = {item["case_id"]: item["status"] for item in data["merged_case_statuses"]}

    assert data["merged_case_summary"] == {"total": 2, "passed": 1, "failed": 1, "skipped": 0, "error": 0}
    assert case_status["TC-test-product-001"] == "failed"
    assert case_status["TC-test-product-002"] == "passed"

    html = report_path.read_text(encoding="utf-8")
    assert "Merged Case Summary" in html
    assert "TC-test-product-002" in html
    assert "NODE-health-notice" in html


def test_agent4_html_report_does_not_hide_unexecuted_merged_cases(tmp_path: Path):
    from e2e_agent.legacy.agents.agent4_exec.report import generate_agent4_html_report

    run_dir = tmp_path / "runs" / "agent1-agent4-20260524-160000"
    merged_cases_path = tmp_path / "agent1" / "merged-cases.json"
    merged_cases_path.parent.mkdir(parents=True)
    merged_cases_path.write_text(
        json.dumps(
            [
                {
                    "case_id": "TC-test-product-001",
                    "title": "main flow",
                    "business_intent": "main_flow",
                    "priority": "P0",
                },
                {
                    "case_id": "TC-test-product-006",
                    "title": "policy service",
                    "business_intent": "policy",
                    "priority": "P1",
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    generate_agent4_html_report(
        run_dir=run_dir,
        product_id="test-product",
        run_id="agent1-agent4-20260524-160000",
        reports=[
            {
                "summary": {"total": 1, "passed": 1, "failed": 0, "skipped": 0, "error": 0},
                "results": [
                    {
                        "case_id": "TC-test-product-001",
                        "path_id": "PATH-001",
                        "status": "passed",
                        "execution_status": "passed",
                        "target_node": "NODE-policy-result",
                        "target_node_status": "reached",
                        "reached_target_node": "NODE-policy-result",
                    }
                ],
            }
        ],
    )

    data = json.loads((run_dir / "report-data.json").read_text(encoding="utf-8"))
    case_status = {item["case_id"]: item["status"] for item in data["merged_case_statuses"]}

    assert data["merged_case_summary"] == {"total": 2, "passed": 1, "failed": 0, "skipped": 1, "error": 0}
    assert case_status == {
        "TC-test-product-001": "passed",
        "TC-test-product-006": "skipped",
    }


def test_agent4_html_report_collects_playwright_test_result_screenshots(tmp_path: Path):
    from e2e_agent.legacy.agents.agent4_exec.report import generate_agent4_html_report

    run_dir = tmp_path / "runs" / "agent3-agent4-20260519-180000"
    shot_dir = run_dir / "agent4" / "tc-exec" / "test-results" / "01-path-001-SCN-001-PATH-001-chromium"
    shot_dir.mkdir(parents=True)
    formal_shot = shot_dir / "test-failed-1.png"
    formal_shot.write_bytes(b"fake-png")

    reports = [
        {
            "run_id": "agent3-agent4-20260519-180000",
            "summary": {"total": 1, "passed": 0, "failed": 1, "skipped": 0, "error": 0},
            "results": [
                {
                    "case_id": "TC-test-product-001",
                    "path_id": "PATH-001",
                    "status": "failed",
                    "execution_status": "failed",
                    "final_url": "https://example.test/product/insure",
                    "page_keys": [
                        {
                            "node_id": "NODE-insure-form",
                            "page_key": "PK-product-insure",
                            "url_pattern": "/product/insure",
                        }
                    ],
                }
            ],
        }
    ]

    report_path = generate_agent4_html_report(
        run_dir=run_dir,
        product_id="test-product",
        run_id="agent3-agent4-20260519-180000",
        reports=reports,
    )

    html = report_path.read_text(encoding="utf-8").replace("\\", "/")
    assert "agent4/tc-exec/test-results/01-path-001-SCN-001-PATH-001-chromium/test-failed-1.png" in html

    data = json.loads((run_dir / "report-data.json").read_text(encoding="utf-8"))
    assert data["formal_screenshot_count"] == 1
    assert data["formal_screenshots"][0]["path_id"] == "PATH-001"


def test_agent4_html_report_collects_isolated_playwright_result_screenshots(tmp_path: Path):
    from e2e_agent.legacy.agents.agent4_exec.report import generate_agent4_html_report

    run_dir = tmp_path / "runs" / "agent3-agent4-20260519-180500"
    first_shot_dir = run_dir / "agent4" / "tc-exec" / "01-path-001" / "test-results" / "01-path-001-SCN-001-PATH-001-chromium"
    second_shot_dir = run_dir / "agent4" / "tc-exec" / "02-path-002" / "test-results" / "02-path-002-SCN-002-PATH-002-chromium"
    first_shot_dir.mkdir(parents=True)
    second_shot_dir.mkdir(parents=True)
    (first_shot_dir / "test-finished-1.png").write_bytes(b"fake-png-1")
    (second_shot_dir / "test-finished-1.png").write_bytes(b"fake-png-2")

    reports = [
        {
            "run_id": "agent3-agent4-20260519-180500",
            "summary": {"total": 2, "passed": 2, "failed": 0, "skipped": 0, "error": 0},
            "results": [
                {"case_id": "TC-test-product-001", "path_id": "PATH-001", "status": "passed"},
                {"case_id": "TC-test-product-002", "path_id": "PATH-002", "status": "passed"},
            ],
        }
    ]

    report_path = generate_agent4_html_report(
        run_dir=run_dir,
        product_id="test-product",
        run_id="agent3-agent4-20260519-180500",
        reports=reports,
    )

    html = report_path.read_text(encoding="utf-8").replace("\\", "/")
    assert "agent4/tc-exec/01-path-001/test-results/01-path-001-SCN-001-PATH-001-chromium/test-finished-1.png" in html
    assert "agent4/tc-exec/02-path-002/test-results/02-path-002-SCN-002-PATH-002-chromium/test-finished-1.png" in html

    data = json.loads((run_dir / "report-data.json").read_text(encoding="utf-8"))
    assert data["formal_screenshot_count"] == 2
    assert [item["path_id"] for item in data["formal_screenshots"]] == ["PATH-001", "PATH-002"]


def test_agent4_html_report_keeps_artifact_links_relative_when_run_dir_is_relative(tmp_path: Path, monkeypatch):
    from e2e_agent.legacy.agents.agent4_exec.report import generate_agent4_html_report

    monkeypatch.chdir(tmp_path)
    run_dir = Path("runs/agent4-relative-run")
    shot_dir = run_dir / "agent4" / "tc-exec" / "01-path-001" / "test-results" / "01-path-001-SCN-001-PATH-001-chromium"
    shot_dir.mkdir(parents=True)
    shot_path = shot_dir / "agent4-business-step-1.png"
    shot_path.write_bytes(b"fake-png")
    shot_path.with_suffix(".json").write_text(
        json.dumps(
            {
                "path_id": "PATH-001",
                "step": 1,
                "label": "step-1",
                "url": "https://example.test/product/insure",
                "planned_to_node_id": "NODE-insure-form",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report_path = generate_agent4_html_report(
        run_dir=run_dir,
        product_id="demo-product",
        run_id="agent4-relative-run",
        reports=[
            {
                "summary": {"total": 1, "passed": 1, "failed": 0, "skipped": 0, "error": 0},
                "results": [
                    {
                        "case_id": "TC-demo-001",
                        "path_id": "PATH-001",
                        "status": "passed",
                        "page_keys": [
                            {
                                "node_id": "NODE-insure-form",
                                "page_key": "PK-product-insure",
                                "url_pattern": "/product/insure",
                            }
                        ],
                    }
                ],
            }
        ],
    )

    html = report_path.read_text(encoding="utf-8").replace("\\", "/")
    data = json.loads((run_dir / "report-data.json").read_text(encoding="utf-8"))

    assert "agent4/tc-exec/01-path-001/test-results/01-path-001-SCN-001-PATH-001-chromium/agent4-business-step-1.png" in html
    assert "runs/agent4-relative-run/runs/agent4-relative-run" not in html
    assert data["formal_screenshots"][0]["relative_path"].replace("\\", "/").startswith("agent4/tc-exec/")


def test_agent4_html_report_maps_business_screenshot_sidecar_to_target_node(tmp_path: Path):
    from e2e_agent.legacy.agents.agent4_exec.report import generate_agent4_html_report

    run_dir = tmp_path / "runs" / "agent3-agent4-20260519-181000"
    shot_dir = run_dir / "agent4" / "tc-exec" / "01-path-001" / "test-results" / "01-path-001-SCN-001-PATH-001-chromium"
    shot_dir.mkdir(parents=True)
    shot_path = shot_dir / "agent4-business-step-1.png"
    shot_path.write_bytes(b"fake-png")
    shot_path.with_suffix(".json").write_text(
        json.dumps(
            {
                "path_id": "PATH-001",
                "step": 1,
                "label": "step-1",
                "url": "https://example.test/product/detail",
                "planned_from_node_id": "NODE-product-detail",
                "planned_to_node_id": "NODE-premium-calculation",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    reports = [
        {
            "run_id": "agent3-agent4-20260519-181000",
            "summary": {"total": 1, "passed": 1, "failed": 0, "skipped": 0, "error": 0},
            "results": [
                {
                    "case_id": "TC-test-product-001",
                    "path_id": "PATH-001",
                    "status": "passed",
                    "execution_status": "passed",
                    "page_keys": [
                        {
                            "node_id": "NODE-product-detail",
                            "page_key": "PK-product-detail",
                            "url_pattern": "/product/detail",
                        },
                        {
                            "node_id": "NODE-premium-calculation",
                            "page_key": "PK-product-to-insure",
                            "url_pattern": "/product/detail",
                        },
                        {
                            "node_id": "NODE-policy-result",
                            "page_key": "PK-result",
                            "url_pattern": "/pay",
                        },
                    ],
                    "node_progress": [
                        {"node_id": "NODE-product-detail", "status": "matched"},
                        {"node_id": "NODE-premium-calculation", "status": "matched"},
                        {"node_id": "NODE-policy-result", "status": "matched"},
                    ],
                }
            ],
        }
    ]

    generate_agent4_html_report(
        run_dir=run_dir,
        product_id="test-product",
        run_id="agent3-agent4-20260519-181000",
        reports=reports,
    )

    data = json.loads((run_dir / "report-data.json").read_text(encoding="utf-8"))
    chain = data["path_chains"][0]
    shots_by_node = {node["node_id"]: node["screenshots"] for node in chain["nodes"]}

    assert len(shots_by_node["NODE-premium-calculation"]) == 1
    assert shots_by_node["NODE-premium-calculation"][0]["label"] == "step-1"
    assert shots_by_node["NODE-policy-result"] == []


def test_agent4_html_report_ignores_sidecar_target_outside_path_nodes(tmp_path: Path):
    from e2e_agent.legacy.agents.agent4_exec.report import generate_agent4_html_report

    run_dir = tmp_path / "runs" / "agent3-agent4-20260519-181050"
    shot_dir = run_dir / "agent4" / "tc-exec" / "01-path-001" / "test-results" / "01-path-001-SCN-001-PATH-001-chromium"
    shot_dir.mkdir(parents=True)
    shot_path = shot_dir / "agent4-business-step-1.png"
    shot_path.write_bytes(b"fake-png")
    shot_path.with_suffix(".json").write_text(
        json.dumps(
            {
                "path_id": "PATH-001",
                "step": 1,
                "label": "step-1",
                "url": "https://example.test/product/detail",
                "planned_to_node_id": "NODE-not-in-this-path",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    reports = [
        {
            "run_id": "agent3-agent4-20260519-181050",
            "summary": {"total": 1, "passed": 1, "failed": 0, "skipped": 0, "error": 0},
            "results": [
                {
                    "case_id": "TC-test-product-001",
                    "path_id": "PATH-001",
                    "status": "passed",
                    "execution_status": "passed",
                    "page_keys": [
                        {
                            "node_id": "NODE-product-detail",
                            "page_key": "PK-product-detail",
                            "url_pattern": "/product/detail",
                        },
                        {
                            "node_id": "NODE-policy-result",
                            "page_key": "PK-result",
                            "url_pattern": "/pay",
                        },
                    ],
                    "node_progress": [
                        {"node_id": "NODE-product-detail", "status": "matched"},
                        {"node_id": "NODE-policy-result", "status": "matched"},
                    ],
                }
            ],
        }
    ]

    generate_agent4_html_report(
        run_dir=run_dir,
        product_id="test-product",
        run_id="agent3-agent4-20260519-181050",
        reports=reports,
    )

    data = json.loads((run_dir / "report-data.json").read_text(encoding="utf-8"))
    shots_by_node = {
        node["node_id"]: node["screenshots"]
        for node in data["path_chains"][0]["nodes"]
    }

    assert len(shots_by_node["NODE-product-detail"]) == 1
    assert shots_by_node["NODE-policy-result"] == []


def test_agent4_html_report_backfills_matched_nodes_from_same_url_screenshot(tmp_path: Path):
    from e2e_agent.legacy.agents.agent4_exec.report import generate_agent4_html_report

    run_dir = tmp_path / "runs" / "agent3-agent4-20260519-181100"
    shot_dir = run_dir / "agent4" / "tc-exec" / "01-path-001" / "test-results" / "01-path-001-SCN-001-PATH-001-chromium"
    shot_dir.mkdir(parents=True)
    shot_path = shot_dir / "agent4-business-final.png"
    shot_path.write_bytes(b"fake-png")
    shot_path.with_suffix(".json").write_text(
        json.dumps(
            {
                "path_id": "PATH-001",
                "label": "final",
                "url": "https://example.test/pay/success/?id=abc",
                "planned_to_node_id": "NODE-policy-service",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    reports = [
        {
            "run_id": "agent3-agent4-20260519-181100",
            "summary": {"total": 1, "passed": 1, "failed": 0, "skipped": 0, "error": 0},
            "results": [
                {
                    "case_id": "TC-test-product-001",
                    "path_id": "PATH-001",
                    "status": "passed",
                    "execution_status": "passed",
                    "page_keys": [
                        {
                            "node_id": "NODE-policy-result",
                            "page_key": "PK-result",
                            "url_pattern": "/pay/success",
                        },
                        {
                            "node_id": "NODE-policy-service",
                            "page_key": "PK-policy-service",
                            "url_pattern": "/pay/success",
                        },
                    ],
                    "node_progress": [
                        {
                            "node_id": "NODE-policy-result",
                            "status": "matched",
                            "actual_url": "https://example.test/pay/success/?id=abc",
                        },
                        {
                            "node_id": "NODE-policy-service",
                            "status": "matched",
                            "actual_url": "https://example.test/pay/success/?id=abc",
                        },
                    ],
                }
            ],
        }
    ]

    generate_agent4_html_report(
        run_dir=run_dir,
        product_id="test-product",
        run_id="agent3-agent4-20260519-181100",
        reports=reports,
    )

    data = json.loads((run_dir / "report-data.json").read_text(encoding="utf-8"))
    shots_by_node = {
        node["node_id"]: node["screenshots"]
        for node in data["path_chains"][0]["nodes"]
    }

    assert shots_by_node["NODE-policy-service"][0]["label"] == "final"
    assert shots_by_node["NODE-policy-result"][0]["label"] == "final"
    assert shots_by_node["NODE-policy-service"][0]["source"] == "Agent4-backfill"
    assert shots_by_node["NODE-policy-result"][0]["source"] == "Agent4"


def test_agent4_html_report_reuses_actual_page_screenshot_for_same_page_nodes(tmp_path: Path):
    from e2e_agent.legacy.agents.agent4_exec.report import generate_agent4_html_report

    run_dir = tmp_path / "runs" / "agent4-same-page-nodes"
    shot_dir = run_dir / "agent4" / "tc-exec" / "03-path-003" / "test-results" / "03-path-003-SCN-003-PATH-003-chromium"
    shot_dir.mkdir(parents=True)
    shot_path = shot_dir / "agent4-business-step-1.png"
    shot_path.write_bytes(b"fake-png")
    actual_url = "https://example.test/m/apps/cps/demo-channel/product/insure?encryptInsureNum=abc"
    shot_path.with_suffix(".json").write_text(
        json.dumps(
            {
                "path_id": "PATH-003",
                "step": 1,
                "label": "step-1",
                "url": actual_url,
                "planned_to_node_id": "NODE-premium-calculation",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    reports = [
        {
            "run_id": "agent4-same-page-nodes",
            "summary": {"total": 1, "passed": 1, "failed": 0, "skipped": 0, "error": 0},
            "results": [
                {
                    "case_id": "TC-test-product-001",
                    "path_id": "PATH-003",
                    "status": "passed",
                    "execution_status": "passed",
                    "page_keys": [
                        {
                            "node_id": "NODE-premium-calculation",
                            "page_key": "PK-product-to-insure",
                            "url_pattern": "/product/to-insure",
                        },
                        {
                            "node_id": "NODE-suitability",
                            "page_key": "PK-product-to-insure",
                            "url_pattern": "/product/to-insure",
                        },
                    ],
                    "node_progress": [
                        {
                            "node_id": "NODE-premium-calculation",
                            "status": "matched",
                            "actual_url": actual_url,
                        },
                        {
                            "node_id": "NODE-suitability",
                            "status": "pending",
                        },
                    ],
                }
            ],
        }
    ]

    generate_agent4_html_report(
        run_dir=run_dir,
        product_id="test-product",
        run_id="agent4-same-page-nodes",
        reports=reports,
    )

    data = json.loads((run_dir / "report-data.json").read_text(encoding="utf-8"))
    nodes = {node["node_id"]: node for node in data["path_chains"][0]["nodes"]}

    assert nodes["NODE-suitability"]["actual_url"] == actual_url
    assert nodes["NODE-suitability"]["screenshots"][0]["label"] == "step-1"
    assert nodes["NODE-suitability"]["screenshots"][0]["source"] == "Agent4-backfill"


def test_agent4_html_report_hides_unobserved_static_health_notice_page(tmp_path: Path):
    from e2e_agent.legacy.agents.agent4_exec.report import generate_agent4_html_report

    run_dir = tmp_path / "runs" / "agent4-no-health-notice"
    run_dir.mkdir(parents=True)

    reports = [
        {
            "run_id": "agent4-no-health-notice",
            "summary": {"total": 1, "passed": 1, "failed": 0, "skipped": 0, "error": 0},
            "results": [
                {
                    "case_id": "TC-test-product-001",
                    "path_id": "PATH-001",
                    "status": "passed",
                    "execution_status": "passed",
                    "page_keys": [
                        {
                            "node_id": "NODE-product-detail",
                            "page_key": "PK-product-detail",
                            "url_pattern": "/product/detail",
                        },
                        {
                            "node_id": "NODE-health-notice",
                            "page_key": "PK-insure-health-notice",
                            "url_pattern": "/insure/health-notice",
                        },
                        {
                            "node_id": "NODE-insure-form",
                            "page_key": "PK-product-insure",
                            "url_pattern": "/product/insure",
                        },
                    ],
                    "node_progress": [
                        {
                            "node_id": "NODE-product-detail",
                            "status": "matched",
                            "actual_url": "https://example.test/product/detail",
                            "action_used": {
                                "planned_route_nodes": [
                                    "NODE-product-detail",
                                    "NODE-health-notice",
                                    "NODE-insure-form",
                                ]
                            },
                        },
                        {
                            "node_id": "NODE-health-notice",
                            "status": "pending",
                        },
                        {
                            "node_id": "NODE-insure-form",
                            "status": "matched",
                            "actual_url": "https://example.test/product/insure",
                        },
                    ],
                }
            ],
        }
    ]

    report_path = generate_agent4_html_report(
        run_dir=run_dir,
        product_id="test-product",
        run_id="agent4-no-health-notice",
        reports=reports,
    )

    data = json.loads((run_dir / "report-data.json").read_text(encoding="utf-8"))
    html = report_path.read_text(encoding="utf-8")

    node_ids = [node["node_id"] for node in data["path_chains"][0]["nodes"]]
    assert "NODE-health-notice" not in node_ids
    result_page_node_ids = [page["node_id"] for page in data["results"][0]["page_keys"]]
    result_progress_node_ids = [node["node_id"] for node in data["results"][0]["node_progress"]]
    assert "NODE-health-notice" not in result_page_node_ids
    assert "NODE-health-notice" not in result_progress_node_ids
    assert "PK-insure-health-notice" not in html
    assert "/insure/health-notice" not in html
    report_data_text = (run_dir / "report-data.json").read_text(encoding="utf-8")
    assert "NODE-health-notice" not in report_data_text
    assert "PK-insure-health-notice" not in report_data_text
    assert "/insure/health-notice" not in report_data_text


def test_agent4_html_report_maps_policy_service_final_screenshot_to_result_page(tmp_path: Path):
    from e2e_agent.legacy.agents.agent4_exec.report import generate_agent4_html_report

    run_dir = tmp_path / "runs" / "agent4-service-final-result"
    shot_dir = run_dir / "agent4" / "tc-exec" / "03-path-003" / "test-results" / "03-path-003-SCN-003-PATH-003-chromium"
    shot_dir.mkdir(parents=True)
    shot_path = shot_dir / "agent4-business-final.png"
    shot_path.write_bytes(b"fake-png")
    issued_url = "https://commerce.example.test/m/demo-channel/pay/success/?id=issued&aid="
    shot_path.with_suffix(".json").write_text(
        json.dumps(
            {
                "path_id": "PATH-003",
                "phase": "final",
                "label": "final",
                "url": issued_url,
                "planned_to_node_id": "NODE-policy-service",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    reports = [
        {
            "run_id": "agent4-service-final-result",
            "summary": {"total": 1, "passed": 1, "failed": 0, "skipped": 0, "error": 0},
            "results": [
                {
                    "case_id": "TC-test-product-006",
                    "path_id": "PATH-003",
                    "status": "passed",
                    "execution_status": "passed",
                    "target_node": "NODE-policy-service",
                    "payment_closed_loop": {"required": True, "status": "passed-after-resume", "issue_status": 1},
                    "page_keys": [
                        {
                            "node_id": "NODE-payment",
                            "page_key": "PK-payment",
                            "url_pattern": "/payment",
                        },
                        {
                            "node_id": "NODE-policy-result",
                            "page_key": "PK-result",
                            "url_pattern": "/result",
                        },
                        {
                            "node_id": "NODE-policy-service",
                            "page_key": "PK-policy-service",
                            "url_pattern": "/policy/service",
                        },
                    ],
                    "node_progress": [
                        {"node_id": "NODE-payment", "status": "matched"},
                        {
                            "node_id": "NODE-policy-result",
                            "status": "matched",
                            "actual_url": issued_url,
                        },
                        {
                            "node_id": "NODE-policy-service",
                            "status": "pending",
                            "actual_url": issued_url,
                        },
                    ],
                }
            ],
        }
    ]

    generate_agent4_html_report(
        run_dir=run_dir,
        product_id="test-product",
        run_id="agent4-service-final-result",
        reports=reports,
    )

    data = json.loads((run_dir / "report-data.json").read_text(encoding="utf-8"))
    chain = data["path_chains"][0]
    nodes = {node["node_id"]: node for node in chain["nodes"]}

    assert chain["target_node"] == "NODE-policy-result"
    assert "NODE-policy-service" not in nodes
    assert nodes["NODE-policy-result"]["page_key"] == "PK-result"
    assert nodes["NODE-policy-result"]["url_pattern"] == "/result"
    assert nodes["NODE-policy-result"]["screenshots"][0]["label"] == "final"


def test_agent4_html_report_does_not_use_payment_gateway_handoff_as_result_screenshot(tmp_path: Path):
    from e2e_agent.legacy.agents.agent4_exec.report import generate_agent4_html_report

    run_dir = tmp_path / "runs" / "agent4-gateway-handoff"
    shot_dir = run_dir / "agent4" / "tc-exec" / "01-path-001" / "test-results" / "01-path-001-SCN-001-PATH-001-chromium"
    shot_dir.mkdir(parents=True)
    shot_path = shot_dir / "agent4-business-final.png"
    shot_path.write_bytes(b"fake-png")
    gateway_url = "https://wx.tenpay.com/cgi-bin/mmpayweb-bin/checkmweb?redirect_url=https%3A%2F%2Fpayments.example.test"
    shot_path.with_suffix(".json").write_text(
        json.dumps(
            {
                "path_id": "PATH-001",
                "phase": "final",
                "label": "final",
                "url": gateway_url,
                "planned_to_node_id": "NODE-policy-result",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    generate_agent4_html_report(
        run_dir=run_dir,
        product_id="test-product",
        run_id="agent4-gateway-handoff",
        reports=[
            {
                "summary": {"total": 1, "passed": 1, "failed": 0, "skipped": 0, "error": 0},
                "results": [
                    {
                        "case_id": "TC-test-product-001",
                        "path_id": "PATH-001",
                        "status": "passed",
                        "execution_status": "passed",
                        "page_keys": [
                            {
                                "node_id": "NODE-payment",
                                "page_key": "PK-payment",
                                "url_pattern": "/pay",
                            },
                            {
                                "node_id": "NODE-policy-result",
                                "page_key": "PK-result",
                                "url_pattern": "/result",
                            },
                        ],
                        "node_progress": [
                            {"node_id": "NODE-payment", "status": "matched"},
                            {"node_id": "NODE-policy-result", "status": "matched"},
                        ],
                    }
                ],
            }
        ],
    )

    data = json.loads((run_dir / "report-data.json").read_text(encoding="utf-8"))
    nodes = {node["node_id"]: node for node in data["path_chains"][0]["nodes"]}

    assert nodes["NODE-policy-result"]["screenshots"] == []


def test_agent4_html_report_does_not_backfill_result_from_payment_page(tmp_path: Path):
    from e2e_agent.legacy.agents.agent4_exec.report import generate_agent4_html_report

    run_dir = tmp_path / "runs" / "agent4-payment-page-result"
    shot_dir = run_dir / "agent4" / "tc-exec" / "01-path-001" / "test-results" / "01-path-001-SCN-001-PATH-001-chromium"
    shot_dir.mkdir(parents=True)
    shot_path = shot_dir / "agent4-business-step-3-before-payment.png"
    shot_path.write_bytes(b"fake-png")
    pay_url = "https://commerce.example.test/m/demo-channel/pay/?id=abc"
    shot_path.with_suffix(".json").write_text(
        json.dumps(
            {
                "path_id": "PATH-001",
                "step": 3,
                "label": "step-3-before-payment",
                "url": pay_url,
                "planned_to_node_id": "NODE-payment",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    generate_agent4_html_report(
        run_dir=run_dir,
        product_id="test-product",
        run_id="agent4-payment-page-result",
        reports=[
            {
                "summary": {"total": 1, "passed": 1, "failed": 0, "skipped": 0, "error": 0},
                "results": [
                    {
                        "case_id": "TC-test-product-001",
                        "path_id": "PATH-001",
                        "status": "passed",
                        "execution_status": "passed",
                        "page_keys": [
                            {
                                "node_id": "NODE-payment",
                                "page_key": "PK-payment",
                                "url_pattern": "/pay",
                            },
                            {
                                "node_id": "NODE-policy-result",
                                "page_key": "PK-result",
                                "url_pattern": "/result",
                            },
                        ],
                        "node_progress": [
                            {"node_id": "NODE-payment", "status": "matched", "actual_url": pay_url},
                            {"node_id": "NODE-policy-result", "status": "matched", "actual_url": pay_url},
                        ],
                    }
                ],
            }
        ],
    )

    data = json.loads((run_dir / "report-data.json").read_text(encoding="utf-8"))
    nodes = {node["node_id"]: node for node in data["path_chains"][0]["nodes"]}

    assert nodes["NODE-payment"]["screenshots"][0]["label"] == "step-3-before-payment"
    assert nodes["NODE-policy-result"]["screenshots"] == []


def test_agent4_html_report_infers_health_notice_from_insure_form_snapshot(tmp_path: Path):
    from e2e_agent.legacy.agents.agent4_exec.report import generate_agent4_html_report

    run_dir = tmp_path / "runs" / "agent3-agent4-20260519-170000"
    merged_cases_path = tmp_path / "agent1" / "merged-cases.json"
    merged_cases_path.parent.mkdir(parents=True)
    merged_cases_path.write_text(
        json.dumps(
            [
                {
                    "case_id": "TC-test-product-002",
                    "title": "health notice",
                    "business_intent": "health_notice",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    reports = [
        {
            "run_id": "agent3-agent4-20260519-170000",
            "summary": {"total": 1, "passed": 0, "failed": 1, "skipped": 0, "error": 0},
            "results": [
                {
                    "case_id": "TC-test-product-002",
                    "path_id": "PATH-001",
                    "status": "failed",
                    "execution_status": "failed",
                    "target_node": "NODE-policy-result",
                    "body_excerpt": "Page snapshot: 起保日期 投保人信息 税收居民身份 证件号码",
                    "executed_actions": [],
                    "node_matches": [],
                }
            ],
        }
    ]

    generate_agent4_html_report(
        run_dir=run_dir,
        product_id="test-product",
        run_id="agent3-agent4-20260519-170000",
        reports=reports,
    )

    data = json.loads((run_dir / "report-data.json").read_text(encoding="utf-8"))
    assert data["merged_case_summary"]["passed"] == 1
    assert data["merged_case_statuses"][0]["case_id"] == "TC-test-product-002"
    assert data["merged_case_statuses"][0]["status"] == "passed"
    assert data["merged_case_statuses"][0]["target_node"] == "NODE-insure-form"


def test_agent4_html_report_passes_health_notice_when_node_progress_matched(tmp_path: Path):
    from e2e_agent.legacy.agents.agent4_exec.report import generate_agent4_html_report

    run_dir = tmp_path / "runs" / "agent3-agent4-20260520-161115"
    merged_cases_path = tmp_path / "agent1" / "merged-cases.json"
    merged_cases_path.parent.mkdir(parents=True)
    merged_cases_path.write_text(
        json.dumps(
            [
                {
                    "case_id": "TC-test-product-002",
                    "title": "health notice",
                    "business_intent": "health_notice",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    reports = [
        {
            "run_id": "agent3-agent4-20260520-161115",
            "summary": {"total": 1, "passed": 0, "failed": 0, "skipped": 0, "error": 1},
            "results": [
                {
                    "case_id": "TC-test-product-002",
                    "path_id": "PATH-001",
                    "status": "error",
                    "execution_status": "error",
                    "target_node": "NODE-policy-result",
                    "node_progress": [
                        {
                            "node_id": "NODE-health-notice",
                            "status": "matched",
                            "actual_url": "https://example.test/product/healthInform",
                        }
                    ],
                }
            ],
        }
    ]

    generate_agent4_html_report(
        run_dir=run_dir,
        product_id="test-product",
        run_id="agent3-agent4-20260520-161115",
        reports=reports,
    )

    data = json.loads((run_dir / "report-data.json").read_text(encoding="utf-8"))
    assert data["merged_case_summary"] == {"total": 1, "passed": 1, "failed": 0, "skipped": 0, "error": 0}
    assert data["merged_case_statuses"][0]["case_id"] == "TC-test-product-002"
    assert data["merged_case_statuses"][0]["status"] == "passed"
    assert data["merged_case_statuses"][0]["target_node"] == "NODE-health-notice"
    assert data["merged_case_statuses"][0]["evidence"] == "target node reached"


def test_agent4_html_report_counts_order_generation_boundary_cases_as_passed(tmp_path: Path):
    from e2e_agent.legacy.agents.agent4_exec.report import generate_agent4_html_report

    run_dir = tmp_path / "runs" / "agent1-agent4-20260524-024249"
    merged_cases_path = tmp_path / "agent1" / "merged-cases.json"
    merged_cases_path.parent.mkdir(parents=True)
    merged_cases_path.write_text(
        json.dumps(
            [
                {
                    "case_id": "TC-test-product-003",
                    "title": "tax identity",
                    "business_intent": "tax_identity",
                },
                {
                    "case_id": "TC-test-product-004",
                    "title": "underwriting",
                    "business_intent": "underwriting",
                },
                {
                    "case_id": "TC-test-product-005",
                    "title": "payment",
                    "business_intent": "payment",
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    reports = [
        {
            "run_id": "agent1-agent4-20260524-024249",
            "summary": {"total": 3, "passed": 3, "failed": 0, "skipped": 0, "error": 0},
            "results": [
                {
                    "case_id": "TC-test-product-003",
                    "path_id": "PATH-002",
                    "status": "passed",
                    "execution_status": "passed",
                    "target_node": "NODE-policy-result",
                    "target_node_status": "reached",
                    "reached_target_node": "NODE-policy-result",
                    "target_node_inference": "agent3.order_generation_boundary",
                    "node_progress": [
                        {"node_id": "NODE-health-notice", "status": "matched"},
                        {"node_id": "NODE-insure-form", "status": "matched"},
                    ],
                },
                {
                    "case_id": "TC-test-product-004",
                    "path_id": "PATH-001",
                    "status": "passed",
                    "execution_status": "passed",
                    "target_node": "NODE-policy-result",
                    "target_node_status": "reached",
                    "reached_target_node": "NODE-policy-result",
                    "target_node_inference": "agent3.order_generation_boundary",
                    "node_progress": [
                        {"node_id": "NODE-insure-form", "status": "matched"},
                        {"node_id": "NODE-risk-control", "status": "matched"},
                    ],
                },
                {
                    "case_id": "TC-test-product-005",
                    "path_id": "PATH-001",
                    "status": "passed",
                    "execution_status": "passed",
                    "target_node": "NODE-policy-result",
                    "target_node_status": "reached",
                    "reached_target_node": "NODE-policy-result",
                    "target_node_inference": "agent3.order_generation_boundary",
                    "node_progress": [
                        {"node_id": "NODE-risk-control", "status": "matched"},
                        {"node_id": "NODE-policy-result", "status": "passed"},
                    ],
                },
            ],
        }
    ]

    generate_agent4_html_report(
        run_dir=run_dir,
        product_id="test-product",
        run_id="agent1-agent4-20260524-024249",
        reports=reports,
    )

    data = json.loads((run_dir / "report-data.json").read_text(encoding="utf-8"))
    case_status = {item["case_id"]: item["status"] for item in data["merged_case_statuses"]}

    assert data["merged_case_summary"] == {"total": 3, "passed": 3, "failed": 0, "skipped": 0, "error": 0}
    assert case_status == {
        "TC-test-product-003": "passed",
        "TC-test-product-004": "passed",
        "TC-test-product-005": "passed",
    }


def test_agent4_html_report_counts_policy_service_order_boundary_as_policy_passed(tmp_path: Path):
    from e2e_agent.legacy.agents.agent4_exec.report import generate_agent4_html_report

    run_dir = tmp_path / "runs" / "agent1-agent4-20260609-021338"
    merged_cases_path = tmp_path / "agent1" / "merged-cases.json"
    merged_cases_path.parent.mkdir(parents=True)
    merged_cases_path.write_text(
        json.dumps(
            [
                {
                    "case_id": "TC-test-product-006",
                    "title": "policy service",
                    "business_intent": "policy",
                },
                {
                    "case_id": "TC-test-product-007",
                    "title": "identity handoff",
                    "business_intent": "payment",
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    reports = [
        {
            "run_id": "agent1-agent4-20260609-021338",
            "summary": {"total": 2, "passed": 2, "failed": 0, "skipped": 0, "error": 0},
            "results": [
                {
                    "case_id": "TC-test-product-006",
                    "path_id": "PATH-004",
                    "status": "passed",
                    "execution_status": "passed",
                    "target_node": "NODE-policy-service",
                    "target_node_status": "reached",
                    "reached_target_node": "NODE-policy-service",
                    "target_node_inference": "agent3.order_generation_boundary",
                    "executed_actions": [
                        {
                            "action_type": "submit_api",
                            "click_strategy": "agent3-submit-api-replay",
                            "matched_nodes": ["NODE-suitability"],
                            "submit_suitability_recovery": {"recovered": True},
                            "submit_api_result": {
                                "success": False,
                                "suitability_task": True,
                                "task_handoff": True,
                            },
                        }
                    ],
                },
                {
                    "case_id": "TC-test-product-007",
                    "path_id": "PATH-004",
                    "status": "passed",
                    "execution_status": "passed",
                    "target_node": "NODE-policy-result",
                    "target_node_status": "reached",
                    "reached_target_node": "NODE-policy-result",
                    "target_node_inference": "agent3.order_generation_boundary",
                    "executed_actions": [
                        {
                            "action_type": "submit_api",
                            "click_strategy": "agent3-submit-api-replay",
                            "submit_api_result": {
                                "order_generated": True,
                                "task_handoff": True,
                                "direct_order": False,
                                "code": "37009",
                                "msg": "identity verification required",
                            },
                        }
                    ],
                },
            ],
        }
    ]

    generate_agent4_html_report(
        run_dir=run_dir,
        product_id="test-product",
        run_id="agent1-agent4-20260609-021338",
        reports=reports,
    )

    data = json.loads((run_dir / "report-data.json").read_text(encoding="utf-8"))

    case_status = {item["case_id"]: item["status"] for item in data["merged_case_statuses"]}

    assert data["merged_case_summary"] == {"total": 2, "passed": 1, "failed": 1, "skipped": 0, "error": 0}
    assert case_status == {
        "TC-test-product-006": "passed",
        "TC-test-product-007": "failed",
    }

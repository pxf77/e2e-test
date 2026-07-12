from __future__ import annotations

from tools.validate_repository import find_violations


def test_find_violations_rejects_generated_outputs() -> None:
    assert find_violations(
        [
            "apps/demo/.assets/runs/run-1/report.json",
            "playwright-report/index.html",
            "test-results/result.json",
            ".local/e2e-agent/gate.json",
            "pytest.log",
            "reports/trace.zip",
            "video/run.webm",
            "src/e2e_agent/__pycache__/module.cpython-312.pyc",
        ]
    ) == [
        ".local/e2e-agent/gate.json",
        "apps/demo/.assets/runs/run-1/report.json",
        "playwright-report/index.html",
        "pytest.log",
        "reports/trace.zip",
        "src/e2e_agent/__pycache__/module.cpython-312.pyc",
        "test-results/result.json",
        "video/run.webm",
    ]


def test_find_violations_allows_source_and_fixtures() -> None:
    assert find_violations(
        [
            "src/e2e_agent/workflow/runtime.py",
            "tests/fixtures/trace-metadata.json",
            "docs/reference/reporting.md",
            "runners/playwright.yaml",
        ]
    ) == []

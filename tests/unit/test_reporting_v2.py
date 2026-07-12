from __future__ import annotations

import json
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from e2e_agent.reporting import normalize_failure, write_report_bundle
from e2e_agent.workflow import WorkflowRuntime

ROOT = Path(__file__).resolve().parents[2]


def test_report_bundle_writes_json_html_and_junit(tmp_path: Path) -> None:
    report = {
        "run_id": "report-test",
        "workflow_id": "workflow",
        "status": "failed",
        "summary": {"passed": 1, "failed": 1, "skipped": 1},
        "failures": [{"id": "case-1", "message": "expected 200 got 500"}],
    }

    artifacts = write_report_bundle(report, tmp_path)

    paths = {Path(item["path"]).name: Path(item["path"]) for item in artifacts}
    assert set(paths) == {"report.json", "report.html", "junit.xml"}
    assert "E2E Regression Report" in paths["report.html"].read_text(encoding="utf-8")
    suite = ET.parse(paths["junit.xml"]).getroot()
    assert suite.attrib["failures"] == "1"
    payload = json.loads(paths["report.json"].read_text(encoding="utf-8"))
    assert payload["failures"][0]["category"] == "assertion_failed"


def test_failure_taxonomy_classifies_network_errors() -> None:
    failure = normalize_failure({"message": "connection timeout"})

    assert failure["category"] == "network_error"
    assert failure["retryable"] is True


@pytest.mark.asyncio
async def test_workflow_manifest_indexes_report_files(tmp_path: Path) -> None:
    runtime = WorkflowRuntime(repo_root=ROOT)
    result = await runtime.run(
        app_path=Path("apps/demo-generic-form/app.yaml"),
        workflow="smoke-static-web",
        run_id="report-manifest-test",
        metadata={"artifacts_dir": str(tmp_path / "run"), "gate_checkpoint_dir": ""},
    )

    manifest_items = result["artifact_manifest"]["artifacts"]
    kinds = {str((item.get("metadata") or {}).get("kind")) for item in manifest_items}
    assert {"json-report", "html-report", "junit-report"} <= kinds

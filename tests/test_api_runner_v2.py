from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from e2e_agent.runners.api import ApiRunner
from e2e_agent.runners.base import ExecutionPlan
from e2e_agent.workflow import WorkflowRuntime

ROOT = Path(__file__).resolve().parents[1]


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            body = json.dumps({"status": "ok", "service": {"name": "demo"}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture
def api_server() -> str:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.mark.asyncio
async def test_api_runner_checks_status_json_and_writes_evidence(api_server: str, tmp_path: Path) -> None:
    result = await ApiRunner().execute(
        ExecutionPlan(
            id="api-runner-test",
            runner="api",
            scenarios=[
                {
                    "id": "health",
                    "request": {"method": "GET", "path": "/health"},
                    "expected": {"status": 200, "json": {"status": "ok", "service.name": "demo"}},
                }
            ],
            fixtures={"base_url": api_server},
            artifacts_dir=str(tmp_path),
        )
    )

    assert result.status == "passed"
    assert result.summary["passed"] == 1
    evidence = Path(result.artifacts[0]["path"])
    assert evidence.exists()
    assert json.loads(evidence.read_text(encoding="utf-8"))["passed"] is True


@pytest.mark.asyncio
async def test_api_workflow_generates_unified_reports(api_server: str, tmp_path: Path) -> None:
    runtime = WorkflowRuntime(repo_root=ROOT)
    result = await runtime.run(
        app_path=Path("apps/demo-api/app.yaml"),
        workflow="api-contract-regression",
        run_id="api-workflow-test",
        inputs={
            "api_fixtures": {"base_url": api_server},
            "api_scenarios": [
                {
                    "id": "health",
                    "request": {"path": "/health"},
                    "expected": {"status": 200, "json": {"status": "ok"}},
                }
            ],
        },
        metadata={"artifacts_dir": str(tmp_path / "run"), "gate_checkpoint_dir": ""},
    )

    assert result["artifacts"]["execution_result"]["status"] == "passed"
    assert result["artifacts"]["test_report"]["status"] == "passed"
    names = {Path(item["path"]).name for item in result["artifacts"]["report_artifacts"]}
    assert names == {"report.json", "report.html", "junit.xml"}

from __future__ import annotations

import json
from pathlib import Path

import pytest

from e2e_agent.workflow import WorkflowRuntime

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
async def test_smoke_static_web_matches_golden_contract(tmp_path: Path) -> None:
    expected = json.loads((ROOT / "tests/golden/v2/smoke-static-web.json").read_text(encoding="utf-8"))
    result = await WorkflowRuntime(repo_root=ROOT).run(
        app_path=Path("apps/demo-generic-form/app.yaml"),
        workflow="smoke-static-web",
        run_id="golden-smoke-v2",
        metadata={"artifacts_dir": str(tmp_path / "run"), "gate_checkpoint_dir": ""},
    )

    actual = {
        "node_ids": [item["node_id"] for item in result["node_trace"]],
        "artifact_names": sorted(result["artifacts"]),
        "report_files": sorted(Path(item["path"]).name for item in result["artifacts"]["report_artifacts"]),
    }
    assert actual == expected

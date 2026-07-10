from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from e2e_agent.runners.base import ExecutionPlan
from e2e_agent.runners.mobile import AppiumRunner


@pytest.mark.asyncio
async def test_appium_runner_returns_blocked_without_command() -> None:
    result = await AppiumRunner().execute(
        ExecutionPlan(id="mobile-blocked", runner="appium", scenarios=[{"id": "one"}])
    )

    assert result.status == "blocked"
    assert result.failures[0]["category"] == "runner_error"


@pytest.mark.asyncio
async def test_appium_runner_executes_external_command(tmp_path: Path) -> None:
    command = [
        sys.executable,
        "-c",
        "import json; print(json.dumps({'passed': 2, 'failed': 0, 'skipped': 1}))",
    ]
    artifact = tmp_path / "mobile.log"
    artifact.write_text("mobile evidence", encoding="utf-8")
    result = await AppiumRunner().execute(
        ExecutionPlan(
            id="mobile-command",
            runner="appium",
            fixtures={"command": command, "artifact_paths": [str(artifact)]},
        )
    )

    assert result.status == "passed"
    assert result.summary == {"passed": 2, "failed": 0, "skipped": 1, "duration_ms": result.summary["duration_ms"]}
    assert result.artifacts[0]["path"] == str(artifact)
    assert json.loads(result.metrics["stdout"])["passed"] == 2

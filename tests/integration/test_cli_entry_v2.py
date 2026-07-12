from __future__ import annotations

import json
import shutil
from pathlib import Path

from e2e_agent.cli_entry import main

ROOT = Path(__file__).resolve().parents[2]


def test_cli_entry_runs_v2_app_and_writes_manifest(capsys) -> None:  # type: ignore[no-untyped-def]
    run_id = "cli-v2-runtime-test"
    run_dir = ROOT / "apps" / "demo-generic-form" / ".assets" / "runs" / run_id
    shutil.rmtree(run_dir, ignore_errors=True)

    try:
        exit_code = main(
            [
                "run",
                "--app",
                "apps/demo-generic-form/app.yaml",
                "--workflow",
                "smoke-static-web",
                "--run-id",
                run_id,
            ]
        )
        payload = json.loads(capsys.readouterr().out)

        assert exit_code == 0
        assert payload["run_id"] == run_id
        assert payload["status"] == "skipped"
        assert payload["pending_gates"] == []
        assert payload["artifact_count"] >= 4
        assert Path(payload["artifact_manifest"]).exists()
        assert (run_dir / "run-context.json").exists()
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)

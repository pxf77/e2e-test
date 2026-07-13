from __future__ import annotations

import json
from pathlib import Path

from e2e_agent.commands.main import main


def _write_checkpoint(root: Path) -> None:
    state = {
        "run_id": "cli-gate-run",
        "workflow_id": "p0-web-regression",
        "gates": {"review": {"status": "pending", "policy": "human_required"}},
    }
    (root / "cli-gate-run.v2.json").write_text(
        json.dumps(
            {
                "run_id": "cli-gate-run",
                "workflow_id": "p0-web-regression",
                "pending_gate": "review",
                "status": "pending",
                "decision": None,
                "state": state,
            }
        ),
        encoding="utf-8",
    )


def test_gate_v2_status_and_approve(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    _write_checkpoint(tmp_path)

    assert main(["gate", "status", "cli-gate-run", "--checkpoint-dir", str(tmp_path)]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["pending_gate"] == "review"
    assert status["gate"]["status"] == "pending"

    assert (
        main(
            [
                "gate",
                "approve",
                "cli-gate-run",
                "--checkpoint-dir",
                str(tmp_path),
                "--operator",
                "qa",
                "--note",
                "approved",
            ]
        )
        == 0
    )
    decision = json.loads(capsys.readouterr().out)
    assert decision["decision"]["status"] == "approved"

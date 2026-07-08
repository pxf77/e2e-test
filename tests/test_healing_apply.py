from __future__ import annotations

import json
from pathlib import Path

import e2e_agent.cli as cli_module


def test_healing_apply_requires_evidence_file(tmp_path: Path) -> None:
    from e2e_agent.core.healing_apply import apply_healing_event

    run_dir = tmp_path / "runs" / "run-001"
    run_dir.mkdir(parents=True)
    (run_dir / "agent4-result.json").write_text(
        json.dumps(
            {
                "healing_events": [
                    {
                        "event_id": "HEAL-001",
                        "case_id": "TC-001",
                        "failure_category": "script_bug",
                        "suggestion": {"action": "add_wait", "description": "wait for submit"},
                        "applied": False,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    try:
        apply_healing_event(run_dir, "HEAL-001", evidence_file=tmp_path / "missing.md", operator="qa")
    except FileNotFoundError as exc:
        assert "Evidence file not found" in str(exc)
    else:
        raise AssertionError("apply_healing_event should require evidence")


def test_healing_apply_marks_event_and_writes_audit(tmp_path: Path) -> None:
    from e2e_agent.core.healing_apply import apply_healing_event

    run_dir = tmp_path / "runs" / "run-001"
    run_dir.mkdir(parents=True)
    evidence = tmp_path / "review.md"
    evidence.write_text("reviewed by qa", encoding="utf-8")
    (run_dir / "agent4-result.json").write_text(
        json.dumps(
            {
                "healing_events": [
                    {
                        "event_id": "HEAL-001",
                        "case_id": "TC-001",
                        "failure_category": "script_bug",
                        "suggestion": {
                            "action": "add_wait",
                            "description": "wait for submit",
                            "code_diff": "--- a/spec.ts\n+++ b/spec.ts\n@@\n+await page.waitForLoadState();\n",
                        },
                        "applied": False,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    result = apply_healing_event(run_dir, "HEAL-001", evidence_file=evidence, operator="qa")

    assert result["event_id"] == "HEAL-001"
    assert Path(result["patch_path"]).exists()
    assert Path(result["audit_path"]).exists()
    updated = json.loads((run_dir / "agent4-result.json").read_text(encoding="utf-8"))
    event = updated["healing_events"][0]
    assert event["applied"] is True
    assert event["applied_by"] == "qa"
    assert event["evidence_file"] == str(evidence)


def test_healing_apply_rejects_unsafe_event_id(tmp_path: Path) -> None:
    from e2e_agent.core.healing_apply import apply_healing_event

    run_dir = tmp_path / "runs" / "run-001"
    run_dir.mkdir(parents=True)
    evidence = tmp_path / "review.md"
    evidence.write_text("reviewed by qa", encoding="utf-8")
    (run_dir / "agent4-result.json").write_text(
        json.dumps(
            {
                "healing_events": [
                    {
                        "event_id": "..\\HEAL-001",
                        "case_id": "TC-001",
                        "failure_category": "script_bug",
                        "suggestion": {"action": "add_wait", "description": "wait"},
                        "applied": False,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    try:
        apply_healing_event(run_dir, "..\\HEAL-001", evidence_file=evidence, operator="qa")
    except ValueError as exc:
        assert "Unsafe event id" in str(exc)
    else:
        raise AssertionError("apply_healing_event should reject unsafe event ids")

    assert not (run_dir / "agent4" / "HEAL-001.patch").exists()


def test_healing_events_schema_accepts_applied_audit_fields(tmp_path: Path) -> None:
    from jsonschema import Draft7Validator

    schema = json.loads(
        (Path(__file__).resolve().parents[1] / "schemas" / "healing-events.schema.json").read_text(
            encoding="utf-8"
        )
    )
    events = [
        {
            "event_id": "HEAL-001",
            "case_id": "TC-001",
            "run_id": "run-001",
            "failure_category": "script_bug",
            "suggestion": {"action": "add_wait", "description": "wait", "code_diff": None},
            "applied": True,
            "applied_by": "qa",
            "applied_at": "2026-05-29T08:00:00+00:00",
            "evidence_file": "review.md",
            "patch_path": "agent4/healing-applied/HEAL-001.patch",
            "audit_path": "agent4/healing-applied/HEAL-001.audit.json",
            "verified_effective": True,
            "timestamp": "2026-05-29T08:00:00+00:00",
        }
    ]

    errors = sorted(Draft7Validator(schema).iter_errors(events), key=lambda err: err.path)

    assert errors == []


def test_healing_apply_cli_locates_run_dir_and_prints_result(tmp_path: Path, monkeypatch, capsys) -> None:
    root = tmp_path
    run_dir = root / "products" / "demo" / "demo.assets" / "runs" / "run-001"
    run_dir.mkdir(parents=True)
    evidence = tmp_path / "evidence.md"
    evidence.write_text("evidence", encoding="utf-8")
    (run_dir / "agent4-result.json").write_text(
        json.dumps(
            {
                "healing_events": [
                    {
                        "event_id": "HEAL-001",
                        "case_id": "TC-001",
                        "failure_category": "script_bug",
                        "suggestion": {"action": "add_wait", "description": "wait"},
                        "applied": False,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(cli_module, "_REPO_ROOT", root)

    exit_code = cli_module.main(
        ["healing", "apply", "--run-id", "run-001", "--event-id", "HEAL-001", "--evidence-file", str(evidence)]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["run_id"] == "run-001"
    assert payload["event_id"] == "HEAL-001"


def test_healing_apply_cli_rejects_unsafe_run_id(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli_module, "_REPO_ROOT", tmp_path)

    exit_code = cli_module.main(
        ["healing", "apply", "--run-id", "..\\run-001", "--event-id", "HEAL-001", "--evidence-file", "x.md"]
    )

    assert exit_code == 1
    assert "Unsafe run id" in capsys.readouterr().err


def test_healing_apply_cli_rejects_unsafe_event_id(tmp_path: Path, monkeypatch, capsys) -> None:
    root = tmp_path
    run_dir = root / "products" / "demo" / "demo.assets" / "runs" / "run-001"
    run_dir.mkdir(parents=True)
    evidence = tmp_path / "evidence.md"
    evidence.write_text("evidence", encoding="utf-8")
    (run_dir / "agent4-result.json").write_text(
        json.dumps(
            {
                "healing_events": [
                    {
                        "event_id": "..\\HEAL-001",
                        "case_id": "TC-001",
                        "failure_category": "script_bug",
                        "suggestion": {"action": "add_wait", "description": "wait"},
                        "applied": False,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(cli_module, "_REPO_ROOT", root)

    exit_code = cli_module.main(
        [
            "healing",
            "apply",
            "--run-id",
            "run-001",
            "--event-id",
            "..\\HEAL-001",
            "--evidence-file",
            str(evidence),
        ]
    )

    assert exit_code == 1
    assert "Unsafe event id" in capsys.readouterr().err

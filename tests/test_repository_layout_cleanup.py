from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_obsolete_repository_files_are_absent() -> None:
    obsolete_paths = [
        ROOT / ".dockerignore",
        ROOT / "patches",
        ROOT / "scripts" / "e2e-agent-run.ps1",
        ROOT / "tools" / "run_v2_workflow.py",
        ROOT / "docs" / "implementation-status.md",
    ]

    assert [str(path.relative_to(ROOT)) for path in obsolete_paths if path.exists()] == []

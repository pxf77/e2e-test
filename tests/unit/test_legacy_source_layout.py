from __future__ import annotations

from pathlib import Path

from tools.validate.legacy import import_smoke, validate

ROOT = Path(__file__).resolve().parents[2]


def test_repository_legacy_layout_is_valid() -> None:
    assert validate() == []
    assert import_smoke() == []


def test_old_top_level_legacy_packages_are_absent() -> None:
    package = ROOT / "src" / "e2e_agent"

    assert [name for name in ("agents", "browser", "graph", "skills") if (package / name).exists()] == []
    assert all((package / "legacy" / name).exists() for name in ("agents", "browser", "graph", "skills"))


def test_validator_detects_old_package_and_reference(tmp_path: Path) -> None:
    package = tmp_path / "src" / "e2e_agent"
    for name in ("agents", "browser", "graph", "skills"):
        (package / "legacy" / name).mkdir(parents=True, exist_ok=True)
    (package / "agents").mkdir()
    stale = tmp_path / "tests" / "unit" / "test_stale.py"
    stale.parent.mkdir(parents=True)
    legacy_import = "e2e_agent" + ".agents"
    stale.write_text(f"import {legacy_import}\n", encoding="utf-8")

    errors = validate(tmp_path)

    assert any("old path" in error for error in errors)
    assert any("stale legacy reference" in error for error in errors)


def test_1x_cli_and_tool_wrappers_are_absent() -> None:
    package = ROOT / "src" / "e2e_agent"
    assert not (package / "cli.py").exists()
    assert not (package / "cli_entry.py").exists()
    assert (package / "legacy" / "cli.py").exists()
    assert not (ROOT / "tools" / "acceptance_matrix.py").exists()
    assert not (ROOT / "tools" / "run_full_workflow.py").exists()
    assert (ROOT / "tools" / "legacy" / "run_full_workflow.py").exists()

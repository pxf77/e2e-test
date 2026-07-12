from __future__ import annotations

from pathlib import Path

from tools.validate.tests import CATEGORIES, validate


def _build_categories(root: Path) -> None:
    for category in CATEGORIES:
        directory = root / "tests" / category
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"test_{category}.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")


def test_repository_test_layout_is_valid() -> None:
    assert validate() == []


def test_validator_rejects_root_test(tmp_path: Path) -> None:
    _build_categories(tmp_path)
    (tmp_path / "tests" / "test_uncategorized.py").write_text("def test_x(): pass\n", encoding="utf-8")

    errors = validate(tmp_path)

    assert any("uncategorized root tests" in error for error in errors)


def test_validator_rejects_stale_relative_path(tmp_path: Path) -> None:
    _build_categories(tmp_path)
    stale = tmp_path / "tests" / "unit" / "test_unit.py"
    expression = "Path(__file__).resolve().parents" + "[1]"
    stale.write_text(
        f"from pathlib import Path\nROOT = {expression}\n",
        encoding="utf-8",
    )

    errors = validate(tmp_path)

    assert any("stale moved-file path expression" in error for error in errors)

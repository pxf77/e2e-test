from __future__ import annotations

from pathlib import Path

from tools.validate_docs import local_target, validate


def test_repository_documentation_links_are_valid() -> None:
    assert validate() == []


def test_local_target_ignores_external_and_anchor_links(tmp_path: Path) -> None:
    source = tmp_path / "doc.md"

    assert local_target(source, "https://example.com") is None
    assert local_target(source, "#section") is None
    assert local_target(source, "child.md#section") == (tmp_path / "child.md").resolve()


def test_validate_reports_missing_local_link(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "README.md").write_text("[missing](docs/missing.md)", encoding="utf-8")

    assert validate(tmp_path) == ["README.md: missing link target: docs/missing.md"]

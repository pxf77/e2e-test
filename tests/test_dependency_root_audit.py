from __future__ import annotations

import json
from pathlib import Path

from tools.validate.dependencies import validate

ROOT = Path(__file__).resolve().parents[1]


def test_repository_dependency_ownership_is_valid() -> None:
    assert validate() == []


def test_dependency_validator_detects_reintroduced_packages(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '''[project]
name = "demo"
version = "0.1.0"
dependencies = ["click>=8"]
[project.scripts]
e2e-agent = "old.entry:main"
''',
        encoding="utf-8",
    )
    (tmp_path / "package.json").write_text(
        json.dumps({"devDependencies": {"playwright": "1.0.0"}}),
        encoding="utf-8",
    )
    (tmp_path / "CLAUDE.md").write_text("D:\\huizecode\\e2e-test", encoding="utf-8")

    errors = validate(tmp_path)

    assert any("click" in error for error in errors)
    assert any("entrypoint" in error for error in errors)
    assert any("duplicates" in error for error in errors)
    assert any("@playwright/test" in error for error in errors)
    assert any("AGENTS.md" in error for error in errors)
    assert any("machine-specific" in error for error in errors)


def test_npm_lock_root_matches_package_json() -> None:
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))

    assert lock["packages"][""]["devDependencies"] == package["devDependencies"]
    assert lock["packages"]["node_modules/@playwright/test"]["dependencies"]["playwright"] == "1.60.0"

from __future__ import annotations

import importlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

CANONICAL_MODULES = (
    "tools.validate.schemas",
    "tools.validate.repository",
    "tools.validate.docs",
    "tools.validate.dependencies",
    "tools.validate.tests",
    "tools.validate.legacy",
    "tools.validate.domains",
    "tools.validate.workflows",
    "tools.validate.runners",
    "tools.validate.plugins",
    "tools.validate.boundaries",
    "tools.validate.rules",
    "tools.acceptance",
    "tools.diagnostics.model_acceptance",
    "tools.diagnostics.playwright_compat",
    "tools.legacy.run_full_workflow",
)

REMOVED_WRAPPERS = (
    "acceptance_matrix.py",
    "check_domain_boundaries.py",
    "ci_rule_check.py",
    "model_acceptance_harness.py",
    "playwright_compat_check.py",
    "run_full_workflow.py",
    "validate_dependencies.py",
    "validate_docs.py",
    "validate_domains.py",
    "validate_legacy.py",
    "validate_plugins.py",
    "validate_repository.py",
    "validate_runners.py",
    "validate_schemas.py",
    "validate_tests.py",
    "validate_workflows.py",
)


def test_canonical_tool_modules_are_importable() -> None:
    for module_name in CANONICAL_MODULES:
        module = importlib.import_module(module_name)
        assert callable(module.main), module_name


def test_root_tool_compatibility_wrappers_are_absent() -> None:
    assert [name for name in REMOVED_WRAPPERS if (ROOT / "tools" / name).exists()] == []


def test_playwright_diagnostic_has_no_machine_specific_repository_path() -> None:
    text = (ROOT / "tools" / "diagnostics" / "playwright_compat.py").read_text(encoding="utf-8")

    assert "D:/huizecode" not in text
    assert "D:\\huizecode" not in text
    assert "--repo-root" in text
    assert ".local" in text


def test_legacy_full_workflow_is_physically_categorized() -> None:
    implementation = ROOT / "tools" / "legacy" / "run_full_workflow.py"

    assert implementation.exists()
    assert not (ROOT / "tools" / "run_full_workflow.py").exists()
    assert callable(importlib.import_module("tools.legacy.run_full_workflow").main)

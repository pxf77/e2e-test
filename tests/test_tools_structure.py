from __future__ import annotations

import importlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_tool_packages_are_present() -> None:
    expected = [
        ROOT / "tools" / "validate" / "schemas.py",
        ROOT / "tools" / "validate" / "repository.py",
        ROOT / "tools" / "validate" / "docs.py",
        ROOT / "tools" / "validate" / "domains.py",
        ROOT / "tools" / "validate" / "workflows.py",
        ROOT / "tools" / "validate" / "runners.py",
        ROOT / "tools" / "validate" / "plugins.py",
        ROOT / "tools" / "validate" / "boundaries.py",
        ROOT / "tools" / "validate" / "rules.py",
        ROOT / "tools" / "diagnostics" / "model_acceptance.py",
        ROOT / "tools" / "diagnostics" / "playwright_compat.py",
        ROOT / "tools" / "legacy" / "run_full_workflow.py",
        ROOT / "tools" / "acceptance.py",
    ]

    assert [str(path.relative_to(ROOT)) for path in expected if not path.exists()] == []


def test_compatibility_scripts_delegate_to_categorized_modules() -> None:
    wrappers = {
        "tools.validate_schemas": "tools.validate.schemas",
        "tools.validate_repository": "tools.validate.repository",
        "tools.validate_docs": "tools.validate.docs",
        "tools.validate_domains": "tools.validate.domains",
        "tools.validate_workflows": "tools.validate.workflows",
        "tools.validate_runners": "tools.validate.runners",
        "tools.validate_plugins": "tools.validate.plugins",
        "tools.check_domain_boundaries": "tools.validate.boundaries",
        "tools.ci_rule_check": "tools.validate.rules",
        "tools.acceptance_matrix": "tools.acceptance",
        "tools.model_acceptance_harness": "tools.diagnostics.model_acceptance",
    }
    for wrapper_name, implementation_name in wrappers.items():
        wrapper = importlib.import_module(wrapper_name)
        implementation = importlib.import_module(implementation_name)
        assert wrapper.main is implementation.main, wrapper_name


def test_playwright_diagnostic_has_no_machine_specific_repository_path() -> None:
    text = (ROOT / "tools" / "diagnostics" / "playwright_compat.py").read_text(encoding="utf-8")

    assert "D:/huizecode" not in text
    assert "D:\\huizecode" not in text
    assert "--repo-root" in text
    assert ".local" in text


def test_legacy_categorized_alias_delegates_to_existing_compatibility_command() -> None:
    categorized = importlib.import_module("tools.legacy.run_full_workflow")
    compatibility = importlib.import_module("tools.run_full_workflow")

    assert categorized.main is compatibility.main

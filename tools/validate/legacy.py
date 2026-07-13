"""Validate physical isolation of the legacy four-Agent runtime."""
from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "src" / "e2e_agent"
LEGACY_PACKAGES = ("agents", "browser", "graph", "skills")
LEGACY_CLI = PACKAGE / "legacy" / "cli.py"
REMOVED_CLI_PATHS = (PACKAGE / "cli.py", PACKAGE / "cli_entry.py")
REMOVED_TOOL_WRAPPERS = (
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
STALE_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"\be2e_agent\.agents\b",
        r"\be2e_agent\.browser\b",
        r"\be2e_agent\.graph\b",
        r"\be2e_agent\.skills\b",
        r"src/e2e_agent/agents(?:/|\b)",
        r"src/e2e_agent/browser(?:/|\b)",
        r"src/e2e_agent/graph(?:/|\b)",
        r"src/e2e_agent/skills(?:/|\b)",
    )
)
TEXT_SUFFIXES = {".py", ".md", ".yaml", ".yml", ".json", ".toml", ".ts", ".js", ".txt", ".ps1"}


def validate(root: Path = ROOT) -> list[str]:
    package = root / "src" / "e2e_agent"
    errors: list[str] = []
    for path in REMOVED_CLI_PATHS:
        candidate = root / path.relative_to(ROOT)
        if candidate.exists():
            errors.append(f"removed CLI wrapper still exists: {candidate.relative_to(root)}")
    legacy_cli = root / LEGACY_CLI.relative_to(ROOT)
    if not legacy_cli.exists():
        errors.append(f"missing legacy CLI implementation: {legacy_cli.relative_to(root)}")
    for name in REMOVED_TOOL_WRAPPERS:
        wrapper = root / "tools" / name
        if wrapper.exists():
            errors.append(f"removed root tool wrapper still exists: {wrapper.relative_to(root)}")
    for name in LEGACY_PACKAGES:
        old = package / name
        current = package / "legacy" / name
        if old.exists():
            errors.append(f"legacy package remains at old path: {old.relative_to(root)}")
        if not current.exists():
            errors.append(f"missing isolated legacy package: {current.relative_to(root)}")

    scan_roots = [root / "src", root / "tests", root / "tools", root / "docs", root / "workflows", root / "README.md", root / "AGENTS.md"]
    this_file = Path(__file__).resolve()
    for scan_root in scan_roots:
        paths = [scan_root] if scan_root.is_file() else list(scan_root.rglob("*")) if scan_root.exists() else []
        for path in paths:
            if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            if path.resolve() == this_file:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for pattern in STALE_PATTERNS:
                if pattern.search(text):
                    errors.append(f"stale legacy reference in {path.relative_to(root)}: {pattern.pattern}")
                    break
    return errors


def import_smoke() -> list[str]:
    modules = [
        "e2e_agent.legacy.agents.agent1_tc_merge.node",
        "e2e_agent.legacy.agents.agent2_path_extract.node",
        "e2e_agent.legacy.agents.agent3_explore.node",
        "e2e_agent.legacy.agents.agent4_exec.node",
        "e2e_agent.legacy.browser.runner",
        "e2e_agent.legacy.graph.graph",
        "e2e_agent.legacy.skills.loader",
        "e2e_agent.legacy.cli",
    ]
    failures: list[str] = []
    for name in modules:
        try:
            importlib.import_module(name)
        except Exception as exc:  # pragma: no cover - CLI reporting
            failures.append(f"cannot import {name}: {exc}")
    return failures


def main() -> int:
    errors = [*validate(), *import_smoke()]
    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: legacy runtime and CLI are isolated; 1.x root wrappers are absent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

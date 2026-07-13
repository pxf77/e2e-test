"""One-time 2.0 migration removing 1.x CLI and root tool wrappers.

Deleted after the migration commit is created and verified.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TOOL_MODULE_REPLACEMENTS = {
    "python -m tools.validate.repository": "python -m tools.validate.repository",
    "python -m tools.validate.docs": "python -m tools.validate.docs",
    "python -m tools.validate.dependencies": "python -m tools.validate.dependencies",
    "python -m tools.validate.tests": "python -m tools.validate.tests",
    "python -m tools.validate.schemas": "python -m tools.validate.schemas",
    "python -m tools.validate.legacy": "python -m tools.validate.legacy",
    "python -m tools.validate.domains": "python -m tools.validate.domains",
    "python -m tools.validate.workflows": "python -m tools.validate.workflows",
    "python -m tools.validate.runners": "python -m tools.validate.runners",
    "python -m tools.validate.plugins": "python -m tools.validate.plugins",
    "python -m tools.validate.rules": "python -m tools.validate.rules",
    "python -m tools.validate.boundaries": "python -m tools.validate.boundaries",
    "python -m tools.acceptance": "python -m tools.acceptance",
}

ROOT_TOOL_WRAPPERS = (
    "acceptance_matrix.py",
    "check_domain_boundaries.py",
    "ci_rule_check.py",
    "model_acceptance_harness.py",
    "playwright_compat_check.py",
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

CLI_ENTRY_TESTS = (
    "tests/compatibility/test_cli_release_v1.py",
    "tests/integration/test_cli_entry_v2.py",
    "tests/integration/test_cli_extensions_v2.py",
    "tests/integration/test_cli_gate_v2.py",
)


def replace(path: Path, old: str, new: str, *, count: int = -1) -> None:
    text = path.read_text(encoding="utf-8")
    updated = text.replace(old, new, count)
    if updated != text:
        path.write_text(updated, encoding="utf-8")


def move_legacy_cli() -> None:
    source = ROOT / "src" / "e2e_agent" / "cli.py"
    target = ROOT / "src" / "e2e_agent" / "legacy" / "cli.py"
    text = source.read_text(encoding="utf-8")
    text = text.replace(
        "Path(__file__).resolve().parents[2]",
        "Path(__file__).resolve().parents[3]",
        1,
    )
    text = text.replace(
        '''    command = [
        sys.executable,
        str(_REPO_ROOT / "tools" / "run_full_workflow.py"),
        "--product-input",
        str(resolved_input),
    ]''',
        '''    command = [
        sys.executable,
        "-m",
        "tools.legacy.run_full_workflow",
        "--product-input",
        str(resolved_input),
    ]''',
    )
    target.write_text(text, encoding="utf-8")
    source.unlink()
    (ROOT / "src" / "e2e_agent" / "cli_entry.py").unlink()


def update_canonical_cli() -> None:
    path = ROOT / "src" / "e2e_agent" / "commands" / "main.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        "from e2e_agent import cli as legacy_cli",
        "from e2e_agent.legacy import cli as legacy_cli",
    )
    text = text.replace("  gate-v2           Deprecated alias for `gate`\n", "")
    text = text.replace(
        "def gate(argv: list[str], *, force_v2: bool = False) -> int:\n"
        "    args = _build_gate_parser().parse_args(argv)\n"
        "    use_v2 = force_v2 or _is_v2_checkpoint(args.run_id, args.checkpoint_dir)\n",
        "def gate(argv: list[str]) -> int:\n"
        "    args = _build_gate_parser().parse_args(argv)\n"
        "    use_v2 = _is_v2_checkpoint(args.run_id, args.checkpoint_dir)\n",
    )
    text = text.replace(
        '''    completed = subprocess.run(
        [sys.executable, str(_REPO_ROOT / "tools" / "acceptance_matrix.py")],
        cwd=_REPO_ROOT,
        check=False,
    )''',
        '''    completed = subprocess.run(
        [sys.executable, "-m", "tools.acceptance"],
        cwd=_REPO_ROOT,
        check=False,
    )''',
    )
    text = text.replace(
        '''        if actual[0] == "gate-v2":
            print("Deprecated: use `e2e-agent gate ...`", file=sys.stderr)
            return gate(actual[1:], force_v2=True)
''',
        "",
    )
    path.write_text(text, encoding="utf-8")


def move_legacy_workflow_tool() -> None:
    source = ROOT / "tools" / "run_full_workflow.py"
    target = ROOT / "tools" / "legacy" / "run_full_workflow.py"
    target.unlink()
    shutil.move(str(source), str(target))
    for name in ROOT_TOOL_WRAPPERS:
        path = ROOT / "tools" / name
        if path.exists():
            path.unlink()


def update_acceptance() -> None:
    path = ROOT / "tools" / "acceptance.py"
    text = path.read_text(encoding="utf-8")
    start = text.index("STATIC_COMMANDS = [")
    end = text.index("]\nAPP_MATRIX", start) + 2
    modules = '''STATIC_MODULES = [
    "tools.validate.repository",
    "tools.validate.docs",
    "tools.validate.dependencies",
    "tools.validate.tests",
    "tools.validate.schemas",
    "tools.validate.legacy",
    "tools.validate.domains",
    "tools.validate.workflows",
    "tools.validate.runners",
    "tools.validate.plugins",
    "tools.validate.rules",
    "tools.validate.boundaries",
]
'''
    text = text[:start] + modules + text[end:]
    text = text.replace(
        "    for command in STATIC_COMMANDS:\n"
        "        completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)\n",
        "    for module in STATIC_MODULES:\n"
        "        command = [sys.executable, \"-m\", module]\n"
        "        completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)\n",
    )
    text = text.replace(
        '"name": Path(command[1]).stem,',
        '"name": module.rsplit(".", 1)[-1],',
    )
    path.write_text(text, encoding="utf-8")


def update_tests() -> None:
    for relative in CLI_ENTRY_TESTS:
        replace(
            ROOT / relative,
            "from e2e_agent.cli_entry import main",
            "from e2e_agent.commands.main import main",
        )

    gate_test = ROOT / "tests" / "integration" / "test_cli_gate_v2.py"
    replace(gate_test, '["gate-v2",', '["gate",')
    replace(gate_test, '"gate-v2",', '"gate",')

    replace(
        ROOT / "tests" / "integration" / "test_cli_v2.py",
        "from e2e_agent.cli import main",
        "from e2e_agent.commands.main import main",
    )
    for relative in (
        "tests/integration/test_gate_cli.py",
        "tests/unit/test_healing_apply.py",
    ):
        replace(
            ROOT / relative,
            "import e2e_agent.cli as cli_module",
            "import e2e_agent.legacy.cli as cli_module",
        )

    unified = ROOT / "tests" / "integration" / "test_cli_unified_entry.py"
    text = unified.read_text(encoding="utf-8")
    text = text.replace("from e2e_agent import cli_entry\n", "")
    text = text.replace("from e2e_agent.commands import main as exported_main\n", "")
    text = re.sub(
        r"\n\ndef test_cli_entry_is_compatibility_wrapper\(\) -> None:\n"
        r"    assert cli_entry\.main is exported_main\n",
        "",
        text,
    )
    text = re.sub(
        r"\n\ndef test_gate_v2_alias_warns_and_uses_v2_dispatch\(.*?"
        r"(?=\ndef test_unified_gate_dispatches_v1_status)",
        '''\n\ndef test_gate_v2_alias_is_removed(capsys: Any) -> None:
    assert command_main(["gate-v2", "status", "run-1"]) == 2
    assert "gate-v2" in capsys.readouterr().err
\n''',
        text,
        flags=re.S,
    )
    unified.write_text(text, encoding="utf-8")

    gate_cli = ROOT / "tests" / "integration" / "test_gate_cli.py"
    text = gate_cli.read_text(encoding="utf-8")
    text = text.replace(
        '''    assert calls[0]["command"] == [
        os.sys.executable,
        str(tmp_path / "tools" / "run_full_workflow.py"),
        "--product-input",
        str(product_input),
    ]''',
        '''    assert calls[0]["command"] == [
        os.sys.executable,
        "-m",
        "tools.legacy.run_full_workflow",
        "--product-input",
        str(product_input),
    ]''',
    )
    gate_cli.write_text(text, encoding="utf-8")


def update_validation() -> None:
    path = ROOT / "tools" / "validate" / "legacy.py"
    text = path.read_text(encoding="utf-8")
    constants = '''LEGACY_CLI = PACKAGE / "legacy" / "cli.py"
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
'''
    text = text.replace(
        'LEGACY_PACKAGES = ("agents", "browser", "graph", "skills")\n',
        'LEGACY_PACKAGES = ("agents", "browser", "graph", "skills")\n' + constants,
    )
    text = text.replace(
        '''    errors: list[str] = []
    for name in LEGACY_PACKAGES:
''',
        '''    errors: list[str] = []
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
''',
    )
    text = text.replace(
        '        "e2e_agent.legacy.skills.loader",',
        '        "e2e_agent.legacy.skills.loader",\n        "e2e_agent.legacy.cli",',
    )
    text = text.replace(
        "PASS: legacy agents, browser, graph and skills are isolated under e2e_agent.legacy.",
        "PASS: legacy runtime and CLI are isolated; 1.x root wrappers are absent.",
    )
    path.write_text(text, encoding="utf-8")

    test_path = ROOT / "tests" / "unit" / "test_legacy_source_layout.py"
    test_path.write_text(
        test_path.read_text(encoding="utf-8")
        + '''\n\ndef test_1x_cli_and_tool_wrappers_are_absent() -> None:
    package = ROOT / "src" / "e2e_agent"
    assert not (package / "cli.py").exists()
    assert not (package / "cli_entry.py").exists()
    assert (package / "legacy" / "cli.py").exists()
    assert not (ROOT / "tools" / "acceptance_matrix.py").exists()
    assert not (ROOT / "tools" / "run_full_workflow.py").exists()
    assert (ROOT / "tools" / "legacy" / "run_full_workflow.py").exists()
''',
        encoding="utf-8",
    )


def update_text_references() -> None:
    suffixes = {".md", ".yaml", ".yml", ".toml", ".ps1", ".py"}
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in suffixes:
            continue
        if path == ROOT / ".github" / "workflows" / "compat-inventory.yml":
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        updated = text
        for old, new in TOOL_MODULE_REPLACEMENTS.items():
            updated = updated.replace(old, new)
            updated = updated.replace(
                old.replace("python", "$(PYTHON)", 1),
                new.replace("python", "$(PYTHON)", 1),
            )
        updated = updated.replace(
            "python -m tools.diagnostics.model_acceptance",
            "python -m tools.diagnostics.model_acceptance",
        )
        updated = updated.replace(
            "python -m tools.diagnostics.playwright_compat",
            "python -m tools.diagnostics.playwright_compat",
        )
        updated = updated.replace(
            "python -m tools.legacy.run_full_workflow",
            "python -m tools.legacy.run_full_workflow",
        )
        if updated != text:
            path.write_text(updated, encoding="utf-8")

    bootstrap = ROOT / "scripts" / "bootstrap.ps1"
    text = bootstrap.read_text(encoding="utf-8")
    text = text.replace(
        '@("-m", "e2e_agent.cli", "doctor")',
        '@("-m", "e2e_agent.commands.main", "doctor")',
    )
    text = text.replace(
        'Write-Host "  .\\scripts\\e2e-agent-run.ps1 -ProductInput products/travel-product/plan-a/product-input.json"',
        'Write-Host "  .\\.venv\\Scripts\\e2e-agent.exe run --product-input products/travel-product/plan-a/product-input.json"',
    )
    bootstrap.write_text(text, encoding="utf-8")

    for relative in (
        "docs/architecture/workflow-runtime.md",
        "docs/guides/gate-operations.md",
        "docs/reference/cli.md",
    ):
        path = ROOT / relative
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            "`gate-v2` 只作为 1.x 弃用别名保留。",
            "`gate-v2` 已在 2.0 删除。",
        )
        text = text.replace(
            "`gate-v2` 在 1.x 中仍可用，但只作为弃用别名。",
            "`gate-v2` 已在 2.0 删除，统一使用 `e2e-agent gate`。",
        )
        text = text.replace(
            "`gate-v2` 在 1.x 中是弃用别名。",
            "`gate-v2` 已在 2.0 删除。",
        )
        path.write_text(text, encoding="utf-8")


def update_tools_readme() -> None:
    (ROOT / "tools" / "README.md").write_text(
        '''# Repository Tools

## Validation

Validation commands use module entry points:

```bash
python -m tools.validate.repository
python -m tools.validate.docs
python -m tools.validate.dependencies
python -m tools.validate.tests
python -m tools.validate.schemas
python -m tools.validate.legacy
python -m tools.validate.domains
python -m tools.validate.workflows
python -m tools.validate.runners
python -m tools.validate.plugins
python -m tools.validate.rules
python -m tools.validate.boundaries
```

Root-level compatibility wrappers were removed in 2.0.

## Acceptance

```bash
python -m tools.acceptance
```

## Diagnostics

```bash
python -m tools.diagnostics.model_acceptance --help
python -m tools.diagnostics.playwright_compat --help
```

## Legacy

The legacy product-input workflow implementation is isolated under:

```bash
python -m tools.legacy.run_full_workflow --help
```
''',
        encoding="utf-8",
    )


def main() -> None:
    move_legacy_cli()
    update_canonical_cli()
    move_legacy_workflow_tool()
    update_acceptance()
    update_tests()
    update_validation()
    update_text_references()
    update_tools_readme()


if __name__ == "__main__":
    main()

"""One-time test layout migration used by the cleanup PR.

The file is deleted after the bot-created move commit is verified.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"

COMPATIBILITY = {
    "test_agent1_markdown_language.py",
    "test_agent4_html_report.py",
    "test_cli_release_v1.py",
    "test_exec_agent.py",
    "test_explore_agent.py",
    "test_full_workflow_tool.py",
    "test_graph_smoke.py",
    "test_legacy_domain_path_adapter.py",
    "test_path_extract_agent.py",
    "test_skill_loader.py",
    "test_static_first_contract.py",
    "test_tc_merge_agent.py",
    "test_w2_contract_alignment.py",
    "test_w3_contracts.py",
}
INTEGRATION = {
    "test_api_runner_v2.py",
    "test_artifact_manifest_v2.py",
    "test_browser_runner.py",
    "test_cli_entry_v2.py",
    "test_cli_extensions_v2.py",
    "test_cli_gate_v2.py",
    "test_cli_unified_entry.py",
    "test_cli_v2.py",
    "test_gate_cli.py",
    "test_gate_resume_v2.py",
    "test_mobile_runner_v2.py",
    "test_plugin_sdk_v2.py",
    "test_workflow_dsl_v2.py",
    "test_workflow_runtime_v2.py",
}
ACCEPTANCE = {"test_golden_v2.py"}


def category(name: str) -> str:
    if name in COMPATIBILITY:
        return "compatibility"
    if name in INTEGRATION:
        return "integration"
    if name in ACCEPTANCE:
        return "acceptance"
    return "unit"


def rewrite_relative_paths(text: str) -> str:
    text = text.replace("Path(__file__).resolve().parents[1]", "Path(__file__).resolve().parents[2]")
    text = text.replace("Path(__file__).parent.parent /", "Path(__file__).parent.parent.parent /")
    return text


def main() -> int:
    files = sorted(TESTS.glob("test_*.py"))
    for directory in ("unit", "integration", "compatibility", "acceptance"):
        target_dir = TESTS / directory
        target_dir.mkdir(parents=True, exist_ok=True)
        init_file = target_dir / "__init__.py"
        if not init_file.exists():
            init_file.write_text('"""Categorized test package."""\n', encoding="utf-8")

    for source in files:
        target = TESTS / category(source.name) / source.name
        target.write_text(rewrite_relative_paths(source.read_text(encoding="utf-8")), encoding="utf-8")
        source.unlink()

    remaining = sorted(path.name for path in TESTS.glob("test_*.py"))
    if remaining:
        raise RuntimeError(f"uncategorized root tests remain: {remaining}")
    print(f"moved {len(files)} tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

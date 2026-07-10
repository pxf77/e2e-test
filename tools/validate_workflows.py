"""Validate Workflow DSL schema, structure and implementation registration."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from e2e_agent.workflow import WorkflowCompiler, load_workflow  # noqa: E402
from e2e_agent.workflow.defaults import build_default_node_registry  # noqa: E402


def main() -> int:
    workflows_root = ROOT / "workflows"
    workflow_files = sorted(workflows_root.glob("*.yaml"))
    if not workflow_files:
        print("ERROR: no workflow YAML files found under workflows/", file=sys.stderr)
        return 1
    compiler = WorkflowCompiler()
    registry = build_default_node_registry(ROOT)
    failed = 0
    for path in workflow_files:
        try:
            definition = load_workflow(path)
            compiled = compiler.compile(definition)
            compiler.compile_langgraph(definition, registry)
        except Exception as exc:  # pragma: no cover - command-line reporting
            failed += 1
            print(f"  FAIL  {path.relative_to(ROOT)}: {exc}")
            continue
        print(f"  pass  {compiled.id}@{compiled.version} ({len(compiled.nodes)} nodes, {len(compiled.edges)} edges)")
    if failed:
        print(f"\nResults: {len(workflow_files) - failed}/{len(workflow_files)} passed, {failed} FAILED")
        return 1
    print(f"\nResults: {len(workflow_files)}/{len(workflow_files)} passed OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

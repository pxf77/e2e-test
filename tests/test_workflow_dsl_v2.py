from __future__ import annotations

from pathlib import Path

from e2e_agent.workflow import WorkflowCompiler, load_workflow

ROOT = Path(__file__).resolve().parents[1]


def test_all_foundation_workflows_compile() -> None:
    compiler = WorkflowCompiler()
    workflows = sorted((ROOT / "workflows").glob("*.yaml"))

    assert workflows
    for path in workflows:
        definition = load_workflow(path)
        compiled = compiler.compile(definition)
        assert compiled.id
        assert compiled.nodes
        assert compiled.edges


def test_insurance_workflow_preserves_legacy_node_names() -> None:
    definition = load_workflow(ROOT / "workflows" / "p0-insurance-regression.yaml")
    compiled = WorkflowCompiler().compile(definition)

    assert list(compiled.nodes) == [
        "tc_merge",
        "r1_gate",
        "path_extract",
        "r2_gate",
        "explore",
        "r3_gate",
        "exec_healing",
        "r4_gate",
    ]

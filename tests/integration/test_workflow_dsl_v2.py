from __future__ import annotations

from pathlib import Path

from e2e_agent.workflow import WorkflowCompiler, load_workflow

ROOT = Path(__file__).resolve().parents[2]


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


def test_workflow_loader_preserves_on_as_string_key() -> None:
    definition = load_workflow(ROOT / "workflows" / "p0-insurance-regression.yaml")
    conditional_edges = [edge for edge in definition.payload["edges"] if "on" in edge]

    assert conditional_edges
    assert all(True not in edge for edge in conditional_edges)
    assert {edge["on"] for edge in conditional_edges} >= {"approved", "rejected", "pending"}

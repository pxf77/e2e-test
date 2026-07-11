from __future__ import annotations

from pathlib import Path

import pytest

from e2e_agent.plugins import PluginManager, PluginManifestLoader
from e2e_agent.workflow import WorkflowRuntime

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_PLUGINS = ROOT / "examples" / "plugins"


def test_production_plugin_root_does_not_include_examples() -> None:
    assert PluginManifestLoader(ROOT / "plugins").discover() == []


def test_plugin_manifest_loader_discovers_explicit_echo_example() -> None:
    manifests = PluginManifestLoader(EXAMPLE_PLUGINS).discover()

    assert [item.id for item in manifests] == ["echo"]
    assert manifests[0].implementation_id == "plugin.echo"
    assert manifests[0].entry_path.exists()


def test_plugin_manager_registers_explicit_example_node() -> None:
    from e2e_agent.workflow.registry import NodeRegistry

    registry = NodeRegistry()
    PluginManager([ROOT / "plugins", EXAMPLE_PLUGINS]).register_nodes(registry)

    assert registry.get("plugin.echo").kind == "plugin"


@pytest.mark.asyncio
async def test_plugin_smoke_workflow_executes_with_explicit_example_root(tmp_path: Path) -> None:
    runtime = WorkflowRuntime(repo_root=ROOT, plugin_roots=[EXAMPLE_PLUGINS])
    result = await runtime.run(
        app_path=Path("apps/demo-generic-form/app.yaml"),
        workflow=Path("examples/workflows/plugin-smoke.yaml"),
        run_id="plugin-smoke-test",
        inputs={"message": "hello-plugin"},
        metadata={"artifacts_dir": str(tmp_path / "run"), "gate_checkpoint_dir": ""},
    )

    assert result["artifacts"]["plugin_echo"] == {"message": "hello-plugin", "plugin_id": "echo"}
    trace = result["node_trace"][-1]
    assert trace["implementation"] == "plugin.echo"
    assert trace["metrics"]["plugin_id"] == "echo"

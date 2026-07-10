from __future__ import annotations

from pathlib import Path

import pytest

from e2e_agent.plugins import PluginManager, PluginManifestLoader
from e2e_agent.workflow import WorkflowRuntime

ROOT = Path(__file__).resolve().parents[1]


def test_plugin_manifest_loader_discovers_echo() -> None:
    manifests = PluginManifestLoader(ROOT / "plugins").discover()

    assert [item.id for item in manifests] == ["echo"]
    assert manifests[0].implementation_id == "plugin.echo"
    assert manifests[0].entry_path.exists()


def test_plugin_manager_registers_node() -> None:
    from e2e_agent.workflow.registry import NodeRegistry

    registry = NodeRegistry()
    PluginManager(ROOT / "plugins").register_nodes(registry)

    assert registry.get("plugin.echo").kind == "plugin"


@pytest.mark.asyncio
async def test_plugin_smoke_workflow_executes_and_validates_output(tmp_path: Path) -> None:
    runtime = WorkflowRuntime(repo_root=ROOT)
    result = await runtime.run(
        app_path=Path("apps/demo-generic-form/app.yaml"),
        workflow="plugin-smoke",
        run_id="plugin-smoke-test",
        inputs={"message": "hello-plugin"},
        metadata={"artifacts_dir": str(tmp_path / "run"), "gate_checkpoint_dir": ""},
    )

    assert result["artifacts"]["plugin_echo"] == {"message": "hello-plugin", "plugin_id": "echo"}
    trace = result["node_trace"][-1]
    assert trace["implementation"] == "plugin.echo"
    assert trace["metrics"]["plugin_id"] == "echo"

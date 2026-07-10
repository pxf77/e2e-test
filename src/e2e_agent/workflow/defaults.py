from __future__ import annotations

from pathlib import Path

from e2e_agent.adapters.legacy import register_legacy_nodes
from e2e_agent.contracts import ContractRegistry
from e2e_agent.plugins import PluginManager

from .assertion_node import assertion_node
from .builtins import (
    case_merge_node,
    explore_node,
    explore_static_node,
    path_extract_node,
    playwright_runner_node,
    report_node,
)
from .data_node import prepare_data_node
from .registry import NodeRegistry


def build_default_node_registry(repo_root: Path | None = None) -> NodeRegistry:
    root = repo_root or Path(__file__).resolve().parents[3]
    registry = NodeRegistry()
    registry.register("builtin.case_merge", case_merge_node, kind="agent")
    registry.register("builtin.path_extract", path_extract_node, kind="agent")
    registry.register("builtin.explore", explore_node, kind="agent")
    registry.register("builtin.explore_static", explore_static_node, kind="agent")
    registry.register("builtin.prepare_data", prepare_data_node, kind="utility")
    registry.register("builtin.assertions", assertion_node, kind="assertion")
    registry.register("builtin.report", report_node, kind="report")
    registry.register("runner.playwright", playwright_runner_node, kind="runner")
    register_legacy_nodes(registry)

    plugin_manager = PluginManager(
        root / "plugins",
        contract_registry=ContractRegistry(root / "schemas").discover(),
    )
    plugin_manager.register_nodes(registry)
    return registry

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .dsl import WorkflowDefinition, validate_workflow_graph


@dataclass(frozen=True)
class CompiledWorkflow:
    """Static compilation result for the v2 Workflow DSL.

    The first implementation validates structure and preserves the normalized
    node/edge payload. A later migration can map this object to a LangGraph
    StateGraph without changing workflow YAML files.
    """

    id: str
    version: str
    nodes: dict[str, dict[str, Any]]
    edges: list[dict[str, Any]]


class WorkflowCompiler:
    """Compiles WorkflowDefinition into an executable plan skeleton."""

    def compile(self, definition: WorkflowDefinition) -> CompiledWorkflow:
        errors = validate_workflow_graph(definition)
        if errors:
            raise ValueError("\n".join(errors))
        nodes = {str(node["id"]): dict(node) for node in definition.payload.get("nodes", [])}
        return CompiledWorkflow(
            id=definition.id,
            version=definition.version,
            nodes=nodes,
            edges=[dict(edge) for edge in definition.payload.get("edges", [])],
        )

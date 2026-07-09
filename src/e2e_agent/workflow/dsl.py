from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from e2e_agent.contracts import ContractRegistry


@dataclass(frozen=True)
class WorkflowDefinition:
    id: str
    version: str
    path: Path
    payload: dict[str, Any]

    @property
    def node_ids(self) -> list[str]:
        return [str(node["id"]) for node in self.payload.get("nodes", [])]


def load_workflow(path: Path, registry: ContractRegistry | None = None) -> WorkflowDefinition:
    registry = registry or ContractRegistry().discover()
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"Workflow YAML must be an object: {path}")
    registry.validate("workflow", "v2", payload)
    return WorkflowDefinition(
        id=str(payload["id"]),
        version=str(payload["version"]),
        path=path,
        payload=payload,
    )


def validate_workflow_graph(definition: WorkflowDefinition) -> list[str]:
    """Returns structural errors that schema validation cannot express."""
    errors: list[str] = []
    node_ids = set(definition.node_ids)
    if len(node_ids) != len(definition.node_ids):
        errors.append(f"{definition.path}: duplicate node id")
    for edge in definition.payload.get("edges", []):
        source = str(edge.get("from"))
        target = str(edge.get("to"))
        if source != "START" and source not in node_ids:
            errors.append(f"{definition.path}: edge source not found: {source}")
        if target != "END" and target not in node_ids:
            errors.append(f"{definition.path}: edge target not found: {target}")
    return errors

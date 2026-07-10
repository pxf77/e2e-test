from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RunContext:
    """Generalized runtime context.

    v1 ``E2EAgentState`` exposes fixed fields for the insurance pipeline. v2
    keeps the stable identifiers explicit and moves node outputs into the
    artifact map so workflows can vary by domain and runner.
    """

    run_id: str
    app_id: str
    domain_id: str
    workflow_id: str
    env: str = "local"
    inputs: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, Any] = field(default_factory=dict)
    gates: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    errors: list[dict[str, Any]] = field(default_factory=list)

    def put_artifact(self, name: str, payload: Any) -> None:
        self.artifacts[name] = payload

    def get_artifact(self, name: str, default: Any = None) -> Any:
        return self.artifacts.get(name, default)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "app_id": self.app_id,
            "domain_id": self.domain_id,
            "workflow_id": self.workflow_id,
            "env": self.env,
            "inputs": self.inputs,
            "artifacts": self.artifacts,
            "gates": self.gates,
            "metadata": self.metadata,
            "errors": self.errors,
        }

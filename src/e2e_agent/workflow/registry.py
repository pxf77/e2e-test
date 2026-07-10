from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from .state import WorkflowRuntimeState


@dataclass
class NodeResult:
    outputs: dict[str, Any] = field(default_factory=dict)
    state_updates: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)


NodeHandler = Callable[
    [WorkflowRuntimeState, dict[str, Any]],
    NodeResult | dict[str, Any] | Awaitable[NodeResult | dict[str, Any]],
]


@dataclass(frozen=True)
class RegisteredNode:
    id: str
    implementation: NodeHandler
    kind: str = "agent"


class NodeRegistry:
    def __init__(self) -> None:
        self._nodes: dict[str, RegisteredNode] = {}

    def register(self, id: str, implementation: NodeHandler, kind: str = "agent") -> None:
        if id in self._nodes:
            raise ValueError(f"Workflow node implementation already registered: {id}")
        self._nodes[id] = RegisteredNode(id=id, implementation=implementation, kind=kind)

    def get(self, id: str) -> RegisteredNode:
        try:
            return self._nodes[id]
        except KeyError as exc:
            raise KeyError(f"Unknown workflow node implementation: {id}") from exc

    async def invoke(
        self,
        id: str,
        state: WorkflowRuntimeState,
        node_spec: dict[str, Any],
    ) -> NodeResult:
        registered = self.get(id)
        result = registered.implementation(state, node_spec)
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, NodeResult):
            return result
        if isinstance(result, dict):
            return NodeResult(outputs=result)
        raise TypeError(f"Node {id} returned unsupported result type: {type(result)!r}")

    def list(self) -> list[RegisteredNode]:
        return [self._nodes[key] for key in sorted(self._nodes)]


class GateRegistry(NodeRegistry):
    """Reserved registry type for custom gate implementations."""

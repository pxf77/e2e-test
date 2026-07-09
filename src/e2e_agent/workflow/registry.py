from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class RegisteredNode:
    id: str
    implementation: Callable[..., Any]
    kind: str = "agent"


class NodeRegistry:
    def __init__(self) -> None:
        self._nodes: dict[str, RegisteredNode] = {}

    def register(self, id: str, implementation: Callable[..., Any], kind: str = "agent") -> None:
        self._nodes[id] = RegisteredNode(id=id, implementation=implementation, kind=kind)

    def get(self, id: str) -> RegisteredNode:
        try:
            return self._nodes[id]
        except KeyError as exc:
            raise KeyError(f"Unknown workflow node implementation: {id}") from exc

    def list(self) -> list[RegisteredNode]:
        return [self._nodes[key] for key in sorted(self._nodes)]


class GateRegistry(NodeRegistry):
    """Specialized registry for gate implementations and policies."""

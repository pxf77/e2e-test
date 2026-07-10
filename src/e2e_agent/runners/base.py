from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ExecutionPlan:
    id: str
    runner: str
    scenarios: list[dict[str, Any]] = field(default_factory=list)
    fixtures: dict[str, Any] = field(default_factory=dict)
    env: dict[str, Any] = field(default_factory=dict)
    artifacts_dir: str | None = None


@dataclass
class ExecutionResult:
    run_id: str
    runner: str
    status: str
    summary: dict[str, Any]
    failures: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)


class ExecutionRunner(Protocol):
    name: str

    def prepare(self, context: Any) -> ExecutionPlan:
        ...

    async def execute(self, plan: ExecutionPlan) -> ExecutionResult:
        ...

    def collect_artifacts(self, result: ExecutionResult) -> list[dict[str, Any]]:
        ...

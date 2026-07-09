from __future__ import annotations

from typing import Any


class RunnerRegistry:
    def __init__(self) -> None:
        self._runners: dict[str, Any] = {}

    def register(self, name: str, runner: Any) -> None:
        self._runners[name] = runner

    def get(self, name: str) -> Any:
        try:
            return self._runners[name]
        except KeyError as exc:
            raise KeyError(f"Unknown execution runner: {name}") from exc

    def list_names(self) -> list[str]:
        return sorted(self._runners)

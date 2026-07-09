from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol


class DataProvider(Protocol):
    name: str

    def load(self, profile: dict[str, Any], base_dir: Path) -> dict[str, Any]:
        ...


class StaticJsonProvider:
    name = "static_json"

    def load(self, profile: dict[str, Any], base_dir: Path) -> dict[str, Any]:
        file_name = profile.get("file")
        if not file_name:
            raise ValueError("static_json profile requires 'file'")
        path = base_dir / str(file_name)
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"static_json profile must be an object: {path}")
        return payload


class DataProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, DataProvider] = {}
        self.register(StaticJsonProvider())

    def register(self, provider: DataProvider) -> None:
        self._providers[provider.name] = provider

    def get(self, name: str) -> DataProvider:
        try:
            return self._providers[name]
        except KeyError as exc:
            raise KeyError(f"Unknown data provider: {name}") from exc

    def list_names(self) -> list[str]:
        return sorted(self._providers)

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DomainPack:
    """Resolved domain knowledge package consumed by the framework core."""

    id: str
    name: str
    version: str
    root: Path
    manifest: dict[str, Any]
    ontology: dict[str, Any] = field(default_factory=dict)
    state_machine: dict[str, Any] = field(default_factory=dict)
    state_deps: dict[str, Any] = field(default_factory=dict)
    assertion_pack: dict[str, Any] = field(default_factory=dict)
    data_pack: dict[str, Any] = field(default_factory=dict)
    prompts: dict[str, str] = field(default_factory=dict)

    @property
    def page_types(self) -> list[str]:
        raw = self.manifest.get("page_types") or []
        return [str(item) for item in raw]

    @property
    def supported_workflows(self) -> list[str]:
        raw = self.manifest.get("supported_workflows") or []
        return [str(item) for item in raw]

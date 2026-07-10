from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from e2e_agent.config.yaml_loader import load_yaml_file
from e2e_agent.contracts import ContractRegistry


@dataclass(frozen=True)
class PluginManifest:
    id: str
    version: str
    kind: str
    root: Path
    path: Path
    payload: dict[str, Any]

    @property
    def implementation_id(self) -> str:
        return f"plugin.{self.id}"

    @property
    def runtime_type(self) -> str:
        return str((self.payload.get("runtime") or {}).get("type") or "python")

    @property
    def entry_path(self) -> Path:
        entry = str((self.payload.get("runtime") or {}).get("entry") or "")
        return self.root / entry


class PluginManifestLoader:
    def __init__(self, plugins_root: Path, contract_registry: ContractRegistry | None = None) -> None:
        self.plugins_root = plugins_root
        self.contract_registry = contract_registry or ContractRegistry().discover()

    def discover(self) -> list[PluginManifest]:
        if not self.plugins_root.exists():
            return []
        manifests: list[PluginManifest] = []
        paths = sorted(
            {
                *self.plugins_root.glob("*/plugin.yaml"),
                *self.plugins_root.glob("*/plugin.yml"),
            }
        )
        for path in paths:
            payload = load_yaml_file(path)
            self.contract_registry.validate("plugin-manifest", "v2", payload)
            manifest = PluginManifest(
                id=str(payload["id"]),
                version=str(payload["version"]),
                kind=str(payload["kind"]),
                root=path.parent,
                path=path,
                payload=payload,
            )
            if manifest.runtime_type != "builtin" and not manifest.entry_path.exists():
                raise FileNotFoundError(f"Plugin entry not found: {manifest.entry_path}")
            manifests.append(manifest)
        ids = [item.id for item in manifests]
        duplicates = sorted({item for item in ids if ids.count(item) > 1})
        if duplicates:
            raise ValueError(f"Duplicate plugin ids: {duplicates}")
        return manifests

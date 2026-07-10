from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .masking import mask_data
from .providers import DataProviderRegistry


@dataclass(frozen=True)
class DataResolution:
    actual: dict[str, Any]
    public: dict[str, Any]
    metadata: dict[str, Any]


class DataResolver:
    """Resolve selected Data Pack profiles into runtime and artifact-safe views."""

    def __init__(self, registry: DataProviderRegistry | None = None) -> None:
        self.registry = registry or DataProviderRegistry()

    def resolve(
        self,
        data_pack: dict[str, Any],
        profile_names: list[str],
        *,
        base_dir: Path,
    ) -> DataResolution:
        profiles = data_pack.get("profiles") or {}
        actual: dict[str, Any] = {}
        public: dict[str, Any] = {}
        metadata: dict[str, Any] = {"profiles": {}, "provider_names": self.registry.list_names()}
        for profile_name in profile_names:
            profile = profiles.get(profile_name)
            if not isinstance(profile, dict):
                raise KeyError(f"Unknown data profile: {profile_name}")
            provider_name = str(profile.get("provider") or "")
            provider = self.registry.get(provider_name)
            resolved = provider.load(profile, base_dir)
            actual[profile_name] = resolved.value
            public[profile_name] = mask_data(resolved.value, force=resolved.sensitive)
            metadata["profiles"][profile_name] = {
                "provider": provider_name,
                "sensitive": resolved.sensitive,
                **(resolved.metadata or {}),
            }
        return DataResolution(actual=actual, public=public, metadata=metadata)


def merge_data_packs(*packs: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {"id": "runtime-data", "version": "1.0.0", "profiles": {}}
    for pack in packs:
        if not isinstance(pack, dict):
            continue
        for profile_name, profile in (pack.get("profiles") or {}).items():
            merged["profiles"][str(profile_name)] = profile
    return merged

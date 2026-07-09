from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from e2e_agent.contracts import ContractRegistry

from .model import DomainPack


class DomainPackLoader:
    """Loads domain packages from ``domains/{domain_id}``.

    A domain package owns page ontology, state dependency governance,
    assertion templates, data profiles, and domain prompts. The framework core
    receives a loaded ``DomainPack`` and remains unaware of industry terms.
    """

    def __init__(self, domains_root: Path | None = None, registry: ContractRegistry | None = None) -> None:
        self.domains_root = domains_root or Path(__file__).resolve().parents[3] / "domains"
        self.registry = registry or ContractRegistry().discover()

    def list_domain_ids(self) -> list[str]:
        if not self.domains_root.exists():
            return []
        return sorted(path.name for path in self.domains_root.iterdir() if (path / "domain.yaml").exists())

    def load(self, domain_id: str) -> DomainPack:
        root = self.domains_root / domain_id
        manifest_path = root / "domain.yaml"
        if not manifest_path.exists():
            raise FileNotFoundError(f"Domain manifest not found: {manifest_path}")
        manifest = self._read_yaml(manifest_path)
        self.registry.validate("domain-pack", "v2", manifest)

        loaded_id = str(manifest.get("id") or "")
        if loaded_id != domain_id:
            raise ValueError(f"Domain id mismatch: requested {domain_id}, manifest has {loaded_id}")

        prompts = self._load_prompts(root, manifest.get("prompts") or {})
        return DomainPack(
            id=loaded_id,
            name=str(manifest.get("name") or loaded_id),
            version=str(manifest.get("version") or "0"),
            root=root,
            manifest=manifest,
            ontology=self._load_optional_yaml(root, manifest.get("ontology")),
            state_deps=self._load_optional_yaml(root, manifest.get("state_deps")),
            assertion_pack=self._load_optional_yaml(root, manifest.get("assertion_pack")),
            data_pack=self._load_optional_yaml(root, manifest.get("data_pack")),
            prompts=prompts,
        )

    @staticmethod
    def _read_yaml(path: Path) -> dict[str, Any]:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(payload, dict):
            raise ValueError(f"YAML document must be an object: {path}")
        return payload

    def _load_optional_yaml(self, root: Path, relative_path: Any) -> dict[str, Any]:
        if not relative_path:
            return {}
        path = root / str(relative_path)
        if not path.exists():
            raise FileNotFoundError(f"Referenced domain file not found: {path}")
        return self._read_yaml(path)

    @staticmethod
    def _load_prompts(root: Path, prompt_map: dict[str, Any]) -> dict[str, str]:
        result: dict[str, str] = {}
        for name, relative_path in prompt_map.items():
            path = root / str(relative_path)
            if not path.exists():
                raise FileNotFoundError(f"Referenced domain prompt not found: {path}")
            result[str(name)] = path.read_text(encoding="utf-8")
        return result

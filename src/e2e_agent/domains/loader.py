from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from e2e_agent.config.yaml_loader import load_yaml_file
from e2e_agent.contracts import ContractRegistry

from .model import DomainPack


class DomainPackLoader:
    """Load and resolve inheritable Domain Packs from ``domains/{domain_id}``."""

    def __init__(self, domains_root: Path | None = None, registry: ContractRegistry | None = None) -> None:
        self.domains_root = domains_root or Path(__file__).resolve().parents[3] / "domains"
        self.registry = registry or ContractRegistry().discover()
        self._cache: dict[str, DomainPack] = {}

    def list_domain_ids(self) -> list[str]:
        if not self.domains_root.exists():
            return []
        return sorted(path.name for path in self.domains_root.iterdir() if (path / "domain.yaml").exists())

    def load(self, domain_id: str) -> DomainPack:
        if domain_id in self._cache:
            return self._cache[domain_id]
        pack = self._load(domain_id, stack=[])
        self._cache[domain_id] = pack
        return pack

    def _load(self, domain_id: str, stack: list[str]) -> DomainPack:
        if domain_id in stack:
            chain = " -> ".join([*stack, domain_id])
            raise ValueError(f"Domain inheritance cycle detected: {chain}")
        if domain_id in self._cache:
            return self._cache[domain_id]

        root = self.domains_root / domain_id
        manifest_path = root / "domain.yaml"
        if not manifest_path.exists():
            raise FileNotFoundError(f"Domain manifest not found: {manifest_path}")
        own_manifest = self._read_yaml(manifest_path)
        self.registry.validate("domain-pack", "v2", own_manifest)
        loaded_id = str(own_manifest.get("id") or "")
        if loaded_id != domain_id:
            raise ValueError(f"Domain id mismatch: requested {domain_id}, manifest has {loaded_id}")

        parent_ids = [str(item) for item in own_manifest.get("extends") or []]
        manifest: dict[str, Any] = {}
        ontology: dict[str, Any] = {}
        state_machine: dict[str, Any] = {}
        state_deps: dict[str, Any] = {}
        assertion_pack: dict[str, Any] = {}
        data_pack: dict[str, Any] = {}
        prompts: dict[str, str] = {}
        lineage: list[str] = []

        for parent_id in parent_ids:
            parent = self._load(parent_id, [*stack, domain_id])
            manifest = _deep_merge(manifest, parent.manifest)
            ontology = _deep_merge(ontology, parent.ontology)
            if parent.state_machine:
                state_machine = deepcopy(parent.state_machine)
            state_deps = _deep_merge(state_deps, parent.state_deps)
            assertion_pack = _deep_merge(assertion_pack, parent.assertion_pack)
            data_pack = _deep_merge(data_pack, parent.data_pack)
            prompts.update(parent.prompts)
            lineage.extend(str(item) for item in parent.manifest.get("resolved_lineage") or [parent.id])

        manifest = _deep_merge(manifest, own_manifest)
        manifest["id"] = loaded_id
        manifest["resolved_lineage"] = _unique([*lineage, *parent_ids])
        ontology = _deep_merge(ontology, self._load_optional_yaml(root, own_manifest.get("ontology")))
        own_state_machine = self._load_optional_yaml(root, own_manifest.get("state_machine"))
        if own_state_machine:
            state_machine = own_state_machine
        state_deps = _deep_merge(state_deps, self._load_optional_yaml(root, own_manifest.get("state_deps")))
        assertion_pack = _deep_merge(assertion_pack, self._load_optional_yaml(root, own_manifest.get("assertion_pack")))
        data_pack = _deep_merge(data_pack, self._load_optional_yaml(root, own_manifest.get("data_pack")))
        prompts.update(self._load_prompts(root, own_manifest.get("prompts") or {}))

        pack = DomainPack(
            id=loaded_id,
            name=str(manifest.get("name") or loaded_id),
            version=str(manifest.get("version") or "0"),
            root=root,
            manifest=manifest,
            ontology=ontology,
            state_machine=state_machine,
            state_deps=state_deps,
            assertion_pack=assertion_pack,
            data_pack=data_pack,
            prompts=prompts,
        )
        self._cache[domain_id] = pack
        return pack

    @staticmethod
    def _read_yaml(path: Path) -> dict[str, Any]:
        return load_yaml_file(path)

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


def _deep_merge(base: Any, override: Any) -> Any:
    if isinstance(base, dict) and isinstance(override, dict):
        result = deepcopy(base)
        for key, value in override.items():
            result[key] = _deep_merge(result[key], value) if key in result else deepcopy(value)
        return result
    if isinstance(base, list) and isinstance(override, list):
        return _unique([*deepcopy(base), *deepcopy(override)])
    return deepcopy(override)


def _unique(values: list[Any]) -> list[Any]:
    result: list[Any] = []
    seen: set[str] = set()
    for value in values:
        marker = repr(value)
        if marker not in seen:
            seen.add(marker)
            result.append(value)
    return result

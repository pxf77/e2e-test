from __future__ import annotations

import asyncio
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from e2e_agent.contracts import ContractRegistry

from .loader import PluginManifest, PluginManifestLoader
from .runtime import PluginRuntime

if TYPE_CHECKING:
    from e2e_agent.workflow.registry import NodeRegistry
    from e2e_agent.workflow.state import WorkflowRuntimeState


class PluginManager:
    def __init__(
        self,
        plugins_root: Path | Iterable[Path],
        *,
        contract_registry: ContractRegistry | None = None,
        runtime: PluginRuntime | None = None,
    ) -> None:
        self.contract_registry = contract_registry or ContractRegistry().discover()
        self.roots = self._normalise_roots(plugins_root)
        self.loaders = [PluginManifestLoader(root, self.contract_registry) for root in self.roots]
        # Compatibility alias for callers that inspected the original single loader.
        self.loader = self.loaders[0] if self.loaders else None
        self.runtime = runtime or PluginRuntime()
        self._manifests: dict[str, PluginManifest] = {}

    @staticmethod
    def _normalise_roots(plugins_root: Path | Iterable[Path]) -> tuple[Path, ...]:
        if isinstance(plugins_root, Path):
            candidates = [plugins_root]
        else:
            candidates = [Path(item) for item in plugins_root]
        result: list[Path] = []
        seen: set[Path] = set()
        for candidate in candidates:
            resolved = candidate.resolve()
            if resolved not in seen:
                seen.add(resolved)
                result.append(resolved)
        return tuple(result)

    def discover(self) -> list[PluginManifest]:
        manifests = [manifest for loader in self.loaders for manifest in loader.discover()]
        ids = [item.id for item in manifests]
        duplicates = sorted({item for item in ids if ids.count(item) > 1})
        if duplicates:
            locations = {
                plugin_id: [str(item.path) for item in manifests if item.id == plugin_id]
                for plugin_id in duplicates
            }
            raise ValueError(f"Duplicate plugin ids across discovery roots: {locations}")
        self._manifests = {item.id: item for item in manifests}
        return sorted(manifests, key=lambda item: (item.id, str(item.path)))

    def list(self) -> list[PluginManifest]:
        return list(self._manifests.values()) if self._manifests else self.discover()

    def register_nodes(self, registry: "NodeRegistry") -> None:
        for manifest in self.list():
            if manifest.kind not in {"node", "skill"}:
                continue
            registry.register(manifest.implementation_id, self._handler(manifest), kind="plugin")

    def _handler(self, manifest: PluginManifest):
        async def _invoke(state: "WorkflowRuntimeState", node_spec: dict[str, Any]):
            from e2e_agent.workflow.registry import NodeResult

            self._validate_inputs(manifest, state)
            payload = {
                "run_context": {
                    "run_id": state.get("run_id"),
                    "app_id": state.get("app_id"),
                    "domain_id": state.get("domain_id"),
                    "workflow_id": state.get("workflow_id"),
                    "env": state.get("env"),
                },
                "inputs": state.get("inputs") or {},
                "artifacts": state.get("artifacts") or {},
                "domain": state.get("domain") or {},
                "node": node_spec,
                "plugin_config": manifest.payload.get("config") or {},
            }
            result = await asyncio.to_thread(self.runtime.run, manifest, payload)
            if result.status not in {"success", "passed", "ok"}:
                raise RuntimeError(f"Plugin {manifest.id} failed: {result.error or result.status}")
            self._validate_outputs(manifest, result.outputs)
            return NodeResult(
                outputs=dict(result.outputs),
                warnings=list(result.warnings),
                metrics={"plugin_id": manifest.id, "plugin_version": manifest.version, **result.metrics},
            )

        return _invoke

    def _validate_inputs(self, manifest: PluginManifest, state: "WorkflowRuntimeState") -> None:
        contracts = (manifest.payload.get("contracts") or {}).get("input") or []
        for contract_ref in contracts:
            name, version = self._split_contract(str(contract_ref))
            payload = self._find_contract_payload(name, state.get("artifacts") or {})
            if payload is None:
                raise KeyError(f"Plugin {manifest.id} requires input contract {contract_ref}")
            self.contract_registry.validate(name, version, payload)

    def _validate_outputs(self, manifest: PluginManifest, outputs: dict[str, Any]) -> None:
        contracts = (manifest.payload.get("contracts") or {}).get("output") or []
        for contract_ref in contracts:
            name, version = self._split_contract(str(contract_ref))
            payload = self._find_contract_payload(name, outputs)
            if payload is None:
                raise KeyError(f"Plugin {manifest.id} did not produce contract {contract_ref}")
            self.contract_registry.validate(name, version, payload)

    @staticmethod
    def _split_contract(reference: str) -> tuple[str, str]:
        if "@" not in reference:
            raise ValueError(f"Contract reference must be name@version: {reference}")
        name, version = reference.rsplit("@", 1)
        normalized_version = version if version.startswith("v") else f"v{version}"
        return name, normalized_version

    @staticmethod
    def _find_contract_payload(name: str, values: dict[str, Any]) -> Any | None:
        candidates = [name, name.replace("-", "_"), name.replace("_", "-")]
        for candidate in candidates:
            if candidate in values:
                return values[candidate]
        return None

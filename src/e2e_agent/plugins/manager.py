from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from e2e_agent.contracts import ContractRegistry
from e2e_agent.workflow.registry import NodeRegistry, NodeResult
from e2e_agent.workflow.state import WorkflowRuntimeState

from .loader import PluginManifest, PluginManifestLoader
from .runtime import PluginRuntime


class PluginManager:
    def __init__(
        self,
        plugins_root: Path,
        *,
        contract_registry: ContractRegistry | None = None,
        runtime: PluginRuntime | None = None,
    ) -> None:
        self.contract_registry = contract_registry or ContractRegistry().discover()
        self.loader = PluginManifestLoader(plugins_root, self.contract_registry)
        self.runtime = runtime or PluginRuntime()
        self._manifests: dict[str, PluginManifest] = {}

    def discover(self) -> list[PluginManifest]:
        manifests = self.loader.discover()
        self._manifests = {item.id: item for item in manifests}
        return manifests

    def list(self) -> list[PluginManifest]:
        return list(self._manifests.values()) if self._manifests else self.discover()

    def register_nodes(self, registry: NodeRegistry) -> None:
        for manifest in self.list():
            if manifest.kind not in {"node", "skill"}:
                continue
            registry.register(manifest.implementation_id, self._handler(manifest), kind="plugin")

    def _handler(self, manifest: PluginManifest):
        async def _invoke(state: WorkflowRuntimeState, node_spec: dict[str, Any]) -> NodeResult:
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

    def _validate_inputs(self, manifest: PluginManifest, state: WorkflowRuntimeState) -> None:
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

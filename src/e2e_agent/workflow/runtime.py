from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from e2e_agent.adapters.legacy import build_legacy_state
from e2e_agent.artifacts import ArtifactManifestStore
from e2e_agent.config.resolver import ConfigResolver
from e2e_agent.config.yaml_loader import load_yaml_file
from e2e_agent.contracts import ContractRegistry
from e2e_agent.domains import DomainPackLoader

from .compiler import WorkflowCompiler
from .defaults import build_default_node_registry
from .dsl import WorkflowDefinition, load_workflow
from .gates import complete_gate_checkpoint, load_gate_checkpoint
from .registry import NodeRegistry
from .state import WorkflowRuntimeState


def _new_run_id(app_id: str) -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{app_id}-{timestamp}"


class WorkflowRuntime:
    """Load App/Domain/Workflow packs and execute the compiled LangGraph."""

    def __init__(
        self,
        *,
        repo_root: Path | None = None,
        registry: NodeRegistry | None = None,
        contract_registry: ContractRegistry | None = None,
    ) -> None:
        self.repo_root = repo_root or Path(__file__).resolve().parents[3]
        self.contract_registry = contract_registry or ContractRegistry(self.repo_root / "schemas").discover()
        self.registry = registry or build_default_node_registry(self.repo_root)
        self.compiler = WorkflowCompiler()
        self.config_resolver = ConfigResolver()
        self.domain_loader = DomainPackLoader(self.repo_root / "domains", registry=self.contract_registry)

    def resolve_workflow_path(self, workflow: str | Path) -> Path:
        path = Path(workflow)
        if path.suffix in {".yaml", ".yml"}:
            resolved = path if path.is_absolute() else self.repo_root / path
        else:
            resolved = self.repo_root / "workflows" / f"{path}.yaml"
        if not resolved.exists():
            raise FileNotFoundError(f"Workflow not found: {resolved}")
        return resolved

    def load_definition(self, workflow: str | Path) -> WorkflowDefinition:
        return load_workflow(self.resolve_workflow_path(workflow), registry=self.contract_registry)

    def build(
        self,
        workflow: str | Path,
        *,
        checkpointer: Any | None = None,
        entrypoint_override: str | None = None,
    ):
        definition = self.load_definition(workflow)
        return self.compiler.compile_langgraph(
            definition,
            self.registry,
            checkpointer=checkpointer,
            entrypoint_override=entrypoint_override,
        )

    def prepare_state(
        self,
        *,
        app_path: Path,
        workflow: str | Path,
        env: str = "local",
        run_id: str | None = None,
        inputs: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        gates: dict[str, dict[str, Any]] | None = None,
    ) -> WorkflowRuntimeState:
        resolved_app = app_path if app_path.is_absolute() else self.repo_root / app_path
        app = load_yaml_file(resolved_app)
        self.contract_registry.validate("app", "v2", app)
        domain = self.domain_loader.load(str(app["domain"]))
        definition = self.load_definition(workflow)
        if domain.supported_workflows and definition.id not in domain.supported_workflows:
            raise ValueError(f"Domain {domain.id} does not support workflow {definition.id}")

        actual_inputs = dict(inputs or {})
        actual_run_id = run_id or _new_run_id(str(app["id"]))
        app_root = resolved_app.parent
        artifacts_dir = app_root / ".assets" / "runs" / actual_run_id
        effective_config = self.config_resolver.resolve(
            defaults={
                "runner": {"default": (app.get("execution") or {}).get("default_runner") or "playwright"},
                "workflow": {"id": definition.id, "version": definition.version},
            },
            domain=domain.manifest,
            app=app,
            app_root=app_root,
            env=env,
            runtime=actual_inputs.get("config_overrides") if isinstance(actual_inputs.get("config_overrides"), dict) else {},
        )
        runtime_metadata = {
            "repo_root": str(self.repo_root),
            "app_root": str(app_root),
            "app_path": str(resolved_app),
            "gate_checkpoint_dir": str(self.repo_root / ".local" / "e2e-agent" / "gate-checkpoints"),
            "artifacts_dir": str(artifacts_dir),
            "artifact_manifest_path": str(artifacts_dir / "artifact-manifest.json"),
            "domain_lineage": list(domain.manifest.get("resolved_lineage") or []),
        }
        runtime_metadata.update(metadata or {})
        legacy_required = any(
            str(node.get("implementation") or "").startswith("legacy.")
            for node in definition.payload.get("nodes") or []
        )
        legacy_state = (
            build_legacy_state(
                app=app,
                app_root=app_root,
                run_id=actual_run_id,
                artifact_root=self.repo_root,
            )
            if legacy_required
            else {}
        )
        state: WorkflowRuntimeState = {
            "run_id": actual_run_id,
            "app_id": str(app["id"]),
            "domain_id": domain.id,
            "workflow_id": definition.id,
            "env": env,
            "config": effective_config,
            "app": app,
            "domain": {
                "manifest": domain.manifest,
                "root": str(domain.root),
                "ontology": domain.ontology,
                "state_deps": domain.state_deps,
                "assertion_pack": domain.assertion_pack,
                "data_pack": domain.data_pack,
            },
            "inputs": actual_inputs,
            "artifacts": {},
            "artifact_manifest": {},
            "runtime_data": {},
            "gates": dict(gates or {}),
            "metadata": runtime_metadata,
            "errors": [],
            "legacy_state": legacy_state,
            "node_trace": [],
        }
        store = ArtifactManifestStore.from_state(state, contract_registry=self.contract_registry)
        if store is not None:
            state["artifact_manifest"] = store.initialize()
        return state

    async def run(
        self,
        *,
        app_path: Path,
        workflow: str | Path,
        env: str = "local",
        run_id: str | None = None,
        inputs: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        gates: dict[str, dict[str, Any]] | None = None,
    ) -> WorkflowRuntimeState:
        state = self.prepare_state(
            app_path=app_path,
            workflow=workflow,
            env=env,
            run_id=run_id,
            inputs=inputs,
            metadata=metadata,
            gates=gates,
        )
        graph = self.build(workflow)
        result = await graph.ainvoke(state, config={"recursion_limit": 100})
        return self._finalize(result)

    async def resume(
        self,
        *,
        run_id: str,
        checkpoint_dir: str | Path | None = None,
    ) -> WorkflowRuntimeState:
        directory = Path(checkpoint_dir) if checkpoint_dir else self.repo_root / ".local" / "e2e-agent" / "gate-checkpoints"
        checkpoint_path, checkpoint = load_gate_checkpoint(run_id, directory)
        state: WorkflowRuntimeState = dict(checkpoint["state"])
        gate_id = str(checkpoint.get("pending_gate") or "")
        gate = dict((state.get("gates") or {}).get(gate_id) or {})
        outcome = str(gate.get("status") or "")
        if outcome not in {"approved", "rejected"}:
            raise ValueError(f"Gate {gate_id} has no decision; approve or reject before resume")

        workflow_id = str(state.get("workflow_id") or checkpoint.get("workflow_id") or "")
        definition = self.load_definition(workflow_id)
        target = self.compiler.route_target(definition, gate_id, outcome)

        runtime_metadata = dict(state.get("metadata") or {})
        history = list(runtime_metadata.get("gate_history") or [])
        history.append({"gate_id": gate_id, **gate})
        runtime_metadata["gate_history"] = history
        state["metadata"] = runtime_metadata

        if outcome == "rejected":
            current_gates = dict(state.get("gates") or {})
            current_gates.pop(gate_id, None)
            state["gates"] = current_gates
            legacy_state = dict(state.get("legacy_state") or {})
            legacy_state.pop(gate_id, None)
            state["legacy_state"] = legacy_state

        if target == "END":
            result = self._finalize(state)
            complete_gate_checkpoint(checkpoint_path, checkpoint, state=result, next_status="completed")
            return result

        graph = self.build(workflow_id, entrypoint_override=target)
        result = await graph.ainvoke(state, config={"recursion_limit": 100})
        result = self._finalize(result)
        pending = [
            gate_name
            for gate_name, gate_state in (result.get("gates") or {}).items()
            if str((gate_state or {}).get("status") or "") == "pending"
        ]
        active_checkpoint = checkpoint
        if pending:
            _, active_checkpoint = load_gate_checkpoint(run_id, directory)
        complete_gate_checkpoint(
            checkpoint_path,
            active_checkpoint,
            state=result,
            next_status="pending" if pending else "completed",
        )
        return result

    def _finalize(self, state: WorkflowRuntimeState) -> WorkflowRuntimeState:
        result: WorkflowRuntimeState = dict(state)
        store = ArtifactManifestStore.from_state(result, contract_registry=self.contract_registry)
        if store is not None:
            result["artifact_manifest"] = store.finalize(result)
        return result

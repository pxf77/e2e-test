from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from e2e_agent.adapters.legacy import build_legacy_state
from e2e_agent.artifacts import ArtifactManifestStore
from e2e_agent.config.yaml_loader import load_yaml_file
from e2e_agent.contracts import ContractRegistry
from e2e_agent.domains import DomainPackLoader

from .compiler import WorkflowCompiler
from .defaults import build_default_node_registry
from .dsl import WorkflowDefinition, load_workflow
from .registry import NodeRegistry
from .state import WorkflowRuntimeState


def _new_run_id(app_id: str) -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{app_id}-{timestamp}"


class WorkflowRuntime:
    """Loads App/Domain/Workflow packs and executes the compiled LangGraph."""

    def __init__(
        self,
        *,
        repo_root: Path | None = None,
        registry: NodeRegistry | None = None,
        contract_registry: ContractRegistry | None = None,
    ) -> None:
        self.repo_root = repo_root or Path(__file__).resolve().parents[3]
        self.contract_registry = contract_registry or ContractRegistry(self.repo_root / "schemas").discover()
        self.registry = registry or build_default_node_registry()
        self.compiler = WorkflowCompiler()
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

    def build(self, workflow: str | Path, *, checkpointer: Any | None = None):
        definition = self.load_definition(workflow)
        return self.compiler.compile_langgraph(definition, self.registry, checkpointer=checkpointer)

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

        actual_run_id = run_id or _new_run_id(str(app["id"]))
        app_root = resolved_app.parent
        artifacts_dir = app_root / ".assets" / "runs" / actual_run_id
        runtime_metadata = {
            "repo_root": str(self.repo_root),
            "app_root": str(app_root),
            "app_path": str(resolved_app),
            "gate_checkpoint_dir": str(self.repo_root / ".local" / "e2e-agent" / "gate-checkpoints"),
            "artifacts_dir": str(artifacts_dir),
            "artifact_manifest_path": str(artifacts_dir / "artifact-manifest.json"),
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
            "app": app,
            "domain": {
                "manifest": domain.manifest,
                "ontology": domain.ontology,
                "state_deps": domain.state_deps,
                "assertion_pack": domain.assertion_pack,
                "data_pack": domain.data_pack,
            },
            "inputs": dict(inputs or {}),
            "artifacts": {},
            "artifact_manifest": {},
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
        store = ArtifactManifestStore.from_state(result, contract_registry=self.contract_registry)
        if store is not None:
            result["artifact_manifest"] = store.finalize(result)
        return result

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langgraph.graph import END, StateGraph

from e2e_agent.artifacts import ArtifactManifestStore

from .dsl import WorkflowDefinition, validate_workflow_graph
from .gates import evaluate_gate, gate_route, persist_pending_gate
from .registry import NodeRegistry
from .state import WorkflowRuntimeState


@dataclass(frozen=True)
class CompiledWorkflow:
    """Normalized static representation of a Workflow DSL document."""

    id: str
    version: str
    nodes: dict[str, dict[str, Any]]
    edges: list[dict[str, Any]]
    entrypoint: str


class WorkflowCompiler:
    """Validates Workflow DSL and compiles it into a LangGraph StateGraph."""

    def compile(self, definition: WorkflowDefinition) -> CompiledWorkflow:
        errors = validate_workflow_graph(definition)
        if errors:
            raise ValueError("\n".join(errors))
        nodes = {str(node["id"]): dict(node) for node in definition.payload.get("nodes", [])}
        if not nodes:
            raise ValueError(f"Workflow has no nodes: {definition.path}")
        entrypoint = str(definition.payload.get("entrypoint") or next(iter(nodes)))
        if entrypoint not in nodes:
            raise ValueError(f"Workflow entrypoint not found: {entrypoint}")
        return CompiledWorkflow(
            id=definition.id,
            version=definition.version,
            nodes=nodes,
            edges=[dict(edge) for edge in definition.payload.get("edges", [])],
            entrypoint=entrypoint,
        )

    def compile_langgraph(
        self,
        definition: WorkflowDefinition,
        registry: NodeRegistry,
        *,
        checkpointer: Any | None = None,
        entrypoint_override: str | None = None,
    ):
        compiled = self.compile(definition)
        builder = StateGraph(WorkflowRuntimeState)

        for node_id, node_spec in compiled.nodes.items():
            if node_spec.get("type") == "gate":
                builder.add_node(node_id, self._make_gate_node(node_id, node_spec))
                continue
            implementation = str(node_spec.get("implementation") or "")
            registry.get(implementation)  # fail during compilation, not execution
            builder.add_node(node_id, self._make_registered_node(registry, implementation, node_spec))

        entrypoint = entrypoint_override or compiled.entrypoint
        if entrypoint not in compiled.nodes:
            raise ValueError(f"Workflow resume entrypoint not found: {entrypoint}")
        builder.set_entry_point(entrypoint)

        outgoing: dict[str, list[dict[str, Any]]] = {}
        for edge in compiled.edges:
            outgoing.setdefault(str(edge["from"]), []).append(edge)

        for source, edges in outgoing.items():
            conditional = [edge for edge in edges if "on" in edge]
            unconditional = [edge for edge in edges if "on" not in edge]
            if conditional and unconditional:
                raise ValueError(f"Node {source} mixes conditional and unconditional edges")
            if conditional:
                route_map: dict[str, Any] = {}
                for edge in conditional:
                    outcome = str(edge["on"])
                    if outcome in route_map:
                        raise ValueError(f"Node {source} has duplicate route outcome: {outcome}")
                    route_map[outcome] = END if edge["to"] == "END" else str(edge["to"])
                builder.add_conditional_edges(source, gate_route(source), route_map)
                continue
            if len(unconditional) > 1:
                raise ValueError(f"Node {source} has multiple unconditional edges")
            if unconditional:
                target = unconditional[0]["to"]
                builder.add_edge(source, END if target == "END" else str(target))

        return builder.compile(checkpointer=checkpointer) if checkpointer is not None else builder.compile()

    @staticmethod
    def route_target(definition: WorkflowDefinition, gate_id: str, outcome: str) -> str:
        matches = [
            edge
            for edge in definition.payload.get("edges") or []
            if str(edge.get("from")) == gate_id and str(edge.get("on")) == outcome
        ]
        if len(matches) != 1:
            raise ValueError(f"Expected exactly one route for {gate_id}:{outcome}, found {len(matches)}")
        return str(matches[0]["to"])

    @staticmethod
    def _make_registered_node(
        registry: NodeRegistry,
        implementation: str,
        node_spec: dict[str, Any],
    ):
        async def _node(state: WorkflowRuntimeState) -> dict[str, Any]:
            result = await registry.invoke(implementation, state, node_spec)
            artifacts = dict(state.get("artifacts") or {})
            artifacts.update(result.outputs)

            artifact_manifest = dict(state.get("artifact_manifest") or {})
            store = ArtifactManifestStore.from_state(state)
            if store is not None and result.outputs:
                artifact_manifest = store.record_outputs(
                    node_id=str(node_spec["id"]),
                    implementation=implementation,
                    outputs=result.outputs,
                )

            trace = list(state.get("node_trace") or [])
            trace.append(
                {
                    "node_id": node_spec["id"],
                    "implementation": implementation,
                    "outputs": sorted(result.outputs),
                    "artifact_ids": sorted(
                        str(item.get("id"))
                        for item in artifact_manifest.get("artifacts") or []
                        if isinstance(item, dict) and str(item.get("node_id") or "") == str(node_spec["id"])
                    ),
                    "warnings": list(result.warnings),
                    "metrics": dict(result.metrics),
                }
            )
            updates = dict(result.state_updates)
            updates["artifacts"] = artifacts
            updates["artifact_manifest"] = artifact_manifest
            updates["node_trace"] = trace
            if result.warnings:
                errors = list(state.get("errors") or [])
                errors.extend(
                    {
                        "type": "NodeWarning",
                        "node_id": node_spec["id"],
                        "message": warning,
                    }
                    for warning in result.warnings
                )
                updates["errors"] = errors
            return updates

        _node.__name__ = f"workflow_node_{node_spec['id']}"
        return _node

    @staticmethod
    def _make_gate_node(node_id: str, node_spec: dict[str, Any]):
        def _gate_node(state: WorkflowRuntimeState) -> dict[str, Any]:
            gate = evaluate_gate(state, node_spec)
            gates = dict(state.get("gates") or {})
            gates[node_id] = gate
            trace = list(state.get("node_trace") or [])
            trace.append(
                {
                    "node_id": node_id,
                    "implementation": node_spec.get("implementation"),
                    "gate_status": gate["status"],
                    "policy": gate.get("policy"),
                }
            )
            updates: dict[str, Any] = {"gates": gates, "node_trace": trace}
            legacy_state = dict(state.get("legacy_state") or {})
            if node_id in {"r1_gate", "r2_gate", "r3_gate", "r4_gate"}:
                legacy_state[node_id] = gate
                updates["legacy_state"] = legacy_state
            persist_pending_gate({**state, **updates}, node_id)
            return updates

        _gate_node.__name__ = f"workflow_gate_{node_id}"
        return _gate_node

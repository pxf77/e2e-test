"""LangGraph StateGraph definition for the 4-Agent + 4-Gate pipeline.

Graph topology:
    tc_merge ──→ r1_gate ──[approved]──→ path_extract
                         ──[rejected]──→ tc_merge     (retry)
                         ──[pending] ──→ END           (await human)

    path_extract ──→ r2_gate ──[approved]──→ explore
                             ──[rejected]──→ path_extract
                             ──[pending] ──→ END

    explore ──→ r3_gate ──[approved]──→ exec_healing
                        ──[rejected]──→ explore
                        ──[pending] ──→ END

    exec_healing ──→ r4_gate ──[approved]──→ END  (R4 is auto-approved)
                             ──[pending] ──→ END
"""
from __future__ import annotations

from langgraph.graph import END, StateGraph

from e2e_agent.legacy.agents.agent1_tc_merge.node import tc_merge_node
from e2e_agent.legacy.agents.agent2_path_extract.node import path_extract_node
from e2e_agent.legacy.agents.agent3_explore.node import explore_node
from e2e_agent.legacy.agents.agent4_exec.node import exec_healing_node
from e2e_agent.legacy.graph.gates import (
    r1_gate_node, r2_gate_node, r3_gate_node, r4_gate_node,
    route_r1, route_r2, route_r3, route_r4,
)
from e2e_agent.legacy.graph.state import E2EAgentState


def _build_builder() -> StateGraph:
    """Construct the StateGraph (without compiling)."""
    builder = StateGraph(E2EAgentState)

    builder.add_node("tc_merge", tc_merge_node)
    builder.add_node("r1_gate", r1_gate_node)
    builder.add_node("path_extract", path_extract_node)
    builder.add_node("r2_gate", r2_gate_node)
    builder.add_node("explore", explore_node)
    builder.add_node("r3_gate", r3_gate_node)
    builder.add_node("exec_healing", exec_healing_node)
    builder.add_node("r4_gate", r4_gate_node)

    builder.set_entry_point("tc_merge")

    builder.add_edge("tc_merge", "r1_gate")
    builder.add_conditional_edges(
        "r1_gate",
        route_r1,
        {"approved": "path_extract", "rejected": "tc_merge", "pending": END},
    )
    builder.add_edge("path_extract", "r2_gate")
    builder.add_conditional_edges(
        "r2_gate",
        route_r2,
        {"approved": "explore", "rejected": "path_extract", "pending": END},
    )
    builder.add_edge("explore", "r3_gate")
    builder.add_conditional_edges(
        "r3_gate",
        route_r3,
        {"approved": "exec_healing", "rejected": "explore", "pending": END},
    )
    builder.add_edge("exec_healing", "r4_gate")
    builder.add_conditional_edges(
        "r4_gate",
        route_r4,
        {"approved": END, "rejected": "exec_healing", "pending": END},
    )
    return builder


def build_graph(db_path: str = "e2e_agent.db"):
    """Build and compile the LangGraph StateGraph.

    Args:
        db_path: SQLite database path for LangGraph checkpointer.
                 Pass ":memory:" for in-memory (tests/smoke).
                 For production, use a file path (e.g. "e2e_agent.db").

    Returns:
        Compiled LangGraph app with SqliteSaver checkpointer.
        Note: SqliteSaver.from_conn_string is a context manager in langgraph 1.x.
        For persistent production use, call build_graph_with_saver() instead.
    """
    from langgraph.checkpoint.memory import MemorySaver

    # MemorySaver for quick smoke/test — replace with SqliteSaver for persistence
    checkpointer = MemorySaver()
    return _build_builder().compile(checkpointer=checkpointer)


def build_persistent_graph(db_path: str = "e2e_agent.db"):
    """Build a graph with SQLite persistence.

    Must be used as a context manager to ensure the SQLite connection is closed:

        with build_persistent_graph("e2e_agent.db") as app:
            result = await app.ainvoke(state, config=config)
    """
    from contextlib import contextmanager
    from langgraph.checkpoint.sqlite import SqliteSaver

    @contextmanager
    def _ctx():
        with SqliteSaver.from_conn_string(db_path) as checkpointer:
            yield _build_builder().compile(checkpointer=checkpointer)

    return _ctx()

"""Agent package aliases."""
from __future__ import annotations

from importlib import import_module
from types import ModuleType


_ALIASES = {
    "tc_merge_agent": "e2e_agent.agents.agent1_tc_merge.node",
    "path_extract_agent": "e2e_agent.agents.agent2_path_extract.node",
    "explore_agent": "e2e_agent.agents.agent3_explore.node",
    "exec_agent": "e2e_agent.agents.agent4_exec.node",
}


def __getattr__(name: str) -> ModuleType:
    if name not in _ALIASES:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(_ALIASES[name])
    globals()[name] = module
    return module

__all__ = [
    "tc_merge_agent",
    "path_extract_agent",
    "explore_agent",
    "exec_agent",
]

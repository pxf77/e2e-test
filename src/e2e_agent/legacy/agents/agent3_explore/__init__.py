"""Agent3: browser exploration and static element-set support."""
from __future__ import annotations

from e2e_agent.legacy.agents.agent3_explore.element_set import (
    STATIC_ELEMENT_SET_PATH,
    load_element_set_for_product,
    load_static_element_set,
)
from e2e_agent.legacy.agents.agent3_explore.node import explore_node

__all__ = [
    "STATIC_ELEMENT_SET_PATH",
    "explore_node",
    "load_element_set_for_product",
    "load_static_element_set",
]

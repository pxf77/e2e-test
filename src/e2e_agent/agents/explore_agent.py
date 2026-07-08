"""Backward-compatible module alias for agent3 exploration."""
from __future__ import annotations

import sys

from e2e_agent.agents.agent3_explore import node as _node

sys.modules[__name__] = _node

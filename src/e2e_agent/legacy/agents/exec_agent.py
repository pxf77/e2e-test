"""Backward-compatible module alias for agent4 execution/healing."""
from __future__ import annotations

import sys

from e2e_agent.legacy.agents.agent4_exec import node as _node

sys.modules[__name__] = _node

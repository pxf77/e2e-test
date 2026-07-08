"""Backward-compatible module alias for agent1 test-case merge."""
from __future__ import annotations

import sys

from e2e_agent.agents.agent1_tc_merge import node as _node

sys.modules[__name__] = _node

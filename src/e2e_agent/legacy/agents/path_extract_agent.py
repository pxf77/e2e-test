"""Backward-compatible module alias for agent2 path extraction."""
from __future__ import annotations

import sys

from e2e_agent.legacy.agents.agent2_path_extract import node as _node

sys.modules[__name__] = _node

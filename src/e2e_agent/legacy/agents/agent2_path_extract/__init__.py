"""Agent2: regression path extraction compatibility package.

The legacy node historically loaded ``config/state-deps.yaml``. Insurance
state governance now has a single source under ``domains/insurance``. The
wrapper keeps the legacy node import path stable while redirecting its default
loader; v2 Domain Pack adapters may still temporarily override the loader for
other domains.
"""
from __future__ import annotations

from pathlib import Path

from e2e_agent.legacy.agents.agent2_path_extract import node as _node


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").exists():
            return parent
    return Path(__file__).resolve().parents[5]


_INSURANCE_STATE_DEPS = _repo_root() / "domains" / "insurance" / "state-deps.yaml"
_original_state_deps_loader = _node._load_state_deps_config


def _load_insurance_state_deps(path: Path | None = None):
    return _original_state_deps_loader(path or _INSURANCE_STATE_DEPS)


_node._load_state_deps_config = _load_insurance_state_deps
path_extract_node = _node.path_extract_node

__all__ = ["path_extract_node"]

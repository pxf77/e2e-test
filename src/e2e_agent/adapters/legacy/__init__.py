"""Compatibility adapters for the v1 four-agent pipeline."""

from .workflow import build_legacy_state, register_legacy_nodes

__all__ = ["build_legacy_state", "register_legacy_nodes"]

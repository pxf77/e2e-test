"""General plugin discovery and runtime primitives."""

from .loader import PluginManifest, PluginManifestLoader
from .manager import PluginManager
from .runtime import PluginResult, PluginRuntime

__all__ = [
    "PluginManifest",
    "PluginManifestLoader",
    "PluginManager",
    "PluginResult",
    "PluginRuntime",
]

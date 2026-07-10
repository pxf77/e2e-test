from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from .yaml_loader import load_yaml_file


class ConfigResolver:
    """Resolve framework configuration using explicit precedence.

    Precedence, low to high: defaults < domain < app < environment < runtime.
    """

    def resolve(
        self,
        *,
        defaults: dict[str, Any] | None = None,
        domain: dict[str, Any] | None = None,
        app: dict[str, Any] | None = None,
        app_root: Path | None = None,
        env: str = "local",
        runtime: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for layer in (
            defaults or {},
            (domain or {}).get("config") or {},
            (app or {}).get("config") or {},
            self._load_environment(app or {}, app_root or Path.cwd(), env),
            runtime or {},
        ):
            result = _deep_merge(result, layer)
        result["environment"] = env
        return result

    @staticmethod
    def _load_environment(app: dict[str, Any], app_root: Path, env: str) -> dict[str, Any]:
        environments = ((app.get("execution") or {}).get("environments") or {})
        value = environments.get(env)
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            path = Path(value)
            resolved = path if path.is_absolute() else app_root / path
            if not resolved.exists():
                return {"environment_file": str(resolved), "environment_file_missing": True}
            payload = load_yaml_file(resolved)
            payload.setdefault("environment_file", str(resolved))
            return payload
        return {}


def _deep_merge(base: Any, override: Any) -> Any:
    if isinstance(base, dict) and isinstance(override, dict):
        result = deepcopy(base)
        for key, value in override.items():
            result[key] = _deep_merge(result[key], value) if key in result else deepcopy(value)
        return result
    if isinstance(base, list) and isinstance(override, list):
        return [*deepcopy(base), *deepcopy(override)]
    return deepcopy(override)

from __future__ import annotations

from pathlib import Path
from typing import Any

from e2e_agent.config.yaml_loader import load_yaml_file
from e2e_agent.data import DataResolver

from .registry import NodeResult
from .state import WorkflowRuntimeState


def _app_root(state: WorkflowRuntimeState) -> Path:
    return Path(str((state.get("metadata") or {}).get("app_root") or "."))


def _repo_root(state: WorkflowRuntimeState) -> Path:
    return Path(str((state.get("metadata") or {}).get("repo_root") or "."))


def _app_path(state: WorkflowRuntimeState, value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else _app_root(state) / path


def prepare_data_node(state: WorkflowRuntimeState, node_spec: dict[str, Any]) -> NodeResult:
    app = state.get("app") or {}
    domain = state.get("domain") or {}
    inputs = state.get("inputs") or {}
    configured = inputs.get("data_profiles") or (app.get("data") or {}).get("profiles") or []
    profile_names = [str(item) for item in configured]
    if not profile_names:
        return NodeResult(
            outputs={"test_data": {}, "data_metadata": {"profiles": {}, "selected_profiles": []}},
            state_updates={"runtime_data": {"test_data": {}}},
            metrics={"profile_count": 0},
        )

    resolver = DataResolver()
    actual: dict[str, Any] = {}
    public: dict[str, Any] = {}
    metadata: dict[str, Any] = {"profiles": {}, "selected_profiles": profile_names}
    warnings: list[str] = []

    app_pack: dict[str, Any] = {}
    app_pack_ref = (app.get("data") or {}).get("pack")
    app_pack_path = _app_path(state, str(app_pack_ref) if app_pack_ref else None)
    if app_pack_path and app_pack_path.exists():
        app_pack = load_yaml_file(app_pack_path)
    elif app_pack_path:
        warnings.append(f"App data pack not found: {app_pack_path}")

    domain_pack = domain.get("data_pack") or {}
    domain_root = _repo_root(state) / "domains" / str(state.get("domain_id") or "")
    app_profiles = app_pack.get("profiles") or {}
    domain_profiles = domain_pack.get("profiles") or {}
    for profile_name in profile_names:
        if profile_name in app_profiles:
            base_dir = app_pack_path.parent if app_pack_path else _app_root(state)
            resolution = resolver.resolve(app_pack, [profile_name], base_dir=base_dir)
        elif profile_name in domain_profiles:
            resolution = resolver.resolve(domain_pack, [profile_name], base_dir=domain_root)
        else:
            raise KeyError(f"Unknown data profile: {profile_name}")
        actual.update(resolution.actual)
        public.update(resolution.public)
        metadata["profiles"].update(resolution.metadata.get("profiles") or {})

    return NodeResult(
        outputs={"test_data": public, "data_metadata": metadata},
        state_updates={"runtime_data": {"test_data": actual}},
        warnings=warnings,
        metrics={"profile_count": len(profile_names)},
    )

"""Static product automation package loader for Agent3 static-first mode."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class StaticProductPackage:
    root_dir: Path
    product_id: str
    automation_dir: Path
    config: dict[str, Any]
    page_models: list[dict[str, Any]]
    flows: list[dict[str, Any]]
    scenario_map: dict[str, Any]
    test_data_profiles: dict[str, dict[str, Any]]

    @property
    def agent3_mode(self) -> str:
        return str(self.config.get("agent3_mode") or "probe-first")

    @property
    def entry_url(self) -> str | None:
        value = self.config.get("entry_url")
        return str(value) if value else None

    def page_model_by_node(self) -> dict[str, dict[str, Any]]:
        return {
            str(item.get("node_id")): item
            for item in self.page_models
            if item.get("node_id")
        }

    def flow_by_id(self) -> dict[str, dict[str, Any]]:
        return {
            str(item.get("flow_id")): item
            for item in self.flows
            if item.get("flow_id")
        }

    def binding_by_path_id(self) -> dict[str, dict[str, Any]]:
        return {
            str(item.get("path_id")): item
            for item in self.scenario_map.get("path_bindings", []) or []
            if item.get("path_id")
        }


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _read_json_files(directory: Path, pattern: str) -> list[dict[str, Any]]:
    if not directory.exists():
        return []
    return [
        _read_json(path)
        for path in sorted(directory.glob(pattern))
        if path.is_file()
    ]


def _read_test_data_profiles(directory: Path) -> dict[str, dict[str, Any]]:
    if not directory.exists():
        return {}

    profiles: dict[str, dict[str, Any]] = {}
    for path in sorted(directory.glob("*.json")):
        if not path.is_file():
            continue
        profile = _read_json(path)
        profile_id = str(profile.get("profile_id") or path.stem)
        profiles[profile_id] = profile
    return profiles


def _page_models_from_element_set(element_set: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not element_set:
        return []
    page_models = element_set.get("page_models")
    if isinstance(page_models, dict):
        return [
            item
            for item in page_models.values()
            if isinstance(item, dict)
        ]
    if isinstance(page_models, list):
        return [
            item
            for item in page_models
            if isinstance(item, dict)
        ]
    return []


def _default_product_source_dir(root_dir: Path, product_id: str) -> Path:
    product_root = root_dir / "products" / product_id
    legacy_source_dir = product_root
    if (legacy_source_dir / "automation").exists():
        return legacy_source_dir
    if not product_root.exists():
        return legacy_source_dir

    candidates = [
        item
        for item in sorted(product_root.iterdir(), key=lambda path: path.name)
        if item.is_dir()
        and not item.name.endswith(".assets")
        and (item / "product-input.json").exists()
        and (item / "automation").exists()
    ]
    return candidates[0] if candidates else legacy_source_dir


def load_static_product_package(
    root_dir: Path,
    product_id: str,
    *,
    product_source_dir: str | Path | None = None,
) -> StaticProductPackage:
    """Load the static Agent3 input package from the product source directory."""
    source_dir = Path(product_source_dir) if product_source_dir else _default_product_source_dir(root_dir, product_id)
    if not source_dir.is_absolute():
        source_dir = root_dir / source_dir
    automation_dir = source_dir / "automation"
    config_path = automation_dir / "product.config.json"
    element_set_path = automation_dir / "element-set.json"
    element_set = _read_json(element_set_path) if element_set_path.exists() else None

    if config_path.exists():
        config = _read_json(config_path)
    elif element_set:
        config = dict(element_set.get("product_config") or {})
        config.setdefault("product_id", product_id)
    else:
        raise FileNotFoundError(f"Static product package config not found: {config_path}")

    scenario_map_path = automation_dir / "scenarios" / "scenario-map.json"
    page_models = _read_json_files(automation_dir / "page-models", "*.json")
    if not page_models:
        page_models = _page_models_from_element_set(element_set)

    return StaticProductPackage(
        root_dir=root_dir,
        product_id=product_id,
        automation_dir=automation_dir,
        config=config,
        page_models=page_models,
        flows=_read_json_files(automation_dir / "flows", "*.flow.json"),
        scenario_map=_read_json(scenario_map_path) if scenario_map_path.exists() else {"path_bindings": []},
        test_data_profiles=_read_test_data_profiles(automation_dir / "test-data"),
    )

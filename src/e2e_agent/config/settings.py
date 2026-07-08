"""Application settings loader."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]


@lru_cache(maxsize=1)
def load_model_routing(config_path: str | None = None) -> dict:
    path = Path(config_path) if config_path else _REPO_ROOT / "config" / "model-routing.yaml"
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


@lru_cache(maxsize=1)
def load_playwright_config(config_path: str | None = None) -> dict:
    path = Path(config_path) if config_path else _REPO_ROOT / "config" / "playwright-config.yaml"
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)

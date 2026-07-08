"""Product artifact path helpers."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


_AGENT_DIRS = {
    "agent1": "agent1",
    "agent2": "agent2",
    "agent3": "agent3",
    "agent4": "agent4",
    "tc_merge": "agent1",
    "tc_merge_agent": "agent1",
    "path_extract": "agent2",
    "path_extract_agent": "agent2",
    "explore": "agent3",
    "explore_agent": "agent3",
    "exec_healing": "agent4",
    "exec_healing_agent": "agent4",
    "exec_agent": "agent4",
}


def agent_dir_name(agent_name: str) -> str:
    """Return the stable product subdirectory for an agent."""
    return _AGENT_DIRS.get(agent_name, agent_name)


def _as_path(value: str | Path | None) -> Path | None:
    if value is None:
        return None
    raw = str(value).strip()
    return Path(raw) if raw else None


def _resolve_optional_dir(root_dir: Path, value: str | Path | None) -> Path | None:
    candidate = _as_path(value)
    if candidate is None:
        return None
    return candidate if candidate.is_absolute() else root_dir / candidate


def _is_product_artifact_dir(path: Path) -> bool:
    return path.is_dir() and path.name.endswith(".assets")


def _artifact_dir_for_source_package(root_dir: Path, product_id: str, source_dir: Path) -> Path | None:
    if not source_dir.is_dir() or source_dir.name.endswith(".assets"):
        return None
    if not (source_dir / "product-input.json").exists():
        return None
    product_root = (root_dir / "products" / product_id).resolve()
    try:
        resolved_source = source_dir.resolve()
        resolved_source.relative_to(product_root)
    except (OSError, ValueError):
        return None
    if resolved_source == product_root:
        return None
    return source_dir.with_name(f"{source_dir.name}.assets")


def _asset_dir_from_source_path(root_dir: Path, product_id: str, source_path: str | Path | None) -> Path | None:
    source = _resolve_optional_dir(root_dir, source_path)
    if source is None:
        return None
    try:
        source = source.resolve()
    except OSError:
        source = source.absolute()
    product_root = (root_dir / "products" / product_id).resolve()
    candidates = [source] if source.is_dir() else list(source.parents)
    for candidate in candidates:
        if _is_product_artifact_dir(candidate):
            try:
                candidate.relative_to(product_root)
            except ValueError:
                continue
            return candidate
        asset_dir = _artifact_dir_for_source_package(root_dir, product_id, candidate)
        if asset_dir is not None:
            return asset_dir
    return None


def _single_asset_dir(product_root: Path) -> Path | None:
    if not product_root.exists():
        return None
    asset_dirs = [item for item in product_root.iterdir() if _is_product_artifact_dir(item)]
    return asset_dirs[0] if len(asset_dirs) == 1 else None


def product_artifact_dir(
    root_dir: Path,
    product_id: str,
    *,
    product_dir: str | Path | None = None,
    source_paths: list[str | Path | None] | tuple[str | Path | None, ...] | None = None,
) -> Path:
    explicit_dir = _resolve_optional_dir(root_dir, product_dir)
    if explicit_dir is not None:
        return explicit_dir

    for source_path in source_paths or ():
        asset_dir = _asset_dir_from_source_path(root_dir, product_id, source_path)
        if asset_dir is not None:
            return asset_dir

    product_root = root_dir / "products" / product_id
    return _single_asset_dir(product_root) or product_root


def product_artifact_path(
    root_dir: Path,
    product_id: str,
    *,
    product_dir: str | Path | None = None,
    source_paths: list[str | Path | None] | tuple[str | Path | None, ...] | None = None,
) -> str:
    output_dir = product_artifact_dir(
        root_dir,
        product_id,
        product_dir=product_dir,
        source_paths=source_paths,
    )
    try:
        return output_dir.resolve().relative_to(root_dir.resolve()).as_posix()
    except ValueError:
        return output_dir.as_posix()


def agent_artifact_dir(
    root_dir: Path,
    product_id: str,
    agent_name: str,
    *,
    product_dir: str | Path | None = None,
    source_paths: list[str | Path | None] | tuple[str | Path | None, ...] | None = None,
) -> Path:
    return (
        product_artifact_dir(
            root_dir,
            product_id,
            product_dir=product_dir,
            source_paths=source_paths,
        )
        / agent_dir_name(agent_name)
    )


def agent_artifact_path(
    product_id: str,
    agent_name: str,
    *parts: str,
    root_dir: Path | None = None,
    product_dir: str | Path | None = None,
    source_paths: list[str | Path | None] | tuple[str | Path | None, ...] | None = None,
) -> str:
    if root_dir is None:
        root_dir = Path.cwd()
    return "/".join(
        (
            product_artifact_path(
                root_dir,
                product_id,
                product_dir=product_dir,
                source_paths=source_paths,
            ),
            agent_dir_name(agent_name),
            *parts,
        )
    )


def write_agent_json_artifact(
    *,
    root_dir: Path,
    product_id: str,
    agent_name: str,
    relative_path: str,
    payload: Any,
    product_dir: str | Path | None = None,
    source_paths: list[str | Path | None] | tuple[str | Path | None, ...] | None = None,
) -> Path:
    output_path = (
        agent_artifact_dir(
            root_dir,
            product_id,
            agent_name,
            product_dir=product_dir,
            source_paths=source_paths,
        )
        / relative_path
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return output_path


def write_agent_text_artifact(
    *,
    root_dir: Path,
    product_id: str,
    agent_name: str,
    relative_path: str,
    text: str,
    product_dir: str | Path | None = None,
    source_paths: list[str | Path | None] | tuple[str | Path | None, ...] | None = None,
) -> Path:
    output_path = (
        agent_artifact_dir(
            root_dir,
            product_id,
            agent_name,
            product_dir=product_dir,
            source_paths=source_paths,
        )
        / relative_path
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text, encoding="utf-8")
    return output_path

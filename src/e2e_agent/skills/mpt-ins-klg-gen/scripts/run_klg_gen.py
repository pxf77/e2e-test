from __future__ import annotations

import json
import sys
import asyncio
from pathlib import Path

REPO_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "pyproject.toml").exists())
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from e2e_agent.core.knowledge_generation import build_knowledge_artifacts, materialise_knowledge_artifacts
from e2e_agent.core.knowledge_page_probe import probe_entry_page


async def _maybe_probe_entry_page(payload: dict, root_dir: Path, product_id: str) -> dict:
    if isinstance(payload.get("page_probe"), dict):
        return dict(payload)
    exploration_mode = str(payload.get("exploration_mode") or "materials-only").strip()
    entry_url = str(payload.get("entry_url") or "").strip()
    if exploration_mode != "page-probe" or not entry_url:
        return dict(payload)

    next_payload = dict(payload)
    try:
        page_probe = await probe_entry_page(
            entry_url,
            screenshot_dir=root_dir / "knowledge" / product_id / "mcp" / "screenshots",
            headless=bool(payload.get("headless", True)),
            viewport=payload.get("viewport") if isinstance(payload.get("viewport"), dict) else None,
        )
    except Exception as exc:
        if payload.get("page_probe_fail_fast"):
            raise
        next_payload["page_probe_error"] = str(exc)
    else:
        next_payload["page_probe"] = page_probe
    return next_payload


def main() -> int:
    payload = json.loads(sys.stdin.read() or "{}")
    product_id = str(payload.get("product_id") or "product").strip() or "product"
    root_dir = Path(str(payload.get("root_dir") or REPO_ROOT))
    payload = asyncio.run(_maybe_probe_entry_page(payload, root_dir, product_id))
    artifacts = build_knowledge_artifacts(payload)

    artifact_paths: dict[str, str] = {}
    if payload.get("materialise", True):
        artifact_paths = materialise_knowledge_artifacts(root_dir, product_id, artifacts)

    result = {
        "product_id": product_id,
        "product_name": artifacts["knowledge_base"]["product_name"],
        "knowledge_root": str(root_dir / "knowledge" / product_id),
        "knowledge_base": artifacts["knowledge_base"],
        "ui_ontology": artifacts["ui_ontology"],
        "field_catalog": artifacts["field_catalog"],
        "workflow_cases": artifacts["workflow_cases"],
        "page_probe": artifacts["page_probe"],
        "artifacts": artifact_paths,
        "warnings": artifacts["warnings"],
        "exploration": artifacts["exploration"],
    }
    json.dump(result, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

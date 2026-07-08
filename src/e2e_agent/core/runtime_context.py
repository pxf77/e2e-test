"""Runtime context, account lease, session reuse, and teardown helpers."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from e2e_agent.artifacts.paths import agent_artifact_dir
from e2e_agent.core.script_generation import platform_from_entry_url


def _runtime_dir(root_dir: Path, product_id: str, product_dir: str | Path | None = None) -> Path:
    return agent_artifact_dir(root_dir, product_id, "agent3", product_dir=product_dir) / "runtime"


def _load_account_pool(root_dir: Path) -> list[dict[str, Any]]:
    path = root_dir / "config" / "account-pool.json"
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return list(payload.get("accounts", []) or [])


def lease_account(root_dir: Path, product_id: str, entry_url: str | None) -> dict[str, Any]:
    platform = platform_from_entry_url(entry_url)
    for account in _load_account_pool(root_dir):
        products = list(account.get("products", []) or [])
        platforms = list(account.get("platforms", []) or [])
        if products and product_id not in products:
            continue
        if platforms and platform not in platforms:
            continue
        return {
            "account_id": str(account.get("account_id") or account.get("username") or "shared-account"),
            "source": "account-pool",
            "platform": platform,
        }
    return {
        "account_id": f"default-{platform}",
        "source": "default",
        "platform": platform,
    }


def prepare_runtime_context(
    *,
    root_dir: Path,
    product_id: str,
    run_id: str,
    entry_url: str | None,
    product_dir: str | Path | None = None,
) -> dict[str, Any]:
    runtime_dir = _runtime_dir(root_dir, product_id, product_dir)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    platform = platform_from_entry_url(entry_url)
    storage_state_path = runtime_dir / f"{platform}-storage-state.json"
    context = {
        "run_id": run_id,
        "platform": platform,
        "session_key": f"{product_id}:{platform}",
        "reuse_source": "playwright.storage_state",
        "session_reused": storage_state_path.exists(),
        "storage_state_path": str(storage_state_path),
        "account": lease_account(root_dir, product_id, entry_url),
        "teardown_plan": {
            "actions": ["release_account", "cleanup_storage_state"],
            "output_path": str(runtime_dir / "teardown-report.json"),
        },
    }
    (runtime_dir / "runtime-context.json").write_text(
        json.dumps(context, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return context


def finalize_runtime_context(
    *,
    root_dir: Path,
    product_id: str,
    runtime_context: dict[str, Any] | None,
    reason: str,
    product_dir: str | Path | None = None,
) -> dict[str, Any]:
    runtime_dir = _runtime_dir(root_dir, product_id, product_dir)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    removed_paths: list[str] = []
    storage_state_raw = str((runtime_context or {}).get("storage_state_path") or "").strip()
    storage_state_path = Path(storage_state_raw) if storage_state_raw else None
    if storage_state_path and storage_state_path.is_file():
        storage_state_path.unlink()
        removed_paths.append(str(storage_state_path))
    report = {
        "session_key": (runtime_context or {}).get("session_key"),
        "account_id": ((runtime_context or {}).get("account") or {}).get("account_id"),
        "reason": reason,
        "removed_paths": removed_paths,
        "released": True,
    }
    (runtime_dir / "teardown-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report

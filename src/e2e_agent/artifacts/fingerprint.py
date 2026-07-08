"""Artifact fingerprint append helpers."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from e2e_agent.artifacts.paths import product_artifact_dir


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _serialise_payload(payload: Any) -> str:
    try:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except TypeError:
        return json.dumps(str(payload), ensure_ascii=False)


def _content_hash(payload: Any) -> str:
    return hashlib.sha256(_serialise_payload(payload).encode("utf-8")).hexdigest()


def _fingerprint_id(run_id: str, artifact_type: str, artifact_path: str) -> str:
    seed = f"{run_id}:{artifact_type}:{artifact_path}"
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]
    return f"AFP-{digest}"


def append_artifact_fingerprint(
    *,
    root_dir: Path,
    product_id: str,
    run_id: str,
    artifact_path: str,
    artifact_type: str,
    payload: Any,
    producer: str,
    model_routed: str = "deterministic-skill",
    model_primary: str | None = None,
    is_fallback: bool = False,
    fallback_chain: list[str] | None = None,
    token_usage: dict[str, Any] | None = None,
    cost_usd: float | None = None,
    product_dir: str | Path | None = None,
    source_paths: list[str | Path | None] | tuple[str | Path | None, ...] | None = None,
) -> dict[str, Any]:
    output_dir = product_artifact_dir(
        root_dir,
        product_id,
        product_dir=product_dir,
        source_paths=source_paths,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "fingerprint_id": _fingerprint_id(run_id, artifact_type, artifact_path),
        "run_id": run_id,
        "artifact_path": artifact_path,
        "artifact_type": artifact_type,
        "producer": producer,
        "model_routed": model_routed,
        "model_primary": model_primary,
        "is_fallback": is_fallback,
        "fallback_chain": fallback_chain,
        "token_usage": token_usage,
        "cost_usd": cost_usd,
        "timestamp": _utc_now(),
        "content_hash": _content_hash(payload),
    }
    output_path = output_dir / "artifact-fingerprints.jsonl"
    with output_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record

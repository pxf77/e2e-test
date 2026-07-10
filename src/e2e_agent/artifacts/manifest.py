from __future__ import annotations

import hashlib
import json
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from e2e_agent.contracts import ContractRegistry


_CONTRACT_BY_OUTPUT = {
    "merged_cases": "merged-cases@v1",
    "regression_paths": "regression-paths@v1",
    "page_registry": "page-registry@v1",
    "execution_result": "execution-result@v2",
    "assertion_report": "assertion-report@v2",
    "test_report": "test-report@v1",
    "healing_events": "healing-events@v1",
    "run_context": "run-context@v2",
}


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _safe_segment(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-._")
    return cleaned or "artifact"


def _json_bytes(payload: Any) -> bytes:
    try:
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
    except TypeError:
        text = json.dumps(str(payload), ensure_ascii=False, indent=2)
    return (text + "\n").encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(data)
    temporary.replace(path)


class ArtifactManifestStore:
    """Persists v2 workflow outputs and a machine-readable artifact index."""

    def __init__(
        self,
        run_dir: Path,
        *,
        run_id: str,
        app_id: str,
        domain_id: str,
        workflow_id: str,
        contract_registry: ContractRegistry | None = None,
    ) -> None:
        self.run_dir = run_dir
        self.manifest_path = run_dir / "artifact-manifest.json"
        self.contract_registry = contract_registry
        self.identity = {
            "run_id": run_id,
            "app_id": app_id,
            "domain_id": domain_id,
            "workflow_id": workflow_id,
        }

    @classmethod
    def from_state(
        cls,
        state: dict[str, Any],
        *,
        contract_registry: ContractRegistry | None = None,
    ) -> "ArtifactManifestStore | None":
        metadata = state.get("metadata") or {}
        artifacts_dir = metadata.get("artifacts_dir")
        if not artifacts_dir:
            return None
        return cls(
            Path(str(artifacts_dir)),
            run_id=str(state.get("run_id") or "run-unknown"),
            app_id=str(state.get("app_id") or "app-unknown"),
            domain_id=str(state.get("domain_id") or "domain-unknown"),
            workflow_id=str(state.get("workflow_id") or "workflow-unknown"),
            contract_registry=contract_registry,
        )

    def initialize(self) -> dict[str, Any]:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        if self.manifest_path.exists():
            return self.load()
        manifest = {
            **self.identity,
            "created_at": _utc_now(),
            "updated_at": _utc_now(),
            "status": "running",
            "artifacts": [],
        }
        self._write_manifest(manifest)
        return manifest

    def load(self) -> dict[str, Any]:
        if not self.manifest_path.exists():
            return self.initialize()
        payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"Artifact manifest must be an object: {self.manifest_path}")
        return payload

    def record_outputs(
        self,
        *,
        node_id: str,
        implementation: str,
        outputs: dict[str, Any],
    ) -> dict[str, Any]:
        manifest = self.load()
        entries = {
            str(item.get("id")): dict(item)
            for item in manifest.get("artifacts") or []
            if isinstance(item, dict) and item.get("id")
        }
        for output_name, payload in outputs.items():
            relative_path = Path("by-node") / _safe_segment(node_id) / f"{_safe_segment(output_name)}.json"
            absolute_path = self.run_dir / relative_path
            data = _json_bytes(payload)
            _atomic_write(absolute_path, data)
            artifact_id = f"{node_id}:{output_name}"
            entries[artifact_id] = {
                "id": artifact_id,
                "contract": _CONTRACT_BY_OUTPUT.get(output_name),
                "path": relative_path.as_posix(),
                "sha256": _sha256(data),
                "producer": implementation,
                "node_id": node_id,
                "created_at": _utc_now(),
                "metadata": {"output_name": output_name, "format": "json"},
            }
            if output_name == "execution_result" and isinstance(payload, dict):
                self._record_runner_files(entries, node_id=node_id, implementation=implementation, payload=payload)
        manifest["artifacts"] = sorted(entries.values(), key=lambda item: str(item.get("id")))
        manifest["updated_at"] = _utc_now()
        self._write_manifest(manifest)
        return manifest

    def finalize(self, state: dict[str, Any]) -> dict[str, Any]:
        manifest = self.load()
        context_payload = {
            key: value
            for key, value in state.items()
            if key not in {"artifact_manifest", "runtime_data"}
        }
        data = _json_bytes(context_payload)
        relative_path = Path("run-context.json")
        _atomic_write(self.run_dir / relative_path, data)
        entries = {
            str(item.get("id")): dict(item)
            for item in manifest.get("artifacts") or []
            if isinstance(item, dict) and item.get("id")
        }
        entries["runtime:run_context"] = {
            "id": "runtime:run_context",
            "contract": "run-context@v2",
            "path": relative_path.as_posix(),
            "sha256": _sha256(data),
            "producer": "workflow.runtime",
            "created_at": _utc_now(),
            "metadata": {"format": "json", "runtime_data_persisted": False},
        }
        manifest["artifacts"] = sorted(entries.values(), key=lambda item: str(item.get("id")))
        manifest["updated_at"] = _utc_now()
        manifest["status"] = self._derive_status(state)
        self._write_manifest(manifest)
        return manifest

    def _record_runner_files(
        self,
        entries: dict[str, dict[str, Any]],
        *,
        node_id: str,
        implementation: str,
        payload: dict[str, Any],
    ) -> None:
        for index, item in enumerate(payload.get("artifacts") or [], start=1):
            if not isinstance(item, dict) or not item.get("path"):
                continue
            source = Path(str(item["path"]))
            if not source.exists() or not source.is_file():
                continue
            target_dir = self.run_dir / "runner" / _safe_segment(node_id)
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / _safe_segment(source.name)
            if source.resolve() != target.resolve():
                shutil.copy2(source, target)
            data = target.read_bytes()
            relative = target.relative_to(self.run_dir)
            artifact_id = f"{node_id}:runner:{index}:{target.name}"
            entries[artifact_id] = {
                "id": artifact_id,
                "contract": item.get("contract"),
                "path": relative.as_posix(),
                "sha256": _sha256(data),
                "producer": implementation,
                "node_id": node_id,
                "created_at": _utc_now(),
                "metadata": {
                    "kind": item.get("kind") or "runner-artifact",
                    "source_path": str(source),
                    "size_bytes": len(data),
                },
            }

    def _write_manifest(self, manifest: dict[str, Any]) -> None:
        data = _json_bytes(manifest)
        if self.contract_registry is not None:
            self.contract_registry.validate("artifact-manifest", "v2", manifest)
        _atomic_write(self.manifest_path, data)

    @staticmethod
    def _derive_status(state: dict[str, Any]) -> str:
        gates = state.get("gates") or {}
        if any(str((gate or {}).get("status")) == "pending" for gate in gates.values()):
            return "pending"
        report = (state.get("artifacts") or {}).get("test_report") or {}
        report_status = str(report.get("status") or "")
        if report_status:
            return report_status
        return "completed" if not state.get("errors") else "completed_with_warnings"


def collect_files(
    roots: Iterable[Path],
    *,
    suffixes: set[str] | None = None,
    max_size_bytes: int = 50 * 1024 * 1024,
) -> list[dict[str, Any]]:
    """Return deterministic metadata for runner files without changing them."""
    allowed = suffixes or {".zip", ".webm", ".png", ".jpg", ".jpeg", ".html", ".json", ".xml", ".txt"}
    found: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for root in roots:
        if not root.exists():
            continue
        candidates = [root] if root.is_file() else sorted(path for path in root.rglob("*") if path.is_file())
        for path in candidates:
            resolved = path.resolve()
            if resolved in seen or path.suffix.lower() not in allowed:
                continue
            size = path.stat().st_size
            if size > max_size_bytes:
                continue
            seen.add(resolved)
            found.append({"path": str(path), "kind": _runner_kind(path), "size_bytes": size})
    return found


def _runner_kind(path: Path) -> str:
    name = path.name.lower()
    if name == "trace.zip" or "trace" in name:
        return "trace"
    if path.suffix.lower() == ".webm":
        return "video"
    if path.suffix.lower() in {".png", ".jpg", ".jpeg"}:
        return "screenshot"
    if path.suffix.lower() == ".html":
        return "html-report"
    return "runner-artifact"

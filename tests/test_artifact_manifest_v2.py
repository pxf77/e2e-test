from __future__ import annotations

import json
from pathlib import Path

from e2e_agent.artifacts import ArtifactManifestStore, collect_files
from e2e_agent.contracts import ContractRegistry

ROOT = Path(__file__).resolve().parents[1]


def _state(run_dir: Path) -> dict:
    return {
        "run_id": "manifest-test",
        "app_id": "demo-app",
        "domain_id": "generic-web",
        "workflow_id": "smoke-static-web",
        "env": "local",
        "inputs": {},
        "artifacts": {"page_registry": {"pages": []}},
        "gates": {},
        "metadata": {"artifacts_dir": str(run_dir)},
        "errors": [],
        "legacy_state": {},
        "node_trace": [],
    }


def test_manifest_persists_node_outputs_and_run_context(tmp_path: Path) -> None:
    registry = ContractRegistry(ROOT / "schemas").discover()
    state = _state(tmp_path / "run")
    store = ArtifactManifestStore.from_state(state, contract_registry=registry)

    assert store is not None
    store.initialize()
    manifest = store.record_outputs(
        node_id="explore",
        implementation="builtin.explore_static",
        outputs={"page_registry": {"pages": [], "entry_url": "https://example.com/"}},
    )
    state["artifact_manifest"] = manifest
    final_manifest = store.finalize(state)

    registry.validate("artifact-manifest", "v2", final_manifest)
    entries = {item["id"]: item for item in final_manifest["artifacts"]}
    assert entries["explore:page_registry"]["contract"] == "page-registry@v1"
    assert (store.run_dir / entries["explore:page_registry"]["path"]).exists()
    assert entries["runtime:run_context"]["contract"] == "run-context@v2"
    assert final_manifest["status"] == "completed"


def test_manifest_copies_playwright_artifacts(tmp_path: Path) -> None:
    registry = ContractRegistry(ROOT / "schemas").discover()
    state = _state(tmp_path / "run")
    trace = tmp_path / "raw" / "trace.zip"
    trace.parent.mkdir(parents=True)
    trace.write_bytes(b"trace-data")
    store = ArtifactManifestStore.from_state(state, contract_registry=registry)

    assert store is not None
    store.initialize()
    manifest = store.record_outputs(
        node_id="execute",
        implementation="runner.playwright",
        outputs={
            "execution_result": {
                "run_id": "manifest-test-execute",
                "runner": "playwright",
                "status": "failed",
                "summary": {"passed": 0, "failed": 1, "skipped": 0},
                "artifacts": [{"path": str(trace), "kind": "trace"}],
            }
        },
    )

    runner_entries = [item for item in manifest["artifacts"] if item["id"].startswith("execute:runner:")]
    assert len(runner_entries) == 1
    copied = store.run_dir / runner_entries[0]["path"]
    assert copied.read_bytes() == b"trace-data"
    assert runner_entries[0]["metadata"]["kind"] == "trace"


def test_collect_files_classifies_playwright_outputs(tmp_path: Path) -> None:
    (tmp_path / "trace.zip").write_bytes(b"zip")
    (tmp_path / "video.webm").write_bytes(b"video")
    (tmp_path / "report.html").write_text("<html></html>", encoding="utf-8")

    found = collect_files([tmp_path])
    kinds = {Path(item["path"]).name: item["kind"] for item in found}

    assert kinds == {
        "report.html": "html-report",
        "trace.zip": "trace",
        "video.webm": "video",
    }

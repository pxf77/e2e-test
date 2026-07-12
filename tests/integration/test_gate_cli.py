from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import e2e_agent.cli as cli_module


def _write_checkpoint(tmp_path: Path, run_id: str) -> Path:
    checkpoint_path = tmp_path / f"{run_id}.json"
    checkpoint_path.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "pending_gate": "r1",
                "updated_at": "",
                "state": {
                    "run_id": run_id,
                    "r1_gate": {"status": "pending", "operator": "", "timestamp": "", "note": ""},
                    "r2_gate": {"status": "pending", "operator": "", "timestamp": "", "note": ""},
                    "r3_gate": {"status": "pending", "operator": "", "timestamp": "", "note": ""},
                    "r4_gate": {"status": "pending", "operator": "", "timestamp": "", "note": ""},
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return checkpoint_path


def test_gate_approve_updates_checkpoint(monkeypatch, tmp_path: Path) -> None:
    run_id = "run-gate-001"
    checkpoint_path = _write_checkpoint(tmp_path, run_id)
    monkeypatch.setattr(cli_module, "get_gate_checkpoint_path", lambda _: checkpoint_path)

    exit_code = cli_module.main(
        ["gate", "approve", run_id, "--gate", "r1", "--operator", "qa", "--note", "looks good"]
    )

    assert exit_code == 0
    payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert payload["state"]["r1_gate"]["status"] == "approved"
    assert payload["state"]["r1_gate"]["operator"] == "qa"


def test_gate_resume_runs_graph_and_writes_state(monkeypatch, tmp_path: Path) -> None:
    run_id = "run-gate-002"
    checkpoint_path = _write_checkpoint(tmp_path, run_id)
    monkeypatch.setattr(cli_module, "get_gate_checkpoint_path", lambda _: checkpoint_path)

    class FakeApp:
        async def ainvoke(self, state: dict, config: dict | None = None) -> dict:
            state["r2_gate"] = {
                "status": "approved",
                "operator": "resume",
                "timestamp": "",
                "note": "resumed",
            }
            return state

    monkeypatch.setattr(cli_module, "build_graph", lambda _: FakeApp())

    exit_code = cli_module.main(["gate", "resume", run_id])

    assert exit_code == 0
    payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert payload["state"]["r2_gate"]["status"] == "approved"
    assert payload["last_resume_thread_id"] == run_id


def test_gate_status_prints_pending_gate_summary(monkeypatch, tmp_path: Path, capsys) -> None:
    run_id = "run-gate-003"
    checkpoint_path = _write_checkpoint(tmp_path, run_id)
    monkeypatch.setattr(cli_module, "get_gate_checkpoint_path", lambda _: checkpoint_path)

    exit_code = cli_module.main(["gate", "status", run_id])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["run_id"] == run_id
    assert payload["pending_gate"] == "r1"
    assert payload["gates"]["r1"]["status"] == "pending"


def test_gate_summary_counts_gate_statuses(monkeypatch, tmp_path: Path, capsys) -> None:
    run_id = "run-gate-004"
    checkpoint_path = _write_checkpoint(tmp_path, run_id)
    payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    payload["state"]["r1_gate"]["status"] = "approved"
    payload["state"]["r2_gate"]["status"] = "rejected"
    checkpoint_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(cli_module, "get_gate_checkpoint_path", lambda _: checkpoint_path)

    exit_code = cli_module.main(["gate", "summary", run_id])

    assert exit_code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["counts"] == {"approved": 1, "pending": 2, "rejected": 1}
    assert summary["blocking_pending_gates"] == ["r3"]


def test_products_command_lists_product_inputs(monkeypatch, tmp_path: Path, capsys) -> None:
    product_input = tmp_path / "products" / "demo-product" / "case-a" / "product-input.json"
    product_input.parent.mkdir(parents=True)
    product_input.write_text(
        json.dumps({"product_id": "demo-product", "product_name": "Demo Product"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(cli_module, "_REPO_ROOT", tmp_path)

    exit_code = cli_module.main(["products", "--json"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == [
        {
            "product_id": "demo-product",
            "product_name": "Demo Product",
            "path": "products/demo-product/case-a/product-input.json",
        }
    ]


def test_run_command_delegates_to_full_workflow(monkeypatch, tmp_path: Path) -> None:
    product_input = tmp_path / "products" / "demo" / "product-input.json"
    product_input.parent.mkdir(parents=True)
    product_input.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(cli_module, "_REPO_ROOT", tmp_path)
    calls: list[dict] = []

    def fake_run(command: list[str], cwd: Path, env: dict[str, str], check: bool) -> subprocess.CompletedProcess:
        calls.append({"command": command, "cwd": cwd, "env": env, "check": check})
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(cli_module.subprocess, "run", fake_run)

    exit_code = cli_module.main(["run", "--product-input", str(product_input)])

    assert exit_code == 0
    assert calls[0]["command"] == [
        os.sys.executable,
        str(tmp_path / "tools" / "run_full_workflow.py"),
        "--product-input",
        str(product_input),
    ]
    assert calls[0]["cwd"] == tmp_path
    assert calls[0]["env"]["AGENT3_HEADLESS"] == "1"
    assert calls[0]["env"]["E2E_AGENT_GATE_CHECKPOINT_DIR"] == str(
        tmp_path / ".local" / "e2e-agent" / "gate-checkpoints"
    )
    assert (tmp_path / ".local" / "e2e-agent" / "gate-checkpoints").is_dir()
    assert (tmp_path / ".local" / "e2e-agent" / "logs").is_dir()


def test_doctor_command_prints_environment_status(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.setattr(cli_module, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(cli_module.shutil, "which", lambda name: f"C:/tools/{name}.exe")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    exit_code = cli_module.main(["doctor", "--json"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["repo_root"] == str(tmp_path)
    assert payload["checks"]["python"]["ok"] is (os.sys.version_info >= (3, 12))
    assert payload["checks"]["node"]["ok"] is True
    assert payload["checks"]["npm"]["ok"] is True
    assert payload["model_keys"]["OPENAI_API_KEY"] is True
    assert "sk-test" not in json.dumps(payload)


def test_reports_serve_command_delegates_to_static_server(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli_module, "_REPO_ROOT", tmp_path)
    calls: list[dict] = []

    def fake_serve(root: Path, host: str, port: int) -> None:
        calls.append({"root": root, "host": host, "port": port})

    monkeypatch.setattr(cli_module, "_serve_static_reports", fake_serve)

    exit_code = cli_module.main(["reports", "serve", "--host", "0.0.0.0", "--port", "9001"])

    assert exit_code == 0
    assert calls == [{"root": tmp_path / "products", "host": "0.0.0.0", "port": 9001}]

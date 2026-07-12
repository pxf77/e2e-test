from __future__ import annotations

import importlib
import json
from typing import Any

from e2e_agent import cli_entry
from e2e_agent.commands import main as exported_main

command_module = importlib.import_module("e2e_agent.commands.main")
command_main = command_module.main


def test_root_help_lists_canonical_commands(capsys: Any) -> None:
    assert command_main(["--help"]) == 0
    output = capsys.readouterr().out

    assert "run" in output
    assert "gate" in output
    assert "plugins" in output
    assert "acceptance" in output


def test_cli_entry_is_compatibility_wrapper() -> None:
    assert cli_entry.main is exported_main


def test_legacy_run_is_dispatched_to_legacy_cli(monkeypatch: Any) -> None:
    captured: list[str] = []

    def fake_legacy_main(argv: list[str]) -> int:
        captured.extend(argv)
        return 17

    monkeypatch.setattr(command_module.legacy_cli, "main", fake_legacy_main)

    assert command_main(["run", "--product-input", "legacy.json"]) == 17
    assert captured == ["run", "--product-input", "legacy.json"]


def test_gate_v2_alias_warns_and_uses_v2_dispatch(monkeypatch: Any, capsys: Any) -> None:
    captured: dict[str, Any] = {}

    def fake_v2_gate(args: Any) -> int:
        captured["command"] = args.gate_command
        captured["run_id"] = args.run_id
        return 0

    monkeypatch.setattr(command_module, "_v2_gate", fake_v2_gate)

    assert command_main(["gate-v2", "status", "run-1"]) == 0
    assert captured == {"command": "status", "run_id": "run-1"}
    assert "Deprecated" in capsys.readouterr().err


def test_unified_gate_dispatches_v1_status(monkeypatch: Any, capsys: Any) -> None:
    monkeypatch.setattr(command_module, "_is_v2_checkpoint", lambda run_id, directory: False)
    monkeypatch.setattr(
        command_module.legacy_cli,
        "gate_status",
        lambda run_id: {"run_id": run_id, "pending_gate": "r1"},
    )

    assert command_main(["gate", "status", "legacy-run"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"run_id": "legacy-run", "pending_gate": "r1"}

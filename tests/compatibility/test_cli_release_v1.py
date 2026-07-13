from __future__ import annotations

import json
from pathlib import Path

from e2e_agent.commands.main import main


def test_cli_lists_all_runner_manifests(capsys) -> None:  # type: ignore[no-untyped-def]
    assert main(["runners", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert {item["id"] for item in payload} == {"api", "appium", "playwright"}


def test_plugin_create_scaffolds_python_plugin(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    assert main(["plugin", "create", "generated-test", "--root", str(tmp_path)]) == 0
    payload = json.loads(capsys.readouterr().out)
    plugin_root = Path(payload["path"])

    assert (plugin_root / "plugin.yaml").exists()
    assert (plugin_root / "plugin.py").exists()
    assert "id: generated-test" in (plugin_root / "plugin.yaml").read_text(encoding="utf-8")


def test_plugin_create_rejects_existing_directory(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / "duplicate").mkdir()

    assert main(["plugin", "create", "duplicate", "--root", str(tmp_path)]) == 1
    assert "already exists" in capsys.readouterr().err

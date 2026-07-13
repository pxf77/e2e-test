from __future__ import annotations

import json

from e2e_agent.commands.main import main


def test_cli_default_plugin_list_excludes_examples(capsys) -> None:  # type: ignore[no-untyped-def]
    assert main(["plugins", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload == []


def test_cli_lists_plugins_from_explicit_path(capsys) -> None:  # type: ignore[no-untyped-def]
    assert main(["plugins", "--path", "examples/plugins", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload[0]["id"] == "echo"
    assert payload[0]["implementation"] == "plugin.echo"
    assert "examples/plugins/echo/plugin.yaml" in payload[0]["path"].replace("\\", "/")


def test_cli_lists_data_providers(capsys) -> None:  # type: ignore[no-untyped-def]
    assert main(["data-providers", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert {"static_json", "csv", "faker", "secret_ref", "account_pool", "api_seed", "db_seed"} <= set(payload)

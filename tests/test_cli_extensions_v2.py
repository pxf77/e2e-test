from __future__ import annotations

import json

from e2e_agent.cli_entry import main


def test_cli_lists_plugins(capsys) -> None:  # type: ignore[no-untyped-def]
    assert main(["plugins", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload[0]["id"] == "echo"
    assert payload[0]["implementation"] == "plugin.echo"


def test_cli_lists_data_providers(capsys) -> None:  # type: ignore[no-untyped-def]
    assert main(["data-providers", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert {"static_json", "csv", "faker", "secret_ref", "account_pool", "api_seed", "db_seed"} <= set(payload)

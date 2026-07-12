from __future__ import annotations

import json

from e2e_agent.cli import main


def test_cli_lists_domains(capsys) -> None:  # type: ignore[no-untyped-def]
    assert main(["domains", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert {item["id"] for item in payload} >= {"generic-web", "ecommerce", "insurance"}


def test_cli_validates_demo_app(capsys) -> None:  # type: ignore[no-untyped-def]
    assert main(["validate", "app", "apps/demo-ecommerce/app.yaml", "--workflow", "p0-web-regression"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["status"] == "valid"
    assert payload["app_id"] == "demo-ecommerce"
    assert payload["domain_id"] == "ecommerce"

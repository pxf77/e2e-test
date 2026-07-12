from __future__ import annotations

from e2e_agent.contracts import ContractRegistry


def test_contract_registry_discovers_v2_contracts() -> None:
    registry = ContractRegistry().discover()

    assert registry.has("app", "v2")
    assert registry.has("domain-pack", "v2")
    assert registry.has("workflow", "v2")
    assert registry.has("execution-result", "v2")


def test_contract_registry_validates_app_payload() -> None:
    registry = ContractRegistry().discover()

    registry.validate(
        "app",
        "v2",
        {
            "id": "demo-app",
            "name": "Demo App",
            "domain": "generic-web",
            "entrypoints": {"web": {"base_url": "https://example.com", "start_url": "/"}},
        },
    )

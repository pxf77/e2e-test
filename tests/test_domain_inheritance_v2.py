from __future__ import annotations

from pathlib import Path

import pytest

from e2e_agent.domains import DomainPackLoader

ROOT = Path(__file__).resolve().parents[1]


def test_child_domain_inherits_generic_assets() -> None:
    loader = DomainPackLoader(ROOT / "domains")
    ecommerce = loader.load("ecommerce")

    assert "generic-web" in ecommerce.manifest["resolved_lineage"]
    assert "landing" in ecommerce.ontology["page_types"]
    assert "cart" in ecommerce.ontology["page_types"]
    assert "smoke-static-web" in ecommerce.supported_workflows
    assert "api-contract-regression" in ecommerce.supported_workflows
    assert "visible_element" in ecommerce.assertion_pack["templates"]
    assert "cart_total" in ecommerce.assertion_pack["templates"]
    assert ecommerce.state_machine["id"] == "ecommerce-order"
    assert "created" in ecommerce.state_machine["states"]
    assert "idle" not in ecommerce.state_machine["states"]


def test_saas_domain_is_complete() -> None:
    saas = DomainPackLoader(ROOT / "domains").load("saas")

    assert saas.ontology["flow_chains"]["create_resource"][-1] == "result"
    assert "permission_applied" in saas.assertion_pack["templates"]
    assert "random_resource" in saas.data_pack["profiles"]
    assert "p0-web-regression" in saas.supported_workflows
    assert saas.state_machine["initial_state"] == "draft"


def test_domain_inheritance_cycle_is_rejected(tmp_path: Path) -> None:
    for domain_id, parent in (("a", "b"), ("b", "a")):
        root = tmp_path / domain_id
        root.mkdir()
        (root / "domain.yaml").write_text(
            f'''id: {domain_id}
name: {domain_id}
version: "1.0.0"
extends: [{parent}]
''',
            encoding="utf-8",
        )

    with pytest.raises(ValueError, match="cycle"):
        DomainPackLoader(tmp_path).load("a")

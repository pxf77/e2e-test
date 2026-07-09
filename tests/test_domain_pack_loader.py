from __future__ import annotations

from pathlib import Path

from e2e_agent.domains import DomainPackLoader
from e2e_agent.domains.resolver import DomainResolver

ROOT = Path(__file__).resolve().parents[1]


def test_domain_pack_loader_lists_foundation_domains() -> None:
    loader = DomainPackLoader(ROOT / "domains")

    assert loader.list_domain_ids() == ["ecommerce", "generic-web", "insurance"]


def test_insurance_domain_pack_loads_state_deps() -> None:
    pack = DomainPackLoader(ROOT / "domains").load("insurance")
    resolver = DomainResolver(pack)

    assert "underwritingResult" in resolver.state_keys_for_route("/underwriting/result")
    assert "policyNo" in resolver.state_keys_for_route("/payment/mock")
    assert "insurance" in pack.supported_workflows or "p0-insurance-regression" in pack.supported_workflows


def test_ecommerce_domain_resolves_page_type() -> None:
    pack = DomainPackLoader(ROOT / "domains").load("ecommerce")
    resolver = DomainResolver(pack)

    assert resolver.resolve_page_type("/cart", "Shopping Cart") == "cart"

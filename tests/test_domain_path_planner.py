from __future__ import annotations

from pathlib import Path

from e2e_agent.core.domain_path_planner import (
    build_domain_path_nodes,
    legacy_page_specs,
    resolve_business_intent,
    resolve_page_types,
)
from e2e_agent.domains import DomainPackLoader

ROOT = Path(__file__).resolve().parents[1]


def test_ecommerce_case_uses_flow_chain() -> None:
    ontology = DomainPackLoader(ROOT / "domains").load("ecommerce").ontology
    case = {
        "case_id": "ECOM-001",
        "title": "Add item to cart and verify total",
        "steps": ["Open product detail", "Add product to cart", "Open cart"],
    }

    assert resolve_business_intent(case, ontology) == "add_to_cart"
    assert resolve_page_types(case, ontology) == ["product_listing", "product_detail", "cart"]
    nodes = build_domain_path_nodes(case, ontology)
    assert [node["page_type"] for node in nodes] == ["product_listing", "product_detail", "cart"]
    assert nodes[-1]["url_pattern"] == "/cart*"


def test_insurance_main_flow_is_domain_configuration() -> None:
    ontology = DomainPackLoader(ROOT / "domains").load("insurance").ontology
    case = {
        "case_id": "INS-001",
        "title": "Complete insurance application",
        "business_intent": "main_flow",
    }

    page_types = resolve_page_types(case, ontology)

    assert page_types[0] == "product_detail"
    assert "health_notice" in page_types
    assert "underwriting" in page_types
    assert page_types[-1] == "policy_result"
    assert "payment" in ontology["optional_page_types"]


def test_legacy_page_specs_are_generated_from_ontology() -> None:
    ontology = DomainPackLoader(ROOT / "domains").load("insurance").ontology

    specs = legacy_page_specs(ontology)

    assert specs["underwriting"] == ("Underwriting", "confirm", "/underwriting")
    assert specs["policy_result"] == ("Policy Result", "result", "/result")

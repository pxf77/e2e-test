from __future__ import annotations

from pathlib import Path

import pytest

from e2e_agent.adapters.legacy.workflow import legacy_path_extract_handler
from e2e_agent.domains import DomainPackLoader

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
async def test_legacy_agent2_uses_ecommerce_ontology_and_state_deps() -> None:
    pack = DomainPackLoader(ROOT / "domains").load("ecommerce")
    state = {
        "run_id": "legacy-domain-test",
        "app_id": "demo-ecommerce",
        "domain_id": "ecommerce",
        "workflow_id": "test",
        "env": "local",
        "app": {},
        "domain": {
            "manifest": pack.manifest,
            "ontology": pack.ontology,
            "state_deps": pack.state_deps,
            "assertion_pack": pack.assertion_pack,
            "data_pack": pack.data_pack,
        },
        "inputs": {},
        "artifacts": {},
        "gates": {},
        "metadata": {},
        "errors": [],
        "legacy_state": {
            "product_id": "demo-ecommerce",
            "entry_url": "https://example.com/products/demo",
            "merged_cases": [
                {
                    "case_id": "ECOM-001",
                    "title": "Add item to cart",
                    "business_intent": "add_to_cart",
                    "priority": "P0",
                    "steps": ["Open product", "Add to cart"],
                    "assertions": ["Cart is visible"],
                }
            ],
        },
        "node_trace": [],
    }
    node_spec = {
        "id": "path_extract",
        "outputs": ["regression_flow", "regression_paths", "governance_summary"],
    }

    result = await legacy_path_extract_handler(state, node_spec)

    path = result.outputs["regression_paths"][0]
    assert path["nodes"] == [
        "NODE-start",
        "NODE-product-listing",
        "NODE-product-detail",
        "NODE-cart",
        "NODE-end",
    ]
    assert result.outputs["governance_summary"]["config_version"] == "1.0.0"

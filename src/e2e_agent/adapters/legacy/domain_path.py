from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from typing import Any, AsyncIterator

from e2e_agent.agents.agent2_path_extract import node as agent2_node
from e2e_agent.core.domain_path_planner import legacy_page_specs, legacy_type_guesses


_DOMAIN_OVERRIDE_LOCK = asyncio.Lock()


def build_legacy_domain_overrides(domain: dict[str, Any]) -> dict[str, Any]:
    ontology = domain.get("ontology") or {}
    return {
        "page_specs": legacy_page_specs(ontology),
        "type_guesses": legacy_type_guesses(ontology),
        "intent_chains": {
            str(intent): [str(item) for item in chain]
            for intent, chain in (ontology.get("flow_chains") or {}).items()
            if isinstance(chain, list)
        },
        "optional_page_types": {
            str(item) for item in ontology.get("optional_page_types") or []
        },
        "state_deps": deepcopy(domain.get("state_deps") or {}),
    }


@asynccontextmanager
async def legacy_path_domain_overrides(domain: dict[str, Any]) -> AsyncIterator[None]:
    """Temporarily adapt legacy Agent2 module constants to a Domain Pack.

    Agent2 remains callable by the v1 graph. The v2 legacy adapter serializes
    this override section with a lock, so concurrent workflows cannot leak
    ontology or state-dependency settings into one another.
    """
    overrides = build_legacy_domain_overrides(domain)
    if not any(overrides.values()):
        yield
        return

    async with _DOMAIN_OVERRIDE_LOCK:
        originals = {
            "page_specs": agent2_node._PAGE_SPECS,
            "type_guesses": agent2_node._TYPE_GUESSES,
            "intent_chains": agent2_node._BUSINESS_INTENT_PAGE_CHAINS,
            "optional_page_types": agent2_node._MAX_PATH_OPTIONAL_PAGE_KEYS,
            "state_deps_loader": agent2_node._load_state_deps_config,
        }
        if overrides["page_specs"]:
            agent2_node._PAGE_SPECS = overrides["page_specs"]
        if overrides["type_guesses"]:
            agent2_node._TYPE_GUESSES = overrides["type_guesses"]
        if overrides["intent_chains"]:
            agent2_node._BUSINESS_INTENT_PAGE_CHAINS = overrides["intent_chains"]
        if overrides["optional_page_types"]:
            agent2_node._MAX_PATH_OPTIONAL_PAGE_KEYS = overrides["optional_page_types"]

        original_loader = originals["state_deps_loader"]

        def _domain_state_deps_loader(path=agent2_node._DEFAULT_STATE_DEPS_PATH):  # type: ignore[no-untyped-def]
            if overrides["state_deps"]:
                return deepcopy(overrides["state_deps"])
            return original_loader(path)

        agent2_node._load_state_deps_config = _domain_state_deps_loader
        try:
            yield
        finally:
            agent2_node._PAGE_SPECS = originals["page_specs"]
            agent2_node._TYPE_GUESSES = originals["type_guesses"]
            agent2_node._BUSINESS_INTENT_PAGE_CHAINS = originals["intent_chains"]
            agent2_node._MAX_PATH_OPTIONAL_PAGE_KEYS = originals["optional_page_types"]
            agent2_node._load_state_deps_config = originals["state_deps_loader"]


async def build_domain_regression_artifacts(
    legacy_state: dict[str, Any],
    domain: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], list[str]]:
    async with legacy_path_domain_overrides(domain):
        return agent2_node._build_regression_artifacts(legacy_state)

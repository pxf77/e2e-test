from __future__ import annotations

import re
from typing import Any


def _normalise(value: str) -> str:
    lowered = value.strip().lower()
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", lowered)


def _case_texts(case: dict[str, Any]) -> list[str]:
    texts = [str(case.get("title") or case.get("name") or "")]
    texts.extend(str(item) for item in case.get("steps") or [])
    texts.extend(str(item) for item in case.get("assertions") or [])
    texts.extend(str(item) for item in case.get("tags") or [])
    return [text for text in texts if text.strip()]


def resolve_business_intent(case: dict[str, Any], ontology: dict[str, Any]) -> str | None:
    """Resolve an intent from explicit fields, tags, or ontology keywords.

    Intent precedence is: explicit value > business-intent tag > local domain
    intent > inherited intent. Within the same scope, an intent with an explicit
    flow chain and a more specific keyword wins.
    """
    flow_chains = ontology.get("flow_chains") or {}
    explicit = str(case.get("business_intent") or case.get("scenario_type") or "").strip()
    if explicit and (not flow_chains or explicit in flow_chains):
        return explicit

    for tag in case.get("tags") or []:
        raw = str(tag)
        if raw.startswith("business-intent:"):
            value = raw.split(":", 1)[1].strip()
            if value and (not flow_chains or value in flow_chains):
                return value

    haystack = _normalise(" ".join(_case_texts(case)))
    intents = ontology.get("business_intents") or {}
    local_intents = {str(item) for item in ontology.get("_local_business_intents") or []}
    matches: list[tuple[int, int, int, str]] = []
    for intent, spec in intents.items():
        intent_name = str(intent)
        keywords = spec.get("keywords") if isinstance(spec, dict) else []
        matched_lengths = [
            len(_normalise(str(keyword)))
            for keyword in keywords or []
            if _normalise(str(keyword)) and _normalise(str(keyword)) in haystack
        ]
        if matched_lengths:
            local_priority = 1 if intent_name in local_intents else 0
            flow_priority = 1 if intent_name in flow_chains else 0
            matches.append((local_priority, flow_priority, max(matched_lengths), intent_name))
    if not matches:
        return None
    matches.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    return matches[0][3]


def resolve_page_types(case: dict[str, Any], ontology: dict[str, Any]) -> list[str]:
    """Resolve an ordered page chain for a case from the domain ontology."""
    intent = resolve_business_intent(case, ontology)
    flow_chains = ontology.get("flow_chains") or {}
    if intent and isinstance(flow_chains.get(intent), list):
        return _unique(str(item) for item in flow_chains[intent])

    page_types = ontology.get("page_types") or {}
    matched: list[str] = []
    for text in _case_texts(case):
        normalised = _normalise(text)
        for page_type, spec in page_types.items():
            if not isinstance(spec, dict):
                continue
            keywords = spec.get("keywords") or []
            if any(_normalise(str(keyword)) in normalised for keyword in keywords):
                matched.append(str(page_type))
    return _unique(matched)


def build_domain_path_nodes(case: dict[str, Any], ontology: dict[str, Any]) -> list[dict[str, Any]]:
    """Build runner-neutral path nodes from ontology page definitions."""
    page_specs = ontology.get("page_types") or {}
    nodes: list[dict[str, Any]] = []
    for index, page_type in enumerate(resolve_page_types(case, ontology), start=1):
        raw_spec = page_specs.get(page_type) or {}
        spec = raw_spec if isinstance(raw_spec, dict) else {}
        node_type = str(spec.get("node_type") or _default_node_type(page_type))
        nodes.append(
            {
                "id": f"page-{index:02d}-{_slugify(page_type)}",
                "page_type": page_type,
                "name": str(spec.get("name") or spec.get("description") or page_type.replace("_", " ").title()),
                "node_type": node_type,
                "url_pattern": spec.get("url_pattern"),
                "optional": page_type in set(str(item) for item in ontology.get("optional_page_types") or []),
            }
        )
    return nodes


def legacy_page_specs(ontology: dict[str, Any]) -> dict[str, tuple[str, str, str | None]]:
    """Convert v2 ontology page definitions to the legacy Agent2 tuple shape."""
    result: dict[str, tuple[str, str, str | None]] = {}
    for page_type, raw_spec in (ontology.get("page_types") or {}).items():
        spec = raw_spec if isinstance(raw_spec, dict) else {}
        result[str(page_type)] = (
            str(spec.get("name") or spec.get("description") or str(page_type).replace("_", " ").title()),
            str(spec.get("node_type") or _default_node_type(str(page_type))),
            str(spec["url_pattern"]) if spec.get("url_pattern") is not None else None,
        )
    return result


def legacy_type_guesses(ontology: dict[str, Any]) -> tuple[tuple[tuple[str, ...], tuple[str, str, str, str | None]], ...]:
    """Convert ontology keywords to the legacy Agent2 keyword matcher shape."""
    page_specs = legacy_page_specs(ontology)
    guesses: list[tuple[tuple[str, ...], tuple[str, str, str, str | None]]] = []
    for page_type, raw_spec in (ontology.get("page_types") or {}).items():
        spec = raw_spec if isinstance(raw_spec, dict) else {}
        keywords = tuple(str(item).lower() for item in spec.get("keywords") or [] if str(item).strip())
        if not keywords or str(page_type) not in page_specs:
            continue
        name, node_type, url_pattern = page_specs[str(page_type)]
        guesses.append((keywords, (str(page_type), name, node_type, url_pattern)))
    return tuple(guesses)


def _default_node_type(page_type: str) -> str:
    normalised = page_type.lower()
    if "payment" in normalised or "checkout" in normalised:
        return "payment"
    if "result" in normalised or "success" in normalised or "complete" in normalised:
        return "result"
    if "review" in normalised or "confirm" in normalised or "underwriting" in normalised:
        return "confirm"
    return "form"


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "page"


def _unique(values: Any) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        item = str(value).strip()
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result

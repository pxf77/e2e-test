from __future__ import annotations

import fnmatch
from typing import Any

from .model import DomainPack


class DomainResolver:
    """Small deterministic helpers backed by a loaded DomainPack."""

    def __init__(self, domain_pack: DomainPack) -> None:
        self.domain_pack = domain_pack

    def state_keys_for_route(self, route: str) -> list[str]:
        whitelist = self.domain_pack.state_deps.get("whitelist") or {}
        keys: list[str] = []
        for pattern, state_keys in whitelist.items():
            if fnmatch.fnmatch(route, str(pattern)):
                keys.extend(str(item) for item in state_keys or [])
        return sorted(dict.fromkeys(keys))

    def resolve_page_type(self, url: str, title: str = "", hints: dict[str, Any] | None = None) -> str:
        hints = hints or {}
        explicit = hints.get("page_type")
        if explicit:
            return str(explicit)
        page_types = self.domain_pack.ontology.get("page_types") or {}
        haystack = f"{url} {title}".lower()
        for page_type, spec in page_types.items():
            keywords = spec.get("keywords") if isinstance(spec, dict) else []
            if any(str(keyword).lower() in haystack for keyword in keywords or []):
                return str(page_type)
        return "unknown"

    def resolve_business_intents(self, text: str) -> list[str]:
        intents = self.domain_pack.ontology.get("business_intents") or {}
        haystack = text.lower()
        matched: list[str] = []
        for intent, spec in intents.items():
            keywords = spec.get("keywords") if isinstance(spec, dict) else []
            if any(str(keyword).lower() in haystack for keyword in keywords or []):
                matched.append(str(intent))
        return matched

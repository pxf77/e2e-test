from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml


class Yaml12SafeLoader(yaml.SafeLoader):
    """SafeLoader variant that follows YAML 1.2 boolean semantics.

    PyYAML's default YAML 1.1 resolver converts keys such as ``on`` and ``off``
    into booleans. Workflow DSL uses ``on`` as an edge condition field, so the
    default resolver corrupts otherwise valid workflow documents.
    """


# Copy resolver mappings before modifying them; mutating the inherited mapping
# would affect yaml.SafeLoader globally.
Yaml12SafeLoader.yaml_implicit_resolvers = {
    key: list(value) for key, value in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
for first_char, resolvers in list(Yaml12SafeLoader.yaml_implicit_resolvers.items()):
    Yaml12SafeLoader.yaml_implicit_resolvers[first_char] = [
        (tag, regexp) for tag, regexp in resolvers if tag != "tag:yaml.org,2002:bool"
    ]

Yaml12SafeLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|false)$", re.IGNORECASE),
    list("tTfF"),
)


def load_yaml_text(content: str) -> dict[str, Any]:
    payload = yaml.load(content, Loader=Yaml12SafeLoader) or {}
    if not isinstance(payload, dict):
        raise ValueError("YAML document must be an object")
    return payload


def load_yaml_file(path: Path) -> dict[str, Any]:
    try:
        return load_yaml_text(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ValueError(f"{exc}: {path}") from exc

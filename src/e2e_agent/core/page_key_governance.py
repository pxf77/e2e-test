"""Page-key state dependency governance helpers."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from fnmatch import fnmatch
from itertools import product
from typing import Any

HOT_VALUE_SENTINELS = {"*", "$hot", "__hot__", "hot"}
TIMESTAMP_KEYS = {"_t", "ts", "timestamp"}


def _normalization_config(config: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return ((config or {}).get("normalization") or {}) if isinstance(config, Mapping) else {}


def _as_scalar(value: Any) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    return str(value).strip()


def _normalize_scalar(key: str, value: Any, config: Mapping[str, Any] | None) -> str:
    normalization = _normalization_config(config)
    if bool(normalization.get("strip_timestamp")) and key.lower() in TIMESTAMP_KEYS:
        return "stripped"
    result = _as_scalar(value)
    if bool(normalization.get("lowercase_values")):
        result = result.lower()
    return result


def _is_collection(value: Any) -> bool:
    return isinstance(value, Iterable) and not isinstance(value, (str, bytes, Mapping))


def normalize_state_signature(
    state_signature: Mapping[str, Any] | None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """Normalize state values before they participate in page-key generation."""
    if not state_signature:
        return {}

    normalized: dict[str, str] = {}
    for raw_key, raw_value in sorted(state_signature.items(), key=lambda item: str(item[0])):
        key = str(raw_key).strip()
        if not key:
            continue
        if _is_collection(raw_value):
            values = sorted(
                {
                    _normalize_scalar(key, item, config)
                    for item in raw_value
                    if _as_scalar(item)
                }
            )
            if values:
                normalized[key] = "|".join(values)
            continue
        value = _normalize_scalar(key, raw_value, config)
        if value:
            normalized[key] = value
    return normalized


def _matching_patterns(url_pattern: str | None, whitelist: Mapping[str, Any]) -> list[str]:
    if not url_pattern:
        return []
    return [
        str(pattern)
        for pattern in whitelist
        if fnmatch(str(url_pattern), str(pattern))
    ]


def _allowed_keys(patterns: Iterable[str], whitelist: Mapping[str, Any]) -> list[str]:
    keys: set[str] = set()
    for pattern in patterns:
        raw_keys = whitelist.get(pattern, []) or []
        keys.update(str(item) for item in raw_keys)
    return sorted(keys)


def _hot_value_config(config: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return ((config or {}).get("hot_values") or {}) if isinstance(config, Mapping) else {}


def _expand_hot_values(
    state: Mapping[str, str],
    config: Mapping[str, Any] | None,
) -> tuple[list[dict[str, str]], dict[str, list[str]]]:
    hot_values = _hot_value_config(config)
    expansions: dict[str, list[str]] = {}
    base_state = dict(state)

    for key, value in list(state.items()):
        if value.lower() not in HOT_VALUE_SENTINELS:
            continue
        configured_values = hot_values.get(key, []) or []
        normalized_values = sorted(
            {
                _normalize_scalar(key, item, config)
                for item in configured_values
                if _as_scalar(item)
            }
        )
        if not normalized_values:
            continue
        expansions[key] = normalized_values
        base_state.pop(key, None)

    if not expansions:
        return [dict(sorted(base_state.items()))], {}

    states: list[dict[str, str]] = []
    expansion_keys = sorted(expansions)
    for values in product(*(expansions[key] for key in expansion_keys)):
        expanded = dict(base_state)
        expanded.update(dict(zip(expansion_keys, values, strict=True)))
        states.append(dict(sorted(expanded.items())))
    return states, expansions


def validate_state_deps(
    url_pattern: str | None,
    state_signature: Mapping[str, Any] | None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Filter and expand page-key state by the state-deps whitelist."""
    cfg = config or {}
    whitelist = cfg.get("whitelist", {}) or {}
    matched_patterns = _matching_patterns(url_pattern, whitelist)
    allowed_keys = _allowed_keys(matched_patterns, whitelist)
    normalized = normalize_state_signature(state_signature, cfg)

    if allowed_keys:
        allowed_set = set(allowed_keys)
        filtered = {
            key: value
            for key, value in normalized.items()
            if key in allowed_set
        }
        rejected = sorted(key for key in normalized if key not in allowed_set)
    else:
        filtered = {}
        rejected = sorted(normalized)

    states, hot_expansions = _expand_hot_values(filtered, cfg)
    warnings: list[str] = []
    route = url_pattern or "<unknown>"
    if normalized and not matched_patterns:
        warnings.append(
            "No state-deps whitelist rule matched path conditions for "
            f"{route}: {', '.join(sorted(normalized))}"
        )
    elif rejected:
        warnings.append(
            f"Path uses non-whitelisted state keys for {route}: "
            + ", ".join(rejected)
        )

    return {
        "url_pattern": url_pattern,
        "matched_whitelist_patterns": matched_patterns,
        "allowed_state_keys": allowed_keys,
        "rejected_state_keys": rejected,
        "states": states,
        "hot_value_expansions": hot_expansions,
        "warnings": warnings,
    }


def _combination_key(state: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    normalized = normalize_state_signature(state)
    return tuple(sorted(normalized.items()))


def page_key_cardinality_check(
    route_states: Mapping[str, Iterable[Mapping[str, Any]]],
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Report route-level page-key cardinality pressure."""
    cardinality = ((config or {}).get("cardinality") or {}) if isinstance(config, Mapping) else {}
    warn_threshold = int(cardinality.get("warn_threshold", 30))
    max_combinations = int(cardinality.get("max_combinations", 50))
    routes: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []

    for route, states in sorted(route_states.items()):
        combinations = {
            _combination_key(state)
            for state in states
        }
        count = len(combinations)
        severity = "ok"
        if count > max_combinations:
            severity = "error"
            warnings.append(f"{route} exceeds max state combination count: {count}>{max_combinations}")
        elif count > warn_threshold:
            severity = "warning"
            warnings.append(f"{route} approaches state combination limit: {count}>{warn_threshold}")
        routes[route] = {
            "combination_count": count,
            "warn_threshold": warn_threshold,
            "max_combinations": max_combinations,
            "severity": severity,
        }

    return {
        "routes": routes,
        "warnings": warnings,
    }


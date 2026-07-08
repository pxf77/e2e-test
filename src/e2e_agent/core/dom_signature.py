"""DOM layered signature helpers for W3/W4 governance handoff."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse

from e2e_agent.artifacts.paths import agent_artifact_dir, agent_artifact_path

SIGNATURE_VERSION = "dom-signature-v1"
NOISE_RULES = [
    "strip_query_timestamps",
    "collapse_whitespace",
    "replace_datetime",
    "replace_numbers",
    "selector_hash_only",
]

_DATETIME_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?\b")
_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_TIME_RE = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b")
_NUMBER_RE = re.compile(r"(?<![\w-])\d+(?:\.\d+)?(?![\w-])")
_TIMESTAMP_QUERY_RE = re.compile(r"([?&])(?:_t|ts|timestamp)=\d+")
_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _stable_hash(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _normalise_url(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlparse(value)
    path = parsed.path or value
    query = _TIMESTAMP_QUERY_RE.sub(r"\1", f"?{parsed.query}" if parsed.query else "")
    query = query.strip("?&")
    return f"{path}?{query}" if query else path


def _safe_output_stem(value: str) -> str:
    normalized = _SAFE_FILENAME_RE.sub("-", value.replace("\\", "/").split("/")[-1])
    normalized = normalized.strip(".-")
    return normalized or "page"


def normalize_text_signature(text: str | None) -> str:
    """Remove volatile text while keeping business-visible wording."""
    value = str(text or "")
    value = _DATETIME_RE.sub("<date> <time>", value)
    value = _DATE_RE.sub("<date>", value)
    value = _TIME_RE.sub("<time>", value)
    value = _NUMBER_RE.sub("<number>", value)
    return " ".join(value.split())


def _field_components(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    fields = record.get("field_map", [])
    if not isinstance(fields, list):
        return []
    components: list[dict[str, Any]] = []
    for index, field in enumerate(fields, start=1):
        if not isinstance(field, Mapping):
            continue
        field_key = str(field.get("field_key") or field.get("name") or f"field-{index}")
        components.append(
            {
                "component_id": f"FIELD-{index:03d}",
                "component_type": "field",
                "field_key": field_key,
                "tag": str(field.get("tag") or ""),
                "input_type": str(field.get("type") or ""),
                "selector_hash": _stable_hash(str(field.get("selector") or "")),
                "label_hash": _stable_hash(normalize_text_signature(str(field.get("label") or field_key))),
            }
        )
    return components


def _iter_selector_values(selector_map: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(selector_map, Mapping):
        for value in selector_map.values():
            if isinstance(value, Mapping):
                yield value
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, Mapping):
                        yield item
    elif isinstance(selector_map, list):
        for item in selector_map:
            if isinstance(item, Mapping):
                yield item


def _action_components(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    components: list[dict[str, Any]] = []
    for index, action in enumerate(_iter_selector_values(record.get("selector_map", {})), start=1):
        text = normalize_text_signature(str(action.get("text") or ""))
        components.append(
            {
                "component_id": f"ACTION-{index:03d}",
                "component_type": "action",
                "tag": str(action.get("tag") or ""),
                "selector_hash": _stable_hash(str(action.get("selector") or "")),
                "text_hash": _stable_hash(text),
                "text_excerpt": text[:80],
            }
        )
    return components


def build_dom_signature_bundle(
    record: Mapping[str, Any],
    *,
    product_id: str,
    root_dir: Path | None = None,
    product_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Build structure, component, and text signatures from an Agent3 page record."""
    record_id = str(record.get("page_content_record_id") or record.get("page_id") or "page")
    output_stem = _safe_output_stem(record_id)
    normalized_url = _normalise_url(str(record.get("actual_url") or record.get("url") or ""))
    field_components = _field_components(record)
    action_components = _action_components(record)
    component_fingerprints = field_components + action_components
    structure_basis = {
        "url": normalized_url,
        "page_key": record.get("actual_page_key") or record.get("page_key"),
        "field_components": [
            {
                "field_key": item["field_key"],
                "tag": item["tag"],
                "input_type": item["input_type"],
            }
            for item in field_components
        ],
        "action_count": len(action_components),
    }
    normalized_text = normalize_text_signature(str(record.get("body_text_excerpt") or ""))
    return {
        "signature_version": SIGNATURE_VERSION,
        "source_page_content_record_id": record_id,
        "source_url": record.get("actual_url") or record.get("url"),
        "normalized_url": normalized_url,
        "noise_rules": list(NOISE_RULES),
        "structure_signature": {
            "hash": _stable_hash(structure_basis),
            "basis": structure_basis,
        },
        "component_fingerprints": [
            {
                **item,
                "hash": _stable_hash(
                    {
                        key: value
                        for key, value in item.items()
                        if key not in {"hash", "text_excerpt"}
                    }
                ),
            }
            for item in component_fingerprints
        ],
        "text_signature": {
            "hash": _stable_hash(normalized_text),
            "normalized_text": normalized_text,
        },
        "false_positive_audit_fields": {
            "structure_changed": False,
            "component_changed_count": 0,
            "text_changed": False,
            "review_required": False,
        },
        "output_path": agent_artifact_path(
            product_id,
            "agent3",
            "dom-signatures",
            f"{output_stem}.json",
            root_dir=root_dir,
            product_dir=product_dir,
        ),
    }


def build_dom_signature_index(
    records: Iterable[Mapping[str, Any]],
    *,
    product_id: str,
    root_dir: Path | None = None,
    product_dir: str | Path | None = None,
) -> dict[str, Any]:
    bundles = [
        build_dom_signature_bundle(
            record,
            product_id=product_id,
            root_dir=root_dir,
            product_dir=product_dir,
        )
        for record in records
    ]
    return {
        "signature_version": SIGNATURE_VERSION,
        "product_id": product_id,
        "signature_count": len(bundles),
        "signatures": bundles,
    }


def _component_index(bundle: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {
        str(item.get("component_id") or item.get("hash") or index): item
        for index, item in enumerate(bundle.get("component_fingerprints", []) or [], start=1)
        if isinstance(item, Mapping)
    }


def compare_dom_signature_bundles(
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> dict[str, Any]:
    """Compare two DOM signature bundles and summarize layer-level changes."""
    baseline_components = _component_index(baseline)
    candidate_components = _component_index(candidate)
    baseline_ids = set(baseline_components)
    candidate_ids = set(candidate_components)
    added = [
        dict(candidate_components[item])
        for item in sorted(candidate_ids - baseline_ids)
    ]
    removed = [
        dict(baseline_components[item])
        for item in sorted(baseline_ids - candidate_ids)
    ]
    changed = [
        {
            "component_id": item,
            "before_hash": baseline_components[item].get("hash"),
            "after_hash": candidate_components[item].get("hash"),
            "component_type": candidate_components[item].get("component_type"),
        }
        for item in sorted(baseline_ids & candidate_ids)
        if baseline_components[item].get("hash") != candidate_components[item].get("hash")
    ]
    structure_changed = (
        baseline.get("structure_signature", {}).get("hash")
        != candidate.get("structure_signature", {}).get("hash")
    )
    text_changed = (
        baseline.get("text_signature", {}).get("hash")
        != candidate.get("text_signature", {}).get("hash")
    )
    component_changed_count = len(added) + len(removed) + len(changed)
    review_required = bool(structure_changed or component_changed_count or text_changed)
    noise_filtered = (
        not structure_changed
        and component_changed_count == 0
        and not text_changed
        and baseline.get("source_page_content_record_id")
        != candidate.get("source_page_content_record_id")
    )
    return {
        "baseline_record_id": baseline.get("source_page_content_record_id"),
        "candidate_record_id": candidate.get("source_page_content_record_id"),
        "normalized_url": candidate.get("normalized_url") or baseline.get("normalized_url"),
        "structure_changed": structure_changed,
        "component_changed_count": component_changed_count,
        "component_changes": {
            "added": added,
            "removed": removed,
            "changed": changed,
        },
        "text_changed": text_changed,
        "review_required": review_required,
        "noise_filtered": noise_filtered,
        "noise_rules": list(candidate.get("noise_rules") or baseline.get("noise_rules") or NOISE_RULES),
    }


def _record_group_key(record: Mapping[str, Any]) -> str:
    return str(
        record.get("actual_page_key")
        or record.get("page_key")
        or _normalise_url(str(record.get("actual_url") or record.get("url") or ""))
        or record.get("page_content_record_id")
        or "page"
    )


def build_dom_sample_validation_report(
    records: Iterable[Mapping[str, Any]],
    *,
    product_id: str,
) -> dict[str, Any]:
    """Build a batch validation report from real or quasi-real page records."""
    grouped_records: dict[str, list[Mapping[str, Any]]] = {}
    for record in records:
        if not isinstance(record, Mapping):
            continue
        grouped_records.setdefault(_record_group_key(record), []).append(record)

    comparisons: list[dict[str, Any]] = []
    for group_key, group_records in sorted(grouped_records.items()):
        if len(group_records) < 2:
            continue
        ordered = sorted(
            group_records,
            key=lambda item: str(item.get("page_content_record_id") or item.get("page_id") or ""),
        )
        baseline = build_dom_signature_bundle(ordered[0], product_id=product_id)
        for candidate_record in ordered[1:]:
            candidate = build_dom_signature_bundle(candidate_record, product_id=product_id)
            comparison = compare_dom_signature_bundles(baseline, candidate)
            comparison["group_key"] = group_key
            comparisons.append(comparison)

    comparison_count = len(comparisons)
    false_positive_candidates = [
        item
        for item in comparisons
        if not item["review_required"] and not item["noise_filtered"]
    ]
    false_positive_count = len(false_positive_candidates)
    false_positive_rate = (
        false_positive_count / comparison_count
        if comparison_count
        else 0
    )
    return {
        "signature_version": SIGNATURE_VERSION,
        "product_id": product_id,
        "summary": {
            "sample_count": sum(len(items) for items in grouped_records.values()),
            "group_count": len(grouped_records),
            "comparison_count": comparison_count,
            "structure_change_count": sum(1 for item in comparisons if item["structure_changed"]),
            "component_change_count": sum(1 for item in comparisons if item["component_changed_count"]),
            "text_change_count": sum(1 for item in comparisons if item["text_changed"]),
            "review_required_count": sum(1 for item in comparisons if item["review_required"]),
            "noise_filtered_count": sum(1 for item in comparisons if item["noise_filtered"]),
            "false_positive_candidate_count": false_positive_count,
            "false_positive_rate": false_positive_rate,
        },
        "comparisons": comparisons,
        "false_positive_candidates": false_positive_candidates,
    }


def load_page_content_records(path: Path) -> list[dict[str, Any]]:
    """Load page_content_records from a registry, explore trace, or raw list file."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return [dict(item) for item in payload if isinstance(item, Mapping)]
    if isinstance(payload, Mapping):
        records = payload.get("page_content_records", payload.get("records", []))
        if isinstance(records, list):
            return [dict(item) for item in records if isinstance(item, Mapping)]
    return []


def write_dom_sample_validation_report(
    *,
    root_dir: Path,
    product_id: str,
    records: Iterable[Mapping[str, Any]],
    product_dir: str | Path | None = None,
) -> dict[str, Any]:
    report = build_dom_sample_validation_report(records, product_id=product_id)
    output_dir = agent_artifact_dir(root_dir, product_id, "agent3", product_dir=product_dir) / "dom-signatures"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "dom-sample-validation.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report


def write_dom_signature_bundles(
    *,
    root_dir: Path,
    product_id: str,
    records: Iterable[Mapping[str, Any]],
    product_dir: str | Path | None = None,
) -> dict[str, Any]:
    materialized_records = list(records)
    index = build_dom_signature_index(
        materialized_records,
        product_id=product_id,
        root_dir=root_dir,
        product_dir=product_dir,
    )
    output_dir = agent_artifact_dir(root_dir, product_id, "agent3", product_dir=product_dir) / "dom-signatures"
    output_dir.mkdir(parents=True, exist_ok=True)
    for bundle in index["signatures"]:
        filename = Path(str(bundle["output_path"])).name
        (output_dir / filename).write_text(
            json.dumps(bundle, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    (output_dir / "dom-signature-index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_dom_sample_validation_report(
        root_dir=root_dir,
        product_id=product_id,
        records=materialized_records,
        product_dir=product_dir,
    )
    return index

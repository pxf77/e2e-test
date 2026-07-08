"""Optional product knowledge-base loading helpers."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class LoadedKnowledge:
    product_id: str
    root: Path
    available: bool
    knowledge_base: dict[str, Any]
    ui_ontology: dict[str, Any]
    field_catalog: dict[str, Any]
    workflow_case_payload: dict[str, Any]
    workflow_cases: list[dict[str, Any]]
    warnings: list[str]


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def normalise_workflow_cases(payload: Any) -> dict[str, Any]:
    """Return a stable workflow-cases payload from loose JSON-like input."""
    if isinstance(payload, dict):
        raw_cases = payload.get("cases")
        product_id = str(payload.get("product_id") or "").strip()
        schema_version = str(payload.get("schema_version") or "1.0").strip()
        source = str(payload.get("source") or "knowledge.workflow_cases").strip()
    elif isinstance(payload, list):
        raw_cases = payload
        product_id = ""
        schema_version = "1.0"
        source = "knowledge.workflow_cases"
    else:
        raw_cases = []
        product_id = ""
        schema_version = "1.0"
        source = "knowledge.workflow_cases"

    cases: list[dict[str, Any]] = []
    if not isinstance(raw_cases, list):
        raw_cases = []
    for index, raw_case in enumerate(raw_cases, start=1):
        if not isinstance(raw_case, dict):
            continue
        case_id = str(raw_case.get("case_id") or raw_case.get("id") or f"KLG-CASE-{index:03d}").strip()
        title = str(raw_case.get("title") or raw_case.get("name") or case_id).strip()
        steps = _as_list(raw_case.get("steps"))
        assertions = _as_list(raw_case.get("assertions") or raw_case.get("expected"))
        preconditions = _as_list(raw_case.get("preconditions"))
        tags = _as_list(raw_case.get("tags"))
        cases.append(
            {
                "case_id": case_id,
                "title": title,
                "priority": str(raw_case.get("priority") or "P1").strip(),
                "type": str(raw_case.get("type") or "happy_path").strip(),
                "steps": steps or [title],
                "assertions": assertions or [f"{title} completes successfully"],
                "preconditions": preconditions,
                "tags": tags,
                "source": str(raw_case.get("source") or source).strip(),
            }
        )

    result: dict[str, Any] = {
        "schema_version": schema_version,
        "source": source,
        "cases": cases,
    }
    if product_id:
        result["product_id"] = product_id
    return result


class KnowledgeLoader:
    """Loads optional knowledge artifacts without making them a hard dependency."""

    def __init__(self, root_dir: str | Path) -> None:
        self.root_dir = Path(root_dir)

    def load(self, product_id: str) -> LoadedKnowledge:
        product = str(product_id or "product").strip() or "product"
        root = self.root_dir / "knowledge" / product
        warnings: list[str] = []
        parsed: dict[str, dict[str, Any]] = {}

        for filename in (
            "knowledge-base.json",
            "ui-ontology.json",
            "field-catalog.json",
            "workflow-cases.json",
        ):
            path = root / filename
            if not path.exists():
                warnings.append(f"{filename} not found at {path}")
                parsed[filename] = {}
                continue
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                warnings.append(f"{filename} is invalid JSON: {exc.msg}")
                parsed[filename] = {}
                continue
            if not isinstance(value, dict):
                warnings.append(f"{filename} must contain a JSON object")
                parsed[filename] = {}
                continue
            parsed[filename] = value

        workflow_case_payload = normalise_workflow_cases(parsed["workflow-cases.json"])
        available = any(bool(value) for value in parsed.values())
        return LoadedKnowledge(
            product_id=product,
            root=root,
            available=available,
            knowledge_base=parsed["knowledge-base.json"],
            ui_ontology=parsed["ui-ontology.json"],
            field_catalog=parsed["field-catalog.json"],
            workflow_case_payload=workflow_case_payload,
            workflow_cases=list(workflow_case_payload.get("cases", [])),
            warnings=warnings,
        )

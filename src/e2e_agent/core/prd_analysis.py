"""Core project-adapted PRD analysis logic."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]+\)")
_HTML_BREAK_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_MARKDOWN_STYLE_RE = re.compile(r"(\*\*|__|`)")


def slugify(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]+", "-", value.strip()).strip("-")
    return cleaned.lower() or "feature"


def load_text(path_value: str) -> str:
    path = Path(path_value)
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8-sig")


def strip_markup(value: str) -> str:
    cleaned = _IMAGE_RE.sub("", value)
    cleaned = _HTML_BREAK_RE.sub("；", cleaned)
    cleaned = _LINK_RE.sub(r"\1", cleaned)
    cleaned = _HTML_TAG_RE.sub("", cleaned)
    cleaned = _MARKDOWN_STYLE_RE.sub("", cleaned)
    cleaned = cleaned.replace("::::info", "").replace("::::success", "").replace("::::", "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def split_sentences(value: str) -> list[str]:
    cleaned = strip_markup(value)
    if not cleaned:
        return []
    parts = re.split(r"[；;。\n]+", cleaned)
    results: list[str] = []
    for part in parts:
        item = part.strip(" -*+•[]")
        if not item:
            continue
        if item in {"既往逻辑：", "本次逻辑：", "具体规则详见下文"}:
            continue
        if item.endswith("逻辑：") and len(item) <= 6:
            continue
        results.append(item)
    return results


def iter_sections(text: str) -> list[tuple[str, list[str]]]:
    sections: list[tuple[str, list[str]]] = []
    current_name = "ROOT"
    current_lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        heading_match = _HEADING_RE.match(line.strip())
        if heading_match:
            if current_lines:
                sections.append((current_name, current_lines))
            current_name = strip_markup(heading_match.group(2)) or "ROOT"
            current_lines = []
            continue
        current_lines.append(line)
    if current_lines:
        sections.append((current_name, current_lines))
    return sections


def parse_markdown_tables(lines: list[str]) -> list[tuple[list[str], list[list[str]]]]:
    tables: list[tuple[list[str], list[list[str]]]] = []
    buffer: list[str] = []

    def flush() -> None:
        nonlocal buffer
        if len(buffer) < 2:
            buffer = []
            return
        rows = [_split_table_line(line) for line in buffer if line.strip().startswith("|")]
        buffer = []
        if len(rows) < 2:
            return
        header = rows[0]
        body = [row for row in rows[1:] if not _is_separator_row(row)]
        if header and body:
            tables.append((header, body))

    for line in lines:
        if line.strip().startswith("|"):
            buffer.append(line)
            continue
        flush()
    flush()
    return tables


def _split_table_line(line: str) -> list[str]:
    content = line.strip().strip("|")
    return [strip_markup(cell) for cell in content.split("|")]


def _is_separator_row(row: list[str]) -> bool:
    return all(not cell or re.fullmatch(r"[:\-\s]+", cell) for cell in row)


def _priority_from_text(*values: str) -> str:
    joined = " ".join(values)
    if any(token in joined for token in ("P1", "低优先", "次要")):
        return "P1"
    if any(token in joined for token in ("P2",)):
        return "P2"
    return "P0"


def _feature_record(
    features: list[dict[str, Any]],
    *,
    name: str,
    description: str,
    acceptance_criteria: list[str],
    priority: str = "P0",
    extra_fields: dict[str, Any] | None = None,
) -> None:
    cleaned_name = strip_markup(name)
    criteria = []
    for item in acceptance_criteria:
        for part in split_sentences(item):
            if part and part not in criteria:
                criteria.append(part)
    if not cleaned_name or not criteria:
        return
    record = {
        "feature_id": f"FEAT-{len(features) + 1:03d}-{slugify(cleaned_name)}",
        "name": cleaned_name,
        "description": strip_markup(description) or f"Derived from PRD item: {cleaned_name}",
        "acceptance_criteria": criteria,
        "priority": priority,
    }
    if extra_fields:
        record.update(extra_fields)
    features.append(record)


def extract_features(text: str) -> list[dict[str, Any]]:
    sections = iter_sections(text)
    features: list[dict[str, Any]] = []
    seen_business_table = False
    seen_attribute_table = False

    for section_name, lines in sections:
        for header, rows in parse_markdown_tables(lines):
            if {"节点", "业务场景", "业务规则"}.issubset(set(header)):
                seen_business_table = True
                node_idx = header.index("节点")
                scenario_idx = header.index("业务场景")
                rules_idx = header.index("业务规则")
                outline_idx = header.index("产品方案概要") if "产品方案概要" in header else None
                current_node = ""
                for row in rows:
                    if len(row) <= max(node_idx, scenario_idx, rules_idx):
                        continue
                    node = row[node_idx].strip() or current_node
                    scenario = row[scenario_idx].strip()
                    rules = row[rules_idx].strip()
                    if node:
                        current_node = node
                    if not (current_node or scenario or rules):
                        continue
                    name = " / ".join(part for part in (current_node, scenario) if part)
                    description = row[outline_idx].strip() if outline_idx is not None and outline_idx < len(row) else f"Derived from section: {section_name}"
                    _feature_record(
                        features,
                        name=name or section_name,
                        description=description,
                        acceptance_criteria=[rules or description or name],
                        priority=_priority_from_text(name, rules, description),
                        extra_fields={
                            "flow_node": current_node or scenario or section_name,
                            "flow_action": scenario or rules or description,
                        },
                    )
                continue

            if {"投保属性", "业务规则"}.issubset(set(header)):
                seen_attribute_table = True
                attr_idx = header.index("投保属性")
                rules_idx = header.index("业务规则")
                current_group = ""
                for row in rows:
                    attr = row[attr_idx].strip() if attr_idx < len(row) else ""
                    rules = row[rules_idx].strip() if rules_idx < len(row) else ""
                    non_empty_cells = [cell for cell in row if cell]
                    if attr and not rules and len(non_empty_cells) == 1:
                        current_group = attr
                        continue
                    if not attr or not rules:
                        continue
                    name_parts = []
                    if current_group and current_group != attr:
                        name_parts.append(current_group)
                    name_parts.append(attr)
                    _feature_record(
                        features,
                        name=" / ".join(name_parts),
                        description=f"Derived from section: {section_name}",
                        acceptance_criteria=[rules],
                        priority=_priority_from_text(attr, rules, current_group),
                    )

        if "新增通用约束" in section_name:
            current_title = ""
            current_items: list[str] = []

            def flush_constraint() -> None:
                nonlocal current_title, current_items
                if current_title and current_items:
                    _feature_record(
                        features,
                        name=f"新增通用约束 / {current_title}",
                        description=f"Derived from section: {section_name}",
                        acceptance_criteria=current_items,
                        priority="P0",
                    )
                current_title = ""
                current_items = []

            for raw_line in lines:
                line = strip_markup(raw_line)
                if not line:
                    continue
                title_match = re.match(r"^(\d+)\.\s*(.+)$", line)
                indent = len(raw_line) - len(raw_line.lstrip())
                if title_match and indent == 0:
                    flush_constraint()
                    current_title = title_match.group(2).strip()
                    continue
                if title_match and indent > 0:
                    current_items.append(title_match.group(2).strip())
                    continue
                if re.match(r"^\d+\.\s*(.+)$", line):
                    continue
                if current_title and line:
                    current_items.append(line)
            flush_constraint()

    if seen_business_table or seen_attribute_table:
        return features

    for section_name, lines in sections:
        if section_name in {"ROOT", "更新日志", "产品信息", "系统流程"}:
            continue
        bullets = [strip_markup(line).lstrip("-*+ ").strip() for line in lines if line.strip().startswith(("-", "*", "+"))]
        bullets = [item for item in bullets if item]
        if bullets:
            _feature_record(
                features,
                name=section_name,
                description=f"Derived from PRD section: {section_name}",
                acceptance_criteria=bullets,
                priority=_priority_from_text(section_name, " ".join(bullets)),
            )

    if features:
        return features

    fallback_items = [part for part in split_sentences(text) if len(part) >= 4][:8]
    _feature_record(
        features,
        name="PRD Feature Summary",
        description="Fallback feature generated from PRD text",
        acceptance_criteria=fallback_items or ["PRD 内容已完成整理"],
        priority="P0",
    )
    return features


def build_application_flow(features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flow: list[dict[str, Any]] = []
    seen_pages: set[str] = set()
    flow_features = [feature for feature in features if feature.get("flow_node")]
    source_features = flow_features or features
    for feature in source_features:
        name = str(feature.get("name") or "")
        if feature.get("flow_node"):
            page = str(feature.get("flow_node") or name or "业务节点").strip()
            action = str(feature.get("flow_action") or "").strip()
        elif " / " in name:
            page, action_name = [part.strip() for part in name.split(" / ", 1)]
            action = str(feature.get("acceptance_criteria", [action_name])[0])
        else:
            page, action_name = name, name
            action = str(feature.get("acceptance_criteria", [action_name])[0])
        page = page or name or "业务节点"
        if page in seen_pages:
            continue
        seen_pages.add(page)
        flow.append(
            {
                "step": len(flow) + 1,
                "page": page,
                "action": str(action),
                "branching": any(token in str(action) for token in ("若", "如果", "可选", "条件", "否则")),
            }
        )
    return flow


def build_traceability_matrix(features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    matrix: list[dict[str, Any]] = []
    for feature in features:
        feature_id = str(feature.get("feature_id") or "").strip()
        criteria = list(feature.get("acceptance_criteria", []) or [])
        for index, criterion in enumerate(criteria, start=1):
            matrix.append(
                {
                    "feature_id": feature_id,
                    "requirement_ref": f"{feature_id}-AC-{index:02d}",
                    "source": str(feature.get("name") or "PRD feature"),
                    "acceptance_criterion": str(criterion),
                    "downstream_hint": {
                        "case_seed": f"{feature_id}-TC-{index:02d}",
                        "priority": str(feature.get("priority") or "P0"),
                    },
                }
            )
    return matrix


def render_analysis_markdown(analysis: dict[str, Any]) -> str:
    lines = [
        f"# {analysis['product_id']} PRD 分析",
        "",
        "## 功能清单",
    ]
    for feature in analysis["features"]:
        lines.append(f"### {feature['name']}")
        for item in feature["acceptance_criteria"]:
            lines.append(f"- {item}")
        lines.append("")
    lines.append("## 应用流程")
    for step in analysis["application_flow"]:
        lines.append(f"{step['step']}. {step['page']} -> {step['action']}")
    lines.append("")
    lines.append("## 需求追踪矩阵")
    for item in analysis.get("traceability_matrix", []):
        lines.append(
            f"- {item['requirement_ref']}: {item['acceptance_criterion']} -> "
            f"{item['downstream_hint']['case_seed']}"
        )
    return "\n".join(lines).rstrip() + "\n"


def materialise_analysis(root_dir: Path, product_id: str, analysis: dict[str, Any]) -> None:
    output_dir = root_dir / "products" / product_id / "prd-ana"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "analysis.json").write_text(
        json.dumps(analysis, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "analysis.md").write_text(
        render_analysis_markdown(analysis),
        encoding="utf-8",
    )


def build_prd_analysis(product_id: str, text: str) -> dict[str, Any]:
    features = extract_features(text)
    return {
        "product_id": product_id,
        "analysis_version": "1.1",
        "features": features,
        "application_flow": build_application_flow(features),
        "dependencies": [],
        "traceability_matrix": build_traceability_matrix(features),
    }


def parse_prd_analysis_from_path(prd_path: str, product_id: str) -> tuple[dict[str, Any], list[str]]:
    path = Path(prd_path)
    if not path.exists():
        return (
            {
                "product_id": product_id,
                "analysis_version": "1.1",
                "features": [],
                "application_flow": [],
                "dependencies": [],
                "traceability_matrix": [],
            },
            [f"prd_path not found: {path}"],
        )

    if path.suffix.lower() == ".json":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                payload.setdefault("product_id", product_id)
                payload.setdefault("analysis_version", "1.1")
                payload.setdefault("features", [])
                payload.setdefault("application_flow", build_application_flow(payload.get("features", [])))
                payload.setdefault("dependencies", [])
                payload.setdefault("traceability_matrix", build_traceability_matrix(payload.get("features", [])))
                return payload, []
        except json.JSONDecodeError as exc:
            return (
                {
                    "product_id": product_id,
                    "analysis_version": "1.1",
                    "features": [],
                    "application_flow": [],
                    "dependencies": [],
                    "traceability_matrix": [],
                },
                [f"Failed to parse PRD JSON {path.name}: {exc}"],
            )

    return build_prd_analysis(product_id, load_text(str(path))), []

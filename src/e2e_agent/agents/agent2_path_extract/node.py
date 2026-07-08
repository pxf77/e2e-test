"""path_extract_agent: Extract regression test paths from merged cases.

LangGraph node responsible for:
- Building a business flow tree from merged test cases
- Enumerating regression paths with stable IDs
- Applying state-deps governance and page-key planning
- Writing regression_flow + regression_paths + governance_summary to state

Reads:  state.merged_cases, state.prd_path
Writes: state.regression_flow, state.regression_paths, state.governance_summary, state.error
Gate:   R2 (human review of path completeness)
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from e2e_agent.artifacts.fingerprint import append_artifact_fingerprint
from e2e_agent.artifacts.paths import agent_artifact_path, product_artifact_dir, write_agent_json_artifact
from e2e_agent.core.page_key_governance import (
    page_key_cardinality_check,
    validate_state_deps,
)
from e2e_agent.skills.loader import SkillPackageLoader

if TYPE_CHECKING:
    from e2e_agent.graph.state import E2EAgentState

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - depends on local environment
    yaml = None

_CONDITION_RE = re.compile(r"([A-Za-z][A-Za-z0-9_]*)\s*=\s*([A-Za-z0-9._:-]+)")
_URL_RE = re.compile(r"https?://\S+")
_REVISIT_KEYWORDS = ("返回", "回退", "修改", "重新", "再次", "back", "return", "revisit")
_BRANCH_KEYWORDS = ("如果", "若", "when", "if ", "switch", "切换", "选择")
_TYPE_GUESSES = (
    (("登录", "login"), ("login", "Login", "form", "/login")),
    (("产品", "product", "详情", "detail", "quote", "plan"), ("product", "Product Detail", "form", "/product/detail")),
    (("投保", "insure", "apply", "form", "填写"), ("form", "Application Form", "form", "/insure/form")),
    (("确认", "review", "核对"), ("confirm", "Confirm Order", "confirm", "/confirm")),
    (("支付", "payment", "pay"), ("payment", "Payment", "payment", "/payment")),
    (("成功", "结果", "完成", "success", "result"), ("result", "Result", "result", "/result")),
)
def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").exists():
            return parent
    return Path(__file__).resolve().parents[4]


_ROOT_DIR = _repo_root()
_DEFAULT_STATE_DEPS_PATH = _ROOT_DIR / "config" / "state-deps.yaml"
_PAGE_SPECS: dict[str, tuple[str, str, str | None]] = {
    "product_detail": ("Product Detail", "form", "/product/detail"),
    "premium_calculation": ("Premium Calculation", "form", "/product/to-insure"),
    "plan_selection": ("Plan Selection", "form", "/insure/plan"),
    "applicant_info": ("Applicant Info", "form", "/insure/applicant"),
    "insured_info": ("Insured Info", "form", "/insure/insured"),
    "beneficiary": ("Beneficiary", "form", "/insure/beneficiary"),
    "insure_form": ("Insure Form", "form", "/product/insure"),
    "tax_info": ("Tax Identity", "form", "/insure/tax"),
    "health_notice": ("Health Notice", "form", "/insure/health-notice"),
    "suitability": ("Suitability Questionnaire", "form", "/product/to-insure"),
    "underwriting": ("Underwriting", "confirm", "/underwriting"),
    "risk_control": ("Risk Control", "form", "/risk-control"),
    "payment": ("Payment", "payment", "/payment"),
    "policy_result": ("Policy Result", "result", "/result"),
    "policy_service": ("Policy Service", "form", "/policy/service"),
    "surrender": ("Surrender", "form", "/policy/surrender"),
}
_MAX_NEW_BUSINESS_ORDER_CHAIN = [
    "product_detail",
    "premium_calculation",
    "suitability",
    "health_notice",
    "insure_form",
    "underwriting",
    "risk_control",
    "payment",
    "policy_result",
]
_MAX_PATH_OPTIONAL_PAGE_KEYS = {
    "premium_calculation",
    "suitability",
    "health_notice",
    "underwriting",
    "risk_control",
    "payment",
}
_BUSINESS_INTENT_PAGE_CHAINS: dict[str, list[str]] = {
    "main_flow": _MAX_NEW_BUSINESS_ORDER_CHAIN,
    "health_notice": _MAX_NEW_BUSINESS_ORDER_CHAIN,
    "tax_identity": [
        "product_detail",
        "suitability",
        "insure_form",
        "tax_info",
        "policy_result",
    ],
    "underwriting": _MAX_NEW_BUSINESS_ORDER_CHAIN,
    "payment": _MAX_NEW_BUSINESS_ORDER_CHAIN,
    "policy": [
        *_MAX_NEW_BUSINESS_ORDER_CHAIN,
        "policy_service",
    ],
    "surrender": [
        *_MAX_NEW_BUSINESS_ORDER_CHAIN,
        "policy_service",
        "surrender",
        "policy_result",
    ],
}


def _normalise_text(value: str) -> str:
    lowered = value.strip().lower()
    collapsed = re.sub(r"\s+", "", lowered)
    return re.sub(r"[^\w\u4e00-\u9fff/:-]", "", collapsed)


def _slugify(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", value.strip().lower()).strip("-")
    return cleaned or "node"


def _unique_in_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        item = value.strip()
        if not item:
            continue
        key = _normalise_text(item)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _unique_objects_in_order(values: list[Any]) -> list[Any]:
    seen: set[str] = set()
    result: list[Any] = []
    for value in values:
        key = repr(value)
        if isinstance(value, (dict, list)):
            key = str(value)
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _path_dedupe_key(node_ids: list[str]) -> tuple[str, ...]:
    return tuple(
        node_id
        for node_id in node_ids
        if node_id not in {"NODE-start", "NODE-end", "NODE-branch"}
    )


def _merge_regression_path(existing: dict[str, Any], incoming: dict[str, Any]) -> None:
    existing["case_ids"] = _unique_in_order(
        [str(item) for item in existing.get("case_ids", []) + incoming.get("case_ids", [])]
    )
    for key in ("assertions", "rules", "coverage_refs", "data_variants"):
        existing[key] = _unique_objects_in_order(
            list(existing.get(key, []) or []) + list(incoming.get(key, []) or [])
        )
    existing["source_path_count"] = int(existing.get("source_path_count", 1) or 1) + 1
    condition_sets = list(existing.get("condition_sets", []) or [existing.get("conditions", {}) or {}])
    condition_sets.append(incoming.get("conditions", {}) or {})
    existing["condition_sets"] = _unique_objects_in_order(condition_sets)
    existing["estimated_duration_s"] = max(
        int(existing.get("estimated_duration_s") or 0),
        int(incoming.get("estimated_duration_s") or 0),
    ) or None


def _merge_governance_path(existing: dict[str, Any], incoming: dict[str, Any]) -> None:
    existing["matched_whitelist_patterns"] = _unique_in_order(
        list(existing.get("matched_whitelist_patterns", []) or [])
        + list(incoming.get("matched_whitelist_patterns", []) or [])
    )
    existing["warnings"] = _unique_in_order(
        list(existing.get("warnings", []) or []) + list(incoming.get("warnings", []) or [])
    )
    existing["page_keys"] = _unique_objects_in_order(
        list(existing.get("page_keys", []) or []) + list(incoming.get("page_keys", []) or [])
    )


def _parse_simple_scalar(value: str) -> object:
    stripped = value.strip()
    if stripped in {"true", "True", "TRUE"}:
        return True
    if stripped in {"false", "False", "FALSE"}:
        return False
    if stripped in {"null", "Null", "NULL"}:
        return None
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {'"', "'"}:
        return stripped[1:-1]
    if stripped.isdigit():
        return int(stripped)
    return stripped


def _load_state_deps_config(path: Path = _DEFAULT_STATE_DEPS_PATH) -> dict[str, Any]:
    config = {
        "version": "unknown",
        "whitelist": {},
        "normalization": {
            "strip_timestamp": False,
            "lowercase_values": False,
        },
        "hot_values": {},
        "cardinality": {"warn_threshold": 30, "max_combinations": 50},
    }
    if not path.exists():
        return config

    if yaml is not None:
        with open(path, encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
        if isinstance(loaded, dict):
            config["version"] = str(loaded.get("version", "unknown"))
            config["whitelist"] = loaded.get("whitelist", {}) or {}
            config["normalization"].update(loaded.get("normalization", {}) or {})
            config["hot_values"] = loaded.get("hot_values", {}) or {}
            config["cardinality"].update(loaded.get("cardinality", {}) or {})
        return config

    section: str | None = None
    current_pattern: str | None = None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not line.startswith(" "):
            if ":" in stripped:
                key, raw_value = stripped.split(":", 1)
                key = key.strip()
                raw_value = raw_value.strip()
                if raw_value:
                    config[key] = _parse_simple_scalar(raw_value)
                    section = None
                    current_pattern = None
                    continue
            section = stripped.rstrip(":")
            current_pattern = None
            continue
        if section == "whitelist":
            if line.startswith('  "') and stripped.endswith(":"):
                current_pattern = stripped.rstrip(":").strip('"')
                config["whitelist"][current_pattern] = []
                continue
            if line.startswith("    - ") and current_pattern:
                config["whitelist"][current_pattern].append(stripped[2:].strip())
                continue
        if section in {"normalization", "cardinality"} and line.startswith("  ") and ":" in stripped:
            key, raw_value = stripped.split(":", 1)
            config[section][key.strip()] = _parse_simple_scalar(raw_value)
            continue
        if section == "hot_values":
            if line.startswith("  ") and stripped.endswith(":"):
                current_pattern = stripped.rstrip(":")
                config["hot_values"][current_pattern.strip()] = []
                continue
            if line.startswith("    - ") and current_pattern:
                config["hot_values"].setdefault(current_pattern.strip(), []).append(stripped[2:].strip())
    return config


def _safe_entry_url(value: str | None) -> str:
    return value or "https://example.invalid/"


def _priority_rank(priority: str) -> int:
    return {"P0": 0, "P1": 1, "P2": 2}.get(priority, 9)


def _node_spec(key: str, page_name: str, node_type: str, url_pattern: str | None) -> dict[str, Any]:
    return {
        "node_id": f"NODE-{_slugify(key)}",
        "page_name": page_name,
        "type": node_type,
        "url_pattern": url_pattern,
        "is_terminal": node_type in {"result", "end"},
    }


def _planned_page_node(page_key: str) -> dict[str, Any]:
    page_name, node_type, url_pattern = _PAGE_SPECS[page_key]
    return _node_spec(page_key, page_name, node_type, url_pattern)


def _optional_nodes_for_intent(intent: str | None) -> list[str]:
    if intent not in {"main_flow", "health_notice", "underwriting", "payment"}:
        return []
    return [
        _planned_page_node(page_key)["node_id"]
        for page_key in _BUSINESS_INTENT_PAGE_CHAINS[intent]
        if page_key in _MAX_PATH_OPTIONAL_PAGE_KEYS
    ]


def _guess_node_from_text(text: str) -> dict[str, Any] | None:
    normalised = _normalise_text(text)
    for keywords, (key, page_name, node_type, url_pattern) in _TYPE_GUESSES:
        if any(keyword in normalised for keyword in keywords):
            return _node_spec(key, page_name, node_type, url_pattern)
    return None


def _guess_node_from_url(url: str) -> dict[str, Any]:
    path_only = url.split("?", 1)[0]
    normalised = _normalise_text(path_only)
    guessed = _guess_node_from_text(normalised)
    if guessed:
        guessed["url_pattern"] = path_only
        return guessed
    slug = _slugify(path_only.replace("/", "-"))
    page_name = slug.replace("-", " ").title()
    return _node_spec(slug, page_name, "form", path_only)


def _extract_conditions(case: dict[str, Any]) -> dict[str, str]:
    conditions: dict[str, str] = {}
    tags = [str(tag) for tag in case.get("tags", [])]
    texts = [str(case.get("title", ""))]
    texts.extend(str(step) for step in case.get("steps", []))
    texts.extend(str(pre) for pre in case.get("preconditions", []))
    texts.extend(tags)
    for text in texts:
        for key, value in _CONDITION_RE.findall(text):
            conditions.setdefault(key, value)
        if text.startswith("condition:"):
            raw = text.split(":", 1)[1].strip()
            if "=" in raw:
                key, value = raw.split("=", 1)
                conditions.setdefault(key.strip(), value.strip())
    if any(keyword in _normalise_text(" ".join(texts)) for keyword in _REVISIT_KEYWORDS):
        conditions.setdefault("revisit", "true")
    return dict(sorted(conditions.items()))


def _extract_explicit_urls(case: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    texts = [str(case.get("title", ""))]
    texts.extend(str(step) for step in case.get("steps", []))
    texts.extend(str(tag) for tag in case.get("tags", []))
    for text in texts:
        if text.startswith("url:"):
            urls.append(text.split(":", 1)[1].strip())
            continue
        urls.extend(_URL_RE.findall(text))
    cleaned = []
    for url in urls:
        item = url.strip().rstrip(".,)")
        if item:
            cleaned.append(item)
    return _unique_in_order(cleaned)


def _business_intent(case: dict[str, Any]) -> str | None:
    intent = str(case.get("business_intent") or case.get("scenario_type") or "").strip()
    if intent in _BUSINESS_INTENT_PAGE_CHAINS:
        return intent
    tags = [str(tag) for tag in case.get("tags", [])]
    for tag in tags:
        if tag.startswith("business-intent:"):
            value = tag.split(":", 1)[1].strip()
            if value in _BUSINESS_INTENT_PAGE_CHAINS:
                return value
    return None


def _build_planned_case_nodes(case: dict[str, Any]) -> list[dict[str, Any]] | None:
    intent = _business_intent(case)
    if not intent:
        return None
    nodes = [_node_spec("start", "Entry", "start", None)]
    nodes.extend(_planned_page_node(page_key) for page_key in _BUSINESS_INTENT_PAGE_CHAINS[intent])
    nodes.append(_node_spec("end", "End", "end", None))
    return nodes


def _build_case_nodes(case: dict[str, Any]) -> list[dict[str, Any]]:
    planned_nodes = _build_planned_case_nodes(case)
    if planned_nodes:
        return planned_nodes

    nodes: list[dict[str, Any]] = [_node_spec("start", "Entry", "start", None)]
    explicit_urls = _extract_explicit_urls(case)
    for url in explicit_urls:
        nodes.append(_guess_node_from_url(url))

    texts = [str(case.get("title", ""))]
    texts.extend(str(step) for step in case.get("steps", []))
    texts.extend(str(assertion) for assertion in case.get("assertions", []))

    has_branch = False
    for text in texts:
        normalised = _normalise_text(text)
        if not has_branch and any(keyword in normalised for keyword in _BRANCH_KEYWORDS):
            nodes.append(_node_spec("branch", "Branch Decision", "branch", None))
            has_branch = True
        guessed = _guess_node_from_text(text)
        if guessed:
            nodes.append(guessed)

    if len(nodes) == 1:
        title = str(case.get("title") or case.get("case_id") or "Case")
        nodes.append(_node_spec(f"case-{title}", title, "form", None))

    if nodes[-1]["type"] != "result":
        nodes.append(_node_spec("result", "Result", "result", "/result"))
    nodes.append(_node_spec("end", "End", "end", None))

    deduped: list[dict[str, Any]] = []
    seen_node_ids: set[str] = set()
    for node in nodes:
        node_id = node["node_id"]
        if deduped and deduped[-1]["node_id"] == node_id:
            continue
        if node_id in seen_node_ids and node["type"] not in {"branch", "end"}:
            continue
        seen_node_ids.add(node_id)
        deduped.append(node)
    if deduped[-1]["node_id"] != "NODE-end":
        deduped.append(_node_spec("end", "End", "end", None))
    return deduped


def _build_page_key(node: dict[str, Any], state_values: dict[str, str]) -> str:
    node_seed = str(node.get("url_pattern") or node.get("node_id") or "page").strip("/")
    state_seed = ",".join(f"{key}={value}" for key, value in sorted(state_values.items()))
    if not state_seed:
        return f"PK-{_slugify(node_seed)}"
    return f"PK-{_slugify(node_seed)}-{_slugify(state_seed)}"


def _governance_for_path(
    path_nodes: list[dict[str, Any]],
    conditions: dict[str, str],
    config: dict[str, Any],
) -> tuple[list[str], list[dict[str, Any]], list[str]]:
    if not conditions:
        page_keys = [
            {
                "node_id": node["node_id"],
                "url_pattern": node.get("url_pattern"),
                "page_key": _build_page_key(node, {}),
                "state": {},
                "allowed_state_keys": [],
                "matched_whitelist_patterns": [],
            }
            for node in path_nodes
            if node.get("type") not in {"start", "end", "branch"}
        ]
        return [], page_keys, []

    condition_keys = sorted(key for key in conditions if key != "revisit")
    matched_pattern_names: list[str] = []
    page_keys: list[dict[str, Any]] = []
    warnings: list[str] = []
    for node in path_nodes:
        if node.get("type") in {"start", "end", "branch"}:
            continue
        url_pattern = node.get("url_pattern")
        governance = validate_state_deps(
            str(url_pattern) if url_pattern else None,
            {
                key: value
                for key, value in conditions.items()
                if key != "revisit"
            },
            config,
        )
        warnings.extend(str(item) for item in governance.get("warnings", []))
        matched_for_node = list(governance.get("matched_whitelist_patterns", []) or [])
        matched_pattern_names.extend(matched_for_node)
        for filtered_state in governance.get("states", []) or [{}]:
            page_keys.append(
                {
                    "node_id": node["node_id"],
                    "url_pattern": url_pattern,
                    "page_key": _build_page_key(node, filtered_state),
                    "state": filtered_state,
                    "allowed_state_keys": list(governance.get("allowed_state_keys", []) or []),
                    "matched_whitelist_patterns": matched_for_node,
                    "rejected_state_keys": list(governance.get("rejected_state_keys", []) or []),
                    "hot_value_expansions": dict(governance.get("hot_value_expansions", {}) or {}),
                }
                )
    if condition_keys and warnings:
        warnings = _unique_in_order(warnings)
    return warnings, page_keys, _unique_in_order(matched_pattern_names)


def _build_regression_artifacts(
    state: "E2EAgentState",
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], list[str]]:
    product_id = state.get("product_id") or "product"
    entry_url = _safe_entry_url(state.get("entry_url"))
    config = _load_state_deps_config()
    warnings: list[str] = []
    governance_paths: list[dict[str, Any]] = []
    all_nodes: dict[str, dict[str, Any]] = {
        "NODE-start": _node_spec("start", "Entry", "start", None),
        "NODE-end": _node_spec("end", "End", "end", None),
    }
    edge_index: dict[tuple[str, str, str | None], dict[str, Any]] = {}
    regression_paths: list[dict[str, Any]] = []
    combinations_by_pattern: dict[str, list[dict[str, str]]] = {}

    merged_cases = sorted(
        state.get("merged_cases", []),
        key=lambda case: (_priority_rank(str(case.get("priority", "P0"))), str(case.get("case_id", ""))),
    )
    for path_number, case in enumerate(merged_cases, start=1):
        path_nodes = _build_case_nodes(case)
        intent = _business_intent(case)
        optional_nodes = _optional_nodes_for_intent(intent)
        for node in path_nodes:
            all_nodes.setdefault(node["node_id"], node)

        conditions = _extract_conditions(case)
        path_warnings, page_keys, matched_patterns = _governance_for_path(path_nodes, conditions, config)
        warnings.extend(path_warnings)
        for page_key_item in page_keys:
            url_pattern = page_key_item.get("url_pattern")
            if not url_pattern:
                continue
            combinations_by_pattern.setdefault(str(url_pattern), []).append(
                dict(page_key_item.get("state", {}) or {})
            )

        node_ids = [node["node_id"] for node in path_nodes]
        branch_condition = ", ".join(f"{key}={value}" for key, value in conditions.items()) or None
        for index in range(len(path_nodes) - 1):
            current_node = path_nodes[index]
            next_node = path_nodes[index + 1]
            condition = branch_condition if current_node["type"] == "branch" else None
            edge_index.setdefault(
                (current_node["node_id"], next_node["node_id"], condition),
                {
                    "from": current_node["node_id"],
                    "to": next_node["node_id"],
                    "condition": condition,
                },
            )

        estimated_duration = len(case.get("steps", [])) * 15 + max(len(node_ids) - 2, 0) * 5
        dedupe_key = _path_dedupe_key(node_ids)
        incoming_path = {
            "path_id": f"PATH-{len(regression_paths) + 1:03d}",
            "case_ids": [str(case.get("case_id"))],
            "nodes": node_ids,
            "business_intent": case.get("business_intent"),
            "scenario_type": case.get("scenario_type"),
            "optional_nodes": optional_nodes,
            "execution_policy": {
                "name": "max_new_business_order_path" if optional_nodes else "strict_path",
                "skip_absent_optional_nodes": bool(optional_nodes),
            },
            "conditions": conditions,
            "condition_sets": [conditions],
            "assertions": list(case.get("assertions", []) or []),
            "rules": list(case.get("rules", []) or []),
            "coverage_refs": list(case.get("coverage_refs", []) or []),
            "data_variants": list(case.get("data_variants", []) or []),
            "page_keys": page_keys,
            "priority": str(case.get("priority", "P0")),
            "estimated_duration_s": estimated_duration or None,
            "dedupe_key": "->".join(dedupe_key),
            "source_path_count": 1,
        }
        incoming_governance = {
            "path_id": incoming_path["path_id"],
            "matched_whitelist_patterns": matched_patterns,
            "page_keys": page_keys,
            "warnings": path_warnings,
        }
        existing_index = next(
            (
                index
                for index, item in enumerate(regression_paths)
                if tuple(str(node_id) for node_id in item.get("nodes", []) if node_id not in {"NODE-start", "NODE-end", "NODE-branch"})
                == dedupe_key
            ),
            None,
        )
        if existing_index is None:
            regression_paths.append(incoming_path)
            governance_paths.append(incoming_governance)
        else:
            _merge_regression_path(regression_paths[existing_index], incoming_path)
            _merge_governance_path(governance_paths[existing_index], incoming_governance)

    cardinality_result = page_key_cardinality_check(combinations_by_pattern, config)
    warnings.extend(str(item) for item in cardinality_result.get("warnings", []))
    cardinality_warning_count = sum(
        1
        for item in cardinality_result.get("routes", {}).values()
        if item.get("severity") in {"warning", "error"}
    )

    if not merged_cases:
        edge_index[("NODE-start", "NODE-end", None)] = {
            "from": "NODE-start",
            "to": "NODE-end",
            "condition": None,
        }

    regression_flow = {
        "product_id": product_id,
        "entry_url": entry_url,
        "flow_version": "business-plan-v1",
        "nodes": sorted(all_nodes.values(), key=lambda node: node["node_id"]),
        "edges": sorted(
            edge_index.values(),
            key=lambda edge: (edge["from"], edge["to"], edge["condition"] or ""),
        ),
    }
    governance_summary = {
        "config_version": str(config.get("version", "unknown")),
        "normalization": config.get("normalization", {}) or {},
        "hot_values": config.get("hot_values", {}) or {},
        "paths": governance_paths,
        "summary": {
            "path_count": len(governance_paths),
            "total_page_keys": sum(len(item.get("page_keys", [])) for item in governance_paths),
            "routes_with_cardinality_warning": cardinality_warning_count,
        },
        "cardinality": cardinality_result,
        "warnings": _unique_in_order(warnings),
    }
    return regression_flow, regression_paths, governance_summary, warnings


async def _path_extract_node_impl(state: "E2EAgentState") -> dict:
    """Extract regression paths from merged cases.

    Prefer the dedicated skill entry when available, with the local heuristic
    implementation kept as a fallback for minimal environments.
    """
    warnings: list[str] = []
    loader = SkillPackageLoader()
    product_id = str(state.get("product_id") or "product")
    run_id = str(state.get("run_id") or "run-unknown")
    artifact_root_dir = Path(str(state.get("artifact_root_dir") or _ROOT_DIR))
    artifact_product_dir = product_artifact_dir(
        artifact_root_dir,
        product_id,
        product_dir=state.get("product_artifact_dir"),
        source_paths=[state.get("prd_path"), state.get("manual_cases_path"), state.get("product_source_dir")],
    )
    manifest = None
    try:
        manifest = loader.load_skill("mpt-reg-path-extract")
    except (FileNotFoundError, ValueError) as exc:
        warnings.append(str(exc))

    if manifest and manifest.entry_script:
        try:
            result = loader.run_entry(
                "mpt-reg-path-extract",
                {
                    "product_id": state.get("product_id"),
                    "entry_url": state.get("entry_url"),
                    "merged_cases": state.get("merged_cases", []),
                },
            )
            warnings.extend(str(item) for item in result.get("warnings", []))
            regression_flow = result.get("regression_flow", {})
            regression_paths = result.get("regression_paths", [])
            governance_summary = result.get("governance_summary", {})
            for filename, payload in {
                "regression-flow.json": regression_flow,
                "regression-paths.json": regression_paths,
                "governance-summary.json": governance_summary,
            }.items():
                write_agent_json_artifact(
                    root_dir=artifact_root_dir,
                    product_id=product_id,
                    agent_name="agent2",
                    relative_path=filename,
                    payload=payload,
                    product_dir=artifact_product_dir,
                )
            return {
                "regression_flow": regression_flow,
                "regression_paths": regression_paths,
                "governance_summary": governance_summary,
                "artifact_fingerprints": list(state.get("artifact_fingerprints", []) or []) + [
                    append_artifact_fingerprint(
                        root_dir=artifact_root_dir,
                        product_id=product_id,
                        run_id=run_id,
                        artifact_path=agent_artifact_path(
                            product_id,
                            "agent2",
                            "regression-flow.json",
                            root_dir=artifact_root_dir,
                            product_dir=artifact_product_dir,
                        ),
                        artifact_type="regression-flow",
                        payload=regression_flow,
                        producer="path_extract_agent",
                        product_dir=artifact_product_dir,
                    ),
                    append_artifact_fingerprint(
                        root_dir=artifact_root_dir,
                        product_id=product_id,
                        run_id=run_id,
                        artifact_path=agent_artifact_path(
                            product_id,
                            "agent2",
                            "regression-paths.json",
                            root_dir=artifact_root_dir,
                            product_dir=artifact_product_dir,
                        ),
                        artifact_type="regression-paths",
                        payload=regression_paths,
                        producer="path_extract_agent",
                        product_dir=artifact_product_dir,
                    ),
                    append_artifact_fingerprint(
                        root_dir=artifact_root_dir,
                        product_id=product_id,
                        run_id=run_id,
                        artifact_path=agent_artifact_path(
                            product_id,
                            "agent2",
                            "governance-summary.json",
                            root_dir=artifact_root_dir,
                            product_dir=artifact_product_dir,
                        ),
                        artifact_type="governance-summary",
                        payload=governance_summary,
                        producer="path_extract_agent",
                        product_dir=artifact_product_dir,
                    ),
                ],
                "product_artifact_dir": str(artifact_product_dir),
                "error": "; ".join(dict.fromkeys(warnings)) if warnings else None,
            }
        except (RuntimeError, ValueError, FileNotFoundError) as exc:
            warnings.append(str(exc))

    regression_flow, regression_paths, governance_summary, governance_warnings = _build_regression_artifacts(state)
    warnings.extend(governance_warnings)
    for filename, payload in {
        "regression-flow.json": regression_flow,
        "regression-paths.json": regression_paths,
        "governance-summary.json": governance_summary,
    }.items():
        write_agent_json_artifact(
            root_dir=artifact_root_dir,
            product_id=product_id,
            agent_name="agent2",
            relative_path=filename,
            payload=payload,
            product_dir=artifact_product_dir,
        )
    existing_fingerprints = list(state.get("artifact_fingerprints", []) or [])
    new_fingerprints = [
        append_artifact_fingerprint(
            root_dir=artifact_root_dir,
            product_id=product_id,
            run_id=run_id,
            artifact_path=agent_artifact_path(
                product_id,
                "agent2",
                "regression-flow.json",
                root_dir=artifact_root_dir,
                product_dir=artifact_product_dir,
            ),
            artifact_type="regression-flow",
            payload=regression_flow,
            producer="path_extract_agent",
            product_dir=artifact_product_dir,
        ),
        append_artifact_fingerprint(
            root_dir=artifact_root_dir,
            product_id=product_id,
            run_id=run_id,
            artifact_path=agent_artifact_path(
                product_id,
                "agent2",
                "regression-paths.json",
                root_dir=artifact_root_dir,
                product_dir=artifact_product_dir,
            ),
            artifact_type="regression-paths",
            payload=regression_paths,
            producer="path_extract_agent",
            product_dir=artifact_product_dir,
        ),
        append_artifact_fingerprint(
            root_dir=artifact_root_dir,
            product_id=product_id,
            run_id=run_id,
            artifact_path=agent_artifact_path(
                product_id,
                "agent2",
                "governance-summary.json",
                root_dir=artifact_root_dir,
                product_dir=artifact_product_dir,
            ),
            artifact_type="governance-summary",
            payload=governance_summary,
            producer="path_extract_agent",
            product_dir=artifact_product_dir,
        ),
    ]
    return {
        "regression_flow": regression_flow,
        "regression_paths": regression_paths,
        "governance_summary": governance_summary,
        "product_artifact_dir": str(artifact_product_dir),
        "artifact_fingerprints": existing_fingerprints + new_fingerprints,
        "error": "; ".join(warnings) if warnings else None,
    }


async def path_extract_node(state: "E2EAgentState") -> dict:
    try:
        return await _path_extract_node_impl(state)
    except Exception as exc:
        return {"error": f"path_extract failed: {exc}"}

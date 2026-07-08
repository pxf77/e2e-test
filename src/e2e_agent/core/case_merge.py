"""Core project-adapted case merge logic."""
from __future__ import annotations

import json
import re
import zipfile
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from e2e_agent.core.prd_analysis import parse_prd_analysis_from_path
from e2e_agent.core.case_skeleton import build_stage1_skeleton

_SUPPORTED_MANUAL_CASE_SUFFIXES = {".json", ".md", ".markdown", ".txt", ".xlsx", ".xmind", ".km"}
_TITLE_LINE_RE = re.compile(r"^(#{1,6}\s+.+|(?:TC|CASE|用例)[-:\s].+)$", re.IGNORECASE)
_BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_PRIORITY_RE = re.compile(r"\b(P0|P1|P2)\b", re.IGNORECASE)
_CORE_REGRESSION_KEYWORDS = (
    "投保",
    "证件",
    "国籍",
    "税收",
    "健康告知",
    "智能认证",
    "反洗钱",
    "人工核保",
    "银行签约",
    "签约",
    "支付",
    "承保",
    "电子保单",
    "回访",
    "退保",
    "保全",
)
_BUSINESS_SCENARIOS: tuple[dict[str, Any], ...] = (
    {
        "intent": "main_flow",
        "title": "少儿重疾标准投保成功主链路",
        "priority": "P0",
        "scenario_type": "main_flow",
        "keywords": ("投保成功", "出单", "每个计划投保", "得同出单", "标体单出单", "选择指定受益人可出单成功", "选择法定受益人可出单成功"),
        "business_goal": "验证客户从产品详情进入投保，完成计划选择、投被保人信息、健康/核保、签约支付并形成出单结果。",
        "steps": [
            "进入产品详情页并选择核心销售计划",
            "填写投保人、被保人和受益人信息",
            "完成健康告知与必要认证",
            "提交投保申请并进入核保/支付流程",
            "完成支付或出单确认并查看结果页",
        ],
    },
    {
        "intent": "health_notice",
        "title": "健康告知与风险警示书关键链路",
        "priority": "P0",
        "scenario_type": "core_branch",
        "keywords": ("健康告知", "适当性问卷", "风险警示", "警示确认书"),
        "business_goal": "验证健康告知、适当性问卷与风险警示书触发/不触发规则正确。",
        "steps": [
            "进入健康告知/适当性问卷页面",
            "选择通过组合并确认不触发风险警示书",
            "选择不匹配组合并确认触发风险警示书",
            "完成签署或阻断校验",
        ],
    },
    {
        "intent": "tax_identity",
        "title": "证件类型-国籍-税收居民联动链路",
        "priority": "P0",
        "scenario_type": "core_branch",
        "keywords": ("证件", "国籍", "税收", "纳税", "出生地", "现居地址"),
        "business_goal": "验证证件类型、国籍、税收居民身份和税收相关信息的联动、必填与阻断规则。",
        "steps": [
            "进入投保信息填写页",
            "切换投保人/被保人证件类型",
            "校验国籍和税收居民身份默认值及可选范围",
            "填写或清空税收相关信息",
            "提交并校验必填、长度和非法组合提示",
        ],
    },
    {
        "intent": "underwriting",
        "title": "智能核保/人工核保关键链路",
        "priority": "P0",
        "scenario_type": "core_branch",
        "required": True,
        "keywords": ("智能认证", "OCR", "ocr", "核保", "智能核保", "人工核保", "人核", "承保", "风控", "审核", "隔代投保", "授权书", "BMI", "保司阻断"),
        "business_goal": "验证智能认证、核保接口、承保结果与风险保额处理符合当前产品规则。",
        "steps": [
            "填写触发核保或认证的投被保信息",
            "进入智能认证/智能核保流程",
            "校验核保接口返回与页面核保结果",
            "校验承保结果、风险保额处理和后续流程入口",
        ],
    },
    {
        "intent": "payment",
        "title": "签约支付与保费阈值链路",
        "priority": "P0",
        "scenario_type": "core_branch",
        "keywords": ("支付", "签约", "银行签约", "首期保费", "保费等于", "保费大于", "保费小于"),
        "business_goal": "验证签约支付入口、保费阈值触发认证规则和订单支付状态流转。",
        "steps": [
            "完成核保或认证后进入支付/签约页面",
            "校验银行签约和支付入口展示",
            "按保费阈值校验影像件/OCR规则",
            "提交支付并校验订单状态",
        ],
    },
    {
        "intent": "policy",
        "title": "出单后电子保单数据一致性校验",
        "priority": "P1",
        "scenario_type": "important_branch",
        "required": True,
        "keywords": ("电子保单", "保单文件", "回访", "在线回访", "保单"),
        "business_goal": "验证出单后电子保单可查看，且投保提交数据与电子保单展示数据一致。",
        "steps": [
            "完成出单或进入保单结果页",
            "查看电子保单/保单文件",
            "比对投保提交数据与电子保单关键字段",
            "进入在线回访入口",
            "校验保单与回访信息展示正确",
        ],
        "required_assertions": [
            "电子保单展示的投保人、被保险人、产品/计划、保额、保费、缴费期间、保险期间、生效日期等关键字段与投保提交数据一致。"
        ],
    },
    {
        "intent": "surrender",
        "title": "撤单与退保关键链路",
        "priority": "P1",
        "scenario_type": "important_branch",
        "keywords": ("退保", "撤单", "生效前撤单", "犹豫期内", "犹豫期外", "撤销核保"),
        "business_goal": "验证支付后未生效撤单、犹豫期内退保和犹豫期外退保的状态流转。",
        "steps": [
            "准备不同保单状态的数据",
            "发起生效前撤单或犹豫期退保",
            "提交退保申请",
            "校验退保结果和订单/保单状态",
        ],
    },
)
_TRAVEL_BUSINESS_SCENARIOS: tuple[dict[str, Any], ...] = (
    {
        **_BUSINESS_SCENARIOS[0],
        "title": "境外旅游险标准投保成功主链路",
        "keywords": (
            *_BUSINESS_SCENARIOS[0]["keywords"],
            "旅游",
            "旅行",
            "境外",
            "出行",
            "目的地",
            "旅行日期",
            "保障期间",
            "旅客",
            "试算",
        ),
        "business_goal": "验证客户从旅游险产品详情进入投保，完成出行目的地、旅行期间、旅客信息、保费试算、提交承保并形成电子保单结果。",
        "steps": [
            "进入境外旅游险产品详情页并选择销售计划",
            "填写出行目的地、旅行日期和保障期间",
            "填写投保人、被保旅客和证件信息",
            "完成保费试算、必要声明和认证",
            "提交投保申请并校验承保/电子保单结果",
        ],
    },
    {
        **_BUSINESS_SCENARIOS[1],
        "title": "旅行声明与风险提示确认链路",
        "keywords": (
            *_BUSINESS_SCENARIOS[1]["keywords"],
            "旅游",
            "旅行",
            "境外",
            "出行",
            "风险提示",
            "声明",
            "告知",
        ),
        "business_goal": "验证旅游险投保过程中的投保声明、风险提示和必要告知确认规则正确。",
        "steps": [
            "进入旅游险投保声明或风险提示页面",
            "确认目的地、旅行期间和保障责任提示",
            "完成必要声明、告知和确认动作",
            "校验未确认时阻断、确认后可继续提交",
        ],
    },
    {
        **_BUSINESS_SCENARIOS[2],
        "title": "旅客证件与出行信息联动链路",
        "keywords": (
            *_BUSINESS_SCENARIOS[2]["keywords"],
            "旅客",
            "护照",
            "证件",
            "目的地",
            "出行",
            "旅行日期",
            "保障期间",
        ),
        "business_goal": "验证旅客证件类型、证件信息、出行目的地、旅行日期和保障期间的联动、必填和阻断规则。",
        "steps": [
            "进入投保信息填写页",
            "切换投保人/被保旅客证件类型",
            "填写出行目的地、旅行日期和旅客证件信息",
            "提交并校验证件、目的地和保障期间规则",
        ],
    },
    {
        **_BUSINESS_SCENARIOS[3],
        "title": "旅游险承保/风控审核关键链路",
        "keywords": (
            *_BUSINESS_SCENARIOS[3]["keywords"],
            "承保",
            "风控",
            "目的地",
            "旅行",
            "境外",
        ),
        "business_goal": "验证旅游险承保、风控审核、目的地限制和后续流程入口符合当前产品规则。",
        "steps": [
            "填写触发承保或风控校验的旅行投保信息",
            "提交旅游险投保申请",
            "校验承保接口返回与页面承保结果",
            "校验风控提示、阻断和后续流程入口",
        ],
    },
    {
        **_BUSINESS_SCENARIOS[4],
        "title": "旅游险支付与保费阈值链路",
        "business_goal": "验证旅游险保费试算、支付入口、保费阈值和支付结果处理正确。",
        "steps": [
            "选择不同旅行计划、目的地和保障期间",
            "校验保费试算结果",
            "进入支付或签约流程",
            "校验支付结果、订单状态和后续出单入口",
        ],
    },
    {
        **_BUSINESS_SCENARIOS[5],
        "title": "旅游险电子保单数据一致性校验",
        "business_goal": "验证电子保单中的旅客、目的地、旅行期间、保障责任、保费和生效信息与投保提交数据一致。",
        "required_assertions": [
            "电子保单展示的投保人、被保旅客、旅行目的地、保障期间、保费、保障责任和生效日期等关键字段与投保提交数据一致。"
        ],
    },
    {
        **_BUSINESS_SCENARIOS[6],
        "title": "旅游险撤单与退保关键链路",
        "business_goal": "验证旅游险支付后未生效撤单、旅行前退保和保单状态流转正确。",
        "steps": [
            "准备不同旅游险保单状态的数据",
            "发起生效前撤单或退保",
            "提交撤单/退保申请",
            "校验退款、退保结果和订单/保单状态",
        ],
    },
)
_XML_NS = {
    "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}
_XMIND_NS = {"x": "urn:xmind:xmap:xmlns:content:2.0"}


def normalise_text(value: str) -> str:
    lowered = value.strip().lower()
    collapsed = re.sub(r"\s+", "", lowered)
    return re.sub(r"[^\w\u4e00-\u9fff]", "", collapsed)


def unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        item = str(value).strip()
        if not item:
            continue
        key = normalise_text(item)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def text_similarity(left: str, right: str) -> float:
    left_norm = normalise_text(left)
    right_norm = normalise_text(right)
    if not left_norm or not right_norm:
        return 0.0
    if left_norm == right_norm:
        return 1.0
    ratio = SequenceMatcher(None, left_norm, right_norm).ratio()
    shorter, longer = sorted((left_norm, right_norm), key=len)
    contains_score = len(shorter) / len(longer) if shorter in longer else 0.0
    chars_left = set(left_norm)
    chars_right = set(right_norm)
    overlap = len(chars_left & chars_right) / max(len(chars_left | chars_right), 1)
    return max(ratio, contains_score * 0.95, overlap * 0.85)


def _is_travel_context(*values: Any) -> bool:
    text = normalise_text(" ".join(str(value or "") for value in values))
    return any(token in text for token in ("travel", "tour", "旅游", "旅行", "境外", "出行", "旅客"))


def _business_scenarios_for_context(*values: Any) -> tuple[dict[str, Any], ...]:
    if _is_travel_context(*values):
        return _TRAVEL_BUSINESS_SCENARIOS
    return _BUSINESS_SCENARIOS


def list_similarity(left: list[str], right: list[str]) -> float:
    if not left or not right:
        return 0.0
    return text_similarity(" ".join(left), " ".join(right))


def parse_priority(*values: str) -> str:
    for value in values:
        match = _PRIORITY_RE.search(value or "")
        if match:
            return match.group(1).upper()
    return "P0"


def _split_blocks(text: str) -> list[tuple[str, list[str]]]:
    title = "Untitled Case"
    buffer: list[str] = []
    blocks: list[tuple[str, list[str]]] = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if _TITLE_LINE_RE.match(line.strip()):
            if buffer:
                blocks.append((title, buffer))
            title = re.sub(r"^#{1,6}\s*", "", line).strip()
            buffer = []
            continue
        buffer.append(line)
    if buffer or not blocks:
        blocks.append((title, buffer))
    return blocks


def _extract_list_items(lines: list[str], aliases: tuple[str, ...]) -> list[str]:
    results: list[str] = []
    current_section = False
    for raw_line in lines:
        stripped = raw_line.strip()
        normalised = normalise_text(stripped)
        if not stripped:
            current_section = False
            continue
        if any(alias in normalised for alias in aliases):
            inline = re.split(r"[:：]", stripped, maxsplit=1)
            if len(inline) == 2 and inline[1].strip():
                results.append(inline[1].strip())
            current_section = True
            continue
        if current_section and _BULLET_RE.match(stripped):
            results.append(_BULLET_RE.sub("", stripped).strip())
            continue
        if current_section and not _TITLE_LINE_RE.match(stripped):
            results.append(stripped)
    return unique_strings(results)


def _fallback_steps(lines: list[str]) -> list[str]:
    items = [
        _BULLET_RE.sub("", line.strip()).strip()
        for line in lines
        if _BULLET_RE.match(line.strip())
    ]
    return unique_strings(items)


def parse_markdown_case_blocks(text: str, source_path: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for index, (title, lines) in enumerate(_split_blocks(text), start=1):
        preconditions = _extract_list_items(lines, ("前置条件", "precondition"))
        steps = _extract_list_items(lines, ("步骤", "step", "操作"))
        assertions = _extract_list_items(lines, ("预期", "断言", "assert", "expected"))
        if not steps:
            steps = _fallback_steps(lines)
        if not assertions:
            assertions = [f"Expected outcome matches case intent: {title}"]
        case_title = title or f"{source_path.stem}-{index}"
        cases.append(
            {
                "case_id": f"MANUAL-{source_path.stem}-{index:03d}",
                "title": case_title,
                "source": "manual",
                "priority": parse_priority(case_title, "\n".join(lines)),
                "steps": steps or [f"执行人工场景：{case_title}"],
                "assertions": assertions,
                "preconditions": preconditions,
                "tags": [f"manual-file:{source_path.name}", "manual-format:markdown"],
            }
        )
    return cases


def parse_json_cases(payload: Any, source_path: Path) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        raw_cases = payload["cases"] if isinstance(payload.get("cases"), list) else [payload]
    elif isinstance(payload, list):
        raw_cases = payload
    else:
        return []

    cases: list[dict[str, Any]] = []
    for index, item in enumerate(raw_cases, start=1):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("name") or f"{source_path.stem}-{index}")
        raw_steps = item.get("steps", [])
        if isinstance(raw_steps, str):
            raw_steps = [raw_steps]
        steps = [str(step) for step in raw_steps if str(step).strip()]
        raw_assertions = item.get("assertions", item.get("expected", []))
        if isinstance(raw_assertions, str):
            raw_assertions = [raw_assertions]
        assertions = [str(assertion) for assertion in raw_assertions if str(assertion).strip()]
        preconditions = [
            str(value)
            for value in item.get("preconditions", item.get("precondition", []))
            if str(value).strip()
        ]
        tags = [str(value) for value in item.get("tags", []) if str(value).strip()]
        if not isinstance(item.get("preconditions"), list) and item.get("precondition"):
            preconditions = [str(item["precondition"])]
        if not assertions:
            expected = item.get("expected")
            if isinstance(expected, str) and expected.strip():
                assertions = [expected.strip()]
        cases.append(
            {
                "case_id": str(item.get("case_id") or f"MANUAL-{source_path.stem}-{index:03d}"),
                "title": title,
                "source": "manual",
                "priority": parse_priority(str(item.get("priority", "")), title),
                "steps": unique_strings(steps) or [f"执行人工场景：{title}"],
                "assertions": unique_strings(assertions) or [f"Expected outcome matches case intent: {title}"],
                "preconditions": unique_strings(preconditions),
                "tags": unique_strings(tags + [f"manual-file:{source_path.name}", "manual-format:json"]),
            }
        )
    return cases


def parse_xlsx_cases(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    rows = _read_xlsx_rows(path)
    if not rows:
        return [], [f"No readable rows found in manual case workbook: {path.name}"]

    header = [normalise_text(cell) for cell in rows[0]]
    column_aliases = {
        "title": {"title", "name", "用例名称", "用例标题", "标题"},
        "steps": {"steps", "step", "步骤", "操作"},
        "assertions": {"assertions", "assert", "expected", "预期", "断言"},
        "preconditions": {"preconditions", "precondition", "前置条件"},
        "priority": {"priority", "优先级"},
        "case_id": {"caseid", "id", "用例id"},
    }

    indexes: dict[str, int] = {}
    for field, aliases in column_aliases.items():
        for index, column_name in enumerate(header):
            if column_name in {normalise_text(alias) for alias in aliases}:
                indexes[field] = index
                break

    if "title" not in indexes:
        return [], [f"Manual case workbook missing title column: {path.name}"]

    cases: list[dict[str, Any]] = []
    for row_index, row in enumerate(rows[1:], start=1):
        title = _cell_value(row, indexes.get("title"))
        if not title:
            continue
        steps = _split_multivalue(_cell_value(row, indexes.get("steps")))
        assertions = _split_multivalue(_cell_value(row, indexes.get("assertions")))
        preconditions = _split_multivalue(_cell_value(row, indexes.get("preconditions")))
        priority = _cell_value(row, indexes.get("priority")) or parse_priority(title)
        case_id = _cell_value(row, indexes.get("case_id")) or f"MANUAL-{path.stem}-{row_index:03d}"
        cases.append(
            {
                "case_id": case_id,
                "title": title,
                "source": "manual",
                "priority": parse_priority(priority, title),
                "steps": steps or [f"执行人工场景：{title}"],
                "assertions": assertions or [f"Expected outcome matches case intent: {title}"],
                "preconditions": preconditions,
                "tags": [f"manual-file:{path.name}", "manual-format:xlsx"],
            }
        )
    return cases, []


def parse_xmind_cases(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        if "content.json" in names:
            return _parse_xmind_content_json(json.loads(archive.read("content.json").decode("utf-8")), path), []
        if "content.xml" in names:
            root = ET.fromstring(archive.read("content.xml"))
            return _parse_xmind_content_xml(root, path), []
    return [], [f"XMind archive missing content.xml/content.json: {path.name}"]


def _parse_xmind_content_json(payload: Any, path: Path) -> list[dict[str, Any]]:
    sheets = payload if isinstance(payload, list) else [payload]
    cases: list[dict[str, Any]] = []
    for sheet in sheets:
        root_topic = sheet.get("rootTopic") if isinstance(sheet, dict) else None
        if isinstance(root_topic, dict):
            _collect_xmind_json_cases(root_topic, [], [], cases, path)
    return cases


def _collect_xmind_json_cases(
    topic: dict[str, Any],
    ancestors: list[str],
    ancestor_markers: list[str],
    cases: list[dict[str, Any]],
    path: Path,
) -> None:
    title = str(topic.get("title") or "").strip()
    markers = topic.get("markers", []) or []
    labels = topic.get("labels", []) or []
    children = []
    for item in (topic.get("children") or {}).get("attached", []) or []:
        if isinstance(item, dict):
            children.append(item)

    if _is_xmind_case_topic(title, labels, children):
        cases.append(_xmind_case_record(path, len(cases) + 1, title, ancestors, labels, markers, ancestor_markers))
    for child in children:
        _collect_xmind_json_cases(child, ancestors + ([title] if title else []), ancestor_markers + markers, cases, path)


def _parse_xmind_content_xml(root: ET.Element, path: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for sheet in root.findall("x:sheet", _XMIND_NS):
        root_topic = sheet.find("x:topic", _XMIND_NS)
        if root_topic is not None:
            _collect_xmind_xml_cases(root_topic, [], [], cases, path)
    return cases


def _collect_xmind_xml_cases(
    topic: ET.Element,
    ancestors: list[str],
    ancestor_markers: list[str],
    cases: list[dict[str, Any]],
    path: Path,
) -> None:
    title = _xmind_xml_title(topic)
    labels = _xmind_xml_labels(topic)
    markers = _xmind_xml_markers(topic)
    children = topic.findall("x:children/x:topics/x:topic", _XMIND_NS)

    if _is_xmind_case_topic(title, labels, children):
        cases.append(_xmind_case_record(path, len(cases) + 1, title, ancestors, labels, markers, ancestor_markers))
    for child in children:
        _collect_xmind_xml_cases(child, ancestors + ([title] if title else []), ancestor_markers + markers, cases, path)


def _xmind_xml_title(topic: ET.Element) -> str:
    title_node = topic.find("x:title", _XMIND_NS)
    return "".join(title_node.itertext()).strip() if title_node is not None else ""


def _xmind_xml_labels(topic: ET.Element) -> list[str]:
    return [
        "".join(label.itertext()).strip()
        for label in topic.findall("x:labels/x:label", _XMIND_NS)
        if "".join(label.itertext()).strip()
    ]


def _xmind_xml_markers(topic: ET.Element) -> list[str]:
    return [
        str(marker.attrib.get("marker-id") or "")
        for marker in topic.findall("x:marker-refs/x:marker-ref", _XMIND_NS)
        if marker.attrib.get("marker-id")
    ]


def _is_xmind_case_topic(title: str, labels: list[str], children: list[Any]) -> bool:
    if not title:
        return False
    if children:
        return False
    normalised_labels = normalise_text(" ".join(labels))
    normalised_title = normalise_text(title)
    if any(token in normalised_labels for token in ("用例", "case", "测试")):
        return True
    if any(token in normalised_title for token in ("用例", "case", "场景")):
        return True
    return len(title) >= 4


def _xmind_priority(markers: list[str], title: str) -> str:
    marker_text = " ".join(markers)
    if "priority-1" in marker_text:
        return "P0"
    if "priority-2" in marker_text:
        return "P1"
    if "priority-3" in marker_text:
        return "P2"
    explicit_priority = _PRIORITY_RE.search(title or "")
    if explicit_priority:
        return explicit_priority.group(1).upper()
    return "P2"


def _xmind_case_record(
    path: Path,
    index: int,
    title: str,
    ancestors: list[str],
    labels: list[str],
    markers: list[str],
    ancestor_markers: list[str],
) -> dict[str, Any]:
    context = [item for item in ancestors[1:] if item] or [item for item in ancestors if item]
    manual_path = unique_strings(context + [title])
    business_context = context[0] if context else title
    steps = unique_strings([f"进入 {item}" for item in context] + [f"执行用例：{title}"])
    assertions = [f"用例结果符合预期：{title}"]
    return {
        "case_id": f"MANUAL-{path.stem}-{index:03d}",
        "title": title,
        "source": "manual",
        "priority": _xmind_priority(markers + ancestor_markers, " ".join(manual_path)),
        "steps": steps,
        "assertions": assertions,
        "preconditions": unique_strings(context[:1]),
        "manual_path": manual_path,
        "business_context": business_context,
        "tags": unique_strings(
            [
                f"manual-file:{path.name}",
                "manual-format:xmind",
                *[f"xmind-label:{label}" for label in labels],
            ]
        ),
    }


def _read_xlsx_rows(path: Path) -> list[list[str]]:
    with zipfile.ZipFile(path) as archive:
        shared_strings = _read_shared_strings(archive)
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        sheets = workbook.find("main:sheets", _XML_NS)
        if sheets is None or not list(sheets):
            return []
        first_sheet = list(sheets)[0]
        rel_id = first_sheet.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
        rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        target = None
        for rel in rels.findall("rel:Relationship", _XML_NS):
            if rel.attrib.get("Id") == rel_id:
                target = rel.attrib.get("Target")
                break
        if not target:
            return []
        sheet_xml = ET.fromstring(archive.read(f"xl/{target.lstrip('/').removeprefix('xl/')}"))
        rows: list[list[str]] = []
        for row in sheet_xml.findall(".//main:sheetData/main:row", _XML_NS):
            values: list[str] = []
            for cell in row.findall("main:c", _XML_NS):
                values.append(_xlsx_cell_value(cell, shared_strings))
            rows.append(values)
        return rows


def _read_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    values: list[str] = []
    for si in root.findall("main:si", _XML_NS):
        text_parts = [node.text or "" for node in si.findall(".//main:t", _XML_NS)]
        values.append("".join(text_parts))
    return values


def _xlsx_cell_value(cell: ET.Element, shared_strings: list[str]) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        parts = [node.text or "" for node in cell.findall(".//main:t", _XML_NS)]
        return "".join(parts).strip()
    value_node = cell.find("main:v", _XML_NS)
    if value_node is None or value_node.text is None:
        return ""
    raw = value_node.text.strip()
    if cell_type == "s":
        try:
            return shared_strings[int(raw)].strip()
        except (ValueError, IndexError):
            return ""
    return raw


def _cell_value(row: list[str], index: int | None) -> str:
    if index is None or index >= len(row):
        return ""
    return str(row[index]).strip()


def _split_multivalue(value: str) -> list[str]:
    if not value:
        return []
    parts = re.split(r"(?:\r?\n|；|;|\|)", value)
    return unique_strings([part.strip(" -*+•") for part in parts if part.strip(" -*+•")])


def load_manual_cases(path_value: str | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    if not path_value:
        return [], [], []
    root = Path(path_value)
    if not root.exists():
        return [], [], [f"manual_cases_path not found: {root}"]

    paths = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file())
    cases: list[dict[str, Any]] = []
    parse_trace: list[dict[str, Any]] = []
    warnings: list[str] = []

    for file_path in paths:
        suffix = file_path.suffix.lower()
        trace_item = {
            "source_path": str(file_path),
            "format": suffix.lstrip(".") or "unknown",
            "parser": "",
            "status": "parsed",
            "case_count": 0,
        }

        if suffix not in _SUPPORTED_MANUAL_CASE_SUFFIXES:
            warnings.append(f"Skipped unsupported manual case format: {file_path.name}")
            trace_item["parser"] = "unsupported"
            trace_item["status"] = "skipped"
            parse_trace.append(trace_item)
            continue

        try:
            if suffix == ".json":
                raw_text = _read_text(file_path)
                parsed = parse_json_cases(json.loads(raw_text), file_path)
                trace_item["parser"] = "json-loader"
            elif suffix in {".md", ".markdown", ".txt"}:
                parsed = parse_markdown_case_blocks(_read_text(file_path), file_path)
                trace_item["parser"] = "markdown-blocks"
            elif suffix == ".xlsx":
                parsed, file_warnings = parse_xlsx_cases(file_path)
                warnings.extend(file_warnings)
                trace_item["parser"] = "xlsx-tabular"
            elif suffix == ".xmind":
                parsed, file_warnings = parse_xmind_cases(file_path)
                warnings.extend(file_warnings)
                trace_item["parser"] = "xmind-content"
            elif suffix == ".km":
                parsed = []
                warnings.append(f"Adapter stub only for manual case format: {file_path.name}")
                trace_item["parser"] = "km adapter stub"
                trace_item["status"] = "stub_adapter"
            else:
                parsed = []
                trace_item["parser"] = "unknown"
                trace_item["status"] = "skipped"
        except json.JSONDecodeError as exc:
            warnings.append(f"Failed to parse manual case JSON {file_path.name}: {exc}")
            parsed = []
            trace_item["parser"] = "json-loader"
            trace_item["status"] = "error"
        except (OSError, KeyError, ET.ParseError, zipfile.BadZipFile) as exc:
            warnings.append(f"Failed to parse manual case file {file_path.name}: {exc}")
            parsed = []
            trace_item["parser"] = trace_item["parser"] or "file-parser"
            trace_item["status"] = "error"

        trace_item["case_count"] = len(parsed)
        parse_trace.append(trace_item)
        cases.extend(parsed)

    return cases, parse_trace, warnings


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8")


def build_ac_cases(prd_analysis: dict[str, Any]) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for feature in prd_analysis.get("features", []):
        feature_name = str(feature.get("name") or "Unnamed Feature")
        priority = parse_priority(str(feature.get("priority", "")), feature_name)
        for index, criterion in enumerate(feature.get("acceptance_criteria", []), start=1):
            criterion_text = str(criterion).strip()
            if not criterion_text:
                continue
            cases.append(
                {
                    "case_id": f"AC-{feature.get('feature_id', 'FEAT')}-{index:03d}",
                    "title": f"{feature_name} - {criterion_text[:48]}",
                    "source": "ac",
                    "priority": priority,
                    "steps": [
                        f"Navigate to feature context: {feature_name}",
                        f"Execute the acceptance scenario: {criterion_text}",
                    ],
                    "assertions": [criterion_text],
                    "preconditions": [f"Feature ready: {feature_name}"],
                    "tags": [f"feature:{feature_name}", "origin:prd-ac"],
                }
            )
    return cases


def ensure_skeleton_cases(
    *,
    product_id: str,
    prd_path: str,
    prd_analysis: dict[str, Any] | None,
    skeleton_cases: list[dict[str, Any]] | None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    analysis = prd_analysis if isinstance(prd_analysis, dict) else None
    skeleton = skeleton_cases if isinstance(skeleton_cases, list) else None

    if analysis is None:
        analysis, prd_warnings = parse_prd_analysis_from_path(prd_path, product_id)
        warnings.extend(prd_warnings)
    if skeleton is None:
        skeleton = build_stage1_skeleton(analysis)
    return analysis, skeleton, warnings


def normalise_stage1_cases(skeleton_cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    stage1_cases: list[dict[str, Any]] = []
    for item in skeleton_cases:
        raw_steps = item.get("steps", [])
        steps: list[str] = []
        assertions: list[str] = []
        for raw_step in raw_steps:
            if isinstance(raw_step, dict):
                action = str(raw_step.get("action") or "").strip()
                expected = str(raw_step.get("expected") or "").strip()
                if action:
                    steps.append(action)
                if expected:
                    assertions.append(expected)
            else:
                text = str(raw_step).strip()
                if text:
                    steps.append(text)
        title = str(item.get("title") or item.get("id") or "Stage1 Skeleton").strip()
        stage1_cases.append(
            {
                "case_id": str(item.get("id") or f"TC-STAGE1-{len(stage1_cases) + 1:03d}"),
                "title": title,
                "source": "ac",
                "priority": parse_priority(str(item.get("priority", "")), title),
                "steps": unique_strings(steps) or [f"执行骨架用例：{title}"],
                "assertions": unique_strings(assertions) or [f"Expected skeleton outcome matches case intent: {title}"],
                "preconditions": unique_strings(
                    [str(value) for value in item.get("preconditions", []) if str(value).strip()]
                ),
                "tags": unique_strings([f"feature:{item.get('feature_id', '')}", "origin:tc-gen-stage1"]),
            }
        )
    return stage1_cases


def pair_score(manual_case: dict[str, Any], ac_case: dict[str, Any]) -> float:
    title_score = text_similarity(manual_case["title"], ac_case["title"])
    assertion_score = list_similarity(manual_case.get("assertions", []), ac_case.get("assertions", []))
    step_score = list_similarity(manual_case.get("steps", []), ac_case.get("steps", []))
    return title_score * 0.55 + assertion_score * 0.3 + step_score * 0.15


def select_pairs(
    manual_cases: list[dict[str, Any]],
    ac_cases: list[dict[str, Any]],
    threshold: float = 0.58,
) -> tuple[list[tuple[int, int, float]], dict[int, tuple[int, float]]]:
    candidates: list[tuple[float, int, int]] = []
    best_manual_for_ac: dict[int, tuple[int, float]] = {}
    for manual_index, manual_case in enumerate(manual_cases):
        for ac_index, ac_case in enumerate(ac_cases):
            score = pair_score(manual_case, ac_case)
            if score > best_manual_for_ac.get(ac_index, (-1, -1.0))[1]:
                best_manual_for_ac[ac_index] = (manual_index, score)
            candidates.append((score, manual_index, ac_index))

    matches: list[tuple[int, int, float]] = []
    used_manual: set[int] = set()
    used_ac: set[int] = set()
    for score, manual_index, ac_index in sorted(candidates, reverse=True):
        if score < threshold or manual_index in used_manual or ac_index in used_ac:
            continue
        used_manual.add(manual_index)
        used_ac.add(ac_index)
        matches.append((manual_index, ac_index, score))
    return matches, best_manual_for_ac


def merge_field(manual_values: list[str], ac_values: list[str]) -> list[str]:
    return unique_strings(list(manual_values) + list(ac_values))


def _conflict_policy(conflict_type: str) -> str:
    return {
        "assertion_mismatch": "needs-product-confirmation",
        "step_contradiction": "temporary-override",
        "precondition_conflict": "needs-product-confirmation",
        "scope_overlap": "update-human-case",
        "missing_coverage": "update-human-case",
    }[conflict_type]


def _next_conflict_id(counter: int) -> str:
    return f"CONFLICT-{counter:03d}"


def _build_conflict(
    counter: int,
    conflict_type: str,
    description: str,
    manual_case_id: str | None,
    ac_case_id: str | None,
) -> dict[str, Any]:
    return {
        "conflict_id": _next_conflict_id(counter),
        "type": conflict_type,
        "policy": _conflict_policy(conflict_type),
        "manual_case_id": manual_case_id,
        "ac_case_id": ac_case_id,
        "description": description,
        "resolution_note": None,
        "resolved": False,
    }


def _build_final_case_id(product_id: str, sequence: int) -> str:
    safe_product = re.sub(r"[^A-Za-z0-9]+", "-", product_id).strip("-") or "product"
    return f"TC-{safe_product}-{sequence:03d}"


def merge_cases(
    product_id: str,
    manual_cases: list[dict[str, Any]],
    ac_cases: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    merged_cases: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    final_sequence = 1
    conflict_sequence = 1
    matches, best_manual_for_ac = select_pairs(manual_cases, ac_cases)
    matched_manual_indexes = {manual_index for manual_index, _, _ in matches}
    matched_ac_indexes = {ac_index for _, ac_index, _ in matches}

    for manual_index, ac_index, score in matches:
        manual_case = manual_cases[manual_index]
        ac_case = ac_cases[ac_index]
        related_conflicts: list[str] = []
        assertion_similarity = list_similarity(manual_case.get("assertions", []), ac_case.get("assertions", []))
        step_similarity = list_similarity(manual_case.get("steps", []), ac_case.get("steps", []))
        precondition_similarity = list_similarity(
            manual_case.get("preconditions", []),
            ac_case.get("preconditions", []),
        )

        if assertion_similarity < 0.25:
            conflict = _build_conflict(
                conflict_sequence,
                "assertion_mismatch",
                f"人工用例《{manual_case['title']}》与 PRD 骨架用例《{ac_case['title']}》的预期断言存在明显差异。",
                manual_case["case_id"],
                ac_case["case_id"],
            )
            conflict_sequence += 1
            conflicts.append(conflict)
            related_conflicts.append(conflict["conflict_id"])
        if step_similarity < 0.18 and score < 0.9:
            conflict = _build_conflict(
                conflict_sequence,
                "step_contradiction",
                f"人工用例《{manual_case['title']}》与 PRD 骨架用例《{ac_case['title']}》的执行步骤路径不一致。",
                manual_case["case_id"],
                ac_case["case_id"],
            )
            conflict_sequence += 1
            conflicts.append(conflict)
            related_conflicts.append(conflict["conflict_id"])
        if manual_case.get("preconditions") and ac_case.get("preconditions") and precondition_similarity < 0.2:
            conflict = _build_conflict(
                conflict_sequence,
                "precondition_conflict",
                f"人工用例《{manual_case['title']}》与 PRD 骨架用例《{ac_case['title']}》要求的前置条件不一致。",
                manual_case["case_id"],
                ac_case["case_id"],
            )
            conflict_sequence += 1
            conflicts.append(conflict)
            related_conflicts.append(conflict["conflict_id"])

        merged_cases.append(
            {
                "case_id": _build_final_case_id(product_id, final_sequence),
                "title": manual_case["title"],
                "source": "merged",
                "priority": manual_case.get("priority") or ac_case.get("priority", "P0"),
                "steps": merge_field(manual_case.get("steps", []), ac_case.get("steps", [])),
                "assertions": merge_field(manual_case.get("assertions", []), ac_case.get("assertions", [])),
                "preconditions": merge_field(manual_case.get("preconditions", []), ac_case.get("preconditions", [])),
                "conflict_ref": related_conflicts[0] if related_conflicts else None,
                "tags": merge_field(manual_case.get("tags", []), ac_case.get("tags", [])),
                "manual_path": manual_case.get("manual_path", []),
                "business_context": manual_case.get("business_context"),
                "manual_case_refs": [manual_case["case_id"]],
                "ac_case_refs": [ac_case["case_id"]],
                "candidate_refs": [
                    {"source": "manual", "case_id": manual_case["case_id"], "title": manual_case["title"]},
                    {"source": "prd-ac", "case_id": ac_case["case_id"], "title": ac_case["title"]},
                ],
            }
        )
        final_sequence += 1

    for ac_index, ac_case in enumerate(ac_cases):
        if ac_index in matched_ac_indexes:
            continue
        best_manual_index, best_score = best_manual_for_ac.get(ac_index, (-1, 0.0))
        conflict_type = "scope_overlap" if best_score >= 0.35 else "missing_coverage"
        manual_case_id = manual_cases[best_manual_index]["case_id"] if best_manual_index >= 0 else None
        description = (
            f"PRD 骨架用例《{ac_case['title']}》与现有人工用例范围相近，但相似度不足以自动合并。"
            if conflict_type == "scope_overlap"
            else f"PRD 骨架用例《{ac_case['title']}》没有匹配到人工用例覆盖。"
        )
        conflict = _build_conflict(
            conflict_sequence,
            conflict_type,
            description,
            manual_case_id,
            ac_case["case_id"],
        )
        conflict_sequence += 1
        conflicts.append(conflict)
        merged_cases.append(
            {
                "case_id": _build_final_case_id(product_id, final_sequence),
                "title": ac_case["title"],
                "source": "ac",
                "priority": ac_case.get("priority", "P0"),
                "steps": ac_case.get("steps", []),
                "assertions": ac_case.get("assertions", []),
                "preconditions": ac_case.get("preconditions", []),
                "conflict_ref": conflict["conflict_id"],
                "tags": ac_case.get("tags", []),
                "manual_path": [],
                "business_context": None,
                "manual_case_refs": [],
                "ac_case_refs": [ac_case["case_id"]],
                "candidate_refs": [
                    {"source": "prd-ac", "case_id": ac_case["case_id"], "title": ac_case["title"]},
                ],
            }
        )
        final_sequence += 1

    for manual_index, manual_case in enumerate(manual_cases):
        if manual_index in matched_manual_indexes:
            continue
        merged_cases.append(
            {
                "case_id": _build_final_case_id(product_id, final_sequence),
                "title": manual_case["title"],
                "source": "manual",
                "priority": manual_case.get("priority", "P0"),
                "steps": manual_case.get("steps", []),
                "assertions": manual_case.get("assertions", []),
                "preconditions": manual_case.get("preconditions", []),
                "conflict_ref": None,
                "tags": manual_case.get("tags", []),
                "manual_path": manual_case.get("manual_path", []),
                "business_context": manual_case.get("business_context"),
                "manual_case_refs": [manual_case["case_id"]],
                "ac_case_refs": [],
                "candidate_refs": [
                    {"source": "manual", "case_id": manual_case["case_id"], "title": manual_case["title"]},
                ],
            }
        )
        final_sequence += 1

    return merged_cases, conflicts


def _case_text(case: dict[str, Any]) -> str:
    parts: list[str] = [
        str(case.get("title") or ""),
        str(case.get("business_context") or ""),
    ]
    for field in ("manual_path", "steps", "assertions", "preconditions", "tags"):
        raw_values = case.get(field, []) or []
        if isinstance(raw_values, str):
            parts.append(raw_values)
            continue
        parts.extend(str(item) for item in raw_values)
    return " ".join(parts)


def _core_keyword(case: dict[str, Any]) -> str | None:
    text = _case_text(case)
    for keyword in _CORE_REGRESSION_KEYWORDS:
        if keyword in text:
            return keyword
    return None


def _priority_rank(priority: str) -> int:
    return {"P0": 0, "P1": 1, "P2": 2}.get(str(priority or "P2").upper(), 3)


def _keyword_hit(text_norm: str, keyword: str) -> bool:
    keyword_norm = normalise_text(keyword)
    return bool(keyword_norm and keyword_norm in text_norm)


def _scenario_score(case: dict[str, Any], scenario: dict[str, Any]) -> int:
    text = _case_text(case)
    text_norm = normalise_text(text)
    score = sum(2 for keyword in scenario["keywords"] if _keyword_hit(text_norm, str(keyword)))
    context = normalise_text(str(case.get("business_context") or ""))
    title = normalise_text(str(case.get("title") or ""))
    intent = str(scenario["intent"])

    if intent == "main_flow" and (
        "每个计划投保" in text
        or re.search(r"计划[0-9一二三四五六七八九十百]+", str(case.get("title") or "")) is not None
        or "出单" in text
        or "投保成功" in text
    ):
        score += 4
    if intent == "health_notice" and any(token in context + title for token in ("健康告知", "适当性问卷")):
        score += 4
    if intent == "tax_identity" and any(token in context + title for token in ("证件", "税收", "国籍")):
        score += 4
    if intent == "underwriting" and any(token in context + title for token in ("核保", "智能认证", "人核", "人工核保")):
        score += 4
    if intent == "payment" and any(token in context + title for token in ("支付", "签约")):
        score += 4
    if intent == "policy" and any(token in context + title for token in ("保单", "回访")):
        score += 4
    if intent == "surrender" and any(token in context + title for token in ("退保", "撤单", "撤销")):
        score += 4
    return score


def _scenario_for_case(
    case: dict[str, Any],
    scenarios: tuple[dict[str, Any], ...] = _BUSINESS_SCENARIOS,
) -> tuple[dict[str, Any] | None, int]:
    best: dict[str, Any] | None = None
    best_score = 0
    for scenario in scenarios:
        score = _scenario_score(case, scenario)
        if score > best_score:
            best = scenario
            best_score = score
    return best, best_score


def _matching_scenarios_for_case(
    case: dict[str, Any],
    scenarios: tuple[dict[str, Any], ...] = _BUSINESS_SCENARIOS,
) -> list[tuple[dict[str, Any], int]]:
    matches: list[tuple[dict[str, Any], int]] = []
    for scenario in scenarios:
        score = _scenario_score(case, scenario)
        if score > 0:
            matches.append((scenario, score))
    return sorted(matches, key=lambda item: item[1], reverse=True)


def _is_ac_like_case(case: dict[str, Any]) -> bool:
    source = str(case.get("source") or "")
    tags = [str(tag) for tag in case.get("tags", []) or []]
    return source == "ac" or bool(case.get("ac_case_refs")) or "origin:tc-gen-stage1" in tags or "origin:prd-ac" in tags


def _is_plan_variant_case(case: dict[str, Any]) -> bool:
    return any(variant["type"] == "plan" for variant in _extract_data_variants(case))


def _case_can_feed_core_scenario(case: dict[str, Any], scenario: dict[str, Any]) -> bool:
    priority = str(case.get("priority") or "P2").upper()
    if priority in {"P0", "P1"}:
        return True
    if _is_ac_like_case(case):
        return True
    if str(scenario.get("priority") or "") == "P0":
        if str(scenario.get("intent")) == "main_flow" and _is_plan_variant_case(case):
            return False
        return True
    return False


def _scenario_case_id(candidate_cases: list[dict[str, Any]], sequence: int) -> str:
    for case in candidate_cases:
        case_id = str(case.get("case_id") or "")
        match = re.match(r"^(.*?)-\d{3}$", case_id)
        if match:
            return f"{match.group(1)}-{sequence:03d}"
    return f"TC-core-regression-{sequence:03d}"


def _case_source_refs(case: dict[str, Any]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for ref in case.get("candidate_refs", []) or []:
        if isinstance(ref, dict):
            refs.append(
                {
                    "source": str(ref.get("source") or case.get("source") or "unknown"),
                    "case_id": str(ref.get("case_id") or case.get("case_id") or ""),
                    "title": str(ref.get("title") or case.get("title") or ""),
                    "priority": str(case.get("priority") or "P2"),
                }
            )
    if not refs:
        refs.append(
            {
                "source": str(case.get("source") or "unknown"),
                "case_id": str(case.get("case_id") or ""),
                "title": str(case.get("title") or ""),
                "priority": str(case.get("priority") or "P2"),
            }
        )
    return [item for item in refs if item["case_id"]]


def _coverage_refs(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for case in cases:
        for ref in _case_source_refs(case):
            key = (ref["source"], ref["case_id"])
            if key in seen:
                continue
            seen.add(key)
            refs.append(ref)
    return refs


def _manual_case_refs(cases: list[dict[str, Any]]) -> list[str]:
    refs: list[str] = []
    for case in cases:
        refs.extend(str(item) for item in case.get("manual_case_refs", []) or [] if str(item).strip())
        for ref in _case_source_refs(case):
            if ref["source"] == "manual":
                refs.append(ref["case_id"])
    return unique_strings(refs)


def _feature_name(case: dict[str, Any]) -> str | None:
    for tag in case.get("tags", []) or []:
        text = str(tag)
        if text.startswith("feature:"):
            return text.split(":", 1)[1]
    return None


def _rules_from_cases(cases: list[dict[str, Any]], scenario: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    seen: set[str] = set()
    for case in cases:
        if not _is_ac_like_case(case):
            continue
        feature = _feature_name(case) or str(case.get("title") or "")
        assertions = [str(item).strip() for item in case.get("assertions", []) or [] if str(item).strip()]
        case_matches_scenario = scenario is None or _scenario_score(case, scenario) > 0
        for index, assertion in enumerate(assertions, start=1):
            text = str(assertion).strip()
            assertion_matches_scenario = scenario is None or _scenario_score(
                {"title": text, "assertions": [text], "steps": [], "tags": []},
                scenario,
            ) > 0
            if scenario is not None and assertions and not assertion_matches_scenario:
                if len(assertions) > 1 or not case_matches_scenario:
                    continue
            key = normalise_text(f"{feature}:{text}")
            if not text or key in seen:
                continue
            seen.add(key)
            rules.append(
                {
                    "rule_id": f"RULE-{case.get('case_id')}-{index:02d}",
                    "feature": feature,
                    "text": text,
                    "source_case_id": str(case.get("case_id") or ""),
                }
            )
    return rules


_CERTIFICATE_VARIANTS = (
    "身份证",
    "出生证",
    "户口簿",
    "护照",
    "港澳居民居住证",
    "台湾居民居住证",
    "港澳通行证",
    "台胞证",
)
_UNDERWRITING_VARIANTS = ("标体", "除外", "拒保", "延期", "人核", "人工核保", "函件回销", "撤销核保")
_TAX_VARIANTS = ("中国税收居民", "非居民", "税收居民", "国籍", "出生地", "现居住地址")
_HEALTH_VARIANTS = ("健康告知", "适当性问卷", "风险警示书", "BMI")


def _variant_record(kind: str, value: str, case: dict[str, Any]) -> dict[str, Any]:
    record = {
        "type": kind,
        "value": value,
        "source_case_id": str(case.get("case_id") or ""),
    }
    manual_path = case.get("manual_path") or []
    if manual_path:
        record["manual_path"] = list(manual_path)
    return record


def _extract_data_variants(case: dict[str, Any]) -> list[dict[str, Any]]:
    text = _case_text(case)
    title = str(case.get("title") or "")
    variants: list[dict[str, Any]] = []
    if re.search(r"计划[0-9一二三四五六七八九十百]+", title):
        variants.append(_variant_record("plan", title, case))
    else:
        for plan in re.findall(r"计划[0-9一二三四五六七八九十百]+", text):
            variants.append(_variant_record("plan", plan, case))
    for age in re.findall(r"\d{1,3}\s*岁", text):
        variants.append(_variant_record("age", re.sub(r"\s+", "", age), case))
    for amount in re.findall(r"(?:保费|金额|首期保费)[^，。；;\n]{0,12}(?:大于|小于|等于|>=|<=|>|<|=)[^，。；;\n]{0,12}", text):
        variants.append(_variant_record("amount_rule", amount.strip(), case))
    for token in (*_CERTIFICATE_VARIANTS, *_UNDERWRITING_VARIANTS, *_TAX_VARIANTS, *_HEALTH_VARIANTS):
        if token in text:
            variants.append(_variant_record("business_value", token, case))

    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in variants:
        key = (str(item["type"]), normalise_text(str(item["value"])))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _data_variants(cases: list[dict[str, Any]], scenario: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    variants: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for case in cases:
        for variant in _extract_data_variants(case):
            if scenario is not None and _is_ac_like_case(case):
                variant_text = str(variant.get("value") or "")
                if _scenario_score({"title": variant_text, "assertions": [variant_text], "steps": [], "tags": []}, scenario) <= 0:
                    continue
            key = (str(variant["type"]), normalise_text(str(variant["value"])))
            if key in seen:
                continue
            seen.add(key)
            variants.append(variant)
    return variants


def _scenario_steps(scenario: dict[str, Any], cases: list[dict[str, Any]]) -> list[str]:
    steps = list(scenario["steps"])
    path_steps: list[str] = []
    for case in sorted(cases, key=lambda item: (_priority_rank(str(item.get("priority") or "P2")), str(item.get("case_id") or ""))):
        manual_path = [str(item) for item in case.get("manual_path", []) or [] if str(item).strip()]
        if manual_path:
            path_steps.append("覆盖人工路径：" + " > ".join(manual_path))
    return unique_strings(steps + path_steps[:8])


def _scenario_assertions(scenario: dict[str, Any], cases: list[dict[str, Any]], rules: list[dict[str, Any]]) -> list[str]:
    assertions: list[str] = [str(scenario["business_goal"])]
    assertions.extend(
        str(item)
        for item in scenario.get("required_assertions", []) or []
        if str(item).strip()
    )
    for case in sorted(cases, key=lambda item: (_priority_rank(str(item.get("priority") or "P2")), str(item.get("case_id") or ""))):
        if _is_ac_like_case(case):
            continue
        assertions.extend(str(item) for item in case.get("assertions", []) or [] if str(item).strip())
    assertions.extend(str(rule["text"]) for rule in rules)
    return unique_strings(assertions)


def _compose_scenario_case(
    *,
    case_id: str,
    scenario: dict[str, Any],
    cases: list[dict[str, Any]],
    conflicts_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    ordered_cases = sorted(cases, key=lambda item: (_priority_rank(str(item.get("priority") or "P2")), str(item.get("case_id") or "")))
    rules = _rules_from_cases(ordered_cases, scenario)
    conflict_refs = unique_strings(
        [
            str(case.get("conflict_ref"))
            for case in ordered_cases
            if case.get("conflict_ref") and str(case.get("conflict_ref")) in conflicts_by_id
        ]
    )
    priority = str(scenario["priority"])

    return {
        "case_id": case_id,
        "title": str(scenario["title"]),
        "source": "merged",
        "priority": priority,
        "scenario_type": str(scenario["scenario_type"]),
        "business_intent": str(scenario["intent"]),
        "business_goal": str(scenario["business_goal"]),
        "steps": _scenario_steps(scenario, ordered_cases),
        "assertions": _scenario_assertions(scenario, ordered_cases, rules),
        "rules": rules,
        "coverage_refs": _coverage_refs(ordered_cases),
        "data_variants": _data_variants(ordered_cases, scenario),
        "manual_case_refs": _manual_case_refs(ordered_cases),
        "preconditions": unique_strings(
            [
                str(item)
                for case in ordered_cases
                for item in case.get("preconditions", []) or []
                if str(item).strip()
            ]
        ),
        "conflict_ref": conflict_refs[0] if conflict_refs else None,
        "tags": unique_strings(
            [
                "selection:business-scenario-orchestration",
                f"business-intent:{scenario['intent']}",
                f"scenario-type:{scenario['scenario_type']}",
                *[
                    str(tag)
                    for case in ordered_cases
                    for tag in case.get("tags", []) or []
                    if str(tag).strip()
                ],
            ]
        ),
        "selection_reason": "business_scenario_orchestration",
        "selection_score": 300 - _priority_rank(priority) * 20 + min(len(ordered_cases), 20),
    }


def _absorbed_reason(case: dict[str, Any], scenario: dict[str, Any]) -> str:
    if scenario["intent"] == "main_flow" and any(variant["type"] == "plan" for variant in _extract_data_variants(case)):
        return "data_variant_absorbed"
    source = str(case.get("source") or "")
    if source == "ac" or case.get("ac_case_refs"):
        return "field_rule_absorbed_as_assertion"
    return "absorbed_into_core_scenario"


def select_regression_cases(
    candidate_cases: list[dict[str, Any]],
    conflicts: list[dict[str, Any]],
    *,
    max_cases: int = 30,
    product_id: str | None = None,
    product_name: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    conflicts_by_id = {
        str(conflict.get("conflict_id")): conflict
        for conflict in conflicts
        if conflict.get("conflict_id")
    }
    context_text = " ".join(
        [
            str(product_id or ""),
            str(product_name or ""),
            " ".join(str(case.get("title") or "") for case in candidate_cases),
            " ".join(
                str(item)
                for case in candidate_cases
                for item in [*(case.get("steps", []) or []), *(case.get("assertions", []) or []), *(case.get("tags", []) or [])]
            ),
        ]
    )
    business_scenarios = _business_scenarios_for_context(context_text)
    scenario_profile = "travel" if business_scenarios is _TRAVEL_BUSINESS_SCENARIOS else "default"
    buckets: dict[str, list[dict[str, Any]]] = {str(item["intent"]): [] for item in business_scenarios}
    scenario_by_intent = {str(item["intent"]): item for item in business_scenarios}
    unmatched: list[dict[str, Any]] = []
    assignment_trace: list[dict[str, Any]] = []

    for case in candidate_cases:
        matches = _matching_scenarios_for_case(case, business_scenarios)
        if not matches:
            unmatched.append(case)
            assignment_trace.append(
                {
                    "case_id": case.get("case_id"),
                    "title": case.get("title"),
                    "assigned_intent": None,
                    "score": 0,
                }
            )
            continue
        primary_scenario, primary_score = matches[0]
        if not _case_can_feed_core_scenario(case, primary_scenario):
            unmatched.append(case)
            assignment_trace.append(
                {
                    "case_id": case.get("case_id"),
                    "title": case.get("title"),
                    "assigned_intent": primary_scenario["intent"],
                    "score": primary_score,
                    "ignored_reason": "priority_not_in_p0_p1",
                }
            )
            continue
        if _is_ac_like_case(case):
            for scenario, _ in matches:
                buckets[str(scenario["intent"])].append(case)
            assignment_trace.append(
                {
                    "case_id": case.get("case_id"),
                    "title": case.get("title"),
                    "assigned_intents": [str(scenario["intent"]) for scenario, _ in matches],
                    "score": matches[0][1],
                }
            )
            continue

        scenario, score = matches[0]
        buckets[str(scenario["intent"])].append(case)
        assignment_trace.append(
            {
                "case_id": case.get("case_id"),
                "title": case.get("title"),
                "assigned_intent": scenario["intent"],
                "score": score,
            }
        )

    selected_intents: list[str] = []
    for scenario in business_scenarios:
        intent = str(scenario["intent"])
        cases = buckets[intent]
        if not cases and scenario.get("required"):
            cases = [
                {
                    "case_id": f"REQUIRED-{intent}",
                    "title": str(scenario["title"]),
                    "source": "required-rule",
                    "priority": str(scenario.get("priority") or "P1"),
                    "steps": list(scenario.get("steps", [])),
                    "assertions": [str(scenario.get("business_goal") or "")],
                    "preconditions": ["已完成投保并生成电子保单。"],
                    "tags": [f"required-scenario:{intent}"],
                    "manual_case_refs": [],
                    "ac_case_refs": [],
                    "candidate_refs": [
                        {
                            "source": "required-rule",
                            "case_id": f"REQUIRED-{intent}",
                            "title": str(scenario["title"]),
                            "priority": str(scenario.get("priority") or "P1"),
                        }
                    ],
                }
            ]
        if not cases:
            continue
        selected.append(
            _compose_scenario_case(
                case_id=_scenario_case_id(candidate_cases, len(selected) + 1),
                scenario=scenario,
                cases=cases,
                conflicts_by_id=conflicts_by_id,
            )
        )
        selected_intents.append(intent)
        if len(selected) >= max_cases:
            break

    selected_case_by_intent = {str(item["business_intent"]): item for item in selected}
    absorbed_by_candidate: dict[str, dict[str, Any]] = {}
    for intent, cases in buckets.items():
        scenario = scenario_by_intent[intent]
        selected_case = selected_case_by_intent.get(intent)
        for case in cases:
            candidate_key = str(case.get("case_id") or id(case))
            if selected_case is None:
                absorbed_by_candidate.setdefault(candidate_key, {**case, "excluded_reason": "selection_cap_reached"})
                continue
            existing = absorbed_by_candidate.get(candidate_key)
            if existing is None:
                absorbed_by_candidate[candidate_key] = {
                    **case,
                    "excluded_reason": _absorbed_reason(case, scenario),
                    "absorbed_by_case_id": selected_case["case_id"],
                    "absorbed_by_case_ids": [selected_case["case_id"]],
                    "absorbed_by_intent": intent,
                    "absorbed_by_intents": [intent],
                }
                continue
            existing.setdefault("absorbed_by_case_ids", [])
            existing.setdefault("absorbed_by_intents", [])
            if selected_case["case_id"] not in existing["absorbed_by_case_ids"]:
                existing["absorbed_by_case_ids"].append(selected_case["case_id"])
            if intent not in existing["absorbed_by_intents"]:
                existing["absorbed_by_intents"].append(intent)

    excluded.extend(absorbed_by_candidate.values())
    for case in unmatched:
        candidate_key = str(case.get("case_id") or id(case))
        if candidate_key in absorbed_by_candidate:
            continue
        priority = str(case.get("priority") or "P2").upper()
        excluded_reason = "low_priority" if priority == "P2" else "not_core_regression"
        excluded.append({**case, "excluded_reason": excluded_reason})

    selection_trace = {
        "selection_policy": {
            "name": "business-scenario-orchestration-v1",
            "scenario_profile": scenario_profile,
            "max_cases": max_cases,
            "rules": [
                "treat each XMind root-to-leaf path as a candidate, not as the final regression case",
                "classify candidates into product-level business intents before selection",
                "emit one core scenario per selected business intent",
                "fold PRD acceptance criteria into assertions, rules, and coverage_refs",
                "fold manual plan leaves such as 计划21/计划22 into data_variants",
                "prefer manual P0/P1 and matching PRD AC evidence, while allowing predefined P0 business scenarios to override unreliable XMind marker priority",
                "always include underwriting verification for intelligent underwriting, manual underwriting, risk-control, and insurer review paths",
                "always include electronic policy verification and assert consistency between submitted insurance data and electronic policy data",
            ],
            "core_keywords": list(_CORE_REGRESSION_KEYWORDS),
            "business_intents": [str(item["intent"]) for item in business_scenarios],
        },
        "summary": {
            "candidate_case_count": len(candidate_cases),
            "selected_case_count": len(selected),
            "excluded_case_count": len(excluded),
            "conflict_count": len(conflicts),
            "business_intent_count": len(selected_intents),
        },
        "selected_cases": [
            {
                "case_id": item.get("case_id"),
                "title": item.get("title"),
                "source": item.get("source"),
                "priority": item.get("priority"),
                "business_intent": item.get("business_intent"),
                "reason": item.get("selection_reason"),
                "score": item.get("selection_score"),
                "coverage_ref_count": len(item.get("coverage_refs", []) or []),
                "rule_count": len(item.get("rules", []) or []),
                "data_variant_count": len(item.get("data_variants", []) or []),
            }
            for item in selected
        ],
        "excluded_reason_counts": {
            reason: sum(1 for item in excluded if item.get("excluded_reason") == reason)
            for reason in sorted({str(item.get("excluded_reason")) for item in excluded})
        },
        "assignment_trace": assignment_trace,
    }
    return selected, excluded, selection_trace

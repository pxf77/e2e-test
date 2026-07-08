"""Core live browser exploration and path-driving logic."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlparse, urlunparse

from e2e_agent.artifacts.paths import agent_artifact_dir
from e2e_agent.browser.session import BrowserSession
from e2e_agent.core.dom_signature import write_dom_signature_bundles
from e2e_agent.core.policy_info_generator import generate_policy_mock_data
from e2e_agent.core.script_generation import platform_from_entry_url

_PATH_ATTEMPT_TIMEOUT_S = 420
_CLICK_TIMEOUT_MS = 2_000
_POST_CLICK_SETTLE_MS = 700
_PROCESSING_OVERLAY_WAIT_MS = 120_000
_DEFAULT_PATH_HEAL_ATTEMPTS = 5
_MAX_PATH_HEAL_ATTEMPTS = 10
_BACKEND_UNAVAILABLE_MIN_FAILURES = 3
_SESSION_BOUNDARY_MIN_FAILURES = 1
_BACKEND_UNAVAILABLE_API_FRAGMENTS = (
    "/api/apps/cps/product/confirmItem/query",
    "/api/apps/cps/product/detail/variable",
    "/api/apps/cps/product/paymentLimit/query",
    "/api/apps/cps/product/getProductJumpDetailByOperProductId",
    "/api/apps/cps/product/detail/get/merge/pay/config",
    "/api/apps/cps/insure/task/next/do",
)
_BACKEND_UNAVAILABLE_TEXT_MARKERS = (
    "read timed out",
    "load balancer does not have available server",
    "status 500 reading",
    "service unavailable",
    "gateway timeout",
)
_BACKEND_UNAVAILABLE_FATAL_TEXT_MARKERS = (
    "connection refused",
    "connection reset",
    "connection aborted",
)
_SESSION_BOUNDARY_API_FRAGMENTS = (
    "/api/apps/customer/customerInsure/query/",
)
_SESSION_BOUNDARY_TEXT_MARKERS = (
    "user not logged in",
    "login channel expired",
    "not logged in",
    "expired",
)
_HEALTH_NOTICE_BOUNDARY_API_FRAGMENTS = (
    "/api/apps/cps/healthnotify/query/by/trial",
)
_HEALTH_NOTICE_BOUNDARY_TEXT_MARKERS = (
    "健康告知获取失败",
)
_BANK_CARD_VALIDATION_LOOP_MIN_EVENTS = 6
_BANK_CARD_FILLED_VALIDATION_LOOP_MIN_EVENTS = 8
_BANK_CARD_VALIDATION_API_FRAGMENTS = (
    "/api/apps/cps/product/insure/card/valid",
    "/api/apps/cps/pay/bank/card/verif",
)
_FRONTEND_RUNTIME_MIN_PAGEERRORS = 3
_TRIAL_GENES_ROUTE_PATTERNS = (
    "**/api/apps/cps/insure/start/insure**",
    "**/api/apps/cps/insure/generateInsureLink**",
    "**/api/apps/cps/product/trial/insured**",
)
_DEFAULT_ID_CARD_IMAGE_PATHS = (
    Path("assets/user_id_card.jpg"),
)
_HZ_PAGE_ACTION_SHIM_SCRIPT = """
var hzPageAction = globalThis.hzPageAction || function agent3HzPageActionNoop(){ return null; };
globalThis.hzPageAction = hzPageAction;
if (typeof window !== 'undefined') {
  window.hzPageAction = hzPageAction;
}
"""


def _agent3_path_attempt_limit() -> int:
    raw = str(os.environ.get("AGENT3_PATH_ATTEMPTS") or "").strip().lower()
    if not raw:
        raw = str(_DEFAULT_PATH_HEAL_ATTEMPTS)
    if raw in {"0", "until_complete", "until-complete", "auto", "max"}:
        return _MAX_PATH_HEAL_ATTEMPTS
    try:
        value = int(raw)
    except ValueError:
        value = _DEFAULT_PATH_HEAL_ATTEMPTS
    return max(1, min(value, _MAX_PATH_HEAL_ATTEMPTS))
_CHOICE_PAGE_HINTS = ("健康告知", "适当性", "问卷", "确认无以上问题", "风险警示", "问题")
_CHOICE_ACTION_HINTS = ("无以上", "没有", "否", "不是", "通过", "已阅读", "同意")
_FORWARD_ACTION_HINTS = ("下一步", "下一页", "继续", "确认", "提交", "完成", "保存", "知道了", "我已阅读")
_NEGATIVE_ACTION_HINTS = (
    "不同意",
    "拒绝",
    "返回",
    "取消",
    "详情",
    "须知",
    "常见问题",
    "首页",
    "客服",
    "理赔",
    "声明",
    "确认书",
    "条款",
    "协议",
    "提示书",
    "授权",
    "规则",
)
_DOCUMENT_ACTION_HINTS = (
    "须知",
    "详情",
    "指引",
    "常见问题",
    "首页",
    "查询",
    "声明",
    "确认书",
    "条款",
    "协议",
    "提示书",
    "授权",
    "规则",
)
_PRODUCT_ENTRY_CTA_TEXTS = ("立即投保", "我要投保", "去投保", "购买", "投保", "保费试算", "试算", "我要测算")
_PRODUCT_ENTRY_CTA_SELECTORS = ("#submit-by",)
_ENTRY_READY_SELECTOR = (
    "#submit-by, "
    "a:has-text('立即投保'), "
    "button:has-text('立即投保'), "
    "div:has-text('保费试算'), "
    "button:has-text('保费试算'), "
    "a:has-text('投保'), "
    "button:has-text('投保'), "
    "text=保障详情, "
    "text=保费"
)
_BLOCKING_OVERLAY_TOKENS = (
    "该单正在处理中",
    "投保正在处理中",
    "请稍后操作",
    "被保人",
    "被保险人",
    "周岁",
    "年龄",
)


def _platform_viewport(entry_url: str | None) -> dict[str, int]:
    if platform_from_entry_url(entry_url) == "h5":
        return {"width": 390, "height": 844}
    return {"width": 1280, "height": 720}


async def _safe_wait(page: Any) -> None:
    for state in ("domcontentloaded", "networkidle"):
        try:
            await page.wait_for_load_state(state, timeout=5_000)
        except Exception:
            continue


async def _wait_for_entry_ready(page: Any, entry_url: str, *, attempts: int = 3) -> None:
    """Wait until the product entry is usable, retrying transient error pages."""
    last_text = ""
    for attempt in range(attempts):
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=5_000)
        except Exception:
            pass
        try:
            await page.wait_for_selector(
                _ENTRY_READY_SELECTOR,
                timeout=3_000,
            )
            return
        except Exception:
            pass
        try:
            last_text = " ".join((await page.locator("body").inner_text(timeout=1_000)).split())[:300]
        except Exception:
            last_text = ""
        if "未知错误" not in last_text and any(token in last_text for token in ("立即投保", "保费", "保障详情", "保障计划", "投保须知", "保险条款")):
            return
        if attempt < attempts - 1:
            await page.goto(entry_url, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(500)
    raise RuntimeError(f"Entry page did not become usable after {attempts} attempts: {last_text}")


def _business_page_ready_text(url: str, body_text: str) -> bool:
    path = urlparse(url).path or url
    if not path.endswith("/product/insure"):
        return True
    text = " ".join(body_text.split())
    if any(token in text for token in ("投保人信息", "被保险人信息", "姓名", "证件号码", "手机号")):
        return True
    return "正在加载" not in text


async def _wait_for_business_ready(page: Any, *, timeout_ms: int = 6_000) -> None:
    deadline = timeout_ms
    while deadline > 0:
        try:
            body_text = await page.locator("body").inner_text(timeout=1_000)
        except Exception:
            body_text = ""
        if _business_page_ready_text(page.url, body_text):
            return
        await page.wait_for_timeout(500)
        deadline -= 500


def _load_agent3_mock_data_from_env() -> dict[str, Any]:
    mock_data_path = os.environ.get("AGENT3_MOCK_DATA_PATH", "").strip()
    if not mock_data_path:
        return {}
    try:
        payload = json.loads(Path(mock_data_path).read_text(encoding="utf-8"))
    except Exception:
        return {}
    return dict(payload) if isinstance(payload, dict) else {}


async def _load_agent3_mock_data_for_page(page: Any) -> dict[str, Any]:
    try:
        mock_data = await page.evaluate("() => window.__agent3MockData || null")
    except Exception:
        mock_data = None
    if isinstance(mock_data, dict):
        return dict(mock_data)
    return _load_agent3_mock_data_from_env()


def _mock_trial_identity(mock_data: dict[str, Any]) -> tuple[str, str]:
    birthdate = str(mock_data.get("applicant.birthdate") or mock_data.get("insured.birthdate") or "").strip()
    id_no = str(mock_data.get("applicant.id_no") or mock_data.get("insure_form.applicantidno") or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", birthdate):
        return "", chr(0x7537)
    sex_text = chr(0x7537)
    if re.fullmatch(r"\d{17}[\dXx]", id_no):
        try:
            sex_text = chr(0x7537) if int(id_no[16]) % 2 else chr(0x5973)
        except Exception:
            sex_text = chr(0x7537)
    return birthdate, sex_text


def _patch_gene_list(genes: Any, *, birthdate: str, sex_text: str) -> bool:
    if not isinstance(genes, list):
        return False
    changed = False
    for gene in genes:
        if not isinstance(gene, dict):
            continue
        key = gene.get("key") or gene.get("geneKey")
        if key == "insurantDate" and gene.get("value") != birthdate:
            gene["value"] = birthdate
            if "defaultValue" in gene:
                gene["defaultValue"] = birthdate
            changed = True
        elif key == "sex" and gene.get("value") != sex_text:
            gene["value"] = sex_text
            if "defaultValue" in gene:
                gene["defaultValue"] = sex_text
            changed = True
    return changed


def _patch_health_notify_payload(raw: str, mock_data: dict[str, Any]) -> tuple[str, bool]:
    birthdate, sex_text = _mock_trial_identity(mock_data)
    if not birthdate or not raw:
        return raw, False
    try:
        data = json.loads(raw)
    except Exception:
        return raw, False
    if not isinstance(data, dict):
        return raw, False

    changed = False

    restrict_raw = data.get("restrictReqParamStr")
    if isinstance(restrict_raw, str) and restrict_raw.strip():
        try:
            restrict_obj = json.loads(restrict_raw)
        except Exception:
            restrict_obj = None
        if isinstance(restrict_obj, dict) and _patch_gene_list(
            restrict_obj.get("genes"), birthdate=birthdate, sex_text=sex_text
        ):
            data["restrictReqParamStr"] = json.dumps(restrict_obj, ensure_ascii=False, separators=(",", ":"))
            changed = True

    trial_genes = data.get("trialGenes")
    trial_obj = None
    if isinstance(trial_genes, str) and trial_genes.strip():
        try:
            trial_obj = json.loads(trial_genes)
        except Exception:
            trial_obj = None
    elif isinstance(trial_genes, dict):
        trial_obj = trial_genes
    if isinstance(trial_obj, dict) and _patch_gene_list(trial_obj.get("genes"), birthdate=birthdate, sex_text=sex_text):
        data["trialGenes"] = json.dumps(trial_obj, ensure_ascii=False, separators=(",", ":"))
        changed = True

    if not changed:
        return raw, False
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")), True


def _field_plain_value(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("value", "text", "label"):
            raw = value.get(key)
            if raw not in (None, ""):
                return str(raw).strip()
        return ""
    if value is None:
        return ""
    return str(value).strip()


def _module_field_value(form_data: Any, module_id: str, key: str) -> str:
    if not isinstance(form_data, dict):
        return ""
    rows = form_data.get(module_id)
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        return ""
    return _field_plain_value(rows[0].get(key))


def _payload_trial_identity(data: dict[str, Any], mock_data: dict[str, Any]) -> tuple[str, str]:
    form_data = data.get("data")
    birthdate = ""
    sex_value = ""
    for module_id in ("20", "10"):
        candidate = _module_field_value(form_data, module_id, "birthdate")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", candidate):
            birthdate = candidate
            sex_value = _module_field_value(form_data, module_id, "sex")
            break
    if birthdate:
        if sex_value in ("0", chr(0x5973)):
            return birthdate, chr(0x5973)
        if sex_value in ("1", chr(0x7537)):
            return birthdate, chr(0x7537)
        _, mock_sex = _mock_trial_identity(mock_data)
        return birthdate, mock_sex
    return _mock_trial_identity(mock_data)


def _patch_trial_container(container: Any, *, birthdate: str, sex_text: str) -> bool:
    if not isinstance(container, dict):
        return False
    changed = False
    for key in ("genes", "changeGeneList", "displayGenes"):
        changed = _patch_gene_list(container.get(key), birthdate=birthdate, sex_text=sex_text) or changed
    trial_attr = container.get("trialAttr")
    if isinstance(trial_attr, dict):
        for key, value in (("insurantDate", birthdate), ("sex", sex_text)):
            attr = trial_attr.get(key)
            if isinstance(attr, dict) and attr.get("value") != value:
                attr["value"] = value
                if "defaultValue" in attr:
                    attr["defaultValue"] = value
                changed = True
    return changed


def _patch_trial_genes_payload(raw: str, mock_data: dict[str, Any]) -> tuple[str, bool]:
    if not raw:
        return raw, False
    try:
        data = json.loads(raw)
    except Exception:
        return raw, False
    if not isinstance(data, dict):
        return raw, False

    birthdate, sex_text = _payload_trial_identity(data, mock_data)
    if not birthdate:
        return raw, False

    changed = _patch_trial_container(data, birthdate=birthdate, sex_text=sex_text)
    trial_genes = data.get("trialGenes")
    trial_obj = None
    trial_was_string = isinstance(trial_genes, str)
    if trial_was_string and trial_genes.strip():
        try:
            trial_obj = json.loads(trial_genes)
        except Exception:
            trial_obj = None
    elif isinstance(trial_genes, dict):
        trial_obj = trial_genes
    if isinstance(trial_obj, dict) and _patch_trial_container(trial_obj, birthdate=birthdate, sex_text=sex_text):
        data["trialGenes"] = (
            json.dumps(trial_obj, ensure_ascii=False, separators=(",", ":")) if trial_was_string else trial_obj
        )
        changed = True

    if not changed:
        return raw, False
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")), True


def _patch_travel_form_payload(raw: str, mock_data: dict[str, Any]) -> tuple[str, bool]:
    if not raw:
        return raw, False
    try:
        payload = json.loads(raw)
    except Exception:
        return raw, False
    if not isinstance(payload, dict):
        return raw, False
    form_data = payload.get("data")
    if not isinstance(form_data, dict):
        return raw, False

    changed = False
    start_date = str(
        mock_data.get("policy.start_date")
        or mock_data.get("policyStartDate")
        or payload.get("trialStartDate")
        or ""
    ).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", start_date):
        rows = form_data.get("102")
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            current = _field_plain_value(rows[0].get("insuranceDate"))
            if not current:
                rows[0]["insuranceDate"] = start_date
                changed = True

    rows_40 = form_data.get("40")
    if isinstance(rows_40, list) and rows_40 and isinstance(rows_40[0], dict):
        row = rows_40[0]
        if not _field_plain_value(row.get("purpose")):
            row["purpose"] = "1"
            changed = True
        if not _field_plain_value(row.get("tripDestination")):
            row["tripDestination"] = "中国澳门"
            changed = True

    if not changed:
        return raw, False
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")), True


def _should_patch_hidden_trial_genes() -> bool:
    value = os.environ.get("AGENT3_PATCH_TRIAL_GENES", "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _should_mock_health_notify_failure() -> bool:
    value = os.environ.get("AGENT3_MOCK_HEALTH_NOTIFY_FAILURE", "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _empty_health_notify_success_response(title: str = "") -> dict[str, Any]:
    return {
        "code": 0,
        "data": {
            "id": 0,
            "title": title or "健康告知",
            "contentType": 1,
            "selectType": 2,
            "healthyModuleVos": [],
            "healthyModularAnswer": 2,
            "needFillIn": 0,
            "healthVerifyWay": 0,
            "healthEntryConclusion": 0,
        },
        "success": True,
    }


def _should_skip_renewal_bank_sign() -> bool:
    value = os.environ.get("AGENT3_SKIP_RENEWAL_BANK_SIGN", "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _patch_submit_skip_renewal_bank(raw: str) -> tuple[str, bool]:
    if not raw:
        return raw, False
    try:
        data = json.loads(raw)
    except Exception:
        return raw, False
    if not isinstance(data, dict):
        return raw, False

    changed = False
    if data.get("autoRenewal") is not False:
        data["autoRenewal"] = False
        changed = True
    if data.get("renewalCheck") not in (None, 0, False):
        data["renewalCheck"] = 0
        changed = True
    payload_data = data.get("data")
    if isinstance(payload_data, dict):
        for bank_key in ("107", 107):
            if bank_key in payload_data:
                payload_data.pop(bank_key, None)
                changed = True

    if not changed:
        return raw, False
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")), True


def _mock_pay_account(mock_data: dict[str, Any]) -> str:
    value = str(mock_data.get("payAccount_107") or "").replace(" ", "").strip()
    return value if re.fullmatch(r"\d{10,30}", value) else ""


def _patch_bank_card_validation_url(url: str, mock_data: dict[str, Any]) -> tuple[str, bool]:
    pay_account = _mock_pay_account(mock_data)
    if not pay_account:
        return url, False
    parsed = urlparse(str(url or ""))
    if "/api/apps/cps/product/insure/card/valid" not in parsed.path:
        return url, False
    query = parse_qs(parsed.query, keep_blank_values=True)
    card_values = query.get("cardNum")
    if card_values and str(card_values[0] or "").strip():
        return url, False
    query["cardNum"] = [pay_account]
    patched_query = urlencode(query, doseq=True)
    return urlunparse(parsed._replace(query=patched_query)), True


def _patch_bank_card_validation_payload(raw: str, mock_data: dict[str, Any]) -> tuple[str, bool]:
    pay_account = _mock_pay_account(mock_data)
    if not pay_account or not raw:
        return raw, False
    try:
        payload = json.loads(raw)
    except Exception:
        return raw, False
    if not isinstance(payload, dict):
        return raw, False
    if str(payload.get("cardNum") or "").replace(" ", "").strip():
        return raw, False
    payload["cardNum"] = pay_account
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")), True


async def _install_hz_page_action_shim(page: Any) -> bool:
    installed = False
    context = getattr(page, "context", None)
    if context is not None:
        try:
            await context.add_init_script(_HZ_PAGE_ACTION_SHIM_SCRIPT)
            installed = True
        except Exception:
            pass
    try:
        await page.add_init_script(_HZ_PAGE_ACTION_SHIM_SCRIPT)
        installed = True
    except Exception:
        pass
    try:
        await page.add_script_tag(content=_HZ_PAGE_ACTION_SHIM_SCRIPT)
        installed = True
    except Exception:
        pass
    try:
        await page.evaluate(f"() => {{ {_HZ_PAGE_ACTION_SHIM_SCRIPT} }}")
        installed = True
    except Exception:
        pass
    return installed


async def _install_submit_trace(page: Any) -> None:
    if getattr(page, "_agent3_submit_trace_installed", False):
        return
    trace: dict[str, list[dict[str, Any]]] = {
        "requests": [],
        "responses": [],
        "console": [],
        "pageerrors": [],
    }
    setattr(page, "_agent3_submit_trace", trace)
    setattr(page, "_agent3_submit_trace_installed", True)
    run_dir = os.environ.get("AGENT3_RUN_DIR")
    api_trace_path = Path(run_dir) / "api-trace.jsonl" if run_dir else None
    api_errors_path = Path(run_dir) / "api-errors.jsonl" if run_dir else None

    def is_api_url(url: str) -> bool:
        return bool(
            re.search(
                r"/api/|/collect(?:\?|$)|/ts\?|/apm-collect|/merak|/phobos",
                url,
                re.I,
            )
        )

    def append_jsonl(path: Path | None, payload: dict[str, Any]) -> None:
        if not path:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
        except Exception:
            pass

    def classify_response_error(item: dict[str, Any]) -> dict[str, Any] | None:
        if item.get("mocked_health_notify_failure") is True:
            return None
        status = int(item.get("status") or 0)
        body = str(item.get("body") or "")
        if status >= 400:
            return {"kind": "http_status", "status": status}
        if not body:
            return None
        try:
            payload = json.loads(body)
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        code = payload.get("code")
        success = payload.get("success")
        url = str(item.get("url") or "")
        if "/api/apps/customer/access-record/save" in url and code == -1:
            return None
        if success is False:
            kind = "business_handoff" if str(code) == "40015" else "business_error"
            return {
                "kind": kind,
                "code": code,
                "msg": payload.get("msg") or payload.get("message"),
                "errorCode": (payload.get("data") or {}).get("errorCode") if isinstance(payload.get("data"), dict) else None,
                "errorMessage": (payload.get("data") or {}).get("errorMessage") if isinstance(payload.get("data"), dict) else None,
            }
        if code not in (None, 0, "0", "SUCCESS", "success"):
            return {
                "kind": "business_code",
                "code": code,
                "msg": payload.get("msg") or payload.get("message"),
            }
        return None

    def keep(bucket: str, item: dict[str, Any], limit: int = 240) -> None:
        values = trace.setdefault(bucket, [])
        values.append(item)
        if len(values) > limit:
            del values[: len(values) - limit]
        event = {
            "event": bucket[:-1] if bucket.endswith("s") else bucket,
            **item,
        }
        append_jsonl(api_trace_path, event)
        if bucket == "responses":
            error = classify_response_error(item)
            if error:
                append_jsonl(api_errors_path, {**event, "error": error})
        elif bucket == "pageerrors":
            append_jsonl(api_errors_path, event)

    async def patch_submit_route(route: Any, request: Any) -> None:
        try:
            raw = request.post_data or ""
            patched = raw
            patched_trial_genes = False
            patched_travel_form = False
            skipped_renewal_bank_sign = False
            patched, patched_travel_form = _patch_travel_form_payload(patched, _load_agent3_mock_data_from_env())
            if _should_patch_hidden_trial_genes():
                patched, patched_trial_genes = _patch_trial_genes_payload(patched, _load_agent3_mock_data_from_env())
            if _should_skip_renewal_bank_sign():
                patched, skipped_renewal_bank_sign = _patch_submit_skip_renewal_bank(patched)
            if patched_travel_form or patched_trial_genes or skipped_renewal_bank_sign:
                keep(
                    "requests",
                    {
                        "ts": int(time.time() * 1000),
                        "method": str(request.method),
                        "url": str(request.url)[:500],
                        "resource_type": str(request.resource_type),
                        "post_data": patched[:50000],
                        "patched_travel_form": patched_travel_form,
                        "patched_trial_genes": patched_trial_genes,
                        "skipped_renewal_bank_sign": skipped_renewal_bank_sign,
                    },
                )
                await route.continue_(post_data=patched)
                return
        except Exception as exc:
            keep("pageerrors", {"ts": int(time.time() * 1000), "text": f"submit route patch failed: {exc}"[:1500]})
        await route.continue_()

    async def patch_bank_card_valid_route(route: Any, request: Any) -> None:
        try:
            patched_url, changed = _patch_bank_card_validation_url(
                str(request.url),
                _load_agent3_mock_data_from_env(),
            )
            if changed:
                keep(
                    "requests",
                    {
                        "ts": int(time.time() * 1000),
                        "method": str(request.method),
                        "url": patched_url[:500],
                        "resource_type": str(request.resource_type),
                        "post_data": str(request.post_data or "")[:50000],
                        "patched_bank_card_validation": True,
                    },
                )
                await route.continue_(url=patched_url)
                return
        except Exception as exc:
            keep("pageerrors", {"ts": int(time.time() * 1000), "text": f"bank card valid route patch failed: {exc}"[:1500]})
        await route.continue_()

    async def patch_bank_card_verif_route(route: Any, request: Any) -> None:
        try:
            raw = request.post_data or ""
            patched, changed = _patch_bank_card_validation_payload(
                raw,
                _load_agent3_mock_data_from_env(),
            )
            if changed:
                keep(
                    "requests",
                    {
                        "ts": int(time.time() * 1000),
                        "method": str(request.method),
                        "url": str(request.url)[:500],
                        "resource_type": str(request.resource_type),
                        "post_data": patched[:50000],
                        "patched_bank_card_validation": True,
                    },
                )
                await route.continue_(post_data=patched)
                return
        except Exception as exc:
            keep("pageerrors", {"ts": int(time.time() * 1000), "text": f"bank card verif route patch failed: {exc}"[:1500]})
        await route.continue_()

    async def patch_trial_genes_route(route: Any, request: Any) -> None:
        try:
            raw = request.post_data or ""
            patched, patched_travel_form = _patch_travel_form_payload(raw, _load_agent3_mock_data_from_env())
            if _should_patch_hidden_trial_genes():
                patched, changed = _patch_trial_genes_payload(patched, _load_agent3_mock_data_from_env())
                if patched_travel_form or changed:
                    keep(
                        "requests",
                        {
                            "ts": int(time.time() * 1000),
                            "method": str(request.method),
                            "url": str(request.url)[:500],
                            "resource_type": str(request.resource_type),
                            "post_data": patched[:50000],
                            "patched_travel_form": patched_travel_form,
                            "patched_trial_genes": changed,
                        },
                    )
                    await route.continue_(post_data=patched)
                    return
            elif patched_travel_form:
                keep(
                    "requests",
                    {
                        "ts": int(time.time() * 1000),
                        "method": str(request.method),
                        "url": str(request.url)[:500],
                        "resource_type": str(request.resource_type),
                        "post_data": patched[:50000],
                        "patched_travel_form": True,
                    },
                )
                await route.continue_(post_data=patched)
                return
        except Exception as exc:
            keep("pageerrors", {"ts": int(time.time() * 1000), "text": f"trial genes route patch failed: {exc}"[:1500]})
        await route.continue_()

    async def patch_health_notify_route(route: Any, request: Any) -> None:
        try:
            if _should_patch_hidden_trial_genes():
                raw = request.post_data or ""
                mock_data = _load_agent3_mock_data_from_env()
                patched, changed = _patch_health_notify_payload(raw, mock_data)
                if changed:
                    keep(
                        "requests",
                        {
                            "ts": int(time.time() * 1000),
                            "method": str(request.method),
                            "url": str(request.url)[:500],
                            "resource_type": str(request.resource_type),
                            "post_data": patched[:50000],
                            "patched_health_trial_genes": True,
                        },
                    )
                    await route.continue_(post_data=patched)
                    return
            if _should_mock_health_notify_failure() and "/query/by/trial" in str(request.url):
                response = await route.fetch()
                body = ""
                try:
                    body = await response.text()
                except Exception:
                    body = ""
                if "健康告知获取失败" in body:
                    fallback = _empty_health_notify_success_response("Agent3 空健康告知兜底")
                    keep(
                        "responses",
                        {
                            "ts": int(time.time() * 1000),
                            "status": int(getattr(response, "status", 200) or 200),
                            "url": str(request.url)[:500],
                            "content_type": "application/json",
                            "body": body[:20000],
                            "mocked_health_notify_failure": True,
                        },
                    )
                    await route.fulfill(
                        status=200,
                        content_type="application/json",
                        body=json.dumps(fallback, ensure_ascii=False),
                    )
                    return
                await route.fulfill(response=response)
                return
        except Exception as exc:
            keep("pageerrors", {"ts": int(time.time() * 1000), "text": f"health notify route patch failed: {exc}"[:1500]})
        await route.continue_()

    try:
        await page.route("**/api/apps/cps/insure/submit**", patch_submit_route)
    except Exception:
        pass
    try:
        await page.route("**/api/apps/cps/product/insure/card/valid**", patch_bank_card_valid_route)
    except Exception:
        pass
    try:
        await page.route("**/api/apps/cps/pay/bank/card/verif**", patch_bank_card_verif_route)
    except Exception:
        pass
    for pattern in _TRIAL_GENES_ROUTE_PATTERNS:
        try:
            await page.route(pattern, patch_trial_genes_route)
        except Exception:
            pass
    try:
        await page.route("**/api/apps/cps/healthnotify/**", patch_health_notify_route)
    except Exception:
        pass

    def on_request(request: Any) -> None:
        try:
            url = str(request.url)
            if not is_api_url(url):
                return
            keep(
                "requests",
                {
                    "ts": int(time.time() * 1000),
                    "method": str(request.method),
                    "url": url[:500],
                    "resource_type": str(request.resource_type),
                    "post_data": str(request.post_data or "")[:50000],
                },
            )
        except Exception:
            pass

    async def on_response(response: Any) -> None:
        try:
            url = str(response.url)
            if not is_api_url(url):
                return
            body = ""
            ctype = ""
            try:
                ctype = str(response.headers.get("content-type", ""))
            except Exception:
                ctype = ""
            if "json" in ctype or "text" in ctype:
                try:
                    body = (await response.text())[:20000]
                except Exception:
                    body = ""
            keep(
                "responses",
                {
                    "ts": int(time.time() * 1000),
                    "status": int(response.status),
                    "url": url[:500],
                    "content_type": ctype[:120],
                    "body": body,
                },
            )
        except Exception:
            pass

    def on_console(message: Any) -> None:
        try:
            keep(
                "console",
                {
                    "ts": int(time.time() * 1000),
                    "type": str(message.type),
                    "text": str(message.text)[:1000],
                    "location": dict(message.location or {}),
                },
            )
        except Exception:
            pass

    def on_pageerror(exc: Any) -> None:
        stack = getattr(exc, "stack", None)
        keep(
            "pageerrors",
            {
                "ts": int(time.time() * 1000),
                "text": str(exc)[:1500],
                "stack": str(stack or "")[:3000],
            },
        )

    page.on("request", on_request)
    page.on("response", lambda response: asyncio.create_task(on_response(response)))
    page.on("console", on_console)
    page.on("pageerror", on_pageerror)
    await _install_hz_page_action_shim(page)
    try:
        await page.add_init_script(
            """(() => {
                window.__agent3Trace = window.__agent3Trace || { fetches: [], xhrs: [], clicks: [] };
                const keep = (bucket, value) => {
                    const target = window.__agent3Trace[bucket] || (window.__agent3Trace[bucket] = []);
                    target.push({ ts: Date.now(), ...value });
                    if (target.length > 200) target.splice(0, target.length - 200);
                };
                if (!window.__agent3TraceInstalled) {
                    window.__agent3TraceInstalled = true;
                    const rawFetch = window.fetch;
                    if (rawFetch) {
                        window.fetch = async (...args) => {
                            const url = String(args[0]?.url || args[0] || '');
                            const method = String(args[1]?.method || args[0]?.method || 'GET');
                            const patchSubmitBody = raw => {
                                if (!/\\/api\\/apps\\/cps\\/insure\\/submit/i.test(url) || !raw) return raw;
                                try {
                                    const data = JSON.parse(String(raw));
                                    const birthdate = window.__agent3TrialBirthdate || data?.data?.['20']?.[0]?.birthdate || data?.data?.['10']?.[0]?.birthdate;
                                    const sexText = window.__agent3TrialSex || (String(data?.data?.['20']?.[0]?.sex || data?.data?.['10']?.[0]?.sex) === '0' ? '女' : '男');
                                    const trialGenes = typeof data.trialGenes === 'string' ? JSON.parse(data.trialGenes) : data.trialGenes;
                                    if (birthdate && trialGenes && Array.isArray(trialGenes.genes)) {
                                        for (const gene of trialGenes.genes) {
                                            if (gene?.key === 'insurantDate') gene.value = birthdate;
                                            if (gene?.key === 'sex') gene.value = sexText;
                                        }
                                        data.trialGenes = JSON.stringify(trialGenes);
                                        keep('fetches', { phase: 'patch-submit-trialGenes', method, url: url.slice(0, 500), birthdate, sexText });
                                        return JSON.stringify(data);
                                    }
                                } catch (_) {}
                                return raw;
                            };
                            if (/\\/api\\/apps\\/cps\\/insure\\/submit/i.test(url) && args[1]?.body) {
                                args[1] = { ...args[1], body: patchSubmitBody(args[1].body) };
                            }
                            if (/api|insure|submit|order|pay|underwrit|trial|bank|card|qixin|baoxin/i.test(url)) {
                                keep('fetches', { phase: 'request', method, url: url.slice(0, 500) });
                            }
                            const response = await rawFetch(...args);
                            if (/api|insure|submit|order|pay|underwrit|trial|bank|card|qixin|baoxin/i.test(url)) {
                                keep('fetches', { phase: 'response', method, url: url.slice(0, 500), status: response.status });
                            }
                            return response;
                        };
                    }
                    const RawXHR = window.XMLHttpRequest;
                    if (RawXHR) {
                        const rawOpen = RawXHR.prototype.open;
                        const rawSend = RawXHR.prototype.send;
                        RawXHR.prototype.open = function(method, url, ...rest) {
                            this.__agent3Method = method;
                            this.__agent3Url = String(url || '');
                            return rawOpen.call(this, method, url, ...rest);
                        };
                        RawXHR.prototype.send = function(body) {
                            const url = String(this.__agent3Url || '');
                            const method = String(this.__agent3Method || '');
                            const patchSubmitBody = raw => {
                                if (!/\\/api\\/apps\\/cps\\/insure\\/submit/i.test(url) || !raw) return raw;
                                try {
                                    const data = JSON.parse(String(raw));
                                    const birthdate = window.__agent3TrialBirthdate || data?.data?.['20']?.[0]?.birthdate || data?.data?.['10']?.[0]?.birthdate;
                                    const sexText = window.__agent3TrialSex || (String(data?.data?.['20']?.[0]?.sex || data?.data?.['10']?.[0]?.sex) === '0' ? '女' : '男');
                                    const trialGenes = typeof data.trialGenes === 'string' ? JSON.parse(data.trialGenes) : data.trialGenes;
                                    if (birthdate && trialGenes && Array.isArray(trialGenes.genes)) {
                                        for (const gene of trialGenes.genes) {
                                            if (gene?.key === 'insurantDate') gene.value = birthdate;
                                            if (gene?.key === 'sex') gene.value = sexText;
                                        }
                                        data.trialGenes = JSON.stringify(trialGenes);
                                        keep('xhrs', { phase: 'patch-submit-trialGenes', method, url: url.slice(0, 500), birthdate, sexText });
                                        return JSON.stringify(data);
                                    }
                                } catch (_) {}
                                return raw;
                            };
                            body = patchSubmitBody(body);
                            if (/api|insure|submit|order|pay|underwrit|trial|bank|card|qixin|baoxin/i.test(url)) {
                                keep('xhrs', { phase: 'request', method, url: url.slice(0, 500), body: String(body || '').slice(0, 800) });
                                this.addEventListener('loadend', () => {
                                    keep('xhrs', { phase: 'response', method, url: url.slice(0, 500), status: this.status, response: String(this.responseText || '').slice(0, 1500) });
                                });
                            }
                            return rawSend.call(this, body);
                        };
                    }
                    document.addEventListener('click', event => {
                        const el = event.target && event.target.closest ? event.target.closest('a,button,[role="button"],.am-button,.submit-btn,div,span') : event.target;
                        if (!el) return;
                        const text = String(el.innerText || el.textContent || el.value || el.getAttribute?.('aria-label') || '').replace(/\\s+/g, ' ').trim();
                        const cls = String(el.className || '');
                        if (/提交|submit|am-button|submit-btn/i.test(text + cls)) {
                            const rect = el.getBoundingClientRect();
                            keep('clicks', { text: text.slice(0, 120), className: cls.slice(0, 180), tag: el.tagName, x: event.clientX, y: event.clientY, rect: { left: rect.left, top: rect.top, width: rect.width, height: rect.height } });
                        }
                    }, true);
                }
            })()"""
        )
    except Exception:
        pass


async def _page_has_processing_overlay(page: Any) -> bool:
    try:
        return bool(
            await page.evaluate(
                """() => {
                    const visible = el => {
                        if (!el) return false;
                        const rect = el.getBoundingClientRect();
                        const style = window.getComputedStyle(el);
                        return rect.width > 0
                            && rect.height > 0
                            && style.display !== 'none'
                            && style.visibility !== 'hidden'
                            && Number(style.opacity || 1) !== 0;
                    };
                    const norm = text => String(text || '').replace(/\\s+/g, '').trim();
                    const viewportArea = Math.max(1, window.innerWidth * window.innerHeight);
                    const candidates = Array.from(document.querySelectorAll(
                        '.am-activity-indicator, .am-activity-indicator-spinner, .adm-spin-loading, .adm-mask, .am-modal-mask, .mask, [class*="loading"], [class*="Loading"], [class*="spinner"], [class*="Spinner"], [class*="spin"], [class*="Spin"]'
                    )).filter(visible);
                    if (candidates.some(el => {
                        const rect = el.getBoundingClientRect();
                        const text = norm(el.innerText || el.textContent);
                        const cls = String(el.className || '');
                        const covers = (rect.width * rect.height) / viewportArea > 0.12;
                        return text.includes('正在加载')
                            || text.includes('请稍后')
                            || /activity|loading|spinner|spin/i.test(cls)
                            || (covers && el.querySelector('[class*="spin"],[class*="loading"],svg'));
                    })) {
                        return true;
                    }
                    const bodyText = norm(document.body?.innerText || '');
                    return bodyText.includes('正在加载，请稍后') || bodyText.includes('正在加载');
                }"""
            )
        )
    except Exception:
        return False


async def _wait_while_processing(page: Any, *, timeout_ms: int = _PROCESSING_OVERLAY_WAIT_MS) -> bool:
    saw_processing = False
    remaining = timeout_ms
    while remaining > 0:
        if not await _page_has_processing_overlay(page):
            return saw_processing
        saw_processing = True
        await page.wait_for_timeout(1_000)
        remaining -= 1_000
    return saw_processing


async def _wait_for_page_flow_settled(
    page: Any,
    *,
    previous_url: str | None = None,
    timeout_ms: int = 20_000,
    min_wait_ms: int = 900,
) -> bool:
    """Wait for H5 route/render transitions to settle before reading or filling."""
    if _submit_trace_blocker_reason(page):
        return True
    await page.wait_for_timeout(min_wait_ms)
    if _submit_trace_blocker_reason(page):
        return True
    for state in ("domcontentloaded", "networkidle"):
        try:
            await page.wait_for_load_state(state, timeout=2_500)
        except Exception:
            pass
        if _submit_trace_blocker_reason(page):
            return True
    saw_processing = await _wait_while_processing(page, timeout_ms=min(timeout_ms, _PROCESSING_OVERLAY_WAIT_MS))
    if _submit_trace_blocker_reason(page):
        return True
    deadline = time.monotonic() + timeout_ms / 1000
    last_probe: tuple[str, str] | None = None
    stable_count = 0
    while time.monotonic() < deadline:
        try:
            body_text = " ".join((await page.locator("body").inner_text(timeout=1_000)).split())
        except Exception:
            body_text = ""
        current_url = str(page.url)
        if await _page_has_processing_overlay(page):
            saw_processing = True
            stable_count = 0
            last_probe = None
            if _submit_trace_blocker_reason(page):
                return True
            await page.wait_for_timeout(800)
            continue
        if not _business_page_ready_text(current_url, body_text):
            stable_count = 0
            last_probe = None
            if _submit_trace_blocker_reason(page):
                return True
            await page.wait_for_timeout(800)
            continue
        if _submit_trace_blocker_reason(page):
            return True
        probe = (current_url, hashlib.sha1(body_text[:4000].encode("utf-8", "ignore")).hexdigest())
        if probe == last_probe:
            stable_count += 1
            if stable_count >= 2:
                return saw_processing or bool(previous_url and current_url != previous_url)
        else:
            last_probe = probe
            stable_count = 1
        await page.wait_for_timeout(700)
    return saw_processing or bool(previous_url and str(page.url) != previous_url)


async def _collect_fields(page: Any) -> list[dict[str, Any]]:
    return await page.locator("input, select, textarea").evaluate_all(
        """(elements) => elements.map((el, index) => ({
            index,
            tag: el.tagName.toLowerCase(),
            type: el.getAttribute('type') || '',
            name: el.getAttribute('name') || '',
            id: el.getAttribute('id') || '',
            placeholder: el.getAttribute('placeholder') || '',
            label: el.getAttribute('aria-label') || '',
            required: !!(el.required || el.getAttribute('required') !== null || el.getAttribute('aria-required') === 'true'),
            disabled: !!el.disabled,
            readonly: !!el.readOnly,
            checked: !!el.checked,
            value_present: !!String(el.value || '').trim(),
            options: el.tagName.toLowerCase() === 'select'
                ? Array.from(el.options || []).map(option => ({
                    text: (option.innerText || option.textContent || '').trim(),
                    value: option.value || '',
                    disabled: !!option.disabled,
                }))
                : [],
            selector: el.getAttribute('id')
                ? `#${CSS.escape(el.getAttribute('id'))}`
                : el.getAttribute('name')
                ? `${el.tagName.toLowerCase()}[name="${CSS.escape(el.getAttribute('name'))}"]`
                : `${el.tagName.toLowerCase()}:nth-of-type(${index + 1})`,
        }))"""
    )


async def _collect_actions(page: Any) -> list[dict[str, Any]]:
    return await page.locator(
        "button, [role='button'], a, input[type='button'], input[type='submit'], [onclick], .btn, .button, [class*='btn'], [class*='button']"
    ).evaluate_all(
        """(elements) => elements.slice(0, 160).map((el, index) => ({
            index,
            tag: el.tagName.toLowerCase(),
            text: (el.innerText || el.textContent || el.getAttribute('value') || '').trim().slice(0, 80),
            href: el.tagName.toLowerCase() === 'a' ? (el.getAttribute('href') || '') : '',
            id: el.getAttribute('id') || '',
            className: el.getAttribute('class') || '',
            role: el.getAttribute('role') || '',
            visible: !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length),
            selector: el.getAttribute('id')
                ? `#${CSS.escape(el.getAttribute('id'))}`
                : `${el.tagName.toLowerCase()}:nth-of-type(${index + 1})`,
            text_selector: (el.innerText || el.textContent || el.getAttribute('value') || '').trim()
                ? `${el.tagName.toLowerCase()} >> text=${(el.innerText || el.textContent || el.getAttribute('value') || '').trim().slice(0, 40)}`
                : '',
        }))"""
    )


async def _page_signature(page: Any) -> str:
    try:
        body_text = await page.locator("body").inner_text(timeout=2_000)
    except Exception:
        body_text = page.url
    return hashlib.sha256(f"{page.url}\n{body_text[:4000]}".encode("utf-8", errors="ignore")).hexdigest()


async def _body_text_excerpt(page: Any) -> str:
    try:
        body_text = await page.locator("body").inner_text(timeout=2_000)
    except Exception:
        return ""
    return " ".join(body_text.split())[:1200]


def _looks_like_submit_action_text(text: str) -> bool:
    compact = "".join(str(text or "").split()).lower()
    submit_tokens = (
        "\u63d0\u4ea4",
        "\u63d0\u4ea4\u8ba2\u5355",
        "\u63d0\u4ea4\u6295\u4fdd\u5355",
        "\u63d0\u4ea4\u6838\u4fdd",
        "\u63d0\u4ea4\u8ba4\u8bc1",
        "submit",
        "鎻愪氦",
        "鎻愪氦璁㈠崟",
        "鎻愪氦鎶曚繚",
        "鎻愪氦鏍镐繚",
    )
    return any(token and token in compact for token in submit_tokens)


async def _capture_submit_diagnostics(
    page: Any,
    *,
    phase: str,
    action_text: str,
) -> dict[str, Any] | None:
    screenshot_dir = os.environ.get("AGENT3_SUBMIT_SCREENSHOT_DIR")
    if not screenshot_dir:
        return None
    directory = Path(screenshot_dir)
    directory.mkdir(parents=True, exist_ok=True)
    safe_text = re.sub(r"[^0-9A-Za-z_.-]+", "-", str(action_text or "submit")).strip("-")[:60] or "submit"
    stem = f"{int(time.time() * 1000)}-{phase}-{safe_text}"
    screenshot_path = directory / f"{stem}.png"
    state_path = directory / f"{stem}.json"
    popup_state: dict[str, Any]
    try:
        await page.screenshot(path=str(screenshot_path), full_page=True)
    except Exception as exc:
        popup_state = {"screenshot_error": str(exc)}
    else:
        popup_state = {}
    trace = getattr(page, "_agent3_submit_trace", None)
    try:
        popup_state.update(
            await page.evaluate(
                """() => {
                    const textOf = el => String(
                        el?.innerText || el?.textContent || el?.value || el?.getAttribute?.('aria-label') || ''
                    ).replace(/\\s+/g, ' ').trim();
                    const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                    const cssSelector = el => {
                        if (!el) return '';
                        if (el.id) return `#${CSS.escape(el.id)}`;
                        const cls = String(el.className || '').split(/\\s+/).filter(Boolean)[0];
                        return cls ? `${el.tagName.toLowerCase()}.${CSS.escape(cls)}` : el.tagName.toLowerCase();
                    };
                    const popupSelector = [
                        '[role="dialog"]',
                        '.am-modal',
                        '.am-modal-content',
                        '.am-toast',
                        '.am-toast-text',
                        '.adm-modal',
                        '.adm-dialog',
                        '.adm-toast',
                        '.layui-layer',
                        '.modal',
                        '.toast',
                        '[class*="dialog"]',
                        '[class*="modal"]',
                        '[class*="popup"]',
                        '[class*="toast"]'
                    ].join(',');
                    const popups = Array.from(document.querySelectorAll(popupSelector))
                        .filter(visible)
                        .map(el => ({
                            selector: cssSelector(el),
                            className: String(el.className || ''),
                            text: textOf(el).slice(0, 1200),
                        }))
                        .filter(item => item.text);
                    const active = document.activeElement;
                    const bodyText = textOf(document.body).slice(0, 1800);
                    const controls = Array.from(document.querySelectorAll('input, textarea, select'))
                        .filter(visible)
                        .map(el => ({
                            tag: el.tagName.toLowerCase(),
                            type: String(el.type || ''),
                            value: String(el.value || '').slice(0, 120),
                            placeholder: String(el.placeholder || '').slice(0, 120),
                            className: String(el.className || '').slice(0, 120),
                            label: textOf(el.closest('.am-list-item,.am-textarea-item,label,li,div')).slice(0, 160),
                        }))
                        .slice(0, 80);
                    const buttons = Array.from(document.querySelectorAll('.insure-footer .submit-btn, .submit-btn, .am-button-primary, a[role="button"], button, [role="button"]'))
                        .filter(visible)
                        .map(el => {
                            const rect = el.getBoundingClientRect();
                            return {
                                text: textOf(el).slice(0, 120),
                                tag: el.tagName.toLowerCase(),
                                className: String(el.className || '').slice(0, 180),
                                ariaDisabled: String(el.getAttribute('aria-disabled') || ''),
                                disabled: !!el.disabled,
                                href: String(el.getAttribute('href') || ''),
                                rect: { left: rect.left, top: rect.top, width: rect.width, height: rect.height },
                            };
                        })
                        .slice(0, 40);
                    const errorRows = Array.from(document.querySelectorAll('.am-input-error,.am-list-item-error,[class*="error"],[class*="Error"]'))
                        .filter(visible)
                        .map(el => ({
                            className: String(el.className || '').slice(0, 180),
                            text: textOf(el).slice(0, 300),
                        }))
                        .filter(item => item.text || item.className)
                        .slice(0, 60);
                    const reduxErrors = [];
                    const pushFieldErrors = (root, prefix) => {
                        if (!root || typeof root !== 'object') return;
                        for (const [moduleId, rows] of Object.entries(root)) {
                            const list = Array.isArray(rows) ? rows : [rows];
                            list.forEach((row, rowIndex) => {
                                if (!row || typeof row !== 'object') return;
                                for (const [key, value] of Object.entries(row)) {
                                    if (!value || typeof value !== 'object') continue;
                                    if (value.hasError || value.hasAjaxError || value.error || value.errorMsg || value.msg || value.ajaxError) {
                                        reduxErrors.push({
                                            path: `${prefix}.${moduleId}[${rowIndex}].${key}`,
                                            value: String(value.value ?? '').slice(0, 120),
                                            hasError: !!value.hasError,
                                            hasAjaxError: !!value.hasAjaxError,
                                            error: !!value.error,
                                            errorMsg: String(value.errorMsg || '').slice(0, 240),
                                            msg: String(value.msg || '').slice(0, 240),
                                            ajaxError: String(value.ajaxError || '').slice(0, 240),
                                        });
                                    }
                                }
                            });
                        }
                    };
                    try {
                        const roots = [
                            [window.__NEXT_DATA__?.props?.pageProps?.initialReduxState?.product?.insure?.data?.data, 'next.product.insure.data.data'],
                            [window.__NEXT_DATA__?.props?.pageProps?.initialReduxState?.offline?.insure?.data?.data, 'next.offline.insure.data.data'],
                        ];
                        const store = window.__NEXT_REDUX_STORE__ || window.store || window.reduxStore;
                        if (store && typeof store.getState === 'function') {
                            const state = store.getState();
                            roots.push([state?.product?.insure?.data?.data, 'redux.product.insure.data.data']);
                            roots.push([state?.insure?.data?.data, 'redux.insure.data.data']);
                            if (state && typeof state.toJS === 'function') {
                                const plain = state.toJS();
                                roots.push([plain?.product?.insure?.data?.data, 'reduxJS.product.insure.data.data']);
                                roots.push([plain?.insure?.data?.data, 'reduxJS.insure.data.data']);
                            }
                        }
                        roots.forEach(([root, prefix]) => pushFieldErrors(root, prefix));
                    } catch (_) {}
                    const resources = performance.getEntriesByType('resource')
                        .filter(entry => /api|insure|submit|order|pay|underwrit|trial|bank|card|qixin|baoxin/i.test(entry.name || ''))
                        .slice(-80)
                        .map(entry => ({
                            name: String(entry.name || '').slice(0, 300),
                            initiatorType: String(entry.initiatorType || ''),
                            duration: Math.round(entry.duration || 0),
                            transferSize: entry.transferSize || 0,
                        }));
                    return {
                        url: location.href,
                        title: document.title,
                        bodyText,
                        popups,
                        activeElement: active ? {
                            tag: active.tagName.toLowerCase(),
                            type: String(active.type || ''),
                            value: String(active.value || '').slice(0, 120),
                            placeholder: String(active.placeholder || '').slice(0, 120),
                            className: String(active.className || '').slice(0, 120),
                        } : null,
                        controls,
                        buttons,
                        errorRows,
                        reduxErrors: reduxErrors.slice(0, 120),
                        resources,
                        agent3Trace: window.__agent3Trace || null,
                    };
                }"""
            )
        )
    except Exception as exc:
        popup_state["popup_probe_error"] = str(exc)
    if isinstance(trace, dict):
        popup_state["pythonTrace"] = {
            "requests": list(trace.get("requests", []) or [])[-80:],
            "responses": list(trace.get("responses", []) or [])[-80:],
            "console": list(trace.get("console", []) or [])[-80:],
            "pageerrors": list(trace.get("pageerrors", []) or [])[-40:],
        }
    payload = {
        "phase": phase,
        "action_text": action_text,
        "screenshot": str(screenshot_path),
        "state": popup_state,
    }
    try:
        state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except Exception as exc:
        payload["state_write_error"] = str(exc)
    payload["state_json"] = str(state_path)
    return payload


def _agent3_product_flow_url(current_url: str, leaf: str, encrypt_insure_num: str) -> str:
    parsed = urlparse(str(current_url or ""))
    path = parsed.path or ""
    if "/product/" in path:
        product_prefix = path.split("/product/", 1)[0] + "/product"
    elif path.endswith("/product"):
        product_prefix = path
    else:
        parts = path.strip("/").split("/")
        product_prefix = "/" + "/".join(parts[:4] + ["product"]) if len(parts) >= 4 else "/m/apps/cps/demo-channel/product"
    return urlunparse(
        parsed._replace(
            path=f"{product_prefix.rstrip('/')}/{leaf.lstrip('/')}",
            query=urlencode({"encryptInsureNum": encrypt_insure_num}),
            fragment="",
        )
    )


def _encrypt_insure_num_from_url(value: str) -> str:
    try:
        params = parse_qs(urlparse(str(value or "")).query)
    except Exception:
        return ""
    return str((params.get("encryptInsureNum") or [""])[0] or "").strip()


def _submit_policy_start_date_window_reason(
    submit_diagnostics: list[dict[str, Any]] | None,
) -> str | None:
    if not submit_diagnostics:
        return None

    fragments: list[str] = []

    def collect(value: Any) -> None:
        if value is None:
            return
        if isinstance(value, str):
            fragments.append(value)
            return
        if isinstance(value, dict):
            for item in value.values():
                collect(item)
            return
        if isinstance(value, (list, tuple)):
            for item in value:
                collect(item)

    collect(submit_diagnostics)
    text = " ".join(" ".join(item.split()) for item in fragments if item)
    if not text:
        return None
    compact = "".join(text.split())
    if "起保" not in compact:
        return None
    if "可选区间" not in compact and "应在" not in compact:
        return None
    match = re.search(
        r"(\d{4}-\d{2}-\d{2})(?:00:00:00)?(?:至|到|-)(\d{4}-\d{2}-\d{2})",
        compact,
    )
    if not match:
        return "Policy start date window after submit click"
    return (
        "Policy start date window after submit click: "
        f"{match.group(1)} to {match.group(2)}"
    )


async def _direct_submit_after_bank_validation_loop(
    page: Any,
    *,
    source_url: str | None = None,
    submit_diagnostics: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    if not hasattr(page, "evaluate"):
        return None
    loop_reason = _submit_bank_card_validation_loop_reason(page)
    trigger_reason = loop_reason or _submit_policy_start_date_window_reason(submit_diagnostics)
    if not trigger_reason:
        return None
    source_url = source_url or str(getattr(page, "url", "") or "")
    try:
        result = await page.evaluate(
            """async () => {
                const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
                const toPlain = value => {
                    if (value == null) return value;
                    if (typeof value?.toJS === 'function') {
                        try { return value.toJS(); } catch (_) {}
                    }
                    if (typeof value?.toJSON === 'function') {
                        try { return value.toJSON(); } catch (_) {}
                    }
                    return value;
                };
                const isObject = value => value && typeof value === 'object' && !Array.isArray(value);
                const looksLikeModuleRoot = value => isObject(toPlain(value))
                    && Object.keys(toPlain(value)).some(key => /^\\d+$/.test(key));
                const looksLikeInsureContainer = value => {
                    const plain = toPlain(value);
                    return isObject(plain) && isObject(plain.data) && looksLikeModuleRoot(plain.data);
                };
                const hasSubmitTopLevel = value => {
                    const plain = toPlain(value);
                    return isObject(plain) && ['productId', 'productPlanId', 'encryptInsureNum', 'trialGenes', 'notifyAnswerId', 'insureNum']
                        .some(key => plain[key] !== undefined && plain[key] !== null && plain[key] !== '');
                };
                const fieldValue = value => {
                    const plain = toPlain(value);
                    if (plain == null) return plain;
                    if (Array.isArray(plain)) return plain.map(fieldValue);
                    if (!isObject(plain)) return plain;
                    for (const key of ['value', 'controlValue', 'code', 'id', 'text', 'label', 'name', 'valueText']) {
                        if (plain[key] !== undefined && plain[key] !== null && plain[key] !== '') return plain[key];
                    }
                    return plain;
                };
                const normalizeRecord = record => {
                    const plain = toPlain(record);
                    if (!isObject(plain)) return plain;
                    const out = {};
                    for (const [key, value] of Object.entries(plain)) {
                        if (/^(hasError|hasAjaxError|error|errorMsg|msg|ajaxError|validateStatus|validStatus|required|isRequired|needValid|validate|display|hidden)$/.test(key)) continue;
                        const normalized = fieldValue(value);
                        if (isObject(normalized)) continue;
                        out[key] = normalized;
                    }
                    return out;
                };
                const normalizeRows = rows => {
                    const plain = toPlain(rows);
                    if (Array.isArray(plain)) return plain.map(normalizeRecord).filter(row => isObject(row) && Object.keys(row).length);
                    if (isObject(plain)) {
                        const row = normalizeRecord(plain);
                        return isObject(row) && Object.keys(row).length ? [row] : [];
                    }
                    return [];
                };
                const normalizeData = dataRoot => {
                    let root = toPlain(dataRoot);
                    const out = {};
                    if (!isObject(root)) return out;
                    if (!looksLikeModuleRoot(root) && looksLikeModuleRoot(root.data)) root = root.data;
                    for (const [moduleId, rows] of Object.entries(root)) {
                        if (!/^\\d+$/.test(moduleId)) continue;
                        const normalizedRows = normalizeRows(rows);
                        if (normalizedRows.length) out[String(moduleId)] = normalizedRows;
                    }
                    return out;
                };
                const plainTopLevel = container => {
                    const plain = toPlain(container);
                    const out = {};
                    if (!isObject(plain)) return out;
                    for (const [key, value] of Object.entries(plain)) {
                        if (key === 'data') continue;
                        if (/^\\d+$/.test(key)) continue;
                        if (/^(loading|error|errors|validate|validator|ajaxError|hasError|hasAjaxError)$/.test(key)) continue;
                        out[key] = fieldValue(value);
                    }
                    return out;
                };
                function normalizeSubmitTrialGenes(rawTrialGenes, payload) {
                    let trialGenes = rawTrialGenes;
                    if (typeof trialGenes === 'string') {
                        try { trialGenes = JSON.parse(trialGenes); } catch (_) { return rawTrialGenes; }
                    }
                    if (!isObject(trialGenes)) return rawTrialGenes;
                    if (!Array.isArray(trialGenes.genes)) trialGenes.genes = [];
                    const applicantRow = payload.data?.['10']?.[0] || {};
                    const insuredRow = payload.data?.['20']?.[0] || applicantRow;
                    const setTrialGene = (key, value) => {
                        if (value === undefined || value === null || value === '') return;
                        let item = trialGenes.genes.find(gene => gene?.key === key);
                        if (!item) {
                            item = { sort: trialGenes.genes.length + 1, protectItemId: '', key, value: String(value) };
                            trialGenes.genes.push(item);
                        } else {
                            item.value = String(value);
                        }
                    };
                    setTrialGene('insurantDate', insuredRow.birthdate || applicantRow.birthdate);
                    const sexValue = String(insuredRow.sex || applicantRow.sex || '');
                    if (sexValue) setTrialGene('sex', sexValue === '2' ? '\\u5973' : '\\u7537');
                    if (payload.productId != null) trialGenes.productId = payload.productId;
                    if (payload.productPlanId != null) trialGenes.productPlanId = payload.productPlanId;
                    return JSON.stringify(trialGenes);
                }
                function summarizeTrialGenes(rawTrialGenes) {
                    let trialGenes = rawTrialGenes;
                    if (typeof trialGenes === 'string') {
                        try { trialGenes = JSON.parse(trialGenes); } catch (_) { return String(rawTrialGenes || '').slice(0, 200); }
                    }
                    if (!isObject(trialGenes) || !Array.isArray(trialGenes.genes)) return trialGenes;
                    return trialGenes.genes.filter(gene => gene?.key).map(gene => `${gene.key}=${gene.value}`).join(';');
                }
                function normalizeBankNameForSubmit(value) {
                    const text = String(value || '').trim();
                    if (!text) return '工商银行';
                    if (/^(1|102)$/.test(text)) return '工商银行';
                    return text.includes('工商') ? '工商银行' : text;
                }
                function submitPriceFromTrialGenes(rawTrialGenes) {
                    let trialGenes = rawTrialGenes;
                    if (typeof trialGenes === 'string') {
                        try { trialGenes = JSON.parse(trialGenes); } catch (_) { return null; }
                    }
                    if (!isObject(trialGenes) || !Array.isArray(trialGenes.genes)) return null;
                    const premiumGene = trialGenes.genes.find(gene => gene?.key === 'premium');
                    const amount = Number(String(premiumGene?.value || '').replace(/[^0-9.]/g, ''));
                    return Number.isFinite(amount) && amount > 0 ? amount : null;
                }
                function alignNativeSubmitPayload(payload) {
                    payload.autoRenewal = payload.autoRenewal === true
                        || payload.autoRenewal === 1
                        || payload.autoRenewal === '1'
                        || String(payload.autoRenewal || '').toLowerCase() === 'true';
                    if (payload.autoRenewal) payload.renewalCheck = payload.renewalCheck ?? 1;
                    else payload.renewalCheck = 0;
                    const applicantRow = payload.data?.['10']?.[0];
                    const insuredRow = payload.data?.['20']?.[0];
                    if (isObject(insuredRow)) {
                        insuredRow.addressIsSameApplicant = insuredRow.addressIsSameApplicant ?? '';
                    }
                    const bankRow = payload.data?.['107']?.[0];
                    if (isObject(bankRow)) {
                        const bankName = normalizeBankNameForSubmit(
                            bankRow.bankName || bankRow.bankText || bankRow.openBank || bankRow.bank || window.__agent3BankName
                        );
                        bankRow.bankName = bankName;
                        bankRow.bank = bankRow.bank || '1';
                        bankRow.cardOwner = bankRow.cardOwner || applicantRow?.cName || insuredRow?.cName || '';
                    }
                    payload.price = submitPriceFromTrialGenes(payload.trialGenes) || payload.price;
                }
                function extractAllowedStartDate(text) {
                    const match = String(text || '').match(/(\\d{4}-\\d{2}-\\d{2})\\s+00:00:00/);
                    return match ? match[1] : '';
                }
                function setPayloadStartDate(payload, value) {
                    if (!/^\\d{4}-\\d{2}-\\d{2}$/.test(String(value || ''))) return false;
                    payload.startDate = value;
                    if (payload.data?.['102']?.[0]) payload.data['102'][0].insuranceDate = value;
                    return true;
                }
                async function submitPayload(payload) {
                    const response = await fetch(`/api/apps/cps/insure/submit?md=${Math.random()}`, {
                        method: 'POST',
                        credentials: 'include',
                        headers: {
                            'content-type': 'application/json;charset=UTF-8',
                            'accept': 'application/json, text/plain, */*',
                        },
                        body: JSON.stringify(payload),
                    });
                    const text = await response.text();
                    let body = null;
                    try { body = JSON.parse(text); } catch (_) {}
                    const data = body?.data || {};
                    const code = String(body?.code ?? body?.errorCode ?? data?.errorCode ?? '');
                    return { response, text, body, data, code, retry_reason: '' };
                }
                const storeCandidates = [
                    window.__NEXT_REDUX_STORE__,
                    window.store,
                    window.reduxStore,
                    ...Object.keys(window)
                        .filter(key => /store|redux/i.test(key))
                        .map(key => {
                            try { return window[key]; } catch (_) { return null; }
                        }),
                ].filter((store, index, array) => store && array.indexOf(store) === index);
                const stateCandidates = [];
                for (const store of storeCandidates) {
                    try {
                        if (typeof store.getState === 'function') stateCandidates.push(toPlain(store.getState()));
                    } catch (_) {}
                }
                stateCandidates.push(toPlain(window.__NEXT_DATA__));
                for (const storage of [window.localStorage, window.sessionStorage]) {
                    if (!storage) continue;
                    for (let index = 0; index < storage.length; index += 1) {
                        const key = storage.key(index);
                        if (!key || !/insure|product/i.test(key)) continue;
                        try {
                            const raw = storage.getItem(key);
                            if (raw && raw[0] === '{') stateCandidates.push(JSON.parse(raw));
                        } catch (_) {}
                    }
                }
                const containerCandidates = [];
                for (const state of stateCandidates) {
                    if (!state || typeof state !== 'object') continue;
                    containerCandidates.push(state?.product?.insure?.data, state?.insure?.data, state?.data, state);
                }
                const containers = containerCandidates.filter(container => container && typeof container === 'object');
                const primary = containers.find(container => looksLikeInsureContainer(container) && hasSubmitTopLevel(container))
                    || containers.find(container => looksLikeInsureContainer(container))
                    || containers[0];
                if (!primary) return { attempted: true, order_generated: false, reason: 'insure_state_not_found' };
                const payload = { ...plainTopLevel(primary), data: normalizeData(primary) };
                for (const container of containers) {
                    if (looksLikeInsureContainer(container) && hasSubmitTopLevel(container)) {
                        const top = plainTopLevel(container);
                        for (const [key, value] of Object.entries(top)) {
                            if (payload[key] === undefined || payload[key] === null || payload[key] === '') payload[key] = value;
                        }
                    }
                    const data = normalizeData(container);
                    for (const [moduleId, rows] of Object.entries(data)) {
                        if (!payload.data[moduleId] || !payload.data[moduleId].length) payload.data[moduleId] = rows;
                    }
                }
                for (const key of Object.keys(payload)) {
                    if (/^\\d+$/.test(key)) delete payload[key];
                }
                const params = new URLSearchParams(window.location.search);
                const notifyAnswerId = params.get('notifyAnswerId');
                const encryptInsureNum = params.get('encryptInsureNum');
                if (encryptInsureNum) payload.encryptInsureNum = encryptInsureNum;
                if (notifyAnswerId && !Number.isNaN(Number(notifyAnswerId))) payload.notifyAnswerId = Number(notifyAnswerId);
                if (payload.startDate && payload.data['102']?.[0]) payload.data['102'][0].insuranceDate = payload.startDate;
                if (!payload.startDate && payload.data['102']?.[0]?.insuranceDate) payload.startDate = payload.data['102'][0].insuranceDate;
                if (!payload.data['30']?.length) payload.data['30'] = [{ relationInsureBeneficiary: 1, insurantIndex: 0 }];
                else {
                    payload.data['30'][0].relationInsureBeneficiary = payload.data['30'][0].relationInsureBeneficiary ?? 1;
                    payload.data['30'][0].insurantIndex = payload.data['30'][0].insurantIndex ?? 0;
                }
                if (!payload.data['101']?.length) payload.data['101'] = [{ urgencyContact: '', urgencyContactPhone: '' }];
                else {
                    payload.data['101'][0].urgencyContact = payload.data['101'][0].urgencyContact || '';
                    payload.data['101'][0].urgencyContactPhone = payload.data['101'][0].urgencyContactPhone || '';
                }
                Object.assign(payload, {
                    isHealthSuccess: true,
                    healthWarningContinueInsure: payload.healthWarningContinueInsure ?? 0,
                    continueInsure: payload.continueInsure ?? 0,
                    traceInsuranceDate: payload.traceInsuranceDate ?? false,
                    insureInsurantType: payload.insureInsurantType || 20,
                    insureBeneficiaryType: payload.insureBeneficiaryType || 1,
                    source: payload.source || 2,
                    merchantId: payload.merchantId || 1000014,
                    aid: payload.aid || '',
                    manualUnderwritingType: payload.manualUnderwritingType ?? 0,
                    editable: payload.editable ?? 1,
                    tenantId: payload.tenantId ?? 0,
                    userId: payload.userId ?? -1,
                    operatInsureFlow: payload.operatInsureFlow ?? 0,
                    autoDeductionAgeRequireCheck: payload.autoDeductionAgeRequireCheck ?? 0,
                    isEmptyData: payload.isEmptyData ?? true,
                    traceInsuredDateNew: payload.traceInsuredDateNew ?? 2,
                    confirmInsureRiskTask: payload.confirmInsureRiskTask ?? 0,
                    companyDiscountPremiumCheck: payload.companyDiscountPremiumCheck ?? false,
                    renewalCheck: payload.renewalCheck ?? 0,
                    isAudit: payload.isAudit ?? 0,
                    standardAuditSwitch: payload.standardAuditSwitch ?? false,
                    extraDiscountConfig: payload.extraDiscountConfig ?? false,
                    platform: payload.platform ?? 1,
                    traceInsuredDate: payload.traceInsuredDate ?? false,
                    isUpdate: payload.isUpdate ?? 0,
                    verifyType: payload.verifyType ?? 0,
                    repurchase: payload.repurchase ?? 0,
                    fromStandardAudit: payload.fromStandardAudit ?? false,
                    isFixedStartDateForRenewal: payload.isFixedStartDateForRenewal ?? 0,
                    insureSource: payload.insureSource ?? 0,
                    confirmSmartAuditGeneAdjust: payload.confirmSmartAuditGeneAdjust ?? false,
                    saveType: payload.saveType ?? 0,
                    unableChangeTemp: payload.unableChangeTemp ?? 0,
                    secondAuditInsureTaskStatus: payload.secondAuditInsureTaskStatus ?? 0,
                    againSubmit: payload.againSubmit ?? false,
                    personnelType: payload.personnelType ?? 20,
                    ignoreCertTask: payload.ignoreCertTask ?? false,
                    applicantType: payload.applicantType ?? 0,
                    busErrorButtonConsole: payload.busErrorButtonConsole ?? true,
                    isTemp: payload.isTemp ?? 0,
                    manualUnderwritingCheck: payload.manualUnderwritingCheck ?? 0,
                    isPay: payload.isPay ?? false,
                    isAp: payload.isAp ?? false,
                    autoRenewal: payload.autoRenewal ?? false,
                    isAdditionalEdu: payload.isAdditionalEdu ?? false,
                    cid: payload.cid ?? -1,
                    buyCountType: payload.buyCountType ?? 0,
                    totalNum: payload.totalNum ?? 1,
                });
                payload.autoRenewal = payload.autoRenewal === true
                    || payload.autoRenewal === 1
                    || payload.autoRenewal === '1'
                    || String(payload.autoRenewal || '').toLowerCase() === 'true';
                if (!payload.autoRenewal) payload.renewalCheck = 0;
                alignNativeSubmitPayload(payload);
                payload.signReturnUtl = payload.signReturnUtl || (() => {
                    const productPrefix = window.location.pathname.match(/^(.*\\/product)\\/insure/)?.[1];
                    if (!productPrefix || !payload.encryptInsureNum) return payload.signReturnUtl;
                    return `${window.location.origin}${productPrefix}/task?encryptInsureNum=${encodeURIComponent(payload.encryptInsureNum)}`;
                })();
                const readMaybeAsync = async fn => {
                    try {
                        const value = fn();
                        const resolved = value && typeof value.then === 'function'
                            ? await Promise.race([value, sleep(5000).then(() => '')])
                            : value;
                        return typeof resolved === 'string' && resolved ? resolved : '';
                    } catch (_) {
                        return '';
                    }
                };
                payload.verifyCode = payload.verifyCode || await readMaybeAsync(() => window.getNVCVal?.())
                    || await readMaybeAsync(() => window.nvc?.getNVCVal?.())
                    || await readMaybeAsync(() => window.NVC_Opt?.getNVCVal?.())
                    || window.__agent3VerifyCode
                    || '';
                if (payload.trialGenes) payload.trialGenes = normalizeSubmitTrialGenes(payload.trialGenes, payload);
                if (payload.confirmItem && typeof payload.confirmItem !== 'string') payload.confirmItem = JSON.stringify(payload.confirmItem);
                for (const [key, value] of Object.entries({ ...payload })) {
                    if (key !== 'data' && isObject(value)) delete payload[key];
                }
                if (!payload.productId || !payload.productPlanId || !Object.keys(payload.data || {}).length || !payload.encryptInsureNum) {
                    return {
                        attempted: true,
                        order_generated: false,
                        reason: 'submit_payload_incomplete',
                        payload_summary: {
                            productId: payload.productId,
                            productPlanId: payload.productPlanId,
                            encryptInsureNum: payload.encryptInsureNum,
                            modules: Object.keys(payload.data || {}),
                        },
                    };
                }
                let submitResult = await submitPayload(payload);
                let { response, text, body, data, code } = submitResult;
                const retryDate = extractAllowedStartDate(text);
                let retriedPolicyStartDate = false;
                if (code === '37202' && retryDate) {
                    retriedPolicyStartDate = setPayloadStartDate(payload, retryDate);
                }
                if (retriedPolicyStartDate) {
                    alignNativeSubmitPayload(payload);
                    if (payload.trialGenes) payload.trialGenes = normalizeSubmitTrialGenes(payload.trialGenes, payload);
                    submitResult = { ...(await submitPayload(payload)), retry_reason: 'policy-start-date-window' };
                    ({ response, text, body, data, code } = submitResult);
                }
                const taskHandoff = code === '37009'
                    && Boolean(data.insureNum)
                    && Boolean(data.encryptInsureNum)
                    && Array.isArray(data.insureTaskList);
                const directOrder = code === '0' && Boolean(data.insureNum || data.encryptInsureNum || payload.insureNum || payload.encryptInsureNum);
                const suitabilityTask = code === '40015' && Boolean(data.encryptInsureNum || payload.encryptInsureNum);
                return {
                    attempted: true,
                    order_generated: response.ok && (taskHandoff || directOrder || suitabilityTask),
                    task_handoff: taskHandoff,
                    direct_order: directOrder,
                    suitability_task: suitabilityTask,
                    status: response.status,
                    ok: response.ok,
                    url: response.url,
                    code,
                    msg: body?.msg || data?.errorMessage || '',
                    payload_summary: {
                        productId: payload.productId,
                        productPlanId: payload.productPlanId,
                        encryptInsureNum: payload.encryptInsureNum,
                        notifyAnswerId: payload.notifyAnswerId,
                        startDate: payload.startDate,
                        insuranceDate: payload.data?.['102']?.[0]?.insuranceDate,
                        applicantRegion: payload.data?.['10']?.[0]?.provCityText,
                        insuredRegion: payload.data?.['20']?.[0]?.provCityText,
                        applicantJob: payload.data?.['10']?.[0]?.jobText,
                        insuredJob: payload.data?.['20']?.[0]?.jobText,
                        applicantBirth: payload.data?.['10']?.[0]?.birthdate,
                        insuredBirth: payload.data?.['20']?.[0]?.birthdate,
                        trialGenes: summarizeTrialGenes(payload.trialGenes),
                        modules: Object.keys(payload.data || {}),
                        hasVerifyCode: Boolean(payload.verifyCode),
                        payloadKeys: Object.keys(payload),
                        retryReason: submitResult.retry_reason || '',
                    },
                    response_order: {
                        insureNum: data.insureNum || payload.insureNum || '',
                        encryptInsureNum: data.encryptInsureNum || payload.encryptInsureNum || '',
                        taskCount: Array.isArray(data.insureTaskList) ? data.insureTaskList.length : 0,
                    },
                    body_excerpt: text.slice(0, 2000),
                };
            }"""
        )
    except Exception as exc:
        result = {"attempted": True, "order_generated": False, "error": str(exc)[:500]}
    if not isinstance(result, dict) or not result.get("attempted"):
        return None

    trace = getattr(page, "_agent3_submit_trace", None)
    if isinstance(trace, dict) and result.get("url"):
        responses = trace.setdefault("responses", [])
        if isinstance(responses, list):
            responses.append(
                {
                    "ts": int(time.time() * 1000),
                    "status": int(result.get("status") or 0),
                    "url": str(result.get("url") or "")[:500],
                    "content_type": "application/json",
                    "body": str(result.get("body_excerpt") or "")[:20000],
                    "direct_submit_after_bank_validation_loop": True,
                }
            )

    response_order = result.get("response_order") if isinstance(result.get("response_order"), dict) else {}
    payload_summary = result.get("payload_summary") if isinstance(result.get("payload_summary"), dict) else {}
    encrypt_insure_num = str(
        response_order.get("encryptInsureNum")
        or payload_summary.get("encryptInsureNum")
        or ""
    ).strip()
    target_url = str(getattr(page, "url", "") or "")
    if result.get("order_generated") and encrypt_insure_num and hasattr(page, "goto"):
        leaf = "adapt/loading" if result.get("suitability_task") else "task"
        target_url = _agent3_product_flow_url(target_url or source_url, leaf, encrypt_insure_num)
        try:
            await page.goto(target_url, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(_POST_CLICK_SETTLE_MS)
        except Exception:
            target_url = str(getattr(page, "url", "") or target_url)
    else:
        target_url = str(getattr(page, "url", "") or target_url)

    summary_parts = [
        f"code={result.get('code')}",
        f"order_generated={bool(result.get('order_generated'))}",
        f"task_handoff={bool(result.get('task_handoff'))}",
        f"direct_order={bool(result.get('direct_order'))}",
        f"suitability_task={bool(result.get('suitability_task'))}",
    ]
    if result.get("reason"):
        summary_parts.append(f"reason={result.get('reason')}")
    if result.get("msg"):
        summary_parts.append(f"msg={str(result.get('msg'))[:120]}")
    record = {
        "text": "direct submit after bank validation loop: " + ", ".join(summary_parts),
        "tag": "xhr",
        "selector": "/api/apps/cps/insure/submit",
        "source_url": source_url,
        "target_url": target_url,
        "score": None,
        "click_strategy": "direct-submit-after-bank-validation-loop",
        "dismissed_overlays": [],
        "action_type": "submit_api",
        "submit_api_result": result,
        "blocked_reason_before_direct_submit": trigger_reason,
    }
    if submit_diagnostics:
        record["submit_diagnostics"] = submit_diagnostics
    return record


async def _body_text_full(page: Any) -> str:
    try:
        body_text = await page.locator("body").inner_text(timeout=2_000)
    except Exception:
        return ""
    return " ".join(body_text.split())


async def _collect_page_state(page: Any) -> dict[str, Any]:
    """Collect structured state for generic exploration repair decisions."""
    body_text = await _body_text_full(page)
    try:
        browser_state = await page.evaluate(
            """() => {
                function textOf(el) {
                    if (!el) return '';
                    return String(
                        el.value || el.getAttribute('aria-label') || el.textContent || el.innerText || ''
                    ).replace(/\\s+/g, ' ').trim();
                }
                function visible(el) {
                    return !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                }
                function cssSelector(el) {
                    if (!el) return '';
                    if (el.id) return `#${CSS.escape(el.id)}`;
                    if (el.name) return `${el.tagName.toLowerCase()}[name="${CSS.escape(el.name)}"]`;
                    const cls = String(el.className || '').split(/\\s+/).filter(Boolean)[0];
                    return cls ? `${el.tagName.toLowerCase()}.${CSS.escape(cls)}` : el.tagName.toLowerCase();
                }
                function labelOf(el) {
                    return [
                        el.name,
                        el.id,
                        el.placeholder,
                        el.getAttribute('aria-label'),
                        el.closest('label')?.innerText,
                        el.closest('.form-item,.form-group,.el-form-item,li,dd,div')?.innerText,
                    ].filter(Boolean).join(' ').replace(/\\s+/g, ' ').trim();
                }
                function placeholderLike(value, label) {
                    const text = `${value || ''} ${label || ''}`;
                    return /请选择|请选择|选择|未选择|--/.test(text) && !/北京市|中国|身份证|子女|他人/.test(text);
                }
                const controls = Array.from(document.querySelectorAll('input, textarea, select, [role=checkbox], [role=radio]'))
                    .map((el) => {
                        const tag = el.tagName.toLowerCase();
                        const type = String(el.type || el.getAttribute('role') || tag).toLowerCase();
                        const label = labelOf(el);
                        const value = tag === 'select'
                            ? (el.options && el.selectedIndex >= 0 ? (el.options[el.selectedIndex].text || el.value || '') : '')
                            : String(el.value || textOf(el) || '');
                        const kind = tag === 'select' || /select|dropdown/.test(String(el.className || ''))
                            ? 'select'
                            : ['checkbox', 'radio'].includes(type)
                            ? type
                            : 'input';
                        return {
                            kind,
                            tag,
                            type,
                            name: el.name || '',
                            id: el.id || '',
                            label: label.slice(0, 240),
                            value: value.slice(0, 160),
                            checked: !!el.checked || el.getAttribute('aria-checked') === 'true' || /checked|active|selected|is-checked/.test(String(el.className || '')),
                            required_like: !!(el.required || el.getAttribute('required') !== null || el.getAttribute('aria-required') === 'true' || /\\*/.test(label)),
                            placeholder_like: placeholderLike(value, label),
                            visible: visible(el),
                            disabled: !!el.disabled,
                            readonly: !!el.readOnly,
                            selector: cssSelector(el),
                        };
                    })
                    .filter(item => item.visible)
                    .slice(0, 220);
                const groups = [
                    {
                        group_id: 'read_and_agree_primary',
                        token: '本人充分阅读',
                        required: ['投保声明', '续期授权声明'],
                    },
                    {
                        group_id: 'read_and_agree_terms',
                        token: '本人已逐页阅读',
                        required: ['保险条款', '责任免除', '隐私政策声明'],
                    },
                ];
                const controlSelector = 'input[type=checkbox], [role=checkbox], .checkbox, [class*=checkbox], .hz-check-item, [class*=check], [class*=agree]';
                const allElements = Array.from(document.querySelectorAll('body *')).slice(0, 2500);
                function controlSatisfied(el) {
                    if (!el) return false;
                    const input = el.matches?.('input[type=checkbox]') ? el : el.querySelector?.('input[type=checkbox]');
                    return !!(input?.checked)
                        || el.getAttribute?.('aria-checked') === 'true'
                        || /checked|active|selected|is-checked/.test(String(el.className || ''));
                }
                function nearestAgreementGroup(group) {
                    const directRows = allElements
                        .map((el, index) => {
                            const text = textOf(el);
                            if (text.length > 800) return null;
                            if (!text.includes(group.token)) return null;
                            if (!group.required.every(item => text.includes(item))) return null;
                            const controls = Array.from(el.querySelectorAll(controlSelector));
                            if (!controls.length && !el.matches(controlSelector)) return null;
                            const target = el.matches(controlSelector) ? el : (controls.find(item => visible(item)) || controls[0]);
                            return {
                                group_id: group.group_id,
                                text: text.slice(0, 240),
                                selector: cssSelector(target),
                                satisfied: controlSatisfied(target),
                                index,
                                score: 5000 - text.length,
                            };
                        })
                        .filter(Boolean)
                        .sort((left, right) => right.score - left.score || left.index - right.index);
                    if (directRows.length) return directRows[0];
                    const candidates = allElements
                        .map((el, index) => {
                            const tag = el.tagName.toLowerCase();
                            const text = textOf(el);
                            if (text.length > 1200) return null;
                            if (!text.includes(group.token)) return null;
                            if (['a', 'button', 'script', 'style'].includes(tag)) return null;
                            if (!group.required.every(item => text.includes(item))) return null;
                            const childHasSameLine = Array.from(el.children || []).some(child => {
                                const childTag = child.tagName.toLowerCase();
                                return childTag !== 'a' && textOf(child).includes(group.token);
                            });
                            if (childHasSameLine && !el.matches(controlSelector) && !el.querySelector(controlSelector)) return null;
                            let root = el;
                            for (let depth = 0; root && depth < 5; depth += 1) {
                                const rootText = textOf(root);
                                if (rootText.length > 1200) {
                                    root = root.parentElement;
                                    continue;
                                }
                                const controls = Array.from(root.querySelectorAll(controlSelector));
                                if (rootText.includes(group.token) && group.required.every(item => rootText.includes(item)) && controls.length) {
                                    const target = controls.find(item => visible(item)) || controls[0];
                                    return {
                                        group_id: group.group_id,
                                        text: rootText.slice(0, 240),
                                        selector: cssSelector(target),
                                        satisfied: controls.some(controlSatisfied),
                                        index,
                                        score: 2000 - rootText.length - depth,
                                    };
                                }
                                root = root.parentElement;
                            }
                            return {
                                group_id: group.group_id,
                                text: text.slice(0, 240),
                                selector: cssSelector(el),
                                satisfied: false,
                                index,
                                score: 1000 - text.length,
                            };
                        })
                        .filter(Boolean)
                        .sort((left, right) => right.score - left.score || left.index - right.index);
                    return candidates[0] || null;
                }
                return {
                    controls,
                    agreement_groups: groups.map(nearestAgreementGroup).filter(Boolean),
                    blocking_overlays: Array.from(document.querySelectorAll(
                        '[role=dialog], .layui-layer, .layui-layer-dialog, .layui-layer-content, .modal, .ant-modal, .el-message-box, .toast, [class*=dialog], [class*=modal], [class*=popup], [class*=toast]'
                    ))
                        .filter(el => visible(el))
                        .map((el) => ({
                            text: textOf(el).slice(0, 240),
                            selector: cssSelector(el),
                        }))
                        .filter(item => item.text),
                };
            }"""
        )
    except Exception:
        browser_state = {"controls": [], "agreement_groups": [], "blocking_overlays": []}
    validation_feedback = [
        token
        for token in (
            "请先阅读并同意相关协议",
            "请先阅读并同意",
            "请选择",
            "请输入",
            "不能为空",
            "必填",
            "未填写",
            "格式不正确",
            "校验失败",
            "该单正在处理中",
            "投保正在处理中",
            "请稍后操作",
            "正在加载",
            "请稍后",
        )
        if token in body_text
    ]
    return {
        "url": page.url,
        "body_text_excerpt": body_text[:1200],
        "validation_feedback": validation_feedback,
        "controls": list(browser_state.get("controls", []) or []),
        "agreement_groups": list(browser_state.get("agreement_groups", []) or []),
        "blocking_overlays": list(browser_state.get("blocking_overlays", []) or []),
    }


def _blocking_overlay_reason(snapshot: dict[str, Any]) -> str | None:
    page_state = snapshot.get("page_state") or {}
    overlay_texts = [
        str(item.get("text") or "")
        for item in page_state.get("blocking_overlays", []) or []
        if isinstance(item, dict)
    ]
    overlay_texts.append(str(snapshot.get("body_text_excerpt") or ""))
    for text in overlay_texts:
        normalized = " ".join(text.split())
        if not normalized:
            continue
        if any(token in normalized for token in ("已阅读并同意", "阅读并同意")) and any(
            token in normalized for token in ("投保条件", "投保重要告知", "保险条款", "重要提示", "免责条款")
        ):
            continue
        if any(token in normalized for token in ("该单正在处理中", "投保正在处理中", "请稍后操作", "正在加载", "请稍后")):
            return f"Blocking overlay after submit: {normalized[:160]}"
        if any(title in normalized for title in ("信息", "提示", "温馨提示", "确认")) and (
            ("被保" in normalized and "周岁" in normalized)
            or ("被保" in normalized and "年龄" in normalized)
        ):
            return f"Blocking overlay after submit: {normalized[:160]}"
    return None


def _is_processing_overlay_reason(reason: str | None) -> bool:
    normalized = " ".join(str(reason or "").split())
    return any(token in normalized for token in ("该单正在处理中", "投保正在处理中", "请稍后操作", "正在加载", "请稍后"))


def _recent_api_error_response_events(limit: int = 80) -> list[dict[str, Any]]:
    run_dir = os.environ.get("AGENT3_RUN_DIR")
    if not run_dir:
        return []
    path = Path(run_dir) / "api-errors.jsonl"
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()[-limit:]
    except Exception:
        return []
    responses: list[dict[str, Any]] = []
    for line in lines:
        try:
            item = json.loads(line)
        except Exception:
            continue
        if isinstance(item, dict) and item.get("event") == "response":
            responses.append(item)
    return responses


def _submit_trace_responses(page: Any, *, trace_limit: int = 40, file_limit: int = 80) -> list[dict[str, Any]]:
    responses: list[dict[str, Any]] = []
    trace = getattr(page, "_agent3_submit_trace", None)
    if isinstance(trace, dict) and isinstance(trace.get("responses"), list):
        responses.extend(item for item in trace.get("responses", [])[-trace_limit:] if isinstance(item, dict))
    responses.extend(_recent_api_error_response_events(limit=file_limit))
    return responses


def _submit_backend_unavailable_reason(page: Any) -> str | None:
    trace = getattr(page, "_agent3_submit_trace", None)
    responses = _submit_trace_responses(page)
    if not isinstance(trace, dict) and not responses:
        return None

    failures: list[tuple[str, str]] = []
    fatal_seen = False
    for item in responses[-80:]:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "")
        if not any(fragment in url for fragment in _BACKEND_UNAVAILABLE_API_FRAGMENTS):
            continue
        try:
            status = int(item.get("status") or 0)
        except (TypeError, ValueError):
            status = 0
        body = str(item.get("body") or "")
        payload: dict[str, Any] = {}
        try:
            parsed = json.loads(body) if body else {}
        except Exception:
            parsed = {}
        if isinstance(parsed, dict):
            payload = parsed
        exception = str(payload.get("exception") or "")
        msg = str(payload.get("msg") or payload.get("message") or "")
        haystack = " ".join([body, exception, msg]).lower()
        code = str(payload.get("code") or "")
        success = payload.get("success")
        is_fatal_backend_unavailable = success is False and code == "-1" and any(
            marker in haystack for marker in _BACKEND_UNAVAILABLE_FATAL_TEXT_MARKERS
        )
        is_backend_unavailable = status >= 500 or is_fatal_backend_unavailable or (
            success is False
            and code == "-1"
            and any(marker in haystack for marker in _BACKEND_UNAVAILABLE_TEXT_MARKERS)
        )
        if not is_backend_unavailable:
            continue
        fatal_seen = fatal_seen or is_fatal_backend_unavailable
        endpoint = urlparse(url).path or url
        detail = exception or msg or f"status={status}"
        failures.append((endpoint, detail[:160]))

    min_failures = 1 if fatal_seen else _BACKEND_UNAVAILABLE_MIN_FAILURES
    if len(failures) < min_failures:
        return None
    endpoint, detail = failures[-1]
    return (
        "Backend/API unavailable while waiting submit processing: "
        f"{len(failures)} recent failures, last={endpoint} {detail}"
    )


def _submit_session_boundary_reason(page: Any) -> str | None:
    trace = getattr(page, "_agent3_submit_trace", None)
    responses = _submit_trace_responses(page)
    if not isinstance(trace, dict) and not responses:
        return None

    failures: list[tuple[str, str]] = []
    for item in responses[-80:]:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "")
        if not any(fragment in url for fragment in _SESSION_BOUNDARY_API_FRAGMENTS):
            continue
        body = str(item.get("body") or "")
        payload: dict[str, Any] = {}
        try:
            parsed = json.loads(body) if body else {}
        except Exception:
            parsed = {}
        if isinstance(parsed, dict):
            payload = parsed
        code = str(payload.get("code") or "")
        success = payload.get("success")
        if success is not False or code != "-1":
            continue
        msg = str(payload.get("msg") or payload.get("message") or "")
        exception = str(payload.get("exception") or "")
        haystack = " ".join([body, exception, msg]).lower()
        if msg and not any(marker in haystack for marker in _SESSION_BOUNDARY_TEXT_MARKERS):
            # The live H5 response is mojibake in this repo's captured logs; the
            # endpoint plus code=-1 is still the stable signal for this boundary.
            pass
        endpoint = urlparse(url).path or url
        detail = exception or msg or "code=-1"
        failures.append((endpoint, detail[:160]))

    if len(failures) < _SESSION_BOUNDARY_MIN_FAILURES:
        return None
    endpoint, detail = failures[-1]
    return (
        "Account/session boundary while waiting submit processing: "
        f"{len(failures)} recent failures, last={endpoint} {detail}"
    )


def _submit_health_notice_boundary_reason(page: Any) -> str | None:
    trace = getattr(page, "_agent3_submit_trace", None)
    responses = _submit_trace_responses(page)
    if not isinstance(trace, dict) and not responses:
        return None

    failures: list[tuple[str, str]] = []
    for item in responses[-80:]:
        if not isinstance(item, dict):
            continue
        if item.get("mocked_health_notify_failure") is True:
            continue
        url = str(item.get("url") or "")
        if not any(fragment in url for fragment in _HEALTH_NOTICE_BOUNDARY_API_FRAGMENTS):
            continue
        body = str(item.get("body") or "")
        payload: dict[str, Any] = {}
        try:
            parsed = json.loads(body) if body else {}
        except Exception:
            parsed = {}
        if isinstance(parsed, dict):
            payload = parsed
        code = str(payload.get("code") or "")
        success = payload.get("success")
        if success is not False or code != "-1":
            continue
        msg = str(payload.get("msg") or payload.get("message") or "")
        haystack = " ".join([body, msg])
        if not any(marker in haystack for marker in _HEALTH_NOTICE_BOUNDARY_TEXT_MARKERS):
            continue
        endpoint = urlparse(url).path or url
        failures.append((endpoint, msg[:160] or "code=-1"))

    if not failures:
        return None
    endpoint, detail = failures[-1]
    return (
        "Health notice boundary while waiting submit processing: "
        f"{len(failures)} recent failures, last={endpoint} {detail}"
    )


def _body_payload(item: dict[str, Any]) -> dict[str, Any]:
    body = str(item.get("body") or "")
    if not body:
        return {}
    try:
        parsed = json.loads(body)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _card_num_from_validation_event(item: dict[str, Any]) -> str | None:
    url = str(item.get("url") or "")
    try:
        query_value = parse_qs(urlparse(url).query).get("cardNum", [None])[0]
    except Exception:
        query_value = None
    if query_value is not None:
        return str(query_value or "").replace(" ", "")

    payload = _body_payload(item)
    candidates: list[Any] = [
        payload.get("cardNum"),
        payload.get("cardNo"),
    ]
    data = payload.get("data")
    if isinstance(data, dict):
        candidates.extend([data.get("cardNum"), data.get("cardNo")])
    for candidate in candidates:
        if candidate is not None:
            return str(candidate or "").replace(" ", "")
    return None


def _submit_bank_card_validation_loop_reason(page: Any) -> str | None:
    path = urlparse(str(getattr(page, "url", "") or "")).path
    if not re.search(r"/product/insure(?:/|$)", path):
        return None

    responses = _submit_trace_responses(page, trace_limit=120, file_limit=120)
    related: list[dict[str, Any]] = []
    valid_count = 0
    verif_count = 0
    empty_card_count = 0
    filled_card_count = 0
    last_endpoint = ""
    for item in responses[-120:]:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "")
        if "/api/apps/cps/insure/submit" in url:
            related = []
            valid_count = 0
            verif_count = 0
            empty_card_count = 0
            filled_card_count = 0
            last_endpoint = ""
            continue
        if not any(fragment in url for fragment in _BANK_CARD_VALIDATION_API_FRAGMENTS):
            continue
        related.append(item)
        endpoint = urlparse(url).path or url
        last_endpoint = endpoint
        if "/product/insure/card/valid" in url:
            valid_count += 1
        if "/pay/bank/card/verif" in url:
            verif_count += 1
        card_num = _card_num_from_validation_event(item)
        if card_num == "":
            empty_card_count += 1
        elif card_num:
            filled_card_count += 1

    if len(related) < _BANK_CARD_VALIDATION_LOOP_MIN_EVENTS:
        return None
    if valid_count < 2 or verif_count < 1:
        return None
    if empty_card_count < 1 and filled_card_count < _BANK_CARD_FILLED_VALIDATION_LOOP_MIN_EVENTS:
        return None
    if filled_card_count < 1:
        return None

    return (
        "Bank card validation loop while waiting submit processing: "
        f"{len(related)} recent validations, card/valid={valid_count}, "
        f"bank/card/verif={verif_count}, empty_cardNum={empty_card_count}, "
        f"filled_cardNum={filled_card_count}, last={last_endpoint}"
    )


def _frontend_runtime_boundary_reason(page: Any) -> str | None:
    trace = getattr(page, "_agent3_submit_trace", None)
    if not isinstance(trace, dict):
        return None
    pageerrors = trace.get("pageerrors")
    if not isinstance(pageerrors, list):
        return None

    counts: dict[str, tuple[int, str]] = {}
    for item in pageerrors[-40:]:
        if not isinstance(item, dict):
            continue
        text = " ".join(str(item.get("text") or "").split())
        stack = str(item.get("stack") or "")
        if not text:
            continue
        if "product/detail" not in stack and "product/detail" not in str(getattr(page, "url", "")):
            continue
        key = f"{text}|{stack[:160]}"
        count, _last_text = counts.get(key, (0, text))
        counts[key] = (count + 1, text)

    if not counts:
        return None
    count, text = max(counts.values(), key=lambda item: item[0])
    if count < _FRONTEND_RUNTIME_MIN_PAGEERRORS:
        return None
    return (
        "Frontend/runtime boundary while waiting page flow: "
        f"{count} repeated page errors, last={text[:160]}"
    )


def _submit_trace_blocker_reason(
    page: Any,
    *,
    allow_product_form_backend: bool = False,
    allow_product_form_session: bool = False,
) -> str | None:
    session_reason = _submit_session_boundary_reason(page)
    if session_reason and (
        _current_page_is_submit_handoff_boundary(page)
        or (
            not allow_product_form_session
            and not _current_page_accepts_session_boundary(page)
        )
    ):
        session_reason = None
    backend_reason = _submit_backend_unavailable_reason(page)
    if backend_reason and not (
        allow_product_form_backend or _current_page_accepts_backend_boundary(page)
    ):
        backend_reason = None
    health_notice_reason = _submit_health_notice_boundary_reason(page)
    bank_card_loop_reason = _submit_bank_card_validation_loop_reason(page)
    return (
        health_notice_reason
        or
        backend_reason
        or session_reason
        or bank_card_loop_reason
        or _frontend_runtime_boundary_reason(page)
    )


def _current_page_accepts_backend_boundary(page: Any) -> bool:
    path = urlparse(str(getattr(page, "url", "") or "")).path
    if re.search(r"/product/(?:detail|healthInform|adapt|to-insure|insure)(?:/|$)", path):
        return False
    return True


def _current_page_accepts_session_boundary(page: Any) -> bool:
    path = urlparse(str(getattr(page, "url", "") or "")).path
    if re.search(r"/product/(?:detail|healthInform|adapt|to-insure|insure)(?:/|$)", path):
        return False
    return True


def _current_page_is_submit_handoff_boundary(page: Any) -> bool:
    current_url = str(getattr(page, "url", "") or "")
    path = urlparse(current_url).path
    if re.search(r"/(?:pay|cashier|order|payment|deduction)(?:/|$)", path):
        return True
    if re.search(r"/product/(?:pay|payment|confirm|order|deduction)(?:/|$)", path):
        return True
    if re.search(r"/authentication(?:/|$)", path):
        return True
    return False


def _authentication_boundary_action(page: Any) -> dict[str, Any] | None:
    current_url = str(getattr(page, "url", "") or "")
    path = urlparse(current_url).path
    if not re.search(r"/authentication(?:/|$)", path):
        return None
    return _minimal_action_record(
        page,
        text="Authentication boundary after order task handoff: live identity verification requires external user/device state",
        tag="boundary",
        selector="/authentication",
        source_url=current_url,
        click_strategy="authentication-boundary",
    )


def _submit_trace_blocker_strategy(reason: str) -> str:
    if reason.startswith("Account/session boundary"):
        return "account-session-boundary"
    if reason.startswith("Health notice boundary"):
        return "health-notice-boundary"
    if reason.startswith("Bank card validation loop"):
        return "bank-card-validation-loop"
    if reason.startswith("Frontend/runtime boundary"):
        return "frontend-runtime-boundary"
    return "backend-unavailable-boundary"


def _should_wait_for_submit_processing(target_node_id: str | None) -> bool:
    return str(target_node_id or "") in {
        "NODE-underwriting",
        "NODE-risk-control",
        "NODE-payment",
        "NODE-policy-result",
    }


async def _wait_for_policy_result_after_processing_overlay(
    page: Any,
    *,
    entry_url: str,
    target_node_id: str,
    timeout_ms: int = _PROCESSING_OVERLAY_WAIT_MS,
) -> tuple[dict[str, Any], str | None]:
    """Wait for asynchronous submit processing before declaring a blocker."""
    deadline = timeout_ms
    last_snapshot = await _snapshot_page(page, entry_url)
    last_reason = _blocking_overlay_reason(last_snapshot)
    trace_blocker_reason = _submit_trace_blocker_reason(
        page,
        allow_product_form_backend=True,
        allow_product_form_session=True,
    )
    if trace_blocker_reason:
        return last_snapshot, trace_blocker_reason
    while deadline > 0:
        if _matches_node_reach_contract(last_snapshot, target_node_id) or _is_external_payment_handoff(
            last_snapshot,
            target_node_id,
        ):
            return last_snapshot, None
        await page.wait_for_timeout(2_000)
        deadline -= 2_000
        last_snapshot = await _snapshot_page(page, entry_url)
        last_reason = _blocking_overlay_reason(last_snapshot)
        trace_blocker_reason = _submit_trace_blocker_reason(
            page,
            allow_product_form_backend=True,
            allow_product_form_session=True,
        )
        if trace_blocker_reason:
            return last_snapshot, trace_blocker_reason
        if not _is_processing_overlay_reason(last_reason) and (
            _matches_node_reach_contract(last_snapshot, target_node_id)
            or _is_external_payment_handoff(last_snapshot, target_node_id)
        ):
            return last_snapshot, None
    if last_reason:
        return last_snapshot, f"{last_reason} (waited {timeout_ms // 1000}s)"
    return last_snapshot, f"Submit processing did not reach {target_node_id} after waiting {timeout_ms // 1000}s"


async def _settle_blocking_overlay_after_action(
    page: Any,
    snapshot: dict[str, Any],
    *,
    entry_url: str,
    target_node_id: str,
    planned_context: dict[str, Any],
    path_id: Any,
) -> tuple[dict[str, Any], str | None]:
    overlay_reason = _blocking_overlay_reason(snapshot)
    if not overlay_reason:
        return snapshot, None
    if _should_wait_for_submit_processing(target_node_id) and _is_processing_overlay_reason(overlay_reason):
        snapshot, overlay_reason = await _wait_for_policy_result_after_processing_overlay(
            page,
            entry_url=entry_url,
            target_node_id=target_node_id,
        )
        snapshot.update(planned_context)
        snapshot["path_id"] = path_id
    return snapshot, overlay_reason


def _control_needs_value(control: dict[str, Any]) -> bool:
    if not control.get("visible") or control.get("disabled") or control.get("readonly"):
        return False
    if not control.get("required_like") and not control.get("placeholder_like"):
        return False
    kind = str(control.get("kind") or "")
    if kind in {"checkbox", "radio"}:
        return not bool(control.get("checked"))
    value = str(control.get("value") or "").strip()
    if control.get("placeholder_like"):
        return True
    return not bool(value)


def _detect_unresolved_requirements(page_state: dict[str, Any]) -> list[dict[str, Any]]:
    """Infer unsatisfied form/agreement requirements from structured page state."""
    requirements: list[dict[str, Any]] = []
    seen: set[str] = set()
    validation_feedback = " ".join(str(item) for item in page_state.get("validation_feedback", []) or [])
    agreement_feedback = bool(validation_feedback) and any(token in validation_feedback for token in ("协议", "阅读", "同意"))
    for group in page_state.get("agreement_groups", []) or []:
        if group.get("satisfied"):
            continue
        group_id = str(group.get("group_id") or group.get("selector") or group.get("text") or "agreement")
        key = f"agreement:{group_id}"
        if key in seen:
            continue
        seen.add(key)
        if agreement_feedback or group.get("text"):
            requirements.append(
                {
                    "type": "agreement",
                    "key": key,
                    "selector": group.get("selector"),
                    "text": group.get("text"),
                    "group_id": group_id,
                }
            )
    for control in page_state.get("controls", []) or []:
        if not _control_needs_value(control):
            continue
        selector = str(control.get("selector") or control.get("name") or control.get("label") or "control")
        key = f"field:{selector}"
        if key in seen:
            continue
        seen.add(key)
        requirements.append(
            {
                "type": "field",
                "key": key,
                "selector": control.get("selector"),
                "name": control.get("name"),
                "label": control.get("label"),
                "kind": control.get("kind"),
            }
        )
    return requirements


def _plan_requirement_repairs(
    requirements: list[dict[str, Any]],
    *,
    repair_counts: dict[str, int] | None = None,
    max_repeats: int = 2,
) -> list[dict[str, Any]]:
    counts = repair_counts or {}
    planned: list[dict[str, Any]] = []
    for requirement in requirements:
        key = str(requirement.get("key") or "")
        if counts.get(key, 0) >= max_repeats:
            continue
        planned.append(requirement)
    return planned


async def _execute_requirement_repairs(page: Any, repairs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for repair in repairs:
        selector = str(repair.get("selector") or "").strip()
        if not selector:
            continue
        repair_type = str(repair.get("type") or "")
        if repair_type == "agreement":
            clicked = await page.evaluate(
                """(selector) => {
                    const el = document.querySelector(selector);
                    if (!el) return null;
                    function textOf(node) {
                        return String(
                            node?.innerText || node?.textContent || node?.value || node?.getAttribute?.('aria-label') || ''
                        ).replace(/\\s+/g, ' ').trim();
                    }
                    function markChecked(node) {
                        let current = node;
                        for (let depth = 0; current && depth < 5; depth += 1) {
                            try {
                                current.setAttribute('aria-checked', 'true');
                                current.classList?.add('checked', 'active', 'selected', 'is-checked');
                            } catch (_) {}
                            for (const input of Array.from(current.querySelectorAll?.('input[type=checkbox]') || [])) {
                                if (!input.checked) input.click();
                                input.checked = true;
                                input.setAttribute('checked', 'checked');
                                input.dispatchEvent(new Event('input', { bubbles: true }));
                                input.dispatchEvent(new Event('change', { bubbles: true }));
                            }
                            current = current.parentElement;
                        }
                    }
                    const input = el.matches('input[type=checkbox]') ? el : el.querySelector('input[type=checkbox]');
                    if (input && !input.checked) input.click();
                    if (!input) {
                        el.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, cancelable: true, view: window }));
                        el.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
                        el.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
                        el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
                    }
                    markChecked(input || el);
                    return { text: textOf(el).slice(0, 120), selector };
                }""",
                selector,
            )
            if clicked:
                actions.append(
                    {
                        "text": str(clicked.get("text") or repair.get("text") or ""),
                        "tag": "oracle",
                        "selector": selector,
                        "source_url": page.url,
                        "target_url": page.url,
                        "score": None,
                        "click_strategy": "oracle-agreement-control",
                        "dismissed_overlays": [],
                        "action_type": "oracle_repair",
                        "repair_key": repair.get("key"),
                    }
                )
            continue
        if repair_type == "field":
            value = _minimal_form_value(str(repair.get("name") or ""), str(repair.get("label") or ""))
            filled = await page.evaluate(
                """({ selector, value }) => {
                    const el = document.querySelector(selector);
                    if (!el) return null;
                    function textOf(node) {
                        return String(node?.innerText || node?.textContent || node?.value || '').replace(/\\s+/g, ' ').trim();
                    }
                    const tag = el.tagName.toLowerCase();
                    const type = String(el.type || '').toLowerCase();
                    if (tag === 'select') {
                        const option = Array.from(el.options || []).find(item => item.value && !item.disabled && !/请选择/.test(item.text || ''));
                        if (!option) return null;
                        el.value = option.value;
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                        el.dispatchEvent(new Event('blur', { bubbles: true }));
                        return { text: `${selector}=${option.text}`.slice(0, 120), selector };
                    }
                    if (['checkbox', 'radio'].includes(type)) {
                        if (!el.checked) el.click();
                        el.checked = true;
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                        return { text: `${selector}=checked`.slice(0, 120), selector };
                    }
                    el.value = value;
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    el.dispatchEvent(new Event('blur', { bubbles: true }));
                    return { text: `${selector}=${value}`.slice(0, 120), selector, label: textOf(el.closest('label')) };
                }""",
                {"selector": selector, "value": value},
            )
            if filled:
                actions.append(
                    {
                        "text": str(filled.get("text") or ""),
                        "tag": "oracle",
                        "selector": selector,
                        "source_url": page.url,
                        "target_url": page.url,
                        "score": None,
                        "click_strategy": "oracle-field-repair",
                        "dismissed_overlays": [],
                        "action_type": "oracle_repair",
                        "repair_key": repair.get("key"),
                    }
                )
    if actions:
        await page.wait_for_timeout(_POST_CLICK_SETTLE_MS)
    return actions


async def _repair_unresolved_requirements(
    page: Any,
    page_state: dict[str, Any] | None,
    repair_counts: dict[str, int],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    state = page_state or await _collect_page_state(page)
    requirements = _detect_unresolved_requirements(state)
    repairs = _plan_requirement_repairs(requirements, repair_counts=repair_counts)
    for repair in repairs:
        key = str(repair.get("key") or "")
        if key:
            repair_counts[key] = repair_counts.get(key, 0) + 1
    actions = await _execute_requirement_repairs(page, repairs)
    if not actions:
        return [], requirements
    after_state = await _collect_page_state(page)
    return actions, _detect_unresolved_requirements(after_state)


def _unfilled_question_numbers_from_text(text: str) -> list[int]:
    numbers: list[int] = []
    for match in re.finditer(r"第\s*(\d+)\s*题未填写", str(text or "")):
        value = int(match.group(1))
        if value not in numbers:
            numbers.append(value)
    return numbers


def _is_document_action(action: dict[str, Any]) -> bool:
    text = str(action.get("text") or "")
    href = str(action.get("href") or "")
    if any(keyword in text for keyword in _DOCUMENT_ACTION_HINTS):
        return True
    href_lower = href.lower()
    parsed = urlparse(href_lower)
    host = parsed.netloc
    path = parsed.path or href_lower
    return (
        href_lower.endswith((".pdf", ".doc", ".docx"))
        or re.search(r"^(?:files?|docs?|documents?)\d*[.-]", host) is not None
        or re.search(r"/(?:file\d?|files?|documents?|attachments?)/", path) is not None
    )


def _action_score(action: dict[str, Any]) -> int:
    text = str(action.get("text") or "")
    normalized_text = " ".join(text.split())
    compact_text = "".join(text.split())
    href = str(action.get("href") or "")
    if _is_document_action(action):
        return 0
    score = 0
    if (
        normalized_text in _PRODUCT_ENTRY_CTA_TEXTS
        or compact_text in _PRODUCT_ENTRY_CTA_TEXTS
        or any(keyword in compact_text for keyword in ("保费试算", "立即投保", "我要投保"))
    ):
        score += 100
    if any(keyword in normalized_text for keyword in ("下一步", "下一页", "继续", "确认", "同意", "已阅读", "知道了")) or any(
        keyword in compact_text for keyword in ("下一步", "下一页", "继续", "确认", "确定", "投保", "同意", "已阅读", "知道了")
    ):
        score += 80
    if href and not href.startswith(("javascript:", "#")):
        score += 20
    if text:
        score += 5
    if action.get("visible") is False:
        score -= 120
    return score


def _primary_actions(page_url: str, actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = sorted(
        [
            {
                **action,
                "source_url": page_url,
                "action_type": "primary" if _action_score(action) >= 80 else "secondary",
                "score": _action_score(action),
            }
            for action in actions
            if _action_score(action) >= 80
        ],
        key=lambda item: (-int(item.get("score", 0)), int(item.get("index", 0))),
    )
    return ranked[:3]


def _looks_like_choice_page(snapshot: dict[str, Any]) -> bool:
    text = str(snapshot.get("body_text_excerpt") or "")
    if any(hint in text for hint in _CHOICE_PAGE_HINTS):
        return True
    action_text = " ".join(str(action.get("text") or "") for action in snapshot.get("actions", []) or [])
    return any(hint in action_text for hint in _CHOICE_ACTION_HINTS)


def _is_forward_action(action: dict[str, Any]) -> bool:
    text = " ".join(str(action.get("text") or "").split())
    compact_text = "".join(str(action.get("text") or "").split())
    return any(keyword in text for keyword in _FORWARD_ACTION_HINTS) or any(
        keyword in compact_text for keyword in ("确定", "投保", "下一步", "继续")
    )


def _is_safe_transit_action(action: dict[str, Any]) -> bool:
    text = str(action.get("text") or "")
    if action.get("visible") is False or not text:
        return False
    if _is_document_action(action) or any(keyword in text for keyword in _NEGATIVE_ACTION_HINTS):
        return False
    return _is_forward_action(action) or _action_score(action) >= 60


def _signal_hits(text: str, signals: list[str] | tuple[str, ...]) -> list[str]:
    return [signal for signal in signals if signal and signal in text]


def _observe_trace(
    *,
    path_id: str,
    node_id: str,
    snapshot: dict[str, Any],
    matched: bool,
) -> dict[str, Any]:
    profile = _node_profile(node_id)
    body_text = str(snapshot.get("body_text_excerpt") or "")
    action_text = " ".join(str(action.get("text") or "") for action in snapshot.get("actions", []) or [])
    return {
        "phase": "observe",
        "path_id": path_id,
        "node_id": node_id,
        "goal": profile.get("goal"),
        "url": snapshot.get("url"),
        "title": snapshot.get("title"),
        "matched": matched,
        "entry_signal_hits": _signal_hits(body_text, profile.get("entry_signal", [])),
        "exit_signal_hits": _signal_hits(body_text + " " + action_text, profile.get("exit_signal", [])),
        "field_count": snapshot.get("field_count"),
        "action_count": snapshot.get("action_count"),
    }


def _act_trace(
    *,
    path_id: str,
    node_id: str,
    target_node_id: str,
    action: dict[str, Any],
) -> dict[str, Any]:
    return {
        "phase": "act",
        "path_id": path_id,
        "node_id": node_id,
        "target_node_id": target_node_id,
        "action_type": action.get("action_type") or "click",
        "text": action.get("text"),
        "selector": action.get("selector"),
        "source_url": action.get("source_url"),
        "target_url": action.get("target_url"),
        "click_strategy": action.get("click_strategy"),
    }


def _verify_trace(
    *,
    path_id: str,
    node_id: str,
    target_node_id: str,
    before_snapshot: dict[str, Any],
    after_snapshot: dict[str, Any],
) -> dict[str, Any]:
    target_profile = _node_profile(target_node_id)
    text = str(after_snapshot.get("body_text_excerpt") or "")
    changed = (
        after_snapshot.get("url") != before_snapshot.get("url")
        or after_snapshot.get("dom_signature") != before_snapshot.get("dom_signature")
    )
    return {
        "phase": "verify",
        "path_id": path_id,
        "node_id": node_id,
        "target_node_id": target_node_id,
        "source_url": before_snapshot.get("url"),
        "target_url": after_snapshot.get("url"),
        "state_changed": changed,
        "target_signal_hits": _signal_hits(text, target_profile.get("entry_signal", [])),
        "target_matched": _page_matches_node(
            after_snapshot,
            {
                "node_id": target_node_id,
                "url_pattern": "",
            },
        ),
    }


def _planned_path_context(regression_paths: list[dict[str, Any]] | None) -> dict[str, Any]:
    paths = list(regression_paths or [])
    return {
        "planned_path_ids": [str(item.get("path_id")) for item in paths if item.get("path_id")],
        "planned_case_ids": [
            str(case_id)
            for item in paths
            for case_id in item.get("case_ids", []) or []
            if case_id
        ],
        "planned_route_nodes": [
            str(node_id)
            for item in paths[:1]
            for node_id in item.get("nodes", []) or []
            if node_id
        ],
    }


def _page_matches_pattern(page_url: str, pattern: str) -> bool:
    path = urlparse(page_url).path or page_url
    if not pattern:
        return False
    if path.endswith(pattern):
        return True
    if pattern == "/product/detail":
        return any(path.endswith(alias) for alias in ("/product/detail", "/product/index", "/media.html"))
    return False


_NODE_TEXT_HINTS: dict[str, tuple[str, ...]] = {
    "NODE-product-detail": ("产品详情", "保障责任", "保费", "购买", "投保"),
    "NODE-premium-calculation": ("保费试算", "保障计划", "保费", "投保", "确定"),
    "NODE-plan-selection": ("计划", "保障计划", "保障方案", "保额", "保费", "选择计划"),
    "NODE-applicant-info": ("投保人", "投保信息", "姓名", "证件", "手机号"),
    "NODE-insured-info": ("被保人", "为谁投保", "被保险人"),
    "NODE-beneficiary": ("受益人", "法定受益人", "指定受益人"),
    "NODE-tax-info": ("税收", "税务", "纳税"),
    "NODE-health-notice": ("健康告知", "告知", "问卷", "风险警示"),
    "NODE-suitability": ("适当性", "风险承受", "问卷"),
    "NODE-underwriting": ("核保", "智能核保", "提交核保"),
    "NODE-risk-control": ("身份认证", "投保意愿认证", "证件照片", "上传照片", "认证", "验证码"),
    "NODE-payment": ("支付", "银行卡", "签约", "付款"),
    "NODE-policy-result": ("出单", "保单", "成功", "结果"),
    "NODE-insure-form": ("投保人信息", "被保险人信息", "提交投保单", "投保声明"),
    "NODE-policy-service": ("保单服务", "保全", "服务"),
    "NODE-surrender": ("退保", "犹豫期", "退费"),
}

_NODE_ACTION_HINTS: dict[str, tuple[str, ...]] = {
    "NODE-plan-selection": ("购买", "立即投保", "我要投保", "去投保", "投保"),
    "NODE-premium-calculation": ("保费试算", "试算", "投保", "确定"),
    "NODE-applicant-info": ("购买", "立即投保", "下一步", "继续", "确认"),
    "NODE-insured-info": ("下一步", "继续", "确认"),
    "NODE-beneficiary": ("下一步", "继续", "确认", "受益人"),
    "NODE-tax-info": ("下一步", "继续", "确认"),
    "NODE-health-notice": ("下一步", "下一页", "继续", "确认", "健康告知", "已阅读", "同意", "知道了", "无以上问题"),
    "NODE-suitability": ("下一步", "下一页", "继续", "确认", "确定", "投保", "问卷", "同意"),
    "NODE-underwriting": ("提交订单", "提交核保", "核保", "下一步", "继续", "确认"),
    "NODE-risk-control": ("发送认证短信", "提交认证", "下一步", "提 交", "提交", "完成", "确认"),
    "NODE-payment": ("提交订单", "支付", "签约", "付款", "确认"),
    "NODE-policy-result": ("提交订单", "提交投保单", "提交", "核保", "支付", "完成", "查看保单", "确认"),
    "NODE-insure-form": ("提交订单", "提交投保单", "提交", "下一步", "继续", "确认"),
}

_POLICY_INFO_MOBILE_PREFIXES: tuple[str, ...] = (
    "134",
    "135",
    "136",
    "137",
    "138",
    "139",
    "147",
    "148",
    "150",
    "151",
    "152",
    "157",
    "158",
    "159",
    "166",
    "167",
    "171",
    "172",
    "173",
    "175",
    "176",
    "177",
    "178",
    "180",
    "181",
    "182",
    "183",
    "184",
    "185",
    "186",
    "187",
    "188",
    "189",
    "190",
    "191",
    "193",
    "195",
    "196",
    "197",
    "198",
    "199",
    "130",
    "131",
    "132",
    "145",
    "155",
    "156",
    "133",
    "149",
    "153",
)
_DEFAULT_POLICY_HEIGHT_CM = "170"
_DEFAULT_POLICY_WEIGHT_KG = "60"
_POLICY_PHONE_SEQUENCE = 0

_MINIMAL_FORM_DEFAULT_RULES: tuple[tuple[str, str], ...] = (
    (r"payAccount|bankAccount|account|银行账号|银行卡号|银行账户|开卡信息|储蓄卡|账号", "6200588435998028938"),
    (r"cardOwner|持卡人", "张三"),
    (r"bankCode|bankName|openBank|开户银行|银行", "中国工商银行"),
    (r"jobText|occupation|职业", "一般"),
    (r"provCityText|region|居住省市|省市", "北京市 北京市 朝阳区"),
    (r"验证码|verifyCode|captcha|sms", "1111"),
    (r"cardPeriodEnd|结束|有效期.*结束", "2046-01-01"),
    (r"cardPeriod|开始|有效期", "2026-01-01"),
    (r"cardNumber|证件号码|idcard|identity", "110101199001011237"),
    (r"email|邮箱", "test@example.com"),
    (r"yearlyIncome|income|年收入", "20"),
    (r"height|身高", _DEFAULT_POLICY_HEIGHT_CM),
    (r"weight|体重", _DEFAULT_POLICY_WEIGHT_KG),
    (r"address|地址", "北京市朝阳区测试地址1号"),
    (r"name|姓名", "张三"),
    (r".*", "测试"),
)

_JOB_DROPDOWN_DEFAULTS: dict[str, dict[str, str | None]] = {
    "jobText_10": {"job1": "一般", "job2": "一般职业人员", "job3": None},
    "jobText_20_default_1": {"job1": "文教行业人员", "job2": "教育机构从业人员", "job3": "一般学生"},
}


def _generate_policy_phone() -> str:
    """Generate a valid non-static mobile number using policy mock prefixes."""
    global _POLICY_PHONE_SEQUENCE
    _POLICY_PHONE_SEQUENCE += 1
    seed = time.time_ns() + _POLICY_PHONE_SEQUENCE
    prefix = _POLICY_INFO_MOBILE_PREFIXES[seed % len(_POLICY_INFO_MOBILE_PREFIXES)]
    suffix = f"{seed % 100_000_000:08d}"
    return f"{prefix}{suffix}"


def _minimal_form_value(field_name: str, label: str) -> str:
    probe = f"{field_name or ''} {label or ''}"
    if re.search(r"payAccount|bankAccount|account|银行账号|银行卡号|银行账户|开卡信息|储蓄卡|账号", probe, re.IGNORECASE):
        return "6200588435998028938"
    if re.search(r"cardOwner|持卡人", probe, re.IGNORECASE):
        return "张三"
    if re.search(r"bankCode|bankName|openBank|开户银行|银行", probe, re.IGNORECASE):
        return "中国工商银行"
    if re.search(r"jobText|occupation|职业", probe, re.IGNORECASE):
        return "一般"
    if re.search(r"provCityText|region|居住省市|省市", probe, re.IGNORECASE):
        return "北京市 北京市 朝阳区"
    if re.search(r"moblie|mobile|phone|手机号|tel", probe, re.IGNORECASE):
        return _generate_policy_phone()
    if re.search(r"cardPeriodEnd.*_20|cardPeriodEnd_20|_20_.*cardPeriodEnd", field_name or "", re.IGNORECASE):
        return "2031-01-01"
    if re.search(r"cardPeriodEnd|有效期.*结束|结束", probe, re.IGNORECASE):
        return "2046-01-01"
    if re.search(r"cardPeriod.*_20|cardPeriod_20|_20_.*cardPeriod", field_name or "", re.IGNORECASE):
        return "2026-01-01"
    if re.search(r"cardPeriod|有效期.*开始|开始", probe, re.IGNORECASE):
        return "2026-01-01"
    if re.search(r"cardNumber.*_20|cardNumber_20|_20_.*cardNumber", field_name or "", re.IGNORECASE):
        return "11010120150315123X"
    if re.search(r"cardNumber|证件号码", probe, re.IGNORECASE):
        return "110101199001011237"
    if re.search(r"birthdate.*_20|birthdate_20|_20_.*birthdate", field_name or "", re.IGNORECASE):
        return "2015-03-15"
    if re.search(r"birthdate|出生日期|生日", probe, re.IGNORECASE):
        return "1990-01-01"
    for pattern, value in _MINIMAL_FORM_DEFAULT_RULES:
        if re.search(pattern, probe, re.IGNORECASE):
            return value
    return "测试"

_MAIN_FLOW_NODE_PROFILES: dict[str, dict[str, Any]] = {
    "NODE-product-detail": {
        "goal": "进入产品详情页",
        "entry_signal": ["产品详情", "保障责任", "保费"],
        "exit_signal": ["立即投保", "投保"],
        "actions": ["click_primary"],
    },
    "NODE-plan-selection": {
        "goal": "选择保障计划",
        "entry_signal": ["计划", "保障方案", "保额", "保费"],
        "exit_signal": ["投保人", "下一步"],
        "actions": ["select_option", "click_next"],
    },
    "NODE-applicant-info": {
        "goal": "进入并填写投保人信息",
        "entry_signal": ["投保人", "姓名", "证件", "手机号"],
        "exit_signal": ["被保人", "下一步", "确认"],
        "actions": ["fill_form", "click_next"],
    },
    "NODE-insured-info": {
        "goal": "进入并填写被保人信息",
        "entry_signal": ["被保人", "为谁投保", "被保险人"],
        "exit_signal": ["受益人", "健康告知", "下一步"],
        "actions": ["fill_form", "click_next"],
    },
    "NODE-beneficiary": {
        "goal": "选择受益人信息",
        "entry_signal": ["受益人", "法定受益人", "指定受益人"],
        "exit_signal": ["健康告知", "下一步"],
        "actions": ["select_option", "click_next"],
    },
    "NODE-health-notice": {
        "goal": "完成健康告知最小通过选项",
        "entry_signal": ["健康告知", "告知", "问卷"],
        "exit_signal": ["核保", "支付", "下一步", "确认"],
        "actions": ["select_safe_options", "click_next"],
    },
    "NODE-underwriting": {
        "goal": "提交核保并进入支付前置",
        "entry_signal": ["核保", "智能核保", "提交核保"],
        "exit_signal": ["支付", "签约", "付款"],
        "actions": ["click_submit", "click_next"],
    },
    "NODE-risk-control": {
        "goal": "完成投保意愿认证和证件照片上传",
        "entry_signal": ["身份认证", "投保意愿认证", "证件照片", "上传照片", "验证码"],
        "exit_signal": ["支付", "签约", "付款", "下一步"],
        "actions": ["send_sms", "fill_sms", "upload_id_card", "click_submit", "click_next"],
    },
    "NODE-payment": {
        "goal": "进入支付或签约页面",
        "entry_signal": ["支付", "银行卡", "签约", "付款"],
        "exit_signal": ["出单", "保单", "成功", "结果"],
        "actions": ["fill_form", "click_pay"],
    },
    "NODE-insure-form": {
        "goal": "填写聚合投保单信息",
        "entry_signal": ["投保人信息", "被保险人信息", "提交投保单"],
        "exit_signal": ["核保", "支付", "提交投保单"],
        "actions": ["fill_form", "accept_protocols", "click_submit"],
    },
    "NODE-policy-result": {
        "goal": "确认出单结果",
        "entry_signal": ["出单", "保单", "成功", "结果"],
        "exit_signal": ["保单", "完成"],
        "actions": ["verify_result"],
    },
}


def _node_profile(node_id: str) -> dict[str, Any]:
    profile = dict(_MAIN_FLOW_NODE_PROFILES.get(node_id, {}))
    profile.setdefault("goal", f"推进到 {node_id}")
    profile.setdefault("entry_signal", list(_NODE_TEXT_HINTS.get(node_id, ())))
    profile.setdefault("exit_signal", list(_NODE_ACTION_HINTS.get(node_id, ())))
    profile.setdefault("actions", ["minimal_data", "click_next"])
    return profile


def _looks_like_product_detail_page(page: dict[str, Any]) -> bool:
    url = str(page.get("url") or "")
    text = str(page.get("body_text_excerpt") or "")
    if _page_matches_pattern(url, "/product/detail"):
        return any(token in text for token in ("保障详情", "保费", "立即投保"))
    return False


def _looks_like_premium_calculation_state(page: dict[str, Any]) -> bool:
    url = str(page.get("url") or "")
    text = _combined_page_text(page)
    compact_text = "".join(text.split())
    if not _page_matches_pattern(url, "/product/detail") and not _page_matches_pattern(url, "/product/to-insure"):
        return False
    if _page_matches_pattern(url, "/product/detail") and int(page.get("field_count") or 0) == 0:
        if "保费试算" in compact_text and "确定" in compact_text:
            return True
        if "产品详情" in text and "立即投保" in text:
            return False
    if int(page.get("field_count") or 0) > 0 and any(token in text for token in ("保费试算", "保障计划", "投 保", "确定")):
        return True
    return any(token in text for token in ("起保日期", "出生日期", "保费合计", "基本保险金额"))


def _combined_page_text(page: dict[str, Any]) -> str:
    page_state = page.get("page_state") or {}
    overlay_text = " ".join(
        str(item.get("text") or "")
        for item in page_state.get("blocking_overlays", []) or []
        if isinstance(item, dict)
    )
    return " ".join(
        str(value or "")
        for value in (page.get("title"), page.get("body_text_excerpt"), overlay_text)
    )


def _looks_like_payment_page(page: dict[str, Any]) -> bool:
    path = urlparse(str(page.get("url") or "")).path
    text = _combined_page_text(page)
    if re.search(r"/authentication(?:/|$)", path):
        return False
    if any(token in text for token in ("投保意愿认证", "证件照片", "身份认证", "发送认证短信", "提交认证")):
        return False
    if re.search(r"/(?:pay|cashier|order|payment|deduction)(?:/|$)", path):
        return True
    if re.search(r"/product/(?:pay|payment|confirm|order|deduction)(?:/|$)", path):
        return True
    if any(token in text for token in ("投保人信息", "被保险人信息", "提交订单", "提交投保单")):
        return False
    return any(
        token in text
        for token in (
            "银行卡签约",
            "签约成功",
            "标准核保",
            "standard-underwriting-probe",
            "taskType=3",
            "银行卡签约",
            "签约成功",
            "标准核保",
            "standard-underwriting-probe",
            "taskType=3",
            "立即支付",
            "应付额",
            "应付总额",
            "微信扫一扫",
            "完成支付",
            "支付方式",
            "支付需验证",
            "验证码已发送",
            "银行代扣",
            "验证码",
            "待支付",
        )
    )


def _agent3_expanded_payment_text(value: Any) -> str:
    seen: set[str] = set()
    expanded: list[str] = []

    def add(candidate: Any, depth: int = 0) -> None:
        raw = str(candidate or "").strip()
        if not raw or raw in seen:
            return
        seen.add(raw)
        expanded.append(raw)
        decoded = raw
        for _ in range(3):
            try:
                next_value = unquote(decoded)
            except Exception:
                break
            if not next_value or next_value == decoded or next_value in seen:
                break
            decoded = next_value
            seen.add(decoded)
            expanded.append(decoded)
        if depth >= 3:
            return
        for text in (raw, decoded):
            parsed = urlparse(text)
            if not parsed.query:
                continue
            params = parse_qs(parsed.query)
            for key in ("redirect_url", "return_url", "callback_url", "paymentUrl", "payUrl", "url"):
                for nested in params.get(key, []):
                    add(nested, depth + 1)

    add(value)
    return "\n".join(expanded)


def _agent3_payment_boundary_text(page: dict[str, Any]) -> str:
    return _agent3_expanded_payment_text(
        json.dumps(
            {
                "url": page.get("url"),
                "title": page.get("title"),
                "body_text_excerpt": page.get("body_text_excerpt"),
                "actions": page.get("actions", []),
                "primary_actions": page.get("primary_actions", []),
            },
            ensure_ascii=False,
            default=str,
        )
    )


def _agent3_payment_method_from_text(value: Any) -> str:
    text = str(value or "")
    lower = text.lower()
    if re.search(r"wechat|weixin|wxpay|wx_pay|tenpay|checkmweb", lower) or "微信" in text:
        return "wechat"
    if re.search(r"alipay|ali_pay|alipayqr|alipay_trade", lower) or "支付宝" in text:
        return "alipay"
    return ""


def _agent3_clean_payment_token(value: Any) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+$", "", str(value or "").strip())


def _agent3_gateway_pay_num_from_text(value: Any, payment_method: str) -> tuple[str, str]:
    text = _agent3_expanded_payment_text(value)
    for key, source_suffix in (
        ("trade_no", "trade_no"),
        ("gatewayPayNum", "gatewayPayNum"),
        ("out_trade_no", "out_trade_no"),
        ("paymentOrder", "paymentOrder"),
        ("payOrderNo", "payOrderNo"),
        ("orderNo", "orderNo"),
    ):
        pattern = rf"(?:[?&#]|^){re.escape(key)}=([A-Za-z0-9_-]{{8,}})"
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            pattern = rf"['\"]?{re.escape(key)}['\"]?\s*[:=]\s*['\"]?([A-Za-z0-9_-]{{8,}})"
            match = re.search(pattern, text, re.IGNORECASE)
        if match:
            gateway_pay_num = _agent3_clean_payment_token(match.group(1))
            if not gateway_pay_num:
                continue
            prefix = f"{payment_method}-url" if payment_method in {"wechat", "alipay"} else "payment-url"
            return gateway_pay_num, f"{prefix}-{source_suffix}"
    return "", ""


def _agent3_insure_num_from_text(value: Any) -> str:
    text = str(value or "")
    for pattern in (
        r"(?:insureNum|insure_num)\\?\"?\s*[:=]\s*\\?\"?(\d{10,20})",
        r"(?:投保单号|投保订单号)\s*[:：]?\s*(\d{10,20})",
    ):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return ""


def _agent3_payment_url_parts(page: dict[str, Any]) -> tuple[str, str]:
    raw_url = str(page.get("url") or "")
    parsed = urlparse(raw_url)
    return parsed.netloc, parsed.path or ""


def _agent3_cashier_owner_from_text(value: Any) -> str:
    lower = str(value or "").lower()
    if any(
        token in lower
        for token in (
            "gatewaypaynum",
            "trade_no",
            "paymentorder",
            "payorderno",
            "checkmweb",
            "wechat_pay",
            "alipay",
            "cashier",
            "/pay",
            "/payment",
            "微信支付",
            "收银台",
        )
    ):
        return "generic-insurance"
    return ""


def _payment_boundary_evidence_from_page(page: dict[str, Any]) -> dict[str, Any]:
    text = _agent3_payment_boundary_text(page)
    payment_method = _agent3_payment_method_from_text(text)
    gateway_pay_num, gateway_source = _agent3_gateway_pay_num_from_text(text, payment_method)
    payment_url_host, payment_url_path = _agent3_payment_url_parts(page)
    cashier_owner = _agent3_cashier_owner_from_text(text)
    if not any(
        (
            payment_method,
            gateway_pay_num,
            cashier_owner,
            _looks_like_payment_page(page),
        )
    ):
        return {}

    evidence: dict[str, Any] = {}
    if payment_method:
        evidence["paymentMethod"] = payment_method
    if gateway_pay_num:
        evidence["gatewayPayNum"] = gateway_pay_num
    if gateway_source:
        evidence["gatewayPayNum_source"] = gateway_source
    insure_num = _agent3_insure_num_from_text(text)
    if insure_num:
        evidence["insureNum"] = insure_num
    if payment_url_host:
        evidence["paymentUrlHost"] = payment_url_host
    if payment_url_path:
        evidence["paymentUrlPath"] = payment_url_path
    if cashier_owner:
        evidence["cashierOwner"] = cashier_owner
    return evidence


def _submit_api_result_is_order_generated(result: Any) -> bool:
    if not isinstance(result, dict) or not result.get("order_generated"):
        return False
    if result.get("task_handoff") or result.get("direct_order") or result.get("suitability_task"):
        return True
    code = str(result.get("code") or "")
    if code not in {"0", "37009", "40015"}:
        return False
    response_order = result.get("response_order") if isinstance(result.get("response_order"), dict) else {}
    payload_summary = result.get("payload_summary") if isinstance(result.get("payload_summary"), dict) else {}
    return bool(
        response_order.get("insureNum")
        or response_order.get("encryptInsureNum")
        or payload_summary.get("insureNum")
        or payload_summary.get("encryptInsureNum")
    )


def _submit_api_results_from_value(value: Any) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    def collect(candidate: Any) -> None:
        if isinstance(candidate, dict):
            submit_result = candidate.get("submit_api_result")
            if isinstance(submit_result, dict):
                results.append(submit_result)
            if candidate.get("attempted") and (
                "order_generated" in candidate
                or "task_handoff" in candidate
                or "direct_order" in candidate
                or "suitability_task" in candidate
            ):
                results.append(candidate)
            for child in candidate.values():
                collect(child)
            return
        if isinstance(candidate, list):
            for child in candidate:
                collect(child)

    collect(value)
    return results


def _steps_look_like_order_generated(steps: list[dict[str, Any]]) -> bool:
    if not steps:
        return False
    for submit_result in _submit_api_results_from_value(steps):
        if _submit_api_result_is_order_generated(submit_result):
            return True
    navigation_values: list[str] = []
    page_text_values: list[str] = []

    def collect(value: Any, key: str = "") -> None:
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                collect(child_value, str(child_key))
            return
        if isinstance(value, list):
            for item in value:
                collect(item, key)
            return
        if not isinstance(value, str):
            return
        if key in {"actual_url", "source_url", "target_url", "url"}:
            navigation_values.append(value)
        elif key in {"bodyText", "body_text_excerpt", "text"}:
            page_text_values.append(value)

    collect(steps)
    for raw_url in navigation_values:
        try:
            parsed = urlparse(raw_url)
        except Exception:
            continue
        path = parsed.path
        if "/api/" in path:
            continue
        if re.search(r"/authentication(?:/|$)", path):
            return True
        if re.search(r"/(?:pay|cashier|order|payment|deduction)(?:/|$)", path):
            return True
        if re.search(r"/product/(?:pay|payment|confirm|order|deduction)(?:/|$)", path):
            return True

    page_text = "\n".join(page_text_values)
    return any(
        token in page_text
        for token in (
            "银行卡签约",
            "签约成功",
            "标准核保",
            "standard-underwriting-probe",
            "taskType=3",
            "支付需验证",
            "验证码已发送",
            "银行代扣",
            "待支付",
            "应付总额",
            "立即支付",
            "投保单号",
            "订单号",
            "订单提交成功",
            "收银台",
            "支付方式",
        )
    )


def _path_order_generation_complete(expected_node_ids: list[str], matched_node_ids: set[str], steps: list[dict[str, Any]]) -> bool:
    return "NODE-insure-form" in matched_node_ids and _steps_look_like_order_generated(steps)


def _looks_like_auth_or_bank_signing_boundary(page: dict[str, Any]) -> bool:
    path = urlparse(str(page.get("url") or "")).path
    text = _combined_page_text(page)
    if re.search(r"/authentication(?:/|$)", path):
        return True
    return any(
        token in text
        for token in (
            "\u8eab\u4efd\u8ba4\u8bc1",
            "\u6295\u4fdd\u610f\u613f\u8ba4\u8bc1",
            "\u8bc1\u4ef6\u7167\u7247",
            "\u94f6\u884c\u5361\u7b7e\u7ea6",
            "\u5373\u5c06\u8fdb\u884c\u4ee5\u4e0b\u64cd\u4f5c",
            "\u53bb\u5b8c\u6210",
            "\u53bb\u8ba4\u8bc1",
            "\u7b7e\u7ea6\u6210\u529f",
        )
    )


def _is_external_payment_handoff(page: dict[str, Any], target_node_id: str) -> bool:
    return target_node_id == "NODE-policy-result" and (
        _looks_like_payment_page(page) or _looks_like_auth_or_bank_signing_boundary(page)
    )


def _looks_like_downstream_task_progress(page: dict[str, Any]) -> bool:
    path = urlparse(str(page.get("url") or "")).path
    text = _combined_page_text(page)
    if _looks_like_payment_page(page):
        return True
    if re.search(r"/product/(?:adapt|task|to-insure|healthInform)(?:/|$)", path):
        return True
    if re.search(r"/authentication(?:/|$)", path):
        return True
    return any(
        token in text
        for token in (
            "适当性",
            "评估问卷",
            "风险测评",
            "健康告知",
            "认证任务",
            "身份认证",
            "银行卡签约",
            "即将进行以下操作",
            "去完成",
            "去认证",
        )
    )


def _matches_node_reach_contract(page: dict[str, Any], node_id: str) -> bool:
    text = _combined_page_text(page)
    path = urlparse(str(page.get("url") or "")).path
    if node_id == "NODE-risk-control":
        return bool(re.search(r"/authentication(?:/|$)", path)) or any(
            token in text
            for token in (
                "身份认证",
                "投保意愿认证",
                "证件照片",
                "上传照片",
                "发送认证短信",
                "提交认证",
            )
        )
    if node_id == "NODE-payment":
        return _looks_like_payment_page(page)
    if node_id == "NODE-premium-calculation":
        return _looks_like_premium_calculation_state(page)
    if node_id == "NODE-insure-form":
        path = urlparse(str(page.get("url") or "")).path
        return path.endswith("/product/insure") and any(
            token in text for token in ("投保人信息", "被保险人信息", "提交投保单", "投保声明")
        )
    if node_id == "NODE-applicant-info":
        if any(token in text for token in ("健康告知", "健康问卷", "适当性评估", "评估问卷")):
            return False
        return sum(1 for token in ("投保人", "姓名", "证件", "手机号") if token in text) >= 2
    if node_id == "NODE-insured-info":
        if any(token in text for token in ("健康告知", "健康问卷", "适当性评估", "评估问卷")):
            return False
        return any(token in text for token in ("被保人", "被保险人")) and any(
            token in text for token in ("姓名", "证件", "手机号")
        )
    if node_id == "NODE-suitability":
        return any(token in text for token in ("适当性", "评估问卷")) or all(
            token in text for token in ("保险需求", "财务分析", "风险偏好")
        )
    if node_id == "NODE-health-notice":
        path = urlparse(str(page.get("url") or "")).path
        return path.endswith("/product/healthInform") or any(
            token in text
            for token in (
                "健康告知",
                "健康问卷",
                "投保告知",
                "健康状况是否符合投保条件",
                "确认无以上问题",
            )
        )
    if node_id == "NODE-policy-result":
        return any(token in text for token in ("投保成功", "出单成功", "支付成功", "电子保单"))
    return bool(text and any(keyword in text for keyword in _NODE_TEXT_HINTS.get(node_id, ())))


def _infer_current_node_id(page: dict[str, Any]) -> str | None:
    page_url = str(page.get("url") or "")
    page_text = str(page.get("body_text_excerpt") or "")
    path = urlparse(page_url).path
    if re.search(r"/pay/success(?:/|$)|/order/detail(?:/|$)", path):
        return "NODE-policy-result"
    if not re.search(r"/product/(?:detail|healthInform|adapt|to-insure|insure)(?:/|$)", path) and any(
        token in page_text for token in ("支付成功", "投保成功", "出单成功")
    ):
        return "NODE-policy-result"
    if re.search(r"/authentication(?:/|$)", path):
        return "NODE-risk-control"
    if "/product/adapt" in page_url:
        return "NODE-suitability"
    if "/product/healthInform" in page_url or (
        "确认无以上问题" in page_text
        and ("有部分问题" in page_text or "健康状况是否符合投保条件" in page_text)
    ):
        return "NODE-health-notice"
    if _looks_like_premium_calculation_state(page):
        return "NODE-premium-calculation"
    if _looks_like_product_detail_page(page):
        return "NODE-product-detail"
    for node_id in (
        "NODE-policy-result",
        "NODE-insure-form",
        "NODE-payment",
        "NODE-risk-control",
        "NODE-suitability",
        "NODE-health-notice",
        "NODE-underwriting",
        "NODE-beneficiary",
        "NODE-insured-info",
        "NODE-applicant-info",
        "NODE-plan-selection",
    ):
        if _matches_node_reach_contract(page, node_id):
            return node_id
    return None


def _align_planned_index(
    planned_nodes: list[dict[str, Any]],
    current_index: int,
    snapshot: dict[str, Any],
) -> int:
    inferred_node_id = _infer_current_node_id(snapshot)
    if not inferred_node_id:
        return current_index
    for index, planned in enumerate(planned_nodes):
        if str(planned.get("node_id") or "") == inferred_node_id:
            return index
    for index in range(current_index, len(planned_nodes)):
        if str(planned_nodes[index].get("node_id") or "") == inferred_node_id:
            return index
    return current_index


def _page_key_for_node_id(node_id: str) -> str:
    return node_id.removeprefix("NODE-") if node_id.startswith("NODE-") else node_id


def _dynamic_planned_node(node_id: str, snapshot: dict[str, Any]) -> dict[str, Any]:
    actual_url = str(snapshot.get("url") or "")
    actual_path = urlparse(actual_url).path if actual_url else ""
    return {
        "node_id": node_id,
        "page_key": _page_key_for_node_id(node_id),
        "url_pattern": actual_path,
        "state": {},
        "source": "agent3.dynamic_path_repair",
        "actual_url": actual_url,
    }


def _repair_planned_nodes_with_observed(
    planned_nodes: list[dict[str, Any]],
    current_index: int,
    snapshot: dict[str, Any],
    *,
    path_id: str,
    warnings: list[str],
) -> tuple[int, bool]:
    observed_node_id = _infer_current_node_id(snapshot)
    if not observed_node_id:
        return current_index, False
    if any(str(planned.get("node_id") or "") == observed_node_id for planned in planned_nodes):
        return current_index, False
    insert_at = max(0, min(current_index, len(planned_nodes)))
    planned_nodes.insert(insert_at, _dynamic_planned_node(observed_node_id, snapshot))
    warnings.append(
        f"Path {path_id} repaired Agent2 route by inserting observed node {observed_node_id} at index {insert_at}"
    )
    return insert_at, True


def _page_matches_node(page: dict[str, Any], planned: dict[str, Any]) -> bool:
    if _page_matches_pattern(str(page.get("url") or ""), str(planned.get("url_pattern") or "")):
        return True
    node_id = str(planned.get("node_id") or "")
    if _is_external_payment_handoff(page, node_id):
        return True
    if _looks_like_product_detail_page(page) and node_id not in {"NODE-product-detail", "NODE-plan-selection", "NODE-premium-calculation"}:
        return False
    return _matches_node_reach_contract(page, node_id)


def _planned_action_score(action: dict[str, Any], target_node_id: str) -> int:
    text = str(action.get("text") or "")
    normalized_text = " ".join(text.split())
    compact_text = "".join(text.split())
    if not text:
        return 0
    if any(token in compact_text for token in ("有部分问题", "存在问题", "重新投保")):
        return 0
    if target_node_id in {"NODE-health-notice", "NODE-suitability", "NODE-insure-form"} and any(
        token in compact_text for token in ("确认无以上问题", "无以上问题", "无上述问题")
    ):
        return 10_000
    if target_node_id == "NODE-insure-form" and ("确认无以上问题" in text or "无以上" in text):
        return 0
    if target_node_id == "NODE-policy-result" and "提交" in normalized_text:
        if normalized_text not in {"提交", "提交订单", "提交投保单", "提交核保"}:
            return 0
    if action.get("visible") is False:
        return 0
    if _is_document_action(action) or any(keyword in text for keyword in ("所有产品", "理赔", "客服", "信息披露")):
        return 0
    score = 0
    matched_hint = False
    for keyword in _NODE_ACTION_HINTS.get(target_node_id, ()):
        if keyword and (keyword in text or keyword in normalized_text or keyword in compact_text):
            matched_hint = True
            score += 100
    base_score = _action_score(action)
    if not matched_hint and base_score < 80:
        return 0
    score += base_score
    return score


def _best_action_for_node(page: dict[str, Any], target_node_id: str, attempted: set[str]) -> dict[str, Any] | None:
    visible_actions = [action for action in page.get("actions", []) or [] if action.get("visible") is not False]
    def action_key(action: dict[str, Any]) -> str:
        return f"{page.get('url')}|{action.get('selector')}|{action.get('text')}|{target_node_id}"

    def node_candidates(*, respect_attempted: bool) -> list[dict[str, Any]]:
        return [
            {
                **action,
                "source_url": page.get("url"),
                "target_planned_node_id": target_node_id,
                "score": _planned_action_score(action, target_node_id),
            }
            for action in visible_actions
            if _planned_action_score(action, target_node_id) > 0
            and (not respect_attempted or action_key(action) not in attempted)
        ]

    candidates = sorted(
        node_candidates(respect_attempted=True) or node_candidates(respect_attempted=False),
        key=lambda item: (-int(item.get("score", 0)), int(item.get("index", 0))),
    )
    if candidates:
        return candidates[0]

    # If the page does not expose node-specific text, still try the strongest visible
    # forward action once. This keeps Agent3 moving with minimal data instead of
    # waiting for perfect semantic matching.
    forward_candidates = sorted(
        [
            {
                **action,
                "source_url": page.get("url"),
                "target_planned_node_id": target_node_id,
                "score": _action_score(action),
            }
            for action in visible_actions
            if _action_score(action) >= 60
            and f"{page.get('url')}|{action.get('selector')}|{action.get('text')}|{target_node_id}" not in attempted
        ],
        key=lambda item: (-int(item.get("score", 0)), int(item.get("index", 0))),
    )
    return forward_candidates[0] if forward_candidates else None


def _best_transit_action(page: dict[str, Any], target_node_id: str, attempted: set[str]) -> dict[str, Any] | None:
    candidates = sorted(
        [
            {
                **action,
                "source_url": page.get("url"),
                "target_planned_node_id": target_node_id,
                "score": _action_score(action) + (120 if _is_forward_action(action) else 0),
            }
            for action in page.get("actions", []) or []
            if _is_safe_transit_action(action)
            and f"{page.get('url')}|{action.get('selector')}|{action.get('text')}|{target_node_id}" not in attempted
        ],
        key=lambda item: (-int(item.get("score", 0)), int(item.get("index", 0))),
    )
    return candidates[0] if candidates else None


def _should_try_transit_action(snapshot: dict[str, Any], *, is_terminal_target: bool) -> bool:
    return (not is_terminal_target) or _looks_like_choice_page(snapshot)


def _product_entry_attempt_allowed(
    page: dict[str, Any],
    action: dict[str, Any],
    target_node_id: str,
    attempted: set[str],
) -> bool:
    attempt_key = f"{page.get('url')}|{action.get('selector')}|{action.get('text')}|{target_node_id}"
    if attempt_key not in attempted:
        return True
    retry_key = f"{attempt_key}|retry"
    return retry_key not in attempted


def _best_product_entry_action(page: dict[str, Any], target_node_id: str, attempted: set[str]) -> dict[str, Any] | None:
    candidates = sorted(
        [
            {
                **action,
                "source_url": page.get("url"),
                "target_planned_node_id": target_node_id,
                "score": _action_score(action) + (400 if action.get("selector") in _PRODUCT_ENTRY_CTA_SELECTORS else 200),
            }
            for action in page.get("actions", []) or []
            if action.get("visible") is not False
            and (
                " ".join(str(action.get("text") or "").split()) in _PRODUCT_ENTRY_CTA_TEXTS
                or "".join(str(action.get("text") or "").split()) in _PRODUCT_ENTRY_CTA_TEXTS
                or any(keyword in "".join(str(action.get("text") or "").split()) for keyword in ("保费试算", "立即投保", "我要投保"))
                or action.get("selector") in _PRODUCT_ENTRY_CTA_SELECTORS
            )
            and not _is_document_action(action)
            and not any(keyword in str(action.get("text") or "") for keyword in _NEGATIVE_ACTION_HINTS)
            and _product_entry_attempt_allowed(page, action, target_node_id, attempted)
        ],
        key=lambda item: (-int(item.get("score", 0)), int(item.get("index", 0))),
    )
    return candidates[0] if candidates else None


def _selector_map(page: dict[str, Any]) -> dict[str, Any]:
    return {
        "page_key": page.get("page_key"),
        "url": page.get("url"),
        "fields": [
            {
                "name": field.get("name") or field.get("id") or field.get("placeholder") or f"field-{field.get('index')}",
                "selector": field.get("selector"),
                "tag": field.get("tag"),
                "type": field.get("type"),
                "label": field.get("label") or field.get("placeholder"),
            }
            for field in page.get("fields", [])
        ],
        "actions": [
            {
                "text": action.get("text"),
                "selector": action.get("selector"),
                "text_selector": action.get("text_selector"),
                "tag": action.get("tag"),
                "href": action.get("href"),
            }
            for action in page.get("actions", [])
            if action.get("text") or action.get("href") or action.get("selector")
        ],
    }


def _field_map(page: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "field_key": field.get("name") or field.get("id") or field.get("placeholder") or f"field-{field.get('index')}",
            "selector": field.get("selector"),
            "source": "dom",
            "required": False,
            "value_strategy": "test-data-profile",
            "raw": field,
        }
        for field in page.get("fields", [])
    ]


def _unique_in_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _observed_node_trace_from_steps(step_results: list[dict[str, Any]]) -> list[str]:
    observed = [
        str(step.get("observed_node_id") or step.get("planned_node_id") or "")
        for step in step_results
        if step.get("observed_node_id") or step.get("planned_node_id")
    ]
    return _unique_in_order(observed)


def _build_agent2_alignment(expected_node_ids: list[str], observed_node_trace: list[str]) -> dict[str, Any]:
    matched_nodes = [node_id for node_id in expected_node_ids if node_id in observed_node_trace]
    missing_nodes = [node_id for node_id in expected_node_ids if node_id not in observed_node_trace]
    suggested_nodes = _unique_in_order(
        observed_node_trace
        + [node_id for node_id in expected_node_ids if node_id not in observed_node_trace and node_id.endswith("policy-result")]
    )
    return {
        "source": "agent3.observed_trace",
        "planned_nodes": expected_node_ids,
        "observed_nodes": observed_node_trace,
        "matched_nodes": matched_nodes,
        "missing_nodes": missing_nodes,
        "plan_mismatch": bool(missing_nodes or observed_node_trace != matched_nodes),
        "suggested_agent2_nodes": suggested_nodes,
    }


def _has_dynamic_path_repair(step_results: list[dict[str, Any]], expected_node_ids: list[str]) -> bool:
    expected = set(expected_node_ids)
    for step in step_results:
        observed = str(step.get("observed_node_id") or "")
        planned = str(step.get("planned_node_id") or "")
        if step.get("dynamic_path_repair"):
            return True
        if observed and observed not in expected:
            return True
        if planned and planned not in expected:
            return True
    return False


def _repaired_node_ids_for_steps(expected_node_ids: list[str], step_results: list[dict[str, Any]]) -> list[str]:
    if not _has_dynamic_path_repair(step_results, expected_node_ids):
        return list(expected_node_ids)
    observed = _observed_node_trace_from_steps(step_results)
    target_node = expected_node_ids[-1] if expected_node_ids else None
    return _unique_in_order(observed + ([target_node] if target_node and target_node not in observed else []))


def _step_for_node(step_results: list[dict[str, Any]], node_id: str) -> dict[str, Any]:
    for step in reversed(step_results):
        if str(step.get("planned_node_id") or "") == node_id or str(step.get("observed_node_id") or "") == node_id:
            return step
    return {}


def _repaired_page_keys_for_nodes(
    node_ids: list[str],
    planned_page_by_node: dict[str, dict[str, Any]],
    step_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    repaired_pages: list[dict[str, Any]] = []
    for node_id in node_ids:
        planned = dict(planned_page_by_node.get(node_id, {}) or {})
        if planned:
            repaired_pages.append(planned)
            continue
        step = _step_for_node(step_results, node_id)
        actual_url = str(step.get("actual_url") or "")
        actual_path = urlparse(actual_url).path if actual_url else ""
        repaired_pages.append(
            {
                "node_id": node_id,
                "page_key": str(step.get("actual_page_key") or _page_key_for_node_id(node_id)),
                "url_pattern": actual_path,
                "source": "agent3.repaired_path",
                "actual_url": actual_url,
            }
        )
    return repaired_pages


def _build_path_exploration_results(
    regression_paths: list[dict[str, Any]] | None,
    pages: list[dict[str, Any]],
    action_trace: list[dict[str, Any]],
    planned_step_results: dict[str, list[dict[str, Any]]] | None = None,
    planned_page_catalog: list[dict[str, Any]] | None = None,
    page_content_records: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    planned_step_results = planned_step_results or {}
    planned_page_catalog = planned_page_catalog or []
    page_content_records = page_content_records or []
    planned_page_index = _planned_page_id_index(planned_page_catalog)
    records_by_url = {
        str(record.get("actual_url") or ""): record
        for record in page_content_records
        if record.get("actual_url")
    }
    for path_item in list(regression_paths or []):
        path_id = str(path_item.get("path_id") or "")
        planned_page_keys = list(path_item.get("page_keys", []) or [])
        matched_pages: list[dict[str, Any]] = []
        matched_page_node_ids: set[str] = set()
        for planned in planned_page_keys:
            pattern = str(planned.get("url_pattern") or "")
            node_id = str(planned.get("node_id") or "")
            for page in pages:
                if _page_matches_node(page, planned):
                    matched_pages.append(
                        {
                            "node_id": node_id,
                            "page_key": planned.get("page_key"),
                            "url_pattern": pattern,
                            "actual_url": page.get("url"),
                            "actual_page_key": page.get("page_key"),
                            "planned_page_id": _catalog_id_for_planned(planned, planned_page_index),
                            "page_content_record_id": _page_record_id(page),
                        }
                    )
                    matched_page_node_ids.add(node_id)
                    break

        step_results = list(planned_step_results.get(path_id, []) or [])
        step_actions = [
            item.get("action")
            for item in step_results
            if isinstance(item.get("action"), dict)
        ]
        trace_actions = [item for item in action_trace if str(item.get("path_id") or "") == path_id]
        path_actions = [
            {
                "step": index,
                "action": "click",
                "text": action.get("text"),
                "tag": action.get("tag"),
                "selector": action.get("selector"),
                "source_url": action.get("source_url"),
                "target_url": action.get("target_url"),
                "planned_from_node_id": action.get("planned_from_node_id"),
                "planned_to_node_id": action.get("planned_to_node_id"),
                "click_strategy": action.get("click_strategy"),
                "dismissed_overlays": action.get("dismissed_overlays", []),
                "status": "executed",
                **(
                    {"action_type": action.get("action_type")}
                    if action.get("action_type") is not None
                    else {}
                ),
                **(
                    {"submit_api_result": action.get("submit_api_result")}
                    if isinstance(action.get("submit_api_result"), dict)
                    else {}
                ),
                **(
                    {"blocked_reason_before_direct_submit": action.get("blocked_reason_before_direct_submit")}
                    if action.get("blocked_reason_before_direct_submit") is not None
                    else {}
                ),
            }
            for index, action in enumerate(
                trace_actions or step_actions,
                start=1,
            )
        ]
        expected_node_ids = [
            str(node_id)
            for node_id in path_item.get("nodes", [])
            if str(node_id) not in {"NODE-start", "NODE-end", "NODE-branch"}
        ]
        observed_node_trace = _observed_node_trace_from_steps(step_results)
        agent2_alignment = _build_agent2_alignment(expected_node_ids, observed_node_trace)
        repaired_node_ids = _repaired_node_ids_for_steps(expected_node_ids, step_results)
        effective_node_ids = repaired_node_ids if repaired_node_ids else expected_node_ids
        target_node = effective_node_ids[-1] if effective_node_ids else None
        matched_from_steps = {
            str(item.get("planned_node_id") or "")
            for item in step_results
            if item.get("matched")
        }
        matched_from_observed_steps = {
            str(item.get("observed_node_id") or "")
            for item in step_results
            if item.get("matched") and item.get("observed_node_id")
        }
        matched_node_ids: set[str] = set(matched_from_steps if step_results else matched_page_node_ids)
        matched_node_ids.update(matched_from_observed_steps)
        missing_nodes = [node_id for node_id in effective_node_ids if node_id not in matched_node_ids]
        target_matched = bool(target_node and target_node in matched_node_ids)
        order_generation_complete = _path_order_generation_complete(effective_node_ids, matched_node_ids, step_results)
        if order_generation_complete:
            missing_nodes = []
            target_matched = True
        is_complete = bool(target_matched and not missing_nodes)
        status = "explored" if is_complete else "partial" if path_actions or matched_node_ids else "blocked"
        blocked_reason = None
        explicit_block = next(
            (
                str(item.get("blocked_reason"))
                for item in reversed(step_results)
                if item.get("blocked_reason")
            ),
            None,
        )
        if status != "explored":
            if explicit_block:
                blocked_reason = explicit_block
            elif missing_nodes:
                blocked_reason = "Unexplored planned nodes: " + ", ".join(missing_nodes)
            elif not path_actions:
                blocked_reason = "No executable primary action found for Agent2 planned path"
        blocked_node = None
        if status != "explored":
            explicit_block_step = next(
                (
                    item
                    for item in reversed(step_results)
                    if item.get("blocked_reason")
                ),
                {},
            )
            explicit_block_action = explicit_block_step.get("action") if isinstance(explicit_block_step, dict) else {}
            explicit_block_target = (
                str(explicit_block_action.get("planned_to_node_id") or "")
                if isinstance(explicit_block_action, dict)
                else ""
            )
            blocked_node = (
                explicit_block_target
                or next((node_id for node_id in effective_node_ids if node_id not in matched_node_ids), target_node)
            )

        planned_page_refs = [
            _catalog_id_for_planned(planned, planned_page_index)
            for planned in planned_page_keys
            if _catalog_id_for_planned(planned, planned_page_index)
        ]
        page_content_refs = [
            str(record.get("page_content_record_id"))
            for record in page_content_records
            if any(node_id in effective_node_ids for node_id in record.get("matched_node_ids", []) or [])
        ]
        latest_step_by_node = {
            str(step.get("planned_node_id") or ""): step
            for step in step_results
            if step.get("planned_node_id")
        }
        execution_trace = [
            trace_item
            for step in step_results
            for trace_item in step.get("node_execution_trace", []) or []
        ]
        reached_node = next(
            (
                node_id
                for node_id in reversed(effective_node_ids)
                if node_id in matched_node_ids
            ),
            None,
        )
        planned_page_by_node = {
            str(planned.get("node_id") or ""): planned
            for planned in planned_page_keys
            if planned.get("node_id")
        }
        repaired_page_keys = _repaired_page_keys_for_nodes(effective_node_ids, planned_page_by_node, step_results)
        node_progress = []
        for node_id in effective_node_ids:
            step = latest_step_by_node.get(node_id, {})
            if not step:
                step = _step_for_node(step_results, node_id)
            planned = planned_page_by_node.get(node_id, {})
            actual_url = step.get("actual_url")
            record = records_by_url.get(str(actual_url or ""))
            node_matched = node_id in matched_node_ids
            progress_status = "matched" if node_matched else "blocked" if node_id == blocked_node else "pending"
            node_progress.append(
                {
                    "node_id": node_id,
                    "planned_page_key": planned.get("page_key"),
                    "planned_url_pattern": planned.get("url_pattern"),
                    "status": progress_status,
                    "actual_url": actual_url,
                    "actual_page_key": step.get("actual_page_key"),
                    "page_content_record_id": record.get("page_content_record_id") if record else None,
                    "action_used": step.get("action"),
                    "evidence": {
                        "title": record.get("title") if record else None,
                        "matched_text": [
                            hint
                            for hint in _NODE_TEXT_HINTS.get(node_id, ())
                            if record and hint in str(record.get("body_text_excerpt") or "")
                        ],
                    },
                    "blocked_reason": step.get("blocked_reason") if node_id == blocked_node else None,
                }
            )
        completion_rule = {
            "source": "agent3.repaired_path" if effective_node_ids != expected_node_ids else "agent2.nodes",
            "target_node": target_node,
            "required_nodes": effective_node_ids,
            "matched_nodes": [node_id for node_id in effective_node_ids if node_id in matched_node_ids],
            "missing_nodes": missing_nodes,
            "order_generation_boundary": order_generation_complete,
            "is_complete": is_complete,
        }
        terminal_boundary = _terminal_boundary_for_result(
            {
                "path_status": status,
                "target_node": target_node,
                "blocked_node": blocked_node,
                "blocked_reason": blocked_reason,
            }
        )
        results.append(
            {
                "path_id": path_id,
                "case_ids": list(path_item.get("case_ids", []) or []),
                "business_intent": path_item.get("business_intent"),
                "scenario_type": path_item.get("scenario_type"),
                "planned_nodes": expected_node_ids,
                "repaired_nodes": repaired_node_ids,
                "effective_nodes": effective_node_ids,
                "repaired_page_keys": repaired_page_keys,
                "path_repaired": effective_node_ids != expected_node_ids,
                "target_node": target_node,
                "planned_page_refs": planned_page_refs,
                "page_content_refs": page_content_refs,
                "planned_steps": step_results,
                "node_progress": node_progress,
                "node_execution_trace": execution_trace,
                "observed_node_trace": observed_node_trace,
                "agent2_alignment": agent2_alignment,
                "reached_node": reached_node,
                "matched_pages": matched_pages,
                "action_chain": path_actions,
                "path_status": status,
                "completion_rule": completion_rule,
                "blocked_node": blocked_node,
                "blocked_reason": blocked_reason,
                "terminal_boundary": terminal_boundary,
                "resume_condition": _resume_condition_for_boundary(terminal_boundary),
                "evidence_source": "agent3-live-browser",
                "coverage_refs": list(path_item.get("coverage_refs", []) or []),
                "rules": list(path_item.get("rules", []) or []),
                "assertions": list(path_item.get("assertions", []) or []),
            }
        )
    return results


def _is_overlay_dismiss_text(text: str) -> bool:
    normalized = " ".join(str(text or "").split())
    return normalized in {"已阅读并同意", "阅读并同意", "我已阅读并同意", "确定", "确认", "继续投保"}


async def _tap_or_click_coordinates(page: Any, x: int, y: int) -> str:
    try:
        touchscreen = getattr(page, "touchscreen", None)
        if touchscreen is not None:
            await page.touchscreen.tap(x, y)
            return "touchscreen"
    except Exception:
        pass
    await page.mouse.click(x, y)
    return "mouse"


async def _dismiss_active_protocol_dialogs(page: Any) -> list[dict[str, Any]]:
    dismissed_actions: list[dict[str, Any]] = []
    for _ in range(12):
        try:
            dismissed = await page.evaluate(
                """() => {
                    const norm = text => String(text || '').replace(/\\s+/g, '').trim();
                    const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                    const fire = el => {
                        for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                            el.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
                        }
                        if (typeof el.click === 'function') el.click();
                    };
                    const dialogs = Array.from(document.querySelectorAll('.am-modal,.adm-modal,[role="dialog"],.layui-layer'))
                        .filter(visible)
                        .filter(el => /\\u6295\\u4fdd\\u6761\\u4ef6|\\u6295\\u4fdd\\u91cd\\u8981\\u544a\\u77e5|\\u4fdd\\u9669\\u6761\\u6b3e|\\u514d\\u8d23\\u6761\\u6b3e|\\u5df2\\u9605\\u8bfb\\u5e76\\u540c\\u610f|\\u9605\\u8bfb\\u5e76\\u540c\\u610f/.test(norm(el.innerText || el.textContent)));
                    const dialog = dialogs[0];
                    if (!dialog) return null;
                    const buttons = Array.from(dialog.querySelectorAll('button,a,[role="button"],.am-modal-button,.adm-button,.am-button,span,div'))
                        .filter(visible)
                        .map((el, index) => {
                            const rect = el.getBoundingClientRect();
                            const cls = String(el.className || '');
                            const text = norm(el.innerText || el.textContent || el.value);
                            let score = 0;
                            if (/am-modal-button|adm-button|am-button|button|footer|operation|action/i.test(cls)) score += 10000;
                            if (el.matches('button,a,[role="button"]')) score += 6000;
                            if (rect.width >= 40 && rect.height >= 24) score += 1000;
                            score += Math.max(0, rect.top);
                            score -= index / 1000;
                            return { el, index, text, score };
                        })
                        .filter(item => item.text === '\\u5df2\\u9605\\u8bfb\\u5e76\\u540c\\u610f' || item.text === '\\u9605\\u8bfb\\u5e76\\u540c\\u610f' || item.text === '\\u786e\\u5b9a' || item.text === '\\u786e\\u8ba4')
                        .sort((a, b) => b.score - a.score);
                    const chosen = buttons[0];
                    if (!chosen) return null;
                    fire(chosen.el);
                    return { text: chosen.text, selector: 'active-protocol-dialog' };
                }"""
            )
        except Exception:
            break
        if not isinstance(dismissed, dict):
            break
        dismissed_actions.append(
            {
                "text": str(dismissed.get("text") or ""),
                "selector": dismissed.get("selector") or "active-protocol-dialog",
                "strategy": "active-protocol-dialog-dismiss",
                "source_url": page.url,
                "target_url": page.url,
            }
        )
        await page.wait_for_timeout(_POST_CLICK_SETTLE_MS)
    return dismissed_actions


async def _ensure_h5_agreement_checkbox_checked(page: Any) -> list[dict[str, Any]]:
    try:
        checked = await page.evaluate(
            """() => {
                const norm = text => String(text || '').replace(/\\s+/g, ' ').trim();
                const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                const getFiber = node => {
                    if (!node) return null;
                    const key = Object.keys(node).find(k => k.startsWith('__reactFiber$') || k.startsWith('__reactInternalInstance$'));
                    return key ? node[key] : null;
                };
                const cssSelector = el => {
                    if (!el) return '';
                    if (el.id) return `#${CSS.escape(el.id)}`;
                    if (el.name) return `${el.tagName.toLowerCase()}[name="${CSS.escape(el.name)}"]`;
                    const cls = String(el.className || '').split(/\\s+/).filter(Boolean)[0];
                    return cls ? `${el.tagName.toLowerCase()}.${CSS.escape(cls)}` : el.tagName.toLowerCase();
                };
                const setNativeChecked = input => {
                    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'checked')?.set;
                    if (setter) setter.call(input, true);
                    else input.checked = true;
                    input.setAttribute('checked', 'checked');
                    input.dispatchEvent(new Event('input', { bubbles: true }));
                    input.dispatchEvent(new Event('change', { bubbles: true }));
                };
                const fireReactHandlers = (input, root) => {
                    const eventLike = { target: input, currentTarget: input, preventDefault() {}, stopPropagation() {} };
                    for (const node of [input, root]) {
                        if (!node) continue;
                        for (const key of Object.keys(node)) {
                            if (!key.startsWith('__reactEventHandlers')) continue;
                            const handlers = node[key] || {};
                            for (const name of ['onChange', 'onClick']) {
                                if (typeof handlers[name] === 'function') {
                                    try { handlers[name](eventLike); } catch (_) {}
                                }
                            }
                        }
                        let fiber = getFiber(node);
                        let depth = 0;
                        while (fiber && depth < 8) {
                            const props = fiber.memoizedProps || fiber.pendingProps || fiber._currentElement?.props;
                            for (const name of ['onChange', 'onClick']) {
                                if (typeof props?.[name] === 'function') {
                                    try { props[name](eventLike); } catch (_) {}
                                }
                            }
                            fiber = fiber.return || fiber._hostParent || fiber._currentElement?._owner;
                            depth += 1;
                        }
                    }
                };
                const roots = Array.from(document.querySelectorAll('label,.am-checkbox-wrapper,.adm-checkbox,.agreement,.protocol'))
                    .filter(visible)
                    .map(el => ({ el, text: norm(el.innerText || el.textContent) }))
                    .filter(item => /\\u672c\\u4eba\\u5145\\u5206\\u9605\\u8bfb|\\u672c\\u4eba\\u5df2\\u9010\\u9875\\u9605\\u8bfb|\\u9605\\u8bfb\\u3001\\u7406\\u89e3\\u5e76\\u540c\\u610f|\\u9605\\u8bfb\\u5e76\\u540c\\u610f|\\u6295\\u4fdd\\u6761\\u4ef6|\\u4fdd\\u9669\\u6761\\u6b3e/.test(item.text));
                const records = [];
                for (const item of roots) {
                    const input = item.el.querySelector?.('input[type="checkbox"]')
                        || item.el.closest?.('label')?.querySelector?.('input[type="checkbox"]');
                    if (!input || input.checked) continue;
                    setNativeChecked(input);
                    fireReactHandlers(input, item.el);
                    for (const node of [input, item.el, item.el.closest?.('label,.am-checkbox-wrapper,.adm-checkbox,.agreement,.protocol')]) {
                        try {
                            node?.setAttribute?.('aria-checked', 'true');
                            node?.classList?.add('checked', 'active', 'selected', 'is-checked', 'am-checkbox-wrapper-checked');
                        } catch (_) {}
                    }
                    records.push({ text: item.text.slice(0, 80) || '\\u672c\\u4eba\\u5145\\u5206\\u9605\\u8bfb\\u5e76\\u540c\\u610f', selector: cssSelector(item.el) });
                }
                return records;
            }"""
        )
    except Exception:
        return []
    return [
        {
            "text": str(item.get("text") or ""),
            "selector": item.get("selector") or "h5-agreement-checkbox",
            "strategy": "h5-agreement-checkbox-sync",
            "source_url": page.url,
            "target_url": page.url,
        }
        for item in checked or []
        if isinstance(item, dict)
    ]


async def _dismiss_unfinished_policy_dialog(page: Any) -> dict[str, Any] | None:
    try:
        body_text = await page.locator("body").inner_text(timeout=1_000)
    except Exception:
        body_text = ""
    is_unfinished_policy = any(token in body_text for token in ("未完成的投保单", "是否继续投保", "继续投保"))
    is_existing_order = any(token in body_text for token in ("订单已存在", "再次提交", "需要再次提交"))
    if not (is_unfinished_policy or is_existing_order):
        return None
    try:
        if False:
            submit_rect = await page.evaluate(
                """() => {
                    const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                    const nodes = Array.from(document.querySelectorAll('.insure-footer .submit-btn, .submit-btn, a[role="button"].am-button-primary, .am-button-primary'))
                        .filter(visible)
                        .map((el, index) => {
                            const rect = el.getBoundingClientRect();
                            const text = String(el.innerText || el.textContent || el.value || el.getAttribute('aria-label') || '').replace(/\\s+/g, '');
                            const cls = String(el.className || '');
                            let score = 0;
                            if (/submit-btn/.test(cls)) score += 3000;
                            if (el.closest('.insure-footer')) score += 2000;
                            if (/提交|submit/i.test(text + cls)) score += 1000;
                            if (rect.width > 60 && rect.height > 24) score += 300;
                            score -= index;
                            return { left: rect.left, top: rect.top, width: rect.width, height: rect.height, text, score };
                        })
                        .sort((a, b) => b.score - a.score);
                    return nodes[0] || null;
                }"""
            )
            if submit_rect:
                submit_x = int(float(submit_rect.get("left", 0)) + float(submit_rect.get("width", 0)) / 2)
                submit_y = int(float(submit_rect.get("top", 0)) + float(submit_rect.get("height", 0)) / 2)
                if hasattr(page, "touchscreen"):
                    await page.touchscreen.tap(submit_x, submit_y)
                    click_strategy = "touchscreen-submit-btn"
                else:
                    await page.mouse.click(submit_x, submit_y)
                    click_strategy = "mouse-submit-btn"
                await page.wait_for_timeout(_POST_CLICK_SETTLE_MS)
                await capture_submit_phase("after-click")
                dismissed_overlays.extend(await _dismiss_blocking_overlays(page))
                if page.url != before_url or dismissed_overlays:
                    try:
                        await page.wait_for_load_state("domcontentloaded", timeout=3_000)
                    except Exception:
                        pass
                    await _wait_for_business_ready(page)
                    await page.wait_for_timeout(_POST_CLICK_SETTLE_MS)
                    return {
                        "text": text,
                        "tag": tag,
                        "source_url": before_url,
                        "target_url": page.url,
                        "selector": selector or action.get("selector"),
                        "score": action.get("score"),
                        "click_strategy": click_strategy,
                        "dismissed_overlays": dismissed_overlays,
                        "submit_diagnostics": submit_diagnostics,
                    }
        before_url = page.url
        clicked = await page.evaluate(
            """(mode) => {
                const norm = text => String(text || '').replace(/\\s+/g, '').trim();
                const visible = el => {
                    if (!el) return false;
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const fire = el => {
                    for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                        el.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
                    }
                    if (typeof el.click === 'function') el.click();
                };
                const roots = Array.from(document.querySelectorAll('[role="dialog"], .layui-layer, .am-modal, .adm-modal, .modal, body'))
                    .filter(visible)
                    .filter(el => /未完成的投保单|是否继续投保|继续投保|订单已存在|再次提交|需要再次提交/.test(norm(el.innerText || el.textContent)));
                for (const root of roots) {
                    const buttons = Array.from(root.querySelectorAll('button,a,[role="button"],.btn,[class*="btn"],[class*="button"],span,div'))
                        .filter(visible)
                        .map((el, index) => ({ el, index, text: norm(el.innerText || el.textContent || el.value) }))
                        .filter(item => {
                            if (mode === 'existing_order_view') return item.text === '查看' || item.text === '查看订单' || item.text === '查看投保单';
                            return item.text === '确定' || item.text === '确认' || item.text === '继续' || item.text === '继续投保' || item.text === '是';
                        });
                    const chosen = buttons.sort((a, b) => b.index - a.index)[0];
                    if (chosen) {
                        fire(chosen.el);
                        return chosen.text;
                    }
                }
                return null;
            }""",
            "existing_order_view" if is_existing_order else "unfinished_policy",
        )
        if clicked:
            await page.wait_for_timeout(1_500)
            selector = "existing-order-dialog-view" if is_existing_order else "unfinished-policy-dialog-continue"
            action_type = "existing_order_view" if is_existing_order else "unfinished_policy_continue"
            return {
                "text": f"继续投保弹窗:{clicked}",
                "source_url": before_url,
                "target_url": page.url,
                "selector": selector,
                "action_type": action_type,
            }
    except Exception:
        pass
    button_query = "button, a, [role='button'], .btn, [class*='btn'], [class*='button'], .layui-layer-btn a"
    positive_pattern = "查看|查看订单|查看投保单" if is_existing_order else "确定|确认|继续|是"
    candidates = [
        page.locator(".layui-layer-btn0, .layui-layer-btn a").filter(has_text=re.compile(positive_pattern)).last,
        page.locator(button_query).filter(has_text=re.compile(positive_pattern)).last,
    ]
    for locator in candidates:
        try:
            if not await locator.is_visible(timeout=1_000):
                continue
            before_url = page.url
            await locator.click(timeout=3_000, no_wait_after=True, force=True)
            await page.wait_for_timeout(1_500)
            selector = "existing-order-dialog-view" if is_existing_order else "unfinished-policy-dialog-continue"
            action_type = "existing_order_view" if is_existing_order else "unfinished_policy_continue"
            return {
                "text": "继续投保弹窗",
                "source_url": before_url,
                "target_url": page.url,
                "selector": selector,
                "action_type": action_type,
            }
        except Exception:
            continue
    return None


async def _dismiss_blocking_overlays(page: Any) -> list[dict[str, Any]]:
    dismissed: list[dict[str, Any]] = []
    button_query = "button, a, [role='button'], .btn, [class*='btn'], [class*='button'], .layui-layer-btn a"
    for _ in range(5):
        unfinished_dismissal = await _dismiss_unfinished_policy_dialog(page)
        if unfinished_dismissal:
            dismissed.append(unfinished_dismissal)
            continue
        dismissed_count = len(dismissed)
        try:
            task_next = await page.evaluate(
                """() => {
                    const norm = text => String(text || '').replace(/\\s+/g, '').trim();
                    const visible = el => {
                        if (!el) return false;
                        const rect = el.getBoundingClientRect();
                        const style = window.getComputedStyle(el);
                        return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                    };
                    const fire = el => {
                        el.scrollIntoView({ block: 'center', inline: 'center' });
                        for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                            el.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
                        }
                        if (typeof el.click === 'function') el.click();
                    };
                    const modal = Array.from(document.querySelectorAll('.task-modal,.am-modal,.am-modal-wrap,[role="dialog"]'))
                        .filter(visible)
                        .reverse()
                        .find(el => {
                            const text = norm(el.innerText || el.textContent);
                            return text.includes('即将进行以下操作') || text.includes('适当性问卷') || text.includes('银行卡签约');
                        });
                    if (!modal) return null;
                    const buttons = Array.from(modal.querySelectorAll('a,button,[role="button"],.am-button,.am-modal-button,div,span'))
                        .filter(visible)
                        .map((el, index) => {
                            const text = norm(el.innerText || el.textContent || el.value || el.getAttribute('aria-label'));
                            const rect = el.getBoundingClientRect();
                            let score = 0;
                            if (text === '去完成') score += 5000;
                            if (text.includes('去完成')) score += 3000;
                            if (/button|am-button|am-modal-button/.test(String(el.className || ''))) score += 500;
                            score += rect.top + rect.left / 1000;
                            return { el, text, index, score };
                        })
                        .filter(item => item.text.includes('去完成'))
                        .sort((a, b) => b.score - a.score || b.index - a.index);
                    if (!buttons.length) return null;
                    fire(buttons[0].el);
                    return { text: buttons[0].text || '去完成', selector: 'task-modal-go-complete' };
                }"""
            )
            if task_next:
                before_url = page.url
                await page.wait_for_timeout(1_200)
                dismissed.append(
                    {
                        "text": str(task_next.get("text") or "去完成"),
                        "source_url": before_url,
                        "target_url": page.url,
                        "selector": str(task_next.get("selector") or "task-modal-go-complete"),
                    }
                )
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=4_000)
                except Exception:
                    pass
                await page.wait_for_timeout(800)
                continue
        except Exception:
            pass
        for selector in ("button.btn-agree", ".btn-agree"):
            locator = page.locator(selector).first
            try:
                if not await locator.is_visible(timeout=1_000):
                    continue
                before_url = page.url
                await locator.click(timeout=3_000, no_wait_after=True)
                dismissed.append(
                    {
                        "text": "已阅读并同意",
                        "source_url": before_url,
                        "target_url": page.url,
                        "selector": selector,
                    }
                )
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=2_000)
                except Exception:
                    pass
                await page.wait_for_timeout(500)
            except Exception:
                continue
        if len(dismissed) > dismissed_count:
            continue
        for text in ("已阅读并同意", "阅读并同意", "我已阅读并同意", "确定", "确认"):
            if not _is_overlay_dismiss_text(text):
                continue
            locator = page.locator(button_query).filter(has_text=text)
            try:
                count = min(await locator.count(), 5)
            except Exception:
                continue
            for index in range(count - 1, -1, -1):
                item = locator.nth(index)
                try:
                    if not await item.is_visible(timeout=1_000):
                        continue
                    if text in {"已阅读并同意", "阅读并同意", "我已阅读并同意"}:
                        if not await item.locator("xpath=ancestor::*[contains(@class,'am-modal') or contains(@class,'adm-modal') or @role='dialog'][1]").count():
                            continue
                    before_url = page.url
                    await item.click(timeout=3_000, no_wait_after=True)
                    dismissed.append(
                        {
                            "text": text,
                            "source_url": before_url,
                            "target_url": page.url,
                        }
                    )
                    try:
                        await page.wait_for_load_state("domcontentloaded", timeout=2_000)
                    except Exception:
                        pass
                    await page.wait_for_timeout(500)
                except Exception:
                    continue
        if len(dismissed) == dismissed_count:
            break
    return dismissed


async def _read_required_agreement_documents(page: Any, body_text: str) -> list[dict[str, Any]]:
    agreement_tokens = ("投保声明", "续期授权声明", "保险条款", "责任免除", "隐私政策声明")
    if not hasattr(page, "locator"):
        return []

    actions: list[dict[str, Any]] = []
    for token in agreement_tokens:
        if token not in body_text:
            continue
        try:
            marked = await page.evaluate(
                """(token) => {
                    window.__e2eAgentReadAgreementDocuments = window.__e2eAgentReadAgreementDocuments || {};
                    if (window.__e2eAgentReadAgreementDocuments[token]) return false;
                    window.__e2eAgentReadAgreementDocuments[token] = true;
                    return true;
                }""",
                token,
            )
            if not marked:
                continue
        except Exception:
            pass
    return actions


async def _click_protocol_list_agreements(page: Any, body_text: str) -> list[dict[str, Any]]:
    groups = (
        ("本人充分阅读", ("投保声明", "续期授权声明"), "read_and_agree_primary"),
        ("本人已逐页阅读", ("保险条款", "责任免除", "隐私政策声明"), "read_and_agree_terms"),
    )
    actions: list[dict[str, Any]] = []
    for token, required, group_id in groups:
        if token not in body_text or not all(item in body_text for item in required):
            continue
        try:
            already_confirmed = await page.evaluate(
                """(groupId) => {
                    window.__e2eAgentConfirmedProtocolListGroups = window.__e2eAgentConfirmedProtocolListGroups || {};
                    return window.__e2eAgentConfirmedProtocolListGroups[groupId] === true;
                }""",
                group_id,
            )
            if already_confirmed:
                actions.append(
                    {
                        "text": token,
                        "tag": "checkbox",
                        "selector": f'protocol-list-group:{group_id}',
                        "source_url": page.url,
                        "target_url": page.url,
                        "score": None,
                        "click_strategy": "protocol-list-already-confirmed",
                        "dismissed_overlays": [],
                        "action_type": "agreement_observed",
                        "agreement_group_id": group_id,
                    }
                )
                continue
            row = page.locator(".protocol-list").filter(has_text=token).first
            if not await row.is_visible(timeout=1_000):
                continue
            row_text = " ".join((await row.inner_text(timeout=1_000)).split())
            if not all(item in row_text for item in required):
                continue
            checkbox = row.locator(".hz-check-item").first
            class_name = str(await checkbox.get_attribute("class", timeout=1_000) or "")
            selector = f'.protocol-list:has-text("{token}") .hz-check-item'
            if "hz-check-item-checked" in class_name:
                actions.append(
                    {
                        "text": row_text[:120],
                        "tag": "checkbox",
                        "selector": selector,
                        "source_url": page.url,
                        "target_url": page.url,
                        "score": None,
                        "click_strategy": "protocol-list-already-checked",
                        "dismissed_overlays": [],
                        "action_type": "agreement_observed",
                        "agreement_group_id": group_id,
                    }
                )
                continue
            before_url = page.url
            await checkbox.click(timeout=3_000, no_wait_after=True, force=True)
            await page.wait_for_timeout(_POST_CLICK_SETTLE_MS)
            try:
                dismissed_overlays = await _dismiss_blocking_overlays(page)
            except Exception:
                dismissed_overlays = []
            try:
                await page.evaluate(
                    """(groupId) => {
                        window.__e2eAgentConfirmedProtocolListGroups = window.__e2eAgentConfirmedProtocolListGroups || {};
                        window.__e2eAgentConfirmedProtocolListGroups[groupId] = true;
                    }""",
                    group_id,
                )
            except Exception:
                pass
            actions.append(
                {
                    "text": row_text[:120],
                    "tag": "checkbox",
                    "selector": selector,
                    "source_url": before_url,
                    "target_url": page.url,
                    "score": None,
                    "click_strategy": "playwright-protocol-list-check",
                    "dismissed_overlays": dismissed_overlays,
                    "action_type": "agreement",
                    "agreement_group_id": group_id,
                }
            )
        except Exception:
            continue
    return actions


async def _click_primary_action(page: Any, action: dict[str, Any]) -> dict[str, Any]:
    text = str(action.get("text") or "").strip()
    compact_text = "".join(text.split())
    is_submit_action = _looks_like_submit_action_text(text)
    tag = str(action.get("tag") or "a").strip() or "a"
    selector = str(action.get("selector") or "").strip()
    before_url = page.url
    dismissed_overlays: list[dict[str, Any]] = []
    submit_diagnostics: list[dict[str, Any]] = []
    click_strategy = "normal"
    locators = []

    try:
        had_analytics_shim = await page.evaluate(
            """() => typeof window.hzPageAction === 'function'
                && typeof globalThis.hzPageAction === 'function'"""
        )
        installed_analytics_shim = False if had_analytics_shim else await _install_hz_page_action_shim(page)
        if installed_analytics_shim:
            dismissed_overlays.append(
                {
                    "text": "agent3-hzPageAction-noop",
                    "selector": "window.hzPageAction",
                    "strategy": "analytics-shim",
                }
            )
    except Exception:
        pass

    async def capture_submit_phase(phase: str) -> None:
        if not is_submit_action:
            return
        diagnostic = await _capture_submit_diagnostics(page, phase=phase, action_text=text or compact_text)
        if diagnostic:
            submit_diagnostics.append(diagnostic)
        if phase == "after-click":
            for settle_ms, settle_phase in ((5_000, "after-click-settle-5s"), (10_000, "after-click-settle-15s")):
                await page.wait_for_timeout(settle_ms)
                diagnostic = await _capture_submit_diagnostics(page, phase=settle_phase, action_text=text or compact_text)
                if diagnostic:
                    submit_diagnostics.append(diagnostic)

    async def dismiss_after_click_overlays() -> list[dict[str, Any]]:
        handler = _dismiss_active_protocol_dialogs if is_submit_action else _dismiss_blocking_overlays
        return await handler(page)

    if "保费试算" in compact_text:
        trial_rect = await page.evaluate(
            """() => {
                const norm = text => String(text || '').replace(/\\s+/g, '');
                const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                const nodes = Array.from(document.querySelectorAll('button,a,[role="button"],div,span'))
                    .filter(visible)
                    .map((el, index) => {
                        const rect = el.getBoundingClientRect();
                        const style = window.getComputedStyle(el);
                        const text = norm(el.innerText || el.textContent || el.getAttribute('aria-label'));
                        if (!text.includes('保费试算')) return null;
                        let score = 0;
                        if (style.position === 'fixed' || style.position === 'sticky') score += 1000;
                        if (rect.width <= 160 && rect.height <= 160) score += 500;
                        score += rect.left + rect.top;
                        return { index, score, left: rect.left, top: rect.top, width: rect.width, height: rect.height };
                    })
                    .filter(Boolean)
                    .sort((a, b) => b.score - a.score || a.index - b.index);
                return nodes[0] || null;
            }"""
        )
        viewport = page.viewport_size or {}
        if trial_rect:
            click_x = int(float(trial_rect.get("left", 0)) + float(trial_rect.get("width", 0)) / 2)
            click_y = int(float(trial_rect.get("top", 0)) + float(trial_rect.get("height", 0)) / 2)
        else:
            click_x = max(10, int(viewport.get("width") or 390) - 48)
            click_y = max(10, int(viewport.get("height") or 844) - 170)
        await _tap_or_click_coordinates(page, click_x, click_y)
        await page.wait_for_timeout(_POST_CLICK_SETTLE_MS)
        try:
            body_after_coordinate = await _body_text_excerpt(page)
        except Exception:
            body_after_coordinate = ""
        if page.url != before_url or any(token in body_after_coordinate for token in ("起保日期", "投保人类型", "出生日期", "保费合计", "立即投保")):
            dismissed_overlays.extend(await _dismiss_blocking_overlays(page))
            await _wait_for_business_ready(page)
            await page.wait_for_timeout(_POST_CLICK_SETTLE_MS)
            return {
                "text": text,
                "tag": tag,
                "source_url": before_url,
                "target_url": page.url,
                "selector": selector or action.get("selector"),
                "score": action.get("score"),
                "click_strategy": "mouse-h5-floating-premium-quote",
                "dismissed_overlays": dismissed_overlays,
            }
        clicked = await page.evaluate(
            """() => {
                const norm = text => String(text || '').replace(/\\s+/g, '');
                const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                const fire = el => {
                    for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                        el.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
                    }
                    if (typeof el.click === 'function') el.click();
                };
                const nodes = Array.from(document.querySelectorAll('button,a,[role="button"],div,span'))
                    .filter(visible)
                    .map((el, index) => {
                        const rect = el.getBoundingClientRect();
                        const style = window.getComputedStyle(el);
                        const text = norm(el.innerText || el.textContent || el.getAttribute('aria-label'));
                        if (!text.includes('保费试算')) return null;
                        let score = 0;
                        if (style.position === 'fixed' || style.position === 'sticky') score += 1000;
                        score += Math.max(0, rect.left);
                        score += Math.max(0, rect.top);
                        if (rect.width <= 140 && rect.height <= 140) score += 500;
                        return { el, index, score, text, rect: { left: rect.left, top: rect.top, width: rect.width, height: rect.height } };
                    })
                    .filter(Boolean)
                    .sort((a, b) => b.score - a.score || a.index - b.index);
                const chosen = nodes[0];
                if (!chosen) return null;
                fire(chosen.el);
                return { text: chosen.text, rect: chosen.rect };
            }"""
        )
        if clicked:
            await page.wait_for_timeout(_POST_CLICK_SETTLE_MS)
            dismissed_overlays.extend(await _dismiss_blocking_overlays(page))
            if action.get("target_planned_node_id") != "NODE-premium-calculation":
                confirmed_quote = await page.evaluate(
                    """() => {
                        const norm = text => String(text || '').replace(/\\s+/g, '');
                        const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                        const fire = el => {
                            el.scrollIntoView({ block: 'center', inline: 'center' });
                            for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                                el.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
                            }
                            if (typeof el.click === 'function') el.click();
                        };
                        const confirm = Array.from(document.querySelectorAll('.am-modal-button, .am-button, a, button, [role="button"]'))
                            .filter(visible)
                            .find(el => ['确定', '确认'].includes(norm(el.innerText || el.textContent || el.value || el.getAttribute('aria-label'))));
                        if (!confirm) return null;
                        fire(confirm);
                        return { text: norm(confirm.innerText || confirm.textContent || confirm.value || confirm.getAttribute('aria-label')) };
                    }"""
                )
                if confirmed_quote:
                    await page.wait_for_timeout(_POST_CLICK_SETTLE_MS)
                    dismissed_overlays.extend(await _dismiss_blocking_overlays(page))
                    footer_clicked_after_quote = await page.evaluate(
                        """() => {
                            const footer = document.querySelector('.product-detail-footer');
                            if (!footer) return null;
                            const rect = footer.getBoundingClientRect();
                            if (!rect.width || !rect.height) return null;
                            const x = rect.left + rect.width * 0.84;
                            const y = rect.top + rect.height * 0.50;
                            const target = document.elementFromPoint(x, y) || footer;
                            for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                                target.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
                            }
                            if (typeof target.click === 'function') target.click();
                            return { text: String(target.innerText || target.textContent || '').trim(), x, y };
                        }"""
                    )
                    if footer_clicked_after_quote:
                        await page.wait_for_timeout(_POST_CLICK_SETTLE_MS)
                        dismissed_overlays.extend(await _dismiss_blocking_overlays(page))
            if page.url == before_url and any(
                item.get("selector") == "unfinished-policy-dialog-cancel" for item in dismissed_overlays
            ):
                retry_clicked = await page.evaluate(
                    """() => {
                        const norm = text => String(text || '').replace(/\\s+/g, '');
                        const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                        const fire = el => {
                            for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                                el.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
                            }
                            if (typeof el.click === 'function') el.click();
                        };
                        const nodes = Array.from(document.querySelectorAll('button,a,[role="button"],div,span'))
                            .filter(visible)
                            .map((el, index) => {
                                const rect = el.getBoundingClientRect();
                                const style = window.getComputedStyle(el);
                                const text = norm(el.innerText || el.textContent || el.getAttribute('aria-label'));
                                if (!text.includes('保费试算')) return null;
                                let score = 0;
                                if (style.position === 'fixed' || style.position === 'sticky') score += 1000;
                                score += Math.max(0, rect.left) + Math.max(0, rect.top);
                                if (rect.width <= 140 && rect.height <= 140) score += 500;
                                return { el, index, score, text };
                            })
                            .filter(Boolean)
                            .sort((a, b) => b.score - a.score || a.index - b.index);
                        const chosen = nodes[0];
                        if (!chosen) return null;
                        fire(chosen.el);
                        return { text: chosen.text };
                    }"""
                )
                if retry_clicked:
                    await page.wait_for_timeout(_POST_CLICK_SETTLE_MS)
                    dismissed_overlays.extend(await _dismiss_blocking_overlays(page))
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=3_000)
            except Exception:
                pass
            try:
                body_after_retry = await _body_text_excerpt(page)
            except Exception:
                body_after_retry = ""
            if page.url == before_url and not any(
                token in body_after_retry for token in ("起保日期", "投保人类型", "出生日期", "保费合计", "立即投保", "健康告知")
            ):
                footer_rect = await page.evaluate(
                    """() => {
                        const footer = document.querySelector('.product-detail-footer');
                        if (!footer) return null;
                        const rect = footer.getBoundingClientRect();
                        if (!rect.width || !rect.height) return null;
                        return { left: rect.left, top: rect.top, width: rect.width, height: rect.height };
                    }"""
                )
                if footer_rect:
                    footer_x = int(float(footer_rect.get("left", 0)) + float(footer_rect.get("width", 0)) * 0.84)
                    footer_y = int(float(footer_rect.get("top", 0)) + float(footer_rect.get("height", 0)) * 0.50)
                    await _tap_or_click_coordinates(page, footer_x, footer_y)
                    await page.wait_for_timeout(_POST_CLICK_SETTLE_MS)
                    dismissed_overlays.extend(await _dismiss_blocking_overlays(page))
                    try:
                        await page.wait_for_load_state("domcontentloaded", timeout=3_000)
                    except Exception:
                        pass
            await _wait_for_business_ready(page)
            await page.wait_for_timeout(_POST_CLICK_SETTLE_MS)
            return {
                "text": text,
                "tag": tag,
                "source_url": before_url,
                "target_url": page.url,
                "selector": selector or action.get("selector"),
                "score": action.get("score"),
                "click_strategy": "mouse-h5-floating-premium-quote+js-fallback",
                "dismissed_overlays": dismissed_overlays,
            }
    if compact_text == "投保":
        footer_clicked = await page.evaluate(
            """() => {
                const footer = document.querySelector('.product-detail-footer');
                if (!footer) return null;
                const rect = footer.getBoundingClientRect();
                if (!rect.width || !rect.height) return null;
                return { left: rect.left, top: rect.top, width: rect.width, height: rect.height };
            }"""
        )
        if footer_clicked:
            footer_x = int(float(footer_clicked.get("left", 0)) + float(footer_clicked.get("width", 0)) * 0.84)
            footer_y = int(float(footer_clicked.get("top", 0)) + float(footer_clicked.get("height", 0)) * 0.50)
            await _tap_or_click_coordinates(page, footer_x, footer_y)
            await page.wait_for_timeout(_POST_CLICK_SETTLE_MS)
            dismissed_overlays.extend(await _dismiss_blocking_overlays(page))
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=3_000)
            except Exception:
                pass
            await _wait_for_business_ready(page)
            await page.wait_for_timeout(_POST_CLICK_SETTLE_MS)
            try:
                body_after_footer = await _body_text_excerpt(page)
            except Exception:
                body_after_footer = ""
            if page.url != before_url or any(
                token in body_after_footer
                for token in ("起保日期", "投保人类型", "出生日期", "保费合计", "立即投保", "健康告知")
            ):
                return {
                    "text": text,
                    "tag": tag,
                    "source_url": before_url,
                    "target_url": page.url,
                    "selector": selector or action.get("selector"),
                    "score": action.get("score"),
                    "click_strategy": "mouse-h5-product-footer-insure",
                    "dismissed_overlays": dismissed_overlays,
                }
            dismissed_overlays.append(
                {
                    "text": "product/footer-insure-no-progress",
                    "selector": ".product-detail-footer",
                    "strategy": "diagnostic",
                }
            )
    if is_submit_action:
        pre_submit_filled = await _apply_minimal_form_data(page)
        if pre_submit_filled:
            dismissed_overlays.append(
                {
                    "text": f"submit-pre-fill:{len(pre_submit_filled)}",
                    "selector": "agent3-submit-pre-fill",
                    "strategy": "submit-pre-fill",
                }
            )
        pre_submit_dismissed = await _dismiss_active_protocol_dialogs(page)
        if pre_submit_dismissed:
            dismissed_overlays.extend(pre_submit_dismissed)
            await page.wait_for_timeout(_POST_CLICK_SETTLE_MS)
        agreement_checked = await _ensure_h5_agreement_checkbox_checked(page)
        if agreement_checked:
            dismissed_overlays.extend(agreement_checked)
            await page.wait_for_timeout(_POST_CLICK_SETTLE_MS)
        post_agreement_dismissed = await _dismiss_active_protocol_dialogs(page)
        if post_agreement_dismissed:
            dismissed_overlays.extend(post_agreement_dismissed)
            await page.wait_for_timeout(_POST_CLICK_SETTLE_MS)
        await page.wait_for_timeout(2_500)
        final_mock_data = await _load_agent3_mock_data_for_page(page)
        for final_select_attempt in range(2):
            final_select_filled = await _apply_h5_select_defaults(page, final_mock_data)
            if final_select_filled:
                dismissed_overlays.append(
                    {
                        "text": f"submit-final-select-fill:{final_select_attempt + 1}:{len(final_select_filled)}",
                        "selector": "agent3-submit-final-select-fill",
                        "strategy": "submit-final-select-fill",
                    }
                )
            await page.wait_for_timeout(1_200)
        final_pay_account_filled = await _ensure_visible_pay_account_value(page, final_mock_data)
        if final_pay_account_filled:
            dismissed_overlays.append(
                {
                    "text": f"submit-final-pay-account-fill:{len(final_pay_account_filled)}",
                    "selector": "agent3-submit-final-pay-account-fill",
                    "strategy": "submit-final-pay-account-fill",
                }
            )
            await page.wait_for_timeout(1_200)
        await _wait_for_business_ready(page)
        submit_rect = await page.evaluate(
            """() => {
                const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                const nodes = Array.from(document.querySelectorAll('.insure-footer .submit-btn, .submit-btn, a[role="button"].am-button-primary, .am-button-primary'))
                    .filter(visible)
                    .filter(el => !el.closest('.am-modal,.adm-modal,[role="dialog"],.layui-layer'))
                    .map((el, index) => {
                        const rect = el.getBoundingClientRect();
                        const text = String(el.innerText || el.textContent || el.value || el.getAttribute('aria-label') || '').replace(/\\s+/g, '');
                        const cls = String(el.className || '');
                        let score = 0;
                        if (/submit-btn/.test(cls)) score += 3000;
                        if (el.closest('.insure-footer')) score += 2000;
                        if (/提交|submit/i.test(text + cls)) score += 1000;
                        if (rect.width > 60 && rect.height > 24) score += 300;
                        score -= index;
                        return { left: rect.left, top: rect.top, width: rect.width, height: rect.height, text, score };
                    })
                    .sort((a, b) => b.score - a.score);
                return nodes[0] || null;
            }"""
        )
        if submit_rect:
            submit_x = int(float(submit_rect.get("left", 0)) + float(submit_rect.get("width", 0)) / 2)
            submit_y = int(float(submit_rect.get("top", 0)) + float(submit_rect.get("height", 0)) / 2)
            click_kind = await _tap_or_click_coordinates(page, submit_x, submit_y)
            click_strategy = f"{click_kind}-submit-btn"
            await page.wait_for_timeout(_POST_CLICK_SETTLE_MS)
            await capture_submit_phase("after-click")
            dismissed_overlays.extend(await dismiss_after_click_overlays())
            direct_submit_action = await _direct_submit_after_bank_validation_loop(
                page,
                source_url=before_url,
                submit_diagnostics=submit_diagnostics,
            )
            if direct_submit_action:
                return direct_submit_action
            if page.url != before_url or dismissed_overlays:
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=3_000)
                except Exception:
                    pass
                await _wait_for_business_ready(page)
                await page.wait_for_timeout(_POST_CLICK_SETTLE_MS)
                return {
                    "text": text,
                    "tag": tag,
                    "source_url": before_url,
                    "target_url": page.url,
                    "selector": selector or action.get("selector"),
                    "score": action.get("score"),
                    "click_strategy": click_strategy,
                    "dismissed_overlays": dismissed_overlays,
                    "submit_diagnostics": submit_diagnostics,
                }
    if compact_text in {"投保", "确定", "确认", "提交订单", "提交投保单", "提交核保"}:
        clicked = await page.evaluate(
            """(wanted) => {
                const norm = text => String(text || '').replace(/\\s+/g, '');
                const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                const isNoiseNode = el => !!el.closest(
                    '.am-accordion-item, .problem, .faq, [class*="faq"], [class*="problem"], [class*="accordion"], [class*="provision"], [href*="provision"]'
                );
                const fire = el => {
                    for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                        el.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
                    }
                    if (typeof el.click === 'function') el.click();
                };
                if (wanted === '投保') {
                    const footer = document.querySelector('.product-detail-footer');
                    const footerButton = footer && Array.from(footer.querySelectorAll('#submit-by, a,button,[role="button"],div,span'))
                        .filter(visible)
                        .find(el => norm(el.innerText || el.textContent || el.value || el.getAttribute('aria-label')).includes(wanted));
                    if (footerButton) {
                        fire(footerButton);
                        return { text: norm(footerButton.innerText || footerButton.textContent || footerButton.value || footerButton.getAttribute('aria-label')) };
                    }
                    const submitBy = document.querySelector('#submit-by');
                    if (submitBy && visible(submitBy)) {
                        fire(submitBy);
                        return { text: norm(submitBy.innerText || submitBy.textContent || submitBy.value || submitBy.getAttribute('aria-label')) };
                    }
                }
                const nodes = Array.from(document.querySelectorAll('a,button,[role="button"],input[type="button"],input[type="submit"],div,span'))
                    .filter(visible)
                    .map((el, index) => {
                        if (isNoiseNode(el)) return null;
                        const text = norm(el.innerText || el.textContent || el.value || el.getAttribute('aria-label'));
                        if (text !== wanted) return null;
                        const rect = el.getBoundingClientRect();
                        const cls = String(el.className || '');
                        let score = 0;
                        if (el.closest('.product-detail-footer')) score += 3000;
                        if (el.matches('#submit-by')) score += 2500;
                        if (/am-button|primary|btn|button/.test(cls)) score += 1000;
                        if (['A', 'BUTTON', 'INPUT'].includes(el.tagName)) score += 500;
                        if (rect.width > 40 && rect.height > 20) score += 100;
                        score -= index;
                        return { el, text, score };
                    })
                    .filter(Boolean)
                    .sort((a, b) => b.score - a.score);
                const chosen = nodes[0];
                if (!chosen) return null;
                fire(chosen.el);
                return { text: chosen.text };
            }""",
            compact_text,
        )
        if clicked:
            await page.wait_for_timeout(_POST_CLICK_SETTLE_MS)
            await capture_submit_phase("after-click")
            dismissed_overlays.extend(await dismiss_after_click_overlays())
            direct_submit_action = await _direct_submit_after_bank_validation_loop(
                page,
                source_url=before_url,
                submit_diagnostics=submit_diagnostics,
            )
            if direct_submit_action:
                return direct_submit_action
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=3_000)
            except Exception:
                pass
            await _wait_for_business_ready(page)
            await page.wait_for_timeout(_POST_CLICK_SETTLE_MS)
            return {
                "text": text,
                "tag": tag,
                "source_url": before_url,
                "target_url": page.url,
                "selector": selector or action.get("selector"),
                "score": action.get("score"),
                "click_strategy": "js-h5-action-button",
                "dismissed_overlays": dismissed_overlays,
                "submit_diagnostics": submit_diagnostics,
            }
    if "继续投保" in text and "取消" in text:
        continue_result = await _dismiss_unfinished_policy_dialog(page)
        if continue_result:
            continue_result["action_type"] = "unfinished_policy_continue"
            return continue_result
    if text == "提交":
        locators.extend(
            [
                page.locator(".submit_butTextx, .submit_butbgx, .js-adapt-question-btn, input[value='提交']").first,
                page.locator("button, input[type='button'], input[type='submit'], [role='button']").filter(has_text="提交").first,
            ]
        )
    if selector:
        locators.append(page.locator(selector).first)
    if text:
        locators.append(page.locator(tag).filter(has_text=text).first)
        locators.append(page.locator("button, input[type='button'], input[type='submit'], [role='button'], .btn, [class*='btn'], div").filter(has_text=text).first)
    if not locators:
        locators.append(page.locator(tag).nth(0))
    last_error: Exception | None = None
    clicked_locator = None
    for locator in locators:
        try:
            try:
                await locator.scroll_into_view_if_needed(timeout=1_000)
            except Exception:
                pass
            await locator.click(timeout=_CLICK_TIMEOUT_MS, no_wait_after=True)
            clicked_locator = locator
            break
        except Exception as exc:
            last_error = exc
            dismissed_overlays.extend(await dismiss_after_click_overlays())
            try:
                click_strategy = "force"
                await locator.click(timeout=_CLICK_TIMEOUT_MS, no_wait_after=True, force=True)
                clicked_locator = locator
                break
            except Exception as force_exc:
                last_error = force_exc
                try:
                    click_strategy = "js"
                    await locator.evaluate("element => element.click()", timeout=_CLICK_TIMEOUT_MS)
                    clicked_locator = locator
                    break
                except Exception as js_exc:
                    last_error = js_exc
                    continue
    else:
        if text:
            clicked = await page.evaluate(
                """(wantedRaw) => {
                    const wanted = String(wantedRaw || '').replace(/\\s+/g, '');
                    const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                    const norm = text => String(text || '').replace(/\\s+/g, '').trim();
                    const isNoiseNode = el => !!el.closest(
                        '.am-accordion-item, .problem, .faq, [class*="faq"], [class*="problem"], [class*="accordion"], [class*="provision"], [href*="provision"]'
                    );
                    const fire = el => {
                        el.scrollIntoView({ block: 'center', inline: 'center' });
                        for (const type of ['pointerdown', 'mousedown', 'mouseup', 'click', 'pointerup']) {
                            try {
                                el.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
                            } catch (_) {}
                        }
                        if (typeof el.click === 'function') el.click();
                    };
                    if (wanted === '投保') {
                        const footer = document.querySelector('.product-detail-footer');
                        const footerButton = footer && Array.from(footer.querySelectorAll('#submit-by, a,button,[role="button"],div,span'))
                            .filter(visible)
                            .find(el => norm(el.innerText || el.textContent || el.value || el.getAttribute('aria-label')).includes(wanted));
                        if (footerButton) {
                            fire(footerButton);
                            return { text: norm(footerButton.innerText || footerButton.textContent || footerButton.value || footerButton.getAttribute('aria-label')), score: 9999 };
                        }
                        const submitBy = document.querySelector('#submit-by');
                        if (submitBy && visible(submitBy)) {
                            fire(submitBy);
                            return { text: norm(submitBy.innerText || submitBy.textContent || submitBy.value || submitBy.getAttribute('aria-label')), score: 9998 };
                        }
                    }
                    const nodes = Array.from(document.querySelectorAll('a,button,[role="button"],input[type="button"],input[type="submit"],.am-modal-button,.am-button,div,span'))
                        .filter(visible)
                        .map((el, index) => {
                            if (isNoiseNode(el)) return null;
                            const text = norm(el.innerText || el.textContent || el.value || el.getAttribute('aria-label'));
                            if (!text || (text !== wanted && !text.includes(wanted))) return null;
                            const rect = el.getBoundingClientRect();
                            const cls = String(el.className || '');
                            let score = 0;
                            if (text === wanted) score += 2000;
                            if (el.closest('.product-detail-footer')) score += 3000;
                            if (el.matches('#submit-by')) score += 2500;
                            if (/am-modal-button|am-button|primary|btn|button/.test(cls)) score += 1000;
                            if (['A', 'BUTTON', 'INPUT'].includes(el.tagName)) score += 500;
                            if (rect.width > 30 && rect.height > 16) score += 100;
                            score -= Math.max(0, text.length - wanted.length);
                            score -= index / 1000;
                            return { el, text, score };
                        })
                        .filter(Boolean)
                        .sort((a, b) => b.score - a.score);
                    const chosen = nodes[0];
                    if (!chosen) return null;
                    fire(chosen.el);
                    return { text: chosen.text, score: chosen.score };
                }""",
                text,
            )
            if clicked:
                click_strategy = "js-text-fallback"
                await page.wait_for_timeout(_POST_CLICK_SETTLE_MS)
                await capture_submit_phase("after-click")
            else:
                if last_error:
                    raise last_error
        else:
            if last_error:
                raise last_error
    await page.wait_for_timeout(_POST_CLICK_SETTLE_MS)
    await capture_submit_phase("after-click")
    dismissed_overlays.extend(await dismiss_after_click_overlays())
    direct_submit_action = await _direct_submit_after_bank_validation_loop(
        page,
        source_url=before_url,
        submit_diagnostics=submit_diagnostics,
    )
    if direct_submit_action:
        return direct_submit_action
    if clicked_locator is not None and page.url == before_url and dismissed_overlays:
        try:
            retry_before_url = page.url
            await clicked_locator.click(timeout=_CLICK_TIMEOUT_MS, no_wait_after=True, force=True)
            await page.wait_for_timeout(_POST_CLICK_SETTLE_MS)
            click_strategy = f"{click_strategy}+post-overlay-retry"
            if page.url == retry_before_url:
                dismissed_overlays.extend(await dismiss_after_click_overlays())
        except Exception:
            pass
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=3_000)
    except Exception:
        pass
    await _wait_for_business_ready(page)
    await page.wait_for_timeout(_POST_CLICK_SETTLE_MS)
    return {
        "text": text,
        "tag": tag,
        "source_url": before_url,
        "target_url": page.url,
        "selector": selector or action.get("selector"),
        "score": action.get("score"),
        "click_strategy": click_strategy,
        "dismissed_overlays": dismissed_overlays,
        "submit_diagnostics": submit_diagnostics,
    }


async def _select_custom_questionnaire_items(
    page: Any,
    question_numbers: list[int] | None = None,
    *,
    strategy: str = "js-custom-questionnaire",
) -> list[dict[str, Any]]:
    try:
        body_text = await _body_text_excerpt(page)
        if any(token in body_text for token in ("保险产品适当性问卷", "适当性问卷", "评估问卷")):
            source_url = page.url
            records: list[dict[str, Any]] = []
            label_locator = page.locator("label.am-radio-wrapper, label.am-checkbox-wrapper, label")

            async def click_label(pattern: re.Pattern[str], label: str) -> bool:
                count = await label_locator.count()
                for index in range(count):
                    item = label_locator.nth(index)
                    try:
                        text = " ".join((await item.inner_text(timeout=1_000)).split())
                    except Exception:
                        continue
                    if not pattern.search(text):
                        continue
                    try:
                        await item.scroll_into_view_if_needed(timeout=1_000)
                    except Exception:
                        pass
                    await item.click(timeout=_CLICK_TIMEOUT_MS)
                    await page.wait_for_timeout(250)
                    records.append(
                        {
                            "text": label,
                            "selector": f"label >> text={text[:80]}",
                            "click_strategy": "playwright-adapt-questionnaire",
                        }
                    )
                    return True
                return False

            await click_label(
                re.compile(r"^C[.．、]\s*.*(?:未来生活规划|养老|子女教育|退休收入|保单利益)"),
                "适当性Q1=C.未来生活规划",
            )
            await click_label(
                re.compile(r"^D[.．、]\s*11\s*-\s*20年"),
                "适当性Q2=D.11-20年",
            )

            inline_inputs = page.locator("input.inline-input")
            inline_count = await inline_inputs.count()
            for index, value in enumerate(("1", "50")):
                if index >= inline_count:
                    break
                item = inline_inputs.nth(index)
                try:
                    await item.scroll_into_view_if_needed(timeout=1_000)
                except Exception:
                    pass
                await item.fill(value, timeout=_CLICK_TIMEOUT_MS)
                await item.dispatch_event("blur")
                records.append(
                    {
                        "text": f"适当性金额={value}",
                        "selector": f"input.inline-input:nth({index})",
                        "click_strategy": "playwright-adapt-questionnaire",
                    }
                )
                await page.wait_for_timeout(150)

            await click_label(
                re.compile(r"^A[.．、]\s*20%及以下"),
                "适当性Q5=A.20%及以下",
            )
            await click_label(
                re.compile(r"^A[.．、]\s*一次性支付"),
                "适当性Q6=A.一次性支付",
            )

            if records:
                await page.wait_for_timeout(_POST_CLICK_SETTLE_MS)
                return [
                    {
                        "text": str(item.get("text") or ""),
                        "tag": "option",
                        "selector": item.get("selector"),
                        "source_url": source_url,
                        "target_url": page.url,
                        "score": None,
                        "click_strategy": item.get("click_strategy") or strategy,
                        "dismissed_overlays": [],
                        "action_type": "minimal_data",
                        "question_number": item.get("question_number"),
                    }
                    for item in records
                ]
    except Exception:
        pass
    try:
        adapted = [] if question_numbers is not None else await page.evaluate(
            """async (strategy) => {
                const bodyText = document.body ? String(document.body.innerText || '') : '';
                if (!/保险产品适当性问卷|适当性问卷|评估问卷/.test(bodyText)) return [];
                const records = [];
                const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
                function visible(el) {
                    if (!el) return false;
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                }
                function textOf(el) {
                    return String(el?.innerText || el?.textContent || el?.value || el?.getAttribute?.('aria-label') || '').replace(/\\s+/g, ' ').trim();
                }
                function cssSelector(el) {
                    if (!el) return '';
                    if (el.id) return `#${CSS.escape(el.id)}`;
                    const cls = String(el.className || '').split(/\\s+/).filter(Boolean)[0];
                    const simple = cls ? `${el.tagName.toLowerCase()}.${CSS.escape(cls)}` : el.tagName.toLowerCase();
                    const same = Array.from(document.querySelectorAll(simple));
                    if (same.length === 1) return simple;
                    const index = same.indexOf(el);
                    return index >= 0 ? `${simple}:nth-of-type(${index + 1})` : simple;
                }
                function fire(el) {
                    if (!el) return;
                    el.scrollIntoView({ block: 'center', inline: 'center' });
                    for (const type of ['pointerdown', 'mousedown', 'touchstart', 'pointerup', 'mouseup', 'touchend', 'click']) {
                        try {
                            el.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
                        } catch (_) {}
                    }
                    if (typeof el.click === 'function') {
                        try { el.click(); } catch (_) {}
                    }
                }
                function setNativeValue(el, value) {
                    if (!el) return false;
                    if (el.disabled) el.removeAttribute('disabled');
                    if (el.readOnly) el.removeAttribute('readonly');
                    const setter = Object.getOwnPropertyDescriptor(el.constructor.prototype, 'value')?.set;
                    if (setter) setter.call(el, value);
                    else el.value = value;
                    el.setAttribute('value', value);
                    for (const type of ['input', 'change', 'compositionend', 'blur']) {
                        el.dispatchEvent(new Event(type, { bubbles: true }));
                    }
                    records.push({ text: `适当性金额=${value}`, selector: cssSelector(el), click_strategy: strategy });
                    return true;
                }
                const labels = Array.from(document.querySelectorAll('label.am-radio-wrapper, label.am-checkbox-wrapper, label'))
                    .filter(visible)
                    .map((el, index) => ({ el, index, text: textOf(el) }))
                    .filter(item => item.text && !/下一步|提交|返回|取消/.test(item.text));
                const chooseBy = (predicate, label) => {
                    const item = labels.find(candidate => predicate(candidate.text, candidate.index));
                    if (!item) return false;
                    const input = item.el.querySelector('input');
                    if (input && (input.checked || input.getAttribute('aria-checked') === 'true')) {
                        records.push({ text: item.text.slice(0, 100), selector: cssSelector(item.el), click_strategy: `${strategy}-already-selected` });
                        return true;
                    }
                    fire(item.el);
                    if (input) {
                        input.checked = true;
                        input.setAttribute('checked', 'checked');
                        input.dispatchEvent(new Event('input', { bubbles: true }));
                        input.dispatchEvent(new Event('change', { bubbles: true }));
                    }
                    item.el.classList.add('am-radio-wrapper-checked', 'am-checkbox-wrapper-checked', 'active', 'selected');
                    records.push({ text: (label || item.text).slice(0, 100), selector: cssSelector(item.el), click_strategy: strategy });
                    return true;
                };

                // 太平 e 满多年金的适当性匹配答案：
                // 购买目的选“未来生活规划”；保障期限选“11-20年”；
                // 保费预算 1 万，家庭年收入 50 万；保费占比选“20%及以下”；缴费年期选“一次性支付”。
                chooseBy(text => /^C[.．、]/.test(text) && /未来生活规划|养老|子女教育|退休收入|保单利益/.test(text), '适当性Q1=C.未来生活规划');
                chooseBy(text => /^D[.．、]/.test(text) && /11\\s*-\\s*20年/.test(text), '适当性Q2=D.11-20年');
                const inlineInputs = Array.from(document.querySelectorAll('input.inline-input, input[type="number"], input[type="tel"], input[type="text"]'))
                    .filter(visible)
                    .filter(el => !/radio|checkbox|hidden|file|button|submit/i.test(el.type || ''));
                const emptyInline = inlineInputs.filter(el => {
                    const cls = String(el.className || '');
                    const row = textOf(el.closest('div,li,label,section') || el);
                    return cls.includes('inline-input') || /保费预算|家庭年收入|万元/.test(row);
                });
                if (emptyInline[0]) setNativeValue(emptyInline[0], '1');
                if (emptyInline[1]) setNativeValue(emptyInline[1], '50');
                chooseBy(text => /^A[.．、]/.test(text) && /20%及以下/.test(text), '适当性Q5=A.20%及以下');
                chooseBy(text => /^A[.．、]/.test(text) && /一次性支付|一次性/.test(text), '适当性Q6=A.一次性支付');

                await sleep(500);
                return records;
            }""",
            strategy,
        )
        if adapted:
            await page.wait_for_timeout(_POST_CLICK_SETTLE_MS)
            return [
                {
                    "text": str(item.get("text") or ""),
                    "tag": "option",
                    "selector": item.get("selector"),
                    "source_url": page.url,
                    "target_url": page.url,
                    "score": None,
                    "click_strategy": item.get("click_strategy") or strategy,
                    "dismissed_overlays": [],
                    "action_type": "minimal_data",
                    "question_number": item.get("question_number"),
                }
                for item in adapted or []
            ]
    except Exception:
        pass
    try:
        selected = await page.evaluate(
            """(questionNumbers) => {
                const requested = Array.isArray(questionNumbers) && questionNumbers.length
                    ? new Set(questionNumbers.map(item => String(item)))
                    : null;
                const prefer = ['确认无以上问题', '无以上', '没有', '否', '不是', '通过', 'A.', 'A．', '已阅读', '同意'];
                const reject = ['不同意', '拒绝', '返回', '取消', '详情', '须知', '有部分问题', '存在问题', '重新投保'];
                const clicked = [];
                const customQuestionNodes = Array.from(document.querySelectorAll(
                    '.adapt-question-wrap [data-number], .js-adapt-question-content [data-number]'
                ));
                function textOf(el) {
                    return String(el.innerText || el.textContent || el.value || el.getAttribute('aria-label') || '').trim();
                }
                function visible(el) {
                    return !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                }
                function cssSelector(el) {
                    if (el.id) return `#${CSS.escape(el.id)}`;
                    const dataNumber = el.closest('[data-number]')?.getAttribute('data-number');
                    const cls = String(el.className || '').split(/\\s+/).filter(Boolean)[0];
                    if (dataNumber && cls) return `[data-number="${CSS.escape(dataNumber)}"] .${CSS.escape(cls)}`;
                    if (cls) return `${el.tagName.toLowerCase()}.${CSS.escape(cls)}`;
                    return el.tagName.toLowerCase();
                }
                function scoreOption(el) {
                    const text = textOf(el);
                    const lower = text.toLowerCase();
                    if (!text) return -1;
                    if (reject.some(token => text.includes(token))) return -1;
                    if (['下一步', '下一页', '继续', '提交', '完成'].some(token => text.includes(token))) return -1;
                    let score = 0;
                    for (const token of prefer) {
                        if (text.includes(token)) score += 100;
                    }
                    if (lower === 'a' || lower.startsWith('a.')) score += 30;
                    if (visible(el)) score += 20;
                    return score;
                }
                function clickLikeUser(el) {
                    const target = el.matches('input, button, a, label, [role=button], .insure-label')
                        ? el
                        : (el.querySelector('input.insure-label, button, [role=button], label, .insure-label') || el);
                    target.scrollIntoView({ block: 'center', inline: 'center' });
                    if (target.matches('input, button')) {
                        try { target.click(); } catch (_) {}
                    } else {
                        for (const type of ['pointerdown', 'mousedown', 'mouseup', 'click']) {
                            target.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
                        }
                    }
                    target.dispatchEvent(new Event('input', { bubbles: true }));
                    target.dispatchEvent(new Event('change', { bubbles: true }));
                    return target;
                }
                const containersByNumber = new Map();
                for (const node of customQuestionNodes) {
                    const number = node.getAttribute('data-number');
                    if (!number || containersByNumber.has(number)) continue;
                    if (requested && !requested.has(number)) continue;
                    containersByNumber.set(number, node);
                }
                for (const [number, container] of Array.from(containersByNumber.entries()).sort((a, b) => Number(a[0]) - Number(b[0]))) {
                    const options = Array.from(container.querySelectorAll(
                        'a, input.insure-label, input[type=button], button, [role=button], label, .insure-label'
                    ))
                        .map((el, index) => ({ el, index, score: scoreOption(el), text: textOf(el) }))
                        .filter(item => item.score > 0)
                        .sort((left, right) => right.score - left.score || left.index - right.index);
                    if (!options.length) continue;
                    const chosen = options[0];
                    const clickedElement = clickLikeUser(chosen.el);
                    clicked.push({
                        text: chosen.text.slice(0, 80),
                        selector: cssSelector(clickedElement),
                        click_strategy: '__STRATEGY__',
                        question_number: Number(number),
                    });
                }
                if (!requested) {
                    const noProblem = Array.from(document.querySelectorAll('a, input.insure-label, .insure-label'))
                        .find(el => textOf(el).includes('确认无以上问题'));
                    if (noProblem) {
                        const clickedElement = clickLikeUser(noProblem);
                        clicked.push({
                            text: textOf(noProblem).slice(0, 80),
                            selector: cssSelector(clickedElement),
                            click_strategy: '__STRATEGY__',
                        });
                    }
                }
                return clicked;
            }""".replace("__STRATEGY__", strategy),
            question_numbers,
        )
    except Exception:
        return []
    try:
        h5_filled = await _apply_h5_insure_form_data(page, json.loads(mock_data_payload))
        if h5_filled:
            filled = list(filled or []) + h5_filled
    except Exception:
        pass
    try:
        await page.wait_for_url("**/product/insure**", timeout=5_000)
    except Exception:
        await page.wait_for_timeout(max(_POST_CLICK_SETTLE_MS, 1_200))
    return [
        {
            "text": str(item.get("text") or ""),
            "tag": "option",
            "selector": item.get("selector"),
            "source_url": page.url,
            "target_url": page.url,
            "score": None,
            "click_strategy": item.get("click_strategy") or strategy,
            "dismissed_overlays": [],
            "action_type": "minimal_data",
            "question_number": item.get("question_number"),
        }
        for item in selected or []
    ]


async def _questionnaire_unfilled_numbers(page: Any) -> list[int]:
    try:
        return [
            int(item)
            for item in await page.evaluate(
                """() => {
                    const bodyText = document.body ? document.body.innerText : '';
                    const unfilledQuestionNumbers = [];
                    const regex = /第\\s*(\\d+)\\s*题未填写/g;
                    let match;
                    while ((match = regex.exec(bodyText)) !== null) {
                        const value = Number(match[1]);
                        if (!unfilledQuestionNumbers.includes(value)) {
                            unfilledQuestionNumbers.push(value);
                        }
                    }
                    return unfilledQuestionNumbers;
                }"""
            )
        ]
    except Exception:
        return []


async def _repair_unfilled_questionnaire_items(page: Any) -> list[dict[str, Any]]:
    question_numbers = await _questionnaire_unfilled_numbers(page)
    if not question_numbers:
        return []
    return await _select_custom_questionnaire_items(
        page,
        question_numbers,
        strategy="js-questionnaire-repair",
    )


async def _select_health_notice_safe_option(page: Any) -> list[dict[str, Any]]:
    try:
        selected = await page.evaluate(
            """async () => {
                function textOf(el) {
                    return String(el.innerText || el.textContent || el.value || el.getAttribute('aria-label') || '').trim();
                }
                function visible(el) {
                    return !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                }
                const candidates = Array.from(document.querySelectorAll('a, input.insure-label, .insure-label, button, [role=button]'))
                    .map((el, index) => ({ el, index, text: textOf(el) }))
                    .filter(item => /确认无以上问题|无以上问题|无上述问题/.test(item.text))
                    .sort((left, right) => {
                        const leftExact = left.text.includes('确认无以上问题') ? 1 : 0;
                        const rightExact = right.text.includes('确认无以上问题') ? 1 : 0;
                        return rightExact - leftExact || right.index - left.index;
                    });
                const chosen = candidates.find(item => visible(item.el)) || candidates[0];
                const target = chosen && chosen.el;
                if (!target) return [];
                if (!String(target.className || '').includes('active')) {
                    target.scrollIntoView({ block: 'center', inline: 'center' });
                    if (target.matches('input, button')) {
                        try { target.click(); } catch (_) {}
                    } else {
                        for (const type of ['pointerdown', 'mousedown', 'mouseup', 'click']) {
                            target.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
                        }
                    }
                    target.dispatchEvent(new Event('input', { bubbles: true }));
                    target.dispatchEvent(new Event('change', { bubbles: true }));
                }
                return [{
                    text: textOf(target).slice(0, 80),
                    selector: target.id ? `#${CSS.escape(target.id)}` : `${target.tagName.toLowerCase()} >> text=${JSON.stringify(textOf(target))}`,
                    click_strategy: 'js-health-notice-safe-option',
                }];
            }"""
        )
    except Exception:
        return []
    await page.wait_for_timeout(_POST_CLICK_SETTLE_MS)
    return [
        {
            "text": str(item.get("text") or ""),
            "tag": "option",
            "selector": item.get("selector"),
            "source_url": page.url,
            "target_url": page.url,
            "score": None,
            "click_strategy": item.get("click_strategy") or "js-health-notice-safe-option",
            "dismissed_overlays": [],
            "action_type": "minimal_data",
        }
        for item in selected or []
    ]


def _looks_like_agreement_feedback(text: str) -> bool:
    normalized = " ".join(str(text or "").split())
    return any(
        token in normalized
        for token in (
            "请先阅读并同意",
            "阅读并同意相关协议",
            "相关协议",
            "投保声明",
            "保险条款",
            "隐私政策",
            "授权声明",
        )
    )


async def _check_required_agreements(page: Any) -> list[dict[str, Any]]:
    body_text = await _body_text_full(page)
    if not _looks_like_agreement_feedback(body_text):
        return []
    document_actions = await _read_required_agreement_documents(page, body_text)
    protocol_list_actions = await _click_protocol_list_agreements(page, body_text)
    protocol_groups = {
        str(action.get("agreement_group_id") or "")
        for action in protocol_list_actions
        if action.get("agreement_group_id")
    }
    if {"read_and_agree_primary", "read_and_agree_terms"}.issubset(protocol_groups):
        return document_actions + protocol_list_actions
    try:
        checked = await page.evaluate(
            """async () => {
                const agreementTokens = ['阅读', '同意', '协议', '投保声明', '保险条款', '隐私', '授权', '告知'];
                const agreementLineTokens = ['本人充分阅读', '本人已逐页阅读'];
                const rejectTokens = ['不同意', '拒绝', '返回', '取消'];
                const checkedRecords = [];
                function textOf(el) {
                    if (!el) return '';
                    return String(
                        el.value || el.getAttribute('aria-label') || el.textContent || el.innerText || ''
                    ).trim();
                }
                function visible(el) {
                    return !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                }
                function cssSelector(el) {
                    if (el.id) return `#${CSS.escape(el.id)}`;
                    if (el.name) return `${el.tagName.toLowerCase()}[name="${CSS.escape(el.name)}"]`;
                    const cls = String(el.className || '').split(/\\s+/).filter(Boolean)[0];
                    const simple = cls ? `${el.tagName.toLowerCase()}.${CSS.escape(cls)}` : el.tagName.toLowerCase();
                    if (document.querySelectorAll(simple).length === 1) return simple;
                    const parts = [];
                    let current = el;
                    for (let depth = 0; current && current.nodeType === 1 && depth < 5; depth += 1) {
                        let part = current.tagName.toLowerCase();
                        const currentCls = String(current.className || '').split(/\\s+/).filter(Boolean)[0];
                        if (currentCls) part += `.${CSS.escape(currentCls)}`;
                        const siblings = Array.from(current.parentElement?.children || [])
                            .filter(item => item.tagName === current.tagName);
                        if (siblings.length > 1) part += `:nth-of-type(${siblings.indexOf(current) + 1})`;
                        parts.unshift(part);
                        const selector = parts.join(' > ');
                        if (document.querySelectorAll(selector).length === 1) return selector;
                        current = current.parentElement;
                    }
                    return parts.join(' > ') || simple;
                }
                function shortText(text) {
                    return String(text || '').replace(/\\s+/g, ' ').trim().slice(0, 120);
                }
                function candidateText(el) {
                    return [
                        textOf(el),
                        textOf(el.closest('label')),
                        textOf(el.closest('.form-item,.form-group,.agreement,.protocol,.checkbox,li,dd,div')),
                    ].filter(Boolean).join(' ');
                }
                function markChecked(el) {
                    const roots = [];
                    let current = el;
                    let depth = 0;
                    while (current && depth < 4) {
                        roots.push(current);
                        if (current.classList && /hz-check-item|checkbox|protocol|agreement/.test(String(current.className || ''))) {
                            break;
                        }
                        current = current.parentElement;
                        depth += 1;
                    }
                    for (const root of roots) {
                        if (!root) continue;
                        try {
                            root.setAttribute('aria-checked', 'true');
                            root.classList?.add('checked', 'active', 'selected', 'is-checked');
                        } catch (_) {}
                        for (const input of Array.from(root.querySelectorAll?.('input[type=checkbox]') || [])) {
                            input.checked = true;
                            input.setAttribute('checked', 'checked');
                            input.dispatchEvent(new Event('input', { bubbles: true }));
                            input.dispatchEvent(new Event('change', { bubbles: true }));
                        }
                    }
                }
                function clickLikeUser(el) {
                    const target = el.closest('label, .checkbox, .agreement, .protocol, li, div') || el;
                    target.scrollIntoView({ block: 'center', inline: 'center' });
                    if (el.matches('input[type=checkbox]') && !el.checked) {
                        el.checked = true;
                    }
                    for (const type of ['pointerdown', 'mousedown', 'mouseup', 'click']) {
                        target.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
                    }
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    markChecked(el);
                    markChecked(target);
                    return target;
                }
                const allElements = Array.from(document.querySelectorAll('body *')).slice(0, 2500);
                window.__e2eAgentConfirmedAgreementGroups = window.__e2eAgentConfirmedAgreementGroups || {};
                window.__e2eAgentAgreementAttemptCounts = window.__e2eAgentAgreementAttemptCounts || {};
                const seen = new Set();
                const seenAgreementTokens = new Set();
                const agreementControlGroups = [
                    {
                        token: '本人充分阅读',
                        required: ['投保声明', '续期授权声明'],
                    },
                    {
                        token: '本人已逐页阅读',
                        required: ['保险条款', '责任免除', '隐私政策声明'],
                    },
                ];
                const controlSelector = [
                    'input[type=checkbox]',
                    '[role=checkbox]',
                    '.checkbox',
                    '[class*=checkbox]',
                    '.hz-check-item',
                    '[class*=check]',
                    '[class*=agree]',
                ].join(',');
                function isAgreementControl(el) {
                    if (!el) return false;
                    const tag = el.tagName.toLowerCase();
                    if (['a', 'button', 'script', 'style'].includes(tag)) return false;
                    const text = shortText(textOf(el));
                    if (rejectTokens.some(token => text.includes(token))) return false;
                    if (el.matches('input[type=checkbox], [role=checkbox], .checkbox, [class*=checkbox], .hz-check-item')) {
                        return true;
                    }
                    const className = String(el.className || '');
                    return /check|agree|protocol|read/i.test(className);
                }
                function isCheckedControl(el) {
                    const input = el?.matches?.('input[type=checkbox]') ? el : el?.querySelector?.('input[type=checkbox]');
                    return !!(input && input.checked)
                        || el?.getAttribute?.('aria-checked') === 'true'
                        || /checked|active|selected|is-checked/.test(String(el?.className || ''));
                }
                function lineControlScore(control, line, index) {
                    if (!visible(control)) return -1;
                    const lineBox = line.getBoundingClientRect();
                    const controlBox = control.getBoundingClientRect();
                    const lineMidY = lineBox.top + lineBox.height / 2;
                    const controlMidY = controlBox.top + controlBox.height / 2;
                    const verticalDistance = Math.abs(lineMidY - controlMidY);
                    const sameRow = verticalDistance <= Math.max(18, lineBox.height, controlBox.height);
                    const leftOrInside = controlBox.left <= lineBox.right + 8;
                    let score = 1000 - verticalDistance - Math.abs(controlBox.left - lineBox.left) / 10 - index;
                    if (sameRow) score += 1000;
                    if (leftOrInside) score += 200;
                    if (isCheckedControl(control)) score += 50;
                    return score;
                }
                function nearestAgreementLineControl(token) {
                    const lineCandidates = allElements
                        .map((el, index) => {
                            const tag = el.tagName.toLowerCase();
                            const text = shortText(textOf(el));
                            if (!text.includes(token)) return null;
                            if (['a', 'button', 'script', 'style'].includes(tag)) return null;
                            const childHasSameLine = Array.from(el.children || []).some(child => {
                                const childTag = child.tagName.toLowerCase();
                                return childTag !== 'a' && shortText(textOf(child)).includes(token);
                            });
                            if (childHasSameLine && !el.matches(controlSelector) && !el.querySelector(controlSelector)) return null;
                            return { el, index, text, score: 1000 - text.length };
                        })
                        .filter(Boolean)
                        .sort((left, right) => right.score - left.score || left.index - right.index);
                    for (const line of lineCandidates) {
                        const roots = [];
                        let root = line.el;
                        for (let depth = 0; root && depth < 5; depth += 1) {
                            roots.push(root);
                            root = root.parentElement;
                        }
                        const controls = roots
                            .flatMap(item => Array.from(item.querySelectorAll?.(controlSelector) || []))
                            .filter((item, index, items) => items.indexOf(item) === index)
                            .filter(isAgreementControl)
                            .map((control, index) => ({
                                control,
                                score: lineControlScore(control, line.el, index),
                            }))
                            .filter(item => item.score > 0)
                            .sort((left, right) => right.score - left.score);
                        if (controls.length) {
                            return {
                                el: line.el,
                                control: controls[0].control,
                                text: line.text,
                                checked: isCheckedControl(controls[0].control),
                            };
                        }
                    }
                    return null;
                }
                function nearestAgreementContainer(token, required) {
                    const candidates = allElements
                        .map((el, index) => {
                            const tag = el.tagName.toLowerCase();
                            const text = shortText(textOf(el));
                            if (!text.includes(token)) return null;
                            if (['a', 'button', 'script', 'style'].includes(tag)) return null;
                            if (!required.every(item => text.includes(item))) return null;
                            const childHasSameLine = Array.from(el.children || []).some(child => {
                                const childTag = child.tagName.toLowerCase();
                                return childTag !== 'a' && shortText(textOf(child)).includes(token);
                            });
                            if (childHasSameLine && !el.matches(controlSelector) && !el.querySelector(controlSelector)) return null;
                            let root = el;
                            for (let depth = 0; root && depth < 5; depth += 1) {
                                const rootText = shortText(textOf(root));
                                const controls = Array.from(root.querySelectorAll(controlSelector)).filter(isAgreementControl);
                                if (rootText.includes(token) && required.every(item => rootText.includes(item)) && controls.length) {
                                    return { el: root, controls, index, text: rootText, score: 2000 - rootText.length - depth };
                                }
                                root = root.parentElement;
                            }
                            return { el, controls: [], index, text, score: 1000 - text.length };
                        })
                        .filter(Boolean)
                        .sort((left, right) => right.score - left.score || left.index - right.index);
                    return candidates[0] || null;
                }
                function attemptAllowed(token) {
                    return Number(window.__e2eAgentAgreementAttemptCounts[token] || 0) < 2;
                }
                function recordAttempt(token) {
                    window.__e2eAgentAgreementAttemptCounts[token] = Number(window.__e2eAgentAgreementAttemptCounts[token] || 0) + 1;
                    seenAgreementTokens.add(token);
                }
                function tokenForText(text) {
                    return agreementLineTokens.find(token => String(text || '').includes(token)) || '';
                }
                for (const group of agreementControlGroups) {
                    if (!attemptAllowed(group.token)) {
                        seenAgreementTokens.add(group.token);
                        continue;
                    }
                    const lineControl = nearestAgreementLineControl(group.token);
                    if (lineControl?.control) {
                        seen.add(lineControl.text);
                        seenAgreementTokens.add(group.token);
                        if (lineControl.checked) {
                            if (!window.__e2eAgentConfirmedAgreementGroups[group.token]) {
                                checkedRecords.push({
                                    text: lineControl.text,
                                    selector: cssSelector(lineControl.control),
                                    click_strategy: 'agreement-line-control-already-checked',
                                    action_type: 'agreement_observed',
                                });
                            }
                            window.__e2eAgentConfirmedAgreementGroups[group.token] = true;
                            continue;
                        }
                        const clicked = clickLikeUser(lineControl.control);
                        window.__e2eAgentConfirmedAgreementGroups[group.token] = true;
                        recordAttempt(group.token);
                        checkedRecords.push({
                            text: lineControl.text,
                            selector: cssSelector(lineControl.control || clicked),
                            click_strategy: 'js-agreement-line-control-check',
                        });
                        continue;
                    }
                    const candidate = nearestAgreementContainer(group.token, group.required);
                    if (!candidate) continue;
                    const controls = candidate.controls.length
                        ? candidate.controls
                        : Array.from(candidate.el.querySelectorAll(controlSelector)).filter(isAgreementControl);
                    const target = controls.find(el => {
                        const input = el.matches('input[type=checkbox]') ? el : el.querySelector('input[type=checkbox]');
                        return !(input && input.checked) && visible(el);
                    }) || controls.find(visible) || controls[0];
                    if (!target) continue;
                    const clicked = clickLikeUser(target);
                    const key = shortText(candidate.text || group.token);
                    seen.add(key);
                    seenAgreementTokens.add(group.token);
                    window.__e2eAgentConfirmedAgreementGroups[group.token] = true;
                    recordAttempt(group.token);
                    checkedRecords.push({
                        text: key,
                        selector: cssSelector(target || clicked),
                        click_strategy: 'js-agreement-control-check',
                    });
                }
                const agreementCandidates = Array.from(document.querySelectorAll(
                    'input[type=checkbox], label, [role=checkbox], .checkbox, [class*=checkbox], .hz-check-item'
                ));
                const ranked = agreementCandidates
                    .map((el, index) => {
                        const text = candidateText(el);
                        if (!text || rejectTokens.some(token => text.includes(token))) return null;
                        const tokenHits = agreementTokens.filter(token => text.includes(token)).length;
                        if (tokenHits < 2) return null;
                        const input = el.matches('input[type=checkbox]')
                            ? el
                            : el.querySelector('input[type=checkbox]');
                        const alreadyChecked = !!(input && input.checked);
                        let score = tokenHits * 100;
                        if (input) score += 50;
                        if (visible(el) || (input && visible(input))) score += 20;
                        if (alreadyChecked) score -= 500;
                        return { el, input, index, text, score, alreadyChecked };
                    })
                    .filter(Boolean)
                    .filter(item => item.score > 0)
                    .sort((left, right) => right.score - left.score || left.index - right.index);
                for (const item of ranked) {
                    const key = item.text.slice(0, 80);
                    if (seen.has(key)) continue;
                    const token = tokenForText(key);
                    if (token && !attemptAllowed(token)) continue;
                    if (Array.from(seenAgreementTokens).some(token => key.includes(token))) continue;
                    seen.add(key);
                    if (token) recordAttempt(token);
                    const clicked = clickLikeUser(item.input || item.el);
                    checkedRecords.push({
                        text: key,
                        selector: cssSelector(item.input || clicked),
                        click_strategy: 'js-agreement-check',
                    });
                    if (checkedRecords.length >= 8) break;
                }
                for (const token of agreementLineTokens) {
                    if (seenAgreementTokens.has(token)) continue;
                    if (!attemptAllowed(token)) continue;
                    const lineCandidates = allElements
                        .map((el, index) => {
                            const tag = el.tagName.toLowerCase();
                            const text = shortText(textOf(el));
                            if (!text.includes(token)) return null;
                            if (['a', 'button', 'script', 'style'].includes(tag)) return null;
                            const childHasSameLine = Array.from(el.children || []).some(child => {
                                const childTag = child.tagName.toLowerCase();
                                return childTag !== 'a' && shortText(textOf(child)).includes(token);
                            });
                            if (childHasSameLine && !el.matches(controlSelector) && !el.querySelector(controlSelector)) return null;
                            return { el, index, text, score: 1000 - text.length };
                        })
                        .filter(Boolean)
                        .sort((left, right) => right.score - left.score || left.index - right.index);
                    const chosen = lineCandidates[0];
                    if (!chosen || seen.has(chosen.text)) continue;
                    seen.add(chosen.text);
                    recordAttempt(token);
                    const clicked = clickLikeUser(chosen.el);
                    checkedRecords.push({
                        text: chosen.text,
                        selector: cssSelector(clicked),
                        click_strategy: 'js-agreement-line-check',
                    });
                    if (checkedRecords.length >= 8) break;
                }
                const textWalker = document.createTreeWalker(
                    document.body,
                    NodeFilter.SHOW_TEXT,
                    {
                        acceptNode(node) {
                            const text = shortText(node.nodeValue || '');
                            if (!agreementLineTokens.some(token => text.includes(token))) {
                                return NodeFilter.FILTER_REJECT;
                            }
                            return NodeFilter.FILTER_ACCEPT;
                        },
                    }
                );
                const textNodeCandidates = [];
                let textNode;
                while ((textNode = textWalker.nextNode())) {
                    let parent = textNode.parentElement;
                    let depth = 0;
                    while (parent && depth < 4) {
                        const tag = parent.tagName.toLowerCase();
                        if (!['a', 'button', 'script', 'style'].includes(tag)) {
                            const text = shortText(parent.textContent || parent.innerText || textNode.nodeValue || '');
                            textNodeCandidates.push({
                                el: parent,
                                text,
                                score: 1000 - text.length - depth,
                            });
                            break;
                        }
                        parent = parent.parentElement;
                        depth += 1;
                    }
                }
                textNodeCandidates.sort((left, right) => right.score - left.score);
                for (const item of textNodeCandidates) {
                    if (!item.text || seen.has(item.text)) continue;
                    const token = tokenForText(item.text);
                    if (token && (!attemptAllowed(token) || seenAgreementTokens.has(token))) continue;
                    seen.add(item.text);
                    if (token) recordAttempt(token);
                    const clicked = clickLikeUser(item.el);
                    checkedRecords.push({
                        text: item.text,
                        selector: cssSelector(clicked),
                        click_strategy: 'js-agreement-text-node-check',
                    });
                    if (checkedRecords.length >= 8) break;
                }
                return checkedRecords;
            }"""
        )
    except Exception as exc:
        return [
            {
                "text": f"agreement_scan_error:{type(exc).__name__}:{str(exc)[:120]}",
                "tag": "diagnostic",
                "selector": None,
                "source_url": page.url,
                "target_url": page.url,
                "score": None,
                "click_strategy": "agreement-scan-error",
                "dismissed_overlays": [],
                "action_type": "agreement_diagnostic",
            }
        ]
    if not checked:
        if document_actions:
            return document_actions
        return [
            {
                "text": f"agreement_scan_miss:body_len={len(body_text)}",
                "tag": "diagnostic",
                "selector": None,
                "source_url": page.url,
                "target_url": page.url,
                "score": None,
                "click_strategy": "agreement-scan-miss",
                "dismissed_overlays": [],
                "action_type": "agreement_diagnostic",
            }
        ]
    await page.wait_for_timeout(_POST_CLICK_SETTLE_MS)
    try:
        dismissed_overlays = await _dismiss_blocking_overlays(page)
    except Exception:
        dismissed_overlays = []
    agreement_actions = [
        {
            "text": str(item.get("text") or ""),
            "tag": item.get("tag") or "checkbox",
            "selector": item.get("selector"),
            "source_url": page.url,
            "target_url": page.url,
            "score": None,
            "click_strategy": item.get("click_strategy") or "js-agreement-check",
            "dismissed_overlays": dismissed_overlays,
            "action_type": item.get("action_type") or "agreement",
        }
        for item in checked or []
    ]
    agreement_actions = document_actions + protocol_list_actions + agreement_actions
    agreement_actions.extend(
        {
            "text": str(item.get("text") or ""),
            "tag": "overlay",
            "selector": item.get("selector"),
            "source_url": item.get("source_url") or page.url,
            "target_url": item.get("target_url") or page.url,
            "score": None,
            "click_strategy": "agreement-overlay-dismiss",
            "dismissed_overlays": [],
            "action_type": "agreement_dismiss",
        }
        for item in dismissed_overlays
    )
    return agreement_actions


async def _apply_minimal_choice_data(page: Any) -> list[dict[str, Any]]:
    """Select safe default questionnaire options so exploration can continue."""
    body_text = await _body_text_excerpt(page)
    if all(token in body_text for token in ("投保人信息", "被保险人信息", "提交投保单")):
        return []
    if (
        ("被保险人健康告知" in body_text or "健康状况是否符合投保条件" in body_text or "确认无以上问题" in body_text)
        and "适当性评估问卷" not in body_text
    ):
        health_selected = await _select_health_notice_safe_option(page)
        if health_selected:
            return health_selected
    custom_selected = await _select_custom_questionnaire_items(page)
    if custom_selected:
        return custom_selected
    try:
        selected = await page.evaluate(
            """async () => {
                const prefer = ['确认无以上问题', '无以上', '没有', '否', '不是', '通过', 'A.', 'A．', '已阅读', '同意'];
                const reject = ['不同意', '拒绝', '返回', '取消', '详情', '须知', '有部分问题', '存在问题', '重新投保'];
                const clicked = [];
                const seenGroups = new Set();
                function textOf(el) {
                    return String(el.innerText || el.textContent || el.value || el.getAttribute('aria-label') || '').trim();
                }
                function visible(el) {
                    return !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                }
                function cssSelector(el) {
                    if (el.id) return `#${CSS.escape(el.id)}`;
                    const cls = String(el.className || '').split(/\\s+/).filter(Boolean)[0];
                    if (cls) return `${el.tagName.toLowerCase()}.${CSS.escape(cls)}`;
                    return el.tagName.toLowerCase();
                }
                function clickLikeUser(el) {
                    const target = el.closest('label, .js-answer-item, .answer-item, .insure-label, li, [role=button], button, div') || el;
                    target.scrollIntoView({ block: 'center', inline: 'center' });
                    for (const type of ['pointerdown', 'mousedown', 'mouseup', 'click']) {
                        target.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
                    }
                    if (target !== el) {
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                    }
                    return target;
                }
                function questionNumberFor(el) {
                    const text = textOf(el.closest('.question, .adapt-question-wrap, .js-answer-item, .answer-item, li, div') || el);
                    const match = text.match(/(?:^|\\s)(\\d+)[.、．]/) || text.match(/第\\s*(\\d+)\\s*题/);
                    return match ? `question-${match[1]}` : '';
                }
                function groupOf(el) {
                    return el.getAttribute('name')
                        || el.getAttribute('questionid')
                        || el.getAttribute('data-attributeid')
                        || el.closest('[questionid],[data-attributeid]')?.getAttribute('questionid')
                        || el.closest('[questionid],[data-attributeid]')?.getAttribute('data-attributeid')
                        || questionNumberFor(el)
                        || textOf(el).slice(0, 12);
                }
                const customQuestionNodes = Array.from(document.querySelectorAll(
                    '.adapt-question-wrap input, .adapt-question-wrap label, .adapt-question-wrap .js-answer-item, .adapt-question-wrap .answer-item, [class*=adapt] input, [class*=question] input'
                ));
                const nodes = customQuestionNodes.length
                    ? customQuestionNodes
                    : Array.from(document.querySelectorAll(
                        'a, input[type=radio], input[type=checkbox], input[type=button], button, [role=button], label, .insure-label'
                    ));
                const ranked = nodes
                    .map((el, index) => {
                        const text = textOf(el);
                        const lower = text.toLowerCase();
                        if (!text) return null;
                        if (reject.some(token => text.includes(token))) return null;
                        if (['下一步', '下一页', '继续', '提交', '完成'].some(token => text.includes(token))) return null;
                        let score = 0;
                        for (const token of prefer) {
                            if (text.includes(token)) score += 100;
                        }
                        if (lower === 'a' || lower.startsWith('a.')) score += 30;
                        if (visible(el) || customQuestionNodes.length) score += 10;
                        return { el, index, text, score, group: groupOf(el) };
                    })
                    .filter(Boolean)
                    .filter(item => item.score > 0)
                    .sort((left, right) => right.score - left.score || left.index - right.index);
                for (const item of ranked) {
                    if (clicked.length >= 24) break;
                    if (item.group && seenGroups.has(item.group)) continue;
                    try {
                        const clickedElement = clickLikeUser(item.el);
                        seenGroups.add(item.group);
                        clicked.push({
                            text: item.text.slice(0, 80),
                            selector: cssSelector(clickedElement),
                            click_strategy: customQuestionNodes.length ? 'js-custom-questionnaire' : 'js-minimal-data',
                        });
                    } catch (_) {}
                }
                return clicked;
            }"""
        )
    except Exception:
        return []
    await page.wait_for_timeout(_POST_CLICK_SETTLE_MS)
    return [
        {
            "text": str(item.get("text") or ""),
            "tag": "option",
            "selector": item.get("selector"),
            "source_url": page.url,
            "target_url": page.url,
            "score": None,
            "click_strategy": item.get("click_strategy") or "js-minimal-data",
            "dismissed_overlays": [],
            "action_type": "minimal_data",
        }
        for item in selected or []
    ]


async def _request_sms_verification_code(page: Any) -> list[dict[str, Any]]:
    if not hasattr(page, "locator"):
        return []
    button_query = "button, a, [role='button'], .btn, [class*='btn']"
    locators = []
    for text in ("获取验证码", "发送验证码", "获取短信验证码", "发送认证短信"):
        try:
            locator = page.locator(button_query).filter(has_text=text)
            try:
                count = min(await locator.count(), 10)
                locators.extend(locator.nth(index) for index in range(count))
            except Exception:
                locators.append(locator.first)
        except Exception:
            continue
    for locator in locators:
        try:
            if not await locator.is_visible(timeout=1_000):
                continue
            before_url = page.url
            await locator.click(timeout=3_000, no_wait_after=True)
            await page.wait_for_timeout(1_000)
            return [
                {
                    "text": "获取验证码",
                    "tag": "button",
                    "selector": None,
                    "source_url": before_url,
                    "target_url": page.url,
                    "score": None,
                    "click_strategy": "sms-code-request",
                    "dismissed_overlays": [],
                    "action_type": "minimal_data",
                }
            ]
        except Exception:
            continue
    try:
        clicked = await page.evaluate(
            """() => {
                function textOf(el) {
                    return String(el.innerText || el.textContent || el.value || el.getAttribute('aria-label') || '').trim();
                }
                function visible(el) {
                    return !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                }
                function clickLikeUser(el) {
                    el.scrollIntoView({ block: 'center', inline: 'center' });
                    for (const type of ['pointerdown', 'mousedown', 'mouseup', 'click']) {
                        el.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
                    }
                }
                const candidates = Array.from(document.querySelectorAll('a, button, [role=button], .btn, [class*=btn], div, span'))
                    .map((el, index) => ({ el, index, text: textOf(el) }))
                    .filter(item => /获取验证码|发送验证码|获取短信验证码|发送认证短信/.test(item.text));
                const chosen = candidates.find(item => visible(item.el)) || candidates[0];
                if (!chosen) return null;
                clickLikeUser(chosen.el);
                return { text: chosen.text.slice(0, 80) || '获取验证码' };
            }"""
        )
        if clicked:
            await page.wait_for_timeout(1_000)
            return [
                {
                    "text": str(clicked.get("text") or "获取验证码"),
                    "tag": "button",
                    "selector": None,
                    "source_url": page.url,
                    "target_url": page.url,
                    "score": None,
                    "click_strategy": "sms-code-request",
                    "dismissed_overlays": [],
                    "action_type": "minimal_data",
                }
            ]
    except Exception:
        pass
    return []


async def _force_fill_sms_captcha(page: Any) -> list[dict[str, Any]]:
    native_records: list[dict[str, Any]] = []
    if hasattr(page, "locator"):
        try:
            controls = page.locator("input, textarea")
            count = min(await controls.count(), 30)
            for index in range(count):
                item = controls.nth(index)
                try:
                    if not await item.is_visible(timeout=400):
                        continue
                    input_type = (await item.get_attribute("type", timeout=400) or "").lower()
                    if input_type in {"hidden", "button", "submit", "reset", "file", "radio", "checkbox"}:
                        continue
                    probe_parts = [
                        await item.get_attribute("name", timeout=400) or "",
                        await item.get_attribute("id", timeout=400) or "",
                        await item.get_attribute("placeholder", timeout=400) or "",
                        await item.get_attribute("aria-label", timeout=400) or "",
                    ]
                    try:
                        row_text = await item.locator(
                            "xpath=ancestor::*[contains(@class,'am-list-item') or contains(@class,'am-input') or contains(@class,'form') or contains(@class,'item')][1]"
                        ).inner_text(timeout=400)
                    except Exception:
                        row_text = ""
                    probe = " ".join(part for part in [*probe_parts, row_text] if part)
                    if not re.search(r"验证码|短信|verifyCode|captcha|sms|4位", probe, re.I):
                        continue
                    await item.fill("1111", timeout=1_500)
                    try:
                        await item.dispatch_event("blur")
                    except Exception:
                        pass
                    native_records.append(
                        {
                            "text": f"{probe[:60] or '验证码'}=1111",
                            "tag": "field",
                            "selector": "input, textarea",
                            "source_url": page.url,
                            "target_url": page.url,
                            "score": None,
                            "click_strategy": "playwright-sms-captcha-fill",
                            "dismissed_overlays": [],
                            "action_type": "minimal_data",
                        }
                    )
                except Exception:
                    continue
        except Exception:
            native_records = []
        if native_records:
            return native_records
    try:
        filled = await page.evaluate(
            """() => {
                const records = [];
                function labelOf(el) {
                    return [
                        el.name, el.id, el.placeholder, el.getAttribute('aria-label'),
                        el.closest('label')?.innerText,
                        el.closest('.form-item,.form-group,li,dd,div')?.innerText,
                    ].filter(Boolean).join(' ');
                }
                function cssSelector(el) {
                    if (el.id) return `#${CSS.escape(el.id)}`;
                    if (el.name) return `${el.tagName.toLowerCase()}[name="${CSS.escape(el.name)}"]`;
                    const cls = String(el.className || '').split(/\\s+/).filter(Boolean)[0];
                    return cls ? `${el.tagName.toLowerCase()}.${CSS.escape(cls)}` : el.tagName.toLowerCase();
                }
                const captchaInputs = Array.from(document.querySelectorAll('input, textarea')).filter(el => {
                    if (el.disabled || el.readOnly) return false;
                    const type = String(el.type || '').toLowerCase();
                    if (['hidden', 'button', 'submit', 'reset', 'file'].includes(type)) return false;
                    return /验证码|verifyCode|captcha|sms/i.test(labelOf(el));
                });
                for (const el of captchaInputs) {
                    el.value = '1111';
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    el.dispatchEvent(new Event('blur', { bubbles: true }));
                    records.push({ text: `${labelOf(el) || '验证码'}=1111`.slice(0, 80), selector: cssSelector(el) });
                }
                return records;
            }"""
        )
    except Exception:
        return []
    return [
        {
            "text": str(item.get("text") or ""),
            "tag": "field",
            "selector": item.get("selector"),
            "source_url": page.url,
            "target_url": page.url,
            "score": None,
            "click_strategy": "sms-captcha-refill",
            "dismissed_overlays": [],
            "action_type": "minimal_data",
        }
        for item in filled or []
    ]


def _has_pay_account_fill_record(records: Any) -> bool:
    if not isinstance(records, list):
        return False
    return any(
        any(token in str((item or {}).get("text") or "") for token in ("银行账号", "银行卡号", "银行账户"))
        for item in records
        if isinstance(item, dict)
    )


async def _mark_pay_account_skip_writes(page: Any) -> None:
    try:
        await page.evaluate(
            """() => {
                window.__agent3SkipPayAccountWrites = true;
            }"""
        )
    except Exception:
        pass


async def _sync_region_mock_data_to_page(page: Any) -> list[dict[str, Any]]:
    region_value = "110000-110105"
    region_text = "北京市-朝阳区"
    try:
        result = await page.evaluate(
            """({ regionValue, regionText }) => {
                let changed = 0;
                const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                const patchRows = root => {
                    if (!root || typeof root !== 'object') return 0;
                    let count = 0;
                    const fieldValue = () => ({
                        display: true,
                        needValid: true,
                        defaultRemind: '',
                        regex: '',
                        errorRemind: '请重新选择省市区',
                        hasError: false,
                        value: regionValue,
                        required: 1,
                        label: regionText,
                        text: regionText,
                        error: false,
                        errorMsg: ''
                    });
                    for (const moduleId of ['10', '20']) {
                        const rows = root[moduleId] || root[Number(moduleId)];
                        const row = Array.isArray(rows) ? rows[0] : rows;
                        if (!row || typeof row !== 'object') continue;
                        if (row.provCityText && typeof row.provCityText === 'object') {
                            row.provCityText.value = regionValue;
                            row.provCityText.label = regionText;
                            row.provCityText.text = regionText;
                            row.provCityText.hasError = false;
                            row.provCityText.error = false;
                            row.provCityText.errorMsg = '';
                            count += 1;
                        } else if ('provCityText' in row) {
                            row.provCityText = fieldValue();
                            count += 1;
                        }
                        for (const key of ['provCityTextText', 'provCityTextName', 'provCityName', 'areaText']) {
                            if (key in row && row[key] !== regionText) {
                                row[key] = regionText;
                                count += 1;
                            }
                        }
                    }
                    return count;
                };
                const patchPlain = obj => {
                    if (!obj || typeof obj !== 'object') return 0;
                    let count = 0;
                    for (const root of [
                        obj.product?.insure?.data?.data,
                        obj.insure?.data?.data,
                        obj.offline?.insure?.data?.data,
                        obj.data?.data,
                        obj.data,
                        obj.props?.pageProps?.initialReduxState?.product?.insure?.data?.data,
                        obj.props?.pageProps?.initialReduxState?.offline?.insure?.data?.data,
                    ]) {
                        count += patchRows(root);
                    }
                    return count;
                };
                changed += patchPlain(window.__NEXT_DATA__);
                for (const storage of [window.localStorage, window.sessionStorage]) {
                    if (!storage) continue;
                    for (let i = 0; i < storage.length; i += 1) {
                        const key = storage.key(i);
                        if (!key || !/insure|product/i.test(key)) continue;
                        try {
                            const raw = storage.getItem(key);
                            if (!raw || raw[0] !== '{') continue;
                            const json = JSON.parse(raw);
                            const storageChanged = patchPlain(json);
                            if (storageChanged) {
                                storage.setItem(key, JSON.stringify(json));
                                changed += storageChanged;
                            }
                        } catch (_) {}
                    }
                }
                const store = window.__NEXT_REDUX_STORE__ || window.store || window.reduxStore;
                if (store && typeof store.getState === 'function') {
                    try {
                        const state = store.getState();
                        let next = state;
                        if (next && typeof next.setIn === 'function') {
                            for (const base of [
                                ['product', 'insure', 'data', 'data'],
                                ['insure', 'data', 'data'],
                                ['offline', 'insure', 'data', 'data'],
                                ['data', 'data'],
                                ['data'],
                            ]) {
                                for (const moduleId of ['10', '20']) {
                                    const path = [...base, moduleId, 0, 'provCityText'];
                                    const current = typeof next.getIn === 'function' ? next.getIn(path) : undefined;
                                    if (current && typeof current === 'object') {
                                        next = next.setIn([...path, 'value'], regionValue);
                                        next = next.setIn([...path, 'label'], regionText);
                                        next = next.setIn([...path, 'text'], regionText);
                                        next = next.setIn([...path, 'hasError'], false);
                                        next = next.setIn([...path, 'error'], false);
                                        next = next.setIn([...path, 'errorMsg'], '');
                                    } else {
                                        next = next.setIn(path, {
                                            display: true,
                                            needValid: true,
                                            defaultRemind: '',
                                            regex: '',
                                            errorRemind: '请重新选择省市区',
                                            hasError: false,
                                            value: regionValue,
                                            required: 1,
                                            label: regionText,
                                            text: regionText,
                                            error: false,
                                            errorMsg: ''
                                        });
                                    }
                                    changed += 1;
                                }
                            }
                        } else {
                            changed += patchPlain(next);
                        }
                        if (next !== state) store.getState = () => next;
                    } catch (_) {}
                }
                for (const row of Array.from(document.querySelectorAll('.am-list-item,.insure-filed-wrapper,li,dd,div')).filter(visible)) {
                    const text = row.innerText || row.textContent || '';
                    if (!/居住省市|省市区|省市/.test(text) || !/请选择|请重新选择/.test(text)) continue;
                    const extra = row.querySelector('.am-list-extra,.adm-list-item-extra,[class*="extra"]');
                    if (extra) {
                        extra.textContent = regionText;
                        row.classList.add('selected');
                        changed += 1;
                    }
                }
                return { changed, regionValue, regionText };
            }""",
            {"regionValue": region_value, "regionText": region_text},
        )
    except Exception:
        return []
    if not isinstance(result, dict):
        return []
    return [
        {
            "text": f"居住省市状态同步 {region_value}/{region_text}",
            "tag": "field",
            "selector": f"region-repair-{result.get('changed')}",
            "source_url": page.url,
            "target_url": page.url,
            "score": None,
            "click_strategy": "region-state-repair",
            "dismissed_overlays": [],
            "action_type": "minimal_data",
        }
    ]


async def _sync_bank_mock_data_from_page(page: Any, mock_data: dict[str, Any]) -> list[dict[str, Any]]:
    if (
        mock_data.get("policy_tool.source_path")
        and mock_data.get("bankName_107")
        and mock_data.get("payAccount_107")
    ):
        try:
            await page.evaluate("(data) => { window.__agent3MockData = data; }", mock_data)
        except Exception:
            pass
        return [
            {
                "text": "bank mock source=builtin-fallback",
                "tag": "field",
                "selector": "policy-tool-bank-mock",
                "source_url": page.url,
                "target_url": page.url,
                "score": None,
                "click_strategy": "bank-mock-from-policy-tool",
                "dismissed_overlays": [],
                "action_type": "minimal_data",
            }
        ]
    try:
        snapshot = await page.evaluate(
            """() => {
                const norm = text => String(text || '').replace(/\\s+/g, '').trim();
                const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                const bankLabelRe = /\\u5f00\\u6237\\u94f6\\u884c|\\u5f00\\u6237\\u884c|\\u94f6\\u884c/;
                const accountLabelRe = /payAccount|bankAccount|\\u94f6\\u884c\\u8d26\\u53f7|\\u94f6\\u884c\\u5361\\u53f7|\\u94f6\\u884c\\u8d26\\u6237|\\u5f00\\u5361\\u4fe1\\u606f|\\u50a8\\u84c4\\u5361|\\u8d26\\u53f7/i;
                const rejectAccountRe = /\\u8bc1\\u4ef6\\u53f7\\u7801|\\u8eab\\u4efd\\u8bc1|\\u624b\\u673a\\u53f7|\\u9a8c\\u8bc1\\u7801|\\u77ed\\u4fe1/i;

                const findBankNameForValue = (root, value) => {
                    if (!value) return '';
                    const wanted = String(value);
                    const stack = [root];
                    const seen = new Set();
                    while (stack.length) {
                        const node = stack.pop();
                        if (!node || typeof node !== 'object' || seen.has(node)) continue;
                        seen.add(node);
                        if (Array.isArray(node)) {
                            if (node.some(item => item && typeof item === 'object' && 'controlValue' in item && 'value' in item)) {
                                const found = node.find(item => String(item.controlValue) === wanted);
                                if (found) return String(found.value || '');
                            }
                            for (const item of node) stack.push(item);
                        } else {
                            if (Array.isArray(node.values)) {
                                const found = node.values.find(item => String(item?.controlValue) === wanted);
                                if (found) return String(found.value || '');
                            }
                            for (const value of Object.values(node)) stack.push(value);
                        }
                    }
                    return '';
                };
                const findBankValueForName = (root, bankName) => {
                    if (!bankName) return '';
                    const wanted = norm(bankName);
                    const stack = [root];
                    const seen = new Set();
                    while (stack.length) {
                        const node = stack.pop();
                        if (!node || typeof node !== 'object' || seen.has(node)) continue;
                        seen.add(node);
                        if (Array.isArray(node)) {
                            if (node.some(item => item && typeof item === 'object' && 'controlValue' in item && 'value' in item)) {
                                const found = node.find(item => norm(item.value) === wanted);
                                if (found) return String(found.controlValue || '');
                            }
                            for (const item of node) stack.push(item);
                        } else {
                            if (Array.isArray(node.values)) {
                                const found = node.values.find(item => norm(item?.value) === wanted);
                                if (found) return String(found.controlValue || '');
                            }
                            for (const value of Object.values(node)) stack.push(value);
                        }
                    }
                    return '';
                };
                const pickPlainBank = obj => {
                    const roots = [
                        obj?.product?.insure?.data?.data,
                        obj?.insure?.data?.data,
                        obj?.data?.data,
                        obj?.data,
                    ].filter(Boolean);
                    for (const root of roots) {
                        const rows = root['107'] || root[107];
                        const row = Array.isArray(rows) ? rows[0] : rows;
                        if (!row || typeof row !== 'object') continue;
                        const bank = row.bank || {};
                        const payAccount = row.payAccount || {};
                        const bankValue = String(bank.value || bank.controlValue || '');
                        const bankName = String(bank.label || bank.text || bank.name || findBankNameForValue(window.__NEXT_DATA__, bankValue) || '');
                        const account = String(payAccount.value || '');
                        if (bankName || bankValue || account) return { bankName, bankValue, payAccount: account };
                    }
                    return {};
                };
                const visibleBankName = () => {
                    const rows = Array.from(document.querySelectorAll('.am-list-item,.insure-filed-wrapper,li,dd,div'))
                        .filter(visible)
                        .map((el, index) => {
                            const text = norm(el.innerText || el.textContent);
                            if (!bankLabelRe.test(text) || text.length > 80) return null;
                            const extra = norm(el.querySelector('.am-list-extra,.adm-list-item-extra,[class*="extra"]')?.innerText || '');
                            const cleaned = extra || text.replace(bankLabelRe, '');
                            if (!cleaned || /\\u8bf7\\u9009\\u62e9|\\u622a\\u6b62\\u65e5\\u671f/.test(cleaned)) return null;
                            return { index, value: cleaned };
                        })
                        .filter(Boolean)
                        .sort((a, b) => a.index - b.index);
                    return rows[0]?.value || '';
                };
                const visiblePayAccount = () => {
                    const candidates = Array.from(document.querySelectorAll('input,textarea'))
                        .filter(visible)
                        .map((el, index) => {
                            const row = el.closest('.am-list-item,.am-list-line,.insure-filed-wrapper,li,dd,label,div');
                            const probe = `${el.name || ''} ${el.id || ''} ${el.placeholder || ''} ${el.getAttribute('aria-label') || ''} ${row?.innerText || ''}`;
                            if (!accountLabelRe.test(probe) || rejectAccountRe.test(probe)) return null;
                            const value = String(el.value || '').replace(/\\s+/g, '');
                            if (!/^\\d{10,30}$/.test(value)) return null;
                            return { index, value };
                        })
                        .filter(Boolean)
                        .sort((a, b) => a.index - b.index);
                    return candidates[0]?.value || '';
                };
                const reduxBank = (() => {
                    const store = window.__NEXT_REDUX_STORE__ || window.store || window.reduxStore;
                    if (!store || typeof store.getState !== 'function') return {};
                    try {
                        const raw = store.getState();
                        return pickPlainBank(raw && typeof raw.toJS === 'function' ? raw.toJS() : raw);
                    } catch (_) {
                        return {};
                    }
                })();
                const nextBank = pickPlainBank(window.__NEXT_DATA__);
                const bankName = visibleBankName() || reduxBank.bankName || nextBank.bankName || '';
                const bankValue = findBankValueForName(window.__NEXT_DATA__, bankName)
                    || reduxBank.bankValue
                    || nextBank.bankValue
                    || '';
                const payAccount = visiblePayAccount() || reduxBank.payAccount || nextBank.payAccount || '';
                return { bankName, bankValue, payAccount };
            }"""
        )
    except Exception:
        return []
    if not isinstance(snapshot, dict):
        return []
    bank_name = str(snapshot.get("bankName") or "").strip()
    bank_value = str(snapshot.get("bankValue") or "").strip()
    pay_account = str(snapshot.get("payAccount") or "").replace(" ", "").strip()
    changed: list[str] = []
    if bank_name:
        mock_data["bankName_107"] = bank_name
        mock_data["bankCode_107"] = bank_name
        mock_data["openBank_107"] = bank_name
        changed.append(f"bank={bank_name}")
    if bank_value:
        mock_data["bankValue_107"] = bank_value
        changed.append(f"bankValue={bank_value}")
    if re.fullmatch(r"\d{10,30}", pay_account):
        mock_data["payAccount_107"] = pay_account
        changed.append(f"payAccount={pay_account}")
    if bank_name or bank_value or pay_account:
        mock_data["bankAccountPair_107"] = "|".join(
            [
                str(mock_data.get("bankName_107") or ""),
                str(mock_data.get("bankValue_107") or ""),
                str(mock_data.get("payAccount_107") or ""),
            ]
        )
    if not changed:
        return []
    try:
        await page.evaluate("(data) => { window.__agent3MockData = data; }", mock_data)
    except Exception:
        pass
    return [
        {
            "text": f"银行mock来源=页面:{','.join(changed)}",
            "tag": "field",
            "selector": "page-bank-mock",
            "source_url": page.url,
            "target_url": page.url,
            "score": None,
            "click_strategy": "bank-mock-from-page",
            "dismissed_overlays": [],
            "action_type": "minimal_data",
        }
    ]


async def _apply_pay_account_user_like_once(page: Any, mock_data: dict[str, Any]) -> list[dict[str, Any]]:
    value = str(mock_data.get("payAccount_107") or "6200588435998028938").replace(" ", "")
    if not re.fullmatch(r"\d{10,30}", value):
        value = "6200588435998028938"
    try:
        target = await page.evaluate(
            """(value) => {
                const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                const norm = text => String(text || '').replace(/\\s+/g, '').trim();
                const all = Array.from(document.querySelectorAll('input,textarea'));
                const candidates = all
                    .map((el, index) => {
                        const type = String(el.type || '').toLowerCase();
                        if (!visible(el) || el.disabled || ['hidden', 'button', 'submit', 'reset', 'file', 'radio', 'checkbox'].includes(type)) return null;
                        const row = el.closest('.am-list-item,.am-list-line,.insure-filed-wrapper,li,dd,label,div');
                        const probe = `${el.name || ''} ${el.id || ''} ${el.placeholder || ''} ${el.getAttribute('aria-label') || ''} ${row?.innerText || ''}`;
                        const matched = /payAccount|bankAccount|银行账号|银行卡号|银行账户|开卡信息|储蓄卡|账号/i.test(probe)
                            && !/证件号码|身份证|手机号码|手机号|验证码|短信/i.test(probe);
                        if (!matched) return null;
                        const rect = el.getBoundingClientRect();
                        let score = 0;
                        if (/银行账号|银行卡号|银行账户/.test(probe)) score += 300;
                        if (/开卡信息|储蓄卡|账号/.test(probe)) score += 200;
                        score += Math.max(0, 1000 - Math.abs(rect.top - window.innerHeight / 2));
                        return { index, current: String(el.value || '').replace(/\\s+/g, ''), label: norm(row?.innerText || el.placeholder || '银行账号'), score };
                    })
                    .filter(Boolean)
                    .sort((a, b) => b.score - a.score);
                const chosen = candidates[0] || null;
                if (chosen && chosen.current === String(value)) {
                    const el = all[chosen.index];
                    el.dataset.agent3PayAccountLocked = '1';
                    window.__agent3PayAccountLocked = true;
                    window.__agent3PayAccountValue = String(value);
                    window.__agent3SkipPayAccountWrites = true;
                    chosen.already = true;
                }
                return chosen;
            }""",
            value,
        )
    except Exception:
        return []
    if not isinstance(target, dict) or target.get("index") is None:
        return []
    label = str(target.get("label") or "银行账号")[:60]
    already = bool(target.get("already"))
    try:
        locator = page.locator("input, textarea").nth(int(target["index"]))
        if not already:
            await locator.scroll_into_view_if_needed(timeout=3_000)
            await locator.click(timeout=3_000)
            await locator.fill("", timeout=3_000)
            await locator.type(value, delay=35, timeout=8_000)
            await locator.press("Tab", timeout=3_000)
            await page.wait_for_timeout(1_200)
        await page.evaluate(
            """(value) => {
                window.__agent3PayAccountLocked = true;
                window.__agent3SkipPayAccountWrites = true;
                window.__agent3PayAccountValue = String(value);
                const clearErrorObject = value => ({
                    value,
                    hasError: false,
                    hasAjaxError: false,
                    error: false,
                    errorMsg: '',
                    msg: '',
                    ajaxError: '',
                    validateStatus: 'success',
                    validStatus: true,
                });
                const patchPlain = obj => {
                    const roots = [
                        obj?.product?.insure?.data?.data,
                        obj?.insure?.data?.data,
                        obj?.data?.data,
                        obj?.data,
                    ].filter(Boolean);
                    for (const root of roots) {
                        const rows = root['107'] || root[107];
                        if (!Array.isArray(rows) || !rows[0] || typeof rows[0] !== 'object') continue;
                        const row = rows[0];
                        row.payAccount = { ...(row.payAccount || {}), ...clearErrorObject(value) };
                    }
                };
                patchPlain(window.__NEXT_DATA__);
                const store = window.__NEXT_REDUX_STORE__ || window.store || window.reduxStore;
                if (store && typeof store.getState === 'function') {
                    try {
                        const state = store.getState();
                        let next = state;
                        if (next && typeof next.setIn === 'function') {
                            const moduleHasRows = path => {
                                const rows = next.getIn?.(path);
                                if (!rows) return false;
                                if (Array.isArray(rows)) return rows.length > 0;
                                if (typeof rows.size === 'number' && typeof rows.get === 'function') return rows.size > 0;
                                return false;
                            };
                            for (const base of [
                                ['product', 'insure', 'data', 'data', '107', 0],
                                ['insure', 'data', 'data', '107', 0],
                                ['data', 'data', '107', 0],
                                ['data', '107', 0],
                            ]) {
                                if (!moduleHasRows(base.slice(0, -1))) continue;
                                next = next.setIn([...base, 'payAccount', 'value'], value);
                                next = next.setIn([...base, 'payAccount', 'hasError'], false);
                                next = next.setIn([...base, 'payAccount', 'hasAjaxError'], false);
                                next = next.setIn([...base, 'payAccount', 'errorMsg'], '');
                                next = next.setIn([...base, 'payAccount', 'msg'], '');
                                next = next.setIn([...base, 'payAccount', 'ajaxError'], '');
                            }
                            if (next !== state) store.getState = () => next;
                        }
                    } catch (_) {}
                }
            }""",
            value,
        )
    except Exception:
        return []
    return [
        {
            "text": f"银行账号={'已存在' if already else '人工式输入'}={value}",
            "tag": "field",
            "selector": label,
            "source_url": page.url,
            "target_url": page.url,
            "score": None,
            "click_strategy": "pay-account-user-like-once",
            "dismissed_overlays": [],
            "action_type": "minimal_data",
        }
    ]


async def _ensure_visible_pay_account_value(page: Any, mock_data: dict[str, Any]) -> list[dict[str, Any]]:
    value = str(mock_data.get("payAccount_107") or "").replace(" ", "").strip()
    if not re.fullmatch(r"\d{10,30}", value):
        return []
    try:
        result = await page.evaluate(
            """(value) => {
                const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                const isPayAccountField = el => {
                    const rowText = el.closest('.am-list-item,.am-list-line,.insure-filed-wrapper,li,dd,label,div')?.innerText || '';
                    const probe = `${el.name || ''} ${el.id || ''} ${el.placeholder || ''} ${el.getAttribute('aria-label') || ''} ${rowText}`;
                    const accountLike = /payAccount|bankAccount|银行账号|银行卡号|银行账户|开卡信息|账号/i.test(probe);
                    const strongAccountHint = /开卡信息|账号|储蓄卡/i.test(probe);
                    const ownerOnly = /账户名|持卡人/i.test(probe) && !/银行账号|银行卡号|银行账户|开卡信息|账号/i.test(probe);
                    const nonAccountIdentityField = /证件号码|身份证/i.test(probe) && !strongAccountHint;
                    return accountLike && !ownerOnly
                        && !nonAccountIdentityField
                        && !/手机号码|手机号|验证码|短信/i.test(probe);
                };
                const setNativeValue = (el, nextValue) => {
                    if (el.disabled) el.removeAttribute('disabled');
                    if (el.readOnly) el.removeAttribute('readonly');
                    const setter = Object.getOwnPropertyDescriptor(el.constructor.prototype, 'value')?.set;
                    if (setter) setter.call(el, nextValue);
                    else el.value = nextValue;
                    el.setAttribute('value', nextValue);
                    for (const type of ['input', 'change', 'compositionend', 'blur']) {
                        el.dispatchEvent(new Event(type, { bubbles: true }));
                    }
                };
                const clearErrorObject = nextValue => ({
                    value: nextValue,
                    hasError: false,
                    hasAjaxError: false,
                    error: false,
                    errorMsg: '',
                    msg: '',
                    ajaxError: '',
                    validateStatus: 'success',
                    validStatus: true,
                });
                const patchPlain = obj => {
                    let changed = 0;
                    const roots = [
                        obj?.product?.insure?.data?.data,
                        obj?.insure?.data?.data,
                        obj?.data?.data,
                        obj?.data,
                    ].filter(Boolean);
                    for (const root of roots) {
                        const rows = root['107'] || root[107];
                        if (!Array.isArray(rows) || !rows[0] || typeof rows[0] !== 'object') continue;
                        rows[0].payAccount = { ...(rows[0].payAccount || {}), ...clearErrorObject(value) };
                        changed += 1;
                    }
                    return changed;
                };
                let domChanged = 0;
                let inputCount = 0;
                for (const el of Array.from(document.querySelectorAll('input,textarea')).filter(visible)) {
                    const type = String(el.type || '').toLowerCase();
                    if (['hidden', 'button', 'submit', 'reset', 'file', 'radio', 'checkbox'].includes(type)) continue;
                    if (!isPayAccountField(el)) continue;
                    const current = String(el.value || '').replace(/\\s+/g, '');
                    if (current !== value) {
                        setNativeValue(el, value);
                        domChanged += 1;
                    }
                    el.dataset.agent3PayAccountLocked = '1';
                    inputCount += 1;
                }
                let stateChanged = patchPlain(window.__NEXT_DATA__);
                const store = window.__NEXT_REDUX_STORE__ || window.store || window.reduxStore;
                if (store && typeof store.getState === 'function') {
                    try {
                        const state = store.getState();
                        let next = state;
                        const moduleHasRows = path => {
                            const rows = next?.getIn?.(path);
                            if (!rows) return false;
                            if (Array.isArray(rows)) return rows.length > 0;
                            if (typeof rows.size === 'number' && typeof rows.get === 'function') return rows.size > 0;
                            return false;
                        };
                        if (next && typeof next.setIn === 'function') {
                            for (const base of [
                                ['product', 'insure', 'data', 'data', '107', 0],
                                ['insure', 'data', 'data', '107', 0],
                                ['data', 'data', '107', 0],
                                ['data', '107', 0],
                            ]) {
                                if (!moduleHasRows(base.slice(0, -1))) continue;
                                next = next.setIn([...base, 'payAccount', 'value'], value);
                                next = next.setIn([...base, 'payAccount', 'hasError'], false);
                                next = next.setIn([...base, 'payAccount', 'hasAjaxError'], false);
                                next = next.setIn([...base, 'payAccount', 'errorMsg'], '');
                                next = next.setIn([...base, 'payAccount', 'msg'], '');
                                next = next.setIn([...base, 'payAccount', 'ajaxError'], '');
                                stateChanged += 1;
                            }
                            if (next !== state) store.getState = () => next;
                        }
                    } catch (_) {}
                }
                window.__agent3PayAccountLocked = inputCount > 0;
                window.__agent3SkipPayAccountWrites = inputCount > 0;
                window.__agent3PayAccountValue = value;
                return { changed: domChanged + stateChanged, domChanged, stateChanged, inputCount, value };
            }""",
            value,
        )
    except Exception:
        return []
    if not isinstance(result, dict) or int(result.get("inputCount") or 0) <= 0:
        return []
    if not int(result.get("domChanged") or 0) and int(result.get("stateChanged") or 0) > 0:
        return []
    return [
        {
            "text": f"银行账号提交前补写={value}",
            "tag": "field",
            "selector": f"visible-pay-account-{result.get('changed')}",
            "source_url": page.url,
            "target_url": page.url,
            "score": None,
            "click_strategy": "pay-account-final-visible-repair",
            "dismissed_overlays": [],
            "action_type": "minimal_data",
        }
    ]


async def _repair_bank_selection_after_account_input(page: Any, mock_data: dict[str, Any]) -> list[dict[str, Any]]:
    bank_pair = str(mock_data.get("bankAccountPair_107") or "").split("|")
    bank_name = bank_pair[0] if len(bank_pair) > 0 and bank_pair[0] else str(mock_data.get("bankName_107") or mock_data.get("openBank_107") or "")
    bank_value = bank_pair[1] if len(bank_pair) > 1 and bank_pair[1] else str(mock_data.get("bankValue_107") or "")
    pay_account = bank_pair[2] if len(bank_pair) > 2 and bank_pair[2] else str(mock_data.get("payAccount_107") or "")
    card_owner = str(mock_data.get("cardOwner_107") or mock_data.get("applicant.name") or "")
    pay_account = pay_account.replace(" ", "")
    if not bank_name or not bank_value or not re.fullmatch(r"\d{10,30}", pay_account):
        return []
    try:
        result = await page.evaluate(
            """(payload) => {
                const { bankName, bankValue, payAccount, cardOwner } = payload;
                const clearField = (field = {}, value = '') => ({
                    ...field,
                    value,
                    hasError: false,
                    hasAjaxError: false,
                    error: false,
                    errorMsg: '',
                    msg: '',
                    ajaxError: '',
                    validateStatus: 'success',
                    validStatus: true,
                });
                const patchRow = row => {
                    if (!row || typeof row !== 'object') return false;
                    row.bank = {
                        ...clearField(row.bank || {}, bankValue),
                        label: bankName,
                        text: bankName,
                        name: bankName,
                        valueText: bankName,
                        controlValue: bankValue,
                    };
                    row.payAccount = clearField(row.payAccount || {}, payAccount);
                    row.cardOwner = clearField(row.cardOwner || {}, cardOwner);
                    return true;
                };
                const patchPlain = obj => {
                    let changed = 0;
                    const roots = [
                        obj?.product?.insure?.data?.data,
                        obj?.insure?.data?.data,
                        obj?.data?.data,
                        obj?.data,
                    ].filter(Boolean);
                    for (const root of roots) {
                        const rows = root['107'] || root[107];
                        if (!Array.isArray(rows) || !rows[0] || typeof rows[0] !== 'object') continue;
                        if (patchRow(rows[0])) changed += 1;
                    }
                    return changed;
                };
                let changed = patchPlain(window.__NEXT_DATA__);
                for (const storage of [window.localStorage, window.sessionStorage]) {
                    if (!storage) continue;
                    for (let i = 0; i < storage.length; i += 1) {
                        const key = storage.key(i);
                        if (!key || !/insure|product/i.test(key)) continue;
                        try {
                            const value = storage.getItem(key);
                            if (!value || value[0] !== '{') continue;
                            const json = JSON.parse(value);
                            const storageChanged = patchPlain(json);
                            if (storageChanged) {
                                storage.setItem(key, JSON.stringify(json));
                                changed += storageChanged;
                            }
                        } catch (_) {}
                    }
                }
                const store = window.__NEXT_REDUX_STORE__ || window.store || window.reduxStore;
                if (store && typeof store.getState === 'function') {
                    try {
                        const state = store.getState();
                        let next = state;
                        const moduleHasRows = path => {
                            const rows = next?.getIn?.(path);
                            if (!rows) return false;
                            if (Array.isArray(rows)) return rows.length > 0;
                            if (typeof rows.size === 'number' && typeof rows.get === 'function') return rows.size > 0;
                            return false;
                        };
                        const setIn = (path, value) => {
                            if (next && typeof next.setIn === 'function') {
                                next = next.setIn(path, value);
                                changed += 1;
                            }
                        };
                        for (const base of [
                            ['product', 'insure', 'data', 'data', '107', 0],
                            ['insure', 'data', 'data', '107', 0],
                            ['data', 'data', '107', 0],
                            ['data', '107', 0],
                        ]) {
                            if (!moduleHasRows(base.slice(0, -1))) continue;
                            for (const [key, value] of [
                                ['bank.value', bankValue],
                                ['bank.label', bankName],
                                ['bank.text', bankName],
                                ['bank.name', bankName],
                                ['bank.valueText', bankName],
                                ['bank.controlValue', bankValue],
                                ['bank.hasError', false],
                                ['bank.hasAjaxError', false],
                                ['bank.error', false],
                                ['bank.errorMsg', ''],
                                ['bank.msg', ''],
                                ['bank.ajaxError', ''],
                                ['payAccount.value', payAccount],
                                ['payAccount.hasError', false],
                                ['payAccount.hasAjaxError', false],
                                ['payAccount.error', false],
                                ['payAccount.errorMsg', ''],
                                ['payAccount.msg', ''],
                                ['payAccount.ajaxError', ''],
                                ['cardOwner.value', cardOwner],
                                ['cardOwner.hasError', false],
                                ['cardOwner.hasAjaxError', false],
                                ['cardOwner.errorMsg', ''],
                                ['cardOwner.msg', ''],
                            ]) {
                                const parts = key.split('.');
                                setIn([...base, ...parts], value);
                            }
                        }
                        if (next !== state) store.getState = () => next;
                    } catch (_) {}
                }
                let inputCount = 0;
                for (const el of Array.from(document.querySelectorAll('input,textarea'))) {
                    const row = el.closest('.am-list-item,.am-list-line,.insure-filed-wrapper,li,dd,label,div');
                    const probe = `${el.name || ''} ${el.id || ''} ${el.placeholder || ''} ${el.getAttribute('aria-label') || ''} ${row?.innerText || ''}`;
                    if (!/payAccount|bankAccount|银行账号|银行卡号|银行账户|开卡信息|储蓄卡|账号/i.test(probe)) continue;
                    el.dataset.agent3PayAccountLocked = '1';
                    el.classList.remove('am-input-error', 'error');
                    row?.classList.remove('am-input-error', 'am-list-item-error', 'error');
                    row?.querySelectorAll?.('.am-input-error-extra,.error,.am-list-error-extra').forEach(node => {
                        node.style.display = 'none';
                    });
                    inputCount += 1;
                }
                for (const node of Array.from(document.querySelectorAll('.am-input-error,.am-list-item-error'))) {
                    const text = node.innerText || '';
                    if (/银行账号|银行卡号|开户行|储蓄卡|账号/.test(text)) node.classList.remove('am-input-error', 'am-list-item-error', 'error');
                }
                for (const node of Array.from(document.querySelectorAll('.am-toast,.am-toast-notice,.adm-toast,.am-modal,.am-modal-wrap,[role="dialog"]'))) {
                    if (/开户行识别失败|手动选择开户行/.test(node.innerText || '')) node.style.display = 'none';
                }
                window.__agent3PayAccountLocked = true;
                window.__agent3SkipPayAccountWrites = true;
                window.__agent3PayAccountValue = payAccount;
                window.__agent3BankName = bankName;
                window.__agent3BankValue = bankValue;
                return { changed, inputCount, bankName, bankValue, payAccount };
            }""",
            {
                "bankName": bank_name,
                "bankValue": bank_value,
                "payAccount": pay_account,
                "cardOwner": card_owner,
            },
        )
    except Exception:
        return []
    if not isinstance(result, dict):
        return []
    return [
        {
            "text": f"开户行识别状态同步={bank_name}/{pay_account}",
            "tag": "field",
            "selector": f"bank-repair-{result.get('changed')}-{result.get('inputCount')}",
            "source_url": page.url,
            "target_url": page.url,
            "score": None,
            "click_strategy": "bank-recognition-state-repair",
            "dismissed_overlays": [],
            "action_type": "minimal_data",
        }
    ]


async def _sync_trial_genes_from_mock_data(page: Any, mock_data: dict[str, Any]) -> list[dict[str, Any]]:
    birthdate = str(mock_data.get("applicant.birthdate") or mock_data.get("insured.birthdate") or "").strip()
    id_no = str(mock_data.get("applicant.id_no") or mock_data.get("insure_form.applicantidno") or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", birthdate):
        return []
    sex_text = "男"
    if re.fullmatch(r"\d{17}[\dXx]", id_no):
        try:
            sex_text = "男" if int(id_no[16]) % 2 else "女"
        except Exception:
            sex_text = "男"
    try:
        result = await page.evaluate(
            """({ birthdate, sexText }) => {
                const changed = [];
                const patchTrialGenes = obj => {
                    const genes = Array.isArray(obj?.trialGenes?.genes) ? obj.trialGenes.genes : [];
                    for (const gene of genes) {
                        if (gene?.key === 'insurantDate' && gene.value !== birthdate) {
                            gene.value = birthdate;
                            changed.push('trialGenes.insurantDate');
                        }
                        if (gene?.key === 'sex' && gene.value !== sexText) {
                            gene.value = sexText;
                            changed.push('trialGenes.sex');
                        }
                    }
                };
                const patchTrialData = obj => {
                    if (!obj || typeof obj !== 'object') return;
                    const candidates = [
                        obj.trialAttr?.insurantDate,
                        obj.trialAttr?.sex,
                        ...(Array.isArray(obj.restrictGenes) ? obj.restrictGenes : []),
                        ...(Array.isArray(obj.displayGenes) ? obj.displayGenes : []),
                    ];
                    for (const item of candidates) {
                        if (!item || typeof item !== 'object') continue;
                        if ((item.key === 'insurantDate' || item.geneKey === 'insurantDate') && item.value !== birthdate) {
                            item.value = birthdate;
                            item.defaultValue = birthdate;
                            changed.push('trialData.insurantDate');
                        }
                        if ((item.key === 'sex' || item.geneKey === 'sex') && item.value !== sexText) {
                            item.value = sexText;
                            item.defaultValue = sexText;
                            changed.push('trialData.sex');
                        }
                    }
                };
                const patchPlain = root => {
                    if (!root || typeof root !== 'object') return;
                    for (const obj of [
                        root,
                        root.data,
                        root.product?.insure?.data,
                        root.product?.common?.trialData,
                        root.props?.pageProps,
                        root.props?.pageProps?.initialReduxState?.product?.insure?.data,
                        root.props?.pageProps?.initialReduxState?.product?.common?.trialData,
                    ]) {
                        patchTrialGenes(obj);
                        patchTrialData(obj);
                    }
                };
                patchPlain(window.__NEXT_DATA__);
                for (const storage of [window.localStorage, window.sessionStorage]) {
                    if (!storage) continue;
                    for (let i = 0; i < storage.length; i += 1) {
                        const key = storage.key(i);
                        if (!key || !/insure|trial|product/i.test(key)) continue;
                        try {
                            const raw = storage.getItem(key);
                            if (!raw || raw[0] !== '{') continue;
                            const json = JSON.parse(raw);
                            patchPlain(json);
                            storage.setItem(key, JSON.stringify(json));
                        } catch (_) {}
                    }
                }
                const store = window.__NEXT_REDUX_STORE__ || window.store || window.reduxStore;
                if (store && typeof store.getState === 'function') {
                    try {
                        const state = store.getState();
                        let next = state;
                        if (next && typeof next.setIn === 'function') {
                            for (const base of [['product', 'insure', 'data', 'trialGenes', 'genes'], ['data', 'trialGenes', 'genes']]) {
                                const genes = next.getIn ? next.getIn(base) : null;
                                if (!genes || typeof genes.size !== 'number') continue;
                                for (let i = 0; i < genes.size; i += 1) {
                                    const key = genes.getIn ? genes.getIn([i, 'key']) : '';
                                    if (key === 'insurantDate') {
                                        next = next.setIn([...base, i, 'value'], birthdate);
                                        changed.push('redux.trialGenes.insurantDate');
                                    }
                                    if (key === 'sex') {
                                        next = next.setIn([...base, i, 'value'], sexText);
                                        changed.push('redux.trialGenes.sex');
                                    }
                                }
                            }
                            if (next !== state) store.getState = () => next;
                        }
                    } catch (_) {}
                }
                window.__agent3TrialBirthdate = birthdate;
                window.__agent3TrialSex = sexText;
                return Array.from(new Set(changed));
            }""",
            {"birthdate": birthdate, "sexText": sex_text},
        )
    except Exception:
        return []
    if not result:
        return []
    return [
        {
            "text": f"trialGenes.insurantDate={birthdate},sex={sex_text}",
            "tag": "field",
            "selector": "trial-genes-sync",
            "source_url": page.url,
            "target_url": page.url,
            "score": None,
            "click_strategy": "trial-genes-sync",
            "dismissed_overlays": [],
            "action_type": "minimal_data",
        }
    ]


async def _capture_sensitive_form_state(page: Any) -> list[dict[str, Any]]:
    try:
        state = await page.evaluate(
            """() => {
                const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                const inputValues = Array.from(document.querySelectorAll('input,textarea'))
                    .filter(visible)
                    .map(el => {
                        const row = el.closest('.am-list-item,.am-list-line,.insure-filed-wrapper,li,dd,label,div');
                        const probe = `${el.name || ''} ${el.id || ''} ${el.placeholder || ''} ${row?.innerText || ''}`;
                        if (!/payAccount|bankAccount|银行账号|银行卡号|银行账户|开卡信息|储蓄卡|账号|cardPeriod|证件有效期/i.test(probe)) return null;
                        return { name: el.name || '', placeholder: el.placeholder || '', value: el.value || '', rowText: (row?.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 120) };
                    })
                    .filter(Boolean);
                const pickPlain = obj => {
                    const roots = [
                        obj?.product?.insure?.data?.data,
                        obj?.insure?.data?.data,
                        obj?.data?.data,
                        obj?.data,
                    ].filter(Boolean);
                    for (const root of roots) {
                        const applicant = (Array.isArray(root['10']) ? root['10'][0] : root['10']) || {};
                        const bank = (Array.isArray(root['107']) ? root['107'][0] : root['107']) || {};
                        if (applicant.cardPeriod || applicant.cardPeriodEnd || bank.payAccount || bank.bank) {
                            return {
                                cardPeriod: applicant.cardPeriod || null,
                                cardPeriodEnd: applicant.cardPeriodEnd || null,
                                bank: bank.bank || null,
                                payAccount: bank.payAccount || null,
                            };
                        }
                    }
                    return null;
                };
                let redux = null;
                const store = window.__NEXT_REDUX_STORE__ || window.store || window.reduxStore;
                if (store && typeof store.getState === 'function') {
                    try {
                        const raw = store.getState();
                        const js = raw && typeof raw.toJS === 'function' ? raw.toJS() : raw;
                        redux = pickPlain(js);
                    } catch (_) {}
                }
                return {
                    inputValues,
                    nextData: pickPlain(window.__NEXT_DATA__),
                    redux,
                    bodyHas20410515: (document.body.innerText || '').includes('2041-05-15'),
                };
            }"""
        )
    except Exception:
        return []
    return [
        {
            "text": f"提交前敏感字段状态={json.dumps(state, ensure_ascii=False)[:900]}",
            "tag": "field",
            "selector": "pre-submit-sensitive-state",
            "source_url": page.url,
            "target_url": page.url,
            "score": None,
            "click_strategy": "pre-submit-state-snapshot",
            "dismissed_overlays": [],
            "action_type": "minimal_data",
        }
    ]


async def _apply_minimal_form_data(page: Any) -> list[dict[str, Any]]:
    await _wait_for_page_flow_settled(page, timeout_ms=25_000, min_wait_ms=1_200)
    saw_processing = await _wait_while_processing(page)
    if saw_processing and await _page_has_processing_overlay(page):
        return []
    unfinished_dialog = await _dismiss_unfinished_policy_dialog(page)
    dismissed_overlays = [unfinished_dialog] if unfinished_dialog else []
    defaults_payload = json.dumps(
        [{"pattern": pattern, "value": value} for pattern, value in _MINIMAL_FORM_DEFAULT_RULES],
        ensure_ascii=False,
    )
    job_preferences_payload = json.dumps(_JOB_DROPDOWN_DEFAULTS, ensure_ascii=False)
    mobile_prefixes_payload = json.dumps(_POLICY_INFO_MOBILE_PREFIXES, ensure_ascii=False)
    try:
        mock_data = await page.evaluate("() => window.__agent3MockData || null")
    except Exception:
        mock_data = None
    if not isinstance(mock_data, dict):
        mock_data_path = os.environ.get("AGENT3_MOCK_DATA_PATH", "").strip()
        if mock_data_path:
            try:
                loaded = json.loads(Path(mock_data_path).read_text(encoding="utf-8"))
                mock_data = dict(loaded) if isinstance(loaded, dict) else None
            except Exception:
                mock_data = None
        if not isinstance(mock_data, dict):
            mock_data = generate_policy_mock_data([], seed=time.time_ns())
        try:
            await page.evaluate("(data) => { window.__agent3MockData = data; }", mock_data)
        except Exception:
            pass
    bank_mock_actions = await _sync_bank_mock_data_from_page(page, mock_data)
    mock_data_for_generic = dict(mock_data)
    mock_data_for_generic["__skipPayAccount"] = True
    mock_data_payload = json.dumps(mock_data_for_generic, ensure_ascii=False)
    await _mark_pay_account_skip_writes(page)
    pre_filled = await _apply_placeholder_form_data(page, mock_data_for_generic)
    pay_account_written = _has_pay_account_fill_record(pre_filled)
    if pay_account_written:
        await _mark_pay_account_skip_writes(page)
    try:
        filled = await page.evaluate(
            """async () => {
                const records = [];
                const skipPayAccountWrites = !!window.__agent3SkipPayAccountWrites;
                const defaults = __DEFAULTS__.map(item => ({
                    re: new RegExp(item.pattern, 'i'),
                    value: item.value,
                }));
                const jobPreferences = __JOB_PREFERENCES__;
                const mobilePrefixes = __MOBILE_PREFIXES__;
                const mockData = __MOCK_DATA__;
                let phoneSequence = 0;
                function visible(el) {
                    return !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                }
                function labelOf(el) {
                    return [
                        el.name, el.id, el.placeholder, el.getAttribute('aria-label'),
                        el.closest('label')?.innerText,
                        el.closest('.form-item,.form-group,li,dd,div')?.innerText,
                    ].filter(Boolean).join(' ');
                }
                function cssSelector(el) {
                    if (el.id) return `#${CSS.escape(el.id)}`;
                    if (el.name) return `${el.tagName.toLowerCase()}[name="${CSS.escape(el.name)}"]`;
                    const cls = String(el.className || '').split(/\\s+/).filter(Boolean)[0];
                    return cls ? `${el.tagName.toLowerCase()}.${CSS.escape(cls)}` : el.tagName.toLowerCase();
                }
                function isPayAccountField(el, label = '') {
                    const rowText = el.closest('.am-list-item,.am-list-line,.insure-filed-wrapper,li,dd,label,div')?.innerText || '';
                    const probe = `${el.name || ''} ${el.id || ''} ${el.placeholder || ''} ${el.getAttribute('aria-label') || ''} ${label || ''} ${rowText}`;
                    const accountLike = /payAccount|bankAccount|银行账号|银行卡号|银行账户|开卡信息|账号/i.test(probe);
                    const strongAccountHint = /开卡信息|账号|储蓄卡/i.test(probe);
                    const ownerOnly = /账户名|持卡人/i.test(probe) && !/银行账号|银行卡号|银行账户|开卡信息|账号/i.test(probe);
                    const nonAccountIdentityField = /证件号码|身份证/i.test(probe) && !strongAccountHint;
                    return accountLike && !ownerOnly
                        && !nonAccountIdentityField
                        && !/手机号码|手机号|验证码|短信/i.test(probe);
                }
                function lockPayAccount(el, value = '') {
                    if (!el) return;
                    el.dataset.agent3PayAccountLocked = '1';
                    window.__agent3PayAccountLocked = true;
                    window.__agent3PayAccountValue = String(value || el.value || '');
                }
                function payAccountAlreadyLocked(el, label = '', nextValue = '') {
                    if (!isPayAccountField(el, label)) return false;
                    const current = String(el.value || '').replace(/\\s+/g, '').trim();
                    const expected = String(nextValue || window.__agent3PayAccountValue || '').replace(/\\s+/g, '').trim();
                    if (skipPayAccountWrites) {
                        if (/^\\d{10,30}$/.test(current)) {
                            lockPayAccount(el, current);
                            return true;
                        }
                        return false;
                    }
                    if ((el.dataset.agent3PayAccountLocked === '1' || window.__agent3PayAccountLocked) && /^\\d{10,30}$/.test(current)) return true;
                    if (current && expected && current === expected) {
                        lockPayAccount(el, current);
                        return true;
                    }
                    return false;
                }
                const englishNameFor = prefix => {
                    const configured = mockData[`${prefix}.english_name`] || mockData[`${prefix}.eName`] || mockData[`${prefix}.pinyin`];
                    if (configured && /^[A-Za-z][A-Za-z\\s'-]{1,60}$/.test(String(configured))) return String(configured).replace(/\\s+/g, '').toLowerCase();
                    return prefix === 'insured' ? 'lisi' : 'zhangsan';
                };
                const isEnglishNameField = (el, label = '') => {
                    const probe = `${el?.name || ''} ${el?.id || ''} ${el?.placeholder || ''} ${el?.getAttribute?.('aria-label') || ''} ${label || ''}`;
                    return /eName|english|pinyin|英文|拼音/i.test(probe);
                };
                function valueFor(el, label) {
                    const name = String(el.name || '');
                    const probe = `${name} ${label || ''}`;
                    const insured = /_20|default_1|insured|被保|为谁投保/i.test(probe);
                    const personPrefix = insured ? 'insured' : 'applicant';
                    if (isEnglishNameField(el, label)) return englishNameFor(personPrefix);
                    if (/eName|english|pinyin|英文|拼音/i.test(probe)) return englishNameFor(personPrefix);
                    if (/payAccount|bankAccount|account|银行账号|银行卡号|银行账户|开卡信息|储蓄卡|账号/i.test(probe)) return mockData.payAccount_107 || '6200588435998028938';
                    if (/cardOwner|持卡人/i.test(probe)) return mockData.cardOwner_107 || mockData['applicant.name'] || '张三';
                    if (/bankCode|bankName|openBank|开户银行|银行/i.test(probe)) return mockData.bankName_107 || '中国工商银行';
                    if (/jobText|occupation|职业/i.test(probe)) return mockData[`${personPrefix}.occupation`] || (insured ? '一般学生' : '一般');
                    if (/provCityText|region|居住省市|省市/i.test(probe)) return mockData[`${personPrefix}.region`] || '北京市 北京市 朝阳区';
                    if (/moblie|mobile|phone|手机号|tel/i.test(probe)) return mockData[`${personPrefix}.mobile`] || nextPolicyPhone();
                    if (/cardPeriodEnd.*_20|cardPeriodEnd_20|_20_.*cardPeriodEnd/i.test(name)) return mockData['insured.card_valid_end'] || '2031-01-01';
                    if (/cardPeriodEnd|有效期.*结束|结束/i.test(probe)) return mockData['applicant.card_valid_end'] || '2046-01-01';
                    if (/cardPeriod.*_20|cardPeriod_20|_20_.*cardPeriod/i.test(name)) return mockData['insured.card_valid_start'] || '2026-01-01';
                    if (/cardPeriod|有效期.*开始|开始/i.test(probe)) return mockData['applicant.card_valid_start'] || '2026-01-01';
                    if (/cardNumber.*_20|cardNumber_20|_20_.*cardNumber/i.test(name)) return mockData['insured.id_no'] || '11010120150315123X';
                    if (/cardNumber|证件号码/i.test(probe)) return mockData['applicant.id_no'] || '110101199001011237';
                    if (/birthdate.*_20|birthdate_20|_20_.*birthdate/i.test(name)) return mockData['insured.birthdate'] || '2015-03-15';
                    if (/birthdate|出生日期|生日/i.test(probe)) return mockData[`${personPrefix}.birthdate`] || '1990-01-01';
                    if (/email|邮箱/i.test(probe)) return mockData[`${personPrefix}.email`] || 'test@example.com';
                    if (/address|地址/i.test(probe)) return mockData[`${personPrefix}.address`] || '北京市朝阳区测试地址1号';
                    if (/name|姓名/i.test(probe)) return mockData[`${personPrefix}.name`] || (insured ? '李四' : '张三');
                    const found = defaults.find(item => item.re.test(probe));
                    return found ? found.value : '测试';
                }
                function nextPolicyPhone() {
                    phoneSequence += 1;
                    const seed = Date.now() + phoneSequence;
                    const prefix = mobilePrefixes[seed % mobilePrefixes.length] || '138';
                    return `${prefix}${String(seed % 100000000).padStart(8, '0')}`;
                }
                function clickLikeUser(el) {
                    if (!el) return;
                    if (typeof el.click === 'function') {
                        el.click();
                        return;
                    }
                    for (const type of ['mousedown', 'mouseup', 'click']) {
                        el.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
                    }
                }
                async function chooseDropdown(root, prefer) {
                    if (!root || !visible(root)) return null;
                    const currentText = String(root.innerText || root.textContent || '').replace(/\\s+/g, ' ').trim();
                    if (!/请选择/.test(currentText) && (!prefer || currentText.includes(String(prefer)))) return null;
                    const rootBox = root.getBoundingClientRect();
                    clickLikeUser(root.querySelector('.input-select') || root);
                    await new Promise(resolve => setTimeout(resolve, 250));
                    const scopedOptions = Array.from(root.querySelectorAll('.hz-select-option, [select-value]'));
                    const globalOptions = Array.from(document.querySelectorAll('.hz-select-option, [select-value]'))
                        .filter(item => visible(item))
                        .sort((left, right) => {
                            const a = left.getBoundingClientRect();
                            const b = right.getBoundingClientRect();
                            return Math.abs(a.top - rootBox.bottom) - Math.abs(b.top - rootBox.bottom);
                        });
                    const options = [...scopedOptions, ...globalOptions]
                        .filter(item => visible(item))
                        .filter(item => String(item.getAttribute('select-value') || item.innerText || item.textContent || '').trim())
                        .filter((item, index, list) => list.indexOf(item) === index);
                    const preferred = options.find(item => prefer && (item.innerText || item.textContent || '').includes(prefer));
                    const chosen = preferred || options.find(item => !/请选择/.test(item.innerText || item.textContent || ''));
                    if (!chosen) return null;
                    const text = (chosen.innerText || chosen.textContent || '').trim();
                    clickLikeUser(chosen);
                    await new Promise(resolve => setTimeout(resolve, 700));
                    return { text, selector: cssSelector(root) };
                }
                function controlsByNamePrefix(namePrefix) {
                    return Array.from(document.querySelectorAll(`[name^="${CSS.escape(namePrefix)}"]`))
                        .filter(visible)
                        .sort((left, right) => {
                            const a = left.getBoundingClientRect();
                            const b = right.getBoundingClientRect();
                            return Math.abs(a.top - b.top) > 8 ? a.top - b.top : a.left - b.left;
                        });
                }
                async function selectCascadeByNamePrefix(namePrefix, preferences) {
                    for (let level = 0; level < preferences.length; level += 1) {
                        const controls = controlsByNamePrefix(namePrefix);
                        if (level >= controls.length) break;
                        const preferList = Array.isArray(preferences[level]) ? preferences[level] : [preferences[level]];
                        let selected = null;
                        for (const prefer of preferList.filter(Boolean)) {
                            selected = await chooseDropdown(controls[level], prefer);
                            if (selected) break;
                        }
                        if (!selected) selected = await chooseDropdown(controls[level], null);
                        if (selected) records.push({ text: `${namePrefix}.${level}=${selected.text}`.slice(0, 80), selector: selected.selector });
                    }
                }
                for (const el of Array.from(document.querySelectorAll('input, textarea, select'))) {
                    if (!visible(el) || el.disabled || el.readOnly) continue;
                    const tag = el.tagName.toLowerCase();
                    const type = String(el.type || '').toLowerCase();
                    if (['hidden', 'button', 'submit', 'reset', 'file'].includes(type)) continue;
                    if (tag === 'select') {
                        const option = Array.from(el.options || []).find(item => item.value && !item.disabled);
                        if (!option) continue;
                        el.value = option.value;
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                        records.push({ text: `${labelOf(el)}=${option.text}`.slice(0, 80), selector: cssSelector(el) });
                        continue;
                    }
                    if (['radio', 'checkbox'].includes(type)) continue;
                    if (String(el.name || '').includes('cardPeriodEnd')) continue;
                    const label = labelOf(el);
                    const value = valueFor(el, label);
                    if (payAccountAlreadyLocked(el, label, value)) continue;
                    if (String(el.value || '').trim() && !isPayAccountField(el, label) && !isEnglishNameField(el, label)) continue;
                    el.value = value;
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    el.dispatchEvent(new Event('blur', { bubbles: true }));
                    if (isPayAccountField(el, label)) lockPayAccount(el, value);
                    records.push({ text: `${label || tag}=${value}`.slice(0, 80), selector: cssSelector(el) });
                    if (records.length >= 120) break;
                }
                const captchaInputs = Array.from(document.querySelectorAll('input, textarea')).filter(el => {
                    if (el.disabled || el.readOnly) return false;
                    const type = String(el.type || '').toLowerCase();
                    if (['hidden', 'button', 'submit', 'reset', 'file'].includes(type)) return false;
                    return /验证码|verifyCode|captcha|sms/i.test(labelOf(el));
                });
                for (const el of captchaInputs) {
                    if (String(el.value || '').trim() === '1111') continue;
                    el.value = '1111';
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    el.dispatchEvent(new Event('blur', { bubbles: true }));
                    records.push({ text: `${labelOf(el) || '验证码'}=1111`.slice(0, 80), selector: cssSelector(el) });
                }
                for (const el of Array.from(document.querySelectorAll('input[type="hidden"], input:not([type])'))) {
                    const name = String(el.name || '');
                    if (!/birthdate/i.test(name) || String(el.value || '').trim()) continue;
                    const value = valueFor(el, name);
                    el.value = value;
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    records.push({ text: `${name}=${value}`.slice(0, 80), selector: cssSelector(el) });
                }
                for (const name of ['provCityText_10', 'provCityText_20']) {
                    await selectCascadeByNamePrefix(name, [['北京市', '北京'], ['北京市', '北京'], ['朝阳区', '东城区', '海淀区']]);
                }
                for (const [name, preferences] of Object.entries(jobPreferences)) {
                    const prefix = name.replace(/_default_1$/, '');
                    await selectCascadeByNamePrefix(prefix, [[preferences.job1, '一般'], [preferences.job2, '一般职业人员', '学生'], [preferences.job3, '一般学生', '学生']]);
                }
                const syncEnglishNameState = () => {
                    const applicantEnglish = englishNameFor('applicant');
                    const insuredEnglish = englishNameFor('insured');
                    const clear = value => ({ value, hasError: false, hasAjaxError: false, error: false, errorMsg: '', msg: '', ajaxError: '' });
                    const patchPlain = root => {
                        const data = root?.product?.insure?.data?.data || root?.insure?.data?.data || root?.data?.data || root?.data;
                        if (!data || typeof data !== 'object') return 0;
                        let changed = 0;
                        for (const [key, value] of [['10', applicantEnglish], ['20', insuredEnglish]]) {
                            const rows = data[key] || data[Number(key)];
                            if (!Array.isArray(rows) || !rows[0] || typeof rows[0] !== 'object') continue;
                            rows[0].eName = { ...(rows[0].eName || {}), ...clear(value) };
                            changed += 1;
                        }
                        return changed;
                    };
                    let changed = patchPlain(window.__NEXT_DATA__);
                    const store = window.__NEXT_REDUX_STORE__ || window.store || window.reduxStore;
                    if (store && typeof store.getState === 'function') {
                        try {
                            const state = store.getState();
                            let next = state;
                            if (next && typeof next.setIn === 'function') {
                                const moduleHasRows = path => {
                                    const rows = next.getIn?.(path);
                                    if (!rows) return false;
                                    if (Array.isArray(rows)) return rows.length > 0;
                                    if (typeof rows.size === 'number' && typeof rows.get === 'function') return rows.size > 0;
                                    return false;
                                };
                                for (const [moduleId, value] of [['10', applicantEnglish], ['20', insuredEnglish]]) {
                                    for (const base of [
                                        ['product', 'insure', 'data', 'data', moduleId, 0],
                                        ['insure', 'data', 'data', moduleId, 0],
                                        ['data', 'data', moduleId, 0],
                                        ['data', moduleId, 0],
                                    ]) {
                                        if (!moduleHasRows(base.slice(0, -1))) continue;
                                        next = next.setIn([...base, 'eName', 'value'], value);
                                        next = next.setIn([...base, 'eName', 'hasError'], false);
                                        next = next.setIn([...base, 'eName', 'hasAjaxError'], false);
                                        next = next.setIn([...base, 'eName', 'errorMsg'], '');
                                        next = next.setIn([...base, 'eName', 'msg'], '');
                                        changed += 1;
                                    }
                                }
                                if (next !== state) store.getState = () => next;
                            }
                        } catch (_) {}
                    }
                    if (changed) records.push({ text: `eName=${applicantEnglish}`, selector: 'state-eName' });
                };
                syncEnglishNameState();
                return records;
            }"""
            .replace("__DEFAULTS__", defaults_payload)
            .replace("__JOB_PREFERENCES__", job_preferences_payload)
            .replace("__MOBILE_PREFIXES__", mobile_prefixes_payload)
            .replace("__MOCK_DATA__", mock_data_payload)
        )
    except Exception:
        return []
    if _has_pay_account_fill_record(filled):
        pay_account_written = True
        await _mark_pay_account_skip_writes(page)
    if pay_account_written:
        mock_data_for_generic["__skipPayAccount"] = True
    post_filled = await _apply_placeholder_form_data(page, mock_data_for_generic)
    if _has_pay_account_fill_record(post_filled):
        pay_account_written = True
        await _mark_pay_account_skip_writes(page)
    if bank_mock_actions or pre_filled or post_filled:
        filled = list(bank_mock_actions) + list(pre_filled) + list(filled or []) + list(post_filled)
    if dismissed_overlays:
        filled = list(filled or []) + [
            {
                "text": str(item.get("text") or ""),
                "tag": "overlay",
                "selector": item.get("selector"),
                "source_url": item.get("source_url") or page.url,
                "target_url": item.get("target_url") or page.url,
                "score": None,
                "click_strategy": "pre-fill-overlay-dismiss",
                "dismissed_overlays": [],
                "action_type": "overlay_dismiss",
            }
            for item in dismissed_overlays
        ]
    select_filled = await _apply_h5_select_defaults(page, mock_data)
    if select_filled:
        filled = list(filled or []) + list(select_filled)
    pay_account_filled = await _apply_pay_account_user_like_once(page, mock_data)
    if pay_account_filled:
        filled = list(filled or []) + list(pay_account_filled)
        pay_account_written = True
        await _mark_pay_account_skip_writes(page)
    bank_repair_filled = await _repair_bank_selection_after_account_input(page, mock_data)
    if bank_repair_filled:
        filled = list(filled or []) + list(bank_repair_filled)
    validity_filled = await _apply_id_validity_defaults(page, mock_data)
    if validity_filled:
        filled = list(filled or []) + list(validity_filled)
    if pay_account_written:
        mock_data_for_generic["__skipPayAccount"] = True
    final_filled = await _apply_placeholder_form_data(page, mock_data_for_generic)
    if final_filled:
        filled = list(filled or []) + list(final_filled)
    await page.wait_for_timeout(1_200)
    final_select_filled = await _apply_h5_select_defaults(page, mock_data)
    if final_select_filled:
        filled = list(filled or []) + list(final_select_filled)
    region_repair_filled = await _sync_region_mock_data_to_page(page)
    if region_repair_filled:
        filled = list(filled or []) + list(region_repair_filled)
    final_bank_repair_filled = await _repair_bank_selection_after_account_input(page, mock_data)
    if final_bank_repair_filled:
        filled = list(filled or []) + list(final_bank_repair_filled)
    trial_genes_filled = await _sync_trial_genes_from_mock_data(page, mock_data)
    if trial_genes_filled:
        filled = list(filled or []) + list(trial_genes_filled)
    sensitive_state = await _capture_sensitive_form_state(page)
    if sensitive_state:
        filled = list(filled or []) + list(sensitive_state)
    terminal_select_filled = await _apply_h5_select_defaults(page, mock_data)
    if terminal_select_filled:
        filled = list(filled or []) + list(terminal_select_filled)
    await page.wait_for_timeout(_POST_CLICK_SETTLE_MS)
    actions = [
        {
            "text": str(item.get("text") or ""),
            "tag": "field",
            "selector": item.get("selector"),
            "source_url": page.url,
            "target_url": page.url,
            "score": None,
            "click_strategy": "js-minimal-data",
            "dismissed_overlays": [],
            "action_type": "minimal_data",
        }
        for item in filled or []
    ]
    sms_actions = await _request_sms_verification_code(page)
    if sms_actions:
        actions.extend(sms_actions)
        actions.extend(await _force_fill_sms_captcha(page))
    else:
        actions.extend(await _force_fill_sms_captcha(page))
    post_sms_select_filled = await _apply_h5_select_defaults(page, mock_data)
    if post_sms_select_filled:
        actions.extend(
            {
                "text": str(item.get("text") or ""),
                "tag": "field",
                "selector": item.get("selector"),
                "source_url": page.url,
                "target_url": page.url,
                "score": None,
                "click_strategy": "js-minimal-data",
                "dismissed_overlays": [],
                "action_type": "minimal_data",
            }
            for item in post_sms_select_filled
        )
    return actions


async def _apply_h5_select_defaults(page: Any, mock_data: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        filled = await page.evaluate(
            """async (mockData) => {
                const records = [];
                const skipPayAccountWrites = true;
                const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
                const norm = text => String(text || '').replace(/\\s+/g, '').trim();
                const visible = el => {
                    if (!el) return false;
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const getFiber = node => {
                    if (!node) return null;
                    const key = Object.keys(node).find(k => k.startsWith('__reactFiber$') || k.startsWith('__reactInternalInstance$'));
                    return key ? node[key] : null;
                };
                const reactText = value => {
                    if (value == null || typeof value === 'boolean') return '';
                    if (typeof value === 'string' || typeof value === 'number') return String(value);
                    if (Array.isArray(value)) return value.map(reactText).join('');
                    if (value.props) return reactText(value.props.children);
                    return '';
                };
                const flattenChildren = children => {
                    if (children == null) return [];
                    if (Array.isArray(children)) return children.flatMap(flattenChildren);
                    return [children];
                };
                const fire = el => {
                    if (!el) return;
                    const eventLike = { target: el, currentTarget: el, preventDefault() {}, stopPropagation() {} };
                    const reactKey = Object.keys(el).find(key => key.startsWith('__reactEventHandlers'));
                    const handler = reactKey ? el[reactKey]?.onClick : null;
                    if (typeof handler === 'function') {
                        try {
                            handler(eventLike);
                        } catch (_) {}
                    }
                    let fiber = getFiber(el);
                    let depth = 0;
                    while (fiber && depth < 12) {
                        const props = fiber.memoizedProps || fiber.pendingProps || fiber._currentElement?.props;
                        if (props && typeof props.onClick === 'function') {
                            try {
                                props.onClick(eventLike);
                            } catch (_) {}
                        }
                        fiber = fiber.return || fiber._hostParent || fiber._currentElement?._owner;
                        depth += 1;
                    }
                    for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                        el.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
                    }
                    if (typeof el.click === 'function') el.click();
                };
                const setNativeValue = (el, value) => {
                    if (!el) return false;
                    if (el.disabled) el.removeAttribute('disabled');
                    if (el.readOnly) el.removeAttribute('readonly');
                    const setter = Object.getOwnPropertyDescriptor(el.constructor.prototype, 'value')?.set;
                    if (setter) setter.call(el, value);
                    else el.value = value;
                    el.setAttribute('value', value);
                    for (const type of ['input', 'change', 'compositionend', 'blur']) {
                        el.dispatchEvent(new Event(type, { bubbles: true }));
                    }
                    return true;
                };
                const insidePopup = el => Boolean(el.closest('.am-picker-popup,[role="dialog"],.job-modal,.adm-popup,.am-modal'));
                const rowLooksLikeField = (text, label) => {
                    if (text === label) return true;
                    if (text.startsWith(label) && /请选择|请重新选择|>$/.test(text)) return true;
                    return false;
                };
                const rowLooksLikePattern = (text, regex) => {
                    if (!regex.test(text)) return false;
                    return /请选择|请重新选择|>$/.test(text) || text.length <= 48;
                };
                const fieldRows = label => {
                    const rows = [];
                    const seen = new Set();
                    for (const node of Array.from(document.querySelectorAll('div,li,label,section')).filter(visible)) {
                        const nodeText = norm(node.innerText || node.textContent);
                        if (!rowLooksLikeField(nodeText, label)) continue;
                        if (insidePopup(node)) continue;
                        let row = node;
                        for (let depth = 0; row.parentElement && depth < 8; depth += 1) {
                            const parent = row.parentElement;
                            const text = norm(parent.innerText || parent.textContent);
                            if (!text.includes(label) || text.length > 260) break;
                            row = parent;
                            if (/请选择|请重新选择|展开|>$/.test(text) || parent.querySelector('input,.am-list-extra,.am-list-arrow,[role="button"]')) {
                                break;
                            }
                        }
                        if (!seen.has(row)) {
                            seen.add(row);
                            rows.push(row);
                        }
                    }
                    return rows
                        .filter(row => visible(row))
                        .sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top);
                };
                const fieldRowsMatching = regex => {
                    const rows = [];
                    const seen = new Set();
                    for (const node of Array.from(document.querySelectorAll('div,li,label,section')).filter(visible)) {
                        const nodeText = norm(node.innerText || node.textContent);
                        if (!rowLooksLikePattern(nodeText, regex)) continue;
                        if (insidePopup(node)) continue;
                        let row = node;
                        for (let depth = 0; row.parentElement && depth < 8; depth += 1) {
                            const parent = row.parentElement;
                            const text = norm(parent.innerText || parent.textContent);
                            if (!regex.test(text) || text.length > 260) break;
                            row = parent;
                            if (/请选择|请重新选择|展开|>$/.test(text) || parent.querySelector('input,.am-list-extra,.am-list-arrow,[role="button"]')) {
                                break;
                            }
                        }
                        if (!seen.has(row)) {
                            seen.add(row);
                            rows.push(row);
                        }
                    }
                    return rows
                        .filter(row => visible(row))
                        .sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top);
                };
                const rowNeedsValue = (row, label = '') => {
                    const text = norm(row.innerText || row.textContent);
                    if (label && text === label) return true;
                    return !text || /请选择|请重新选择|请填写|不能为空/.test(text);
                };
                const openRow = async row => {
                    row.scrollIntoView({ block: 'center', inline: 'center' });
                    await sleep(150);
                    const rect = row.getBoundingClientRect();
                    const points = [
                        [rect.right - 22, rect.top + rect.height / 2],
                        [rect.left + rect.width * 0.68, rect.top + rect.height / 2],
                        [rect.left + rect.width / 2, rect.top + rect.height / 2],
                    ];
                    for (const [x, y] of points) {
                        const target = document.elementFromPoint(Math.max(1, x), Math.max(1, y));
                        fire(target instanceof HTMLElement ? target : row);
                        await sleep(350);
                        if (document.querySelector('.am-picker-popup,[role="dialog"],.job-modal,.adm-popup,.am-modal')) return true;
                    }
                    fire(row);
                    await sleep(350);
                    return Boolean(document.querySelector('.am-picker-popup,[role="dialog"],.job-modal,.adm-popup,.am-modal'));
                };
                const clickPickerConfirm = async () => {
                    const candidates = Array.from(document.querySelectorAll('.am-picker-popup-header-right, [role="dialog"] button, .am-modal-button, button, a, span, div'))
                        .filter(visible)
                        .map((el, index) => ({ el, index, text: norm(el.innerText || el.textContent) }))
                        .filter(item => item.text === '确定' || item.text === '完成');
                    const scoped = candidates.find(item => item.el.closest('.am-picker-popup,[role="dialog"],.adm-popup,.am-modal')) || candidates[0];
                    if (!scoped) return false;
                    fire(scoped.el);
                    await sleep(450);
                    return true;
                };
                const setPickerCol = async (colIndex, preferredTexts) => {
                    const texts = preferredTexts.map(text => norm(text)).filter(Boolean);
                    const cols = Array.from(document.querySelectorAll('.am-picker-col')).filter(visible);
                    const col = cols[colIndex] || document.querySelectorAll('.am-picker-col')[colIndex];
                    if (!col) return null;
                    let selectedValue = '';
                    let selectedText = '';
                    let onValueChange = null;
                    const seen = new Set();
                    const roots = [];
                    let root = getFiber(col);
                    while (root && roots.length < 8) {
                        roots.push(root);
                        root = root.return;
                    }
                    const visit = fiber => {
                        if (!fiber || seen.has(fiber) || (selectedValue && onValueChange)) return;
                        seen.add(fiber);
                        const props = fiber.memoizedProps || fiber.pendingProps;
                        if (props) {
                            if (typeof props.onValueChange === 'function') onValueChange = props.onValueChange;
                            for (const child of flattenChildren(props.children)) {
                                const text = norm(reactText(child?.props?.children));
                                if (!text) continue;
                                const exact = texts.find(target => text === target);
                                const fuzzy = texts.find(target => text.includes(target) || target.includes(text));
                                if ((exact || fuzzy) && child?.props?.value != null) {
                                    selectedValue = String(child.props.value);
                                    selectedText = text;
                                    break;
                                }
                            }
                        }
                        visit(fiber.child);
                        visit(fiber.sibling);
                    };
                    for (const candidate of roots) {
                        visit(candidate);
                        if (selectedValue && onValueChange) break;
                    }
                    if (!selectedValue) {
                        const items = Array.from(col.querySelectorAll('.am-picker-col-item')).filter(visible);
                        const target = items.find(item => texts.some(text => norm(item.textContent) === text))
                            || items.find(item => texts.some(text => norm(item.textContent).includes(text) || text.includes(norm(item.textContent))));
                        if (target) {
                            selectedText = norm(target.textContent);
                            let fiber = getFiber(target);
                            while (fiber && !selectedValue) {
                                const props = fiber.memoizedProps || fiber.pendingProps;
                                if (props?.value != null) selectedValue = String(props.value);
                                fiber = fiber.return;
                            }
                        }
                    }
                    if (!onValueChange) {
                        let fiber = getFiber(col);
                        while (fiber && !onValueChange) {
                            const props = fiber.memoizedProps || fiber.pendingProps;
                            if (props && typeof props.onValueChange === 'function') onValueChange = props.onValueChange;
                            fiber = fiber.return;
                        }
                    }
                    if (!selectedValue || !onValueChange) return null;
                    onValueChange(selectedValue);
                    await sleep(420);
                    return selectedText || selectedValue;
                };
                const cascadeValuePath = (items, preferredColumns, depth = 0) => {
                    if (!Array.isArray(items) || depth >= preferredColumns.length) return [];
                    const wants = preferredColumns[depth].map(text => norm(text)).filter(Boolean);
                    let chosen = items.find(item => wants.some(want => norm(item.label).includes(want) || want.includes(norm(item.label))));
                    let nextDepth = depth + 1;
                    if (!chosen && depth + 1 < preferredColumns.length) {
                        const nextWants = preferredColumns[depth + 1].map(text => norm(text)).filter(Boolean);
                        chosen = items.find(item => nextWants.some(want => norm(item.label).includes(want) || want.includes(norm(item.label))));
                        if (chosen) nextDepth = depth + 2;
                    }
                    chosen = chosen || items[0];
                    if (!chosen) return [];
                    return [String(chosen.value), ...cascadeValuePath(chosen.children || [], preferredColumns, nextDepth)];
                };
                const setCascadeDirect = async (row, label, preferredColumns) => {
                    const source = row.querySelector('.am-list-content')?.closest('.am-list-item') || row.closest?.('.am-list-item') || row;
                    let fiber = getFiber(source);
                    let depth = 0;
                    while (fiber && depth < 16) {
                        const props = fiber.memoizedProps || fiber.pendingProps || fiber._currentElement?.props;
                        if (props && Array.isArray(props.data) && props.data.length) {
                            let values = cascadeValuePath(props.data, preferredColumns).filter(Boolean);
                            if (label === '居住省市') values = ['110000', '110105'];
                            if (values.length >= 2 && (typeof props.onOk === 'function' || typeof props.onChange === 'function')) {
                                if (typeof props.onOk === 'function') props.onOk(values);
                                if (typeof props.onChange === 'function') props.onChange(values);
                                await sleep(650);
                                records.push({ text: `${label}=${preferredColumns.map(col => col[0]).join('/')}`, selector: 'h5-cascade-fiber' });
                                return true;
                            }
                        }
                        fiber = fiber.return || fiber._hostParent || fiber._currentElement?._owner;
                        depth += 1;
                    }
                    return false;
                };
                const setSingleDirect = async (row, label, preferredTexts) => {
                    const wants = preferredTexts.map(text => norm(text)).filter(Boolean);
                    const source = row.querySelector('.am-list-content')?.closest('.am-list-item') || row.closest?.('.am-list-item') || row;
                    let fiber = getFiber(source);
                    let depth = 0;
                    while (fiber && depth < 16) {
                        const props = fiber.memoizedProps || fiber.pendingProps || fiber._currentElement?.props;
                        if (props && Array.isArray(props.data) && props.data.length) {
                            const bankPair = String(mockData.bankAccountPair_107 || '').split('|');
                            const preferredBankValue = bankPair[1] || mockData.bankValue_107 || mockData.bankControlValue_107 || '';
                            const isBankLabel = /开户银行|开户行|银行/.test(label);
                            const chosen = (isBankLabel && preferredBankValue
                                    ? props.data.find(item => String(item.value) === String(preferredBankValue))
                                    : null)
                                || props.data.find(item => wants.some(want => norm(item.label).includes(want) || want.includes(norm(item.label))))
                                || props.data.find(item => /银行/.test(norm(item.label)))
                                || props.data[0];
                            if (chosen?.value != null && typeof props.onChange === 'function') {
                                props.onChange([String(chosen.value)]);
                                await sleep(650);
                                records.push({ text: `${label}=${chosen.label || chosen.value}`, selector: 'h5-single-fiber' });
                                return true;
                            }
                        }
                        fiber = fiber.return || fiber._hostParent || fiber._currentElement?._owner;
                        depth += 1;
                    }
                    return false;
                };
                const setBusinessFieldDirect = async (row, label, keyCode, value) => {
                    const moduleIdForKey = code => {
                        if (code === 'insuranceDate') return '102';
                        if (code === 'purpose' || code === 'tripDestination') return '40';
                        return '';
                    };
                    const clearBusinessField = raw => ({
                        ...(raw && typeof raw === 'object' ? raw : {}),
                        value: String(value),
                        label: label === '出行目的' && String(value) === '1' ? '旅游' : String(value),
                        text: label === '出行目的' && String(value) === '1' ? '旅游' : String(value),
                        hasError: false,
                        hasAjaxError: false,
                        error: false,
                        errorMsg: '',
                        errorRemind: '',
                        msg: '',
                        ajaxError: '',
                    });
                    const patchBusinessState = () => {
                        let changed = 0;
                        const moduleId = moduleIdForKey(keyCode);
                        if (!moduleId) return 0;
                        const patchPlain = obj => {
                            const roots = [
                                obj?.product?.insure?.data?.data,
                                obj?.insure?.data?.data,
                                obj?.data?.data,
                                obj?.data,
                            ].filter(Boolean);
                            for (const root of roots) {
                                const rows = root[moduleId] || root[Number(moduleId)];
                                if (!Array.isArray(rows) || !rows[0] || typeof rows[0] !== 'object') continue;
                                const current = rows[0][keyCode];
                                rows[0][keyCode] = current && typeof current === 'object'
                                    ? clearBusinessField(current)
                                    : String(value);
                                changed += 1;
                            }
                        };
                        patchPlain(window.__NEXT_DATA__);
                        for (const storage of [window.localStorage, window.sessionStorage]) {
                            if (!storage) continue;
                            for (let i = 0; i < storage.length; i += 1) {
                                const storageKey = storage.key(i);
                                if (!storageKey || !/insure|product/i.test(storageKey)) continue;
                                try {
                                    const raw = storage.getItem(storageKey);
                                    if (!raw || raw[0] !== '{') continue;
                                    const json = JSON.parse(raw);
                                    const before = changed;
                                    patchPlain(json);
                                    if (changed !== before) storage.setItem(storageKey, JSON.stringify(json));
                                } catch (_) {}
                            }
                        }
                        const store = window.__NEXT_REDUX_STORE__ || window.store || window.reduxStore;
                        if (store && typeof store.getState === 'function') {
                            try {
                                const state = store.getState();
                                let next = state;
                                const moduleHasRows = path => {
                                    const rows = next?.getIn?.(path);
                                    if (!rows) return false;
                                    if (Array.isArray(rows)) return rows.length > 0;
                                    if (typeof rows.size === 'number' && typeof rows.get === 'function') return rows.size > 0;
                                    return false;
                                };
                                const setIn = (path, newValue) => {
                                    if (next && typeof next.setIn === 'function') {
                                        try {
                                            next = next.setIn(path, newValue);
                                            changed += 1;
                                        } catch (_) {}
                                    }
                                };
                                for (const base of [
                                    ['product', 'insure', 'data', 'data', moduleId, 0],
                                    ['insure', 'data', 'data', moduleId, 0],
                                    ['data', 'data', moduleId, 0],
                                    ['data', moduleId, 0],
                                ]) {
                                    if (!moduleHasRows(base.slice(0, -1))) continue;
                                    setIn([...base, keyCode, 'value'], String(value));
                                    setIn([...base, keyCode, 'label'], label === '出行目的' && String(value) === '1' ? '旅游' : String(value));
                                    setIn([...base, keyCode, 'text'], label === '出行目的' && String(value) === '1' ? '旅游' : String(value));
                                    setIn([...base, keyCode, 'hasError'], false);
                                    setIn([...base, keyCode, 'hasAjaxError'], false);
                                    setIn([...base, keyCode, 'error'], false);
                                    setIn([...base, keyCode, 'errorMsg'], '');
                                    setIn([...base, keyCode, 'errorRemind'], '');
                                    setIn([...base, keyCode, 'msg'], '');
                                    setIn([...base, keyCode, 'ajaxError'], '');
                                }
                                if (next !== state) store.getState = () => next;
                            } catch (_) {}
                        }
                        return changed;
                    };
                    patchBusinessState();
                    const sources = [
                        row.querySelector('.am-list-content')?.closest('.am-list-item'),
                        row.closest?.('.am-list-item'),
                        row,
                        ...Array.from(row.querySelectorAll('.insure-filed-wrapper,.am-list-item,.picker-input,.insure-filed-wrapper *,.am-list-item *')),
                    ].filter(Boolean);
                    const seen = new Set();
                    for (const source of sources) {
                        let fiber = getFiber(source);
                        let depth = 0;
                        while (fiber && depth < 24) {
                            if (seen.has(fiber)) {
                                fiber = fiber.return || fiber._hostParent || fiber._currentElement?._owner;
                                depth += 1;
                                continue;
                            }
                            seen.add(fiber);
                            const props = fiber.memoizedProps || fiber.pendingProps || fiber._currentElement?.props;
                            if (
                                props?.currentAttr?.keyCode === keyCode
                                && typeof props.onChange === 'function'
                            ) {
                                const sourceValue = props.currentAttrData && typeof props.currentAttrData === 'object'
                                    ? props.currentAttrData
                                    : {};
                                const nextValue = {
                                    ...sourceValue,
                                    value: String(value),
                                    hasError: false,
                                    errorMsg: '',
                                    errorRemind: '',
                                };
                                const mid = props.currentModule?.id ?? props.currentModule?.moduleId ?? props.mid ?? sourceValue.mid;
                                const index = Number.isFinite(Number(props.index)) ? Number(props.index) : 0;
                                props.onChange({ mid, index, keyCode, value: nextValue });
                                patchBusinessState();
                                await sleep(650);
                                records.push({ text: `${label}=${value}`, selector: 'h5-business-field-fiber' });
                                return true;
                            }
                            fiber = fiber.return || fiber._hostParent || fiber._currentElement?._owner;
                            depth += 1;
                        }
                    }
                    return patchBusinessState() > 0;
                };
                const setTravelDestinationDirect = async (row, destination) => {
                    const sources = [
                        row,
                        ...Array.from(row.querySelectorAll('.insure-filed-wrapper,.am-list-item,.picker-input,.insure-filed-wrapper *,.am-list-item *')),
                    ].filter(Boolean);
                    const seen = new Set();
                    for (const source of sources) {
                        let fiber = getFiber(source);
                        let depth = 0;
                        while (fiber && depth < 24) {
                            if (seen.has(fiber)) {
                                fiber = fiber.return || fiber._hostParent || fiber._currentElement?._owner;
                                depth += 1;
                                continue;
                            }
                            seen.add(fiber);
                            const node = fiber.stateNode;
                            const props = fiber.memoizedProps || fiber.pendingProps || fiber._currentElement?.props;
                            if (
                                node
                                && typeof node.doSubmit === 'function'
                                && props?.currentAttr?.keyCode === 'tripDestination'
                            ) {
                                node.doSubmit([destination]);
                                if (typeof node.hideDestinationPannel === 'function') node.hideDestinationPannel();
                                await sleep(650);
                                return true;
                            }
                            fiber = fiber.return || fiber._hostParent || fiber._currentElement?._owner;
                            depth += 1;
                        }
                    }
                    return false;
                };
                const setDatePickerDirect = async (row, label, value) => {
                    const parts = formatCompactDate(value).match(/^(\\d{4})-(\\d{2})-(\\d{2})$/);
                    if (!parts) return false;
                    const datePickerSources = [
                        row.querySelector('.am-list-content')?.closest('.am-list-item'),
                        row.closest?.('.am-list-item'),
                        ...Array.from(row.querySelectorAll('.am-list-item,.insure-filed-wrapper,.picker-input')),
                        row,
                    ].filter(Boolean);
                    for (const source of datePickerSources) {
                        let fiber = getFiber(source);
                        let depth = 0;
                        while (fiber && depth < 16) {
                            const props = fiber.memoizedProps || fiber.pendingProps || fiber._currentElement?.props;
                            if (
                                props
                                && props.mode === 'date'
                                && (typeof props.onOk === 'function' || typeof props.onChange === 'function')
                            ) {
                                const dateValue = new Date(Number(parts[1]), Number(parts[2]) - 1, Number(parts[3]));
                                if (typeof props.onChange === 'function') props.onChange(dateValue);
                                if (typeof props.onOk === 'function') props.onOk(dateValue);
                                await sleep(650);
                                records.push({ text: `${label}=${formatCompactDate(value)}`, selector: 'h5-date-picker-fiber' });
                                return true;
                            }
                            fiber = fiber.return || fiber._hostParent || fiber._currentElement?._owner;
                            depth += 1;
                        }
                    }
                    return false;
                };
                const selectCascade = async (label, preferredColumns) => {
                    let changed = 0;
                    for (const row of fieldRows(label)) {
                        if (!rowNeedsValue(row)) continue;
                        if (await setCascadeDirect(row, label, preferredColumns)) {
                            changed += 1;
                            continue;
                        }
                        if (!await openRow(row)) continue;
                        let ok = true;
                        for (let i = 0; i < preferredColumns.length; i += 1) {
                            const selected = await setPickerCol(i, preferredColumns[i]);
                            if (!selected) {
                                ok = false;
                                break;
                            }
                        }
                        if (ok && await clickPickerConfirm()) {
                            records.push({ text: `${label}=${preferredColumns.map(col => col[0]).join('/')}`, selector: 'h5-cascade-picker' });
                            changed += 1;
                        }
                    }
                    return changed;
                };
                const selectSinglePicker = async (label, preferredTexts) => {
                    let changed = 0;
                    for (const row of fieldRows(label)) {
                        if (!rowNeedsValue(row)) continue;
                        if (await setSingleDirect(row, label, preferredTexts)) {
                            changed += 1;
                            continue;
                        }
                        if (!await openRow(row)) continue;
                        let selected = await setPickerCol(0, preferredTexts);
                        if (!selected) {
                            const options = Array.from(document.querySelectorAll('.am-picker-col-item, [role="dialog"] li, [role="dialog"] .am-list-item, .adm-popup li, .am-modal li'))
                                .filter(visible)
                                .map(el => ({ el, text: norm(el.innerText || el.textContent) }))
                                .filter(item => item.text && !/请选择|取消|确定|完成/.test(item.text));
                            const wanted = preferredTexts.map(text => norm(text)).filter(Boolean);
                            const preferred = options.find(item => wanted.some(want => item.text.includes(want) || want.includes(item.text)));
                            const fallback = preferred || options.find(item => /银行/.test(item.text)) || options[0];
                            if (fallback) {
                                fire(fallback.el);
                                selected = fallback.text;
                                await sleep(300);
                            }
                        }
                        if (selected && await clickPickerConfirm()) {
                            records.push({ text: `${label}=${selected}`, selector: 'h5-single-picker' });
                            changed += 1;
                        }
                    }
                    return changed;
                };
                const formatCompactDate = value => String(value || '').replace(/[./]/g, '-').trim();
                const normalizePolicyStartDate = value => {
                    const raw = formatCompactDate(value);
                    const parseDate = text => {
                        const parts = String(text || '').match(/^(\\d{4})-(\\d{2})-(\\d{2})$/);
                        return parts ? new Date(Number(parts[1]), Number(parts[2]) - 1, Number(parts[3])) : null;
                    };
                    const formatDate = date => {
                        const year = date.getFullYear();
                        const month = String(date.getMonth() + 1).padStart(2, '0');
                        const day = String(date.getDate()).padStart(2, '0');
                        return `${year}-${month}-${day}`;
                    };
                    const today = new Date();
                    const fallbackDate = new Date(today.getFullYear(), today.getMonth(), today.getDate() + 1);
                    const fallbackText = formatDate(fallbackDate);
                    const parsed = parseDate(raw);
                    if (parsed && parsed.getTime() === fallbackDate.getTime()) return fallbackText;
                    return fallbackText;
                };
                const pickDateByText = async value => {
                    const parts = formatCompactDate(value).match(/^(\\d{4})-(\\d{2})-(\\d{2})$/);
                    if (!parts) return '';
                    const [, year, month, day] = parts;
                    for (const text of [year, String(Number(month)), month, String(Number(day)), day]) {
                        const nodes = Array.from(document.querySelectorAll('.am-picker-col-item, [role="dialog"] li, [role="dialog"] .am-list-item, .adm-popup li, .am-modal li, button, span, div'))
                            .filter(el => visible(el) && insidePopup(el))
                            .map(el => ({ el, text: norm(el.innerText || el.textContent) }));
                        const choice = nodes.find(item => item.text === text || item.text === `${Number(text)}日` || item.text === `${Number(text)}月` || item.text === `${text}年`)
                            || nodes.find(item => item.text.includes(text));
                        if (choice) {
                            fire(choice.el);
                            await sleep(250);
                        }
                    }
                    return formatCompactDate(value);
                };
                const selectPolicyStartDate = async () => {
                    const startDate = normalizePolicyStartDate(mockData['policy.start_date'] || mockData.policyStartDate || '');
                    if (!startDate) return 0;
                    let changed = 0;
                    for (const row of fieldRows('起保日期')) {
                        if (!rowNeedsValue(row)) continue;
                        const businessChanged = await setBusinessFieldDirect(row, '起保日期', 'insuranceDate', startDate);
                        if (businessChanged) changed += 1;
                        if (!rowNeedsValue(row)) continue;
                        if (await setDatePickerDirect(row, '起保日期', startDate)) {
                            changed += 1;
                            continue;
                        }
                        if (await setSingleDirect(row, '起保日期', [startDate])) {
                            changed += 1;
                            continue;
                        }
                        if (!await openRow(row)) continue;
                        let selected = '';
                        const parts = startDate.match(/^(\\d{4})-(\\d{2})-(\\d{2})$/);
                        if (parts) {
                            selected = await setPickerCol(0, [parts[1]]) || selected;
                            selected = await setPickerCol(1, [String(Number(parts[2])), parts[2]]) || selected;
                            selected = await setPickerCol(2, [String(Number(parts[3])), parts[3]]) || selected;
                        }
                        if (!selected) selected = await pickDateByText(startDate);
                        if (await clickPickerConfirm()) {
                            records.push({ text: `起保日期=${startDate}`, selector: 'h5-policy-start-date' });
                            changed += 1;
                        }
                    }
                    return changed;
                };
                const selectTravelPurpose = async () => {
                    let changed = 0;
                    for (const row of fieldRowsMatching(/^出行目的(?!地)/)) {
                        if (!rowNeedsValue(row)) continue;
                        const businessChanged = await setBusinessFieldDirect(row, '出行目的', 'purpose', '1');
                        if (businessChanged) changed += 1;
                        if (!rowNeedsValue(row)) continue;
                        if (await setSingleDirect(row, '出行目的', ['旅游', '观光旅游', '旅游观光', '休闲旅游', '商务', '探亲'])) {
                            changed += 1;
                            continue;
                        }
                        if (!await openRow(row)) continue;
                        let selected = await setPickerCol(0, ['旅游', '观光旅游', '旅游观光', '休闲旅游', '商务', '探亲']);
                        if (!selected) {
                            const options = Array.from(document.querySelectorAll('.am-picker-col-item, [role="dialog"] li, [role="dialog"] .am-list-item, .adm-popup li, .am-modal li'))
                                .filter(el => visible(el) && insidePopup(el))
                                .map(el => ({ el, text: norm(el.innerText || el.textContent) }))
                                .filter(item => item.text && !/请选择|取消|确定|完成/.test(item.text));
                            const fallback = options.find(item => /旅游|观光|商务|探亲/.test(item.text)) || options[0];
                            if (fallback) {
                                fire(fallback.el);
                                selected = fallback.text;
                                await sleep(300);
                            }
                        }
                        if (selected && await clickPickerConfirm()) {
                            records.push({ text: `出行目的=${selected}`, selector: 'h5-travel-purpose-picker' });
                            changed += 1;
                        }
                    }
                    return changed;
                };
                const selectTravelDestination = async () => {
                    let changed = 0;
                    for (const row of fieldRowsMatching(/^出行目的地|目的地$/)) {
                        if (!rowNeedsValue(row, '出行目的地')) continue;
                        const businessChanged = await setBusinessFieldDirect(row, '出行目的地', 'tripDestination', '中国澳门');
                        if (businessChanged) changed += 1;
                        if (!rowNeedsValue(row, '出行目的地')) continue;
                        if (await setTravelDestinationDirect(row, '中国澳门')) {
                            records.push({ text: '出行目的地=中国澳门', selector: 'h5-travel-destination-fiber' });
                            changed += 1;
                            continue;
                        }
                        if (await setSingleDirect(row, '出行目的地', ['日本', '泰国', '新加坡', '亚洲', '中国香港', '中国澳门'])) {
                            changed += 1;
                            continue;
                        }
                        if (!await openRow(row)) continue;
                        const search = Array.from(document.querySelectorAll('input,textarea'))
                            .filter(el => visible(el) && insidePopup(el))
                            .find(input => /搜索|目的地|国家|地区|请输入/i.test(`${input.placeholder || ''}${input.getAttribute('aria-label') || ''}${input.name || ''}`));
                        if (search) {
                            setNativeValue(search, '日本');
                            await sleep(650);
                        }
                        let selected = await setPickerCol(0, ['日本', '泰国', '新加坡', '亚洲', '中国香港', '中国澳门']);
                        if (!selected) {
                            const options = Array.from(document.querySelectorAll('.am-picker-col-item, [role="dialog"] li, [role="dialog"] .am-list-item, .adm-popup li, .am-modal li, button, span, div'))
                                .filter(el => visible(el) && insidePopup(el))
                                .map(el => ({ el, text: norm(el.innerText || el.textContent) }))
                                .filter(item => item.text && item.text.length <= 24 && !/请选择|取消|确定|完成|搜索|请输入/.test(item.text));
                            const fallback = options.find(item => /日本|泰国|新加坡|亚洲|香港|澳门/.test(item.text)) || options[0];
                            if (fallback) {
                                fire(fallback.el);
                                selected = fallback.text;
                                await sleep(350);
                            }
                        }
                        if (selected && await clickPickerConfirm()) {
                            records.push({ text: `出行目的地=${selected}`, selector: 'h5-travel-destination-picker' });
                            changed += 1;
                        }
                    }
                    return changed;
                };
                const isBadOccupation = text => /职业选择|职业分类|职业类别|职业大类|职业中类|职业小类|展开选择|取消|返回|输入搜索|搜索内容|请选择|下一步|上一步|确定|完成|全部职业|收起|拒保|不承保|高危|危险|禁止|木材|森林|渔业|矿|爆破|高空|潜水|海上|消防|警察|军人/.test(text);
                const occupationFallbacks = ['一般内勤人员', '机关团体公司', '一般职业人员', '内勤', '文员', '行政', '学生', '一般'];
                const occupationOptionScore = (item, wantedTexts) => {
                    const text = item.text;
                    let score = 0;
                    wantedTexts.forEach((wanted, index) => {
                        if (!wanted) return;
                        if (text === wanted) score = Math.max(score, 3000 - index * 10);
                        else if (text.includes(wanted) || wanted.includes(text)) score = Math.max(score, 2200 - index * 10);
                    });
                    occupationFallbacks.forEach((wanted, index) => {
                        if (text === wanted) score = Math.max(score, 2000 - index * 20);
                        else if (text.includes(wanted) || wanted.includes(text)) score = Math.max(score, 1400 - index * 20);
                    });
                    if (/内勤|文员|行政/.test(text)) score = Math.max(score, 1000);
                    if (/学生/.test(text)) score = Math.max(score, 800);
                    if (text === '一般') score = Math.max(score, 120);
                    return score;
                };
                const selectOccupation = async () => {
                    let changed = 0;
                    const keywords = [
                        mockData['applicant.occupation'],
                        mockData['insured.occupation'],
                        ...occupationFallbacks,
                        '内勤',
                        '一般职业人员',
                        '一般',
                        '学生',
                    ].map(text => norm(text)).filter(Boolean);
                    for (const row of fieldRows('职业')) {
                        if (!rowNeedsValue(row)) continue;
                        if (!await openRow(row)) continue;
                        const search = Array.from(document.querySelectorAll('input')).filter(visible)
                            .find(input => /职业|搜索|工程师|请输入/.test(`${input.placeholder || ''}${input.getAttribute('aria-label') || ''}${input.name || ''}`));
                        if (search) {
                            setNativeValue(search, keywords[0] || '内勤');
                            await sleep(650);
                        }
                        let picked = '';
                        for (let depth = 0; depth < 3 && !picked; depth += 1) {
                            const options = Array.from(document.querySelectorAll('.job-modal li, .job-modal .am-list-item, [role="dialog"] li, [role="dialog"] .am-list-item, .adm-popup li, .adm-popup .am-list-item, .am-modal li, .am-modal .am-list-item, [class*="job"] li, [class*="occupation"] li, div, span'))
                                .filter(visible)
                                .map((el, index) => ({ el, index, text: norm(el.innerText || el.textContent) }))
                                .filter(item => item.text.length >= 2 && item.text.length <= 32 && !isBadOccupation(item.text));
                            const ranked = options
                                .map(item => ({ ...item, score: occupationOptionScore(item, keywords) }))
                                .filter(item => item.score > 0)
                                .sort((left, right) => right.score - left.score || left.index - right.index);
                            const choice = ranked[0];
                            if (!choice) break;
                            fire(choice.el);
                            picked = choice.text;
                            await sleep(550);
                            const text = norm(row.innerText || row.textContent);
                            if (text && !/请选择|请填写|请重新选择/.test(text)) break;
                            picked = '';
                        }
                        const close = Array.from(document.querySelectorAll('[role="dialog"] button, .job-modal button, .adm-popup button, .am-modal button, span, div'))
                            .filter(visible)
                            .map(el => ({ el, text: norm(el.innerText || el.textContent) }))
                            .find(item => item.text === '确定' || item.text === '完成');
                        if (close) {
                            fire(close.el);
                            await sleep(350);
                        }
                        const rowText = norm(row.innerText || row.textContent);
                        if (picked || (rowText && !/请选择|请填写|请重新选择/.test(rowText))) {
                            records.push({ text: `职业=${picked || rowText}`, selector: 'h5-occupation-picker' });
                            changed += 1;
                        }
                    }
                    return changed;
                };

                const regionParts = ['北京市', '北京市', '朝阳区'];
                await selectCascade('居住省市', [[regionParts[0], '北京'], [regionParts[1], '北京'], [regionParts[2], '朝阳区', '东城区', '海淀区']]);
                await selectPolicyStartDate();
                await selectTravelPurpose();
                await selectTravelDestination();
                await selectOccupation();
                const bankPair = String(mockData.bankAccountPair_107 || '').split('|');
                const bankName = bankPair[0] || mockData.bankName_107 || mockData.openBank_107 || '中国工商银行';
                const bankValue = bankPair[1] || mockData.bankValue_107 || '16';
                const payAccount = bankPair[2] || mockData.payAccount_107 || '6200588435998028938';
                const cardOwner = mockData.cardOwner_107 || mockData['applicant.name'] || '';
                const clearErrorObject = value => ({
                    value,
                    hasError: false,
                    hasAjaxError: false,
                    error: false,
                    errorMsg: '',
                    msg: '',
                    ajaxError: '',
                    validateStatus: 'success',
                    validStatus: true,
                });
                const markExistingPayAccountInputs = () => {
                    let locked = 0;
                    for (const el of Array.from(document.querySelectorAll('input,textarea'))) {
                        const rowText = el.closest('.am-list-item,.am-list-line,.insure-filed-wrapper,li,dd,label,div')?.innerText || '';
                        const probe = `${el.name || ''} ${el.id || ''} ${el.placeholder || ''} ${el.getAttribute('aria-label') || ''} ${rowText}`;
                        const matched = /payAccount|bankAccount|银行账号|银行卡号|银行账户|开卡信息|储蓄卡|账号/i.test(probe)
                            && !/证件号码|身份证|手机号码|手机号|验证码|短信/i.test(probe);
                        if (!matched || !String(el.value || '').trim()) continue;
                        el.dataset.agent3PayAccountLocked = '1';
                        window.__agent3PayAccountLocked = true;
                        window.__agent3PayAccountValue = String(el.value || payAccount);
                        locked += 1;
                    }
                    return locked;
                };
                const syncBankState = () => {
                    let changed = 0;
                    const patchPlain = obj => {
                        const roots = [
                            obj?.product?.insure?.data?.data,
                            obj?.insure?.data?.data,
                            obj?.data?.data,
                            obj?.data,
                        ].filter(Boolean);
                        for (const root of roots) {
                            const rows = root['107'] || root[107];
                            if (!Array.isArray(rows) || !rows[0] || typeof rows[0] !== 'object') continue;
                            const row = rows[0];
                            row.bank = { ...(row.bank || {}), ...clearErrorObject(bankValue), label: bankName, text: bankName, name: bankName };
                            row.payAccount = { ...(row.payAccount || {}), ...clearErrorObject(payAccount) };
                            row.cardOwner = { ...(row.cardOwner || {}), ...clearErrorObject(cardOwner) };
                            changed += 1;
                        }
                    };
                    patchPlain(window.__NEXT_DATA__);
                    for (const storage of [window.localStorage, window.sessionStorage]) {
                        if (!storage) continue;
                        for (let i = 0; i < storage.length; i += 1) {
                            const key = storage.key(i);
                            if (!key || !/insure|product/i.test(key)) continue;
                            try {
                                const value = storage.getItem(key);
                                if (!value || value[0] !== '{') continue;
                                const json = JSON.parse(value);
                                patchPlain(json);
                                storage.setItem(key, JSON.stringify(json));
                            } catch (_) {}
                        }
                    }
                    const store = window.__NEXT_REDUX_STORE__ || window.store || window.reduxStore;
                    if (store && typeof store.getState === 'function') {
                        try {
                            const state = store.getState();
                            let next = state;
                            const moduleHasRows = path => {
                                const rows = next?.getIn?.(path);
                                if (!rows) return false;
                                if (Array.isArray(rows)) return rows.length > 0;
                                if (typeof rows.size === 'number' && typeof rows.get === 'function') return rows.size > 0;
                                return false;
                            };
                            const setIn = (path, value) => {
                                if (next && typeof next.setIn === 'function') {
                                    next = next.setIn(path, value);
                                    changed += 1;
                                }
                            };
                            for (const base of [
                                ['product', 'insure', 'data', 'data', '107', 0],
                                ['insure', 'data', 'data', '107', 0],
                                ['data', 'data', '107', 0],
                                ['data', '107', 0],
                            ]) {
                                if (!moduleHasRows(base.slice(0, -1))) continue;
                                setIn([...base, 'bank', 'value'], bankValue);
                                setIn([...base, 'bank', 'hasError'], false);
                                setIn([...base, 'bank', 'hasAjaxError'], false);
                                setIn([...base, 'bank', 'errorMsg'], '');
                                setIn([...base, 'bank', 'msg'], '');
                                setIn([...base, 'bank', 'ajaxError'], '');
                                setIn([...base, 'bank', 'label'], bankName);
                                setIn([...base, 'bank', 'text'], bankName);
                                setIn([...base, 'bank', 'name'], bankName);
                                setIn([...base, 'payAccount', 'value'], payAccount);
                                setIn([...base, 'payAccount', 'hasError'], false);
                                setIn([...base, 'payAccount', 'hasAjaxError'], false);
                                setIn([...base, 'payAccount', 'errorMsg'], '');
                                setIn([...base, 'payAccount', 'msg'], '');
                                setIn([...base, 'payAccount', 'ajaxError'], '');
                                setIn([...base, 'cardOwner', 'value'], cardOwner);
                                setIn([...base, 'cardOwner', 'hasError'], false);
                                setIn([...base, 'cardOwner', 'hasAjaxError'], false);
                            }
                            if (next !== state) store.getState = () => next;
                        } catch (_) {}
                    }
                    return changed;
                };
                const bankSynced = syncBankState();
                const accountLocked = markExistingPayAccountInputs();
                records.push({ text: `开户银行状态=${bankName}/${payAccount}`, selector: `state-bank-${bankSynced}-account-${accountLocked}` });
                await selectSinglePicker('开户银行', [bankName, '中国工商银行', '工商银行', '中国建设银行', '招商银行', '中国银行', '农业银行']);
                await selectSinglePicker('开户行', [bankName, '中国工商银行', '工商银行', '中国建设银行', '招商银行', '中国银行', '农业银行']);
                return records;
            }""",
            mock_data,
        )
    except Exception:
        return []
    return [
        {
            "text": str(item.get("text") or ""),
            "tag": "field",
            "selector": item.get("selector"),
            "source_url": page.url,
            "target_url": page.url,
            "score": None,
            "click_strategy": "h5-picker-default",
            "dismissed_overlays": [],
            "action_type": "minimal_data",
        }
        for item in filled or []
    ]


async def _apply_placeholder_form_data(page: Any, mock_data: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        filled = await page.evaluate(
            """(mockData) => {
                const records = [];
                const skipPayAccountWrites = !!mockData.__skipPayAccount || !!window.__agent3SkipPayAccountWrites;
                function isPayAccountField(el, label = '') {
                    const rowText = el.closest('.am-list-item,.am-list-line,.insure-filed-wrapper,li,dd,label,div')?.innerText || '';
                    const probe = `${el.name || ''} ${el.id || ''} ${el.placeholder || ''} ${el.getAttribute('aria-label') || ''} ${label || ''} ${rowText}`;
                    const accountLike = /payAccount|bankAccount|银行账号|银行卡号|银行账户|开卡信息|账号/i.test(probe);
                    const strongAccountHint = /开卡信息|账号|储蓄卡/i.test(probe);
                    const ownerOnly = /账户名|持卡人/i.test(probe) && !/银行账号|银行卡号|银行账户|开卡信息|账号/i.test(probe);
                    const nonAccountIdentityField = /证件号码|身份证/i.test(probe) && !strongAccountHint;
                    return accountLike && !ownerOnly
                        && !nonAccountIdentityField
                        && !/手机号码|手机号|验证码|短信/i.test(probe);
                }
                function lockPayAccount(el, value = '') {
                    if (!el) return;
                    el.dataset.agent3PayAccountLocked = '1';
                    window.__agent3PayAccountLocked = true;
                    window.__agent3PayAccountValue = String(value || el.value || '');
                }
                function shouldSkipPayAccount(el, label = '', value = '') {
                    if (!isPayAccountField(el, label)) return false;
                    const current = String(el.value || '').replace(/\\s+/g, '').trim();
                    const expected = String(value || window.__agent3PayAccountValue || '').replace(/\\s+/g, '').trim();
                    if (skipPayAccountWrites) {
                        if (/^\\d{10,30}$/.test(current)) {
                            lockPayAccount(el, current);
                            return true;
                        }
                        return false;
                    }
                    if ((el.dataset.agent3PayAccountLocked === '1' || window.__agent3PayAccountLocked) && /^\\d{10,30}$/.test(current)) return true;
                    if (current && expected && current === expected) {
                        lockPayAccount(el, current);
                        return true;
                    }
                    return false;
                }
                const englishNameFor = prefix => {
                    const configured = mockData[`${prefix}.english_name`] || mockData[`${prefix}.eName`] || mockData[`${prefix}.pinyin`];
                    if (configured && /^[A-Za-z][A-Za-z\\s'-]{1,60}$/.test(String(configured))) return String(configured).replace(/\\s+/g, '').toLowerCase();
                    return prefix === 'insured' ? 'lisi' : 'zhangsan';
                };
                const isEnglishNameField = (el, label = '') => {
                    const probe = `${el?.name || ''} ${el?.id || ''} ${el?.placeholder || ''} ${el?.getAttribute?.('aria-label') || ''} ${label || ''}`;
                    return /eName|english|pinyin|英文|拼音/i.test(probe);
                };
                function setNativeValue(el, value, label = '') {
                    if (!el) return false;
                    if (shouldSkipPayAccount(el, label, value)) return false;
                    if (el.disabled) el.removeAttribute('disabled');
                    if (el.readOnly) el.removeAttribute('readonly');
                    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
                    const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
                    if (setter) setter.call(el, value);
                    else el.value = value;
                    el.setAttribute('value', value);
                    for (const type of ['input', 'change', 'blur']) el.dispatchEvent(new Event(type, { bubbles: true }));
                    if (isPayAccountField(el, label)) lockPayAccount(el, value);
                    return true;
                }
                const rules = [
                    [/eName|english|pinyin|英文|拼音/i, englishNameFor('applicant'), '投保人拼音/英文名'],
                    [/真实姓名|姓名/, mockData['applicant.name'] || '张三', '投保人姓名'],
                    [/证件号码/, mockData['applicant.id_no'] || '110101199001011237', '投保人证件号码'],
                    [/详细地址|联系地址|地址/, mockData['applicant.address'] || '北京市朝阳区测试地址1号', '投保人地址'],
                    [/真实手机|手机号码|手机号/, mockData['applicant.mobile'] || '13800138000', '投保人手机号'],
                    [/真实邮箱|电子邮箱|邮箱/i, mockData['applicant.email'] || 'zhangsan@example.com', '投保人邮箱'],
                    [/账户名须为投保人本人|持卡人/, mockData.cardOwner_107 || mockData['applicant.name'] || '张三', '持卡人'],
                    [/开卡信息|储蓄卡|账号/, mockData.payAccount_107 || '6200588435998028938', '银行账号'],
                ];
                for (const el of Array.from(document.querySelectorAll('input,textarea'))) {
                    const type = String(el.type || '').toLowerCase();
                    if (['hidden', 'button', 'submit', 'reset', 'file', 'radio', 'checkbox'].includes(type)) continue;
                    const probe = `${el.placeholder || ''} ${el.getAttribute('aria-label') || ''}`;
                    for (const [regex, value, label] of rules) {
                        if (!regex.test(probe)) continue;
                        if (setNativeValue(el, String(value), label)) {
                            records.push({ text: `${label}=${value}`, selector: el.tagName.toLowerCase() });
                            break;
                        }
                    }
                }
                return records;
            }""",
            mock_data,
        )
    except Exception:
        return []
    return [
        {
            "text": str(item.get("text") or ""),
            "tag": "field",
            "selector": item.get("selector"),
            "source_url": page.url,
            "target_url": page.url,
            "score": None,
            "click_strategy": "placeholder-force-fill",
            "dismissed_overlays": [],
            "action_type": "minimal_data",
        }
        for item in filled or []
    ]


async def _apply_id_validity_defaults(page: Any, mock_data: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    filled: list[dict[str, Any]] = []
    data = mock_data or {}
    start_value = str(data.get("applicant.card_valid_start") or "2026-01-01")
    end_value = str(data.get("applicant.card_valid_end") or "2031-01-01")
    has_explicit_end_value = bool(data.get("applicant.card_valid_end"))
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", start_value):
        start_value = "2026-01-01"
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", end_value):
        end_value = "2031-01-01"
    id_period_is_long = False

    def derive_id_card_period(id_no: str, start: str) -> tuple[str | None, bool]:
        match = re.fullmatch(r"\d{17}[\dXx]", id_no or "")
        if not match:
            return None, False
        try:
            birth_year = int(id_no[6:10])
            birth_month = int(id_no[10:12])
            birth_day = int(id_no[12:14])
            start_year, start_month, start_day = [int(item) for item in start.split("-")]
            start_date = date(start_year, start_month, start_day)
        except Exception:
            return None, False
        age = start_year - birth_year - ((start_month, start_day) < (birth_month, birth_day))
        if age < 0:
            return None, False
        if age < 16:
            valid_years = 5
        elif age < 26:
            valid_years = 10
        elif age < 46:
            valid_years = 20
        else:
            return None, True
        try:
            end_date = start_date.replace(year=start_date.year + valid_years)
        except ValueError:
            end_date = start_date.replace(year=start_date.year + valid_years, month=2, day=28)
        return end_date.isoformat(), False

    try:
        visible_id_no = await page.evaluate(
            """() => {
                const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                const values = Array.from(document.querySelectorAll('input'))
                    .filter(visible)
                    .map(el => String(el.value || '').trim())
                    .filter(value => /^\\d{17}[\\dXx]$/.test(value));
                return values[0] || '';
            }"""
        )
    except Exception:
        visible_id_no = ""
    derived_end_value, id_period_is_long = derive_id_card_period(
        str(visible_id_no or data.get("applicant.id_no") or ""),
        start_value,
    )
    if derived_end_value and not has_explicit_end_value:
        end_value = derived_end_value

    async def sync_card_period_state(start: str, end: str) -> None:
        try:
            await page.evaluate(
                """({ start, end }) => {
                    const periodValue = `${start}|${end}`;
                    const clear = value => item => ({ ...(item || {}), value, hasError: false, errorMsg: '', msg: '' });
                    const patchPlain = obj => {
                        const roots = [
                            obj?.product?.insure?.data?.data,
                            obj?.insure?.data?.data,
                            obj?.data?.data,
                            obj?.data,
                        ].filter(Boolean);
                        for (const root of roots) {
                            for (const key of ['10', '20']) {
                                const rows = root[key] || root[Number(key)];
                                const row = Array.isArray(rows) ? rows[0] : rows;
                                if (!row || typeof row !== 'object') continue;
                                row.cardPeriod = clear(periodValue)(row.cardPeriod);
                                row.cardPeriodEnd = clear(end)(row.cardPeriodEnd);
                            }
                        }
                    };
                    patchPlain(window.__NEXT_DATA__);
                    for (const storage of [window.localStorage, window.sessionStorage]) {
                        if (!storage) continue;
                        for (let i = 0; i < storage.length; i += 1) {
                            const key = storage.key(i);
                            if (!key || !/insure|product/i.test(key)) continue;
                            try {
                                const raw = storage.getItem(key);
                                if (!raw || raw[0] !== '{') continue;
                                const json = JSON.parse(raw);
                                patchPlain(json);
                                storage.setItem(key, JSON.stringify(json));
                            } catch (_) {}
                        }
                    }
                    const store = window.__NEXT_REDUX_STORE__ || window.store || window.reduxStore;
                    if (store && typeof store.getState === 'function') {
                        try {
                            const state = store.getState();
                            let next = state;
                            if (next && typeof next.setIn === 'function') {
                                for (const base of [
                                    ['product', 'insure', 'data', 'data', '10', 0],
                                    ['product', 'insure', 'data', 'data', '20', 0],
                                    ['data', 'data', '10', 0],
                                    ['data', 'data', '20', 0],
                                ]) {
                                    next = next.setIn([...base, 'cardPeriod', 'value'], periodValue);
                                    next = next.setIn([...base, 'cardPeriod', 'hasError'], false);
                                    next = next.setIn([...base, 'cardPeriodEnd', 'value'], end);
                                    next = next.setIn([...base, 'cardPeriodEnd', 'hasError'], false);
                                }
                                if (next !== state) store.getState = () => next;
                            }
                        } catch (_) {}
                    }
                }""",
                {"start": start, "end": end},
            )
        except Exception:
            return

    async def _inject_open_picker_date(value: str) -> dict[str, Any]:
        try:
            result = await page.evaluate(
                """(value) => {
                    const [year, month, day] = String(value).split('-').map(Number);
                    const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                    const getFiber = node => {
                        if (!node) return null;
                        const key = Object.keys(node).find(
                            item => item.startsWith('__reactFiber$') || item.startsWith('__reactInternalInstance$')
                        );
                        return key ? node[key] : null;
                    };
                    const invokeFrom = node => {
                        let fiber = getFiber(node);
                        let depth = 0;
                        while (fiber && depth < 90) {
                            const props = fiber.memoizedProps || fiber.pendingProps || fiber._currentElement?.props;
                            if (props && (typeof props.onOk === 'function' || typeof props.onDateChange === 'function')) {
                                (props.onOk || props.onDateChange)(new Date(year, month - 1, day));
                                return true;
                            }
                            fiber = fiber.return || fiber._hostParent || fiber._currentElement?._owner;
                            depth += 1;
                        }
                        return false;
                    };
                    const header = Array.from(document.querySelectorAll('.am-picker-popup-header-right'))
                        .filter(visible)
                        .reverse()[0];
                    if (header && invokeFrom(header)) return { ok: true, via: 'header-right' };
                    const dialogs = Array.from(document.querySelectorAll('[role="dialog"], .am-picker-popup, .adm-popup, .adm-picker'))
                        .filter(visible)
                        .reverse();
                    for (const dialog of dialogs) {
                        if (invokeFrom(dialog)) return { ok: true, via: 'dialog' };
                    }
                    return { ok: false, via: 'none' };
                }""",
                value,
            )
            return dict(result or {})
        except Exception as exc:
            return {"ok": False, "via": "exception", "error": str(exc)[:160]}

    async def _close_open_picker() -> None:
        try:
            await page.evaluate(
                """() => {
                    const norm = text => String(text || '').replace(/\\s+/g, '').trim();
                    const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                    const fire = el => {
                        if (!el) return false;
                        for (const type of ['touchstart', 'mousedown', 'mouseup', 'click', 'touchend']) {
                            try {
                                const event = type.startsWith('touch')
                                    ? new Event(type, { bubbles: true, cancelable: true })
                                    : new MouseEvent(type, { bubbles: true, cancelable: true, view: window });
                                el.dispatchEvent(event);
                            } catch (_) {}
                        }
                        if (typeof el.click === 'function') el.click();
                        return true;
                    };
                    const headerLeft = Array.from(document.querySelectorAll('.am-picker-popup-header-left, .adm-picker-header-button'))
                        .filter(visible)
                        .reverse()[0];
                    if (headerLeft) return fire(headerLeft);
                    const nodes = Array.from(document.querySelectorAll('[role="dialog"] a, [role="dialog"] button, [role="dialog"] span, .am-picker-popup a, .am-picker-popup button, .am-picker-popup span, .adm-popup button, .adm-popup span'))
                        .filter(visible)
                        .map(el => ({ el, text: norm(el.innerText || el.textContent) }));
                    const cancel = [...nodes].reverse().find(item => item.text === '取消');
                    if (cancel) return fire(cancel.el);
                    const ok = nodes.find(item => /确定|完成|确认/.test(item.text));
                    return ok ? fire(ok.el) : false;
                }"""
            )
        except Exception:
            return

    async def set_card_period_by_e2e_pattern(start: str, end: str, is_long: bool = False) -> dict[str, Any]:
        diagnostics: dict[str, Any] = {
            "block_found": False,
            "short_clicked": False,
            "long_clicked": False,
            "start_clicked": False,
            "start_injected": False,
            "end_clicked": False,
            "end_injected": False,
        }

        try:
            direct = await page.evaluate(
                """async ({ start, end, isLong }) => {
                    const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
                    const norm = text => String(text || '').replace(/\\s+/g, '').trim();
                    const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                    const fire = el => {
                        if (!el) return false;
                        el.scrollIntoView({ block: 'center', inline: 'center' });
                        for (const type of ['touchstart', 'mousedown', 'mouseup', 'click', 'touchend']) {
                            try {
                                const event = type.startsWith('touch')
                                    ? new Event(type, { bubbles: true, cancelable: true })
                                    : new MouseEvent(type, { bubbles: true, cancelable: true, view: window });
                                el.dispatchEvent(event);
                            } catch (_) {}
                        }
                        if (typeof el.click === 'function') el.click();
                        return true;
                    };
                    const getFiber = node => {
                        if (!node) return null;
                        const key = Object.keys(node).find(
                            item => item.startsWith('__reactFiber$') || item.startsWith('__reactInternalInstance$')
                        );
                        return key ? node[key] : null;
                    };
                    const injectDate = value => {
                        const [year, month, day] = String(value).split('-').map(Number);
                        const invokeFrom = node => {
                            let fiber = getFiber(node);
                            let depth = 0;
                            while (fiber && depth < 100) {
                                const props = fiber.memoizedProps || fiber.pendingProps || fiber._currentElement?.props;
                                if (props && (typeof props.onOk === 'function' || typeof props.onDateChange === 'function')) {
                                    (props.onOk || props.onDateChange)(new Date(year, month - 1, day));
                                    return true;
                                }
                                fiber = fiber.return || fiber._hostParent || fiber._currentElement?._owner;
                                depth += 1;
                            }
                            return false;
                        };
                        const header = Array.from(document.querySelectorAll('.am-picker-popup-header-right'))
                            .filter(visible)
                            .reverse()[0];
                        if (header && invokeFrom(header)) return { ok: true, via: 'header-right' };
                        const dialogs = Array.from(document.querySelectorAll('[role="dialog"], .am-picker-popup, .adm-popup, .adm-picker'))
                            .filter(visible)
                            .reverse();
                        for (const dialog of dialogs) {
                            if (invokeFrom(dialog)) return { ok: true, via: 'dialog' };
                        }
                        return { ok: false, via: 'none' };
                    };
                    const closePicker = () => {
                        const headerLeft = Array.from(document.querySelectorAll('.am-picker-popup-header-left, .adm-picker-header-button'))
                            .filter(visible)
                            .reverse()[0];
                        if (headerLeft) return fire(headerLeft);
                        const nodes = Array.from(document.querySelectorAll('[role="dialog"] a, [role="dialog"] button, [role="dialog"] span, .am-picker-popup a, .am-picker-popup button, .am-picker-popup span, .adm-popup button, .adm-popup span'))
                            .filter(visible)
                            .map(el => ({ el, text: norm(el.innerText || el.textContent) }));
                        const cancel = [...nodes].reverse().find(item => item.text === '取消');
                        if (cancel) return fire(cancel.el);
                        const ok = nodes.find(item => /确定|完成|确认/.test(item.text));
                        return ok ? fire(ok.el) : false;
                    };
                    const block = Array.from(document.querySelectorAll('.module-period-picker'))
                        .filter(visible)
                        .find(el => el.querySelector('.date-picker-wrapper.end-picker, .end-picker, .stop-picker'))
                        || document.querySelector('.module-period-picker');
                    const diag = {
                        strategy: 'dom-selector',
                        block_found: !!block,
                        start_target_found: false,
                        end_target_found: false,
                        start_clicked: false,
                        start_injected: false,
                        end_clicked: false,
                        end_injected: false,
                        start_via: '',
                        end_via: '',
                    };
                    if (!block) return diag;
                    const options = Array.from(block.querySelectorAll('.option-item')).filter(visible);
                    const shortOpt = options.find(el => norm(el.innerText || el.textContent) === '短期') || options[0];
                    const longOpt = options.find(el => norm(el.innerText || el.textContent) === '长期') || options[1];
                    if (isLong && longOpt) {
                        fire(longOpt);
                        diag.long_clicked = true;
                        await sleep(300);
                    } else if (shortOpt) {
                        fire(shortOpt);
                        diag.short_clicked = true;
                        await sleep(200);
                    }
                    const clickAndSet = async (kind, selector, value) => {
                        if ((document.body.innerText || '').includes(value)) return { clicked: false, injected: true, visible: true, via: 'already-visible' };
                        const picker = block.querySelector(selector);
                        if (!picker) return { found: false, clicked: false, injected: false, visible: false, via: 'missing-picker' };
                        fire(picker);
                        await sleep(700);
                        const opened = Array.from(document.querySelectorAll('[role="dialog"], .am-picker-popup, .adm-popup, .adm-picker')).some(visible);
                        const injected = injectDate(value);
                        await sleep(250);
                        closePicker();
                        await sleep(500);
                        const finalText = document.body.innerText || '';
                        return { found: true, clicked: opened, injected: !!injected.ok, visible: finalText.includes(value), via: injected.via };
                    };
                    const startResult = await clickAndSet('start', '.date-picker-wrapper.start-picker, .start-picker', start);
                    const endResult = isLong
                        ? { found: !!longOpt, clicked: !!longOpt, injected: true, visible: true, via: 'long-option' }
                        : await clickAndSet('end', '.date-picker-wrapper.end-picker, .end-picker, .stop-picker', end);
                    diag.start_target_found = startResult.found !== false;
                    diag.start_clicked = startResult.clicked;
                    diag.start_injected = startResult.injected;
                    diag.start_visible = startResult.visible;
                    diag.start_via = startResult.via;
                    diag.end_target_found = endResult.found !== false;
                    diag.end_clicked = endResult.clicked;
                    diag.end_injected = endResult.injected;
                    diag.end_visible = endResult.visible;
                    diag.end_via = endResult.via;
                    diag.block_text = norm(block.innerText || block.textContent).slice(0, 160);
                    return diag;
                }""",
                {"start": start, "end": end, "isLong": is_long},
            )
            diagnostics.update(dict(direct or {}))
            if diagnostics.get("start_visible") and (is_long or diagnostics.get("end_visible")):
                return diagnostics
        except Exception as exc:
            diagnostics["dom_selector_error"] = str(exc)[:160]

        async def body_contains(value: str) -> bool:
            try:
                return value in (await page.locator("body").inner_text(timeout=1_000))
            except Exception:
                return False

        block = page.locator(
            "xpath=//*[contains(normalize-space(.),'证件有效期') or contains(normalize-space(.),'证件有效期限')]"
            "/ancestor::*[self::div or self::li][contains(normalize-space(.),'短期') "
            "or contains(normalize-space(.),'长期') or contains(normalize-space(.),'截止日期')][1]"
        ).first
        if not await block.count():
            block = page.locator(
                "xpath=//*[contains(normalize-space(.),'截止日期')]"
                "/ancestor::*[self::div or self::li][contains(normalize-space(.),'证件有效期') "
                "or contains(normalize-space(.),'短期') or contains(normalize-space(.),'长期')][1]"
            ).first
        if not await block.count():
            block = page.locator("body")
        diagnostics["block_found"] = bool(await block.count())

        try:
            if is_long:
                long_opt = block.locator(".option-item").filter(has_text="长期").first
                if await long_opt.count() and await long_opt.is_visible(timeout=500):
                    await long_opt.click(timeout=5_000)
                    diagnostics["long_clicked"] = True
                    await page.wait_for_timeout(250)
            else:
                short_opt = block.locator(".option-item").filter(has_text="短期").first
                if await short_opt.count() and await short_opt.is_visible(timeout=500):
                    await short_opt.click(timeout=5_000)
                    diagnostics["short_clicked"] = True
                    await page.wait_for_timeout(250)
        except Exception:
            pass

        async def click_trigger(label: str, *, prefer_rightmost: bool = False) -> bool:
            relative_class_selectors = (
                ".date-picker-wrapper.start-picker, .start-picker"
                if label == "起始日期"
                else ".date-picker-wrapper.end-picker, .end-picker, .stop-picker"
            )
            global_class_selectors = (
                ".module-period-picker .date-picker-wrapper.start-picker, .module-period-picker .start-picker"
                if label == "起始日期"
                else ".module-period-picker .date-picker-wrapper.end-picker, .module-period-picker .end-picker, .module-period-picker .stop-picker"
            )
            candidates = [
                block.locator(relative_class_selectors),
                page.locator(global_class_selectors),
                block.locator("div").filter(has_text=re.compile(f"^{re.escape(label)}$")),
                block.locator("span").filter(has_text=re.compile(f"^{re.escape(label)}$")),
                page.get_by_text(label, exact=True),
            ]
            for locator in candidates:
                try:
                    count = min(await locator.count(), 20)
                except Exception:
                    continue
                indexes = range(count - 1, -1, -1) if prefer_rightmost else range(count)
                for index in indexes:
                    item = locator.nth(index)
                    try:
                        if not await item.is_visible(timeout=500):
                            continue
                        await item.scroll_into_view_if_needed(timeout=2_000)
                        await item.click(timeout=5_000, force=True)
                        await page.wait_for_timeout(650)
                        if await page.locator("[role='dialog'], .am-picker-popup, .adm-popup, .adm-picker").count():
                            diagnostics[f"{'end' if label == '截止日期' else 'start'}_selector"] = relative_class_selectors
                            return True
                        try:
                            await item.dispatch_event("click")
                            await page.wait_for_timeout(650)
                        except Exception:
                            pass
                        if await page.locator("[role='dialog'], .am-picker-popup, .adm-popup, .adm-picker").count():
                            diagnostics[f"{'end' if label == '截止日期' else 'start'}_selector"] = f"{relative_class_selectors}:dispatch"
                            return True
                        try:
                            await item.evaluate(
                                """el => {
                                    for (const type of ['touchstart', 'mousedown', 'mouseup', 'click', 'touchend']) {
                                        const event = type.startsWith('touch')
                                            ? new Event(type, { bubbles: true, cancelable: true })
                                            : new MouseEvent(type, { bubbles: true, cancelable: true, view: window });
                                        el.dispatchEvent(event);
                                    }
                                    if (typeof el.click === 'function') el.click();
                                }"""
                            )
                            await page.wait_for_timeout(650)
                        except Exception:
                            pass
                        if await page.locator("[role='dialog'], .am-picker-popup, .adm-popup, .adm-picker").count():
                            diagnostics[f"{'end' if label == '截止日期' else 'start'}_selector"] = f"{relative_class_selectors}:evaluate"
                            return True
                    except Exception:
                        continue
            return False

        if not await body_contains(start):
            diagnostics["start_clicked"] = await click_trigger("起始日期")
            if diagnostics["start_clicked"]:
                injected = await _inject_open_picker_date(start)
                diagnostics["start_injected"] = bool(injected.get("ok"))
                diagnostics["start_via"] = injected.get("via")
                await page.wait_for_timeout(250)
                await _close_open_picker()
                await page.wait_for_timeout(500)

        if not is_long and not await body_contains(end):
            diagnostics["end_clicked"] = await click_trigger("截止日期", prefer_rightmost=True)
            if diagnostics["end_clicked"]:
                injected = await _inject_open_picker_date(end)
                diagnostics["end_injected"] = bool(injected.get("ok"))
                diagnostics["end_via"] = injected.get("via")
                await page.wait_for_timeout(250)
                await _close_open_picker()
                await page.wait_for_timeout(500)

        try:
            body_text = await page.locator("body").inner_text(timeout=1_000)
        except Exception:
            body_text = ""
        diagnostics["start_visible"] = start in body_text
        diagnostics["end_visible"] = True if is_long else end in body_text
        return diagnostics

    async def set_h5_period_dates(start: str, end: str) -> None:
        try:
            result = await page.evaluate(
                """async ({ start, end }) => {
                    const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
                    const norm = text => String(text || '').replace(/\\s+/g, '').trim();
                    const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                    const parseYmd = value => {
                        const [year, month, day] = String(value).split('-').map(Number);
                        return { year, month, day };
                    };
                    const fire = el => {
                        if (!el) return false;
                        el.scrollIntoView({ block: 'center', inline: 'center' });
                        for (const type of ['touchstart', 'mousedown', 'mouseup', 'click', 'touchend']) {
                            try {
                                const event = type.startsWith('touch')
                                    ? new Event(type, { bubbles: true, cancelable: true })
                                    : new MouseEvent(type, { bubbles: true, cancelable: true, view: window });
                                el.dispatchEvent(event);
                            } catch (_) {}
                        }
                        return true;
                    };
                    const getFiber = node => {
                        if (!node) return null;
                        const key = Object.keys(node).find(
                            item => item.startsWith('__reactFiber$') || item.startsWith('__reactInternalInstance$')
                        );
                        return key ? node[key] : null;
                    };
                    const callPickerOk = value => {
                        const { year, month, day } = parseYmd(value);
                        const invokeFrom = node => {
                            let fiber = getFiber(node);
                            let depth = 0;
                            while (fiber && depth < 90) {
                                const props = fiber.memoizedProps || fiber.pendingProps || fiber._currentElement?.props;
                                if (props && (typeof props.onOk === 'function' || typeof props.onDateChange === 'function')) {
                                    (props.onOk || props.onDateChange)(new Date(year, month - 1, day));
                                    return true;
                                }
                                fiber = fiber.return || fiber._hostParent || fiber._currentElement?._owner;
                                depth += 1;
                            }
                            return false;
                        };
                        const header = Array.from(document.querySelectorAll('.am-picker-popup-header-right'))
                            .filter(visible)
                            .reverse()[0];
                        if (header && invokeFrom(header)) return true;
                        const dialogs = Array.from(document.querySelectorAll('[role="dialog"], .am-picker-popup, .adm-popup, .adm-picker'))
                            .filter(visible)
                            .reverse();
                        for (const dialog of dialogs) {
                            if (invokeFrom(dialog)) return true;
                        }
                        return false;
                    };
                    const closePicker = () => {
                        const nodes = Array.from(document.querySelectorAll('[role="dialog"] a, [role="dialog"] button, [role="dialog"] span, .am-picker-popup a, .am-picker-popup button, .am-picker-popup span, .adm-popup button, .adm-popup span'))
                            .filter(visible)
                            .map(el => ({ el, text: norm(el.innerText || el.textContent) }));
                        const cancel = [...nodes].reverse().find(item => item.text === '取消');
                        if (cancel) return fire(cancel.el);
                        const ok = nodes.find(item => /确定|完成|确认/.test(item.text));
                        return ok ? fire(ok.el) : false;
                    };
                    const pickTrigger = (block, kind) => {
                        const selectors = kind === 'start'
                            ? ['.date-picker-wrapper.start-picker', 'input[name^="cardPeriod"]:not([name^="cardPeriodEnd"])']
                            : ['.date-picker-wrapper.end-picker', '.date-picker-wrapper.stop-picker', 'input[name^="cardPeriodEnd"]'];
                        for (const selector of selectors) {
                            const target = Array.from(block.querySelectorAll(selector)).filter(visible)[0];
                            if (target) return target;
                        }
                        const exactTexts = kind === 'start' ? ['起始日期', '开始日期'] : ['截止日期', '结束日期'];
                        return Array.from(block.querySelectorAll('div, span, input'))
                            .filter(visible)
                            .find(el => exactTexts.includes(norm(el.innerText || el.textContent || el.value || el.placeholder || el.name)));
                    };
                    const globalTrigger = kind => {
                        const exactTexts = kind === 'start' ? ['起始日期', '开始日期'] : ['截止日期', '结束日期'];
                        const nodes = Array.from(document.querySelectorAll('div, span, input'))
                            .filter(visible)
                            .map(el => {
                                const rect = el.getBoundingClientRect();
                                const text = norm(el.innerText || el.textContent || el.value || el.placeholder || el.name);
                                if (!exactTexts.includes(text)) return null;
                                let score = 0;
                                let parent = el;
                                for (let depth = 0; parent && depth < 8; depth += 1, parent = parent.parentElement) {
                                    const pText = parent.innerText || parent.textContent || '';
                                    if (/证件有效期|证件有效期限/.test(pText)) score += 1000 - depth * 20;
                                    if (/短期|长期/.test(pText)) score += 120;
                                }
                                if (kind === 'end') score += rect.left;
                                else score += Math.max(0, 1000 - rect.left);
                                score += Math.max(0, 1200 - Math.abs(rect.top - window.innerHeight / 2));
                                return { el, score, text, rect: { left: rect.left, top: rect.top, width: rect.width, height: rect.height } };
                            })
                            .filter(Boolean)
                            .sort((a, b) => b.score - a.score);
                        return nodes[0]?.el || null;
                    };
                    const blocks = [];
                    for (const el of Array.from(document.querySelectorAll('.module-period-picker')).filter(visible)) {
                        if (/证件有效期|证件有效期限|起始日期|截止日期|短期|长期/.test(el.innerText || el.textContent || '')) {
                            blocks.push(el);
                        }
                    }
                    for (const label of Array.from(document.querySelectorAll('div, span, li')).filter(visible)) {
                        if (!/证件有效期|证件有效期限/.test(label.innerText || label.textContent || '')) continue;
                        let parent = label;
                        for (let depth = 0; parent && depth < 8; depth += 1, parent = parent.parentElement) {
                            const text = parent.innerText || parent.textContent || '';
                            if (/起始日期|截止日期|短期|长期/.test(text)) {
                                blocks.push(parent);
                                break;
                            }
                        }
                    }
                    const uniqueBlocks = [...new Set(blocks)].sort((a, b) => {
                        const ar = a.getBoundingClientRect();
                        const br = b.getBoundingClientRect();
                        return (br.width * br.height) - (ar.width * ar.height);
                    }).slice(0, 4);
                    const changedKinds = new Set();
                    for (const block of uniqueBlocks) {
                        const shortOpt = Array.from(block.querySelectorAll('.option-item, button, span, div'))
                            .filter(visible)
                            .find(el => norm(el.innerText || el.textContent) === '短期');
                        if (shortOpt) {
                            fire(shortOpt);
                            await sleep(200);
                        }
                        for (const [kind, value] of [['start', start], ['end', end]]) {
                            if (norm(block.innerText || block.textContent).includes(value)) {
                                changedKinds.add(kind);
                                continue;
                            }
                            const trigger = pickTrigger(block, kind) || globalTrigger(kind);
                            if (!trigger) continue;
                            fire(trigger);
                            await sleep(550);
                            if (!callPickerOk(value)) continue;
                            await sleep(250);
                            closePicker();
                            await sleep(500);
                            changedKinds.add(kind);
                        }
                    }
                    for (const [kind, value] of [['start', start], ['end', end]]) {
                        if (changedKinds.has(kind)) continue;
                        const trigger = globalTrigger(kind);
                        if (!trigger) continue;
                        fire(trigger);
                        await sleep(550);
                        if (!callPickerOk(value)) continue;
                        await sleep(250);
                        closePicker();
                        await sleep(500);
                        changedKinds.add(kind);
                    }
                    const bodyText = document.body.innerText || '';
                    return {
                        changed: changedKinds.size,
                        startChanged: changedKinds.has('start') || bodyText.includes(start),
                        endChanged: changedKinds.has('end') || bodyText.includes(end),
                        text: bodyText,
                    };
                }""",
                {"start": start, "end": end},
            )
            text = str((result or {}).get("text") or "")
            if ((result or {}).get("startChanged") or start in text) and ((result or {}).get("endChanged") or end in text):
                filled.append({"text": f"证件有效期起始={start}", "selector": "证件有效期/起始日期"})
                filled.append({"text": f"证件有效期截止={end}", "selector": "证件有效期/截止日期"})
        except Exception:
            return

    async def set_date_by_exact_trigger(trigger_text: str, value: str) -> bool:
        try:
            clicked = await page.evaluate(
                """(triggerText) => {
                    const norm = text => String(text || '').replace(/\\s+/g, '').trim();
                    const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                    const fire = el => {
                        if (!el) return false;
                        el.scrollIntoView({ block: 'center', inline: 'center' });
                        for (const type of ['touchstart', 'mousedown', 'mouseup', 'click', 'touchend']) {
                            try {
                                const event = type.startsWith('touch')
                                    ? new Event(type, { bubbles: true, cancelable: true })
                                    : new MouseEvent(type, { bubbles: true, cancelable: true, view: window });
                                el.dispatchEvent(event);
                            } catch (_) {}
                        }
                        if (typeof el.click === 'function') el.click();
                        return true;
                    };
                    const nodes = Array.from(document.querySelectorAll('div, span, input'))
                        .filter(visible)
                        .map(el => {
                            const text = norm(el.innerText || el.textContent || el.value || el.placeholder || el.name);
                            if (text !== triggerText) return null;
                            const rect = el.getBoundingClientRect();
                            let score = 0;
                            let parent = el;
                            for (let depth = 0; parent && depth < 8; depth += 1, parent = parent.parentElement) {
                                const pText = parent.innerText || parent.textContent || '';
                                if (/证件有效期|证件有效期限/.test(pText)) score += 1000 - depth * 30;
                                if (/短期|长期/.test(pText)) score += 120;
                            }
                            if (/截止|结束/.test(triggerText)) score += rect.left;
                            return { el, score, rect: { left: rect.left, top: rect.top, width: rect.width, height: rect.height } };
                        })
                        .filter(Boolean)
                        .sort((a, b) => b.score - a.score);
                    const chosen = nodes[0]?.el;
                    return chosen ? fire(chosen) : false;
                }""",
                trigger_text,
            )
            if not clicked:
                return False
            await page.wait_for_timeout(600)
            injected = await _inject_open_picker_date(value)
            if not injected.get("ok"):
                return False
            await page.wait_for_timeout(250)
            await page.evaluate(
                """() => {
                    const norm = text => String(text || '').replace(/\\s+/g, '').trim();
                    const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                    const fire = el => {
                        for (const type of ['touchstart', 'mousedown', 'mouseup', 'click', 'touchend']) {
                            try {
                                const event = type.startsWith('touch')
                                    ? new Event(type, { bubbles: true, cancelable: true })
                                    : new MouseEvent(type, { bubbles: true, cancelable: true, view: window });
                                el.dispatchEvent(event);
                            } catch (_) {}
                        }
                        if (typeof el.click === 'function') el.click();
                    };
                    const nodes = Array.from(document.querySelectorAll('[role="dialog"] a, [role="dialog"] button, [role="dialog"] span, .am-picker-popup a, .am-picker-popup button, .am-picker-popup span, .adm-popup button, .adm-popup span'))
                        .filter(visible)
                        .map(el => ({ el, text: norm(el.innerText || el.textContent) }));
                    const cancel = [...nodes].reverse().find(item => item.text === '取消');
                    const ok = nodes.find(item => /确定|完成|确认/.test(item.text));
                    if (cancel) fire(cancel.el);
                    else if (ok) fire(ok.el);
                }"""
            )
            await page.wait_for_timeout(500)
            return True
        except Exception:
            return False

    await sync_card_period_state(start_value, end_value)
    try:
        diagnostics = await asyncio.wait_for(
            set_card_period_by_e2e_pattern(start_value, end_value, id_period_is_long),
            timeout=25,
        )
    except Exception as exc:
        diagnostics = {"error": str(exc)[:160]}
    try:
        body_before = await page.locator("body").inner_text(timeout=1_000)
    except Exception:
        body_before = ""
    if start_value not in body_before:
        try:
            await asyncio.wait_for(set_date_by_exact_trigger("起始日期", start_value), timeout=10)
        except Exception:
            pass
    try:
        body_mid = await page.locator("body").inner_text(timeout=1_000)
    except Exception:
        body_mid = ""
    if not id_period_is_long and end_value not in body_mid:
        try:
            await asyncio.wait_for(set_date_by_exact_trigger("截止日期", end_value), timeout=10)
        except Exception:
            pass
    try:
        body_after_exact = await page.locator("body").inner_text(timeout=1_000)
    except Exception:
        body_after_exact = ""
    if not id_period_is_long and (start_value not in body_after_exact or end_value not in body_after_exact):
        try:
            await asyncio.wait_for(set_h5_period_dates(start_value, end_value), timeout=15)
        except Exception:
            pass
    await sync_card_period_state(start_value, end_value)
    try:
        final_body = await page.locator("body").inner_text(timeout=1_000)
    except Exception:
        final_body = ""
    if id_period_is_long and start_value in final_body and not filled:
        filled.append({"text": f"证件有效期起始={start_value}", "selector": "证件有效期/起始日期"})
        filled.append({"text": "证件有效期截止=长期", "selector": "证件有效期/长期"})
    elif start_value in final_body and end_value in final_body and not filled:
        filled.append({"text": f"证件有效期起始={start_value}", "selector": "证件有效期/起始日期"})
        filled.append({"text": f"证件有效期截止={end_value}", "selector": "证件有效期/截止日期"})
    if diagnostics:
        filled.append({"text": f"证件有效期诊断={json.dumps(diagnostics, ensure_ascii=False)}", "selector": "证件有效期/debug"})
    actions = [
        {
            "text": str(item.get("text") or ""),
            "tag": "field",
            "selector": item.get("selector"),
            "source_url": page.url,
            "target_url": page.url,
            "score": None,
            "click_strategy": "id-validity-default",
            "dismissed_overlays": [],
            "action_type": "minimal_data",
        }
        for item in filled or []
    ]
    return actions


async def _apply_h5_insure_form_data(page: Any, mock_data: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        filled = await page.evaluate(
            """async (mockData) => {
                const records = [];
                const skipPayAccountWrites = true;
                const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
                const norm = text => String(text || '').replace(/\\s+/g, ' ').trim();
                const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                const clickLikeUser = el => {
                    if (!el) return;
                    el.scrollIntoView({ block: 'center', inline: 'center' });
                    for (const type of ['mousedown', 'mouseup', 'click']) {
                        el.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
                    }
                    if (typeof el.click === 'function') el.click();
                };
                const cssSelector = el => {
                    if (!el) return '';
                    if (el.id) return `#${CSS.escape(el.id)}`;
                    if (el.name) return `${el.tagName.toLowerCase()}[name="${CSS.escape(el.name)}"]`;
                    const cls = String(el.className || '').split(/\\s+/).filter(Boolean)[0];
                    return cls ? `${el.tagName.toLowerCase()}.${CSS.escape(cls)}` : el.tagName.toLowerCase();
                };
                const isPayAccountField = (el, label = '') => {
                    const rowText = el?.closest('.am-list-item,.am-list-line,.insure-filed-wrapper,li,dd,label,div')?.innerText || '';
                    const probe = `${el?.name || ''} ${el?.id || ''} ${el?.placeholder || ''} ${el?.getAttribute?.('aria-label') || ''} ${label || ''} ${rowText}`;
                    const accountLike = /payAccount|bankAccount|银行账号|银行卡号|银行账户|开卡信息|账号/i.test(probe);
                    const strongAccountHint = /开卡信息|账号|储蓄卡/i.test(probe);
                    const ownerOnly = /账户名|持卡人/i.test(probe) && !/银行账号|银行卡号|银行账户|开卡信息|账号/i.test(probe);
                    const nonAccountIdentityField = /证件号码|身份证/i.test(probe) && !strongAccountHint;
                    return accountLike && !ownerOnly
                        && !nonAccountIdentityField
                        && !/手机号码|手机号|验证码|短信/i.test(probe);
                };
                const lockPayAccount = (el, value = '') => {
                    if (!el) return;
                    el.dataset.agent3PayAccountLocked = '1';
                    window.__agent3PayAccountLocked = true;
                    window.__agent3PayAccountValue = String(value || el.value || '');
                };
                const shouldSkipPayAccount = (el, label = '', value = '') => {
                    if (!isPayAccountField(el, label)) return false;
                    const current = String(el.value || '').replace(/\\s+/g, '').trim();
                    const expected = String(value || window.__agent3PayAccountValue || '').replace(/\\s+/g, '').trim();
                    if (skipPayAccountWrites) {
                        if (/^\\d{10,30}$/.test(current)) {
                            lockPayAccount(el, current);
                            return true;
                        }
                        return false;
                    }
                    if ((el.dataset.agent3PayAccountLocked === '1' || window.__agent3PayAccountLocked) && /^\\d{10,30}$/.test(current)) return true;
                    if (current && expected && current === expected) {
                        lockPayAccount(el, current);
                        return true;
                    }
                    return false;
                };
                const setValue = (el, value, label) => {
                    if (!el || value == null) return false;
                    const text = String(value);
                    if (shouldSkipPayAccount(el, label, text)) return false;
                    if (el.readOnly) el.removeAttribute('readonly');
                    if (el.disabled) el.removeAttribute('disabled');
                    const setter = Object.getOwnPropertyDescriptor(el.constructor.prototype, 'value')?.set;
                    if (setter) setter.call(el, text);
                    else el.value = text;
                    el.setAttribute('value', text);
                    for (const type of ['input', 'change', 'blur']) el.dispatchEvent(new Event(type, { bubbles: true }));
                    if (isPayAccountField(el, label)) lockPayAccount(el, text);
                    records.push({ text: `${label || 'field'}=${text}`.slice(0, 90), selector: cssSelector(el) });
                    return true;
                };
                const rowRoots = () => Array.from(document.querySelectorAll(
                    '.am-list-item,.am-list-line,.adm-list-item,.adm-list-item-content,.form-item,.form-group,li,dd,label,section,article,div'
                ))
                    .filter(visible)
                    .filter(el => {
                        const text = norm(el.innerText || el.textContent);
                        return text.length >= 2 && text.length <= 260;
                    })
                    .sort((a, b) => {
                        const ar = a.getBoundingClientRect();
                        const br = b.getBoundingClientRect();
                        return Math.abs(ar.top - br.top) > 6 ? ar.top - br.top : ar.left - br.left;
                    });
                const uniqueRows = rows => rows.filter((row, index, list) => list.findIndex(item => item === row || item.contains(row)) === index);
                const rowsByLabel = regex => uniqueRows(rowRoots().filter(row => regex.test(norm(row.innerText || row.textContent))));
                const editableIn = row => Array.from(row.querySelectorAll('input,textarea')).filter(el => {
                    const type = String(el.type || '').toLowerCase();
                    return visible(el) && !['hidden', 'button', 'submit', 'reset', 'file', 'radio', 'checkbox'].includes(type);
                })[0] || null;
                const fillByLabel = (regex, value, occurrence = 0, label = '') => {
                    const rows = rowsByLabel(regex);
                    const row = rows[Math.min(occurrence, Math.max(rows.length - 1, 0))];
                    if (!row) return false;
                    const input = editableIn(row);
                    return setValue(input, value, label || norm(row.innerText).slice(0, 16));
                };
                const allEditable = () => Array.from(document.querySelectorAll('input,textarea')).filter(el => {
                    const type = String(el.type || '').toLowerCase();
                    return visible(el) && !el.disabled && !['hidden', 'button', 'submit', 'reset', 'file', 'radio', 'checkbox'].includes(type);
                });
                const fillEmptyByName = (regex, value, label) => {
                    for (const el of allEditable()) {
                        const probe = `${el.name || ''} ${el.id || ''} ${el.placeholder || ''}`;
                        if (!regex.test(probe) || norm(el.value)) continue;
                        if (setValue(el, value, label)) return true;
                    }
                    return false;
                };
                const fillByPlaceholder = (regex, value, label) => {
                    let changed = false;
                    for (const el of allEditable()) {
                        const probe = `${el.placeholder || ''} ${el.getAttribute('aria-label') || ''}`;
                        if (!regex.test(probe)) continue;
                        changed = setValue(el, value, label) || changed;
                    }
                    return changed;
                };
                const applicantName = mockData['applicant.name'] || '张三';
                const insuredName = mockData['insured.name'] || '李四';
                fillByPlaceholder(/真实姓名|姓名/, applicantName, '投保人姓名');
                fillByPlaceholder(/证件号码/, mockData['applicant.id_no'] || '110101199001011237', '投保人证件号码');
                fillByPlaceholder(/详细地址|联系地址|地址/, mockData['applicant.address'] || '北京市朝阳区测试地址1号', '投保人地址');
                fillByPlaceholder(/真实手机|手机号码|手机号/, mockData['applicant.mobile'] || '13800138000', '投保人手机号');
                fillByPlaceholder(/真实邮箱|邮箱|电子邮箱/i, mockData['applicant.email'] || 'zhangsan@example.com', '投保人邮箱');
                fillByPlaceholder(/账户名须为投保人本人|持卡人/, mockData.cardOwner_107 || applicantName, '持卡人');
                fillByPlaceholder(/开卡信息|储蓄卡|账号/, mockData.payAccount_107 || '6200588435998028938', '银行账号');
                fillByLabel(/姓名|投保人.*姓名/, applicantName, 0, '投保人姓名') || fillEmptyByName(/name/i, applicantName, '投保人姓名');
                fillByLabel(/姓名|被保人.*姓名|被保险人.*姓名/, insuredName, 1, '被保人姓名');
                fillByLabel(/证件号码|身份证号/, mockData['applicant.id_no'] || '110101199001011237', 0, '投保人证件号码') || fillEmptyByName(/cardNumber|id/i, mockData['applicant.id_no'] || '110101199001011237', '投保人证件号码');
                fillByLabel(/证件号码|身份证号/, mockData['insured.id_no'] || '11010120150315123X', 1, '被保人证件号码');
                fillByLabel(/手机号码|手机号|联系电话/, mockData['applicant.mobile'] || '13800138000', 0, '投保人手机号') || fillEmptyByName(/moblie|mobile|phone|tel/i, mockData['applicant.mobile'] || '13800138000', '投保人手机号');
                fillByLabel(/电子邮箱|邮箱|email/i, mockData['applicant.email'] || 'zhangsan@example.com', 0, '投保人邮箱') || fillEmptyByName(/email/i, mockData['applicant.email'] || 'zhangsan@example.com', '投保人邮箱');
                fillByLabel(/联系地址|详细地址|地址/, mockData['applicant.address'] || '北京市朝阳区测试地址1号', 0, '投保人地址');
                fillByLabel(/联系地址|详细地址|地址/, mockData['insured.address'] || mockData['applicant.address'] || '北京市朝阳区测试地址1号', 1, '被保人地址');
                fillByLabel(/持卡人/, mockData.cardOwner_107 || applicantName, 0, '持卡人');
                fillByLabel(/银行账号|银行卡号|卡号/, mockData.payAccount_107 || '6200588435998028938', 0, '银行账号');

                const textNodes = () => Array.from(document.querySelectorAll('button,a,span,div,li,p')).filter(visible);
                const clickExactText = async (texts, root = document) => {
                    const list = Array.isArray(texts) ? texts : [texts];
                    const nodes = Array.from(root.querySelectorAll('button,a,span,div,li,p')).filter(visible);
                    for (const target of list) {
                        const exact = nodes.filter(node => norm(node.innerText || node.textContent) === target);
                        const candidates = exact.length ? exact : nodes.filter(node => norm(node.innerText || node.textContent).includes(target));
                        const chosen = candidates.sort((a, b) => norm(a.innerText).length - norm(b.innerText).length)[0];
                        if (chosen) {
                            clickLikeUser(chosen);
                            await sleep(450);
                            return norm(chosen.innerText || chosen.textContent);
                        }
                    }
                    return null;
                };
                const clickRowSelector = async (labelRegex, occurrence = 0) => {
                    const rows = rowsByLabel(labelRegex);
                    const row = rows[Math.min(occurrence, Math.max(rows.length - 1, 0))];
                    if (!row) return null;
                    const target = Array.from(row.querySelectorAll('input,.input-select,.am-list-extra,.adm-list-item-extra,span,div'))
                        .filter(visible)
                        .reverse()
                        .find(el => /请选择|请选择|$/.test(norm(el.innerText || el.value || el.placeholder || ''))) || row;
                    clickLikeUser(target);
                    await sleep(650);
                    return row;
                };
                const activeModal = () => Array.from(document.querySelectorAll('.am-picker-popup,.am-modal,.adm-popup,.adm-modal,.adm-picker,.layui-layer,.modal,[role="dialog"],body'))
                    .filter(visible)
                    .sort((a, b) => {
                        const ar = a.getBoundingClientRect();
                        const br = b.getBoundingClientRect();
                        return (br.width * br.height) - (ar.width * ar.height);
                    })[0] || document;

                const selectRegionForOccurrence = async occurrence => {
                    const row = await clickRowSelector(/居住省市|省市区|省市|地区/, occurrence);
                    if (!row) return;
                    const modal = activeModal();
                    await clickExactText(['北京市', '北京'], modal);
                    await clickExactText(['北京市', '北京'], modal);
                    await clickExactText(['朝阳区', '东城区', '海淀区'], modal);
                    await clickExactText(['确定', '完成', '确认'], modal);
                    records.push({ text: `居住省市=${occurrence}:北京市 北京市 朝阳区`, selector: cssSelector(row) });
                    await sleep(700);
                };
                await selectRegionForOccurrence(0);
                await selectRegionForOccurrence(1);

                const pickOccupation = async occurrence => {
                    const row = await clickRowSelector(/职业/, occurrence);
                    if (!row) return;
                    const modal = activeModal();
                    const search = Array.from(modal.querySelectorAll('input,textarea')).filter(visible)[0];
                    if (search) {
                        setValue(search, '一般', '职业搜索');
                        await sleep(500);
                    }
                    const picked = await clickExactText(['一般', '一般职业人员', '学生', '一般学生', '内勤'], modal);
                    await clickExactText(['确定', '完成', '确认'], modal);
                    records.push({ text: `职业=${occurrence}:${picked || '一般'}`, selector: cssSelector(row) });
                    await sleep(600);
                };
                await pickOccupation(0);
                await pickOccupation(1);

                const clickAgreementLabelControls = async () => {
                    const rows = Array.from(document.querySelectorAll('label,.am-checkbox-wrapper,.adm-checkbox,.agreement,.protocol'))
                        .filter(visible)
                        .map(el => ({ el, text: norm(el.innerText || el.textContent) }))
                        .filter(item => /本人充分阅读|本人已逐页阅读|阅读、理解并同意|阅读并同意|相关协议|投保条件|保险条款/.test(item.text));
                    let changed = 0;
                    for (const item of rows) {
                        const input = item.el.querySelector?.('input[type="checkbox"]') || item.el.closest?.('label')?.querySelector?.('input[type="checkbox"]');
                        if (!input) continue;
                        if (input && input.checked) continue;
                        const target = item.el.closest?.('label,.am-checkbox-wrapper,.adm-checkbox,.agreement,.protocol') || item.el;
                        clickLikeUser(target);
                        records.push({ text: item.text.slice(0, 80) || '本人充分阅读并同意', selector: cssSelector(target) });
                        await sleep(350);
                        changed += 1;
                        for (let i = 0; i < 4; i += 1) {
                            const clicked = await clickExactText(['已阅读并同意', '我已阅读并同意', '已阅读并确认', '同意', '确定']);
                            if (!clicked) break;
                        }
                    }
                    return changed;
                };
                await clickAgreementLabelControls();

                for (const node of textNodes()) {
                    const text = norm(node.innerText || node.textContent);
                    if (/暂不开启|不开启续期|不自动续费/.test(text) && text.length <= 30) {
                        clickLikeUser(node);
                        records.push({ text: text.slice(0, 80), selector: cssSelector(node) });
                        await sleep(300);
                        break;
                    }
                }
                for (const input of Array.from(document.querySelectorAll('input[type="checkbox"]')).filter(visible)) {
                    if (input.checked) continue;
                    clickLikeUser(input);
                    records.push({ text: '勾选协议', selector: cssSelector(input) });
                    await sleep(250);
                    for (let i = 0; i < 4; i += 1) {
                        const clicked = await clickExactText(['已阅读并同意', '我已阅读并同意', '已阅读并确认', '同意', '确定']);
                        if (!clicked) break;
                    }
                }
                return records;
            }""",
            mock_data,
        )
    except Exception:
        return []
    return [
        {
            "text": str(item.get("text") or ""),
            "tag": "field",
            "selector": item.get("selector"),
            "source_url": page.url,
            "target_url": page.url,
            "score": None,
            "click_strategy": "h5-form-healing",
            "dismissed_overlays": [],
            "action_type": "minimal_data",
        }
        for item in filled or []
    ]


def _identity_text(mock_data: dict[str, Any], *keys: str, default: str = "") -> str:
    for key in keys:
        value = mock_data.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return default


def _format_id_card_date(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text.replace("-", ".").replace("/", ".")


def _split_id_validity(text: str, start: str, end: str) -> tuple[str, str]:
    normalized = str(text or "").replace("至", "-").replace("到", "-")
    if "-" in normalized:
        left, right = normalized.split("-", 1)
        start = left.strip() or start
        end = right.strip() or end
    return _format_id_card_date(start), _format_id_card_date(end)


def _wrap_cjk_text(text: str, max_chars: int) -> list[str]:
    raw = str(text or "").strip()
    if not raw:
        return [""]
    return [raw[index : index + max_chars] for index in range(0, len(raw), max_chars)]


def _resolve_id_card_font(size: int, *, bold: bool = False) -> Any:
    from PIL import ImageFont

    configured = os.environ.get("AGENT3_ID_CARD_FONT_PATH", "").strip()
    candidates = [
        Path(configured) if configured else None,
        Path(r"C:\Windows\Fonts\NotoSansSC-VF.ttf"),
        Path(r"C:\Windows\Fonts\msyhbd.ttc" if bold else r"C:\Windows\Fonts\msyh.ttc"),
        Path(r"C:\Windows\Fonts\simhei.ttf"),
        Path(r"C:\Windows\Fonts\simsun.ttc"),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            if candidate.exists():
                return ImageFont.truetype(str(candidate), size=size)
        except Exception:
            continue
    return ImageFont.load_default()


def _id_card_asset_dir(mock_data: dict[str, Any]) -> Path:
    configured = os.environ.get("AGENT3_ID_CARD_ASSET_DIR", "").strip()
    if configured:
        return Path(configured)
    mock_data_path = os.environ.get("AGENT3_MOCK_DATA_PATH", "").strip()
    if mock_data_path:
        return Path(mock_data_path).resolve().parent / "generated-id-card-assets"
    return Path.cwd() / ".tmp" / "agent3-id-card-assets"


def _generate_mock_id_card_image_paths(mock_data: dict[str, Any] | None) -> tuple[Path, ...]:
    data = mock_data or {}
    name = _identity_text(data, "applicant.name", "insure_form.applicantname")
    id_no = _identity_text(data, "applicant.id_no", "insure_form.applicantidno")
    if not name or not id_no:
        return ()
    gender = _identity_text(data, "applicant.gender", default="男")
    nation = _identity_text(data, "policy_tool.record.民族", default="汉")
    birthdate = _identity_text(data, "applicant.birthdate")
    birth_year, birth_month, birth_day = "", "", ""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", birthdate):
        birth_year, birth_month, birth_day = birthdate.split("-")
    elif len(id_no) >= 14 and id_no[6:14].isdigit():
        birth_year, birth_month, birth_day = id_no[6:10], id_no[10:12], id_no[12:14]
    address = _identity_text(
        data,
        "applicant.certificate_address",
        "policy_tool.record.证件地址",
        "applicant.address",
        default="北京市朝阳区测试地址1号",
    )
    issuer = _identity_text(data, "policy_tool.record.签发机关", default="朝阳区公安局")
    validity_text = _identity_text(data, "applicant.certificate_validity_text", "policy_tool.record.证件有效期")
    valid_start, valid_end = _split_id_validity(
        validity_text,
        _identity_text(data, "applicant.card_valid_start", default="2021-05-16"),
        _identity_text(data, "applicant.card_valid_end", default="2041-05-16"),
    )
    fingerprint_payload = json.dumps(
        {
            "name": name,
            "id_no": id_no,
            "gender": gender,
            "nation": nation,
            "birthdate": birthdate,
            "address": address,
            "issuer": issuer,
            "valid_start": valid_start,
            "valid_end": valid_end,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    fingerprint = hashlib.sha1(fingerprint_payload.encode("utf-8")).hexdigest()[:16]
    asset_dir = _id_card_asset_dir(data) / fingerprint
    front_path = asset_dir / "id-card-front.jpg"
    back_path = asset_dir / "id-card-back.jpg"
    if front_path.exists() and back_path.exists():
        return (front_path, back_path)
    try:
        from PIL import Image, ImageDraw

        asset_dir.mkdir(parents=True, exist_ok=True)
        label_font = _resolve_id_card_font(26, bold=True)
        value_font = _resolve_id_card_font(28)
        value_small_font = _resolve_id_card_font(24)
        number_font = _resolve_id_card_font(30, bold=True)
        title_font = _resolve_id_card_font(42, bold=True)
        small_font = _resolve_id_card_font(22)

        front = Image.new("RGB", (856, 540), (246, 252, 255))
        draw = ImageDraw.Draw(front)
        draw.rounded_rectangle((18, 18, 838, 522), radius=28, fill=(248, 253, 255), outline=(183, 214, 230), width=3)
        for y in range(24, 522, 16):
            draw.line((24, y, 832, y + 6), fill=(235, 247, 252), width=2)
        portrait_box = (604, 104, 780, 330)
        draw.rounded_rectangle(portrait_box, radius=8, fill=(226, 235, 239), outline=(166, 188, 199), width=2)
        draw.ellipse((654, 138, 730, 214), fill=(185, 151, 122), outline=(120, 100, 86), width=2)
        draw.polygon([(620, 330), (690, 230), (766, 330)], fill=(45, 84, 128))
        draw.rectangle((645, 278, 742, 330), fill=(238, 238, 238))
        draw.text((644, 348), "测试影像", fill=(126, 144, 153), font=small_font)

        draw.text((74, 78), "姓名", fill=(34, 65, 104), font=label_font)
        draw.text((166, 76), name, fill=(10, 26, 45), font=value_font)
        draw.text((74, 134), "性别", fill=(34, 65, 104), font=label_font)
        draw.text((166, 132), gender, fill=(10, 26, 45), font=value_font)
        draw.text((274, 134), "民族", fill=(34, 65, 104), font=label_font)
        draw.text((358, 132), nation, fill=(10, 26, 45), font=value_font)
        draw.text((74, 190), "出生", fill=(34, 65, 104), font=label_font)
        draw.text((166, 188), birth_year, fill=(10, 26, 45), font=value_font)
        draw.text((250, 190), "年", fill=(34, 65, 104), font=label_font)
        draw.text((296, 188), birth_month, fill=(10, 26, 45), font=value_font)
        draw.text((344, 190), "月", fill=(34, 65, 104), font=label_font)
        draw.text((390, 188), birth_day, fill=(10, 26, 45), font=value_font)
        draw.text((438, 190), "日", fill=(34, 65, 104), font=label_font)
        draw.text((74, 248), "住址", fill=(34, 65, 104), font=label_font)
        for line_index, line in enumerate(_wrap_cjk_text(address, 18)[:4]):
            draw.text((166, 246 + line_index * 35), line, fill=(10, 26, 45), font=value_small_font)
        draw.text((74, 442), "公民身份号码", fill=(34, 65, 104), font=label_font)
        draw.text((266, 438), id_no, fill=(10, 26, 45), font=number_font)
        front.save(front_path, quality=95)

        back = Image.new("RGB", (856, 540), (246, 252, 255))
        draw = ImageDraw.Draw(back)
        draw.rounded_rectangle((18, 18, 838, 522), radius=28, fill=(248, 253, 255), outline=(183, 214, 230), width=3)
        draw.ellipse((358, 74, 498, 214), fill=(196, 38, 43), outline=(142, 22, 24), width=4)
        draw.polygon([(428, 96), (446, 142), (494, 142), (455, 170), (470, 216), (428, 188), (386, 216), (401, 170), (362, 142), (410, 142)], fill=(246, 205, 70))
        draw.text((232, 256), "中华人民共和国", fill=(170, 35, 38), font=title_font)
        draw.text((312, 316), "居民身份证", fill=(170, 35, 38), font=title_font)
        draw.text((116, 410), "签发机关", fill=(34, 65, 104), font=label_font)
        draw.text((260, 408), issuer, fill=(10, 26, 45), font=value_font)
        draw.text((116, 466), "有效期限", fill=(34, 65, 104), font=label_font)
        draw.text((260, 464), f"{valid_start}-{valid_end}", fill=(10, 26, 45), font=value_font)
        back.save(back_path, quality=95)
    except Exception:
        return ()
    return (front_path, back_path)


def _resolve_id_card_image_paths(mock_data: dict[str, Any] | None = None) -> tuple[Path, ...]:
    configured_front = os.environ.get("AGENT3_ID_CARD_FRONT_IMAGE_PATH", "").strip()
    configured_back = os.environ.get("AGENT3_ID_CARD_BACK_IMAGE_PATH", "").strip()
    configured_single = os.environ.get("AGENT3_ID_CARD_IMAGE_PATH", "").strip()
    configured: list[Path] = []
    if configured_front:
        configured.append(Path(configured_front))
    if configured_back:
        configured.append(Path(configured_back))
    if configured:
        usable = tuple(path for path in configured if path.exists() and path.is_file())
        if usable:
            return usable
    if configured_single:
        single = Path(configured_single)
        if single.exists() and single.is_file():
            return (single,)
    generated = _generate_mock_id_card_image_paths(mock_data)
    if generated:
        return generated
    return tuple(path for path in _DEFAULT_ID_CARD_IMAGE_PATHS if path.exists() and path.is_file())


def _resolve_id_card_image_path() -> Path | None:
    configured = os.environ.get("AGENT3_ID_CARD_IMAGE_PATH")
    candidates = [Path(configured)] if configured else []
    candidates.extend(_DEFAULT_ID_CARD_IMAGE_PATHS)
    for candidate in candidates:
        try:
            if candidate and candidate.exists() and candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


def _minimal_action_record(
    page: Any,
    *,
    text: str,
    tag: str = "button",
    selector: str | None = None,
    source_url: str | None = None,
    click_strategy: str,
    action_type: str = "minimal_data",
    submit_diagnostics: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    record = {
        "text": text,
        "tag": tag,
        "selector": selector,
        "source_url": source_url or getattr(page, "url", ""),
        "target_url": getattr(page, "url", ""),
        "score": None,
        "click_strategy": click_strategy,
        "dismissed_overlays": [],
        "action_type": action_type,
    }
    if submit_diagnostics:
        record["submit_diagnostics"] = submit_diagnostics
    return record


def _submit_trace_blocker_action(page: Any, reason: str, *, source_url: str | None = None) -> dict[str, Any]:
    return _minimal_action_record(
        page,
        text=reason,
        tag="xhr",
        selector="agent3-submit-trace",
        source_url=source_url,
        click_strategy=_submit_trace_blocker_strategy(reason),
    )


def _submit_trace_response_items(page: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    trace = getattr(page, "_agent3_submit_trace", None)
    if isinstance(trace, dict) and isinstance(trace.get("responses"), list):
        items.extend(item for item in trace.get("responses", []) if isinstance(item, dict))
    run_dir = os.environ.get("AGENT3_RUN_DIR", "").strip()
    if run_dir:
        for filename in ("api-trace.jsonl", "api-errors.jsonl"):
            path = Path(run_dir) / filename
            if not path.exists():
                continue
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except Exception:
                continue
            for line in lines[-240:]:
                try:
                    payload = json.loads(line)
                except Exception:
                    continue
                if not isinstance(payload, dict):
                    continue
                if str(payload.get("event") or "") not in {"response", ""}:
                    continue
                items.append(payload)
    return items


def _submit_trace_order_handoff_action(page: Any, *, source_url: str | None = None) -> dict[str, Any] | None:
    source_url = source_url or str(getattr(page, "url", "") or "")
    responses = _submit_trace_response_items(page)
    if not responses:
        return None
    for item in reversed(responses):
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "")
        if "/api/apps/cps/insure/submit" not in url:
            continue
        try:
            body = json.loads(str(item.get("body") or ""))
        except Exception:
            continue
        if not isinstance(body, dict):
            continue
        data = body.get("data") if isinstance(body.get("data"), dict) else {}
        code = str(body.get("code") or body.get("errorCode") or data.get("errorCode") or "")
        task_handoff = code == "37009" and bool(data.get("insureNum")) and isinstance(data.get("insureTaskList"), list)
        direct_order = code == "0" and bool(data.get("insureNum") or data.get("encryptInsureNum"))
        suitability_task = code == "40015" and bool(data.get("encryptInsureNum") or _encrypt_insure_num_from_url(source_url))
        order_generated = task_handoff or direct_order or suitability_task
        if not order_generated:
            continue
        encrypt_insure_num = str(data.get("encryptInsureNum") or _encrypt_insure_num_from_url(source_url) or "").strip()
        target_url = source_url
        if encrypt_insure_num:
            leaf = "adapt/loading" if suitability_task else "task"
            target_url = _agent3_product_flow_url(source_url, leaf, encrypt_insure_num)
        result = {
            "attempted": True,
            "order_generated": True,
            "task_handoff": task_handoff,
            "direct_order": direct_order,
            "suitability_task": suitability_task,
            "status": int(item.get("status") or 0),
            "ok": 200 <= int(item.get("status") or 0) < 300,
            "url": url,
            "code": code,
            "msg": body.get("msg") or data.get("errorMessage") or "",
            "response_order": {
                "insureNum": data.get("insureNum") or "",
                "encryptInsureNum": encrypt_insure_num,
                "taskCount": len(data.get("insureTaskList") or []) if isinstance(data.get("insureTaskList"), list) else 0,
            },
            "body_excerpt": str(item.get("body") or "")[:2000],
        }
        return {
            "text": (
                "submit trace order handoff: "
                f"code={code}, order_generated=True, task_handoff={task_handoff}, "
                f"direct_order={direct_order}, suitability_task={suitability_task}"
            ),
            "tag": "xhr",
            "selector": "/api/apps/cps/insure/submit",
            "source_url": source_url,
            "target_url": target_url,
            "score": None,
            "click_strategy": "submit-trace-order-handoff",
            "dismissed_overlays": [],
            "action_type": "submit_api",
            "planned_from_node_id": "NODE-insure-form",
            "planned_to_node_id": "NODE-suitability" if suitability_task else "NODE-underwriting",
            "submit_api_result": result,
        }
    return None


async def _click_auth_button(page: Any, texts: tuple[str, ...], *, strategy: str) -> dict[str, Any] | None:
    if not hasattr(page, "locator"):
        return None
    source_url = page.url
    diagnostics: list[dict[str, Any]] = []
    primary_button_query = (
        "button, a, [role='button'], .am-button, .adm-button, .btn, [class*='btn'], "
        "input[type='button'], input[type='submit']"
    )
    fallback_button_query = "div, span"
    for text in texts:
        locators = []
        for query in (primary_button_query, fallback_button_query):
            try:
                locator = page.locator(query).filter(has_text=text)
                count = min(await locator.count(), 20)
                locators.extend(locator.nth(index) for index in range(count - 1, -1, -1))
            except Exception:
                pass
            if locators:
                break
        for locator in locators:
            try:
                if not await locator.is_visible(timeout=500):
                    continue
                try:
                    await locator.scroll_into_view_if_needed(timeout=1_000)
                except Exception:
                    pass
                if _looks_like_submit_action_text(text):
                    before = await _capture_submit_diagnostics(
                        page,
                        phase="before-auth-click",
                        action_text=text,
                    )
                    if before:
                        diagnostics.append(before)
                await locator.click(timeout=3_000, no_wait_after=True, force=True)
                await page.wait_for_timeout(1_500)
                if _looks_like_submit_action_text(text):
                    after = await _capture_submit_diagnostics(
                        page,
                        phase="after-auth-click",
                        action_text=text,
                    )
                    if after:
                        diagnostics.append(after)
                return _minimal_action_record(
                    page,
                    text=text,
                    source_url=source_url,
                    selector=None,
                    click_strategy=strategy,
                    submit_diagnostics=diagnostics,
                )
            except Exception:
                continue
    try:
        clicked = await page.evaluate(
            """(texts) => {
                const wanted = texts.map(text => String(text || '').replace(/\\s+/g, ''));
                const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                const norm = text => String(text || '').replace(/\\s+/g, '').trim();
                const fire = el => {
                    el.scrollIntoView({ block: 'center', inline: 'center' });
                    for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                        try {
                            el.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
                        } catch (_) {}
                    }
                    if (typeof el.click === 'function') el.click();
                };
                const nodes = Array.from(document.querySelectorAll('button,a,[role=button],.am-button,.adm-button,.btn,[class*=btn],input[type=button],input[type=submit],div,span'))
                    .filter(visible)
                    .map((el, index) => {
                        const text = norm(el.innerText || el.textContent || el.value || el.getAttribute('aria-label'));
                        const matchedIndex = wanted.findIndex(item => item && (text === item || text.includes(item)));
                        if (matchedIndex < 0) return null;
                        const rect = el.getBoundingClientRect();
                        const cls = String(el.className || '');
                        let score = 1000 - matchedIndex * 10 - index / 1000;
                        if (/am-button|adm-button|primary|btn|button/.test(cls)) score += 500;
                        if (['A', 'BUTTON', 'INPUT'].includes(el.tagName)) score += 300;
                        if (rect.width > 40 && rect.height > 20) score += 100;
                        return { el, text, score };
                    })
                    .filter(Boolean)
                    .sort((a, b) => b.score - a.score);
                const chosen = nodes[0];
                if (!chosen) return null;
                fire(chosen.el);
                return { text: chosen.text };
            }""",
            list(texts),
        )
        if clicked:
            await page.wait_for_timeout(1_500)
            return _minimal_action_record(
                page,
                text=str(clicked.get("text") or texts[0]),
                source_url=source_url,
                click_strategy=f"{strategy}-js",
            )
    except Exception:
        pass
    return None


async def _auth_file_input_count(page: Any) -> int:
    if not hasattr(page, "locator"):
        return 0
    try:
        return int(await page.locator("input[type='file']").count())
    except Exception:
        return 0


async def _wait_for_auth_file_inputs(page: Any, timeout_ms: int = 30_000) -> int:
    deadline = time.monotonic() + timeout_ms / 1000
    count = await _auth_file_input_count(page)
    while count <= 0 and time.monotonic() < deadline:
        try:
            await page.wait_for_timeout(500)
        except Exception:
            await asyncio.sleep(0.5)
        count = await _auth_file_input_count(page)
    return count


async def _wait_for_toast_closed(page: Any, timeout_ms: int = 6_000) -> None:
    if not hasattr(page, "locator"):
        return
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        try:
            toast = page.locator(".am-toast, .adm-toast, [class*='toast']")
            count = min(await toast.count(), 10)
            visible = False
            for index in range(count):
                try:
                    if await toast.nth(index).is_visible(timeout=250):
                        visible = True
                        break
                except Exception:
                    continue
            if not visible:
                return
        except Exception:
            return
        try:
            await page.wait_for_timeout(300)
        except Exception:
            await asyncio.sleep(0.3)


async def _direct_verify_auth_sms(page: Any, sms_token: str | None = None) -> dict[str, Any] | None:
    if not hasattr(page, "evaluate"):
        return None
    try:
        result = await page.evaluate(
            """async ({ smsToken }) => {
                const currentUrl = new URL(location.href);
                const encryptInsureNum = currentUrl.searchParams.get('encryptInsureNum') || '';
                const headers = { 'content-type': 'application/json;charset=UTF-8' };
                const findKey = (root, key, depth = 0) => {
                    if (!root || depth > 8) return undefined;
                    if (Array.isArray(root)) {
                        for (const item of root) {
                            const found = findKey(item, key, depth + 1);
                            if (found !== undefined && found !== null && found !== '') return found;
                        }
                        return undefined;
                    }
                    if (typeof root !== 'object') return undefined;
                    if (Object.prototype.hasOwnProperty.call(root, key)) return root[key];
                    for (const value of Object.values(root)) {
                        const found = findKey(value, key, depth + 1);
                        if (found !== undefined && found !== null && found !== '') return found;
                    }
                    return undefined;
                };
                const merchantId = Number(findKey(window.__NEXT_DATA__, 'merchantId') || 1000014);
                const postJson = async (path, payload) => {
                    const response = await fetch(`${path}?md=${Math.random()}`, {
                        method: 'POST',
                        credentials: 'include',
                        headers,
                        body: JSON.stringify(payload),
                    });
                    const text = await response.text();
                    let json = null;
                    try { json = text ? JSON.parse(text) : null; } catch (_) {}
                    return { status: response.status, text, json };
                };
                const list = await postJson('/api/apps/cps/insure/task/approve/list', { encryptInsureNum });
                const masters = list.json?.data?.approveMasterVoList || [];
                const master = masters.find(item => item && item.approveStatus !== 2) || masters[0] || {};
                const encryptMasterId = master.encryptMasterId || findKey(window.__NEXT_DATA__, 'encryptMasterId') || '';
                if (!smsToken) {
                    const sent = await postJson('/api/apps/cps/insure/task/approve/insuredSms/send', {
                        authType: 2,
                        suitFor: 1,
                        encryptInsureNum,
                        encryptMasterId,
                        merchantId,
                    });
                    smsToken = sent.json?.data || '';
                }
                const verify = await postJson('/api/apps/cps/insure/task/approve/insuredSms/verify', {
                    authType: 2,
                    suitFor: 1,
                    encryptInsureNum,
                    encryptMasterId,
                    merchantId,
                    smsToken,
                    verifyCode: '1111',
                });
                return { list, verify, smsToken, encryptMasterId, encryptInsureNum, ok: verify.json?.code === 0 };
            }""",
            {"smsToken": sms_token or ""},
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}
    return result if isinstance(result, dict) else None


def _start_api_response_waiter(page: Any, path_fragment: str, *, timeout_ms: int = 30_000) -> Any | None:
    if not hasattr(page, "wait_for_response"):
        return None
    try:
        return asyncio.create_task(
            page.wait_for_response(
                lambda response: path_fragment in response.url and response.status < 500,
                timeout=timeout_ms,
            )
        )
    except Exception:
        return None


async def _record_api_waiter_result(
    page: Any,
    waiter: Any | None,
    *,
    text: str,
    selector: str,
    source_url: str,
    click_strategy: str,
) -> dict[str, Any] | None:
    if waiter is None:
        return None
    trace_blocker_reason = _submit_trace_blocker_reason(page)
    if trace_blocker_reason:
        try:
            waiter.cancel()
        except Exception:
            pass
        return _submit_trace_blocker_action(page, trace_blocker_reason, source_url=source_url)
    try:
        if hasattr(waiter, "done"):
            while not waiter.done():
                trace_blocker_reason = _submit_trace_blocker_reason(page)
                if trace_blocker_reason:
                    try:
                        waiter.cancel()
                    except Exception:
                        pass
                    return _submit_trace_blocker_action(page, trace_blocker_reason, source_url=source_url)
                await asyncio.wait({waiter}, timeout=0.5)
        response = await waiter
    except Exception as exc:
        trace_blocker_reason = _submit_trace_blocker_reason(page)
        if trace_blocker_reason:
            return _submit_trace_blocker_action(page, trace_blocker_reason, source_url=source_url)
        return _minimal_action_record(
            page,
            text=f"{text}: wait-timeout {str(exc)[:80]}",
            tag="xhr",
            selector=selector,
            source_url=source_url,
            click_strategy=f"{click_strategy}-timeout",
        )
    payload: Any = None
    body = ""
    try:
        payload = await response.json()
    except Exception:
        try:
            body = (await response.text())[:1000]
        except Exception:
            body = ""
    summary = ""
    if isinstance(payload, dict):
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        parts = [
            f"code={payload.get('code')}",
            f"success={payload.get('success')}",
        ]
        if data:
            for key in ("taskType", "taskStatus", "isNext", "canPay"):
                if key in data:
                    parts.append(f"{key}={data.get(key)}")
        msg = payload.get("msg") or payload.get("message")
        if msg:
            parts.append(f"msg={msg}")
        summary = ", ".join(parts)
    elif body:
        summary = f"body={body[:180]}"
    else:
        summary = f"status={getattr(response, 'status', '')}"
    return _minimal_action_record(
        page,
        text=f"{text}: {summary}",
        tag="xhr",
        selector=selector,
        source_url=source_url,
        click_strategy=click_strategy,
    )


async def _wait_after_task_next_click(
    page: Any,
    waiter: Any | None,
    *,
    source_url: str,
    click_strategy: str,
    timeout_ms: int = 45_000,
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    response_record = await _record_api_waiter_result(
        page,
        waiter,
        text="后续任务接口",
        selector="/api/apps/cps/insure/task/next/do",
        source_url=source_url,
        click_strategy=click_strategy,
    )
    if response_record:
        actions.append(response_record)
        standard_actions = await _poll_standard_underwriting_task(
            page,
            response_record=response_record,
            source_url=source_url,
        )
        if standard_actions:
            actions.extend(standard_actions)
            return actions
    trace_blocker_reason = _submit_trace_blocker_reason(page)
    if trace_blocker_reason:
        actions.append(_submit_trace_blocker_action(page, trace_blocker_reason, source_url=source_url))
        return actions
    await _wait_for_page_flow_settled(
        page,
        previous_url=source_url,
        timeout_ms=timeout_ms,
        min_wait_ms=1_200,
    )
    trace_blocker_reason = _submit_trace_blocker_reason(page)
    if trace_blocker_reason:
        actions.append(_submit_trace_blocker_action(page, trace_blocker_reason, source_url=source_url))
        return actions
    deadline = time.monotonic() + timeout_ms / 1000
    last_probe: tuple[str, str] | None = None
    stable_count = 0
    while time.monotonic() < deadline:
        try:
            body_text = await _body_text_full(page)
        except Exception:
            body_text = ""
        current_url = str(getattr(page, "url", ""))
        if _bank_sign_hint(current_url, body_text) or _looks_like_payment_page(
            {"url": current_url, "title": "", "body_text_excerpt": body_text[:1200]}
        ):
            break
        trace_blocker_reason = _submit_trace_blocker_reason(page)
        if trace_blocker_reason:
            actions.append(_submit_trace_blocker_action(page, trace_blocker_reason, source_url=source_url))
            return actions
        probe = (current_url, hashlib.sha1(body_text[:4000].encode("utf-8", "ignore")).hexdigest())
        if probe == last_probe:
            stable_count += 1
            if stable_count >= 2:
                break
        else:
            last_probe = probe
            stable_count = 1
        try:
            await page.wait_for_timeout(800)
        except Exception:
            await asyncio.sleep(0.8)
    return actions


def _record_indicates_standard_underwriting_wait(record: dict[str, Any] | None) -> bool:
    if not isinstance(record, dict):
        return False
    text = str(record.get("text") or "")
    if "taskType=4" not in text:
        return False
    return any(token in text for token in ("code=41011", "code=41001", "code=37002", "系统错误", "智能核保"))


def _record_indicates_standard_underwriting_backend_error(record: dict[str, Any] | None) -> bool:
    if not isinstance(record, dict):
        return False
    text = str(record.get("text") or "")
    return "taskType=4" in text and "code=41011" in text


def _result_indicates_standard_underwriting_wait(result: dict[str, Any] | None) -> bool:
    if not isinstance(result, dict):
        return False
    payload = result.get("json")
    if not isinstance(payload, dict):
        return False
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    if str(data.get("taskType") or "") != "4":
        return False
    code = str(payload.get("code") or "")
    msg = str(payload.get("msg") or payload.get("message") or "")
    return code in {"41011", "41001", "37002"} or "系统错误" in msg or "智能核保" in msg


def _result_indicates_standard_underwriting_backend_error(result: dict[str, Any] | None) -> bool:
    if not isinstance(result, dict):
        return False
    payload = result.get("json")
    if not isinstance(payload, dict):
        return False
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    return str(data.get("taskType") or "") == "4" and str(payload.get("code") or "") == "41011"


def _result_is_bank_sign_task(result: dict[str, Any] | None) -> bool:
    if not isinstance(result, dict):
        return False
    payload = result.get("json")
    if not isinstance(payload, dict):
        return False
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    code = str(payload.get("code"))
    return (payload.get("success") is True or code == "0") and str(data.get("taskType") or "") == "3"


async def _direct_next_task(page: Any) -> dict[str, Any] | None:
    if not hasattr(page, "evaluate"):
        return None
    try:
        result = await page.evaluate(
            """async () => {
                const headers = { 'content-type': 'application/json;charset=UTF-8' };
                const currentUrl = new URL(location.href);
                const findKey = (root, key, depth = 0) => {
                    if (!root || depth > 9) return undefined;
                    if (Array.isArray(root)) {
                        for (const item of root) {
                            const found = findKey(item, key, depth + 1);
                            if (found !== undefined && found !== null && found !== '') return found;
                        }
                        return undefined;
                    }
                    if (typeof root !== 'object') return undefined;
                    if (Object.prototype.hasOwnProperty.call(root, key)) return root[key];
                    for (const value of Object.values(root)) {
                        const found = findKey(value, key, depth + 1);
                        if (found !== undefined && found !== null && found !== '') return found;
                    }
                    return undefined;
                };
                const nextData = window.__NEXT_DATA__ || {};
                const store = window.__NEXT_REDUX_STORE__ || window.store || window.reduxStore;
                let redux = {};
                try {
                    if (store && typeof store.getState === 'function') {
                        const raw = store.getState();
                        redux = raw && typeof raw.toJS === 'function' ? raw.toJS() : raw;
                    }
                } catch (_) {}
                const encryptInsureNum = currentUrl.searchParams.get('encryptInsureNum')
                    || findKey(nextData, 'encryptInsureNum')
                    || findKey(redux, 'encryptInsureNum')
                    || '';
                const merchantId = Number(findKey(nextData, 'merchantId') || findKey(redux, 'merchantId') || 1000014);
                const parts = location.pathname.split('/').filter(Boolean);
                const basePrefix = parts.length >= 4 ? `/${parts.slice(0, 4).join('/')}` : '/m/apps/cps/demo-channel';
                const taskUrl = `${location.origin}${basePrefix}/product/task?encryptInsureNum=${encodeURIComponent(encryptInsureNum)}`;
                const insureUrl = `${location.origin}${basePrefix}/product/insure?encryptInsureNum=${encodeURIComponent(encryptInsureNum)}`;
                const response = await fetch(`/api/apps/cps/insure/task/next/do?md=${Math.random()}`, {
                    method: 'POST',
                    credentials: 'include',
                    headers,
                    body: JSON.stringify({ encryptInsureNum, merchantId, taskUrl, insureUrl }),
                });
                const text = await response.text();
                let json = null;
                try { json = text ? JSON.parse(text) : null; } catch (_) {}
                return { status: response.status, text, json, encryptInsureNum };
            }"""
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:500]}
    return result if isinstance(result, dict) else None


def _next_task_summary(result: dict[str, Any] | None) -> str:
    if not isinstance(result, dict):
        return "no-result"
    payload = result.get("json")
    if not isinstance(payload, dict):
        return f"status={result.get('status')} body={str(result.get('text') or '')[:120]}"
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    parts = [
        f"code={payload.get('code')}",
        f"success={payload.get('success')}",
    ]
    if data:
        for key in ("taskType", "taskStatus", "isNext", "canPay"):
            if key in data:
                parts.append(f"{key}={data.get(key)}")
    msg = payload.get("msg") or payload.get("message")
    if msg:
        parts.append(f"msg={msg}")
    return ", ".join(parts)


async def _probe_standard_underwriting_after_auth(
    page: Any,
    *,
    source_url: str,
    click_strategy: str,
) -> list[dict[str, Any]]:
    trace_blocker_reason = _submit_trace_blocker_reason(page)
    if trace_blocker_reason:
        return [_submit_trace_blocker_action(page, trace_blocker_reason, source_url=source_url)]
    result = await _direct_next_task(page)
    summary = _next_task_summary(result)
    action = _minimal_action_record(
        page,
        text=f"standard-underwriting-probe: {summary}",
        tag="xhr",
        selector="/api/apps/cps/insure/task/next/do",
        source_url=source_url,
        click_strategy=click_strategy,
    )
    actions = [action]
    trace_blocker_reason = _submit_trace_blocker_reason(page)
    if trace_blocker_reason:
        actions.append(_submit_trace_blocker_action(page, trace_blocker_reason, source_url=source_url))
        return actions
    if _result_is_bank_sign_task(result):
        actions.extend(await _apply_bank_sign_task_data(page, force=True))
        return actions
    if _result_indicates_standard_underwriting_wait(result):
        actions.extend(
            await _poll_standard_underwriting_task(
                page,
                response_record=action,
                source_url=source_url,
            )
        )
    return actions


async def _poll_standard_underwriting_task(
    page: Any,
    *,
    response_record: dict[str, Any] | None,
    source_url: str,
) -> list[dict[str, Any]]:
    if not _record_indicates_standard_underwriting_wait(response_record):
        return []

    actions: list[dict[str, Any]] = []
    trace_blocker_reason = _submit_trace_blocker_reason(page)
    if trace_blocker_reason:
        return [_submit_trace_blocker_action(page, trace_blocker_reason, source_url=source_url)]
    deadline = time.monotonic() + 300
    attempt = 0
    backend_error_count = 1 if _record_indicates_standard_underwriting_backend_error(response_record) else 0
    while time.monotonic() < deadline:
        trace_blocker_reason = _submit_trace_blocker_reason(page)
        if trace_blocker_reason:
            actions.append(_submit_trace_blocker_action(page, trace_blocker_reason, source_url=source_url))
            return actions
        attempt += 1
        try:
            await page.wait_for_timeout(12_000)
        except Exception:
            await asyncio.sleep(12)
        trace_blocker_reason = _submit_trace_blocker_reason(page)
        if trace_blocker_reason:
            actions.append(_submit_trace_blocker_action(page, trace_blocker_reason, source_url=source_url))
            return actions
        result = await _direct_next_task(page)
        summary = _next_task_summary(result)
        actions.append(
            _minimal_action_record(
                page,
                text=f"standard-underwriting-poll-{attempt}: {summary}",
                tag="xhr",
                selector="/api/apps/cps/insure/task/next/do",
                source_url=source_url,
                click_strategy="standard-underwriting-wait",
            )
        )
        trace_blocker_reason = _submit_trace_blocker_reason(page)
        if trace_blocker_reason:
            actions.append(_submit_trace_blocker_action(page, trace_blocker_reason, source_url=source_url))
            return actions
        payload = result.get("json") if isinstance(result, dict) else None
        if not isinstance(payload, dict):
            continue
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        code = str(payload.get("code"))
        task_type = str(data.get("taskType") or "")
        msg = str(payload.get("msg") or payload.get("message") or "")
        if (payload.get("success") is True or code == "0") and task_type == "3":
            actions.extend(await _apply_bank_sign_task_data(page, force=True))
            return actions
        if payload.get("success") is True or code == "0":
            await _wait_for_page_flow_settled(page, previous_url=source_url, timeout_ms=60_000, min_wait_ms=1_200)
            return actions
        if _result_indicates_standard_underwriting_backend_error(result):
            backend_error_count += 1
            if backend_error_count >= 2:
                actions.append(
                    _minimal_action_record(
                        page,
                        text=f"standard-underwriting-backend-blocked: {summary}",
                        tag="xhr",
                        selector="/api/apps/cps/insure/task/next/do",
                        source_url=source_url,
                        click_strategy="standard-underwriting-backend-blocked",
                    )
                )
                return actions
            continue
        if task_type == "4" and (code in {"41011", "41001", "37002"} or "系统错误" in msg or "智能核保" in msg):
            continue
        if code in {"41011", "41001", "37002"}:
            continue
        return actions
    actions.append(
        _minimal_action_record(
            page,
            text="standard-underwriting-poll-timeout",
            tag="xhr",
            selector="/api/apps/cps/insure/task/next/do",
            source_url=source_url,
            click_strategy="standard-underwriting-timeout",
        )
    )
    return actions


def _bank_sign_hint(url: str, body_text: str) -> bool:
    path = urlparse(str(url or "")).path
    if re.search(r"/authentication(?:/|$)", path):
        return False
    return any(
        token in str(body_text or "")
        for token in (
            "银行卡签约",
            "银行签约",
            "短信验证",
            "手机号验证",
            "签约失败",
            "签约授权书",
            "发送验证码",
            "验证码已发送",
            "银行代扣",
        )
    )


async def _bank_sign_dialog_state(page: Any) -> dict[str, Any]:
    if not hasattr(page, "evaluate"):
        return {"visible": False, "text": ""}
    try:
        state = await page.evaluate(
            """() => {
                const visible = el => {
                    if (!el) return false;
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 0
                        && rect.height > 0
                        && style.display !== 'none'
                        && style.visibility !== 'hidden'
                        && Number(style.opacity || 1) !== 0;
                };
                const norm = text => String(text || '').replace(/\\s+/g, ' ').trim();
                const roots = Array.from(document.querySelectorAll(
                    '[role="dialog"], .am-modal, .am-modal-content, .am-modal-wrap, .adm-modal, .adm-modal-body'
                )).filter(visible).reverse();
                for (const root of roots) {
                    const text = norm(root.innerText || root.textContent);
                    if (/短信验证|手机号验证|签约失败|签约授权书|银行签约|银行卡签约/.test(text)) {
                        return { visible: true, text: text.slice(0, 800) };
                    }
                }
                const bodyText = norm(document.body?.innerText || '');
                return { visible: false, text: bodyText.slice(0, 800) };
            }"""
        )
    except Exception:
        return {"visible": False, "text": ""}
    return state if isinstance(state, dict) else {"visible": False, "text": ""}


async def _check_bank_sign_authorization(page: Any) -> list[dict[str, Any]]:
    if not hasattr(page, "evaluate"):
        return []
    source_url = str(getattr(page, "url", ""))
    try:
        checked = await page.evaluate(
            """() => {
                const records = [];
                const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                const textOf = el => String(el?.innerText || el?.textContent || el?.value || '').replace(/\\s+/g, ' ').trim();
                const fire = el => {
                    if (!el) return false;
                    el.scrollIntoView({ block: 'center', inline: 'center' });
                    for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                        try { el.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window })); } catch (_) {}
                    }
                    if (typeof el.click === 'function') el.click();
                    return true;
                };
                const modal = Array.from(document.querySelectorAll('[role="dialog"], .am-modal, .am-modal-content, .adm-modal, .adm-modal-body'))
                    .filter(visible)
                    .reverse()
                    .find(el => /短信验证|手机号验证|签约授权书|银行签约|银行卡签约/.test(textOf(el)));
                if (!modal) return records;
                const inputs = Array.from(modal.querySelectorAll('input[type="checkbox"]')).filter(visible);
                for (const input of inputs) {
                    if (!input.checked && fire(input)) {
                        records.push({ text: '勾选签约授权书', selector: 'input[type=checkbox]' });
                    }
                }
                const agreement = Array.from(modal.querySelectorAll('.auth-agree, .am-checkbox, .adm-checkbox, label, div, span'))
                    .filter(visible)
                    .map((el, index) => ({ el, index, text: textOf(el) }))
                    .filter(item => /我已查看并同意|我已阅读并同意|签约授权书|同意/.test(item.text))
                    .sort((a, b) => a.index - b.index)[0];
                if (agreement && fire(agreement.el)) {
                    records.push({ text: agreement.text.slice(0, 80) || '签约授权书同意', selector: '.auth-agree' });
                }
                return records;
            }"""
        )
    except Exception:
        checked = []
    return [
        _minimal_action_record(
            page,
            text=str(item.get("text") or "签约授权书同意"),
            tag="checkbox",
            selector=item.get("selector"),
            source_url=source_url,
            click_strategy="bank-sign-agreement",
        )
        for item in (checked or [])
        if isinstance(item, dict)
    ]


async def _direct_bank_sign_sms(page: Any) -> dict[str, Any] | None:
    if not hasattr(page, "evaluate"):
        return None
    try:
        result = await page.evaluate(
            """async () => {
                const headers = { 'content-type': 'application/json;charset=UTF-8' };
                const currentUrl = new URL(location.href);
                const findKey = (root, key, depth = 0) => {
                    if (!root || depth > 9) return undefined;
                    if (Array.isArray(root)) {
                        for (const item of root) {
                            const found = findKey(item, key, depth + 1);
                            if (found !== undefined && found !== null && found !== '') return found;
                        }
                        return undefined;
                    }
                    if (typeof root !== 'object') return undefined;
                    if (Object.prototype.hasOwnProperty.call(root, key)) return root[key];
                    for (const value of Object.values(root)) {
                        const found = findKey(value, key, depth + 1);
                        if (found !== undefined && found !== null && found !== '') return found;
                    }
                    return undefined;
                };
                const readRedux = () => {
                    const store = window.__NEXT_REDUX_STORE__ || window.store || window.reduxStore;
                    if (!store || typeof store.getState !== 'function') return {};
                    try {
                        const raw = store.getState();
                        return raw && typeof raw.toJS === 'function' ? raw.toJS() : raw;
                    } catch (_) {
                        return {};
                    }
                };
                const firstModule = (root, key) => {
                    const value = root?.product?.insure?.data?.data?.[key]
                        || root?.initialReduxState?.product?.insure?.data?.data?.[key]
                        || findKey(root, key);
                    if (Array.isArray(value)) return value[0] || {};
                    if (value && typeof value === 'object') return value;
                    return {};
                };
                const nextData = window.__NEXT_DATA__ || {};
                const redux = readRedux();
                const mock = window.__agent3MockData || {};
                const applicant = Object.assign({}, firstModule(nextData, '10'), firstModule(redux, '10'));
                const bankModule = Object.assign({}, firstModule(nextData, '107'), firstModule(redux, '107'));
                const encryptInsureNum = currentUrl.searchParams.get('encryptInsureNum')
                    || findKey(nextData, 'encryptInsureNum')
                    || findKey(redux, 'encryptInsureNum')
                    || '';
                const merchantId = Number(findKey(nextData, 'merchantId') || findKey(redux, 'merchantId') || 1000014);
                const firstNumber = values => {
                    for (const value of values) {
                        const number = Number(value);
                        if (Number.isFinite(number) && number > 0) return number;
                    }
                    return 0;
                };
                const productId = firstNumber([
                    currentUrl.searchParams.get('prodId'),
                    currentUrl.searchParams.get('productId'),
                    nextData?.props?.pageProps?.prodId,
                    nextData?.props?.pageProps?.productBasicInfo?.productOper?.id,
                    nextData?.props?.pageProps?.productBasicInfo?.productOper?.operProductId,
                    nextData?.props?.pageProps?.productBasicInfo?.productOper?.realProductId,
                    nextData?.props?.pageProps?.initialReduxState?.product?.detail?.basicProductInfo?.productOper?.id,
                    redux?.product?.detail?.basicProductInfo?.productOper?.id,
                    redux?.product?.detail?.basicProductInfo?.productOper?.operProductId,
                    mock.productId,
                    mock.prodId,
                ]);
                const productPlanId = firstNumber([
                    currentUrl.searchParams.get('planId'),
                    currentUrl.searchParams.get('productPlanId'),
                    nextData?.props?.pageProps?.planId,
                    nextData?.props?.pageProps?.productPlanId,
                    nextData?.props?.pageProps?.productBasicInfo?.planLst?.[0]?.id,
                    nextData?.props?.pageProps?.initialReduxState?.product?.detail?.basicProductInfo?.planLst?.[0]?.id,
                    redux?.product?.detail?.basicProductInfo?.planLst?.[0]?.id,
                    mock.productPlanId,
                    mock.planId,
                ]);
                const params = {
                    productId,
                    productPlanId,
                    bankName: bankModule.bankName || mock.bankName_107 || mock.openBank_107 || '工商银行',
                    bankId: bankModule.bank || mock.bankValue_107 || mock.bankControlValue_107 || mock.bank_107 || '1',
                    accountCode: String(bankModule.payAccount || mock.payAccount_107 || '6200588435998028938').replace(/\\s+/g, ''),
                    cardOwnerName: bankModule.cardOwner || mock.cardOwner_107 || mock['applicant.name'] || applicant.cName || '',
                    holderMobile: bankModule.BankReservingMobile || mock['applicant.mobile'] || applicant.moblie || '',
                    cardOwnerIdType: applicant.cardTypeName || '1',
                    cardOwnerIdNo: applicant.cardNumber || mock['applicant.id_no'] || '',
                    encryptInsureNum,
                    readProtocol: false,
                };
                const postJson = async (path, payload) => {
                    const response = await fetch(`${path}?md=${Math.random()}`, {
                        method: 'POST',
                        credentials: 'include',
                        headers,
                        body: JSON.stringify(payload),
                    });
                    const text = await response.text();
                    let json = null;
                    try { json = text ? JSON.parse(text) : null; } catch (_) {}
                    return { status: response.status, text, json };
                };
                const getJson = async (path) => {
                    const response = await fetch(`${path}${path.includes('?') ? '&' : '?'}md=${Math.random()}`, {
                        method: 'GET',
                        credentials: 'include',
                    });
                    const text = await response.text();
                    let json = null;
                    try { json = text ? JSON.parse(text) : null; } catch (_) {}
                    return { status: response.status, text, json };
                };
                const agreements = await getJson(`/api/apps/cps/insure/bank/sign/agreements?encryptInsureNum=${encodeURIComponent(encryptInsureNum)}`);
                params.readProtocol = Array.isArray(agreements.json?.data) && agreements.json.data.length > 0;
                const apply = await postJson('/api/apps/cps/insure/bank/sign/apply', params);
                const authTransNo = apply.json?.data?.authTransNo || apply.json?.data || '';
                if (!(apply.json?.code === 0 || apply.json?.success === true) || !authTransNo) {
                    return { ok: false, params, agreements, apply, confirm: null, next: null };
                }
                const confirm = await postJson('/api/apps/cps/insure/bank/sign/confirm', {
                    ...params,
                    authTransNo,
                    verifyCode: '1111',
                });
                const parts = location.pathname.split('/').filter(Boolean);
                const basePrefix = parts.length >= 4 ? `/${parts.slice(0, 4).join('/')}` : '/m/apps/cps/demo-channel';
                const taskUrl = `${location.origin}${basePrefix}/product/task?encryptInsureNum=${encodeURIComponent(encryptInsureNum)}`;
                const insureUrl = `${location.origin}${basePrefix}/product/insure?encryptInsureNum=${encodeURIComponent(encryptInsureNum)}`;
                const next = await postJson('/api/apps/cps/insure/task/next/do', {
                    encryptInsureNum,
                    merchantId,
                    taskUrl,
                    insureUrl,
                });
                return { ok: confirm.json?.code === 0 || confirm.json?.success === true, params, agreements, apply, confirm, next };
            }"""
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:500]}
    return result if isinstance(result, dict) else None


async def _apply_bank_sign_task_data(page: Any, *, force: bool = False) -> list[dict[str, Any]]:
    try:
        body_text = await _body_text_full(page)
    except Exception:
        body_text = ""
    current_url = str(getattr(page, "url", ""))
    state = await _bank_sign_dialog_state(page)
    if not force and not state.get("visible") and not _bank_sign_hint(current_url, body_text):
        return []

    actions: list[dict[str, Any]] = []
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        trace_blocker_reason = _submit_trace_blocker_reason(page)
        if trace_blocker_reason:
            source_url = str(getattr(page, "url", "") or current_url)
            actions.append(_submit_trace_blocker_action(page, trace_blocker_reason, source_url=source_url))
            return actions
        state = await _bank_sign_dialog_state(page)
        state_text = str(state.get("text") or "")
        if state.get("visible"):
            source_url = str(getattr(page, "url", ""))
            actions.append(
                _minimal_action_record(
                    page,
                    text=f"识别银行卡签约弹窗: {state_text[:80]}",
                    tag="dialog",
                    selector="[role=dialog], .am-modal",
                    source_url=source_url,
                    click_strategy="bank-sign-dialog-detected",
                )
            )
            actions.extend(await _check_bank_sign_authorization(page))
            apply_waiter = _start_api_response_waiter(page, "/api/apps/cps/insure/bank/sign/apply", timeout_ms=20_000)
            sms_action = await _click_auth_button(
                page,
                ("发送验证码", "获取验证码", "获取短信验证码", "重新获取", "重新发送"),
                strategy="bank-sign-sms-send",
            )
            if sms_action:
                actions.append(sms_action)
            apply_record = await _record_api_waiter_result(
                page,
                apply_waiter,
                text="银行卡签约申请",
                selector="/api/apps/cps/insure/bank/sign/apply",
                source_url=source_url,
                click_strategy="bank-sign-sms-apply",
            )
            if apply_record:
                actions.append(apply_record)
            actions.extend(await _force_fill_sms_captcha(page))
            await page.wait_for_timeout(500)
            confirm_waiter = _start_api_response_waiter(page, "/api/apps/cps/insure/bank/sign/confirm", timeout_ms=30_000)
            next_waiter = _start_api_response_waiter(page, "/api/apps/cps/insure/task/next/do", timeout_ms=45_000)
            before = await _capture_submit_diagnostics(page, phase="before-bank-sign-confirm", action_text="确定")
            confirm_action = await _click_auth_button(page, ("确定", "确认", "提交"), strategy="bank-sign-confirm")
            if confirm_action:
                if before:
                    confirm_action.setdefault("submit_diagnostics", []).append(before)
                after = await _capture_submit_diagnostics(page, phase="after-bank-sign-confirm", action_text="确定")
                if after:
                    confirm_action.setdefault("submit_diagnostics", []).append(after)
                actions.append(confirm_action)
            confirm_record = await _record_api_waiter_result(
                page,
                confirm_waiter,
                text="银行卡签约确认",
                selector="/api/apps/cps/insure/bank/sign/confirm",
                source_url=source_url,
                click_strategy="bank-sign-confirm-response",
            )
            if confirm_record:
                actions.append(confirm_record)
            actions.extend(
                await _wait_after_task_next_click(
                    page,
                    next_waiter,
                    source_url=source_url,
                    click_strategy="bank-sign-next-task-response",
                    timeout_ms=60_000,
                )
            )
            return actions

        if _bank_sign_hint(current_url, state_text):
            click_action = await _click_auth_button(page, ("去完成", "下一步", "继续", "确认"), strategy="bank-sign-task-open")
            if click_action:
                actions.append(click_action)
                await page.wait_for_timeout(2_000)
                continue
        if force:
            direct_result = await _direct_bank_sign_sms(page)
            if direct_result:
                actions.append(
                    _minimal_action_record(
                        page,
                        text=f"银行卡签约接口兜底={direct_result.get('ok')}",
                        tag="xhr",
                        selector="/api/apps/cps/insure/bank/sign/*",
                        source_url=current_url,
                        click_strategy="bank-sign-direct",
                    )
                )
                await _wait_for_page_flow_settled(page, previous_url=current_url, timeout_ms=60_000, min_wait_ms=1_200)
            return actions
        try:
            await page.wait_for_timeout(1_000)
        except Exception:
            await asyncio.sleep(1)
    return actions


async def _apply_authentication_task_data(page: Any) -> list[dict[str, Any]]:
    try:
        body_text = await _body_text_full(page)
    except Exception:
        body_text = ""
    if not (
        re.search(r"/authentication(?:/|$)", urlparse(str(getattr(page, "url", ""))).path)
        or any(token in body_text for token in ("身份认证", "投保意愿认证", "证件照片", "上传照片", "发送认证短信", "提交认证"))
    ):
        return []

    actions: list[dict[str, Any]] = []
    sms_verified = False
    if any(token in body_text for token in ("发送认证短信", "重新发送", "验证码")) and "已认证" not in body_text and "认证通过" not in body_text:
        sms_token: str | None = None
        has_countdown = bool(re.search(r"重新发送\(\d+s\)", body_text))
        sms_action = await _click_auth_button(
            page,
            ("发送认证短信", "获取验证码", "发送验证码", "获取短信验证码"),
            strategy="auth-sms-send",
        ) if not has_countdown else None
        if sms_action:
            actions.append(sms_action)
            await page.wait_for_timeout(1_000)
            await _wait_for_toast_closed(page, timeout_ms=6_000)
        else:
            await _wait_for_toast_closed(page, timeout_ms=3_000)
        sms_fill_actions = await _force_fill_sms_captcha(page)
        actions.extend(sms_fill_actions)
        await page.wait_for_timeout(700)
        await _wait_for_toast_closed(page, timeout_ms=6_000)
        verify_waiter = None
        if hasattr(page, "wait_for_response"):
            try:
                verify_waiter = asyncio.create_task(
                    page.wait_for_response(
                        lambda response: "insuredSms/verify" in response.url and response.status < 500,
                        timeout=8_000,
                    )
                )
            except Exception:
                verify_waiter = None
        submit_action = await _click_auth_button(
            page,
            ("提交认证",),
            strategy="auth-sms-submit",
        )
        if submit_action:
            actions.append(submit_action)
        verified = False
        if verify_waiter is not None:
            try:
                verify_response = await verify_waiter
                try:
                    verify_json = await verify_response.json()
                except Exception:
                    verify_json = None
                verified = not isinstance(verify_json, dict) or verify_json.get("code") == 0
            except Exception:
                verified = False
        if not verified:
            await page.wait_for_timeout(1_200)
            try:
                after_sms_text = await _body_text_full(page)
            except Exception:
                after_sms_text = ""
            if any(
                token in after_sms_text
                for token in ("已认证", "认证通过", "证件照片", "上传照片", "身份证图片", "人像面", "国徽面", "完成认证")
            ):
                verified = True
        if not verified:
            direct_result = await _direct_verify_auth_sms(page, sms_token=sms_token)
            if direct_result:
                actions.append(
                    _minimal_action_record(
                        page,
                        text=f"短信认证接口兜底={direct_result.get('ok')}",
                        tag="xhr",
                        selector="insuredSms/verify",
                        source_url=page.url,
                        click_strategy="auth-sms-direct-verify",
                    )
                )
                if direct_result.get("ok"):
                    verified = True
        sms_verified = verified
        await page.wait_for_timeout(2_000)

    try:
        body_text = await _body_text_full(page)
    except Exception:
        body_text = ""
    if sms_verified and "下一步" in body_text:
        next_action = await _click_auth_button(page, ("下一步",), strategy="auth-sms-next-photo")
        if next_action:
            actions.append(next_action)
        await _wait_for_auth_file_inputs(page, timeout_ms=30_000)
        try:
            body_text = await _body_text_full(page)
        except Exception:
            body_text = ""
    if ("已认证" in body_text or "认证通过" in body_text) and "下一步" in body_text:
        next_action = await _click_auth_button(page, ("下一步",), strategy="auth-certified-next")
        if next_action:
            actions.append(next_action)
        await _wait_for_auth_file_inputs(page, timeout_ms=30_000)

    try:
        body_text = await _body_text_full(page)
    except Exception:
        body_text = ""
    file_count = await _auth_file_input_count(page)
    if file_count <= 0 and any(token in body_text for token in ("身份证图片", "上传照片", "人像面", "国徽面")):
        file_count = await _wait_for_auth_file_inputs(page, timeout_ms=30_000)
    if file_count > 0:
        try:
            auth_mock_data = await page.evaluate("() => window.__agent3MockData || null")
        except Exception:
            auth_mock_data = None
        if not isinstance(auth_mock_data, dict):
            auth_mock_data = _load_agent3_mock_data_from_env()
        image_paths = _resolve_id_card_image_paths(auth_mock_data)
        uploaded_count = 0
        if image_paths and hasattr(page, "locator"):
            file_inputs = page.locator("input[type='file']")
            try:
                file_count = await file_inputs.count()
            except Exception:
                file_count = 0
            for index in range(min(file_count, 2)):
                try:
                    image_path = image_paths[index] if index < len(image_paths) else image_paths[-1]
                    source_url = page.url
                    upload_waiter = None
                    if hasattr(page, "wait_for_response"):
                        try:
                            upload_waiter = page.wait_for_response(
                                lambda response: re.search(r"/api/apps/base/file/user-file-upload", response.url, re.I)
                                and response.status < 500,
                                timeout=30_000,
                            )
                        except Exception:
                            upload_waiter = None
                    await file_inputs.nth(index).set_input_files(str(image_path), timeout=20_000)
                    if upload_waiter is not None:
                        try:
                            await upload_waiter
                        except Exception:
                            pass
                    await page.wait_for_timeout(1_500)
                    uploaded_count += 1
                    actions.append(
                        _minimal_action_record(
                            page,
                            text=f"上传证件照{index + 1}: {image_path.name}",
                            tag="input",
                            selector="input[type='file']",
                            source_url=source_url,
                            click_strategy="auth-id-card-upload",
                        )
                    )
                except Exception as exc:
                    actions.append(
                        _minimal_action_record(
                            page,
                            text=f"上传证件照{index + 1}失败: {str(exc)[:80]}",
                            tag="input",
                            selector="input[type='file']",
                            source_url=page.url,
                            click_strategy="auth-id-card-upload-failed",
                        )
                    )
        if uploaded_count > 0:
            submit_photo = await _click_auth_button(
                page,
                ("提 交", "提交", "完成"),
                strategy="auth-photo-submit",
            )
            if submit_photo:
                actions.append(submit_photo)
            await page.wait_for_timeout(5_000)
        else:
            actions.append(
                _minimal_action_record(
                    page,
                    text="证件照上传控件存在但未成功上传，跳过提交避免空文件保存",
                    tag="input",
                    selector="input[type='file']",
                    source_url=page.url,
                    click_strategy="auth-id-card-upload-required",
                )
            )

    try:
        body_text = await _body_text_full(page)
    except Exception:
        body_text = ""
    if any(token in body_text for token in ("完成认证", "认证通过", "已认证")):
        source_url = str(getattr(page, "url", ""))
        next_waiter = _start_api_response_waiter(page, "/api/apps/cps/insure/task/next/do", timeout_ms=45_000)
        final_next = await _click_auth_button(
            page,
            ("下一步", "去支付", "立即支付", "确认"),
            strategy="auth-final-next",
        )
        if final_next:
            actions.append(final_next)
            next_actions = await _wait_after_task_next_click(
                page,
                next_waiter,
                source_url=source_url,
                click_strategy="auth-final-next-task-response",
                timeout_ms=75_000,
            )
            actions.extend(next_actions)
            try:
                after_next_text = await _body_text_full(page)
            except Exception:
                after_next_text = ""
            if any("taskType=3" in str(item.get("text") or "") for item in next_actions) or _bank_sign_hint(
                str(getattr(page, "url", "")),
                after_next_text,
            ):
                actions.extend(await _apply_bank_sign_task_data(page, force=True))
            elif not any("standard-underwriting-poll" in str(item.get("text") or "") for item in next_actions):
                actions.extend(
                    await _probe_standard_underwriting_after_auth(
                        page,
                        source_url=source_url,
                        click_strategy="auth-final-next-standard-probe",
                    )
                )
        else:
            if next_waiter is not None:
                next_waiter.cancel()
            await page.wait_for_timeout(2_000)
    return actions


async def _apply_minimal_transit_data(page: Any) -> list[dict[str, Any]]:
    actions = []
    boundary_strategies = {
        "account-session-boundary",
        "backend-unavailable-boundary",
        "health-notice-boundary",
        "bank-card-validation-loop",
        "frontend-runtime-boundary",
        "authentication-boundary",
    }

    def boundary_action() -> dict[str, Any] | None:
        auth_boundary = _authentication_boundary_action(page)
        if auth_boundary:
            if any(str(item.get("click_strategy") or "") == "authentication-boundary" for item in actions):
                return None
            return auth_boundary
        reason = _submit_trace_blocker_reason(page)
        if not reason:
            return None
        if any(str(item.get("click_strategy") or "") in boundary_strategies for item in actions):
            return None
        return _submit_trace_blocker_action(page, reason, source_url=str(getattr(page, "url", "")))

    for apply_step in (
        _apply_authentication_task_data,
        _apply_bank_sign_task_data,
        _apply_minimal_choice_data,
        _apply_minimal_form_data,
        _check_required_agreements,
    ):
        blocker = boundary_action()
        if blocker:
            actions.append(blocker)
            return actions
        actions.extend(await apply_step(page))
        blocker = boundary_action()
        if blocker:
            actions.append(blocker)
            return actions
    return actions


def _looks_like_form_validation_feedback(text: str) -> bool:
    normalized = " ".join(str(text or "").split())
    return any(
        token in normalized
        for token in (
            "请先",
            "请选择",
            "请输入",
            "不能为空",
            "必填",
            "未填写",
            "格式不正确",
            "校验失败",
        )
    )


async def _repair_form_validation_feedback(page: Any) -> list[dict[str, Any]]:
    body_text = await _body_text_excerpt(page)
    if not (_looks_like_form_validation_feedback(body_text) or _looks_like_agreement_feedback(body_text)):
        return []
    actions: list[dict[str, Any]] = []
    actions.extend(await _check_required_agreements(page))
    if _looks_like_form_validation_feedback(body_text):
        actions.extend(await _apply_minimal_choice_data(page))
        actions.extend(await _apply_minimal_form_data(page))
    return actions


def _same_origin_links(base_url: str, actions: list[dict[str, Any]]) -> list[str]:
    base = urlparse(base_url)
    links: list[str] = []
    for action in actions:
        href = str(action.get("href") or "").strip()
        if not href or href.startswith(("javascript:", "#", "mailto:", "tel:")):
            continue
        target = urljoin(base_url, href)
        parsed = urlparse(target)
        if parsed.scheme not in {"http", "https"}:
            continue
        if parsed.netloc != base.netloc:
            continue
        if target not in links:
            links.append(target)
    return links[:3]


async def _snapshot_page(page: Any, source_url: str) -> dict[str, Any]:
    last_error: Exception | None = None
    for _ in range(3):
        try:
            title = await page.title()
            fields = await _collect_fields(page)
            actions = await _collect_actions(page)
            candidate_links = _same_origin_links(page.url, actions)
            primary_actions = _primary_actions(page.url, actions)
            path = urlparse(page.url).path or "/"
            page_key = path.strip("/").replace("/", "-") or "home"
            snapshot = {
                "page_key": page_key,
                "url": page.url,
                "source_url": source_url,
                "title": title,
                "dom_signature": await _page_signature(page),
                "body_text_excerpt": await _body_text_excerpt(page),
                "page_state": await _collect_page_state(page),
                "field_count": len(fields),
                "action_count": len(actions),
                "fields": fields,
                "actions": actions,
                "primary_actions": primary_actions,
                "candidate_links": candidate_links,
            }
            payment_boundary_evidence = _payment_boundary_evidence_from_page(snapshot)
            if payment_boundary_evidence:
                snapshot["payment_boundary_evidence"] = payment_boundary_evidence
            return snapshot
        except Exception as exc:
            last_error = exc
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=3_000)
            except Exception:
                pass
            await page.wait_for_timeout(500)
    if last_error is not None:
        raise last_error
    raise RuntimeError("Unable to snapshot page")


def _expected_page_keys(path_item: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        planned
        for planned in path_item.get("page_keys", []) or []
        if str(planned.get("node_id") or "") not in {"NODE-start", "NODE-end", "NODE-branch"}
    ]


def _build_planned_page_catalog(regression_paths: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    catalog_by_key: dict[str, dict[str, Any]] = {}
    for path_item in list(regression_paths or []):
        path_id = str(path_item.get("path_id") or "")
        for order, planned in enumerate(_expected_page_keys(path_item), start=1):
            node_id = str(planned.get("node_id") or "")
            page_key = str(planned.get("page_key") or node_id)
            url_pattern = str(planned.get("url_pattern") or "")
            state = planned.get("state") or {}
            state_key = json.dumps(state, ensure_ascii=False, sort_keys=True)
            dedupe_key = f"{node_id}|{page_key}|{url_pattern}|{state_key}"
            item = catalog_by_key.setdefault(
                dedupe_key,
                {
                    "planned_page_id": f"PP-{len(catalog_by_key) + 1:03d}",
                    "node_id": node_id,
                    "page_key": page_key,
                    "url_pattern": url_pattern,
                    "state": state,
                    "first_order": order,
                    "path_ids": [],
                    "source": "agent2.page_keys",
                },
            )
            if path_id and path_id not in item["path_ids"]:
                item["path_ids"].append(path_id)
    return sorted(catalog_by_key.values(), key=lambda item: (int(item["first_order"]), str(item["planned_page_id"])))


def _planned_page_id_index(planned_page_catalog: list[dict[str, Any]]) -> dict[tuple[str, str, str], str]:
    return {
        (
            str(item.get("node_id") or ""),
            str(item.get("page_key") or ""),
            str(item.get("url_pattern") or ""),
        ): str(item.get("planned_page_id") or "")
        for item in planned_page_catalog
    }


def _catalog_id_for_planned(
    planned: dict[str, Any],
    index: dict[tuple[str, str, str], str],
) -> str | None:
    key = (
        str(planned.get("node_id") or ""),
        str(planned.get("page_key") or ""),
        str(planned.get("url_pattern") or ""),
    )
    return index.get(key)


def _page_record_id(page: dict[str, Any]) -> str:
    raw = str(page.get("dom_signature") or page.get("url") or page.get("page_key") or "")
    return "PCR-" + hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:12].upper()


def _build_page_content_records(
    pages: list[dict[str, Any]],
    planned_page_catalog: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    records_by_id: dict[str, dict[str, Any]] = {}
    for page in pages:
        record_id = _page_record_id(page)
        record = records_by_id.setdefault(
            record_id,
            {
                "page_content_record_id": record_id,
                "actual_url": page.get("url"),
                "actual_page_key": page.get("page_key"),
                "title": page.get("title"),
                "dom_signature": page.get("dom_signature"),
                "body_text_excerpt": page.get("body_text_excerpt"),
                "field_count": page.get("field_count"),
                "action_count": page.get("action_count"),
                "field_map": _field_map(page),
                "selector_map": _selector_map(page),
                "matched_planned_page_ids": [],
                "matched_node_ids": [],
                "source_path_ids": [],
            },
        )
        path_id = str(page.get("path_id") or "")
        if path_id and path_id not in record["source_path_ids"]:
            record["source_path_ids"].append(path_id)
        payment_boundary_evidence = page.get("payment_boundary_evidence") or _payment_boundary_evidence_from_page(page)
        if payment_boundary_evidence and not record.get("payment_boundary_evidence"):
            record["payment_boundary_evidence"] = dict(payment_boundary_evidence)
        for planned in planned_page_catalog:
            planned_probe = {
                "node_id": planned.get("node_id"),
                "page_key": planned.get("page_key"),
                "url_pattern": planned.get("url_pattern"),
            }
            if not _page_matches_node(page, planned_probe):
                continue
            planned_page_id = str(planned.get("planned_page_id") or "")
            node_id = str(planned.get("node_id") or "")
            if planned_page_id and planned_page_id not in record["matched_planned_page_ids"]:
                record["matched_planned_page_ids"].append(planned_page_id)
            if node_id and node_id not in record["matched_node_ids"]:
                record["matched_node_ids"].append(node_id)
    return list(records_by_id.values())


def _page_elements_from_records(page_content_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "page_content_record_id": record.get("page_content_record_id"),
            "actual_url": record.get("actual_url"),
            "actual_page_key": record.get("actual_page_key"),
            "title": record.get("title"),
            "matched_planned_page_ids": list(record.get("matched_planned_page_ids", []) or []),
            "matched_node_ids": list(record.get("matched_node_ids", []) or []),
            "source_path_ids": list(record.get("source_path_ids", []) or []),
            "fields": list(record.get("field_map", []) or []),
            "actions": list((record.get("selector_map", {}) or {}).get("actions", []) or []),
        }
        for record in page_content_records
    ]


def _action_trace_from_path_results(path_exploration_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "path_id": result.get("path_id"),
            "path_status": result.get("path_status"),
            "action_count": len(result.get("action_chain", []) or []),
            "action_chain": list(result.get("action_chain", []) or []),
            "events": list(result.get("node_execution_trace", []) or []),
            "successful_actions": [
                event
                for event in result.get("node_execution_trace", []) or []
                if event.get("phase") == "act" and (event.get("selector") or event.get("text"))
            ],
        }
        for result in path_exploration_results
    ]


def _main_flow_progress_from_results(path_exploration_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "path_id": result.get("path_id"),
            "path_status": result.get("path_status"),
            "target_node": result.get("target_node"),
            "reached_node": result.get("reached_node"),
            "blocked_node": result.get("blocked_node"),
            "blocked_reason": result.get("blocked_reason"),
            "node_progress": list(result.get("node_progress", []) or []),
            "completion_rule": result.get("completion_rule", {}),
        }
        for result in path_exploration_results
    ]


def _exploration_cache_key(path_item: dict[str, Any]) -> str:
    return json.dumps(
        {
            "route": _cache_route_signature(path_item),
            "conditions": _branch_conditions_for_cache(path_item),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _clone_step_results_for_path(
    steps: list[dict[str, Any]],
    *,
    path_id: str,
    reused_from_path_id: str | None = None,
) -> list[dict[str, Any]]:
    cloned = json.loads(json.dumps(steps, ensure_ascii=False))
    for step in cloned:
        step["path_id"] = path_id
        if reused_from_path_id:
            step["reused_from_path_id"] = reused_from_path_id
            step["reuse_source"] = "agent3.completed_path"
        action = step.get("action")
        if isinstance(action, dict):
            action["path_id"] = path_id
            if reused_from_path_id:
                action["reused_from_path_id"] = reused_from_path_id
                action["reuse_source"] = "agent3.completed_path"
    return cloned


def _clone_action_trace_for_path(
    actions: list[dict[str, Any]],
    *,
    path_id: str,
    reused_from_path_id: str | None = None,
) -> list[dict[str, Any]]:
    cloned = json.loads(json.dumps(actions, ensure_ascii=False))
    for action in cloned:
        action["path_id"] = path_id
        if reused_from_path_id:
            action["reused_from_path_id"] = reused_from_path_id
            action["reuse_source"] = "agent3.completed_path"
    return cloned


def _matched_node_ids_from_steps(steps: list[dict[str, Any]]) -> set[str]:
    matched = {
        str(step.get("planned_node_id") or "")
        for step in steps
        if step.get("matched") and step.get("planned_node_id")
    }
    matched.update(
        str(step.get("observed_node_id") or "")
        for step in steps
        if step.get("matched") and step.get("observed_node_id")
    )
    return matched


def _required_node_ids(path_item: dict[str, Any]) -> list[str]:
    return [
        str(node_id)
        for node_id in path_item.get("nodes", []) or []
        if str(node_id) not in {"NODE-start", "NODE-end", "NODE-branch"}
    ]


def _path_route_node_ids(path_item: dict[str, Any]) -> list[str]:
    page_nodes = [
        str(planned.get("node_id") or "")
        for planned in path_item.get("page_keys", []) or []
        if isinstance(planned, dict) and planned.get("node_id")
    ]
    return _unique_in_order(page_nodes or _required_node_ids(path_item))


def _completed_path_reuse_key(path_item: dict[str, Any]) -> str:
    nodes = _path_route_node_ids(path_item)
    if not nodes:
        return ""
    entry_node = nodes[0]
    target_node = nodes[-1]
    if entry_node != "NODE-product-detail" or target_node != "NODE-policy-result":
        return ""
    return json.dumps(
        {
            "entry_node": entry_node,
            "target_node": target_node,
            "flow": "product-detail-to-policy-result",
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _steps_complete_for_path(path_item: dict[str, Any], steps: list[dict[str, Any]]) -> bool:
    expected = _required_node_ids(path_item)
    required = _repaired_node_ids_for_steps(expected, steps)
    matched = _matched_node_ids_from_steps(steps)
    if bool(required) and all(node_id in matched for node_id in required):
        return True
    return _path_order_generation_complete(required, matched, steps)


def _best_step_attempt(
    attempts: list[tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[str]]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    def rank(attempt: tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[str]]) -> tuple[int, int, int]:
        snapshots, actions, steps, _warnings = attempt
        return (len(_matched_node_ids_from_steps(steps)), len(actions), len(snapshots))

    return max(attempts, key=rank) if attempts else ([], [], [], [])


def _timeout_recovery_attempt(
    *,
    path_id: str,
    path_item: dict[str, Any],
    current_snapshot: dict[str, Any] | None,
    current_url: str,
    warning: str,
    attempt_index: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    if not isinstance(current_snapshot, dict) or not current_snapshot.get("url"):
        return [], [], [], [warning]
    snapshot = dict(current_snapshot)
    snapshot["path_id"] = path_id
    observed_node_id = _infer_current_node_id(snapshot)
    required_node_ids = _required_node_ids(path_item)
    if not required_node_ids:
        return [snapshot], [], [], [warning]
    if observed_node_id and observed_node_id in required_node_ids:
        recovered_node_ids = required_node_ids[: required_node_ids.index(observed_node_id) + 1]
    elif observed_node_id:
        recovered_node_ids = [observed_node_id]
    else:
        recovered_node_ids = [required_node_ids[0]]
    steps: list[dict[str, Any]] = []
    for index, node_id in enumerate(recovered_node_ids, start=1):
        is_observed = node_id == observed_node_id
        step = {
            "step": index,
            "planned_node_id": node_id,
            "observed_node_id": node_id if is_observed else None,
            "planned_page_key": None,
            "planned_url_pattern": None,
            "actual_url": snapshot.get("url") or current_url if is_observed else None,
            "actual_page_key": snapshot.get("page_key") if is_observed else None,
            "matched": True,
            "status": "matched" if is_observed else "inferred_matched_before_timeout",
            "action": None,
            "blocked_reason": warning if is_observed else None,
            "node_execution_trace": [],
            "exploration_attempt": attempt_index,
        }
        if is_observed:
            step["node_execution_trace"].append(
                _observe_trace(
                    path_id=path_id,
                    node_id=node_id,
                    snapshot=snapshot,
                    matched=True,
                )
            )
        steps.append(step)
    return [snapshot], [], steps, [warning]


def _attempt_progress_key(steps: list[dict[str, Any]], actions: list[dict[str, Any]]) -> tuple[tuple[str, ...], int]:
    return (tuple(sorted(_matched_node_ids_from_steps(steps))), len(actions))


def _non_retryable_trace_boundary_reason(steps: list[dict[str, Any]], actions: list[dict[str, Any]]) -> str | None:
    for item in list(steps or []) + list(actions or []):
        if not isinstance(item, dict):
            continue
        reason = str(item.get("blocked_reason") or item.get("text") or "")
        strategy = str((item.get("action") or {}).get("click_strategy") if isinstance(item.get("action"), dict) else "")
        strategy = strategy or str(item.get("click_strategy") or "")
        if strategy in {
            "account-session-boundary",
            "backend-unavailable-boundary",
            "health-notice-boundary",
            "bank-card-validation-loop",
            "authentication-boundary",
        }:
            return reason or strategy
        if strategy == "frontend-runtime-boundary":
            return reason or strategy
        if (
            reason.startswith("Account/session boundary")
            or reason.startswith("Backend/API unavailable")
            or reason.startswith("Health notice boundary")
            or reason.startswith("Bank card validation loop")
            or reason.startswith("Authentication boundary")
        ):
            return reason
        if reason.startswith("Frontend/runtime boundary"):
            return reason
    return None


def _branch_conditions_for_cache(path_item: dict[str, Any]) -> dict[str, str]:
    conditions = path_item.get("conditions", {})
    if not isinstance(conditions, dict):
        return {}
    return {str(key): str(value) for key, value in sorted(conditions.items())}


def _cache_route_signature(path_item: dict[str, Any]) -> list[dict[str, str]]:
    planned_pages = _expected_page_keys(path_item)
    if planned_pages:
        return [
            {
                "node_id": str(planned.get("node_id") or ""),
                "page_key": str(planned.get("page_key") or ""),
                "url_pattern": str(planned.get("url_pattern") or ""),
            }
            for planned in planned_pages
        ]
    return [
        {"node_id": node_id, "page_key": "", "url_pattern": ""}
        for node_id in _required_node_ids(path_item)
    ]


def _boundary_classification(result: dict[str, Any]) -> str:
    if result.get("path_status") == "explored":
        return "success"
    reason = str(result.get("blocked_reason") or "").lower()
    if any(
        token in reason
        for token in (
            "no executable primary action",
            "unexplored planned nodes",
            "missing page model",
        )
    ):
        return "coverage_gap"
    if any(
        token in reason
        for token in (
            "timed out",
            "timeout",
            "browser",
            "playwright",
            "node.js",
            "npx",
            "network",
            "transport",
            "not available",
            "unavailable",
            "frontend/runtime",
            "page error",
            "runtime boundary",
            "failed to open entry_url",
            "live browser exploration failed",
            "environment",
        )
    ):
        return "environment"
    if any(
        token in reason
        for token in (
            "form validation",
            "overlay",
            "blocked_by_overlay",
            "authentication",
            "identity",
            "payment boundary",
            "bank card validation loop",
            "manual review",
            "manual underwriting",
            "account boundary",
            "account/session",
            "session boundary",
        )
    ):
        return "blocking"
    return "coverage_gap"


def _terminal_boundary_for_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": "agent3.exploration_contract",
        "boundary_node": result.get("blocked_node") or result.get("target_node"),
        "target_node": result.get("target_node"),
        "classification": _boundary_classification(result),
        "path_status": result.get("path_status"),
        "blocked_reason": result.get("blocked_reason"),
    }


def _resume_condition_for_boundary(terminal_boundary: dict[str, Any]) -> str | None:
    classification = str(terminal_boundary.get("classification") or "")
    if classification == "success":
        return None
    if classification == "environment":
        return "restore browser/runtime environment and rerun Agent3 exploration from the entry chain"
    if classification == "blocking":
        return "resolve or provide the observed business/account boundary, then rerun Agent3 exploration"
    return "add a reachable Agent2 route/action mapping or manual evidence, then rerun Agent3 exploration"


def _build_exploration_contract(path_exploration_results: list[dict[str, Any]]) -> dict[str, Any]:
    completed_paths: list[dict[str, Any]] = []
    blocked_paths: list[dict[str, Any]] = []
    for result in path_exploration_results:
        completion_rule = result.get("completion_rule", {}) or {}
        terminal_boundary = dict(result.get("terminal_boundary") or _terminal_boundary_for_result(result))
        item = {
            "path_id": result.get("path_id"),
            "case_ids": list(result.get("case_ids", []) or []),
            "target_node": result.get("target_node"),
            "path_status": result.get("path_status"),
            "required_nodes": list(completion_rule.get("required_nodes", []) or []),
            "matched_nodes": list(completion_rule.get("matched_nodes", []) or []),
            "missing_nodes": list(completion_rule.get("missing_nodes", []) or []),
            "blocked_node": result.get("blocked_node"),
            "blocked_reason": result.get("blocked_reason"),
            "terminal_boundary": terminal_boundary,
            "resume_condition": result.get("resume_condition")
            or _resume_condition_for_boundary(terminal_boundary),
            "evidence_source": result.get("evidence_source") or "agent3-unknown",
        }
        if result.get("path_status") == "explored" and completion_rule.get("is_complete"):
            completed_paths.append(item)
        else:
            blocked_paths.append(item)
    return {
        "version": "agent3-path-contract-v2",
        "policy": "complete_paths_only_enter_agent4",
        "retry_limit": _agent3_path_attempt_limit(),
        "phase1_contract": {
            "exploration_mode": "path-driven",
            "leaf_contract_mode": "observe",
            "reuse_policy": "reuse_identical_route_or_completed_product_to_policy_chain",
            "boundary_policy": "blocked_or_environment_boundaries_must_not_be_treated_as_covered",
            "evidence_policy": "live_browser_evidence_first_with_explicit_degraded_sources",
        },
        "total_paths": len(path_exploration_results),
        "completed_count": len(completed_paths),
        "blocked_count": len(blocked_paths),
        "is_complete": not blocked_paths,
        "completed_paths": completed_paths,
        "blocked_paths": blocked_paths,
    }


async def _drive_planned_path(
    page: Any,
    *,
    path_item: dict[str, Any],
    entry_url: str,
    planned_context: dict[str, Any],
    max_steps: int = 12,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    snapshots: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    step_results: list[dict[str, Any]] = []
    warnings: list[str] = []
    planned_nodes = _expected_page_keys(path_item)
    attempted: set[str] = set()
    repair_counts: dict[str, int] = {}
    current_index = 0

    try:
        await page.goto(entry_url, wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(500)
        await _wait_for_entry_ready(page, entry_url)
    except Exception as exc:
        warnings.append(f"Planned path {path_item.get('path_id')} failed to open entry_url: {exc}")
        return snapshots, actions, step_results, warnings

    for _ in range(max_steps):
        if current_index >= len(planned_nodes):
            break

        snapshot = await _snapshot_page(page, entry_url)
        snapshot.update(planned_context)
        snapshot["path_id"] = path_item.get("path_id")
        snapshots.append(snapshot)

        observed_node_id = _infer_current_node_id(snapshot)
        current_index, _ = _repair_planned_nodes_with_observed(
            planned_nodes,
            current_index,
            snapshot,
            path_id=str(path_item.get("path_id") or ""),
            warnings=warnings,
        )
        aligned_index = _align_planned_index(planned_nodes, current_index, snapshot)
        if aligned_index != current_index:
            current_index = aligned_index

        planned = planned_nodes[current_index]
        current_node_id = str(planned.get("node_id") or "")
        observed_node_id = observed_node_id or _infer_current_node_id(snapshot)
        matched = _page_matches_node(snapshot, planned)
        step_result = {
            "step": len(step_results) + 1,
            "planned_node_id": current_node_id,
            "observed_node_id": observed_node_id,
            "dynamic_path_repair": planned.get("source") == "agent3.dynamic_path_repair",
            "planned_source": planned.get("source") or "agent2.page_keys",
            "node_profile": _node_profile(current_node_id),
            "planned_page_key": planned.get("page_key"),
            "planned_url_pattern": planned.get("url_pattern"),
            "actual_url": snapshot.get("url"),
            "actual_page_key": snapshot.get("page_key"),
            "matched": matched,
            "status": "matched" if matched else "not_matched",
            "action": None,
            "blocked_reason": None,
            "node_execution_trace": [],
        }
        step_result["node_execution_trace"].append(
            _observe_trace(
                path_id=str(path_item.get("path_id") or ""),
                node_id=current_node_id,
                snapshot=snapshot,
                matched=matched,
            )
        )
        step_results.append(step_result)

        if matched:
            current_index += 1
            if current_index >= len(planned_nodes):
                break

        target_planned = planned_nodes[current_index]
        target_node_id = str(target_planned.get("node_id") or "")
        is_terminal_target = current_index >= len(planned_nodes) - 1
        minimal_actions = (
            []
            if current_node_id in {"NODE-product-detail", "NODE-premium-calculation"}
            else await _apply_minimal_transit_data(page)
        )
        for minimal_action in minimal_actions:
            minimal_action.update(planned_context)
            minimal_action["path_id"] = path_item.get("path_id")
            minimal_action["planned_from_node_id"] = current_node_id
            minimal_action["planned_to_node_id"] = target_node_id
            actions.append(minimal_action)
            step_result["node_execution_trace"].append(
                _act_trace(
                    path_id=str(path_item.get("path_id") or ""),
                    node_id=current_node_id,
                    target_node_id=target_node_id,
                    action=minimal_action,
                )
            )
        trace_blocker_action = next(
            (
                item
                for item in minimal_actions
                if str(item.get("click_strategy") or "")
                in {
                    "account-session-boundary",
                    "backend-unavailable-boundary",
                    "health-notice-boundary",
                    "authentication-boundary",
                }
            ),
            None,
        )
        if trace_blocker_action:
            step_result["status"] = "blocked"
            step_result["blocked_reason"] = str(trace_blocker_action.get("text") or "")
            break
        if minimal_actions:
            step_result["action"] = minimal_actions[-1]
            snapshot = await _snapshot_page(page, entry_url)
            snapshot.update(planned_context)
            snapshot["path_id"] = path_item.get("path_id")
            oracle_actions, unresolved_requirements = await _repair_unresolved_requirements(
                page,
                snapshot.get("page_state"),
                repair_counts,
            )
            if oracle_actions:
                for oracle_action in oracle_actions:
                    oracle_action.update(planned_context)
                    oracle_action["path_id"] = path_item.get("path_id")
                    oracle_action["planned_from_node_id"] = current_node_id
                    oracle_action["planned_to_node_id"] = target_node_id
                    actions.append(oracle_action)
                    step_result["node_execution_trace"].append(
                        _act_trace(
                            path_id=str(path_item.get("path_id") or ""),
                            node_id=current_node_id,
                            target_node_id=target_node_id,
                            action=oracle_action,
                        )
                    )
                step_result["oracle_unresolved_requirements"] = unresolved_requirements[:10]
                snapshot = await _snapshot_page(page, entry_url)
                snapshot.update(planned_context)
                snapshot["path_id"] = path_item.get("path_id")
        if target_node_id == "NODE-insure-form" and _page_matches_node(snapshot, target_planned):
            step_result["status"] = "matched"
            step_result["matched"] = True
            step_result["node_execution_trace"].append(
                _verify_trace(
                    path_id=str(path_item.get("path_id") or ""),
                    node_id=current_node_id,
                    target_node_id=target_node_id,
                    before_snapshot=snapshots[-1],
                    after_snapshot=snapshot,
                )
            )
            continue
        action = (
            _best_product_entry_action(snapshot, target_node_id, attempted)
            if current_node_id == "NODE-product-detail"
            else _best_action_for_node(snapshot, target_node_id, attempted)
        )
        if not action and _should_try_transit_action(snapshot, is_terminal_target=is_terminal_target):
            action = _best_transit_action(snapshot, target_node_id, attempted)
        if not action:
            step_result["status"] = "blocked_after_match" if matched else "blocked"
            step_result["blocked_reason"] = (
                f"No safe transit action found before final node {target_node_id}"
                if not is_terminal_target
                else f"No executable action found for planned node {target_node_id}"
            )
            break

        attempt_key = f"{snapshot.get('url')}|{action.get('selector')}|{action.get('text')}|{target_node_id}"
        if attempt_key in attempted:
            attempted.add(f"{attempt_key}|retry")
        else:
            attempted.add(attempt_key)
        before_signature = snapshot.get("dom_signature")
        try:
            action_result = await _click_primary_action(page, action)
            action_result.update(planned_context)
            action_result["path_id"] = path_item.get("path_id")
            action_result["planned_from_node_id"] = current_node_id
            action_result["planned_to_node_id"] = target_node_id
            actions.append(action_result)
            step_result["action"] = action_result
            saw_processing_after_action = await _wait_for_page_flow_settled(
                page,
                previous_url=str(action_result.get("source_url") or snapshot.get("url") or ""),
                timeout_ms=30_000,
                min_wait_ms=1_500,
            )
            next_snapshot = await _snapshot_page(page, entry_url)
            next_snapshot, immediate_overlay_reason = await _settle_blocking_overlay_after_action(
                page,
                next_snapshot,
                entry_url=entry_url,
                target_node_id=target_node_id,
                planned_context=planned_context,
                path_id=path_item.get("path_id"),
            )
            step_result["node_execution_trace"].append(
                _act_trace(
                    path_id=str(path_item.get("path_id") or ""),
                    node_id=current_node_id,
                    target_node_id=target_node_id,
                    action=action_result,
                )
            )
            step_result["node_execution_trace"].append(
                _verify_trace(
                    path_id=str(path_item.get("path_id") or ""),
                    node_id=current_node_id,
                    target_node_id=target_node_id,
                    before_snapshot=snapshot,
                    after_snapshot=next_snapshot,
                )
            )
            if immediate_overlay_reason:
                step_result["status"] = "blocked_by_overlay"
                step_result["blocked_reason"] = immediate_overlay_reason
                break
            if saw_processing_after_action:
                if _matches_node_reach_contract(next_snapshot, target_node_id) or _is_external_payment_handoff(
                    next_snapshot,
                    target_node_id,
                ):
                    step_result["node_execution_trace"].append(
                        _verify_trace(
                            path_id=str(path_item.get("path_id") or ""),
                            node_id=current_node_id,
                            target_node_id=target_node_id,
                            before_snapshot=snapshot,
                            after_snapshot=next_snapshot,
                        )
                    )
                elif target_node_id in {"NODE-payment", "NODE-policy-result", "NODE-underwriting"}:
                    if _looks_like_downstream_task_progress(next_snapshot):
                        step_result["status"] = "action_progress"
                        step_result["blocked_reason"] = None
                    else:
                        step_result["status"] = "blocked_by_overlay"
                        step_result["blocked_reason"] = (
                            "Submit processing finished without reaching the next node; "
                            "no form data was changed while the loading overlay was visible"
                        )
                        break
            if target_node_id == "NODE-insure-form":
                unfilled = _unfilled_question_numbers_from_text(str(next_snapshot.get("body_text_excerpt") or ""))
                if unfilled:
                    repair_actions = await _repair_unfilled_questionnaire_items(page)
                    for repair_action in repair_actions:
                        repair_action.update(planned_context)
                        repair_action["path_id"] = path_item.get("path_id")
                        repair_action["planned_from_node_id"] = current_node_id
                        repair_action["planned_to_node_id"] = target_node_id
                        actions.append(repair_action)
                        step_result["node_execution_trace"].append(
                            _act_trace(
                                path_id=str(path_item.get("path_id") or ""),
                                node_id=current_node_id,
                                target_node_id=target_node_id,
                                action=repair_action,
                            )
                        )
                    if repair_actions:
                        before_repair_submit = await _snapshot_page(page, entry_url)
                        retry_action = _best_action_for_node(before_repair_submit, target_node_id, attempted) or action
                        retry_result = await _click_primary_action(page, retry_action)
                        retry_result.update(planned_context)
                        retry_result["path_id"] = path_item.get("path_id")
                        retry_result["planned_from_node_id"] = current_node_id
                        retry_result["planned_to_node_id"] = target_node_id
                        actions.append(retry_result)
                        step_result["action"] = retry_result
                        await _wait_for_page_flow_settled(
                            page,
                            previous_url=str(retry_result.get("source_url") or before_repair_submit.get("url") or ""),
                            timeout_ms=25_000,
                            min_wait_ms=1_200,
                        )
                        next_snapshot = await _snapshot_page(page, entry_url)
                        step_result["node_execution_trace"].append(
                            _act_trace(
                                path_id=str(path_item.get("path_id") or ""),
                                node_id=current_node_id,
                                target_node_id=target_node_id,
                                action=retry_result,
                            )
                        )
                        step_result["node_execution_trace"].append(
                            _verify_trace(
                                path_id=str(path_item.get("path_id") or ""),
                                node_id=current_node_id,
                                target_node_id=target_node_id,
                                before_snapshot=before_repair_submit,
                                after_snapshot=next_snapshot,
                            )
                        )
                    remaining_unfilled = _unfilled_question_numbers_from_text(
                        str(next_snapshot.get("body_text_excerpt") or "")
                    )
                    if remaining_unfilled:
                        step_result["blocked_reason"] = (
                            "Questionnaire validation failed for question(s): "
                            + ", ".join(str(item) for item in remaining_unfilled)
                        )
                current_unfilled = _unfilled_question_numbers_from_text(str(next_snapshot.get("body_text_excerpt") or ""))
                if (
                    not current_unfilled
                    and not step_result.get("blocked_reason")
                    and not _matches_node_reach_contract(next_snapshot, target_node_id)
                    and _looks_like_choice_page(next_snapshot)
                ):
                    health_actions = await _select_health_notice_safe_option(page)
                    for health_action in health_actions:
                        health_action.update(planned_context)
                        health_action["path_id"] = path_item.get("path_id")
                        health_action["planned_from_node_id"] = current_node_id
                        health_action["planned_to_node_id"] = target_node_id
                        actions.append(health_action)
                        step_result["node_execution_trace"].append(
                            _act_trace(
                                path_id=str(path_item.get("path_id") or ""),
                                node_id=current_node_id,
                                target_node_id=target_node_id,
                                action=health_action,
                            )
                        )
                    if health_actions:
                        before_health_submit = await _snapshot_page(page, entry_url)
                        health_retry_action = _best_action_for_node(before_health_submit, target_node_id, attempted) or action
                        health_retry_result = await _click_primary_action(page, health_retry_action)
                        health_retry_result.update(planned_context)
                        health_retry_result["path_id"] = path_item.get("path_id")
                        health_retry_result["planned_from_node_id"] = current_node_id
                        health_retry_result["planned_to_node_id"] = target_node_id
                        actions.append(health_retry_result)
                        step_result["action"] = health_retry_result
                        await _wait_for_page_flow_settled(
                            page,
                            previous_url=str(health_retry_result.get("source_url") or before_health_submit.get("url") or ""),
                            timeout_ms=25_000,
                            min_wait_ms=1_200,
                        )
                        next_snapshot = await _snapshot_page(page, entry_url)
                        step_result["node_execution_trace"].append(
                            _act_trace(
                                path_id=str(path_item.get("path_id") or ""),
                                node_id=current_node_id,
                                target_node_id=target_node_id,
                                action=health_retry_result,
                            )
                        )
                        step_result["node_execution_trace"].append(
                            _verify_trace(
                                path_id=str(path_item.get("path_id") or ""),
                                node_id=current_node_id,
                                target_node_id=target_node_id,
                                before_snapshot=before_health_submit,
                                after_snapshot=next_snapshot,
                            )
                        )
            validation_repair_actions = await _repair_form_validation_feedback(page)
            if validation_repair_actions:
                for repair_action in validation_repair_actions:
                    repair_action.update(planned_context)
                    repair_action["path_id"] = path_item.get("path_id")
                    repair_action["planned_from_node_id"] = current_node_id
                    repair_action["planned_to_node_id"] = target_node_id
                    actions.append(repair_action)
                    step_result["node_execution_trace"].append(
                        _act_trace(
                            path_id=str(path_item.get("path_id") or ""),
                            node_id=current_node_id,
                            target_node_id=target_node_id,
                            action=repair_action,
                        )
                    )
                before_validation_retry = await _snapshot_page(page, entry_url)
                validation_retry_action = _best_action_for_node(
                    before_validation_retry,
                    target_node_id,
                    attempted,
                )
                if not validation_retry_action and _should_try_transit_action(
                    before_validation_retry,
                    is_terminal_target=is_terminal_target,
                ):
                    validation_retry_action = _best_transit_action(
                        before_validation_retry,
                        target_node_id,
                        attempted,
                    )
                validation_retry_action = validation_retry_action or action
                validation_retry_result = await _click_primary_action(page, validation_retry_action)
                validation_retry_result.update(planned_context)
                validation_retry_result["path_id"] = path_item.get("path_id")
                validation_retry_result["planned_from_node_id"] = current_node_id
                validation_retry_result["planned_to_node_id"] = target_node_id
                actions.append(validation_retry_result)
                step_result["action"] = validation_retry_result
                await _wait_for_page_flow_settled(
                    page,
                    previous_url=str(validation_retry_result.get("source_url") or before_validation_retry.get("url") or ""),
                    timeout_ms=25_000,
                    min_wait_ms=1_200,
                )
                next_snapshot = await _snapshot_page(page, entry_url)
                step_result["node_execution_trace"].append(
                    _act_trace(
                        path_id=str(path_item.get("path_id") or ""),
                        node_id=current_node_id,
                        target_node_id=target_node_id,
                        action=validation_retry_result,
                    )
                )
                step_result["node_execution_trace"].append(
                    _verify_trace(
                        path_id=str(path_item.get("path_id") or ""),
                        node_id=current_node_id,
                        target_node_id=target_node_id,
                        before_snapshot=before_validation_retry,
                        after_snapshot=next_snapshot,
                    )
                )
                remaining_feedback = str(next_snapshot.get("body_text_excerpt") or "")
                if _looks_like_form_validation_feedback(remaining_feedback):
                    oracle_actions, unresolved_requirements = await _repair_unresolved_requirements(
                        page,
                        next_snapshot.get("page_state"),
                        repair_counts,
                    )
                    if oracle_actions:
                        for oracle_action in oracle_actions:
                            oracle_action.update(planned_context)
                            oracle_action["path_id"] = path_item.get("path_id")
                            oracle_action["planned_from_node_id"] = current_node_id
                            oracle_action["planned_to_node_id"] = target_node_id
                            actions.append(oracle_action)
                            step_result["node_execution_trace"].append(
                                _act_trace(
                                    path_id=str(path_item.get("path_id") or ""),
                                    node_id=current_node_id,
                                    target_node_id=target_node_id,
                                    action=oracle_action,
                                )
                            )
                        step_result["oracle_unresolved_requirements"] = unresolved_requirements[:10]
                        before_oracle_retry = await _snapshot_page(page, entry_url)
                        oracle_retry_action = _best_action_for_node(
                            before_oracle_retry,
                            target_node_id,
                            attempted,
                        )
                        if not oracle_retry_action and _should_try_transit_action(
                            before_oracle_retry,
                            is_terminal_target=is_terminal_target,
                        ):
                            oracle_retry_action = _best_transit_action(
                                before_oracle_retry,
                                target_node_id,
                                attempted,
                            )
                        oracle_retry_action = oracle_retry_action or validation_retry_action
                        oracle_retry_result = await _click_primary_action(page, oracle_retry_action)
                        oracle_retry_result.update(planned_context)
                        oracle_retry_result["path_id"] = path_item.get("path_id")
                        oracle_retry_result["planned_from_node_id"] = current_node_id
                        oracle_retry_result["planned_to_node_id"] = target_node_id
                        actions.append(oracle_retry_result)
                        step_result["action"] = oracle_retry_result
                        await _wait_for_page_flow_settled(
                            page,
                            previous_url=str(oracle_retry_result.get("source_url") or before_oracle_retry.get("url") or ""),
                            timeout_ms=25_000,
                            min_wait_ms=1_200,
                        )
                        next_snapshot = await _snapshot_page(page, entry_url)
                        step_result["node_execution_trace"].append(
                            _act_trace(
                                path_id=str(path_item.get("path_id") or ""),
                                node_id=current_node_id,
                                target_node_id=target_node_id,
                                action=oracle_retry_result,
                            )
                        )
                        step_result["node_execution_trace"].append(
                            _verify_trace(
                                path_id=str(path_item.get("path_id") or ""),
                                node_id=current_node_id,
                                target_node_id=target_node_id,
                                before_snapshot=before_oracle_retry,
                                after_snapshot=next_snapshot,
                            )
                        )
                        remaining_feedback = str(next_snapshot.get("body_text_excerpt") or "")
                if _looks_like_form_validation_feedback(remaining_feedback):
                    step_result["blocked_reason"] = (
                        "Form validation still blocks transition: "
                        + " ".join(remaining_feedback.split())[:160]
                    )
            overlay_reason = _blocking_overlay_reason(next_snapshot)
            if overlay_reason and _should_wait_for_submit_processing(target_node_id):
                if _is_processing_overlay_reason(overlay_reason):
                    waited_snapshot, waited_reason = await _wait_for_policy_result_after_processing_overlay(
                        page,
                        entry_url=entry_url,
                        target_node_id=target_node_id,
                    )
                    waited_snapshot.update(planned_context)
                    waited_snapshot["path_id"] = path_item.get("path_id")
                    next_snapshot = waited_snapshot
                    if waited_reason is None:
                        step_result["node_execution_trace"].append(
                            _verify_trace(
                                path_id=str(path_item.get("path_id") or ""),
                                node_id=current_node_id,
                                target_node_id=target_node_id,
                                before_snapshot=snapshot,
                                after_snapshot=next_snapshot,
                            )
                        )
                    else:
                        overlay_reason = waited_reason
                if overlay_reason:
                    step_result["status"] = "blocked_by_overlay"
                    step_result["blocked_reason"] = overlay_reason
                    break
            if (
                next_snapshot.get("url") == snapshot.get("url")
                and next_snapshot.get("dom_signature") == before_signature
            ):
                step_result["status"] = "action_no_state_change"
                overlay_reason = _blocking_overlay_reason(next_snapshot)
                if overlay_reason:
                    if _should_wait_for_submit_processing(target_node_id) and _is_processing_overlay_reason(overlay_reason):
                        next_snapshot, waited_reason = await _wait_for_policy_result_after_processing_overlay(
                            page,
                            entry_url=entry_url,
                            target_node_id=target_node_id,
                        )
                        next_snapshot.update(planned_context)
                        next_snapshot["path_id"] = path_item.get("path_id")
                        overlay_reason = waited_reason
                    if overlay_reason is None:
                        continue
                    step_result["status"] = "blocked_by_overlay"
                    step_result["blocked_reason"] = overlay_reason
                    break
                if step_result.get("blocked_reason"):
                    pass
                elif not is_terminal_target or _looks_like_choice_page(snapshot) or not _is_forward_action(action):
                    step_result["blocked_reason"] = None
                else:
                    step_result["blocked_reason"] = f"Action did not change page state for planned node {target_node_id}"
                continue
        except Exception as exc:
            message = f"Planned path {path_item.get('path_id')} action failed for {target_node_id}: {exc}"
            warnings.append(message)
            step_result["status"] = "action_failed"
            step_result["blocked_reason"] = message
            break

    return snapshots, actions, step_results, warnings


async def _drive_action_chain(
    page: Any,
    *,
    source_url: str,
    planned_context: dict[str, Any],
    existing_pages: list[dict[str, Any]],
    max_pages: int,
    max_depth: int = 4,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    snapshots: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    warnings: list[str] = []
    attempted: set[str] = set()

    for _ in range(max_depth):
        if len(existing_pages) + len(snapshots) >= max_pages:
            break
        current_snapshot = await _snapshot_page(page, source_url)
        current_snapshot.update(planned_context)
        primary_actions = list(current_snapshot.get("primary_actions", []) or [])
        action = next(
            (
                candidate
                for candidate in primary_actions
                if f"{page.url}|{candidate.get('selector')}|{candidate.get('text')}" not in attempted
            ),
            None,
        )
        if not action:
            break

        attempt_key = f"{page.url}|{action.get('selector')}|{action.get('text')}"
        attempted.add(attempt_key)
        before_signature = current_snapshot.get("dom_signature")
        try:
            action_result = await _click_primary_action(page, action)
            action_result.update(planned_context)
            actions.append(action_result)
            await _wait_for_page_flow_settled(
                page,
                previous_url=str(action_result.get("source_url") or current_snapshot.get("url") or ""),
                timeout_ms=25_000,
                min_wait_ms=1_200,
            )
            next_snapshot = await _snapshot_page(page, source_url)
            next_snapshot["triggered_by"] = action_result
            next_snapshot.update(planned_context)
            snapshots.append(next_snapshot)
            if (
                next_snapshot.get("url") == current_snapshot.get("url")
                and next_snapshot.get("dom_signature") == before_signature
            ):
                break
        except Exception as exc:
            warnings.append(f"Planned primary action failed for {page.url}: {exc}")
            break

    return snapshots, actions, warnings


def _materialise_explore_outputs(
    root_dir: Path,
    product_id: str,
    entry_url: str | None,
    page_registry: dict[str, Any],
    explore_trace: dict[str, Any],
    product_dir: str | Path | None = None,
) -> None:
    platform = platform_from_entry_url(entry_url)
    agent3_dir = agent_artifact_dir(root_dir, product_id, "agent3", product_dir=product_dir)
    output_dir = agent3_dir / "ts-gen" / platform
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "page-registry.json").write_text(
        json.dumps(page_registry, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "explore-trace.json").write_text(
        json.dumps(explore_trace, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    explore_dir = agent3_dir / "explore"
    explore_dir.mkdir(parents=True, exist_ok=True)
    path_exploration_results = list(page_registry.get("path_exploration_results", []) or [])
    page_content_records = list(page_registry.get("page_content_records", []) or [])
    for filename, payload in {
        "planned-page-catalog.json": page_registry.get("planned_page_catalog", []),
        "page-content-records.json": page_content_records,
        "page-elements.json": _page_elements_from_records(page_content_records or []),
        "path-exploration-results.json": path_exploration_results,
        "action-trace.json": _action_trace_from_path_results(path_exploration_results),
        "main-flow-progress.json": _main_flow_progress_from_results(path_exploration_results),
        "exploration-contract.json": page_registry.get("exploration_contract", {}),
        "explore-trace.json": explore_trace,
    }.items():
        (explore_dir / filename).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    if page_content_records:
        write_dom_signature_bundles(
            root_dir=root_dir,
            product_id=product_id,
            records=page_content_records,
            product_dir=product_dir,
        )


async def _install_agent3_mock_data(page: Any, mock_data: dict[str, Any] | None) -> None:
    if not isinstance(mock_data, dict) or not mock_data:
        return
    payload = json.dumps(mock_data, ensure_ascii=False, default=str)
    script = (
        f"window.__agent3MockData = {payload};\n"
        "window.__agent3MockDataSource = 'state.mock_data';\n"
        "if (window.__agent3MockData.payAccount_107) {\n"
        "  window.__agent3PayAccountValue = String(window.__agent3MockData.payAccount_107);\n"
        "}\n"
    )
    try:
        await page.add_init_script(script)
    except Exception:
        pass
    try:
        await page.evaluate(f"() => {{ {script} }}")
    except Exception:
        pass


async def run_live_exploration(
    *,
    product_id: str,
    entry_url: str | None,
    root_dir: Path,
    runtime_context: dict[str, Any] | None = None,
    regression_paths: list[dict[str, Any]] | None = None,
    regression_flow: dict[str, Any] | None = None,
    mock_data: dict[str, Any] | None = None,
    product_dir: str | Path | None = None,
    materialise: bool = True,
    max_pages: int = 8,
) -> dict[str, Any]:
    warnings: list[str] = []
    if not entry_url:
        return {
            "page_registry": {"product_id": product_id, "entry_url": entry_url, "pages": []},
            "explore_trace": {"visited_urls": [], "warnings": ["entry_url is required for live exploration"]},
            "warnings": ["entry_url is required for live exploration"],
        }

    queue = [entry_url]
    visited: set[str] = set()
    pages: list[dict[str, Any]] = []
    action_trace: list[dict[str, Any]] = []
    planned_step_results: dict[str, list[dict[str, Any]]] = {}
    planned_context = _planned_path_context(regression_paths)
    planned_page_catalog = _build_planned_page_catalog(regression_paths)
    exploration_cache: dict[str, dict[str, Any]] = {}
    completed_path_reuse_cache: dict[str, dict[str, Any]] = {}

    agent3_headless = str(os.environ.get("AGENT3_HEADLESS", "1")).lower() not in {
        "0",
        "false",
        "no",
    }
    agent3_slow_mo = int(os.environ.get("AGENT3_SLOW_MO", "0") or "0")

    async with BrowserSession(
        headless=agent3_headless,
        slow_mo=agent3_slow_mo,
        viewport=_platform_viewport(entry_url),
        storage_state_path=str((runtime_context or {}).get("storage_state_path") or ""),
        record_storage_state=bool((runtime_context or {}).get("storage_state_path")),
    ) as session:
        if session.page is None:
            raise RuntimeError("BrowserSession did not provide a page")
        await _install_submit_trace(session.page)
        await _install_agent3_mock_data(session.page, mock_data)

        if regression_paths:
            for path_item in regression_paths:
                path_id = str(path_item.get("path_id") or "")
                cache_key = _exploration_cache_key(path_item)
                if cache_key in exploration_cache:
                    cached = exploration_cache[cache_key]
                    planned_step_results[path_id] = _clone_step_results_for_path(
                        list(cached.get("steps", []) or []),
                        path_id=path_id,
                    )
                    action_trace.extend(
                        _clone_action_trace_for_path(
                            list(cached.get("actions", []) or []),
                            path_id=path_id,
                        )
                    )
                    continue
                reuse_key = _completed_path_reuse_key(path_item)
                if reuse_key and reuse_key in completed_path_reuse_cache:
                    cached = completed_path_reuse_cache[reuse_key]
                    reused_from_path_id = str(cached.get("representative_path_id") or "")
                    planned_step_results[path_id] = _clone_step_results_for_path(
                        list(cached.get("steps", []) or []),
                        path_id=path_id,
                        reused_from_path_id=reused_from_path_id,
                    )
                    action_trace.extend(
                        _clone_action_trace_for_path(
                            list(cached.get("actions", []) or []),
                            path_id=path_id,
                            reused_from_path_id=reused_from_path_id,
                        )
                    )
                    exploration_cache[cache_key] = {
                        "representative_path_id": reused_from_path_id,
                        "reused_by_path_id": path_id,
                        "reuse_key": reuse_key,
                        "reuse_source": "agent3.completed_path",
                        "steps": planned_step_results[path_id],
                        "actions": list(cached.get("actions", []) or []),
                        "complete": True,
                        "action_count": int(cached.get("action_count") or 0),
                        "snapshot_count": int(cached.get("snapshot_count") or 0),
                    }
                    warnings.append(
                        f"Path {path_id} reused completed Agent3 chain from {reused_from_path_id}; same entry and target page path"
                    )
                    continue
                path_context = {
                    **planned_context,
                    "planned_path_id": path_id,
                    "planned_route_nodes": [
                        str(node_id)
                        for node_id in path_item.get("nodes", []) or []
                        if node_id
                    ],
                }
                attempts: list[tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[str]]] = []
                last_progress_key: tuple[tuple[str, ...], int] | None = None
                max_path_attempts = _agent3_path_attempt_limit()
                for attempt_index in range(1, max_path_attempts + 1):
                    try:
                        next_snapshots, next_actions, next_steps, next_warnings = await asyncio.wait_for(
                            _drive_planned_path(
                                session.page,
                                path_item=path_item,
                                entry_url=entry_url,
                                planned_context={**path_context, "exploration_attempt": attempt_index},
                                max_steps=max(len(path_item.get("page_keys", []) or []) + 8, 8),
                            ),
                            timeout=_PATH_ATTEMPT_TIMEOUT_S,
                        )
                    except TimeoutError:
                        timeout_warning = (
                            f"Path {path_id} exploration attempt {attempt_index}/{max_path_attempts} "
                            f"timed out after {_PATH_ATTEMPT_TIMEOUT_S}s"
                        )
                        recovery_snapshot = None
                        try:
                            recovery_snapshot = await _snapshot_page(session.page, entry_url)
                            recovery_snapshot.update({**path_context, "exploration_attempt": attempt_index})
                            recovery_snapshot["path_id"] = path_id
                        except Exception:
                            recovery_snapshot = None
                        next_snapshots, next_actions, next_steps, next_warnings = _timeout_recovery_attempt(
                            path_id=path_id,
                            path_item=path_item,
                            current_snapshot=recovery_snapshot,
                            current_url=str(getattr(session.page, "url", "") or entry_url),
                            warning=timeout_warning,
                            attempt_index=attempt_index,
                        )
                    page_trace_blocker_reason = _submit_trace_blocker_reason(session.page)
                    if page_trace_blocker_reason:
                        if page_trace_blocker_reason.startswith("Bank card validation loop"):
                            direct_submit_action = await _direct_submit_after_bank_validation_loop(
                                session.page,
                                source_url=str(getattr(session.page, "url", "") or entry_url),
                            )
                            if direct_submit_action:
                                direct_submit_action.update({**path_context, "exploration_attempt": attempt_index})
                                direct_submit_action["path_id"] = path_id
                                direct_submit_action.setdefault("planned_from_node_id", "NODE-insure-form")
                                direct_submit_action.setdefault("planned_to_node_id", "NODE-underwriting")
                                next_actions.append(direct_submit_action)
                                if next_steps:
                                    next_steps[-1]["action"] = direct_submit_action
                                    next_steps[-1]["status"] = "action_progress"
                                    next_steps[-1]["blocked_reason"] = None
                                    next_steps[-1].setdefault("node_execution_trace", []).append(
                                        _act_trace(
                                            path_id=path_id,
                                            node_id=str(next_steps[-1].get("planned_node_id") or "NODE-insure-form"),
                                            target_node_id=str(
                                                direct_submit_action.get("planned_to_node_id") or "NODE-underwriting"
                                            ),
                                            action=direct_submit_action,
                                        )
                                    )
                                try:
                                    direct_submit_snapshot = await _snapshot_page(session.page, entry_url)
                                    direct_submit_snapshot.update({**path_context, "exploration_attempt": attempt_index})
                                    direct_submit_snapshot["path_id"] = path_id
                                    next_snapshots.append(direct_submit_snapshot)
                                    if next_steps:
                                        next_steps[-1].setdefault("node_execution_trace", []).append(
                                            _verify_trace(
                                                path_id=path_id,
                                                node_id=str(next_steps[-1].get("planned_node_id") or "NODE-insure-form"),
                                                target_node_id=str(
                                                    direct_submit_action.get("planned_to_node_id") or "NODE-underwriting"
                                                ),
                                                before_snapshot=next_snapshots[-2] if len(next_snapshots) > 1 else direct_submit_snapshot,
                                                after_snapshot=direct_submit_snapshot,
                                            )
                                        )
                                except Exception:
                                    pass
                                page_trace_blocker_reason = _submit_trace_blocker_reason(session.page)
                        if not page_trace_blocker_reason:
                            pass
                        else:
                            boundary_action = _submit_trace_blocker_action(
                                session.page,
                                page_trace_blocker_reason,
                                source_url=str(getattr(session.page, "url", "") or entry_url),
                            )
                            if not any(
                                str(item.get("click_strategy") or "")
                                == str(boundary_action.get("click_strategy") or "")
                                for item in next_actions
                                if isinstance(item, dict)
                            ):
                                next_actions.append(boundary_action)
                            if next_steps:
                                next_steps[-1]["status"] = "blocked"
                                next_steps[-1]["blocked_reason"] = page_trace_blocker_reason
                                next_steps[-1].setdefault("action", boundary_action)
                    order_handoff_action = _submit_trace_order_handoff_action(
                        session.page,
                        source_url=str(getattr(session.page, "url", "") or entry_url),
                    )
                    if order_handoff_action and not any(
                        isinstance(item, dict)
                        and isinstance(item.get("submit_api_result"), dict)
                        and item.get("submit_api_result", {}).get("order_generated")
                        for item in next_actions
                    ):
                        order_handoff_action.update({**path_context, "exploration_attempt": attempt_index})
                        order_handoff_action["path_id"] = path_id
                        next_actions.append(order_handoff_action)
                        if next_steps:
                            next_steps[-1]["action"] = order_handoff_action
                            next_steps[-1]["status"] = "action_progress"
                            next_steps[-1]["blocked_reason"] = None
                            next_steps[-1].setdefault("node_execution_trace", []).append(
                                _act_trace(
                                    path_id=path_id,
                                    node_id=str(next_steps[-1].get("planned_node_id") or "NODE-insure-form"),
                                    target_node_id=str(
                                        order_handoff_action.get("planned_to_node_id") or "NODE-underwriting"
                                    ),
                                    action=order_handoff_action,
                                )
                            )
                    for step in next_steps:
                        step.setdefault("exploration_attempt", attempt_index)
                    attempts.append((next_snapshots, next_actions, next_steps, next_warnings))
                    if _steps_complete_for_path(path_item, next_steps):
                        break
                    non_retryable_reason = _non_retryable_trace_boundary_reason(next_steps, next_actions)
                    if non_retryable_reason:
                        warnings.append(
                            f"Path {path_id} stopped self-healing after non-retryable Agent3 boundary: {non_retryable_reason}"
                        )
                        break
                    progress_key = _attempt_progress_key(next_steps, next_actions)
                    if attempt_index == 1 and not progress_key[0] and progress_key[1] == 0:
                        warnings.append(
                            f"Path {path_id} exploration attempt {attempt_index}/{max_path_attempts} made no page progress; continuing self-healing"
                        )
                    elif last_progress_key is not None and progress_key == last_progress_key:
                        warnings.append(
                            f"Path {path_id} exploration attempt {attempt_index}/{max_path_attempts} made no new progress; continuing self-healing"
                        )
                    last_progress_key = progress_key
                    if attempt_index < max_path_attempts:
                        warnings.append(f"Path {path_id} exploration attempt {attempt_index}/{max_path_attempts} incomplete; self-healing retrying")
                    else:
                        warnings.append(f"Path {path_id} blocked after {max_path_attempts} incomplete self-healing attempts")
                next_snapshots, next_actions, next_steps, next_warnings = _best_step_attempt(attempts)
                pages.extend(next_snapshots)
                action_trace.extend(next_actions)
                planned_step_results[path_id] = next_steps
                exploration_cache[cache_key] = {
                    "representative_path_id": path_id,
                    "steps": next_steps,
                    "actions": next_actions,
                    "complete": _steps_complete_for_path(path_item, next_steps),
                    "action_count": len(next_actions),
                    "snapshot_count": len(next_snapshots),
                }
                if _steps_complete_for_path(path_item, next_steps):
                    reuse_key = _completed_path_reuse_key(path_item)
                    if reuse_key:
                        completed_path_reuse_cache.setdefault(
                            reuse_key,
                            {
                                "representative_path_id": path_id,
                                "steps": next_steps,
                                "actions": next_actions,
                                "complete": True,
                                "action_count": len(next_actions),
                                "snapshot_count": len(next_snapshots),
                            },
                        )
                warnings.extend(next_warnings)
                for snapshot in next_snapshots:
                    if snapshot.get("url"):
                        visited.add(str(snapshot.get("url")))
                if not _steps_complete_for_path(path_item, next_steps):
                    warnings.append(
                        f"Path {path_id} did not pass Agent3 validation; stop exploring later paths until this path is healed"
                    )
                    break
        else:
            while queue and len(pages) < max_pages:
                target = queue.pop(0)
                if target in visited:
                    continue
                visited.add(target)
                try:
                    await session.page.goto(target, wait_until="domcontentloaded", timeout=30_000)
                    await _safe_wait(session.page)
                    if target == entry_url:
                        await _wait_for_entry_ready(session.page, entry_url)
                    snapshot = await _snapshot_page(session.page, target)
                    snapshot.update(planned_context)
                    pages.append(snapshot)
                    if snapshot.get("primary_actions") and len(pages) < max_pages:
                        next_snapshots, next_actions, next_warnings = await _drive_action_chain(
                            session.page,
                            source_url=target,
                            planned_context=planned_context,
                            existing_pages=pages,
                            max_pages=max_pages,
                        )
                        action_trace.extend(next_actions)
                        warnings.extend(next_warnings)
                        for next_snapshot in next_snapshots:
                            if len(pages) >= max_pages:
                                break
                            pages.append(next_snapshot)
                            if next_snapshot.get("url"):
                                visited.add(str(next_snapshot.get("url")))
                    for link in snapshot["candidate_links"]:
                        if link not in visited and link not in queue:
                            queue.append(link)
                except Exception as exc:
                    warnings.append(f"Live exploration failed for {target}: {exc}")

        if str(os.environ.get("AGENT3_KEEP_BROWSER_OPEN", "")).lower() in {"1", "true", "yes"}:
            print("AGENT3_KEEP_BROWSER_OPEN=1; browser remains open for inspection.")
            while True:
                await asyncio.sleep(3600)

    page_content_records = _build_page_content_records(pages, planned_page_catalog)
    page_registry = {
        "product_id": product_id,
        "entry_url": entry_url,
        "platform": platform_from_entry_url(entry_url),
        "generated_by": "explore_agent.live",
        "runtime_context": runtime_context or {},
        "planned_flow_version": (regression_flow or {}).get("flow_version"),
        "planned_page_catalog": planned_page_catalog,
        "page_content_records": page_content_records,
        "pages": pages,
        "primary_actions": sorted(
            [
                action
                for page in pages
                for action in page.get("primary_actions", [])
            ],
            key=lambda item: (-int(item.get("score", 0)), str(item.get("source_url") or "")),
        )[:5],
    }
    path_exploration_results = _build_path_exploration_results(
        regression_paths,
        pages,
        action_trace,
        planned_step_results,
        planned_page_catalog,
        page_content_records,
    )
    page_registry["path_exploration_results"] = path_exploration_results
    exploration_contract = _build_exploration_contract(path_exploration_results)
    page_registry["exploration_contract"] = exploration_contract
    explore_trace = {
        "product_id": product_id,
        "visited_urls": list(visited),
        "discovered_page_count": len(pages),
        "planned_path_ids": planned_context["planned_path_ids"],
        "planned_page_count": len(planned_page_catalog),
        "page_content_record_count": len(page_content_records),
        "exploration_cache": exploration_cache,
        "action_trace": action_trace,
        "exploration_contract": exploration_contract,
        "path_exploration_summary": {
            "total": len(path_exploration_results),
            "explored": sum(1 for item in path_exploration_results if item.get("path_status") == "explored"),
            "partial": sum(1 for item in path_exploration_results if item.get("path_status") == "partial"),
            "blocked": sum(1 for item in path_exploration_results if item.get("path_status") == "blocked"),
        },
        "session_reused": bool((runtime_context or {}).get("session_reused")),
        "warnings": warnings,
    }

    if materialise:
        _materialise_explore_outputs(
            root_dir,
            product_id,
            entry_url,
            page_registry,
            explore_trace,
            product_dir=product_dir,
        )

    return {
        "page_registry": page_registry,
        "explore_trace": explore_trace,
        "warnings": warnings,
    }

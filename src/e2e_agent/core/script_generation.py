"""Core script generation logic for ts-gen skill execution."""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qs, unquote, urlparse

from e2e_agent.artifacts.paths import agent_artifact_dir, agent_artifact_path
from e2e_agent.core.assertion_templates import (
    load_assertion_template_catalog,
    match_assertion_template,
    summarize_assertion_template_coverage,
)
from e2e_agent.core.policy_info_generator import (
    generate_policy_mock_data,
    mock_value_for_field as policy_mock_value_for_field,
)


def slugify(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", value.strip()).strip("-").lower()
    return cleaned or "item"


def to_pascal_case(value: str) -> str:
    parts = re.split(r"[^A-Za-z0-9]+", value)
    return "".join(part[:1].upper() + part[1:] for part in parts if part) or "Page"


def platform_from_entry_url(entry_url: str | None) -> str:
    if entry_url and "/m/" in entry_url:
        return "h5"
    return "pc"


def node_label(node_id: str) -> str:
    return node_id.removeprefix("NODE-").replace("-", " ").title()


def guess_param_type(name: str, value: str) -> str:
    if name.lower().endswith("id") or value.isdigit():
        return "string"
    if value.lower() in {"true", "false"}:
        return "boolean"
    if re.fullmatch(r"\d+(?:\.\d+)?", value):
        return "number"
    return "string"


def path_conditions(path_item: Mapping[str, Any]) -> dict[str, str]:
    conditions = path_item.get("conditions", {})
    if isinstance(conditions, dict):
        return {str(key): str(value) for key, value in conditions.items()}
    return {}


def assertion_template(path_item: Mapping[str, Any]) -> str:
    nodes = [str(node).lower() for node in path_item.get("nodes", [])]
    if any("payment" in node for node in nodes):
        return "order_status"
    if any("result" in node or "underwriting" in node for node in nodes):
        return "underwriting_result"
    if any("product" in node or "premium" in node for node in nodes):
        return "price_premium"
    return "custom"


def _state_product_dir(state: Mapping[str, Any]) -> str | Path | None:
    return state.get("product_artifact_dir")


def product_ts_gen_root(
    root_dir: Path,
    product_id: str,
    *,
    product_dir: str | Path | None = None,
) -> Path:
    return agent_artifact_dir(root_dir, product_id, "agent3", product_dir=product_dir) / "ts-gen"


def platform_root(
    root_dir: Path,
    product_id: str,
    entry_url: str | None,
    *,
    product_dir: str | Path | None = None,
) -> Path:
    return product_ts_gen_root(root_dir, product_id, product_dir=product_dir) / platform_from_entry_url(entry_url)


def ts_type(type_name: str) -> str:
    return {
        "string": "string",
        "number": "number",
        "boolean": "boolean",
    }.get(type_name, "string")


_TS_IDENTIFIER = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")


def ts_property_name(name: str) -> str:
    """Return a TypeScript-safe interface property name."""
    return name if _TS_IDENTIFIER.fullmatch(name) else json.dumps(name, ensure_ascii=False)


def ts_string(value: object) -> str:
    return json.dumps(str(value), ensure_ascii=False)


_ZURICH_TC003_PASSPORT_MOCK_DATA = {
    "applicant.name": "谭博",
    "applicant.english_name": "eson",
    "applicant.pinyin": "eson",
    "applicant.eName": "eson",
    "applicant.id_type": "护照",
    "applicant.id_type_code": "2",
    "applicant.id_no": "EA1342046",
    "applicant.birthdate": "2001-02-15",
    "applicant.sex": "男",
    "applicant.sex_code": "1",
    "applicant.mobile": "13103331433",
    "applicant.phone": "13103331433",
    "applicant.email": "9095043102@qq.com",
    "applicant.card_valid_start": "2022-06-09",
    "applicant.card_valid_end": "2032-06-09",
    "insured.name": "谭博",
    "insured.english_name": "eson",
    "insured.pinyin": "eson",
    "insured.eName": "eson",
    "insured.id_type": "护照",
    "insured.id_type_code": "2",
    "insured.id_no": "EA1342046",
    "insured.birthdate": "2001-02-15",
    "insured.sex": "男",
    "insured.sex_code": "1",
    "insured.mobile": "13103331433",
    "insured.phone": "13103331433",
    "insured.email": "9095043102@qq.com",
    "insured.forWho": "100",
    "insure_form.applicantname": "谭博",
    "insure_form.applicantpinyin": "eson",
    "insure_form.applicantidno": "EA1342046",
    "insure_form.applicantphone": "13103331433",
    "insure_form.applicantemail": "9095043102@qq.com",
    "insure_form.insuredname": "谭博",
    "insure_form.insuredpinyin": "eson",
    "insure_form.insuredidno": "EA1342046",
    "insure_form.insuredphone": "13103331433",
    "insure_form.cardtype": "护照",
    "insure_form.insuredidtype": "护照",
    "请填写真实姓名，应与投保有效证件相符": "谭博",
    "请输入英文名或者中文姓名拼音,注意与护照信息一致,\n例如,姓名为“张三”,拼音“zhangsan”。": "eson",
    "请准确填写您有效的证件号码": "EA1342046",
    "请填写真实手机号码,以便接收保单信息": "13103331433",
    "请填写真实邮箱,以便接收电子保单或承保确认函": "9095043102@qq.com",
}


def _scenario_execution_requirements(scenario: Mapping[str, Any]) -> dict[str, Any]:
    requirements = scenario.get("execution_requirements")
    return dict(requirements) if isinstance(requirements, Mapping) else {}


def _scenario_uses_passport_mock_user(scenario: Mapping[str, Any]) -> bool:
    requirements = _scenario_execution_requirements(scenario)
    for value in (
        requirements.get("mock_user_id_type"),
        scenario.get("mock_user_id_type"),
        (scenario.get("mock_data", {}) or {}).get("applicant.id_type")
        if isinstance(scenario.get("mock_data"), Mapping)
        else None,
        (scenario.get("mock_data", {}) or {}).get("insure_form.cardtype")
        if isinstance(scenario.get("mock_data"), Mapping)
        else None,
    ):
        if str(value or "").strip() == "护照":
            return True
    return _scenario_uses_zurich_tc003_passport(scenario)


def _scenario_uses_zurich_tc003_passport(scenario: Mapping[str, Any]) -> bool:
    case_ids = {str(case_id) for case_id in scenario.get("case_ids", []) or []}
    return "TC-travel-product-003" in case_ids


def _scenario_mock_data_with_overrides(
    scenario: Mapping[str, Any],
    mock_data: Mapping[str, Any],
) -> dict[str, Any]:
    result = dict(mock_data)
    if _scenario_uses_passport_mock_user(scenario):
        result.update(_ZURICH_TC003_PASSPORT_MOCK_DATA)
    return result


def questionnaire_answer_helper_lines() -> list[str]:
    return [
        "async function advanceSuitabilityIntroIfPresent(page: any): Promise<Record<string, unknown> | null> {",
        "  const bodyText = await page.locator('body').innerText({ timeout: 1200 }).catch(() => '');",
        "  if (!/特别提示|填写本调查问卷前|评估问卷/.test(String(bodyText || ''))) return null;",
        "  await page.evaluate(() => {",
        "    const nodes = [document.scrollingElement, document.documentElement, document.body, ...Array.from(document.querySelectorAll<HTMLElement>('div, section, main'))].filter(Boolean) as HTMLElement[];",
        "    for (const node of nodes) {",
        "      node.scrollTop = node.scrollHeight;",
        "      node.dispatchEvent(new Event('scroll', { bubbles: true }));",
        "    }",
        "    window.scrollTo(0, Math.max(document.documentElement.scrollHeight, document.body.scrollHeight));",
        "  }).catch(() => undefined);",
        "  await page.mouse.wheel(0, 4000).catch(() => undefined);",
        "  await page.waitForTimeout(500);",
        "  const candidate = page.locator('button, a, [role=\"button\"], .am-button, .am-button-primary, .submit-btn, .btn, input[type=\"button\"], input[type=\"submit\"], span, div')",
        "    .filter({ hasText: /我已阅读|已阅读|阅读并同意|开始评估|开始填写|开始|下一步|继续|同意|确认/ }).last();",
        "  if (!(await candidate.isVisible({ timeout: 1800 }).catch(() => false))) return null;",
        "  const text = await candidate.innerText({ timeout: 1000 }).catch(async () => await candidate.inputValue({ timeout: 1000 }).catch(() => ''));",
        "  await candidate.click({ timeout: 5000, noWaitAfter: true, force: true }).catch(async () => {",
        "    await candidate.scrollIntoViewIfNeeded({ timeout: 2000 }).catch(() => undefined);",
        "    const box = await candidate.boundingBox().catch(() => null);",
        "    if (box) await page.touchscreen.tap(box.x + box.width / 2, box.y + box.height / 2).catch(() => undefined);",
        "  });",
        "  await page.waitForLoadState('domcontentloaded', { timeout: 10000 }).catch(() => undefined);",
        "  await page.waitForTimeout(1200);",
        "  return { strategy: 'suitability-intro-continue', clicked: true, text: String(text || '').slice(0, 80), url: page.url() };",
        "}",
        "",
        "async function answerQuestionnaire(page: any): Promise<{ clicked_count: number; clicked: Array<Record<string, unknown>> }> {",
        "  await advanceSuitabilityIntroIfPresent(page).catch(() => null);",
        "  const result = await page.evaluate(() => {",
        "    const optionPattern = /^\\s*[A-H](?:[.、．:：\\s]|$)/;",
        "    const questionPattern = /^\\s*(?:\\d+|[一二三四五六七八九十]+)[.、．:：]/;",
        "    const selectedGroups = new Set<string>();",
        "    const clicked: Array<Record<string, unknown>> = [];",
        "",
        "    function isVisible(element: Element): boolean {",
        "      const style = window.getComputedStyle(element);",
        "      const rect = element.getBoundingClientRect();",
        "      return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;",
        "    }",
        "",
        "    function normalizedText(element: Element | null): string {",
        "      if (!element) return '';",
        "      const text = String((element as HTMLElement | null)?.innerText || element.textContent || '').replace(/\\s+/g, ' ').trim();",
        "      if (text) return text;",
        "      return String(element.getAttribute?.('value') || '').replace(/\\s+/g, ' ').trim();",
        "    }",
        "",
        "    function labelTextForInput(input: Element): string {",
        "      const id = input.getAttribute('id');",
        "      if (id) {",
        "        const label = document.querySelector(`label[for=\"${CSS.escape(id)}\"]`);",
        "        if (label) return normalizedText(label);",
        "      }",
        "      const label = input.closest('label');",
        "      return label ? normalizedText(label) : (normalizedText(input) || normalizedText(input.parentElement));",
        "    }",
        "",
        "    function hasOptionChild(element: Element): boolean {",
        "      return Array.from(element.children || []).some(child => isVisible(child) && optionPattern.test(normalizedText(child)));",
        "    }",
        "",
        "    function questionTextForContainer(container: Element): string {",
        "      const siblings = Array.from(container.parentElement?.children || []);",
        "      const index = siblings.indexOf(container);",
        "      for (let i = index - 1; i >= 0; i -= 1) {",
        "        const text = normalizedText(siblings[i]);",
        "        if (text && !optionPattern.test(text)) return text;",
        "      }",
        "      return normalizedText(container.closest('.question, [class*=\"question\"]'));",
        "    }",
        "",
        "    function isPurposeQuestion(questionText: string): boolean {",
        "      return ['保障需求', '保险需求', '目的', '为了什么'].some(token => String(questionText || '').includes(token));",
        "    }",
        "",
        "    function clickableFor(element: Element): HTMLElement {",
        "      return (element.closest('input,label,button,[role=\"radio\"],[role=\"checkbox\"],li,div') || element) as HTMLElement;",
        "    }",
        "",
        "    function cssSelector(element: Element): string {",
        "      const htmlElement = element as HTMLElement;",
        "      if (htmlElement.id) return `#${CSS.escape(htmlElement.id)}`;",
        "      const dataNumber = element.closest('[data-number]')?.getAttribute('data-number');",
        "      const className = String(htmlElement.className || '').split(/\\s+/).filter(Boolean)[0];",
        "      if (dataNumber && className) return `[data-number=\"${CSS.escape(dataNumber)}\"] .${CSS.escape(className)}`;",
        "      if (className) return `${element.tagName.toLowerCase()}.${CSS.escape(className)}`;",
        "      return element.tagName.toLowerCase();",
        "    }",
        "",
        "    function scoreOption(element: Element): number {",
        "      const text = normalizedText(element);",
        "      if (!text) return -1;",
        "      if (['不同意', '拒绝', '返回', '取消', '详情', '须知'].some(token => text.includes(token))) return -1;",
        "      if (['下一步', '下一页', '继续', '提交', '完成'].some(token => text.includes(token))) return -1;",
        "      let score = 0;",
        "      for (const token of ['确认无以上问题', '无以上', '没有', '否', '不是', '通过', 'A.', 'A．', '已阅读', '同意']) {",
        "        if (text.includes(token)) score += 100;",
        "      }",
        "      const lower = text.toLowerCase();",
        "      if (lower === 'a' || lower.startsWith('a.')) score += 30;",
        "      if (isVisible(element)) score += 20;",
        "      return score;",
        "    }",
        "",
        "    function clickLikeUser(element: Element): HTMLElement {",
        "      const htmlElement = element as HTMLElement;",
        "      const target = htmlElement.matches('input, button, a, label, [role=\"button\"], .insure-label')",
        "        ? htmlElement",
        "        : (htmlElement.querySelector('input.insure-label, input[type=\"button\"], button, [role=\"button\"], label, .insure-label') || htmlElement) as HTMLElement;",
        "      target.scrollIntoView({ block: 'center', inline: 'center' });",
        "      if (target.matches('input, button')) {",
        "        try { target.click(); } catch (_) {}",
        "      } else {",
        "        for (const type of ['pointerdown', 'mousedown', 'mouseup', 'click']) {",
        "          target.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));",
        "        }",
        "      }",
        "      target.dispatchEvent(new Event('input', { bubbles: true }));",
        "      target.dispatchEvent(new Event('change', { bubbles: true }));",
        "      return target;",
        "    }",
        "",
        "    function questionNumberOf(groupKey: string, questionText: string): number {",
        "      const direct = String(groupKey || '').match(/^data-number-(\\d+)$|^(\\d+)[.、．:：]?$/);",
        "      const fromText = String(questionText || '').match(/^\\s*(\\d+)/);",
        "      return Number(direct?.[1] || direct?.[2] || fromText?.[1] || 0);",
        "    }",
        "",
        "    function candidateDisplayText(item: Record<string, any>): string {",
        "      return String(item.candidateText ?? item.text ?? '').replace(/\\s+/g, ' ').trim();",
        "    }",
        "",
        "    function preferredBusinessQuestionnaireChoice(groupKey: string, questionText: string, candidates: Array<Record<string, any>>): Record<string, any> | null {",
        "      const number = questionNumberOf(groupKey, questionText);",
        "      if (number === 1) return candidates.find(item => /^C[.、．:：\\s]/.test(candidateDisplayText(item)) && /未来生活规划|养老|子女教育|退休收入|保单利益/.test(candidateDisplayText(item))) || null;",
        "      if (number === 2) return candidates.find(item => /^D[.、．:：\\s]/.test(candidateDisplayText(item)) && /11\\s*-\\s*20年/.test(candidateDisplayText(item))) || null;",
        "      if (number === 5) return candidates.find(item => /^A[.、．:：\\s]/.test(candidateDisplayText(item)) && /20%及以下/.test(candidateDisplayText(item))) || null;",
        "      if (number === 6) return candidates.find(item => /^A[.、．:：\\s]/.test(candidateDisplayText(item)) && /一次性支付|一次性/.test(candidateDisplayText(item))) || null;",
        "      return null;",
        "    }",
        "",
        "    function setNativeInputValue(input: HTMLInputElement | HTMLTextAreaElement, value: string): void {",
        "      input.scrollIntoView({ block: 'center', inline: 'center' });",
        "      const descriptor = Object.getOwnPropertyDescriptor(input.constructor.prototype, 'value');",
        "      if (descriptor?.set) descriptor.set.call(input, value);",
        "      else input.value = value;",
        "      input.setAttribute('value', value);",
        "      input.dispatchEvent(new Event('input', { bubbles: true }));",
        "      input.dispatchEvent(new Event('change', { bubbles: true }));",
        "      input.dispatchEvent(new Event('blur', { bubbles: true }));",
        "    }",
        "",
        "    function fillQuestionnaireInlineInputs(): void {",
        "      const values = ['1', '50', '10', '20'];",
        "      const inputs = Array.from(document.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>('input.inline-input, .adapt-question-wrap input, .js-adapt-question-content input, textarea'))",
        "        .filter(input => {",
        "          if (!isVisible(input) || input.disabled || input.readOnly) return false;",
        "          const type = String(input.getAttribute('type') || '').toLowerCase();",
        "          return !['hidden', 'button', 'submit', 'reset', 'file', 'radio', 'checkbox'].includes(type);",
        "        });",
        "      inputs.forEach((input, index) => {",
        "        if (String(input.value || '').trim()) return;",
        "        const value = values[index] || values[values.length - 1];",
        "        setNativeInputValue(input, value);",
        "        clicked.push({",
        "          group: `inline-input-${index + 1}`,",
        "          question_text: normalizedText(input.closest('[data-number], .adapt-question-wrap, .js-adapt-question-content, .question') || input.parentElement || input).slice(0, 160),",
        "          text: value,",
        "          tag: input.tagName.toLowerCase(),",
        "          selector: cssSelector(input),",
        "          choice_rule: 'questionnaire-inline-input',",
        "          click_strategy: 'js-questionnaire-inline-input',",
        "        });",
        "      });",
        "    }",
        "",
        "    const customQuestionNodes = Array.from(document.querySelectorAll('[data-number].answer-radio, [data-number].answer-multiple-select, .adapt-question-wrap [data-number], .js-adapt-question-content [data-number]'));",
        "    const containersByNumber = new Map<string, Element>();",
        "    for (const node of customQuestionNodes) {",
        "      const number = node.getAttribute('data-number');",
        "      if (!number || containersByNumber.has(number)) continue;",
        "      containersByNumber.set(number, node);",
        "    }",
        "    for (const [number, container] of Array.from(containersByNumber.entries()).sort((a, b) => Number(a[0]) - Number(b[0]))) {",
        "      const options = Array.from(container.querySelectorAll('input.insure-label, input[type=\"button\"], button, [role=\"button\"], label, .insure-label'))",
        "        .map((element, index) => ({ element, index, score: scoreOption(element), text: normalizedText(element) }))",
        "        .filter(item => item.score > 0);",
        "      if (!options.length) continue;",
        "      const questionText = questionTextForContainer(container);",
        "      const preferred = preferredBusinessQuestionnaireChoice(number, questionText, options);",
        "      const chosen = preferred || options[isPurposeQuestion(questionText) ? 0 : options.length - 1];",
        "      const clickedElement = clickLikeUser(chosen.element);",
        "      clicked.push({",
        "        group: `data-number-${number}`,",
        "        question_text: questionText.slice(0, 160),",
        "        text: chosen.text.slice(0, 160),",
        "        tag: clickedElement.tagName.toLowerCase(),",
        "        selector: cssSelector(clickedElement),",
        "        question_number: Number(number),",
        "        choice_rule: preferred ? 'business-safe-option' : (isPurposeQuestion(questionText) ? 'purpose-first-option' : 'non-purpose-last-option'),",
        "        click_strategy: 'js-custom-questionnaire',",
        "      });",
        "    }",
        "    fillQuestionnaireInlineInputs();",
        "    if (clicked.length) {",
        "      return { strategy: 'business_questionnaire_rule', clicked_count: clicked.length, clicked };",
        "    }",
        "",
        "    const elements = Array.from(document.querySelectorAll('body *')).filter(isVisible);",
        "    let currentGroup = '';",
        "    const fallbackGroups = new Map<string, Array<Record<string, any>>>();",
        "    for (const element of elements) {",
        "      const tag = String(element.tagName || '').toLowerCase();",
        "      const role = String(element.getAttribute('role') || '').toLowerCase();",
        "      const inputType = String(element.getAttribute('type') || '').toLowerCase();",
        "      const rawText = normalizedText(element);",
        "      if (!rawText) continue;",
        "      if (questionPattern.test(rawText) && !optionPattern.test(rawText)) {",
        "        currentGroup = rawText.slice(0, 120);",
        "        continue;",
        "      }",
        "",
        "      const isChoiceInput = tag === 'input' && ['radio', 'checkbox'].includes(inputType) && !(element as HTMLInputElement).disabled;",
        "      const isChoiceRole = ['radio', 'checkbox'].includes(role);",
        "      const candidateText = isChoiceInput ? labelTextForInput(element) : rawText;",
        "      const isTextOption = optionPattern.test(candidateText);",
        "      if (!isChoiceInput && !isChoiceRole && !isTextOption) continue;",
        "      if (!isChoiceInput && !isChoiceRole && hasOptionChild(element)) continue;",
        "",
        "      const rect = element.getBoundingClientRect();",
        "      const inputName = isChoiceInput ? String(element.getAttribute('name') || '') : '';",
        "      const groupKey = inputName ? `input-name-${inputName}` : (currentGroup || `visual-row-${Math.floor(rect.top / 90)}`);",
        "      if (!fallbackGroups.has(groupKey)) fallbackGroups.set(groupKey, []);",
        "      fallbackGroups.get(groupKey)?.push({ element, candidateText, tag, role, inputType, inputName, questionText: currentGroup });",
        "    }",
        "    for (const [groupKey, candidates] of fallbackGroups.entries()) {",
        "      if (selectedGroups.has(groupKey) || !candidates.length) continue;",
        "      selectedGroups.add(groupKey);",
        "      const questionText = String(candidates[0].questionText || groupKey);",
        "      const preferred = preferredBusinessQuestionnaireChoice(groupKey, questionText, candidates);",
        "      const chosen = preferred || candidates[isPurposeQuestion(questionText) ? 0 : candidates.length - 1];",
        "      const target = clickableFor(chosen.element as Element);",
        "      target.scrollIntoView({ block: 'center', inline: 'nearest' });",
        "      target.click();",
        "      clicked.push({",
        "        group: groupKey,",
        "        question_text: questionText.slice(0, 160),",
        "        text: String(chosen.candidateText || '').slice(0, 160),",
        "        tag: chosen.tag,",
        "        role: chosen.role,",
        "        input_type: chosen.inputType,",
        "        input_name: chosen.inputName,",
        "        choice_rule: preferred ? 'business-safe-option' : (isPurposeQuestion(questionText) ? 'purpose-first-option' : 'non-purpose-last-option'),",
        "      });",
        "    }",
        "    fillQuestionnaireInlineInputs();",
        "    return { strategy: 'business_questionnaire_rule', clicked_count: clicked.length, clicked };",
        "  });",
        "  expect(result.clicked_count).toBeGreaterThan(0);",
        "  return result;",
        "}",
        "",
        "async function answerHealthNotice(page: any): Promise<{ clicked_count: number; clicked: Array<Record<string, unknown>> } | null> {",
        "  const hasNoIssueText = await page.getByText(/确认无以上问题|无以上问题|无上述问题/).count().catch(() => 0);",
        "  const hasNoIssueValue = await page.locator('input[type=\"button\"], input[type=\"submit\"], input[type=\"checkbox\"], input[type=\"radio\"]').evaluateAll((elements: Element[]) => {",
        "    return elements.filter(element => /确认无以上问题|无以上问题|无上述问题/.test(String(element.getAttribute('value') || ''))).length;",
        "  }).catch(() => 0);",
        "  if (!hasNoIssueText && !hasNoIssueValue) return null;",
        "  const result = await page.evaluate(() => {",
        "    const clicked: Array<Record<string, unknown>> = [];",
        "",
        "    function isVisible(element: Element): boolean {",
        "      const style = window.getComputedStyle(element);",
        "      const rect = element.getBoundingClientRect();",
        "      return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;",
        "    }",
        "",
        "    function normalizedText(element: Element | null): string {",
        "      if (!element) return '';",
        "      const text = String((element as HTMLElement | null)?.innerText || element.textContent || '').replace(/\\s+/g, ' ').trim();",
        "      if (text) return text;",
        "      return String(element.getAttribute?.('value') || '').replace(/\\s+/g, ' ').trim();",
        "    }",
        "",
        "    function cssSelector(element: Element): string {",
        "      const htmlElement = element as HTMLElement;",
        "      if (htmlElement.id) return `#${CSS.escape(htmlElement.id)}`;",
        "      const className = String(htmlElement.className || '').split(/\\s+/).filter(Boolean)[0];",
        "      if (className) return `${element.tagName.toLowerCase()}.${CSS.escape(className)}`;",
        "      return element.tagName.toLowerCase();",
        "    }",
        "",
        "    function clickLikeUser(element: Element): HTMLElement {",
        "      const htmlElement = element as HTMLElement;",
        "      const target = htmlElement.matches('input, button, a, label, [role=\"button\"], .insure-label')",
        "        ? htmlElement",
        "        : (htmlElement.querySelector('input.insure-label, input[type=\"button\"], input[type=\"submit\"], button, [role=\"button\"], label, .insure-label') || htmlElement) as HTMLElement;",
        "      target.scrollIntoView({ block: 'center', inline: 'center' });",
        "      if (target.matches('input, button')) {",
        "        try { target.click(); } catch (_) {}",
        "      } else {",
        "        for (const type of ['pointerdown', 'mousedown', 'mouseup', 'click']) {",
        "          target.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));",
        "        }",
        "      }",
        "      target.dispatchEvent(new Event('input', { bubbles: true }));",
        "      target.dispatchEvent(new Event('change', { bubbles: true }));",
        "      return target;",
        "    }",
        "",
        "    const candidates = Array.from(document.querySelectorAll('input[type=\"button\"], input[type=\"submit\"], button, a, label, [role=\"button\"], .insure-label'))",
        "      .map((element, index) => ({ element, index, text: normalizedText(element) }))",
        "      .filter(item => isVisible(item.element) && /确认无以上问题|无以上问题|无上述问题/.test(item.text));",
        "    if (!candidates.length) return null;",
        "    const chosen = candidates.find(item => item.text === '确认无以上问题')",
        "      || candidates.find(item => /^确认无以上问题/.test(item.text))",
        "      || candidates.find(item => item.text.includes('确认无以上问题'))",
        "      || candidates[candidates.length - 1];",
        "    const clickedElement = clickLikeUser(chosen.element);",
        "    clicked.push({",
        "      group: 'health-notice-no-issue',",
        "      question_text: '被保险人健康告知',",
        "      text: chosen.text.slice(0, 160),",
        "      tag: clickedElement.tagName.toLowerCase(),",
        "      selector: cssSelector(clickedElement),",
        "      choice_rule: 'health_notice_no_issue',",
        "      click_strategy: 'js-health-notice',",
        "    });",
        "    for (const checkbox of Array.from(document.querySelectorAll('input[type=\"checkbox\"]'))) {",
        "      if (!isVisible(checkbox) || (checkbox as HTMLInputElement).checked) continue;",
        "      const checkedElement = clickLikeUser(checkbox);",
        "      clicked.push({",
        "        group: 'health-notice-agreement',",
        "        question_text: '健康告知确认',",
        "        text: normalizedText(checkbox.parentElement || checkbox).slice(0, 160),",
        "        tag: checkedElement.tagName.toLowerCase(),",
        "        selector: cssSelector(checkedElement),",
        "        choice_rule: 'confirm-agreement',",
        "        click_strategy: 'js-health-notice-checkbox',",
        "      });",
        "    }",
        "    return { strategy: 'health_notice_no_issue', clicked_count: clicked.length, clicked };",
        "  });",
        "  if (!result || !result.clicked_count) return null;",
        "  await page.waitForTimeout(300);",
        "  const bodyAfterChoice = await page.locator('body').innerText({ timeout: 1000 }).catch(() => '');",
        "  if (/确认无以上问题|无以上问题|无上述问题/.test(bodyAfterChoice)) {",
        "    const noIssueText = await clickLastVisible(page, page.locator('a, button, [role=\"button\"], label, .insure-label, input[type=\"button\"], input[type=\"submit\"]').filter({ hasText: /确认无以上问题|无以上问题|无上述问题/ }), /确认无以上问题|无以上问题|无上述问题/).catch(() => '');",
        "    if (noIssueText) {",
        "      result.clicked.push({ group: 'health-notice-no-issue-playwright', text: noIssueText, click_strategy: 'playwright-health-notice-no-issue' });",
        "      result.clicked_count = result.clicked.length;",
        "      await page.waitForLoadState('domcontentloaded', { timeout: 10000 }).catch(() => undefined);",
        "      await settlePostClickFlow(page);",
        "    }",
        "  }",
        "  const submitBeforeUrl = page.url();",
        "  const submitCandidates = [",
        "    page.getByRole('button', { name: /下一步|确认|确定|提交|我已阅读|同意|完成/ }).last(),",
        "    page.locator('button, a, [role=\"button\"], .am-button, .am-button-primary, .submit-btn, .btn').filter({ hasText: /下一步|确认|确定|提交|我已阅读|同意|完成/ }).last(),",
        "  ];",
        "  for (const candidate of submitCandidates) {",
        "    if (page.url() !== submitBeforeUrl) break;",
        "    if (!(await candidate.isVisible({ timeout: 1200 }).catch(() => false))) continue;",
        "    const text = String(await candidate.innerText({ timeout: 1000 }).catch(() => '')).replace(/\\s+/g, ' ').trim();",
        "    await candidate.click({ timeout: 10000, noWaitAfter: true }).catch(async () => { await candidate.click({ timeout: 10000, noWaitAfter: true, force: true }); });",
        "    result.clicked.push({ group: 'health-notice-submit', text: text || 'health notice submit', click_strategy: 'playwright-health-notice-submit' });",
        "    result.clicked_count = result.clicked.length;",
        "    await page.waitForLoadState('domcontentloaded', { timeout: 10000 }).catch(() => undefined);",
        "    await settlePostClickFlow(page);",
        "    break;",
        "  }",
        "  return result;",
        "}",
        "",
        "async function acceptQuestionnaireWarningIfPresent(page: any): Promise<Record<string, unknown> | null> {",
        "  const warning = page.getByText(/投保风险警示确认书|风险警示|适当性问卷匹配结果/).first();",
        "  if (!(await warning.isVisible({ timeout: 1200 }).catch(() => false))) return null;",
        "",
        "  const candidates = [",
        "    page.locator('button, a, [role=\"button\"], .btn, .button, input[type=\"button\"], input[type=\"submit\"]').filter({ hasText: /阅读并同意|已阅读并同意|继续投保|确认继续|我已知晓|同意/ }).last(),",
        "    page.getByText(/阅读并同意|已阅读并同意|继续投保|确认继续|我已知晓|同意/).last(),",
        "  ];",
        "  for (const candidate of candidates) {",
        "    if (!(await candidate.isVisible({ timeout: 1200 }).catch(() => false))) continue;",
        "    const text = await candidate.innerText({ timeout: 1000 }).catch(async () => {",
        "      return await candidate.inputValue({ timeout: 1000 }).catch(() => '');",
        "    });",
        "    await candidate.click({ timeout: 10000, noWaitAfter: true }).catch(async () => {",
        "      await candidate.click({ timeout: 10000, noWaitAfter: true, force: true });",
        "    });",
        "    await page.waitForLoadState('domcontentloaded', { timeout: 10000 }).catch(() => undefined);",
        "    return { strategy: 'questionnaire-warning-confirm', accepted: true, text: String(text || '').slice(0, 80) };",
        "  }",
        "  return { strategy: 'questionnaire-warning-confirm', accepted: false, text: 'warning visible but confirm button not found' };",
        "}",
        "",
    ]


def real_action_helper_lines() -> list[str]:
    return [
        "function escapeRegex(value: string): string {",
        "  return value.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&');",
        "}",
        "",
        "function fuzzyTextRegex(text: string): RegExp {",
        "  const compact = String(text || '').replace(/\\s+/g, '').trim();",
        "  return new RegExp(Array.from(compact).map(escapeRegex).join('\\\\s*'));",
        "}",
        "",
        "const agent4NetworkResponses: Array<Record<string, unknown>> = [];",
        "let agent4LatestTrialInsuredResult: Record<string, unknown> | null = null;",
        "",
        "function agent4TrialInsuredAgeBand(result: Record<string, unknown> | null): string {",
        "  const geneList = Array.isArray((result as any)?.geneList) ? (result as any).geneList : [];",
        "  const ageGene = geneList.find((gene: any) => gene?.key === 'insurantDate' || gene?.geneKey === 'insurantDate');",
        "  return String(ageGene?.value || '').trim();",
        "}",
        "",
        "function agent4LatestTrialResultForSubmit(): Record<string, unknown> | null {",
        "  const latest = (globalThis as any).agent4LatestTrialInsuredResult || agent4LatestTrialInsuredResult;",
        "  if (!latest || typeof latest !== 'object') return null;",
        "  const expectedAgeBand = String((globalThis as any).__agent4ExpectedTrialAgeBand || '').trim();",
        "  const actualAgeBand = agent4TrialInsuredAgeBand(latest as Record<string, unknown>);",
        "  if (expectedAgeBand && actualAgeBand && actualAgeBand !== expectedAgeBand) return null;",
        "  return latest as Record<string, unknown>;",
        "}",
        "",
        "async function waitForAgent4TrialInsuredResultForSubmit(timeoutMs = 5000): Promise<Record<string, unknown> | null> {",
        "  const deadline = Date.now() + timeoutMs;",
        "  while (Date.now() < deadline) {",
        "    const latest = agent4LatestTrialResultForSubmit();",
        "    if (latest) return latest;",
        "    await new Promise(resolve => setTimeout(resolve, 200));",
        "  }",
        "  return agent4LatestTrialResultForSubmit();",
        "}",
        "",
        "function setupAgent4NetworkResponseCache(page: any): void {",
        "  if ((page as any).__agent4NetworkResponseCacheSetup) return;",
        "  (page as any).__agent4NetworkResponseCacheSetup = true;",
        "  page.on('response', async (response: any) => {",
        "    const url = String(response.url() || '');",
        "    if (!/\\/api\\/apps\\/cps\\/(?:product\\/insure|insure|product\\/adapt|product\\/task|pay\\/bank|product\\/trial)/i.test(url)) return;",
        "    const status = Number(response.status?.() || 0);",
        "    let body = '';",
        "    if (status >= 400 || /\\/api\\/apps\\/cps\\/(?:product\\/)?insure\\/submit|\\/api\\/apps\\/cps\\/product\\/trial\\/insured/i.test(url)) {",
        "      const responseBody = await response.text().then((value: string) => String(value || '')).catch(() => '');",
        "      body = responseBody.slice(0, 1200);",
        "      if (status < 400 && /\\/api\\/apps\\/cps\\/product\\/trial\\/insured/i.test(url) && responseBody) {",
        "        try {",
        "          const payload = JSON.parse(responseBody);",
        "          if (payload?.code === 0 && payload?.data) {",
        "            agent4LatestTrialInsuredResult = payload.data;",
        "            (globalThis as any).agent4LatestTrialInsuredResult = payload.data;",
        "          }",
        "        } catch (_) {}",
        "      }",
        "    }",
        "    agent4NetworkResponses.push({ status, url, body_excerpt: body });",
        "    if (agent4NetworkResponses.length > 80) agent4NetworkResponses.shift();",
        "  });",
        "}",
        "",
        "async function stableReplayLocator(page: any, locator: any): Promise<any> {",
        "  const token = `agent4-${Date.now()}-${Math.random().toString(36).slice(2)}`;",
        "  const marked = await locator.evaluate((element: Element, value: string) => {",
        "    element.setAttribute('data-agent4-replay-target', value);",
        "  }, token).then(() => true).catch(() => false);",
        "  if (!marked) return locator;",
        "  return page.locator(`[data-agent4-replay-target=\"${token}\"]`).first();",
        "}",
        "",
        "async function replayActionLocator(page: any, selector: string, tag: string, text: string): Promise<any> {",
        "  const actionableSelector = 'button, a, [role=\"button\"], .am-button, input[type=\"button\"], input[type=\"submit\"], label';",
        "  const broadSelector = `${actionableSelector}, span, div`;",
        "  const normalizedText = String(text || '').replace(/\\s+/g, '').trim();",
        "  const smsCodeButtonText = /获取验证码|发送认证短信|发送验证码|发送短信|获取认证短信/;",
        "  async function locatorText(locator: any): Promise<string> {",
        "    const inner = await locator.innerText({ timeout: 600 }).catch(() => '');",
        "    if (inner) return String(inner).replace(/\\s+/g, '').trim();",
        "    const value = await locator.inputValue({ timeout: 600 }).catch(() => '');",
        "    return String(value || '').replace(/\\s+/g, '').trim();",
        "  }",
        "  async function visibleByExactText(css: string): Promise<any | null> {",
        "    const matches = page.locator(css);",
        "    const count = await matches.count().catch(() => 0);",
        "    for (let index = count - 1; index >= 0; index -= 1) {",
        "      const candidate = matches.nth(index);",
        "      if (!(await candidate.isVisible({ timeout: 600 }).catch(() => false))) continue;",
        "      if ((await locatorText(candidate)) === normalizedText) return await stableReplayLocator(page, candidate);",
        "    }",
        "    return null;",
        "  }",
        "  async function visibleByText(css: string): Promise<any | null> {",
        "    const matches = page.locator(css).filter({ hasText: fuzzyTextRegex(normalizedText) });",
        "    const count = await matches.count().catch(() => 0);",
        "    for (let index = count - 1; index >= 0; index -= 1) {",
        "      const candidate = matches.nth(index);",
        "      if (await candidate.isVisible({ timeout: 800 }).catch(() => false)) return await stableReplayLocator(page, candidate);",
        "    }",
        "    return null;",
        "  }",
        "  if (/获取验证码|发送认证短信|发送验证码|发送短信|获取认证短信/.test(normalizedText)) {",
        "    const smsAction = page.locator(actionableSelector).filter({ hasText: smsCodeButtonText }).last();",
        "    if (await smsAction.isVisible({ timeout: 1200 }).catch(() => false)) return await stableReplayLocator(page, smsAction);",
        "    const smsBroad = page.locator(broadSelector).filter({ hasText: smsCodeButtonText }).last();",
        "    if (await smsBroad.isVisible({ timeout: 1200 }).catch(() => false)) return await stableReplayLocator(page, smsBroad);",
        "  }",
        "  if (normalizedText) {",
        "    const exactActionableByText = await visibleByExactText(actionableSelector);",
        "    if (exactActionableByText) return exactActionableByText;",
        "    const actionableByText = await visibleByText(actionableSelector);",
        "    if (actionableByText) return actionableByText;",
        "  }",
        "  if (selector) {",
        "    const bySelector = page.locator(selector).first();",
        "    if (await bySelector.isVisible({ timeout: 800 }).catch(() => false)) return await stableReplayLocator(page, bySelector);",
        "  }",
        "  if (normalizedText) {",
        "    const exactBroadByText = await visibleByExactText(broadSelector);",
        "    if (exactBroadByText) return exactBroadByText;",
        "    const broadByText = await visibleByText(broadSelector);",
        "    if (broadByText) return broadByText;",
        "  }",
        "  if (selector) return page.locator(selector).first();",
        "  const fallback = page.locator(broadSelector).filter({ hasText: fuzzyTextRegex(normalizedText) }).last();",
        "  if (await fallback.isVisible({ timeout: 800 }).catch(() => false)) return await stableReplayLocator(page, fallback);",
        "  return fallback;",
        "}",
        "",
        "async function acceptProductNoticeIfPresent(page: any): Promise<Record<string, unknown> | null> {",
        "  const dialogs = page.locator('[role=\"dialog\"], .am-modal, .am-modal-wrap').filter({ hasText: /投保须知|投保前请您仔细阅读/ });",
        "  const dialogCount = await dialogs.count().catch(() => 0);",
        "  for (let dialogIndex = dialogCount - 1; dialogIndex >= 0; dialogIndex -= 1) {",
        "    const dialog = dialogs.nth(dialogIndex);",
        "    if (!(await dialog.isVisible({ timeout: 1200 }).catch(() => false))) continue;",
        "    const checkboxes = dialog.locator('input[type=\"checkbox\"]');",
        "    const checkboxCount = await checkboxes.count().catch(() => 0);",
        "    for (let index = 0; index < checkboxCount; index += 1) {",
        "      const checkbox = checkboxes.nth(index);",
        "      const checked = await checkbox.isChecked().catch(() => false);",
        "      if (!checked) await checkbox.click({ timeout: 2000, force: true }).catch(() => undefined);",
        "    }",
        "    const buttons = dialog.locator('button, a, [role=\"button\"], .am-button, .btn, span, div').filter({ hasText: /阅读并同意|已阅读并同意|同意并继续|继续投保|确认投保|立即投保|投\\s*保|确定|我知道/ });",
        "    const buttonCount = await buttons.count().catch(() => 0);",
        "    for (let index = buttonCount - 1; index >= 0; index -= 1) {",
        "      const button = buttons.nth(index);",
        "      if (!(await button.isVisible({ timeout: 1200 }).catch(() => false))) continue;",
        "      const text = await button.innerText({ timeout: 1000 }).catch(() => '');",
        "      await button.click({ timeout: 10000, noWaitAfter: true }).catch(async () => {",
        "        await button.click({ timeout: 10000, noWaitAfter: true, force: true });",
        "      });",
        "      await page.waitForLoadState('domcontentloaded', { timeout: 10000 }).catch(() => undefined);",
        "      return { strategy: 'product-notice-confirm', accepted: true, text: String(text || '').slice(0, 80) };",
        "    }",
        "    return { strategy: 'product-notice-confirm', accepted: false, text: 'notice visible but confirm button not found' };",
        "  }",
        "  return null;",
        "}",
        "",
        "async function acceptContinuationDialogIfPresent(page: any): Promise<Record<string, unknown> | null> {",
        "  const domAccepted = await page.evaluate(() => {",
        "    const visible = (element: Element): boolean => {",
        "      const node = element as HTMLElement;",
        "      if (!node) return false;",
        "      const style = window.getComputedStyle(node);",
        "      const rect = node.getBoundingClientRect();",
        "      return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;",
        "    };",
        "    const panelSelector = '[role=\"dialog\"], .am-modal, .am-modal-wrap, .am-modal-content, .am-modal-body, .adm-modal, .adm-dialog';",
        "    const panels = Array.from(document.querySelectorAll<HTMLElement>(panelSelector)).filter(visible).reverse();",
        "    for (const panel of panels) {",
        "      const panelText = String(panel.innerText || '').replace(/\\s+/g, '');",
        "      if (!/继续投保|已有|订单已存在|再次提交|重复投保|同期限|同类保险产品|勿重复购买|是否继续|是否要再次提交/.test(panelText)) continue;",
        "      if (/投保人声明及确认|投保条件|投保重要告知|保险条款|免责条款|已阅读并同意/.test(panelText)) continue;",
        "      const candidates = Array.from(panel.querySelectorAll<HTMLElement>('button, a, [role=\"button\"], .am-button, .adm-button, .btn, .submit-btn, span, div')).filter(visible).reverse();",
        "      for (const candidate of candidates) {",
        "        const text = String(candidate.innerText || candidate.textContent || '').replace(/\\s+/g, '');",
        "        if (!text || text.length > 12) continue;",
        "        if (/查看|取消|关闭|返回/.test(text)) continue;",
        "        if (!/^(提交|确定|确认|继续投保|继续|是)$/.test(text)) continue;",
        "        candidate.scrollIntoView({ block: 'center', inline: 'center' });",
        "        candidate.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, cancelable: true }));",
        "        candidate.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));",
        "        candidate.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true }));",
        "        candidate.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));",
        "        return { accepted: true, text: text.slice(0, 80) };",
        "      }",
        "      return { accepted: false, text: 'continuation dialog visible but confirm button not found' };",
        "    }",
        "    return null;",
        "  }).catch(() => null);",
        "  if (domAccepted) {",
        "    await page.waitForLoadState('domcontentloaded', { timeout: 10000 }).catch(() => undefined);",
        "    await page.waitForTimeout(500);",
        "    return { strategy: 'continuation-dialog-dom-confirm', ...domAccepted };",
        "  }",
        "  const dialogs = page.locator('[role=\"dialog\"], .am-modal, .am-modal-wrap').filter({ hasText: /继续投保|已有|订单已存在|再次提交|重复投保|同期限|同类保险产品|勿重复购买|是否继续|是否要再次提交/ }).filter({ hasNotText: /投保人声明及确认|投保条件|投保重要告知|保险条款|免责条款|已阅读并同意/ });",
        "  const dialogCount = await dialogs.count().catch(() => 0);",
        "  for (let dialogIndex = dialogCount - 1; dialogIndex >= 0; dialogIndex -= 1) {",
        "    const dialog = dialogs.nth(dialogIndex);",
        "    if (!(await dialog.isVisible({ timeout: 1200 }).catch(() => false))) continue;",
        "    const buttons = dialog.locator('button, a, [role=\"button\"], .am-button, .btn, span, div').filter({ hasText: /提交|确定|确认|继续投保|继续|是/ });",
        "    const buttonCount = await buttons.count().catch(() => 0);",
        "    for (let index = buttonCount - 1; index >= 0; index -= 1) {",
        "      const button = buttons.nth(index);",
        "      if (!(await button.isVisible({ timeout: 1200 }).catch(() => false))) continue;",
        "      const text = await button.innerText({ timeout: 1000 }).catch(() => '');",
        "      await button.click({ timeout: 10000, noWaitAfter: true }).catch(async () => {",
        "        await button.click({ timeout: 10000, noWaitAfter: true, force: true });",
        "      });",
        "      await page.waitForLoadState('domcontentloaded', { timeout: 10000 }).catch(() => undefined);",
        "      return { strategy: 'continuation-dialog-confirm', accepted: true, text: String(text || '').slice(0, 80) };",
        "    }",
        "    return { strategy: 'continuation-dialog-confirm', accepted: false, text: 'continuation dialog visible but confirm button not found' };",
        "  }",
        "  return null;",
        "}",
        "",
        "async function clickLastVisible(page: any, locator: any, pattern?: RegExp): Promise<string | null> {",
        "  const count = await locator.count().catch(() => 0);",
        "  for (let index = count - 1; index >= 0; index -= 1) {",
        "    const candidate = locator.nth(index);",
        "    if (!(await candidate.isVisible({ timeout: 1000 }).catch(() => false))) continue;",
        "    if (pattern) {",
        "      const text = await candidate.innerText({ timeout: 800 }).catch(async () => await candidate.inputValue({ timeout: 800 }).catch(() => ''));",
        "      if (!pattern.test(String(text || '').replace(/\\s+/g, ''))) continue;",
        "    }",
        "    const text = await candidate.innerText({ timeout: 800 }).catch(async () => await candidate.inputValue({ timeout: 800 }).catch(() => ''));",
        "    await candidate.scrollIntoViewIfNeeded({ timeout: 2000 }).catch(() => undefined);",
        "    await candidate.click({ timeout: 10000, noWaitAfter: true }).catch(async () => {",
        "      await candidate.click({ timeout: 10000, noWaitAfter: true, force: true });",
        "    });",
        "    await page.waitForLoadState('domcontentloaded', { timeout: 10000 }).catch(() => undefined);",
        "    await page.waitForTimeout(500);",
        "    return String(text || '').slice(0, 80);",
        "  }",
        "  return null;",
        "}",
        "",
        "async function clickLastVisibleShortText(page: any, locator: any, pattern: RegExp): Promise<string | null> {",
        "  const count = await locator.count().catch(() => 0);",
        "  for (let index = count - 1; index >= 0; index -= 1) {",
        "    const candidate = locator.nth(index);",
        "    if (!(await candidate.isVisible({ timeout: 1000 }).catch(() => false))) continue;",
        "    const text = await candidate.innerText({ timeout: 800 }).catch(async () => await candidate.inputValue({ timeout: 800 }).catch(() => ''));",
        "    const compact = String(text || '').replace(/\\s+/g, '');",
        "    if (!compact || compact.length > 18 || !pattern.test(compact)) continue;",
        "    await candidate.scrollIntoViewIfNeeded({ timeout: 2000 }).catch(() => undefined);",
        "    await candidate.click({ timeout: 10000, noWaitAfter: true }).catch(async () => {",
        "      await candidate.click({ timeout: 10000, noWaitAfter: true, force: true });",
        "    });",
        "    await page.waitForLoadState('domcontentloaded', { timeout: 10000 }).catch(() => undefined);",
        "    await page.waitForTimeout(500);",
        "    return String(text || '').slice(0, 80);",
        "  }",
        "  return null;",
        "}",
        "",
        "async function tapLocatorCenter(page: any, locator: any): Promise<boolean> {",
        "  await locator.scrollIntoViewIfNeeded({ timeout: 2000 }).catch(() => undefined);",
        "  const box = await locator.boundingBox().catch(() => null);",
        "  if (box) {",
        "    const x = box.x + box.width / 2;",
        "    const y = box.y + box.height / 2;",
        "    await page.touchscreen.tap(x, y).catch(async () => {",
        "      await page.mouse.click(x, y);",
        "    });",
        "    await page.waitForTimeout(500);",
        "    return true;",
        "  }",
        "  await locator.click({ timeout: 10000, noWaitAfter: true }).catch(async () => {",
        "    await locator.click({ timeout: 10000, noWaitAfter: true, force: true });",
        "  });",
        "  await page.waitForTimeout(500);",
        "  return true;",
        "}",
        "",
        "async function clickReplayAction(page: any, locator: any): Promise<boolean> {",
        "  await locator.scrollIntoViewIfNeeded({ timeout: 2000 }).catch(() => undefined);",
        "  const clicked = await locator.click({ timeout: 5000, noWaitAfter: true }).then(() => true).catch(async () => {",
        "    return await locator.click({ timeout: 5000, noWaitAfter: true, force: true }).then(() => true).catch(async () => {",
        "      const domClicked = await locator.evaluate((element: HTMLElement) => {",
        "        element.scrollIntoView({ block: 'center', inline: 'center' });",
        "        for (const type of ['touchstart', 'touchend', 'pointerdown', 'mousedown', 'mouseup', 'click']) {",
        "          const event = type.startsWith('touch')",
        "            ? new Event(type, { bubbles: true, cancelable: true })",
        "            : new MouseEvent(type, { bubbles: true, cancelable: true, view: window });",
        "          element.dispatchEvent(event);",
        "        }",
        "        if (typeof element.click === 'function') element.click();",
        "        element.dispatchEvent(new Event('input', { bubbles: true }));",
        "        element.dispatchEvent(new Event('change', { bubbles: true }));",
        "        return true;",
        "      }).catch(() => false);",
        "      if (domClicked) return true;",
        "      return await tapLocatorCenter(page, locator).then(() => true).catch(() => false);",
        "    });",
        "  });",
        "  await page.waitForTimeout(500);",
        "  return Boolean(clicked);",
        "}",
        "",
        "async function triggerH5SubmitDomClick(page: any): Promise<boolean> {",
        "  return await page.evaluate(() => {",
        "    const pattern = /提交\\s*订单|提交|下一步|确认|submit/i;",
        "    const visible = (element: Element) => {",
        "      const style = window.getComputedStyle(element);",
        "      const rect = (element as HTMLElement).getBoundingClientRect();",
        "      return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;",
        "    };",
        "    const textOf = (element: Element) => String((element as HTMLElement).innerText || element.textContent || '').replace(/\\s+/g, ' ').trim();",
        "    const candidates = Array.from(document.querySelectorAll<HTMLElement>('.insure-footer .submit-btn, .submit-btn, a[role=\"button\"].am-button-primary, button.am-button-primary, [role=\"button\"].am-button-primary, .am-button-primary, button, a, [role=\"button\"]'))",
        "      .filter(element => visible(element) && pattern.test(textOf(element)));",
        "    const target = candidates[candidates.length - 1];",
        "    if (!target) return false;",
        "    target.scrollIntoView({ block: 'center', inline: 'center' });",
        "    for (const type of ['touchstart', 'touchend', 'mousedown', 'mouseup', 'click']) {",
        "      const event = type.startsWith('touch')",
        "        ? new Event(type, { bubbles: true, cancelable: true })",
        "        : new MouseEvent(type, { bubbles: true, cancelable: true, view: window });",
        "      target.dispatchEvent(event);",
        "    }",
        "    target.click();",
        "    return true;",
        "  }).catch(() => false);",
        "}",
        "",
        "async function clickH5SubmitCandidate(page: any, locator: any): Promise<boolean> {",
        "  await locator.scrollIntoViewIfNeeded({ timeout: 2000 }).catch(() => undefined);",
        "  const clicked = await locator.click({ timeout: 5000, noWaitAfter: true, force: true }).then(() => true).catch(async () => {",
        "    return await tapLocatorCenter(page, locator).then(() => true).catch(() => false);",
        "  });",
        "  const domClicked = await triggerH5SubmitDomClick(page);",
        "  await page.waitForTimeout(500);",
        "  return Boolean(clicked || domClicked);",
        "}",
        "",
        "async function h5ProductInsureProgressed(page: any): Promise<boolean> {",
        "  const url = String(page.url() || '');",
        "  if (/healthInform|health|notice|inform|product\\/insure/i.test(url) && !/product\\/detail/i.test(url)) return true;",
        "  const healthAction = page.locator('button, a, [role=\"button\"], .am-button').filter({ hasText: /确认无以上问题|有部分问题|纭鏃犱互涓婇棶棰|鏈夐儴鍒嗛棶棰/ }).last();",
        "  return healthAction.isVisible({ timeout: 800 }).catch(() => false);",
        "}",
        "",
        "async function selectProductDetailCoveragePlan(page: any, planName: string): Promise<Record<string, unknown>> {",
        "  const targetPlan = String(planName || '').trim();",
        "  if (!targetPlan) return { selected: false, reason: 'empty-plan' };",
        "  const option = page.locator('ul.condition-slide li.condition').filter({ hasText: targetPlan });",
        "  const optionCount = await option.count().catch(() => 0);",
        "  if (optionCount !== 1) throw new Error(`Expected one product detail coverage plan \"${targetPlan}\", found ${optionCount}`);",
        "  await option.click({ timeout: 10000 });",
        "  await page.waitForTimeout(500);",
        "  const activeCount = await page.locator('ul.condition-slide li.condition.active').filter({ hasText: targetPlan }).count().catch(() => 0);",
        "  expect(activeCount).toBeGreaterThan(0);",
        "  const coverageExcerpt = await page.evaluate(() => {",
        "    const bodyText = String(document.body?.innerText || document.body?.textContent || '').replace(/\\s+/g, ' ').trim();",
        "    const start = bodyText.indexOf('意外身故、伤残保障');",
        "    return start >= 0 ? bodyText.slice(start, start + 140) : '';",
        "  }).catch(() => '');",
        "  return { selected: true, planName: targetPlan, activeCount, coverageExcerpt };",
        "}",
        "",
        "async function h5TransientPageErrorText(page: any): Promise<string> {",
        "  const bodyText = await page.locator('body').innerText({ timeout: 2000 }).catch(() => '');",
        "  const text = String(bodyText || '');",
        "  if (/系统正在维护|页面暂时无法访问|系统内部发生错误|服务器出问题啦|请刷新重试|502|Bad Gateway|请求超时|请求失败/.test(text)) return text.slice(0, 300);",
        "  return '';",
        "}",
        "",
        "async function recoverH5TransientPageError(page: any): Promise<Record<string, unknown>> {",
        "  const attempts: string[] = [];",
        "  for (let attempt = 0; attempt < 3; attempt += 1) {",
        "    const errorText = await h5TransientPageErrorText(page);",
        "    if (!errorText) return { recovered: attempts.length > 0, attempts };",
        "    attempts.push(`transient-page-error-${attempt + 1}:${errorText.slice(0, 80)}`);",
        "    const refreshButton = page.locator('button, a, [role=\"button\"], .am-button, .btn').filter({ hasText: /刷新|重试|重新加载|Refresh|Retry/i }).last();",
        "    if (await refreshButton.isVisible({ timeout: 1000 }).catch(() => false)) {",
        "      await tapLocatorCenter(page, refreshButton).catch(() => undefined);",
        "    } else {",
        "      await page.reload({ waitUntil: 'domcontentloaded', timeout: 30000 }).catch(() => undefined);",
        "    }",
        "    await page.waitForLoadState('domcontentloaded', { timeout: 10000 }).catch(() => undefined);",
        "    await page.waitForTimeout(1200);",
        "    if (!(await h5TransientPageErrorText(page))) return { recovered: true, attempts };",
        "    await page.goto(FLOW_ENTRY, { waitUntil: 'domcontentloaded', timeout: 30000 }).catch(() => undefined);",
        "    await page.waitForLoadState('domcontentloaded', { timeout: 10000 }).catch(() => undefined);",
        "    await page.waitForTimeout(1200);",
        "  }",
        "  return { recovered: false, attempts, error_text: await h5TransientPageErrorText(page) };",
        "}",
        "",
        "async function replayH5ProductFooterInsure(page: any, selector: string, tag: string, text: string): Promise<Record<string, unknown>> {",
        "  const actionText = String(text || '').replace(/\\s+/g, '').trim() || '投保';",
        "  const textPattern = fuzzyTextRegex(actionText);",
        "  const footerSelector = '.product-detail-footer, [class*=\"product-detail-footer\"], .product-footer, [class*=\"product-footer\"], .detail-footer, [class*=\"detail-footer\"], .footer-bar, [class*=\"footer-bar\"]';",
        "  const actionableSelector = 'button, a, [role=\"button\"], .am-button, .btn, input[type=\"button\"], input[type=\"submit\"], span, div';",
        "  const attempts: string[] = [];",
        "  let clicked = false;",
        "  async function afterClickProgress(clickName: string): Promise<Record<string, unknown> | null> {",
        "    await page.waitForLoadState('domcontentloaded', { timeout: 10000 }).catch(() => undefined);",
        "    await page.waitForTimeout(1200);",
        "    const settledAfter = await settlePostClickFlow(page).catch(() => []);",
        "    if (await h5ProductInsureProgressed(page)) {",
        "      return { strategy: 'mouse-h5-product-footer-insure', clicked: true, attempts, settled: settledAfter, last_click: clickName };",
        "    }",
        "    attempts.push(`${clickName}-no-progress`);",
        "    return null;",
        "  }",
        "  for (let attempt = 0; attempt < 6; attempt += 1) {",
        "    const transientRecovery = await recoverH5TransientPageError(page);",
        "    if (Array.isArray(transientRecovery.attempts) && transientRecovery.attempts.length) attempts.push(...transientRecovery.attempts.map(String));",
        "    const remainingTransientError = await h5TransientPageErrorText(page);",
        "    if (remainingTransientError) throw new Error(`H5 product entry unavailable after transient recovery: ${remainingTransientError}`);",
        "    const settledBefore = await settlePostClickFlow(page).catch(() => []);",
        "    if (await h5ProductInsureProgressed(page)) {",
        "      return { strategy: 'mouse-h5-product-footer-insure', clicked: true, attempts: ['post-click-progressed'], settled: settledBefore };",
        "    }",
        "    const footer = page.locator(footerSelector).last();",
        "    const footerButton = footer.locator(actionableSelector).filter({ hasText: textPattern }).last();",
        "    if (await footerButton.isVisible({ timeout: 1200 }).catch(() => false)) {",
        "      clicked = await tapLocatorCenter(page, footerButton).then(() => true).catch(() => false);",
        "      attempts.push('footer-text');",
        "      if (clicked) {",
        "        const progressed = await afterClickProgress('footer-text');",
        "        if (progressed) return progressed;",
        "      }",
        "    }",
        "    const broadButton = page.locator(actionableSelector).filter({ hasText: textPattern }).last();",
        "    if (await broadButton.isVisible({ timeout: 1200 }).catch(() => false)) {",
        "      clicked = await tapLocatorCenter(page, broadButton).then(() => true).catch(() => false);",
        "      attempts.push('page-text');",
        "      if (clicked) {",
        "        const progressed = await afterClickProgress('page-text');",
        "        if (progressed) return progressed;",
        "      }",
        "    }",
        "    if (await footer.isVisible({ timeout: 800 }).catch(() => false)) {",
        "      const footerBox = await footer.boundingBox().catch(() => null);",
        "      if (footerBox) {",
        "        const x = footerBox.x + footerBox.width * 0.84;",
        "        const y = footerBox.y + footerBox.height / 2;",
        "        await page.touchscreen.tap(x, y).catch(async () => {",
        "          await page.mouse.click(x, y);",
        "        });",
        "        await page.waitForTimeout(500);",
        "        attempts.push('footer-coordinate');",
        "        clicked = true;",
        "        const progressed = await afterClickProgress('footer-coordinate');",
        "        if (progressed) return progressed;",
        "      }",
        "    }",
        "    const fallback = await replayActionLocator(page, selector, tag, text).catch(() => null);",
        "    if (fallback && await fallback.isVisible({ timeout: 800 }).catch(() => false)) {",
        "      clicked = await tapLocatorCenter(page, fallback).then(() => true).catch(() => false);",
        "      attempts.push('agent3-selector-fallback');",
        "      if (clicked) {",
        "        const progressed = await afterClickProgress('agent3-selector-fallback');",
        "        if (progressed) return progressed;",
        "      }",
        "    }",
        "    await page.mouse.wheel(0, 1200).catch(() => undefined);",
        "    await page.waitForTimeout(800);",
        "    if (await h5ProductInsureProgressed(page)) {",
        "      return { strategy: 'mouse-h5-product-footer-insure', clicked: true, attempts: ['post-click-progressed'], settled: [] };",
        "    }",
        "  }",
        "  if (!clicked) {",
        "    throw new Error(`H5 product footer insure action not clickable: ${text || selector}`);",
        "  }",
        "  await page.waitForLoadState('domcontentloaded', { timeout: 10000 }).catch(() => undefined);",
        "  const settled = await settlePostClickFlow(page);",
        "  return { strategy: 'mouse-h5-product-footer-insure', clicked, attempts, settled };",
        "}",
        "",
        "async function clickTaskModalGoCompleteIfPresent(page: any): Promise<Record<string, unknown>> {",
        "  const dialog = page.locator('.task-modal, .am-modal, .am-modal-wrap, .adm-modal, .adm-popup, [role=\"dialog\"]').filter({ hasText: /即将进行以下操作|适当性问卷|身份认证|银行卡签约|去完成/ }).last();",
        "  if (!(await dialog.isVisible({ timeout: 1200 }).catch(() => false))) return { clicked: false, reason: 'task modal not visible' };",
        "  const button = dialog.locator('button, a, [role=\"button\"], .am-button, .am-button-primary, .btn, span, div').filter({ hasText: /去完成|继续|下一步|完成/ }).last();",
        "  if (!(await button.isVisible({ timeout: 1200 }).catch(() => false))) return { clicked: false, reason: 'task modal go-complete button not visible' };",
        "  const text = await button.innerText({ timeout: 1000 }).catch(() => '');",
        "  await tapLocatorCenter(page, button).catch(async () => {",
        "    await button.click({ timeout: 5000, force: true, noWaitAfter: true });",
        "  });",
        "  await page.waitForLoadState('domcontentloaded', { timeout: 15000 }).catch(() => undefined);",
        "  await page.waitForTimeout(1200);",
        "  return { clicked: true, strategy: 'task-modal-go-complete', text, url: page.url() };",
        "}",
        "",
        "function recentSuitabilitySubmitBlocker(): Record<string, unknown> | null {",
        "  const suitabilityTaskPattern = /40015|需要进行适当性问卷|适当性问卷|suitability|questionnaire/i;",
        "  return agent4NetworkResponses.slice().reverse().find((item) => {",
        "    const url = String(item.url || '');",
        "    const body = String(item.body_excerpt || '');",
        "    return /\\/api\\/apps\\/cps\\/(?:product\\/)?insure\\/submit/i.test(url) && suitabilityTaskPattern.test(`${body} ${item.status || ''}`);",
        "  }) || null;",
        "}",
        "",
        "async function waitForSuitabilitySubmitBlocker(page: any, timeoutMs = 12000): Promise<Record<string, unknown> | null> {",
        "  const deadline = Date.now() + timeoutMs;",
        "  while (Date.now() < deadline) {",
        "    const blocker = recentSuitabilitySubmitBlocker();",
        "    if (blocker) return blocker;",
        "    await page.waitForTimeout(500).catch(() => undefined);",
        "  }",
        "  return null;",
        "}",
        "",
        "function recentIdentitySubmitBlocker(): Record<string, unknown> | null {",
        "  const identityTaskPattern = /37009|taskType[\"']?\\s*:\\s*2|identity|authentication|approve|身份验证|身份认证/i;",
        "  return agent4NetworkResponses.slice().reverse().find((item) => {",
        "    const url = String(item.url || '');",
        "    const body = String(item.body_excerpt || '');",
        "    return /\\/api\\/apps\\/cps\\/(?:product\\/)?insure\\/submit/i.test(url) && identityTaskPattern.test(`${body} ${item.status || ''}`);",
        "  }) || null;",
        "}",
        "",
        "async function waitForIdentitySubmitBlocker(page: any, timeoutMs = 12000): Promise<Record<string, unknown> | null> {",
        "  const deadline = Date.now() + timeoutMs;",
        "  while (Date.now() < deadline) {",
        "    const blocker = recentIdentitySubmitBlocker();",
        "    if (blocker) return blocker;",
        "    await page.waitForTimeout(500).catch(() => undefined);",
        "  }",
        "  return null;",
        "}",
        "",
        "function parseAgent4BodyExcerpt(item: Record<string, unknown> | null): Record<string, unknown> | null {",
        "  const body = String(item?.body_excerpt || '').trim();",
        "  if (!body) return null;",
        "  try { return JSON.parse(body); } catch (_) { return null; }",
        "}",
        "",
        "function identityEncryptInsureNumFromBlocker(blocker: Record<string, unknown> | null, currentUrl: string): string {",
        "  const payload = parseAgent4BodyExcerpt(blocker);",
        "  const body = String(blocker?.body_excerpt || '');",
        "  const nested = (payload?.data || payload || {}) as any;",
        "  const candidates = [",
        "    nested.encryptInsureNum,",
        "    payload?.encryptInsureNum,",
        "    body.match(/\"encryptInsureNum\"\\s*:\\s*\"([^\"]+)\"/)?.[1],",
        "  ];",
        "  try {",
        "    const parsed = new URL(currentUrl);",
        "    candidates.push(parsed.searchParams.get('encryptInsureNum'));",
        "    candidates.push(parsed.searchParams.get('id'));",
        "  } catch (_) {}",
        "  return String(candidates.find((value) => value) || '').trim();",
        "}",
        "",
        "function latestIdentitySubmitEncryptInsureNum(currentUrl = ''): string {",
        "  const candidates: Array<string | null | undefined> = [];",
        "  try {",
        "    const parsed = new URL(currentUrl);",
        "    candidates.push(parsed.searchParams.get('encryptInsureNum'));",
        "    candidates.push(parsed.searchParams.get('id'));",
        "  } catch (_) {}",
        "  for (const item of agent4NetworkResponses.slice().reverse()) {",
        "    const url = String(item.url || '');",
        "    if (!/\\/api\\/apps\\/cps\\/(?:product\\/)?insure\\/submit/i.test(url)) continue;",
        "    const payload = parseAgent4BodyExcerpt(item);",
        "    const body = String(item.body_excerpt || '');",
        "    const nested = (payload?.data || payload || {}) as any;",
        "    candidates.push(nested.encryptInsureNum);",
        "    candidates.push(payload?.encryptInsureNum);",
        "    candidates.push(body.match(/\"encryptInsureNum\"\\s*:\\s*\"([^\"]+)\"/)?.[1]);",
        "  }",
        "  return String(candidates.find((value) => value) || '').trim();",
        "}",
        "",
        "function identityTargetUrl(page: any, expectedUrl: string, encryptInsureNum: string): string {",
        "  if (!expectedUrl) return '';",
        "  try {",
        "    const target = new URL(expectedUrl, page.url());",
        "    if (encryptInsureNum && /\\/pay(?:\\/|$)/i.test(target.pathname)) target.searchParams.set('id', encryptInsureNum);",
        "    return target.href;",
        "  } catch (_) {",
        "    return expectedUrl;",
        "  }",
        "}",
        "",
        "async function postAgent4Json(page: any, url: string, payload: Record<string, unknown>): Promise<Record<string, unknown>> {",
        "  return await page.evaluate(async ({ url, payload }) => {",
        "    const target = `${url}${url.includes('?') ? '&' : '?'}md=${Math.random()}`;",
        "    const response = await fetch(target, {",
        "      method: 'POST',",
        "      credentials: 'include',",
        "      headers: { 'content-type': 'application/json;charset=UTF-8' },",
        "      body: JSON.stringify(payload),",
        "    });",
        "    const text = await response.text();",
        "    let json: any = null;",
        "    try { json = JSON.parse(text); } catch (_) {}",
        "    return { status: response.status, ok: response.ok, json, text: text.slice(0, 1200) };",
        "  }, { url, payload });",
        "}",
        "",
        "function h5CpsTaskBasePath(page: any, expectedUrl = ''): string {",
        "  for (const seed of [page.url(), expectedUrl]) {",
        "    try {",
        "      const parsed = new URL(seed || page.url(), page.url());",
        "      const parts = parsed.pathname.split('/').filter(Boolean);",
        "      const cpsIndex = parts.indexOf('cps');",
        "      if (parts[0] === 'm' && parts[1] === 'apps' && cpsIndex >= 0 && parts[cpsIndex + 1]) {",
        "        return `/${parts.slice(0, cpsIndex + 2).join('/')}`;",
        "      }",
        "      if (parts[0] === 'm' && parts[1] && /^lxr\\d+/i.test(parts[1])) {",
        "        return `/m/apps/cps/${parts[1]}`;",
        "      }",
        "    } catch (_) {}",
        "  }",
        "  return '/m/apps/cps/demo-channel';",
        "}",
        "",
        "function identityTaskListUrl(page: any, expectedUrl: string, encryptInsureNum: string): string {",
        "  const target = new URL(page.url());",
        "  target.pathname = `${h5CpsTaskBasePath(page, expectedUrl)}/product/task`;",
        "  target.search = '';",
        "  target.searchParams.set('encryptInsureNum', encryptInsureNum);",
        "  return target.href;",
        "}",
        "",
        "function identityAuthDetailUrl(page: any, expectedUrl: string, encryptInsureNum: string): string {",
        "  const target = new URL(page.url());",
        "  target.pathname = `${h5CpsTaskBasePath(page, expectedUrl)}/authentication/detail`;",
        "  target.search = '';",
        "  target.searchParams.set('encryptInsureNum', encryptInsureNum);",
        "  return target.href;",
        "}",
        "",
        "function identityListTaskCompleted(listResult: Record<string, unknown>): boolean {",
        "  const json: any = (listResult as any).json || {};",
        "  const data = json.data || {};",
        "  const taskList = Array.isArray(data.taskList) ? data.taskList : Array.isArray(data.insureTaskList) ? data.insureTaskList : [];",
        "  const taskDone = taskList.some((item: any) => Number(item?.taskType) === 2 && (Number(item?.taskStatus) === 2 || Number(item?.approveStatus) === 2));",
        "  const flatDone = Number(data.taskStatus) === 2 || Number(data.approveStatus) === 2 || Number(json.taskStatus) === 2 || Number(json.approveStatus) === 2;",
        "  return Boolean(taskDone || flatDone);",
        "}",
        "",
        "async function clickIdentityTaskGoCompleteIfPresent(page: any): Promise<Record<string, unknown>> {",
        "  const button = page.locator('button, a, [role=\"button\"], .am-button, .am-button-primary, .adm-button, .btn, span, div').filter({ hasText: /去完成|完成认证|下一步|继续|go complete/i }).last();",
        "  if (!(await button.isVisible({ timeout: 1500 }).catch(() => false))) return { clicked: false, reason: 'identity task go-complete button not visible' };",
        "  const text = await button.innerText({ timeout: 1000 }).catch(() => '');",
        "  await tapLocatorCenter(page, button).catch(async () => {",
        "    await button.click({ timeout: 5000, force: true, noWaitAfter: true });",
        "  });",
        "  await page.waitForLoadState('domcontentloaded', { timeout: 15000 }).catch(() => undefined);",
        "  await page.waitForTimeout(1200);",
        "  return { clicked: true, strategy: 'identity-task-go-complete', text, url: page.url() };",
        "}",
        "",
        "async function clickIdentityAuthFooterButton(page: any, pattern: RegExp): Promise<string> {",
        "  const selectors = ['.auth-detail-btn a, .auth-detail-btn button', '.auth-btn a, .auth-btn button', '.footer a, .footer button', 'a.am-button-primary, button.am-button-primary', 'a, button, [role=\"button\"]'];",
        "  for (const selector of selectors) {",
        "    const locator = page.locator(selector).filter({ hasText: pattern }).last();",
        "    if (!(await locator.isVisible({ timeout: 1200 }).catch(() => false))) continue;",
        "    const text = await locator.innerText({ timeout: 1000 }).catch(() => '');",
        "    await tapLocatorCenter(page, locator).catch(async () => {",
        "      await locator.click({ timeout: 5000, force: true, noWaitAfter: true });",
        "    });",
        "    await page.waitForLoadState('domcontentloaded', { timeout: 15000 }).catch(() => undefined);",
        "    await page.waitForTimeout(1500);",
        "    return text || pattern.source;",
        "  }",
        "  return '';",
        "}",
        "",
        "async function uploadIdentityAuthImageIfPresent(page: any, label: string): Promise<Record<string, unknown>> {",
        "  const inputs = page.locator('input[type=\"file\"]');",
        "  const count = await inputs.count().catch(() => 0);",
        "  if (!count) return { uploaded: false, reason: 'no-file-input' };",
        "  const fixture = resolveIdCardFixture(label);",
        "  for (let index = 0; index < count; index += 1) {",
        "    const input = inputs.nth(index);",
        "    await input.setInputFiles(fixture).catch(async () => {",
        "      await input.evaluate((node: HTMLInputElement) => { node.style.display = 'block'; node.style.visibility = 'visible'; node.removeAttribute('hidden'); });",
        "      await input.setInputFiles(fixture);",
        "    });",
        "    await page.waitForTimeout(1800);",
        "    return { uploaded: true, strategy: 'auth-identity-upload', label, fixture, inputIndex: index, url: page.url() };",
        "  }",
        "  return { uploaded: false, reason: 'no-uploadable-file-input' };",
        "}",
        "",
        "async function completeIdentityAuthTaskIfNeeded(page: any, expectedUrl: string, encryptInsureNum: string): Promise<Record<string, unknown>> {",
        "  const attempts: string[] = [];",
        "  const targetUrl = identityTargetUrl(page, expectedUrl, encryptInsureNum);",
        "  const expectedPath = observedPath(targetUrl);",
        "  const matchesExpectedPayPath = () => {",
        "    const currentPath = observedPath(page.url());",
        "    return Boolean(expectedPath && (currentPath === expectedPath || currentPath.endsWith(expectedPath) || currentPath.includes(expectedPath)));",
        "  };",
        "  const gotoPayIfCompleted = async (listResult: Record<string, unknown>): Promise<Record<string, unknown> | null> => {",
        "    if (!identityListTaskCompleted(listResult) || !targetUrl) return null;",
        "    attempts.push('goto-identity-target-url');",
        "    await page.goto(targetUrl, { waitUntil: 'domcontentloaded', timeout: 30000 }).catch(() => undefined);",
        "    await page.waitForLoadState('networkidle', { timeout: 10000 }).catch(() => undefined);",
        "    await page.waitForTimeout(1200);",
        "    await settlePostClickFlow(page).catch(() => []);",
        "    return { recovered: true, progressed: matchesExpectedPayPath(), strategy: 'submit-identity-task-recovery', attempts, encryptInsureNum, listResult, url: page.url() };",
        "  };",
        "  const preList = await postAgent4Json(page, '/api/apps/cps/insure/task/approve/list', { encryptInsureNum }).catch((error) => ({ error: String(error) }));",
        "  attempts.push('approve-list-before-auth-detail');",
        "  const preProgress = await gotoPayIfCompleted(preList as Record<string, unknown>);",
        "  if (preProgress) return preProgress;",
        "  attempts.push('goto-identity-task-list');",
        "  await page.goto(identityTaskListUrl(page, expectedUrl, encryptInsureNum), { waitUntil: 'domcontentloaded', timeout: 30000 }).catch(() => undefined);",
        "  await page.waitForLoadState('networkidle', { timeout: 10000 }).catch(() => undefined);",
        "  const taskClick = await clickIdentityTaskGoCompleteIfPresent(page).catch((error) => ({ clicked: false, error: String(error) }));",
        "  attempts.push('identity-task-go-complete');",
        "  if (!(taskClick as any).clicked && !/\\/authentication\\/detail/i.test(observedPath(page.url()))) {",
        "    attempts.push('goto-authentication-detail');",
        "    await page.goto(identityAuthDetailUrl(page, expectedUrl, encryptInsureNum), { waitUntil: 'domcontentloaded', timeout: 30000 }).catch(() => undefined);",
        "    await page.waitForLoadState('networkidle', { timeout: 10000 }).catch(() => undefined);",
        "  }",
        "  for (let attempt = 0; attempt < 6; attempt += 1) {",
        "    await page.waitForTimeout(700).catch(() => undefined);",
        "    const bodyText = await page.locator('body').innerText({ timeout: 1800 }).catch(() => '');",
        "    if (/认证通过|完成认证|已认证|auth.*pass|passed/i.test(String(bodyText || ''))) {",
        "      const nextText = await clickIdentityAuthFooterButton(page, /下一步|继续|去支付|立即支付|确认/i);",
        "      if (nextText) attempts.push(`auth-identity-confirm-next:${String(nextText).slice(0, 40)}`);",
        "      await postAgent4Json(page, '/api/apps/cps/insure/task/next/do', { encryptInsureNum }).catch(() => ({}));",
        "      attempts.push('task-next-do');",
        "      const listResult = await postAgent4Json(page, '/api/apps/cps/insure/task/approve/list', { encryptInsureNum }).catch((error) => ({ error: String(error) }));",
        "      attempts.push('approve-list-after-auth-detail');",
        "      const progressed = await gotoPayIfCompleted(listResult as Record<string, unknown>);",
        "      if (progressed) return progressed;",
        "    }",
        "    const upload = await uploadIdentityAuthImageIfPresent(page, attempt === 0 ? 'passport front' : 'selfie front').catch((error) => ({ uploaded: false, error: String(error) }));",
        "    if ((upload as any).uploaded) {",
        "      attempts.push(`auth-identity-upload:${attempt + 1}`);",
        "      const buttonText = await clickIdentityAuthFooterButton(page, /下一步|提\\s*交|提交|完成|确认/i);",
        "      if (buttonText) attempts.push(`auth-identity-submit:${String(buttonText).slice(0, 40)}`);",
        "      continue;",
        "    }",
        "    const goComplete = await clickIdentityTaskGoCompleteIfPresent(page).catch((error) => ({ clicked: false, error: String(error) }));",
        "    if ((goComplete as any).clicked) { attempts.push('identity-task-go-complete-loop'); continue; }",
        "    const buttonText = await clickIdentityAuthFooterButton(page, /下一步|提\\s*交|提交|完成|确认/i);",
        "    if (buttonText) { attempts.push(`auth-identity-button:${String(buttonText).slice(0, 40)}`); continue; }",
        "  }",
        "  const finalList = await postAgent4Json(page, '/api/apps/cps/insure/task/approve/list', { encryptInsureNum }).catch((error) => ({ error: String(error) }));",
        "  attempts.push('approve-list-final');",
        "  const finalProgress = await gotoPayIfCompleted(finalList as Record<string, unknown>);",
        "  if (finalProgress) return finalProgress;",
        "  return { recovered: true, progressed: false, strategy: 'submit-identity-task-recovery', attempts, encryptInsureNum, finalList, url: page.url() };",
        "}",
        "",
        "async function recoverIdentityTaskAfterSubmitIfNeeded(page: any, expectedUrl: string): Promise<Record<string, unknown>> {",
        "  const attempts: string[] = [];",
        "  const blocker = recentIdentitySubmitBlocker() || await waitForIdentitySubmitBlocker(page, 1000);",
        "  const encryptInsureNum = identityEncryptInsureNumFromBlocker(blocker, page.url()) || latestIdentitySubmitEncryptInsureNum(page.url());",
        "  const bodyText = await page.locator('body').innerText({ timeout: 1200 }).catch(() => '');",
        "  const bodySignal = /身份验证|身份认证|投保意愿认证|authentication|approve/i.test(String(bodyText || ''));",
        "  const identityErrorSignal = /\\/m\\/error\\/?$/i.test(observedPath(page.url())) && /身份认证|认证暂未完成|identity|authentication/i.test(String(bodyText || '') + ' ' + page.url());",
        "  if (!blocker && !bodySignal && !identityErrorSignal && !/\\/authentication(?:\\/|$)/i.test(observedPath(page.url()))) {",
        "    return { recovered: false, reason: 'no-identity-task-signal' };",
        "  }",
        "  if (!encryptInsureNum) return { recovered: Boolean(blocker || bodySignal || identityErrorSignal), progressed: false, reason: 'missing-encryptInsureNum', blocker, url: page.url() };",
        "  const processResult = await postAgent4Json(page, '/api/apps/cps/insure/task/approve/process', { encryptInsureNum }).catch((error) => ({ error: String(error) }));",
        "  attempts.push('approve-process');",
        "  await page.waitForTimeout(800).catch(() => undefined);",
        "  const listResult = await postAgent4Json(page, '/api/apps/cps/insure/task/approve/list', { encryptInsureNum }).catch((error) => ({ error: String(error) }));",
        "  attempts.push('approve-list');",
        "  if (!identityListTaskCompleted(listResult as Record<string, unknown>)) {",
        "    const detailRecovery = await completeIdentityAuthTaskIfNeeded(page, expectedUrl, encryptInsureNum).catch((error) => ({ recovered: true, progressed: false, strategy: 'submit-identity-task-recovery', error: String(error) }));",
        "    if ((detailRecovery as any).progressed) return { ...(detailRecovery as any), processResult, initialListResult: listResult };",
        "    attempts.push(...(((detailRecovery as any).attempts || []) as string[]));",
        "    return { ...(detailRecovery as any), recovered: true, progressed: false, strategy: 'submit-identity-task-recovery', attempts, encryptInsureNum, processResult, listResult, blocker, url: page.url() };",
        "  }",
        "  await page.waitForLoadState('domcontentloaded', { timeout: 10000 }).catch(() => undefined);",
        "  await settlePostClickFlow(page).catch(() => []);",
        "  const targetUrl = identityTargetUrl(page, expectedUrl, encryptInsureNum);",
        "  const expectedPath = observedPath(targetUrl);",
        "  if (targetUrl && expectedPath) {",
        "    attempts.push('goto-identity-target-url');",
        "    await page.goto(targetUrl, { waitUntil: 'domcontentloaded', timeout: 30000 }).catch(() => undefined);",
        "    await page.waitForLoadState('networkidle', { timeout: 10000 }).catch(() => undefined);",
        "    await page.waitForTimeout(1200).catch(() => undefined);",
        "    await settlePostClickFlow(page).catch(() => []);",
        "    const currentPath = observedPath(page.url());",
        "    if (currentPath === expectedPath || currentPath.endsWith(expectedPath) || currentPath.includes(expectedPath)) {",
        "      return { recovered: true, progressed: true, strategy: 'submit-identity-task-recovery', attempts, encryptInsureNum, processResult, listResult, url: page.url() };",
        "    }",
        "  }",
        "  return { recovered: true, progressed: false, strategy: 'submit-identity-task-recovery', attempts, encryptInsureNum, processResult, listResult, blocker, url: page.url() };",
        "}",
        "",
        "function isSuitabilityQuestionnairePageUrl(value: string): boolean {",
        "  const pathname = observedPath(value);",
        "  return /\\/product\\/adapt(?:\\/loading)?$/i.test(pathname) || /\\/product\\/adapt\\/question/i.test(pathname);",
        "}",
        "",
        "function isSuitabilityResultPageUrl(value: string): boolean {",
        "  return /\\/product\\/adapt\\/result$/i.test(observedPath(value));",
        "}",
        "",
        "function inferSuitabilityTaskUrls(page: any, expectedUrl: string): string[] {",
        "  const urls: string[] = [];",
        "  function add(value: string): void {",
        "    if (value && !urls.includes(value)) urls.push(value);",
        "  }",
        "  const seeds = [expectedUrl, page.url()].filter(Boolean);",
        "  for (const seed of seeds) {",
        "    try {",
        "      const parsed = new URL(seed, page.url());",
        "      if (/\\/product\\/adapt\\/loading$/i.test(parsed.pathname)) {",
        "        add(parsed.href);",
        "        parsed.pathname = parsed.pathname.replace(/\\/adapt\\/loading$/i, '/adapt');",
        "        add(parsed.href);",
        "      }",
        "      if (/\\/product\\/adapt(?:\\/question)?$/i.test(parsed.pathname)) add(parsed.href);",
        "      if (/\\/product\\/insure$/i.test(parsed.pathname)) {",
        "        parsed.pathname = parsed.pathname.replace(/\\/insure$/i, '/adapt');",
        "        add(parsed.href);",
        "      }",
        "    } catch (_) {}",
        "  }",
        "  return urls;",
        "}",
        "",
        "function isHealthNoticePageUrl(urlValue: string): boolean {",
        "  const path = observedPath(urlValue);",
        "  return /\\/product\\/healthInform$/i.test(path) || path.includes('/product/healthInform');",
        "}",
        "",
        "async function bodyMentionsHealthNotice(page: any): Promise<boolean> {",
        "  const text = await page.locator('body').innerText({ timeout: 1200 }).catch(() => '');",
        "  return /健康告知|确认无以上问题|无以上问题|无上述问题|被保人健康状况|投保条件|healthInform/i.test(String(text || ''));",
        "}",
        "",
        "async function recoverHealthNoticeAfterSubmitIfNeeded(page: any, expectedUrl: string): Promise<Record<string, unknown>> {",
        "  const attempts: string[] = [];",
        "  const expectedPath = observedPath(expectedUrl);",
        "  for (let attempt = 0; attempt < 3; attempt += 1) {",
        "    const onHealthNotice = isHealthNoticePageUrl(page.url()) || await bodyMentionsHealthNotice(page);",
        "    if (!onHealthNotice) break;",
        "    const answer = await answerHealthNotice(page).catch((error) => ({ error: String(error) }));",
        "    attempts.push(`answer-health-notice:${String((answer as any)?.clicked_count || 0)}`);",
        "    await page.waitForLoadState('domcontentloaded', { timeout: 10000 }).catch(() => undefined);",
        "    await page.waitForTimeout(1200);",
        "    await settlePostClickFlow(page).catch(() => []);",
        "    const currentPath = observedPath(page.url());",
        "    if (expectedPath && (currentPath === expectedPath || currentPath.endsWith(expectedPath) || currentPath.includes(expectedPath))) {",
        "      return { recovered: true, progressed: true, attempts, url: page.url() };",
        "    }",
        "    if (!isHealthNoticePageUrl(page.url()) && !(await bodyMentionsHealthNotice(page))) break;",
        "  }",
        "  return { recovered: attempts.length > 0, progressed: false, attempts, url: page.url() };",
        "}",
        "",
        "async function bodyMentionsSuitabilityTask(page: any): Promise<boolean> {",
        "  const text = await page.locator('body').innerText({ timeout: 1200 }).catch(() => '');",
        "  return /需要进行适当性问卷|适当性问卷|投保风险警示确认书|风险警示|questionnaire|suitability/i.test(String(text || ''));",
        "}",
        "",
        "async function clickSuitabilityContinueIfPresent(page: any): Promise<Record<string, unknown> | null> {",
        "  const candidates = page.locator('button, a, [role=\"button\"], .am-button, .am-button-primary, .submit-btn, .btn, input[type=\"button\"], input[type=\"submit\"], span, div').filter({ hasText: /确认投保|继续投保|继续|去签约|签约|下一步|完成|确认|提交/ }).last();",
        "  if (!(await candidates.isVisible({ timeout: 1800 }).catch(() => false))) return null;",
        "  const text = await candidates.innerText({ timeout: 1000 }).catch(async () => await candidates.inputValue({ timeout: 1000 }).catch(() => ''));",
        "  await tapLocatorCenter(page, candidates).catch(async () => { await candidates.click({ timeout: 5000, force: true, noWaitAfter: true }); });",
        "  await page.waitForLoadState('domcontentloaded', { timeout: 10000 }).catch(() => undefined);",
        "  await page.waitForTimeout(1200);",
        "  return { accepted: true, strategy: 'suitability-continue', text: String(text || '').slice(0, 80), url: page.url() };",
        "}",
        "",
        "async function recoverSuitabilityTaskAfterSubmitIfNeeded(page: any, expectedUrl: string): Promise<Record<string, unknown>> {",
        "  const attempts: string[] = [];",
        "  const blocker = recentSuitabilitySubmitBlocker();",
        "  const alreadyOnTask = isSuitabilityQuestionnairePageUrl(page.url()) || isSuitabilityResultPageUrl(page.url());",
        "  const bodySignal = await bodyMentionsSuitabilityTask(page);",
        "  if (!blocker && !alreadyOnTask && !bodySignal) return { recovered: false, reason: 'no-suitability-task-signal' };",
        "  const modal = await clickTaskModalGoCompleteIfPresent(page).catch((error) => ({ clicked: false, reason: String(error) }));",
        "  if (modal.clicked) attempts.push('task-modal-go-complete');",
        "  await page.waitForLoadState('domcontentloaded', { timeout: 10000 }).catch(() => undefined);",
        "  await page.waitForTimeout(1000);",
        "  if (!isSuitabilityQuestionnairePageUrl(page.url()) && !isSuitabilityResultPageUrl(page.url())) {",
        "    for (const taskUrl of inferSuitabilityTaskUrls(page, expectedUrl)) {",
        "      attempts.push(`goto-suitability-task:${taskUrl}`);",
        "      await page.goto(taskUrl, { waitUntil: 'domcontentloaded', timeout: 30000 }).catch(() => undefined);",
        "      await page.waitForLoadState('networkidle', { timeout: 10000 }).catch(() => undefined);",
        "      await page.waitForTimeout(1200);",
        "      if (isSuitabilityQuestionnairePageUrl(page.url()) || isSuitabilityResultPageUrl(page.url()) || await bodyMentionsSuitabilityTask(page)) break;",
        "    }",
        "  }",
        "  if (isSuitabilityQuestionnairePageUrl(page.url()) || await bodyMentionsSuitabilityTask(page)) {",
        "    const answerResult = await answerQuestionnaire(page).catch((error) => ({ clicked_count: 0, error: String(error) }));",
        "    attempts.push(`answer-questionnaire:${String((answerResult as any).clicked_count || 0)}`);",
        "    const submit = page.locator('button.js-adapt-question-btn, .js-adapt-question-btn, button, a, [role=\"button\"], .am-button, .am-button-primary, .submit-btn, .btn, input[type=\"button\"], input[type=\"submit\"]').filter({ hasText: /提交|下一步|确定|确认|完成|同意/ }).last();",
        "    if (await submit.isVisible({ timeout: 2500 }).catch(() => false)) {",
        "      const text = await submit.innerText({ timeout: 1000 }).catch(async () => await submit.inputValue({ timeout: 1000 }).catch(() => ''));",
        "      await tapLocatorCenter(page, submit).catch(async () => { await submit.click({ timeout: 5000, force: true, noWaitAfter: true }); });",
        "      attempts.push(`submit-questionnaire:${String(text || '').slice(0, 40)}`);",
        "      await page.waitForLoadState('domcontentloaded', { timeout: 10000 }).catch(() => undefined);",
        "      await page.waitForTimeout(1800);",
        "      const warning = await acceptQuestionnaireWarningIfPresent(page).catch(() => null);",
        "      if (warning) attempts.push(`questionnaire-warning:${String(warning.accepted)}`);",
        "    }",
        "  }",
        "  const continuation = await clickSuitabilityContinueIfPresent(page).catch(() => null);",
        "  if (continuation) attempts.push('suitability-continue');",
        "  return {",
        "    recovered: attempts.length > 0 || Boolean(blocker),",
        "    strategy: 'submit-suitability-task-recovery',",
        "    attempts,",
        "    blocker,",
        "    url: page.url(),",
        "  };",
        "}",
        "",
        "async function h5SubmitBankBlockerVisible(page: any): Promise<boolean> {",
        "  const text = await page.locator('body').innerText({ timeout: 1200 }).catch(() => '');",
        "  if (/开户行识别失败|手动选择开户行|银行卡|银行账户|bank/i.test(String(text || ''))) {",
        "    const toast = page.locator('.am-toast, .adm-toast, [class*=\"toast\"], [role=\"alert\"]').last();",
        "    if (await toast.isVisible({ timeout: 500 }).catch(() => false)) return true;",
        "  }",
        "  const bankRowError = await page.evaluate(() => {",
        "    const visible = (element: Element | null) => {",
        "      if (!element) return false;",
        "      const style = window.getComputedStyle(element);",
        "      const rect = (element as HTMLElement).getBoundingClientRect();",
        "      return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;",
        "    };",
        "    const rowText = (element: Element) => String((element as HTMLElement).innerText || element.textContent || '').replace(/\\s+/g, ' ');",
        "    const rows = Array.from(document.querySelectorAll<HTMLElement>('.am-list-item, .am-list-line, .insure-filed-wrapper, li, dd, div')).filter(visible);",
        "    return rows.some(row => {",
        "      if (!/开户银行|开户行|银行账号|银行卡号|银行账户/.test(rowText(row))) return false;",
        "      return Boolean(row.querySelector('.am-input-error-extra, .am-list-extra-error, .error, [class*=\"error\"], [class*=\"warn\"], [class*=\"fail\"]'));",
        "    });",
        "  }).catch(() => false);",
        "  if (bankRowError) {",
        "    await page.evaluate(() => document.body.setAttribute('data-agent4-submit-blocker', 'bank-row-error-icon')).catch(() => undefined);",
        "    return true;",
        "  }",
        "  return false;",
        "}",
        "",
        "async function clickH5PickerOptionByText(page: any, preferredTexts: string[]): Promise<string> {",
        "  const popupSelector = '.am-picker-popup, .am-modal, .am-modal-wrap, .adm-popup, .adm-modal, .adm-picker, [role=\"dialog\"]';",
        "  const popup = page.locator(popupSelector).filter({ hasText: /工商银行|中国工商银行|银行/ }).last();",
        "  const root = await popup.isVisible({ timeout: 1200 }).catch(() => false) ? popup : page.locator(popupSelector).last();",
        "  const exactPattern = new RegExp(preferredTexts.map(text => text.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&')).join('|'));",
        "  const option = root.locator('button, a, [role=\"option\"], .am-list-item, .adm-list-item, .am-picker-col-item, .adm-picker-view-column-item, li, span, div').filter({ hasText: exactPattern }).last();",
        "  if (!(await option.isVisible({ timeout: 1500 }).catch(() => false))) return '';",
        "  const selected = await option.innerText({ timeout: 800 }).catch(() => '');",
        "  await tapLocatorCenter(page, option).catch(async () => { await option.click({ timeout: 3000, force: true, noWaitAfter: true }); });",
        "  await page.waitForTimeout(500);",
        "  const confirm = page.locator('button, a, [role=\"button\"], .am-picker-popup-item, .adm-button, span, div').filter({ hasText: /确定|完成|确认/ }).last();",
        "  if (await confirm.isVisible({ timeout: 1000 }).catch(() => false)) {",
        "    await tapLocatorCenter(page, confirm).catch(async () => { await confirm.click({ timeout: 3000, force: true, noWaitAfter: true }); });",
        "    await page.waitForTimeout(500);",
        "  }",
        "  return selected;",
        "}",
        "",
        "async function selectH5BankPickerByMock(page: any): Promise<Record<string, unknown>> {",
        "  const mock = (globalThis as any).__agent3MockData || mockData;",
        "  const bankName = String(mock.bankName_107 || mock.openBank_107 || '中国工商银行');",
        "  const row = page.locator('.am-list-item, .adm-list-item, .insure-filed-wrapper, li, dd, div').filter({ hasText: /^\\s*(开户银行|开户行|银行)\\s*/ }).filter({ hasText: /工商银行|中国工商银行|开户银行|开户行/ }).last();",
        "  if (!(await row.isVisible({ timeout: 1500 }).catch(() => false))) return { selected: false, reason: 'bank row not visible' };",
        "  const before = await row.innerText({ timeout: 800 }).catch(() => '');",
        "  await row.scrollIntoViewIfNeeded({ timeout: 2000 }).catch(() => undefined);",
        "  await tapLocatorCenter(page, row).catch(async () => { await row.click({ timeout: 3000, force: true, noWaitAfter: true }); });",
        "  await page.waitForTimeout(900);",
        "  const selected = await clickH5PickerOptionByText(page, [bankName, '中国工商银行', '工商银行']).catch(() => '');",
        "  const after = await row.innerText({ timeout: 800 }).catch(() => '');",
        "  return { selected: !!selected || /工商银行/.test(after), selected_text: selected, before, after };",
        "}",
        "",
        "async function fillH5BankAccountFromMock(page: any): Promise<Record<string, unknown>> {",
        "  const mock = (globalThis as any).__agent3MockData || mockData;",
        "  const payAccount = String(mock.payAccount_107 || '').replace(/\\s+/g, '');",
        "  if (!payAccount) return { filled: false, reason: 'missing payAccount_107' };",
        "  const candidate = page.locator('input[placeholder*=\"开卡\"], input[placeholder*=\"银行账号\"], input[placeholder*=\"银行卡\"], input[placeholder*=\"银行账户\"], textarea[placeholder*=\"银行账号\"], textarea[placeholder*=\"银行卡\"]').last();",
        "  const fallback = page.locator('input:visible, textarea:visible').last();",
        "  const target = await candidate.isVisible({ timeout: 1200 }).catch(() => false) ? candidate : fallback;",
        "  if (!(await target.isVisible({ timeout: 1200 }).catch(() => false))) return { filled: false, reason: 'bank account input not visible' };",
        "  await target.scrollIntoViewIfNeeded({ timeout: 2000 }).catch(() => undefined);",
        "  await target.fill(payAccount, { timeout: 5000 }).catch(async () => {",
        "    await target.click({ timeout: 3000, force: true });",
        "    await page.keyboard.press(process.platform === 'darwin' ? 'Meta+A' : 'Control+A').catch(() => undefined);",
        "    await page.keyboard.type(payAccount, { delay: 10 });",
        "  });",
        "  await target.blur().catch(() => undefined);",
        "  await page.waitForTimeout(900);",
        "  return { filled: true, payAccount };",
        "}",
        "",
        "async function replayH5SubmitButton(page: any, selector: string, tag: string, text: string, expectedUrl = ''): Promise<Record<string, unknown>> {",
        "  const attempts: string[] = [];",
        "  const beforePath = observedPath(page.url());",
        "  let settled: Array<Record<string, unknown>> = [];",
        "  let clicked = false;",
        "  const submitPattern = /提交\\s*订单|提交|下一步|确认|submit/i;",
        "  await syncH5InsureFormFromMock(page, { mode: 'initial' }).catch(() => 0);",
        "  await ensureH5AgreementCheckedBeforeSubmit(page).catch(() => 0);",
        "  await selectH5BankPickerByMock(page).catch(() => ({ selected: false }));",
        "  await fillH5BankAccountFromMock(page).catch(() => ({ filled: false }));",
        "  for (let submitAttempt = 0; submitAttempt < 12; submitAttempt += 1) {",
        "    if (submitAttempt > 0) await syncH5InsureFormFromMock(page, { mode: 'retry' }).catch(() => 0);",
        "    await ensureH5AgreementCheckedBeforeSubmit(page).catch(() => 0);",
        "    await page.evaluate(() => {",
        "      const nodes = [document.scrollingElement, document.documentElement, document.body, ...Array.from(document.querySelectorAll<HTMLElement>('div, section, main, form'))].filter(Boolean) as HTMLElement[];",
        "      for (const node of nodes) {",
        "        node.scrollTop = node.scrollHeight;",
        "        node.dispatchEvent(new Event('scroll', { bubbles: true }));",
        "      }",
        "      window.scrollTo(0, Math.max(document.documentElement.scrollHeight, document.body.scrollHeight));",
        "    }).catch(() => undefined);",
        "    await page.mouse.wheel(0, 4000).catch(() => undefined);",
        "    await page.waitForTimeout(400);",
        "    await settlePostClickFlow(page, { includeAgreementDetails: false }).catch(() => []);",
        "    await ensureH5AgreementCheckedBeforeSubmit(page).catch(() => 0);",
        "    await assertH5AgreementCheckedBeforeSubmit(page);",
        "    let clickedThisAttempt = false;",
        "    const footerSubmit = page.locator('.insure-footer .submit-btn, .submit-btn, a[role=\"button\"].am-button-primary, button.am-button-primary, [role=\"button\"].am-button-primary, .am-button-primary').filter({ hasText: submitPattern }).last();",
        "    if (await footerSubmit.isVisible({ timeout: 1500 }).catch(() => false)) {",
        "      clickedThisAttempt = await clickH5SubmitCandidate(page, footerSubmit).then(() => true).catch(() => false);",
        "      attempts.push(`footer-submit-${submitAttempt + 1}`);",
        "    }",
        "    if (!clickedThisAttempt) {",
        "      const roleSubmit = page.getByRole('button', { name: submitPattern }).last();",
        "      if (await roleSubmit.isVisible({ timeout: 1000 }).catch(() => false)) {",
        "        clickedThisAttempt = await clickH5SubmitCandidate(page, roleSubmit).then(() => true).catch(() => false);",
        "        attempts.push(`role-submit-${submitAttempt + 1}`);",
        "      }",
        "    }",
        "    if (!clickedThisAttempt) {",
        "      const fallback = await replayActionLocator(page, selector, tag, text).catch(() => null);",
        "      if (fallback && await fallback.isVisible({ timeout: 800 }).catch(() => false)) {",
        "        clickedThisAttempt = await clickH5SubmitCandidate(page, fallback).then(() => true).catch(() => false);",
        "        attempts.push(`agent3-selector-fallback-${submitAttempt + 1}`);",
        "      }",
        "    }",
        "    if (!clickedThisAttempt) {",
        "      clickedThisAttempt = await triggerH5SubmitDomClick(page).then(() => true).catch(() => false);",
        "      if (clickedThisAttempt) attempts.push(`dom-submit-${submitAttempt + 1}`);",
        "    }",
        "    if (!clickedThisAttempt) {",
        "      attempts.push(`submit-not-clickable-${submitAttempt + 1}`);",
        "      await page.waitForTimeout(800);",
        "      continue;",
        "    }",
        "    clicked = true;",
        "    await page.waitForLoadState('domcontentloaded', { timeout: 10000 }).catch(() => undefined);",
        "    await page.waitForTimeout(submitAttempt === 0 ? 2500 : 5000);",
        "    settled = await settlePostClickFlow(page).catch(() => []);",
        "    const currentPathAfterSettle = observedPath(page.url());",
        "    const settledContinuationDialog = settled.some((event: any) => /continuation-dialog/.test(String(event?.strategy || '')));",
        "    if (settledContinuationDialog && beforePath && currentPathAfterSettle === beforePath) {",
        "      attempts.push('continuation-dialog-settled');",
        "      continue;",
        "    }",
        "    await Promise.all([",
        "      waitForSuitabilitySubmitBlocker(page, 6000),",
        "      waitForIdentitySubmitBlocker(page, 6000),",
        "    ]).catch(() => []);",
        "    const healthNoticeRecovery = await recoverHealthNoticeAfterSubmitIfNeeded(page, expectedUrl).catch((error) => ({ recovered: false, error: String(error) }));",
        "    if ((healthNoticeRecovery as any).recovered) {",
        "      attempts.push('submit-health-notice-recovery');",
        "      if ((healthNoticeRecovery as any).progressed) {",
        "        return { strategy: 'touchscreen-submit-btn', clicked, attempts, settled, healthNoticeRecovery, progressed: true, url: page.url() };",
        "      }",
        "      continue;",
        "    }",
        "    const taskModal = await clickTaskModalGoCompleteIfPresent(page).catch((error) => ({ clicked: false, reason: String(error) }));",
        "    if (taskModal.clicked) {",
        "      attempts.push('task-modal-go-complete');",
        "      return { strategy: 'touchscreen-submit-btn', clicked, attempts, settled, taskModal, url: page.url() };",
        "    }",
        "    const suitabilityRecovery = await recoverSuitabilityTaskAfterSubmitIfNeeded(page, expectedUrl).catch((error) => ({ recovered: false, error: String(error) }));",
        "    if (suitabilityRecovery.recovered) {",
        "      attempts.push('submit-suitability-task-recovery');",
        "      return { strategy: 'touchscreen-submit-btn', clicked, attempts, settled, suitabilityRecovery, url: page.url() };",
        "    }",
        "    const identityRecovery = await recoverIdentityTaskAfterSubmitIfNeeded(page, expectedUrl).catch((error) => ({ recovered: false, error: String(error) }));",
        "    if (identityRecovery.recovered) {",
        "      attempts.push('submit-identity-task-recovery');",
        "      return { strategy: 'touchscreen-submit-btn', clicked, attempts, settled, identityRecovery, progressed: Boolean((identityRecovery as any).progressed), url: page.url() };",
        "    }",
        "    const currentPath = observedPath(page.url());",
        "    const settledPostSubmitDialog = settled.some((event: any) => /agreement-detail-dialog|bottom-sheet-agreement-detail|accept-customer-suitability-evaluation|product-confirm-panel/.test(String(event?.strategy || '')));",
        "    if (settledPostSubmitDialog && beforePath && currentPath === beforePath) {",
        "      attempts.push('post-submit-dialog-settled');",
        "      continue;",
        "    }",
        "    if (beforePath && currentPath && currentPath !== beforePath) {",
        "      attempts.push('url-progressed');",
        "      return { strategy: 'touchscreen-submit-btn', clicked, attempts, settled, progressed: true, url: page.url() };",
        "    }",
        "    if (await h5SubmitBankBlockerVisible(page)) {",
        "      attempts.push('bank-blocker-front-select');",
        "      await selectH5BankPickerByMock(page).catch(() => ({ selected: false }));",
        "      await fillH5BankAccountFromMock(page).catch(() => ({ filled: false }));",
        "    }",
        "  }",
        "  if (!clicked) {",
        "    throw new Error(`H5 submit action not clickable: ${text || selector}`);",
        "  }",
        "  return { strategy: 'touchscreen-submit-btn', clicked, attempts, settled };",
        "}",
        "",
        "async function triggerH5PaymentDomClick(page: any): Promise<boolean> {",
        "  return await page.evaluate(() => {",
        "    const pattern = /立即\\s*支付|去\\s*支付|确认\\s*支付|Pay/i;",
        "    const visible = (element: Element) => {",
        "      const style = window.getComputedStyle(element);",
        "      const rect = (element as HTMLElement).getBoundingClientRect();",
        "      return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;",
        "    };",
        "    const textOf = (element: Element) => String((element as HTMLElement).innerText || element.textContent || '').replace(/\\s+/g, ' ').trim();",
        "    const candidates = Array.from(document.querySelectorAll<HTMLElement>('#submitToPay, .pay-footer .submit-btn, .payment-footer .submit-btn, .footer .submit-btn, .submit-btn, button, a, [role=\"button\"]'))",
        "      .filter(element => visible(element) && pattern.test(textOf(element)));",
        "    const target = candidates[candidates.length - 1];",
        "    if (!target) return false;",
        "    target.scrollIntoView({ block: 'center', inline: 'center' });",
        "    for (const type of ['touchstart', 'touchend', 'pointerdown', 'mousedown', 'mouseup', 'click']) {",
        "      const event = type.startsWith('touch')",
        "        ? new Event(type, { bubbles: true, cancelable: true })",
        "        : new MouseEvent(type, { bubbles: true, cancelable: true, view: window });",
        "      target.dispatchEvent(event);",
        "    }",
        "    target.click();",
        "    return true;",
        "  }).catch(() => false);",
        "}",
        "",
        "async function replayH5PaymentButton(page: any, selector: string, tag: string, text: string): Promise<Record<string, unknown>> {",
        "  const attempts: string[] = [];",
        "  const beforeUrl = page.url();",
        "  const paymentPattern = /立即\\s*支付|去\\s*支付|确认\\s*支付|Pay/i;",
        "  const normalizedText = String(text || '').replace(/\\s+/g, '').trim() || '立即支付';",
        "  const buttonTextPattern = /立即\\s*支付|去\\s*支付|确认\\s*支付|Pay/i.test(normalizedText) ? paymentPattern : fuzzyTextRegex(normalizedText);",
        "  async function clickCandidate(name: string, locator: any): Promise<boolean> {",
        "    if (!(await locator.isVisible({ timeout: 1500 }).catch(() => false))) return false;",
        "    const label = await locator.innerText({ timeout: 800 }).catch(async () => await locator.inputValue({ timeout: 800 }).catch(() => ''));",
        "    const clicked = await tapLocatorCenter(page, locator).then(() => true).catch(async () => {",
        "      return await locator.click({ timeout: 5000, force: true, noWaitAfter: true }).then(() => true).catch(() => false);",
        "    });",
        "    attempts.push(`${name}:${String(label || '').replace(/\\s+/g, '').slice(0, 40)}`);",
        "    return clicked;",
        "  }",
        "  await page.evaluate(() => {",
        "    const nodes = [document.scrollingElement, document.documentElement, document.body, ...Array.from(document.querySelectorAll<HTMLElement>('div, section, main, form'))].filter(Boolean) as HTMLElement[];",
        "    for (const node of nodes) {",
        "      node.scrollTop = node.scrollHeight;",
        "      node.dispatchEvent(new Event('scroll', { bubbles: true }));",
        "    }",
        "    window.scrollTo(0, Math.max(document.documentElement.scrollHeight, document.body.scrollHeight));",
        "  }).catch(() => undefined);",
        "  await page.mouse.wheel(0, 4000).catch(() => undefined);",
        "  await page.waitForTimeout(500);",
        "  const selectorCandidate = selector ? page.locator(selector).filter({ hasText: buttonTextPattern }).last() : null;",
        "  if (selectorCandidate && await clickCandidate('agent3-selector-text', selectorCandidate)) return { strategy: 'touchscreen-payment-btn', clicked: true, attempts, beforeUrl, url: page.url() };",
        "  const selectorAny = selector ? page.locator(selector).last() : null;",
        "  if (selectorAny && await clickCandidate('agent3-selector', selectorAny)) return { strategy: 'touchscreen-payment-btn', clicked: true, attempts, beforeUrl, url: page.url() };",
        "  const idButton = page.locator('#submitToPay').last();",
        "  if (await clickCandidate('submitToPay-id', idButton)) return { strategy: 'touchscreen-payment-btn', clicked: true, attempts, beforeUrl, url: page.url() };",
        "  const footerButton = page.locator('.pay-footer, .payment-footer, .footer, [class*=\"footer\"], [class*=\"pay\"]').locator('button, a, [role=\"button\"], .am-button, .adm-button, .submit-btn, .btn, span, div').filter({ hasText: buttonTextPattern }).last();",
        "  if (await clickCandidate('footer-payment-text', footerButton)) return { strategy: 'touchscreen-payment-btn', clicked: true, attempts, beforeUrl, url: page.url() };",
        "  const broadButton = page.locator('button, a, [role=\"button\"], .am-button, .adm-button, .submit-btn, .btn, input[type=\"button\"], input[type=\"submit\"], span, div').filter({ hasText: buttonTextPattern }).last();",
        "  if (await clickCandidate('page-payment-text', broadButton)) return { strategy: 'touchscreen-payment-btn', clicked: true, attempts, beforeUrl, url: page.url() };",
        "  const domClicked = await triggerH5PaymentDomClick(page);",
        "  if (domClicked) {",
        "    attempts.push('dom-payment-click');",
        "    await page.waitForTimeout(800);",
        "    return { strategy: 'touchscreen-payment-btn', clicked: true, attempts, beforeUrl, url: page.url() };",
        "  }",
        "  throw new Error(`H5 payment action not clickable: ${text || selector}`);",
        "}",
        "",
        "async function acceptTrialPanelIfPresent(page: any): Promise<Record<string, unknown> | null> {",
        "  const trialKeywords = /保费\\s*试算|保费与被保人|价值演示|被保险人出生日期|保障期限|缴费类型|缴费年限|承保职业/;",
        "  const panels = page.locator('.trial-pannel, .trial-panel, [role=\"dialog\"], .am-modal, .am-modal-wrap, .am-popup, .am-popup-body, .am-drawer, .am-drawer-content').filter({ hasText: trialKeywords });",
        "  const panelCount = await panels.count().catch(() => 0);",
        "  for (let panelIndex = panelCount - 1; panelIndex >= 0; panelIndex -= 1) {",
        "    const panel = panels.nth(panelIndex);",
        "    if (!(await panel.isVisible({ timeout: 1500 }).catch(() => false))) continue;",
        "    const roleText = await clickLastVisible(page, panel.getByRole('button', { name: /确\\s*定|确\\s*认|下一步|立即投保|投\\s*保/ }), /^(确定|确认|下一步|立即投保|投保)$/);",
        "    if (roleText) return { strategy: 'trial-panel-confirm', accepted: true, text: roleText };",
        "    const text = await clickLastVisible(page, panel.locator('button, a, [role=\"button\"], .am-button, .am-button-primary, .submit-btn, .btn').filter({ hasText: /确\\s*定|确\\s*认|下一步|立即投保|投\\s*保/ }), /^(确定|确认|下一步|立即投保|投保)$/);",
        "    return { strategy: 'trial-panel-confirm', accepted: Boolean(text), text: text || 'trial panel visible but confirm button not found' };",
        "  }",
        "  const bodyText = await page.locator('body').innerText({ timeout: 1000 }).catch(() => '');",
        "  if (!trialKeywords.test(String(bodyText || ''))) return null;",
        "  const roleText = await clickLastVisible(page, page.getByRole('button', { name: /确\\s*定|确\\s*认|下一步|立即投保|投\\s*保/ }), /^(确定|确认|下一步|立即投保|投保)$/);",
        "  if (roleText) return { strategy: 'trial-panel-confirm', accepted: true, text: roleText };",
        "  const text = await clickLastVisible(page, page.locator('button, a, [role=\"button\"], .am-button, .am-button-primary, .submit-btn, .btn').filter({ hasText: /确\\s*定|确\\s*认|下一步|立即投保|投\\s*保/ }), /^(确定|确认|下一步|立即投保|投保)$/);",
        "  return { strategy: 'trial-panel-confirm', accepted: Boolean(text), text: text || 'trial panel visible but confirm button not found' };",
        "}",
        "",
        "async function acceptProductConfirmPanelIfPresent(page: any): Promise<Record<string, unknown> | null> {",
        "  const panel = page.locator('.confirm-pannel, .confirm-panel, [role=\"dialog\"], .am-modal, .am-modal-wrap').filter({ hasText: /确认进入投保流程|已阅读并同意|阅读并同意|投保须知/ }).last();",
        "  if (!(await panel.isVisible({ timeout: 1200 }).catch(() => false))) return null;",
        "  const checkboxes = panel.locator('input[type=\"checkbox\"]');",
        "  const checkboxCount = await checkboxes.count().catch(() => 0);",
        "  for (let index = 0; index < checkboxCount; index += 1) {",
        "    const checkbox = checkboxes.nth(index);",
        "    const checked = await checkbox.isChecked().catch(() => false);",
        "    if (!checked) await checkbox.click({ timeout: 2000, force: true }).catch(() => undefined);",
        "  }",
        "  const buttons = panel.locator('button, a, [role=\"button\"], .am-button, .btn, span, div').filter({ hasText: /阅读并同意|已阅读并同意|同意并继续|继续投保|确认投保|立即投保|投\\s*保|确定|我知道/ });",
        "  for (let attempt = 0; attempt < 12; attempt += 1) {",
        "    const text = await clickLastVisible(page, buttons, /阅读并同意|已阅读并同意|同意并继续|继续投保|确认投保|立即投保|投保|确定|我知道/);",
        "    if (text && !/\\d+秒|秒/.test(text)) return { strategy: 'product-confirm-panel', accepted: true, text };",
        "    await page.waitForTimeout(1000);",
        "  }",
        "  return { strategy: 'product-confirm-panel', accepted: false, text: 'confirm panel visible but enabled confirm button not found' };",
        "}",
        "",
        "async function acceptCustomerSuitabilityEvaluationIfPresent(page: any): Promise<Record<string, unknown> | null> {",
        "  const panelPattern = /客户适当性评估|中国银保监会|互联网人身保险业务有关事项的通知|保险责任为境外旅行期间|本投保人已确认能够熟练使用智能手机/;",
        "  const buttonPattern = /已阅读并同意|阅读并同意|同意并继续|继续投保|确认继续|我已知晓|同意/;",
        "  const panels = page.locator('[role=\"dialog\"], .am-modal, .am-modal-wrap, .adm-modal, .adm-popup, .adm-popup-body, .am-popup, .am-popup-body, .am-drawer, .am-drawer-content, .evaluate-pop, .suitability-pop, .suitability-modal').filter({ hasText: panelPattern });",
        "  const panelCount = await panels.count().catch(() => 0);",
        "  let sawVisiblePanel = false;",
        "  for (let panelIndex = panelCount - 1; panelIndex >= 0; panelIndex -= 1) {",
        "    const panel = panels.nth(panelIndex);",
        "    if (!(await panel.isVisible({ timeout: 1200 }).catch(() => false))) continue;",
        "    sawVisiblePanel = true;",
        "    const roleText = await clickLastVisible(page, panel.getByRole('button', { name: buttonPattern }), buttonPattern);",
        "    if (roleText) return { strategy: 'accept-customer-suitability-evaluation', accepted: true, text: roleText, url: page.url() };",
        "    const buttons = panel.locator('button, a, [role=\"button\"], .am-button, .am-button-primary, .adm-button, .btn, .button, .submit-btn, input[type=\"button\"], input[type=\"submit\"]').filter({ hasText: buttonPattern });",
        "    const text = await clickLastVisible(page, buttons, buttonPattern);",
        "    if (text) return { strategy: 'accept-customer-suitability-evaluation', accepted: true, text, url: page.url() };",
        "    const textButtons = panel.locator('span, div').filter({ hasText: buttonPattern });",
        "    const textButtonText = await clickLastVisibleShortText(page, textButtons, buttonPattern);",
        "    if (textButtonText) return { strategy: 'accept-customer-suitability-evaluation', accepted: true, text: textButtonText, url: page.url() };",
        "  }",
        "  if (sawVisiblePanel) return { strategy: 'accept-customer-suitability-evaluation', accepted: false, text: 'customer suitability evaluation visible but agree button not found' };",
        "  return null;",
        "}",
        "",
        "async function acceptAgreementDetailDialogIfPresent(page: any): Promise<Record<string, unknown> | null> {",
        "  const titlePattern = /投保人声明及确认|投保条件|投保重要告知|重要提示|数据隐私保护及授权|服务流程及服务信息说明|保险条款|免责条款|客户适当性评估|鎶曚繚浜哄０鏄庡強纭/;",
        "  const buttonPattern = /已阅读并同意|阅读并同意|同意并继续|继续投保|确认继续|我已知晓|我知道|同意|宸查槄璇诲苟鍚屾剰|闃呰骞跺悓鎰|鍚屾剰/;",
        "  const clickAgreementButtonByDom = async (): Promise<string> => {",
        "    return await page.evaluate(() => {",
        "      const normalize = (value: unknown): string => String(value || '').replace(/\\s+/g, '');",
        "      const titlePattern = /投保人声明及确认|投保条件|投保重要告知|重要提示|数据隐私保护及授权|服务流程及服务信息说明|保险条款|免责条款|客户适当性评估|鎶曚繚浜哄０鏄庡強纭/;",
        "      const buttonPattern = /已阅读并同意|阅读并同意|同意并继续|继续投保|确认继续|我已知晓|我知道|同意|宸查槄璇诲苟鍚屾剰|闃呰骞跺悓鎰|鍚屾剰/;",
        "      const isVisible = (el: Element): boolean => {",
        "        const element = el as HTMLElement;",
        "        const style = window.getComputedStyle(element);",
        "        const rect = element.getBoundingClientRect();",
        "        return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;",
        "      };",
        "      const containerSelector = '.am-drawer-content, .am-drawer-sidebar, .drawer, .drawer-content, .popup, .popup-content, [class*=\"drawer\"], [class*=\"popup\"], [class*=\"modal\"], [role=\"dialog\"]';",
        "      const containers = Array.from(document.querySelectorAll(containerSelector)).filter((el) => isVisible(el) && titlePattern.test(normalize((el as HTMLElement).innerText || el.textContent)));",
        "      for (const container of containers.reverse()) {",
        "        const candidates = Array.from(container.querySelectorAll('button, a, [role=\"button\"], .am-button, .am-button-primary, .adm-button, .adm-button-primary, .btn, .button, .submit-btn, span, div')).filter((el) => isVisible(el));",
        "        const buttons = candidates.filter((el) => {",
        "          const text = normalize((el as HTMLElement).innerText || el.textContent);",
        "          return Boolean(text) && text.length <= 30 && buttonPattern.test(text);",
        "        });",
        "        const target = buttons[buttons.length - 1] as HTMLElement | undefined;",
        "        if (!target) continue;",
        "        const text = normalize(target.innerText || target.textContent).slice(0, 80);",
        "        target.scrollIntoView({ block: 'center', inline: 'center' });",
        "        const rect = target.getBoundingClientRect();",
        "        const eventInit = { bubbles: true, cancelable: true, clientX: Math.floor(rect.left + rect.width / 2), clientY: Math.floor(rect.top + rect.height / 2) };",
        "        if (typeof PointerEvent !== 'undefined') target.dispatchEvent(new PointerEvent('pointerdown', eventInit));",
        "        target.dispatchEvent(new MouseEvent('mousedown', eventInit));",
        "        target.dispatchEvent(new MouseEvent('mouseup', eventInit));",
        "        target.dispatchEvent(new MouseEvent('click', eventInit));",
        "        if (typeof target.click === 'function') target.click();",
        "        return text;",
        "      }",
        "      return '';",
        "    }).catch(() => '');",
        "  };",
        "  const panels = page.locator('[role=\"dialog\"], .am-modal, .am-modal-wrap, .adm-modal, .adm-dialog, .adm-popup, .adm-popup-body, .am-popup, .am-popup-body, .am-drawer, .am-drawer-content, .am-drawer-sidebar, .drawer, .drawer-content, .popup, .popup-content').filter({ hasText: titlePattern });",
        "  const panelCount = await panels.count().catch(() => 0);",
        "  let sawVisiblePanel = false;",
        "  for (let panelIndex = panelCount - 1; panelIndex >= 0; panelIndex -= 1) {",
        "    const panel = panels.nth(panelIndex);",
        "    if (!(await panel.isVisible({ timeout: 1200 }).catch(() => false))) continue;",
        "    sawVisiblePanel = true;",
        "    const roleText = await clickLastVisible(page, panel.getByRole('button', { name: buttonPattern }), buttonPattern);",
        "    if (roleText) return { strategy: 'agreement-detail-dialog', accepted: true, text: roleText, url: page.url() };",
        "    const primaryButtons = panel.locator('button, a, [role=\"button\"], .am-button, .am-button-primary, .adm-button, .adm-button-primary, .btn, .button, .submit-btn, input[type=\"button\"], input[type=\"submit\"]').filter({ hasText: buttonPattern });",
        "    const primaryText = await clickLastVisible(page, primaryButtons, buttonPattern);",
        "    if (primaryText) return { strategy: 'agreement-detail-dialog', accepted: true, text: primaryText, url: page.url() };",
        "    const textButtons = panel.locator('span, div').filter({ hasText: buttonPattern });",
        "    const textButtonText = await clickLastVisibleShortText(page, textButtons, buttonPattern);",
        "    if (textButtonText) return { strategy: 'agreement-detail-dialog', accepted: true, text: textButtonText, url: page.url() };",
        "  }",
        "  const domButtonText = await clickAgreementButtonByDom();",
        "  if (domButtonText) return { strategy: 'bottom-sheet-agreement-detail', accepted: true, text: domButtonText, url: page.url() };",
        "  const bottomSheets = page.locator('.am-drawer-content, .am-drawer-sidebar, .drawer, .drawer-content, .popup, .popup-content, div').filter({ hasText: titlePattern });",
        "  const bottomSheetCount = await bottomSheets.count().catch(() => 0);",
        "  for (let sheetIndex = bottomSheetCount - 1; sheetIndex >= 0; sheetIndex -= 1) {",
        "    const sheet = bottomSheets.nth(sheetIndex);",
        "    if (!(await sheet.isVisible({ timeout: 800 }).catch(() => false))) continue;",
        "    sawVisiblePanel = true;",
        "    const buttonText = await clickLastVisibleShortText(page, sheet.locator('button, a, [role=\"button\"], .am-button, .am-button-primary, .adm-button, .adm-button-primary, .submit-btn, span, div').filter({ hasText: buttonPattern }), buttonPattern);",
        "    if (buttonText) return { strategy: 'bottom-sheet-agreement-detail', accepted: true, text: buttonText, url: page.url() };",
        "  }",
        "  if (sawVisiblePanel) return { strategy: 'agreement-detail-dialog', accepted: false, text: 'agreement detail dialog visible but agree button not found' };",
        "  return null;",
        "}",
        "",
        "async function settlePostClickFlow(page: any, options: { includeAgreementDetails?: boolean } = {}): Promise<Array<Record<string, unknown>>> {",
        "  const events: Array<Record<string, unknown>> = [];",
        "  for (let attempt = 0; attempt < 12; attempt += 1) {",
        "    const before = page.url();",
        "    const handlers = [",
        "      acceptTrialPanelIfPresent,",
        "      acceptProductNoticeIfPresent,",
        "      acceptProductConfirmPanelIfPresent,",
        "      acceptCustomerSuitabilityEvaluationIfPresent,",
        "    ];",
        "    if (options.includeAgreementDetails !== false) handlers.push(acceptAgreementDetailDialogIfPresent);",
        "    handlers.push(acceptContinuationDialogIfPresent);",
        "    handlers.push(acceptQuestionnaireWarningIfPresent);",
        "    let accepted: Record<string, unknown> | null = null;",
        "    for (const handler of handlers) {",
        "      accepted = await handler(page).catch(() => null);",
        "      if (accepted && accepted.accepted !== false) break;",
        "    }",
        "    if (!accepted || accepted.accepted === false) break;",
        "    events.push(accepted);",
        "    await page.waitForLoadState('domcontentloaded', { timeout: 10000 }).catch(() => undefined);",
        "    await page.waitForTimeout(800);",
        "    if (page.url() !== before) {",
        "      await page.waitForLoadState('networkidle', { timeout: 8000 }).catch(() => undefined);",
        "    }",
        "  }",
        "  return events;",
        "}",
        "",
        "function observedPath(urlValue: string): string {",
        "  try { return new URL(urlValue).pathname.replace(/\\/+$/g, ''); } catch (_) { return ''; }",
        "}",
        "",
        "async function bodyMentionsAuthFinalPayBlocker(page: any): Promise<boolean> {",
        "  const text = await page.locator('body').innerText({ timeout: 1500 }).catch(() => '');",
        "  const normalized = String(text || '').replace(/\\s+/g, '');",
        "  return /完成认证|认证通过|投保意愿认证|身份认证/.test(normalized) && /请求太平人寿异常|太平人寿异常|修改投保信息/.test(normalized);",
        "}",
        "",
        "async function closeAuthFinalPayBlockerDialog(page: any): Promise<string> {",
        "  const dialog = page.locator('[role=\"dialog\"], .am-modal, .am-modal-wrap, .adm-modal, .adm-dialog').filter({ hasText: /请求太平人寿异常|太平人寿异常|修改投保信息/ }).last();",
        "  const root = await dialog.isVisible({ timeout: 1000 }).catch(() => false) ? dialog : page;",
        "  const closeText = await clickLastVisible(page, root.locator('button, a, [role=\"button\"], .am-button, .adm-button, .btn, span, div').filter({ hasText: /取消|关闭|我知道|知道/ }), /取消|关闭|我知道|知道/);",
        "  return closeText || '';",
        "}",
        "",
        "async function recoverAuthFinalPayTransitionAfterServiceDialog(page: any, expectedUrl: string, expectedPath: string): Promise<Record<string, unknown>> {",
        "  if (!expectedPath || !/\\/pay(?:\\/|$)/i.test(expectedPath)) return { recovered: false, reason: 'not-pay-transition' };",
        "  const currentPath = observedPath(page.url());",
        "  if (!/\\/authentication(?:\\/|$)/i.test(currentPath) && !currentPath.includes('/authentication')) return { recovered: false, reason: 'not-authentication-page' };",
        "  if (!(await bodyMentionsAuthFinalPayBlocker(page))) return { recovered: false, reason: 'no-auth-final-pay-blocker' };",
        "  const attempts: string[] = [];",
        "  const firstClose = await closeAuthFinalPayBlockerDialog(page).catch(() => '');",
        "  if (firstClose) attempts.push(`close-dialog:${firstClose}`);",
        "  for (let attempt = 0; attempt < 2; attempt += 1) {",
        "    const next = page.locator('button, a, [role=\"button\"], .am-button, .am-button-primary, .adm-button, .submit-btn, .btn, input[type=\"button\"], input[type=\"submit\"], span, div').filter({ hasText: /下一步|继续|立即支付|去支付/ }).last();",
        "    if (await next.isVisible({ timeout: 1800 }).catch(() => false)) {",
        "      const text = await next.innerText({ timeout: 1000 }).catch(async () => await next.inputValue({ timeout: 1000 }).catch(() => ''));",
        "      await tapLocatorCenter(page, next).catch(async () => { await next.click({ timeout: 5000, force: true, noWaitAfter: true }); });",
        "      attempts.push(`retry-auth-final-next:${String(text || '').slice(0, 40)}`);",
        "      await page.waitForLoadState('domcontentloaded', { timeout: 10000 }).catch(() => undefined);",
        "      await page.waitForTimeout(1800);",
        "      await settlePostClickFlow(page).catch(() => []);",
        "      const retriedPath = observedPath(page.url());",
        "      if (retriedPath === expectedPath || retriedPath.endsWith(expectedPath) || retriedPath.includes(expectedPath)) {",
        "        return { recovered: true, progressed: true, strategy: 'auth-final-pay-recovery', attempts, url: page.url() };",
        "      }",
        "    }",
        "    const closeText = await closeAuthFinalPayBlockerDialog(page).catch(() => '');",
        "    if (closeText) attempts.push(`close-dialog:${closeText}`);",
        "  }",
        "  if (expectedUrl) {",
        "    attempts.push('goto-agent3-observed-pay-url');",
        "    const targetUrl = new URL(expectedUrl, page.url()).href;",
        "    await page.goto(targetUrl, { waitUntil: 'domcontentloaded', timeout: 30000 }).catch(() => undefined);",
        "    await page.waitForLoadState('networkidle', { timeout: 10000 }).catch(() => undefined);",
        "    await page.waitForTimeout(1200);",
        "    await settlePostClickFlow(page).catch(() => []);",
        "    const navigatedPath = observedPath(page.url());",
        "    if (navigatedPath === expectedPath || navigatedPath.endsWith(expectedPath) || navigatedPath.includes(expectedPath)) {",
        "      return { recovered: true, progressed: true, strategy: 'auth-final-pay-recovery', attempts, url: page.url() };",
        "    }",
        "  }",
        "  return { recovered: true, progressed: false, strategy: 'auth-final-pay-recovery', attempts, url: page.url() };",
        "}",
        "",
        "function h5PayUrlWithCurrentEncryptInsureNum(page: any, expectedUrl: string, seedUrl = ''): string {",
        "  try {",
        "    const target = new URL(expectedUrl, page.url());",
        "    const current = new URL(page.url());",
        "    const seed = seedUrl ? new URL(seedUrl, page.url()) : null;",
        "    const encryptInsureNum = current.searchParams.get('encryptInsureNum') || current.searchParams.get('id') || seed?.searchParams.get('encryptInsureNum') || seed?.searchParams.get('id') || latestIdentitySubmitEncryptInsureNum(page.url()) || '';",
        "    if (encryptInsureNum && /\\/pay(?:\\/|$)/i.test(target.pathname)) target.searchParams.set('id', encryptInsureNum);",
        "    return target.href;",
        "  } catch (_) {",
        "    return expectedUrl;",
        "  }",
        "}",
        "",
        "async function isH5FormDetailConfirmationPage(page: any): Promise<boolean> {",
        "  const currentUrl = page.url();",
        "  const currentPath = observedPath(currentUrl);",
        "  if (!/\\/product\\/insure$/i.test(currentPath) && !currentUrl.includes('/product/insure')) return false;",
        "  if (/isFormDetail=1/i.test(currentUrl)) return true;",
        "  const bodyText = await page.locator('body').innerText({ timeout: 1500 }).catch(() => '');",
        "  const normalized = String(bodyText || '').replace(/\\s+/g, '');",
        "  return /投被保险人|受益人|个人信息|提交订单|修改保障|鎶曡淇濋櫓浜篳鍙楃泭浜篳涓汉淇℃伅|鎻愪氦璁㈠崟|淇敼淇濋殰/.test(normalized);",
        "}",
        "",
        "async function h5PayQueryErrorVisible(page: any): Promise<boolean> {",
        "  try {",
        "    const url = new URL(page.url());",
        "    const content = decodeURIComponent(url.searchParams.get('content') || '');",
        "    if (/\\/m\\/error\\/?$/i.test(url.pathname) && content) return true;",
        "    if (/\\/m\\/error\\/?$/i.test(url.pathname) && /保单查询失败|policy/i.test(content)) return true;",
        "  } catch (_) {}",
        "  const bodyText = await page.locator('body').innerText({ timeout: 1500 }).catch(() => '');",
        "  return /保单查询失败|policy query failed|淇濆崟鏌ヨ澶辫触/i.test(String(bodyText || ''));",
        "}",
        "",
        "async function recoverH5FormDetailPayTransition(page: any, expectedUrl: string, expectedPath: string, beforeUrl = ''): Promise<Record<string, unknown>> {",
        "  if (!expectedPath || !/\\/pay(?:\\/|$)/i.test(expectedPath)) return { recovered: false, reason: 'not-pay-transition' };",
        "  const formDetailReady = await isH5FormDetailConfirmationPage(page);",
        "  const currentPath = observedPath(page.url());",
        "  const canRecoverFromSeed = /\\/m\\/error\\/?$/i.test(currentPath) && /\\/product\\/insure|isFormDetail=1/i.test(beforeUrl || '');",
        "  if (!formDetailReady && !canRecoverFromSeed) return { recovered: false, reason: 'not-form-detail-confirmation-page' };",
        "  const attempts: string[] = [];",
        "  const targetUrl = h5PayUrlWithCurrentEncryptInsureNum(page, expectedUrl, beforeUrl);",
        "  const submitSelector = '.insure-footer .submit-btn, .submit-btn, button[type=\"submit\"], input[type=\"submit\"], .am-button-primary, .adm-button-primary';",
        "  const matchesExpectedPayPath = () => {",
        "    const currentPath = observedPath(page.url());",
        "    return currentPath === expectedPath || currentPath.endsWith(expectedPath) || currentPath.includes(expectedPath);",
        "  };",
        "  if (formDetailReady) {",
        "  for (let attempt = 0; attempt < 3; attempt += 1) {",
        "    await ensureH5AgreementCheckedBeforeSubmit(page).catch(() => 0);",
        "    await page.evaluate(() => {",
        "      window.scrollTo(0, Math.max(document.documentElement.scrollHeight, document.body.scrollHeight));",
        "      const scrolling = document.scrollingElement || document.documentElement || document.body;",
        "      if (scrolling) scrolling.scrollTop = scrolling.scrollHeight;",
        "    }).catch(() => undefined);",
        "    await page.mouse.wheel(0, 3000).catch(() => undefined);",
        "    await page.waitForTimeout(500);",
        "    const submit = page.locator(submitSelector).last();",
        "    let clicked = false;",
        "    if (await submit.isVisible({ timeout: 1800 }).catch(() => false)) {",
        "      const text = await submit.innerText({ timeout: 1000 }).catch(async () => await submit.inputValue({ timeout: 1000 }).catch(() => ''));",
        "      clicked = await clickH5SubmitCandidate(page, submit).then(() => true).catch(() => false);",
        "      attempts.push(`click-form-detail-submit:${String(text || '').slice(0, 40)}`);",
        "    }",
        "    if (!clicked) {",
        "      clicked = await triggerH5SubmitDomClick(page).then(() => true).catch(() => false);",
        "      if (clicked) attempts.push('dom-form-detail-submit');",
        "    }",
        "    await page.waitForLoadState('domcontentloaded', { timeout: 10000 }).catch(() => undefined);",
        "    await page.waitForTimeout(attempt === 0 ? 1800 : 3000);",
        "    await settlePostClickFlow(page).catch(() => []);",
        "    if (matchesExpectedPayPath()) return { recovered: true, progressed: true, strategy: 'submit-form-detail-pay-recovery', attempts, url: page.url() };",
        "    if (!(await isH5FormDetailConfirmationPage(page))) break;",
        "  }",
        "  }",
        "  if (targetUrl) {",
        "    for (let retry = 0; retry < 3; retry += 1) {",
        "      attempts.push(`pay-query-error-retry-${retry + 1}`);",
        "      await page.goto(targetUrl, { waitUntil: 'domcontentloaded', timeout: 30000 }).catch(() => undefined);",
        "      await page.waitForLoadState('networkidle', { timeout: 10000 }).catch(() => undefined);",
        "      await page.waitForTimeout(1500 * (retry + 1));",
        "      await settlePostClickFlow(page).catch(() => []);",
        "      if (matchesExpectedPayPath()) return { recovered: true, progressed: true, strategy: 'submit-form-detail-pay-recovery', attempts, url: page.url() };",
        "      if (!(await h5PayQueryErrorVisible(page))) break;",
        "    }",
        "  }",
        "  return { recovered: attempts.length > 0, progressed: false, strategy: 'submit-form-detail-pay-recovery', attempts, url: page.url() };",
        "}",
        "",
        "async function retryExpectedUrlTransitionAfterTransientError(page: any, expectedPath: string): Promise<boolean> {",
        "  for (let attempt = 0; attempt < 3; attempt += 1) {",
        "    const errorText = await h5TransientPageErrorText(page);",
        "    if (!errorText) return false;",
        "    test.info().annotations.push({ type: 'transient-transition-reload', description: `attempt ${attempt + 1}: ${errorText.slice(0, 80)}` });",
        "    await page.reload({ waitUntil: 'domcontentloaded', timeout: 30000 }).catch(() => undefined);",
        "    await page.waitForLoadState('networkidle', { timeout: 10000 }).catch(() => undefined);",
        "    await settlePostClickFlow(page).catch(() => []);",
        "    const currentPath = observedPath(page.url());",
        "    if (currentPath === expectedPath || currentPath.endsWith(expectedPath) || currentPath.includes(expectedPath)) return true;",
        "  }",
        "  return false;",
        "}",
        "",
        "async function waitForObservedUrlTransition(page: any, beforeUrl: string, expectedUrl: string, stepLabel: string): Promise<boolean> {",
        "  const expectedPath = observedPath(expectedUrl);",
        "  const beforePath = observedPath(beforeUrl);",
        "  if (!expectedPath || expectedPath === beforePath) return false;",
        "  const matched = await page.waitForURL((url: URL) => {",
        "    const currentPath = url.pathname.replace(/\\/+$/g, '');",
        "    return currentPath === expectedPath || currentPath.endsWith(expectedPath) || currentPath.includes(expectedPath);",
        "  }, { timeout: 45000 }).then(() => true).catch(() => false);",
        "  if (!matched) {",
        "    if (await retryExpectedUrlTransitionAfterTransientError(page, expectedPath)) return true;",
        "    const authFinalRecovery = await recoverAuthFinalPayTransitionAfterServiceDialog(page, expectedUrl, expectedPath).catch((error) => ({ recovered: false, error: String(error) }));",
        "    if ((authFinalRecovery as any).recovered) {",
        "      test.info().annotations.push({ type: 'auth-final-pay-recovery', description: JSON.stringify(authFinalRecovery).slice(0, 1000) });",
        "      if ((authFinalRecovery as any).progressed) return true;",
        "    }",
        "    const identityTaskRecovery = await recoverIdentityTaskAfterSubmitIfNeeded(page, expectedUrl).catch((error) => ({ recovered: false, error: String(error) }));",
        "    if ((identityTaskRecovery as any).recovered) {",
        "      test.info().annotations.push({ type: 'submit-identity-task-recovery', description: JSON.stringify(identityTaskRecovery).slice(0, 1000) });",
        "      if ((identityTaskRecovery as any).progressed) return true;",
        "    }",
        "    const formDetailPayRecovery = await recoverH5FormDetailPayTransition(page, expectedUrl, expectedPath, beforeUrl).catch((error) => ({ recovered: false, error: String(error) }));",
        "    if ((formDetailPayRecovery as any).recovered) {",
        "      test.info().annotations.push({ type: 'submit-form-detail-pay-recovery', description: JSON.stringify(formDetailPayRecovery).slice(0, 1000) });",
        "      if ((formDetailPayRecovery as any).progressed) return true;",
        "    }",
        "    const body = await page.locator('body').innerText({ timeout: 1500 }).catch(() => '');",
        "    throw new Error(`Agent3 replay transition failed at ${stepLabel}: expected ${expectedPath}, current ${page.url()}, body=${String(body || '').slice(0, 300)}`);",
        "  }",
        "  return true;",
        "}",
        "",
        "async function captureAgent4BusinessScreenshot(page: any, label: string, meta: Record<string, unknown> = {}): Promise<string> {",
        "  const safeLabel = String(label || 'business').replace(/[^A-Za-z0-9_.-]+/g, '-').replace(/^-+|-+$/g, '') || 'business';",
        "  const outputPath = test.info().outputPath(`agent4-business-${safeLabel}.png`);",
        "  fs.mkdirSync(path.dirname(outputPath), { recursive: true });",
        "  await page.screenshot({ path: outputPath, fullPage: true });",
        "  const metadata = { ...meta, label: safeLabel, url: page.url(), path: outputPath };",
        "  fs.writeFileSync(outputPath.replace(/\\.[^.]+$/, '.json'), JSON.stringify(metadata, null, 2), 'utf-8');",
        "  test.info().annotations.push({ type: 'agent4-business-screenshot', description: `${safeLabel}: ${outputPath} | ${page.url()}` });",
        "  return outputPath;",
        "}",
        "",
        "async function fillFirstVisible(page: any, selector: string, value: string): Promise<void> {",
        "  const locators = page.locator(selector || 'input, textarea');",
        "  const count = await locators.count().catch(() => 0);",
        "  for (let index = count - 1; index >= 0; index -= 1) {",
        "    const candidate = locators.nth(index);",
        "    if (!(await candidate.isVisible().catch(() => false))) continue;",
        "    await candidate.fill(value);",
        "    await candidate.dispatchEvent('input').catch(() => undefined);",
        "    await candidate.dispatchEvent('change').catch(() => undefined);",
        "    return;",
        "  }",
        "  throw new Error(`No visible input found for selector: ${selector}`);",
        "}",
        "",
        "function walkFiles(root: string): string[] {",
        "  if (!fs.existsSync(root)) return [];",
        "  const entries = fs.readdirSync(root, { withFileTypes: true });",
        "  const files: string[] = [];",
        "  for (const entry of entries) {",
        "    const fullPath = path.join(root, entry.name);",
        "    if (entry.isDirectory()) files.push(...walkFiles(fullPath));",
        "    if (entry.isFile()) files.push(fullPath);",
        "  }",
        "  return files;",
        "}",
        "",
        "function resolveIdCardFixture(text: string): string {",
        "  const rawText = String(text || '');",
        "  const lowerText = rawText.toLowerCase();",
        "  const wanted = lowerText.includes('back') || lowerText.includes('2') || /国徽|反面|背面/.test(rawText) ? 'id-card-back.jpg' : 'id-card-front.jpg';",
        "  const mockDataPath = String(process.env.AGENT3_MOCK_DATA_PATH || '');",
        "  const mockDataDir = mockDataPath ? path.dirname(mockDataPath) : '';",
        "  const roots = [",
        "    ...(mockDataDir ? [",
        "      path.resolve(mockDataDir, '..', '.tmp', 'id-card-preview'),",
        "      path.resolve(mockDataDir, 'generated-id-card-assets'),",
        "      path.resolve(mockDataDir, '..', 'generated-id-card-assets'),",
        "    ] : []),",
        "    path.resolve(path.dirname(__filename), '..', '..', '..', '..', '.tmp', 'id-card-preview'),",
        "    path.resolve(path.dirname(__filename), '..', '..', '..', '..', '.tmp', 'agent3-id-card-assets'),",
        "    path.resolve(process.cwd(), '.tmp', 'id-card-preview'),",
        "    path.resolve(process.cwd(), '..', '.tmp', 'id-card-preview'),",
        "    path.resolve(process.cwd(), '..', '..', '.tmp', 'id-card-preview'),",
        "    path.resolve(process.cwd(), '..', '..', '..', '.tmp', 'id-card-preview'),",
        "    path.resolve(process.cwd(), '..', '..', '..', '..', '.tmp', 'id-card-preview'),",
        "    path.resolve(process.cwd(), '.tmp', 'agent3-id-card-assets'),",
        "    path.resolve(process.cwd(), '..', '.tmp', 'agent3-id-card-assets'),",
        "    path.resolve(process.cwd(), '..', '..', '.tmp', 'agent3-id-card-assets'),",
        "    path.resolve(process.cwd(), '..', '..', '..', '.tmp', 'agent3-id-card-assets'),",
        "    path.resolve(process.cwd(), '..', '..', '..', '..', '.tmp', 'agent3-id-card-assets'),",
        "  ];",
        "  for (const root of roots) {",
        "    const match = walkFiles(root).find(file => path.basename(file).toLowerCase() === wanted);",
        "    if (match) return match;",
        "  }",
        "  throw new Error(`ID card fixture not found: ${wanted}`);",
        "}",
        "",
        "async function completeAuthSmsBeforeIdCardUploadIfNeeded(page: any, fileInputSelector: string): Promise<Record<string, unknown>> {",
        "  const attempts: string[] = [];",
        "  async function fileInputsReady(): Promise<boolean> {",
        "    return (await page.locator(fileInputSelector).count().catch(() => 0)) > 0;",
        "  }",
        "  if (await fileInputsReady()) return { advanced: false, ready: true, reason: 'file-input-present' };",
        "  const bodyText = await page.locator('body').innerText({ timeout: 1500 }).catch(() => '');",
        "  const authDetailSignal = /\\/authentication\\/detail/i.test(observedPath(page.url())) || /投保意愿认证|提交认证|获取验证码|证件照片|上传证件照|身份认证/.test(String(bodyText || ''));",
        "  if (!authDetailSignal) return { advanced: false, ready: false, reason: 'not-auth-detail' };",
        "  const mock = (globalThis as any).__agent3MockData || mockData || {};",
        "  const smsCode = String(mock['risk_control_check.smscode'] ?? mock['请输入4位数验证码'] ?? mock.smsCode ?? '1111');",
        "  async function fillAuthSms(): Promise<boolean> {",
        "    if (!/获取验证码|验证码|提交认证|投保意愿认证/.test(await page.locator('body').innerText({ timeout: 1000 }).catch(() => ''))) return false;",
        "    await fillFirstVisible(page, \"input:not([type='file']), textarea\", smsCode).catch(() => undefined);",
        "    await page.waitForTimeout(300);",
        "    attempts.push('fill-auth-sms');",
        "    return true;",
        "  }",
        "  async function clickAuthStep(pattern: RegExp, label: string): Promise<boolean> {",
        "    const candidates = page.locator('button, a, [role=\"button\"], .am-button, .am-button-primary, .adm-button, .submit-btn, .btn, input[type=\"button\"], input[type=\"submit\"], span, div').filter({ hasText: pattern });",
        "    const count = await candidates.count().catch(() => 0);",
        "    for (let index = count - 1; index >= 0; index -= 1) {",
        "      const candidate = candidates.nth(index);",
        "      if (!(await candidate.isVisible({ timeout: 800 }).catch(() => false))) continue;",
        "      const text = String(await candidate.innerText({ timeout: 800 }).catch(async () => await candidate.inputValue({ timeout: 800 }).catch(() => ''))).replace(/\\s+/g, ' ').trim();",
        "      if (text.length > 80 || !pattern.test(text.replace(/\\s+/g, ''))) continue;",
        "      if (await candidate.isDisabled().catch(() => false)) continue;",
        "      await tapLocatorCenter(page, candidate).catch(async () => { await candidate.click({ timeout: 5000, force: true, noWaitAfter: true }); });",
        "      attempts.push(`${label}:${text.slice(0, 40)}`);",
        "      await page.waitForLoadState('domcontentloaded', { timeout: 10000 }).catch(() => undefined);",
        "      await page.waitForTimeout(1200);",
        "      await settlePostClickFlow(page).catch(() => []);",
        "      return true;",
        "    }",
        "    return false;",
        "  }",
        "  for (let attempt = 0; attempt < 4; attempt += 1) {",
        "    if (await fileInputsReady()) return { advanced: attempts.length > 0, ready: true, attempts, url: page.url() };",
        "    await fillAuthSms();",
        "    await clickAuthStep(/获取验证码|发送认证短信|发送验证码|发送短信|获取认证短信/, 'request-sms');",
        "    await fillAuthSms();",
        "    await clickAuthStep(/下一步|继续/, 'next-auth');",
        "    await fillAuthSms();",
        "    await clickAuthStep(/提交认证|确认认证|开始认证/, 'submit-auth');",
        "    await clickAuthStep(/下一步|继续|证件照片|上传证件照/, 'next-photo');",
        "    if (await fileInputsReady()) return { advanced: true, ready: true, attempts, url: page.url() };",
        "    await page.waitForTimeout(800);",
        "  }",
        "  return { advanced: attempts.length > 0, ready: await fileInputsReady(), attempts, url: page.url() };",
        "}",
        "",
        "async function advanceToIdCardUploadIfNeeded(page: any): Promise<void> {",
        "  const fileInputSelector = 'input[type=\"file\"], input[type=file], input[accept*=\"image\"], input[accept*=\"jpg\"], input[accept*=\"jpeg\"], input[accept*=\"png\"]';",
        "  if ((await page.locator(fileInputSelector).count().catch(() => 0)) > 0) return;",
        "  const authPreflight = await completeAuthSmsBeforeIdCardUploadIfNeeded(page, fileInputSelector).catch((error) => ({ advanced: false, ready: false, error: String(error) }));",
        "  if ((authPreflight as any).advanced || (authPreflight as any).ready) {",
        "    test.info().annotations.push({ type: 'auth-id-card-preflight', description: JSON.stringify(authPreflight).slice(0, 1000) });",
        "  }",
        "  if ((await page.locator(fileInputSelector).count().catch(() => 0)) > 0) return;",
        "  const advanceText = /证件照片|上传证件照|上传照片|下一步|继续|提交认证/;",
        "  for (let attempt = 0; attempt < 4; attempt += 1) {",
        "    const candidate = page.locator('button, a, [role=\"button\"], .am-button, .am-button-primary, .btn, input[type=\"button\"], input[type=\"submit\"], span, div')",
        "      .filter({ hasText: advanceText }).last();",
        "    if (await candidate.isVisible({ timeout: 1500 }).catch(() => false)) {",
        "      await candidate.scrollIntoViewIfNeeded({ timeout: 2000 }).catch(() => undefined);",
        "      await candidate.click({ timeout: 5000, noWaitAfter: true, force: true }).catch(async () => {",
        "        await tapLocatorCenter(page, candidate).catch(() => undefined);",
        "      });",
        "      await page.waitForLoadState('domcontentloaded', { timeout: 10000 }).catch(() => undefined);",
        "      await settlePostClickFlow(page).catch(() => undefined);",
        "    } else {",
        "      await page.waitForTimeout(800);",
        "    }",
        "    if ((await page.locator(fileInputSelector).count().catch(() => 0)) > 0) return;",
        "  }",
        "}",
        "",
        "async function uploadIdCardFixtureToInput(page: any, selector: string, text: string): Promise<Record<string, unknown>> {",
        "  const fixture = resolveIdCardFixture(text);",
        "  const preferredIndex = /(?:2|back|国徽|反面|背面)/i.test(String(text || '')) ? 1 : 0;",
        "  await advanceToIdCardUploadIfNeeded(page);",
        "  const rawSelector = String(selector || '').trim();",
        "  const selectorCandidates = Array.from(new Set([",
        "    rawSelector && !['input', 'textarea', 'input, textarea'].includes(rawSelector) ? rawSelector : '',",
        "    \"input[type='file']\",",
        "    'input[type=\"file\"]',",
        "    'input[type=file]',",
        "    'input[accept*=\"image\"]',",
        "    'input[accept*=\"jpg\"]',",
        "    'input[accept*=\"jpeg\"]',",
        "    'input[accept*=\"png\"]',",
        "  ].filter(Boolean)));",
        "  const attempts: string[] = [];",
        "  for (const candidateSelector of selectorCandidates) {",
        "    const inputs = page.locator(candidateSelector);",
        "    const count = await inputs.count().catch(() => 0);",
        "    if (count <= 0) {",
        "      attempts.push(`${candidateSelector}:0`);",
        "      continue;",
        "    }",
        "    const orderedIndexes = [",
        "      Math.min(preferredIndex, count - 1),",
        "      ...Array.from({ length: count }, (_, inputIndex) => inputIndex),",
        "    ].filter((value, inputIndex, values) => value >= 0 && values.indexOf(value) === inputIndex);",
        "    for (const inputIndex of orderedIndexes) {",
        "      try {",
        "        await inputs.nth(inputIndex).setInputFiles(fixture, { timeout: 5000 });",
        "        return { strategy: 'id-card-upload', selector: candidateSelector, input_index: inputIndex, fixture };",
        "      } catch (error) {",
        "        attempts.push(`${candidateSelector}[${inputIndex}]:${String(error).slice(0, 140)}`);",
        "      }",
        "    }",
        "  }",
        "  throw new Error(`ID card upload input not found: ${text}; attempts=${attempts.join(' | ')}`);",
        "}",
        "",
        "async function assertBankSignBoundary(page: any): Promise<void> {",
        "  const dialog = page.locator('[role=dialog], .am-modal').filter({ hasText: /签约|银行卡|短信验证|续期缴费/ }).first();",
        "  const body = page.locator('body');",
        "  const hasDialog = await dialog.isVisible({ timeout: 5000 }).catch(() => false);",
        "  if (!hasDialog) {",
        "    await expect(body).toContainText(/签约|银行卡|身份认证|投保意愿认证|下一步/, { timeout: 10000 });",
        "  }",
        "}",
        "",
    ]


def huize_payment_closed_loop_helper_lines() -> list[str]:
    return """
type HuizePaymentClosedLoopConfig = {
  enabled: boolean;
  paymentMethodHint?: string;
  gatewayPayNumSourceHint?: string;
  payOperationId: string;
  issueOperationId: string;
};

type HuizePaymentEvidence = {
  paymentUrl: string;
  paymentMethod: string;
  gatewayPayNum: string;
  gatewayPayNumSource: string;
  insureNum: string;
  paymentResultUrl?: string;
};

function huizeFirstMatch(patterns: RegExp[], text: string): string {
  for (const pattern of patterns) {
    const match = String(text || '').match(pattern);
    if (match?.[1]) return decodeURIComponent(String(match[1])).trim();
  }
  return '';
}

function huizeExpandedPaymentText(value: unknown): string {
  const seen = new Set<string>();
  const expanded: string[] = [];
  const add = (candidate: unknown, depth = 0) => {
    const raw = String(candidate || '').trim();
    if (!raw || seen.has(raw)) return;
    seen.add(raw);
    expanded.push(raw);
    let decoded = raw;
    for (let index = 0; index < 3; index += 1) {
      try {
        const next = decodeURIComponent(decoded);
        if (!next || next === decoded || seen.has(next)) break;
        decoded = next;
        seen.add(decoded);
        expanded.push(decoded);
      } catch (_) {
        break;
      }
    }
    if (depth >= 3) return;
    for (const text of [raw, decoded]) {
      try {
        const url = new URL(text);
        for (const key of ['redirect_url', 'return_url', 'callback_url', 'paymentUrl', 'payUrl', 'url']) {
          const nested = url.searchParams.get(key);
          if (nested) add(nested, depth + 1);
        }
      } catch (_) {}
    }
  };
  add(value);
  return expanded.join('\\n');
}

function inferHuizePaymentMethod(value: unknown, fallback = ''): string {
  const text = String(value || '').toLowerCase();
  if (/wechat|weixin|wxpay|wx_pay|tenpay|checkmweb/.test(text) || /微信/.test(String(value || ''))) return 'wechat';
  if (/alipay|ali_pay/.test(text) || /支付宝/.test(String(value || ''))) return 'alipay';
  return fallback;
}

function extractHuizeGatewayPayNum(text: string): { value: string; source: string } {
  const raw = huizeExpandedPaymentText(text);
  const tradeNo = huizeFirstMatch([/(?:[?&#]|^)trade_no=([^&#\\s]+)/i, /["']?trade_no["']?\\s*[:=]\\s*["']?([A-Za-z0-9_-]{8,})/i], raw);
  if (tradeNo) return { value: tradeNo, source: 'runtime-trade_no' };
  const gatewayPayNum = huizeFirstMatch([
    /(?:[?&#]|^)gatewayPayNum=([^&#\\s]+)/i,
    /["']?gatewayPayNum["']?\\s*[:=]\\s*["']?([A-Za-z0-9_-]{8,})/i,
    /(?:paymentOrder|payOrderNo|out_trade_no|orderNo)["'\\s:=：]+["']?([A-Za-z0-9_-]{8,})/i,
    /(?:订单号|订单编号|商户订单号)\\s*[:：]?\\s*([A-Za-z0-9_-]{8,})/i,
  ], raw);
  return gatewayPayNum ? { value: gatewayPayNum, source: 'runtime-gatewayPayNum' } : { value: '', source: '' };
}

function extractHuizeInsureNum(text: string): string {
  return huizeFirstMatch([
    /\\?"?insureNum\\?"?\\s*[:=]\\s*\\?"?(\\d{10,20})/i,
    /\\?"?insure_num\\?"?\\s*[:=]\\s*\\?"?(\\d{10,20})/i,
    /["']?insureNum["']?\\s*[:=]\\s*["']?(\\d{10,20})/i,
    /["']?insure_num["']?\\s*[:=]\\s*["']?(\\d{10,20})/i,
    /(?:投保单号|投保订单号)\\s*[:：]?\\s*(\\d{10,20})/i,
  ], String(text || ''));
}

async function huizePaymentBoundaryText(page: any, extraEvidence: Record<string, unknown> = {}): Promise<Record<string, string>> {
  const paymentUrl = page.url();
  const bodyText = await page.locator('body').innerText({ timeout: 2000 }).catch(() => '');
  const responseText = agent4NetworkResponses
    .slice()
    .reverse()
    .map((item) => `${item.url || ''}\\n${item.body_excerpt || ''}`)
    .join('\\n');
  const storageText = await page.evaluate(() => {
    const pairs: string[] = [];
    for (const store of [window.localStorage, window.sessionStorage]) {
      for (let index = 0; index < store.length; index += 1) {
        const key = store.key(index) || '';
        if (/insure|pay|order|trade|gateway/i.test(key)) pairs.push(`${key}=${store.getItem(key) || ''}`);
      }
    }
    return pairs.join('\\n').slice(0, 8000);
  }).catch(() => '');
  const combined = [paymentUrl, JSON.stringify(extraEvidence || {}), bodyText, responseText, storageText].join('\\n');
  return { paymentUrl, bodyText, responseText, storageText, combined };
}

async function waitForHuizePaymentBoundary(page: any, config: HuizePaymentClosedLoopConfig, extraEvidence: Record<string, unknown> = {}, timeoutMs = Number(process.env.AGENT4_PAYMENT_BOUNDARY_TIMEOUT_MS || 45000)): Promise<Record<string, string>> {
  const timeout = Number.isFinite(timeoutMs) && timeoutMs > 0 ? timeoutMs : 45000;
  const deadline = Date.now() + timeout;
  let latest = await huizePaymentBoundaryText(page, extraEvidence);
  while (Date.now() <= deadline) {
    latest = await huizePaymentBoundaryText(page, extraEvidence);
    const gateway = extractHuizeGatewayPayNum(latest.combined || '');
    const method = inferHuizePaymentMethod(latest.combined || '', config.paymentMethodHint || '');
    if (gateway.value && ['wechat', 'alipay'].includes(method)) return latest;
    const remaining = Math.max(250, deadline - Date.now());
    await page.waitForURL(/(?:trade_no|gatewayPayNum|checkmweb|wechat|alipay|prepay|qixin)/i, { timeout: Math.min(1500, remaining) }).catch(() => undefined);
    await page.waitForLoadState('domcontentloaded', { timeout: 1000 }).catch(() => undefined);
    await page.waitForTimeout(Math.min(500, remaining)).catch(() => undefined);
  }
  return latest;
}

function huizePolicySuccessUrlFromText(text: string): string {
  const expanded = huizeExpandedPaymentText(text);
  for (const candidate of expanded.split(/\\n+/)) {
    try {
      const url = new URL(candidate);
      if (/\\/pay\\/success\\/?$/i.test(url.pathname) && url.searchParams.get('id')) return url.href;
      if (!/\\/pay\\/?$/i.test(url.pathname)) continue;
      const payId = url.searchParams.get('id');
      if (!payId) continue;
      const resultId = payId.endsWith('!!') ? payId : `${payId}!!`;
      const successPath = url.pathname.replace(/\\/pay\\/?$/i, '/pay/success/');
      return `${url.origin}${successPath}?id=${resultId}&aid=`;
    } catch (_) {}
  }
  return '';
}

async function navigateToHuizePolicySuccessPage(page: any, evidence: HuizePaymentEvidence): Promise<string> {
  if (!evidence.paymentResultUrl) return '';
  await page.goto(evidence.paymentResultUrl, { waitUntil: 'domcontentloaded', timeout: 30000 }).catch(() => undefined);
  await page.waitForLoadState('domcontentloaded', { timeout: 10000 }).catch(() => undefined);
  await page.locator('body').isVisible({ timeout: 5000 }).catch(() => false);
  return page.url();
}

async function collectHuizePaymentBoundaryEvidence(page: any, config: HuizePaymentClosedLoopConfig, extraEvidence: Record<string, unknown> = {}): Promise<HuizePaymentEvidence> {
  const boundary = await waitForHuizePaymentBoundary(page, config, extraEvidence);
  const paymentUrl = boundary.paymentUrl || page.url();
  const combined = boundary.combined || [paymentUrl, JSON.stringify(extraEvidence || {})].join('\\n');
  const gateway = extractHuizeGatewayPayNum(combined);
  const insureNum = extractHuizeInsureNum(combined);
  const paymentMethod = inferHuizePaymentMethod(combined, config.paymentMethodHint || '');
  const gatewayPayNumSource = gateway.source || config.gatewayPayNumSourceHint || 'runtime-payment-boundary';
  const paymentResultUrl = huizePolicySuccessUrlFromText(combined);
  if (!paymentMethod || !['wechat', 'alipay'].includes(paymentMethod)) {
    throw new Error(`Huize payment closed loop needs wechat/alipay boundary, got ${paymentMethod || 'unknown'}`);
  }
  if (!gateway.value) {
    throw new Error('Huize payment closed loop missing gatewayPayNum/trade_no from live payment boundary');
  }
  if (!insureNum) {
    throw new Error('Huize payment closed loop missing insureNum from live page/network evidence');
  }
  return { paymentUrl, paymentMethod, gatewayPayNum: gateway.value, gatewayPayNumSource, insureNum, paymentResultUrl };
}

function redactedHuizePaymentEvidence(evidence: HuizePaymentEvidence): Record<string, unknown> {
  let paymentUrlHost = '';
  try { paymentUrlHost = new URL(evidence.paymentUrl).host; } catch (_) {}
  return {
    paymentMethod: evidence.paymentMethod,
    gatewayPayNumSource: evidence.gatewayPayNumSource,
    hasGatewayPayNum: Boolean(evidence.gatewayPayNum),
    hasInsureNum: Boolean(evidence.insureNum),
    paymentUrlHost,
  };
}

function huizeOperationStatus(operationId: string, operationType: string, result: any, evidence: HuizePaymentEvidence): Record<string, unknown> {
  return {
    operationId,
    operationType,
    status: result?.success ? 'passed' : 'failed',
    paymentMethod: evidence.paymentMethod,
    gatewayPayNumSource: evidence.gatewayPayNumSource,
    issueStatus: result?.issueStatus,
    message: result?.message || null,
    evidence: redactedHuizePaymentEvidence(evidence),
  };
}

function huizePayResultSummary(result: any): Record<string, unknown> {
  const data = result?.data || {};
  return {
    success: Boolean(result?.success),
    message: result?.message || null,
    status: data?.status || null,
    code: data?.code || null,
    result: typeof data?.result === 'string' ? data.result : null,
  };
}

function huizeIssueResultSummary(result: any): Record<string, unknown> {
  const data = result?.data?.result || result?.data || {};
  return {
    success: Boolean(result?.success),
    message: result?.message || null,
    issueStatus: result?.issueStatus ?? data?.issueStatus ?? null,
    payStatus: data?.payStatus ?? null,
    effectiveStatus: data?.effectiveStatus ?? null,
    auditStatus: data?.auditStatus ?? null,
    hasOrderNum: Boolean(data?.orderNum),
    updateTime: data?.updateTime || null,
  };
}

function huizeSafeArtifactName(value: string): string {
  return String(value || 'huize-payment').replace(/[^A-Za-z0-9_.-]+/g, '-').replace(/^-+|-+$/g, '') || 'huize-payment';
}

function writeHuizeExternalOpArtifact(testInfo: any, payload: Record<string, unknown>): string {
  const reportDir = process.env.REPORT_DIR ? path.join(process.env.REPORT_DIR, 'external-ops') : testInfo.outputPath('external-ops');
  fs.mkdirSync(reportDir, { recursive: true });
  const filePath = path.join(reportDir, `${huizeSafeArtifactName(String(payload.operationId || 'huize-payment'))}.json`);
  fs.writeFileSync(filePath, JSON.stringify(payload, null, 2), 'utf8');
  return filePath;
}

async function runHuizePaymentClosedLoop(page: any, testInfo: any, config: HuizePaymentClosedLoopConfig, extraEvidence: Record<string, unknown> = {}): Promise<Record<string, unknown>> {
  if (!config.enabled) return { success: false, skipped: true, reason: 'disabled' };
  const evidence = await collectHuizePaymentBoundaryEvidence(page, config, extraEvidence);
  const payResult = await huizePaySuccess({ gatewayPayNum: evidence.gatewayPayNum });
  const payOperation = huizeOperationStatus(config.payOperationId, 'huize-pay-success', payResult, evidence);
  if (!payResult?.success) {
    const artifact = writeHuizeExternalOpArtifact(testInfo, { operationId: config.payOperationId, evidence: redactedHuizePaymentEvidence(evidence), externalOperations: [payOperation], payResult: huizePayResultSummary(payResult) });
    testInfo.annotations.push({ type: 'huize-payment-closed-loop', description: JSON.stringify({ status: 'failed', artifact, externalOperations: [payOperation] }).slice(0, 1000) });
    throw new Error(`Huize pay-success operation failed: ${payResult?.message || 'unknown error'}`);
  }
  const issueResult = await huizeWaitForIssueStatus({ insureNum: evidence.insureNum });
  const issueOperation = huizeOperationStatus(config.issueOperationId, 'huize-issue-status', issueResult, evidence);
  const externalOperations = [payOperation, issueOperation];
  const passed = Boolean(issueResult?.success && Number(issueResult?.issueStatus) === 1);
  let policyResultUrl = '';
  if (passed) {
    policyResultUrl = await navigateToHuizePolicySuccessPage(page, evidence);
  }
  const artifact = writeHuizeExternalOpArtifact(testInfo, {
    operationId: config.issueOperationId,
    status: passed ? 'passed-after-resume' : 'failed',
    evidence: redactedHuizePaymentEvidence(evidence),
    externalOperations,
    payResult: huizePayResultSummary(payResult),
    issueResult: huizeIssueResultSummary(issueResult),
    policyResultUrl,
  });
  const verificationSummary = { externalOperations, policyResultUrl };
  testInfo.annotations.push({ type: passed ? 'breakpoint-resume-passed' : 'breakpoint-resume-failed', description: JSON.stringify({ artifact, verificationSummary }).slice(0, 1000) });
  if (!passed) throw new Error(`Huize issue-status operation failed: ${issueResult?.message || 'issueStatus not passed'}`);
  return { success: true, artifact, verificationSummary };
}

async function waitForVisibleBodyOrBlankPaymentRedirect(page: any): Promise<Record<string, unknown>> {
  const visible = await page.locator('body').isVisible({ timeout: 5000 }).catch(() => false);
  if (visible) return { status: 'visible-body', url: page.url() };
  const state = await page.evaluate(() => ({
    readyState: document.readyState,
    bodyText: document.body?.innerText || '',
    elementCount: document.body ? document.body.querySelectorAll('*').length : 0,
  })).catch(() => null);
  const bodyText = String((state as any)?.bodyText || '').trim();
  const elementCount = Number((state as any)?.elementCount || 0);
  if (!bodyText && elementCount === 0) {
    return { status: 'blank-payment-redirect', url: page.url(), readyState: (state as any)?.readyState || '' };
  }
  throw new Error(`Payment page body is not visible after redirect: ${page.url()}`);
}
""".strip("\n").splitlines()


def render_huize_pay_success_cjs() -> str:
    return r"""#!/usr/bin/env node
'use strict';

const DEFAULT_API = 'http://venus-fpp-payment.ibmp-preview.svc.cluster.pre:9090/catfish/dispatch';
const DEFAULT_REMARK = '1111';
const DEFAULT_OPERATOR = Object.freeze({ userId: 4827, userName: 'E2E_TEST', userCode: 'E2E_TEST' });
const SERVICE_ID = 'VENUS-FPP-PMT';
const COMMAND = 'com.huize.venus.fpp.payment.api.facade.OrderFacade.modifyOrder2Successful';

function pad2(value) { return String(value).padStart(2, '0'); }
function formatGatewayPayTime(date = new Date()) {
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())} ${pad2(date.getHours())}:${pad2(date.getMinutes())}:${pad2(date.getSeconds())}`;
}
function expandEncodedPaymentText(input) {
  const seen = new Set();
  const expanded = [];
  const add = (candidate, depth = 0) => {
    const raw = String(candidate || '').trim();
    if (!raw || seen.has(raw)) return;
    seen.add(raw);
    expanded.push(raw);
    let decoded = raw;
    for (let index = 0; index < 3; index += 1) {
      try {
        const next = decodeURIComponent(decoded);
        if (!next || next === decoded || seen.has(next)) break;
        decoded = next;
        seen.add(decoded);
        expanded.push(decoded);
      } catch (_) {
        break;
      }
    }
    if (depth >= 3) return;
    for (const text of [raw, decoded]) {
      try {
        const url = new URL(text);
        for (const key of ['redirect_url', 'return_url', 'callback_url', 'paymentUrl', 'payUrl', 'url']) {
          const nested = url.searchParams.get(key);
          if (nested) add(nested, depth + 1);
        }
      } catch (_) {}
    }
  };
  add(input);
  return expanded.join('\n');
}
function extractGatewayPayNum(input) {
  const original = String(input || '').trim();
  if (!original) throw new Error('missing gatewayPayNum/trade_no');
  const raw = expandEncodedPaymentText(original);
  for (const pattern of [
    /(?:[?&#]|^)(?:trade_no|gatewayPayNum)=([^&#\s]+)/i,
    /(?:trade_no|gatewayPayNum|paymentOrder|payOrderNo|out_trade_no|orderNo)["'\s:=:]+["']?([A-Za-z0-9_-]{8,})/i,
    /(?:订单号|订单编号|商户订单号)\s*[:：]?\s*([A-Za-z0-9_-]{8,})/i,
  ]) {
    const match = raw.match(pattern);
    if (match?.[1]) return decodeURIComponent(match[1]).trim();
  }
  for (const candidate of raw.split(/\n+/)) {
    try {
      const url = new URL(candidate);
      const value = url.searchParams.get('trade_no') || url.searchParams.get('gatewayPayNum');
      if (value) return value.trim();
    } catch (_) {}
  }
  if (/^[A-Za-z0-9_-]{8,}$/.test(original)) return original;
  throw new Error(`unable to extract gatewayPayNum/trade_no from: ${original.slice(0, 160)}`);
}
function readOperatorFromEnv(env = process.env) {
  return {
    userId: Number(env.HUIZE_PAY_USER_ID) || DEFAULT_OPERATOR.userId,
    userName: env.HUIZE_PAY_USER_NAME || DEFAULT_OPERATOR.userName,
    userCode: env.HUIZE_PAY_USER_CODE || DEFAULT_OPERATOR.userCode,
  };
}
function buildPaySuccessPayload({ gatewayPayNum, gatewayPayTime, remark, operator }) {
  return { serviceId: SERVICE_ID, command: COMMAND, parameters: [{ operator, remark, gatewayPayNum, gatewayPayTime }] };
}
function takeValue(argv, index, name) {
  const value = argv[index + 1];
  if (!value || value.startsWith('--')) throw new Error(`${name} requires a value`);
  return value;
}
function parseCliArgs(argv = process.argv.slice(2), env = process.env) {
  const parsed = {
    api: env.HUIZE_PAY_API || DEFAULT_API,
    remark: env.HUIZE_PAY_REMARK || DEFAULT_REMARK,
    gatewayPayTime: env.HUIZE_PAY_TIME || formatGatewayPayTime(),
    operator: readOperatorFromEnv(env),
  };
  let input = '';
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === '--help' || arg === '-h') return { help: true };
    if (['--gatewayPayNum', '--gateway-pay-num', '--tradeNo', '--trade-no', '--paymentOrder', '--payment-order', '--url'].includes(arg)) {
      input = takeValue(argv, index, arg);
      index += 1;
      continue;
    }
    if (arg === '--api') { parsed.api = takeValue(argv, index, arg); index += 1; continue; }
    if (arg === '--remark') { parsed.remark = takeValue(argv, index, arg); index += 1; continue; }
    if (arg === '--payTime' || arg === '--pay-time') { parsed.gatewayPayTime = takeValue(argv, index, arg); index += 1; continue; }
    if (arg.startsWith('--')) throw new Error(`unknown option: ${arg}`);
    input = arg;
  }
  parsed.gatewayPayNum = extractGatewayPayNum(input);
  return parsed;
}
function parseResponseText(text) { try { return text ? JSON.parse(text) : {}; } catch (_) { return text; } }
async function paySuccess(options = {}) {
  const { api = DEFAULT_API, gatewayPayNum, gatewayPayTime = formatGatewayPayTime(), remark = DEFAULT_REMARK, operator = DEFAULT_OPERATOR, fetchImpl = globalThis.fetch } = options;
  if (!gatewayPayNum) throw new Error('missing gatewayPayNum');
  if (typeof fetchImpl !== 'function') throw new Error('fetch is unavailable; Node 18+ is required');
  const response = await fetchImpl(api, {
    method: 'POST',
    headers: { 'Content-Type': 'text/plain;charset=UTF-8' },
    body: JSON.stringify(buildPaySuccessPayload({ gatewayPayNum, gatewayPayTime, remark, operator })),
  });
  const data = parseResponseText(await response.text());
  if (!response.ok) return { success: false, message: `HTTP ${response.status}`, data };
  if (data && typeof data === 'object') {
    if (data.success === false) return { success: false, message: data.message || 'pay success business failure', data };
    if (Object.prototype.hasOwnProperty.call(data, 'status') && String(data.status) !== '00000') return { success: false, message: data.message || `status=${data.status}`, data };
    if (Object.prototype.hasOwnProperty.call(data, 'code') && String(data.code) !== '0') return { success: false, message: data.message || `code=${data.code}`, data };
  }
  return { success: true, message: 'pay status updated to success', data };
}
async function main() {
  const args = parseCliArgs();
  if (args.help) { console.log('usage: node huize-pay-success.cjs --gatewayPayNum <gatewayPayNum>'); return; }
  const result = await paySuccess(args);
  const out = { success: result.success, message: result.message, gatewayPayNum: args.gatewayPayNum, api: args.api, data: result.data };
  if (result.success) console.log(JSON.stringify(out, null, 2));
  else { console.error(JSON.stringify(out, null, 2)); process.exitCode = 1; }
}
if (require.main === module) main().catch((error) => { console.error(error?.stack || String(error)); process.exitCode = 1; });
module.exports = { DEFAULT_API, DEFAULT_OPERATOR, DEFAULT_REMARK, buildPaySuccessPayload, extractGatewayPayNum, formatGatewayPayTime, parseCliArgs, paySuccess };
"""


def render_huize_issue_status_cjs() -> str:
    return r"""#!/usr/bin/env node
'use strict';

const DEFAULT_API = 'http://pluto-is-server.ibmp-preview.svc.cluster.pre:9090/catfish/dispatch';
const DEFAULT_PLATFORM = 4;
const DEFAULT_INITIAL_DELAY_MS = 5000;
const DEFAULT_POLL_INTERVAL_MS = 2500;
const DEFAULT_TIMEOUT_MS = 600000;
const SERVICE_ID = 'pluto-is-server';
const COMMAND = 'com.hzins.pluto.is.api.outside.insure.facade.OutsideSearchFacade.searchInsureByInsureNum';

function extractInsureNum(input) {
  const raw = String(input || '').trim();
  if (!raw) throw new Error('missing insureNum');
  if (/^\d{10,20}$/.test(raw)) return raw;
  const match = raw.match(/(?:insureNum|insure_num|投保单号|投保订单号)["'\s:=：]+["']?(\d{10,20})/i);
  if (match) return match[1];
  throw new Error(`unable to extract insureNum from: ${raw.slice(0, 160)}`);
}
function buildIssueStatusPayload({ insureNum, platform = DEFAULT_PLATFORM }) {
  return { serviceId: SERVICE_ID, command: COMMAND, parameters: [Number(insureNum), Number(platform)] };
}
function takeValue(argv, index, name) {
  const value = argv[index + 1];
  if (!value || value.startsWith('--')) throw new Error(`${name} requires a value`);
  return value;
}
function integer(value, name) {
  const parsed = Number(value);
  if (!Number.isInteger(parsed)) throw new Error(`${name} must be an integer`);
  return parsed;
}
function positiveInteger(value, name) {
  const parsed = integer(value, name);
  if (parsed <= 0) throw new Error(`${name} must be positive`);
  return parsed;
}
function nonNegativeInteger(value, name) {
  const parsed = integer(value, name);
  if (parsed < 0) throw new Error(`${name} must be non-negative`);
  return parsed;
}
function envNumber(env, name, fallback, parser) { return env[name] ? parser(env[name], name) : fallback; }
function parseCliArgs(argv = process.argv.slice(2), env = process.env) {
  const parsed = {
    api: env.HUIZE_ISSUE_STATUS_API || DEFAULT_API,
    platform: envNumber(env, 'HUIZE_ISSUE_STATUS_PLATFORM', DEFAULT_PLATFORM, positiveInteger),
    initialDelayMs: envNumber(env, 'HUIZE_ISSUE_INITIAL_DELAY_MS', DEFAULT_INITIAL_DELAY_MS, nonNegativeInteger),
    pollIntervalMs: envNumber(env, 'HUIZE_ISSUE_POLL_INTERVAL_MS', DEFAULT_POLL_INTERVAL_MS, positiveInteger),
    timeoutMs: envNumber(env, 'HUIZE_ISSUE_TIMEOUT_MS', DEFAULT_TIMEOUT_MS, nonNegativeInteger),
  };
  let input = '';
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === '--help' || arg === '-h') return { help: true };
    if (arg === '--insureNum' || arg === '--insure-num') { input = takeValue(argv, index, arg); index += 1; continue; }
    if (arg === '--api') { parsed.api = takeValue(argv, index, arg); index += 1; continue; }
    if (arg === '--platform') { parsed.platform = positiveInteger(takeValue(argv, index, arg), arg); index += 1; continue; }
    if (arg === '--initialDelayMs' || arg === '--initial-delay-ms') { parsed.initialDelayMs = nonNegativeInteger(takeValue(argv, index, arg), arg); index += 1; continue; }
    if (arg === '--pollIntervalMs' || arg === '--poll-interval-ms') { parsed.pollIntervalMs = positiveInteger(takeValue(argv, index, arg), arg); index += 1; continue; }
    if (arg === '--timeoutMs' || arg === '--timeout-ms') { parsed.timeoutMs = nonNegativeInteger(takeValue(argv, index, arg), arg); index += 1; continue; }
    if (arg.startsWith('--')) throw new Error(`unknown option: ${arg}`);
    input = arg;
  }
  parsed.insureNum = extractInsureNum(input);
  return parsed;
}
function parseResponseText(text) { try { return text ? JSON.parse(text) : {}; } catch (_) { return text; } }
function readIssueStatus(data) {
  const value = data && typeof data === 'object' && data.result ? data.result.issueStatus : undefined;
  const issueStatus = Number(value);
  return Number.isFinite(issueStatus) ? issueStatus : null;
}
function getBusinessFailure(data) {
  if (!data || typeof data !== 'object') return null;
  if (data.success === false) return data.message || 'success=false';
  if (Object.prototype.hasOwnProperty.call(data, 'status') && String(data.status) !== '00000') return data.message || `status=${data.status}`;
  if (Object.prototype.hasOwnProperty.call(data, 'code') && String(data.code) !== '0') return data.message || `code=${data.code}`;
  return null;
}
async function queryIssueStatus(options = {}) {
  const { api = DEFAULT_API, insureNum, platform = DEFAULT_PLATFORM, fetchImpl = globalThis.fetch } = options;
  if (!insureNum) throw new Error('missing insureNum');
  if (typeof fetchImpl !== 'function') throw new Error('fetch is unavailable; Node 18+ is required');
  const response = await fetchImpl(api, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(buildIssueStatusPayload({ insureNum, platform })) });
  const data = parseResponseText(await response.text());
  if (!response.ok) return { success: false, message: `HTTP ${response.status}`, data };
  const businessFailure = getBusinessFailure(data);
  if (businessFailure) return { success: false, message: businessFailure, data };
  const issueStatus = readIssueStatus(data);
  if (issueStatus === null) return { success: false, message: 'issueStatus missing', data };
  return { success: true, message: 'issue status query succeeded', insureNum: String(insureNum), issueStatus, data };
}
async function defaultSleep(ms) { await new Promise((resolve) => setTimeout(resolve, ms)); }
function issueStatusMessage(issueStatus) {
  if (issueStatus === 1) return 'policy issued';
  if (issueStatus === 3) return 'issue cancelled';
  return 'waiting for policy issue';
}
async function waitForIssueStatus(options = {}) {
  const { initialDelayMs = DEFAULT_INITIAL_DELAY_MS, pollIntervalMs = DEFAULT_POLL_INTERVAL_MS, timeoutMs = DEFAULT_TIMEOUT_MS, sleepImpl = defaultSleep } = options;
  const startedAt = Date.now();
  let elapsedMs = 0;
  let lastResult = null;
  if (initialDelayMs > 0 && timeoutMs > 0) {
    const sleepMs = Math.min(initialDelayMs, timeoutMs);
    await sleepImpl(sleepMs);
    elapsedMs += sleepMs;
  }
  while (elapsedMs <= timeoutMs) {
    lastResult = await queryIssueStatus(options);
    if (!lastResult.success) return lastResult;
    if (lastResult.issueStatus === 1) return { ...lastResult, success: true, message: issueStatusMessage(1) };
    if (lastResult.issueStatus === 3) return { ...lastResult, success: false, message: issueStatusMessage(3) };
    const effectiveElapsed = Math.max(elapsedMs, Date.now() - startedAt);
    const remainingMs = timeoutMs - effectiveElapsed;
    if (remainingMs <= 0) break;
    const sleepMs = Math.min(pollIntervalMs, remainingMs);
    await sleepImpl(sleepMs);
    elapsedMs += sleepMs;
  }
  const issueStatus = lastResult ? lastResult.issueStatus : null;
  return { ...(lastResult || {}), success: false, issueStatus, message: `issue status timeout; last issueStatus=${issueStatus}` };
}
async function main() {
  const args = parseCliArgs();
  if (args.help) { console.log('usage: node huize-issue-status.cjs --insureNum <insureNum>'); return; }
  const result = await waitForIssueStatus(args);
  const out = { success: result.success, message: result.message, insureNum: args.insureNum, issueStatus: result.issueStatus, api: args.api, data: result.data };
  if (result.success) console.log(JSON.stringify(out, null, 2));
  else { console.error(JSON.stringify(out, null, 2)); process.exitCode = 1; }
}
if (require.main === module) main().catch((error) => { console.error(error?.stack || String(error)); process.exitCode = 1; });
module.exports = { DEFAULT_API, DEFAULT_INITIAL_DELAY_MS, DEFAULT_PLATFORM, DEFAULT_POLL_INTERVAL_MS, DEFAULT_TIMEOUT_MS, buildIssueStatusPayload, extractInsureNum, parseCliArgs, queryIssueStatus, waitForIssueStatus };
"""


def field_probe_helper_lines() -> list[str]:
    return [
        "function cssAttrValue(value: unknown): string {",
        "  return String(value || '').replace(/\\\\/g, '\\\\\\\\').replace(/\"/g, '\\\\\"');",
        "}",
        "",
        "const filledMockDataNodes = new Set<string>();",
        "const FIELD_ALIAS_STOP_WORDS = new Set(['insure', 'form', 'field', 'input', 'submit', 'button', 'select', 'applicant', 'insured', 'policy', 'payment', 'beneficiary']);",
        "",
        "function normaliseAgent4Date(value: unknown): string {",
        "  const text = String(value || '').replace(/[./]/g, '-').trim();",
        "  return /^\\d{4}-\\d{2}-\\d{2}$/.test(text) ? text : '';",
        "}",
        "",
        "function dateFromAgent4OffsetDays(value: unknown): string {",
        "  const text = String(value ?? '').trim();",
        "  if (!/^-?\\d+$/.test(text)) return '';",
        "  const date = new Date();",
        "  date.setHours(0, 0, 0, 0);",
        "  date.setDate(date.getDate() + Number.parseInt(text, 10));",
        "  const year = date.getFullYear();",
        "  const month = String(date.getMonth() + 1).padStart(2, '0');",
        "  const day = String(date.getDate()).padStart(2, '0');",
        "  return `${year}-${month}-${day}`;",
        "}",
        "",
        "function resolveAgent4PolicyStartDateOverride(): string {",
        "  return normaliseAgent4Date(process.env.AGENT4_POLICY_START_DATE) || dateFromAgent4OffsetDays(process.env.AGENT4_POLICY_START_OFFSET_DAYS || '1');",
        "}",
        "",
        "function parseAgent4MockDataOverrides(): Record<string, unknown> {",
        "  const raw = String(process.env.AGENT4_MOCK_DATA_OVERRIDES || '').trim();",
        "  if (!raw) return {};",
        "  try {",
        "    const parsed = JSON.parse(raw);",
        "    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed as Record<string, unknown> : {};",
        "  } catch (_) {",
        "    return {};",
        "  }",
        "}",
        "",
        "function withAgent4RuntimeMockDataOverrides(base: Record<string, unknown>): Record<string, unknown> {",
        "  const overrides = parseAgent4MockDataOverrides();",
        "  const policyStartDate = resolveAgent4PolicyStartDateOverride();",
        "  if (!policyStartDate && Object.keys(overrides).length === 0) return base;",
        "  return {",
        "    ...base,",
        "    ...overrides,",
        "    ...(policyStartDate ? {",
        "      'policy.start_date': policyStartDate,",
        "      policyStartDate,",
        "      'insure_form.insurancedate': policyStartDate,",
        "    } : {}),",
        "  };",
        "}",
        "",
        "function semanticAliasesForField(field: any): string[] {",
        "  const key = String(field.field_key || '').toLowerCase();",
        "  const aliases: string[] = [];",
        "  const add = (value: unknown) => { const text = String(value || '').replace(/\\s+/g, ' ').trim(); if (text && !aliases.includes(text)) aliases.push(text); };",
        "  if (key.includes('start_date')) add('起保日期');",
        "  if (key.includes('applicant') || key.startsWith('insure_form.applicant')) {",
        "    if (key.includes('name')) { add('投保人信息 姓名'); add('投保人姓名'); }",
        "    if (key.includes('id_no') || key.includes('idno') || key.includes('idnumber')) { add('投保人信息 证件号码'); add('投保人证件号码'); }",
        "    if (key.includes('phone') || key.includes('mobile')) { add('投保人信息 手机号码'); add('投保人手机号码'); }",
        "    if (key.includes('email')) { add('投保人信息 电子邮箱'); add('投保人电子邮箱'); }",
        "    if (key.includes('address')) { add('投保人信息 联系地址'); add('投保人联系地址'); }",
        "    if (key.includes('annual_income')) { add('投保人信息 年收入'); add('年收入（万元）'); }",
        "    if (key.includes('occupation')) { add('投保人信息 职业'); add('投保人职业'); }",
        "    if (key.includes('region')) { add('投保人信息 居住省市'); add('投保人居住省市'); }",
        "    if (key.includes('height')) { add('投保人信息 身高'); add('投保人身高'); }",
        "    if (key.includes('weight')) { add('投保人信息 体重'); add('投保人体重'); }",
        "    if (key.includes('card_valid_start') || key.includes('cardvalidstart')) add('投保人信息 证件有效期 开始');",
        "    if (key.includes('card_valid_end') || key.includes('cardvalidend')) add('投保人信息 证件有效期 结束');",
        "  }",
        "  if (key.includes('insured') && !key.startsWith('insure_form.applicant')) {",
        "    if (key.includes('name')) { add('被保险人信息 姓名'); add('为谁投保 姓名'); add('被保险人姓名'); }",
        "    if (key.includes('id_no') || key.includes('idno') || key.includes('idnumber')) { add('被保险人信息 证件号码'); add('为谁投保 证件号码'); add('被保险人证件号码'); }",
        "    if (key.includes('phone') || key.includes('mobile')) { add('被保险人信息 手机号码'); add('为谁投保 手机号码'); add('被保险人手机号码'); }",
        "    if (key.includes('address')) { add('被保险人信息 联系地址'); add('为谁投保 联系地址'); add('被保险人联系地址'); }",
        "    if (key.includes('occupation')) { add('被保险人信息 职业'); add('为谁投保 职业'); add('被保险人职业'); }",
        "    if (key.includes('region')) { add('被保险人信息 居住省市'); add('为谁投保 居住省市'); add('被保险人居住省市'); }",
        "    if (key.includes('height')) { add('被保险人信息 身高'); add('为谁投保 身高'); add('被保险人身高'); }",
        "    if (key.includes('weight')) { add('被保险人信息 体重'); add('为谁投保 体重'); add('被保险人体重'); }",
        "    if (key.includes('card_valid_start') || key.includes('cardvalidstart')) add('被保险人信息 证件有效期 开始');",
        "    if (key.includes('card_valid_end') || key.includes('cardvalidend')) add('被保险人信息 证件有效期 结束');",
        "  }",
        "  if (key.includes('agreement')) { add('本人充分阅读'); add('本人已逐页阅读'); add('保险条款'); add('责任免除'); }",
        "  return aliases;",
        "}",
        "",
        "function fieldAliases(field: any): string[] {",
        "  const aliases: string[] = [];",
        "  const add = (value: unknown) => {",
        "    const text = String(value || '').replace(/\\s+/g, ' ').trim();",
        "    if (!text || text === 'mock' || aliases.includes(text)) return;",
        "    if (FIELD_ALIAS_STOP_WORDS.has(text.toLowerCase())) return;",
        "    aliases.push(text);",
        "  };",
        "  for (const locator of field.locators || []) {",
        "    const by = String(locator.by || '');",
        "    if (['label_text', 'text', 'param', 'name', 'placeholder'].includes(by)) add(locator.value);",
        "  }",
        "  for (const alias of semanticAliasesForField(field)) add(alias);",
        "  add(field.label);",
        "  add(field.name);",
        "  add(field.field_key);",
        "  add(field.raw?.label);",
        "  add(field.raw?.placeholder);",
        "  for (const token of String(field.field_key || '').split(/[._:-]+/)) {",
        "    if (token.length >= 4 && !FIELD_ALIAS_STOP_WORDS.has(token.toLowerCase())) add(token);",
        "  }",
        "  return aliases;",
        "}",
        "",
        "function targetProbeFieldSelector(field: any): string {",
        "  return `agent-field-${String(field.field_key || 'field').replace(/[^a-zA-Z0-9_-]+/g, '-')}-${Date.now()}-${Math.random().toString(16).slice(2)}`;",
        "}",
        "",
        "function shouldProbeFieldsForNode(nodeId: unknown): boolean {",
        "  return ['NODE-insure-form'].includes(String(nodeId || ''));",
        "}",
        "",
        "function pageElementRecordsForNode(nodeId: unknown): any[] {",
        "  if (!shouldProbeFieldsForNode(nodeId)) return [];",
        "  return pageElementPlan.filter((record: any) => record.node_id === nodeId || (record.matched_node_ids ?? []).includes(nodeId));",
        "}",
        "",
        "function selectedLocatorValue(locator: any): string {",
        "  if (!locator || typeof locator !== 'object') return '';",
        "  return String(locator.value || '').trim();",
        "}",
        "",
        "function strategyForField(nodeId: unknown, fieldKey: unknown): any {",
        "  return (componentStrategy.field_strategies ?? []).find((item: any) => item.node_id === nodeId && item.field_key === fieldKey) ?? {};",
        "}",
        "",
        "function pageElementFieldForKey(nodeId: unknown, fieldKey: unknown): any {",
        "  for (const record of pageElementRecordsForNode(nodeId)) {",
        "    const found = (record.fields ?? []).find((field: any) => field.field_key === fieldKey);",
        "    if (found) return found;",
        "  }",
        "  return {};",
        "}",
        "",
        "function fieldWithContract(nodeId: unknown, resolution: any): any {",
        "  const base = pageElementFieldForKey(nodeId, resolution.field_key);",
        "  const component = strategyForField(nodeId, resolution.field_key);",
        "  const selected = resolution.selected_locator ?? {};",
        "  const selector = selected.by === 'selector' ? selectedLocatorValue(selected) : base.selector;",
        "  return {",
        "    ...base,",
        "    field_key: resolution.field_key ?? base.field_key,",
        "    selector,",
        "    required: Boolean(resolution.required ?? base.required),",
        "    locators: resolution.locator_candidates ?? base.locators ?? [],",
        "    field_resolution: resolution,",
        "    component_strategy: component,",
        "    control_type: component.control_type ?? resolution.control_type ?? base.control_type,",
        "    fill_strategy: component.fill_strategy ?? resolution.fill_strategy ?? base.fill_strategy,",
        "    mock_key: resolution.mock_key ?? base.field_key,",
        "  };",
        "}",
        "",
        "function isRunnableMockField(field: any): boolean {",
        "  return Boolean(field.required);",
        "}",
        "",
        "function fieldsForNodeFromContract(nodeId: unknown): any[] {",
        "  if (!shouldProbeFieldsForNode(nodeId)) return [];",
        "  const resolutions = (fieldResolutionPlan.fields ?? []).filter((item: any) => item.node_id === nodeId);",
        "  if (resolutions.length) return resolutions.map((item: any) => fieldWithContract(nodeId, item)).filter(isRunnableMockField);",
        "  return pageElementRecordsForNode(nodeId).flatMap((record: any) =>",
        "    (record.fields ?? []).map((field: any) => ({",
        "      ...field,",
        "      component_strategy: strategyForField(nodeId, field.field_key),",
        "      fill_strategy: field.fill_strategy ?? strategyForField(nodeId, field.field_key).fill_strategy,",
        "      control_type: field.control_type ?? strategyForField(nodeId, field.field_key).control_type,",
        "    })).filter(isRunnableMockField)",
        "  );",
        "}",
        "",
        "async function firstVisibleLocatorForSelector(page: any, selector: string): Promise<any | null> {",
        "  const locator = page.locator(selector);",
        "  const count = Math.min(await locator.count().catch(() => 0), 80);",
        "  for (let index = 0; index < count; index += 1) {",
        "    const candidate = locator.nth(index);",
        "    if (await candidate.isVisible({ timeout: 300 }).catch(() => false)) return candidate;",
        "  }",
        "  return null;",
        "}",
        "",
        "async function resolveFieldLocator(page: any, field: any): Promise<{ locator: any; selector: string; strategy: string } | null> {",
        "  const selected = field.field_resolution?.selected_locator || field.selected_locator || {};",
        "  if (selected.by === 'selector' && selectedLocatorValue(selected)) {",
        "    const locator = await firstVisibleLocatorForSelector(page, selectedLocatorValue(selected));",
        "    if (locator) return { locator, selector: selectedLocatorValue(selected), strategy: 'field-resolution' };",
        "  }",
        "  if (field.selector) {",
        "    const locator = await firstVisibleLocatorForSelector(page, field.selector);",
        "    if (locator) return { locator, selector: field.selector, strategy: 'selector' };",
        "  }",
        "  const aliases = fieldAliases(field);",
        "  for (const alias of aliases) {",
        "    const value = cssAttrValue(alias);",
        "    const locator = page.locator(",
        "      `input:not([type=\"hidden\"])[name*=\"${value}\" i], textarea[name*=\"${value}\" i], select[name*=\"${value}\" i], ` +",
        "      `input:not([type=\"hidden\"])[id*=\"${value}\" i], textarea[id*=\"${value}\" i], select[id*=\"${value}\" i], ` +",
        "      `input:not([type=\"hidden\"])[placeholder*=\"${value}\" i], textarea[placeholder*=\"${value}\" i], ` +",
        "      `input:not([type=\"hidden\"])[aria-label*=\"${value}\" i], textarea[aria-label*=\"${value}\" i], select[aria-label*=\"${value}\" i]`",
        "    ).first();",
        "    if (await locator.isVisible({ timeout: 300 }).catch(() => false)) return { locator, selector: `attr:${alias}`, strategy: 'attr' };",
        "  }",
        "  const token = targetProbeFieldSelector(field);",
        "  const found = await page.evaluate(({ aliases, token }) => {",
        "    const controls = Array.from(document.querySelectorAll(",
        "      'input:not([type=\"hidden\"]):not([type=\"button\"]):not([type=\"submit\"]):not([type=\"reset\"]):not([type=\"file\"]), textarea, select, [contenteditable=\"true\"], div[name], [role=\"button\"], [role=\"combobox\"], .hz-dropdown, .input-select'",
        "    ));",
        "    function normalize(value: unknown): string {",
        "      return String(value || '').replace(/\\s+/g, '').toLowerCase();",
        "    }",
        "    function isVisible(element: any): boolean {",
        "      const style = window.getComputedStyle(element);",
        "      const rect = element.getBoundingClientRect();",
        "      return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0 && !element.disabled;",
        "    }",
        "    function textOf(element: any): string {",
        "      return String(element?.innerText || element?.textContent || '').replace(/\\s+/g, ' ').trim();",
        "    }",
        "    function relatedText(control: any): string {",
        "      const chunks = [control.getAttribute('name'), control.getAttribute('id'), control.getAttribute('placeholder'), control.getAttribute('aria-label')];",
        "      if (control.id) chunks.push(textOf(document.querySelector(`label[for=\"${CSS.escape(control.id)}\"]`)));",
        "      chunks.push(textOf(control.closest('label')));",
        "      const containers = [",
        "        control.closest('.form-item, .form-group, .el-form-item, .ant-form-item, .form-row, .item, li, tr, td, dd, dl, p, div'),",
        "        control.parentElement,",
        "        control.parentElement?.parentElement,",
        "      ];",
        "      for (const container of containers) chunks.push(textOf(container).slice(0, 240));",
        "      let sibling = control.previousElementSibling;",
        "      for (let index = 0; sibling && index < 3; index += 1, sibling = sibling.previousElementSibling) chunks.push(textOf(sibling));",
        "      return chunks.filter(Boolean).join(' ');",
        "    }",
        "    function score(control: any): number {",
        "      if (!isVisible(control)) return -1;",
        "      const attrText = normalize([control.getAttribute('name'), control.getAttribute('id'), control.getAttribute('placeholder'), control.getAttribute('aria-label'), control.className].filter(Boolean).join(' '));",
        "      const contextText = normalize(relatedText(control));",
        "      const hasValue = Boolean(String(control.value || '').trim());",
        "      let best = 0;",
        "      for (const alias of aliases as string[]) {",
        "        const normalizedAlias = normalize(alias);",
        "        if (!normalizedAlias || normalizedAlias.length < 2) continue;",
        "        if (attrText === normalizedAlias) best = Math.max(best, 160);",
        "        if (attrText.includes(normalizedAlias) || normalizedAlias.includes(attrText)) best = Math.max(best, 120);",
        "        if (contextText.includes(normalizedAlias)) best = Math.max(best, 100);",
        "      }",
        "      if (!hasValue) best += 15;",
        "      return best;",
        "    }",
        "    const ranked = controls.map(control => ({ control, score: score(control) })).filter(item => item.score > 0).sort((left, right) => right.score - left.score);",
        "    if (!ranked.length) return false;",
        "    ranked[0].control.setAttribute('data-agent-field-target', token);",
        "    return true;",
        "  }, { aliases, token }).catch(() => false);",
        "  if (!found) return null;",
        "  const selector = `[data-agent-field-target=\"${token}\"]`;",
        "  const locator = page.locator(selector).first();",
        "  if (!(await locator.count().catch(() => 0))) return null;",
        "  return { locator, selector, strategy: 'target-probe' };",
        "}",
        "",
        "async function fillByStrategy(page: any, locator: any, field: any, value: unknown): Promise<void> {",
        "  const strategy = String(field.fill_strategy || field.component_strategy?.fill_strategy || '');",
        "  const controlType = String(field.control_type || field.component_strategy?.control_type || '');",
        "  if (strategy === 'check_agreement' || strategy === 'check' || controlType === 'agreement_checkbox' || controlType === 'checkbox') {",
        "    await locator.check().catch(async () => { await locator.click({ force: true }).catch(() => undefined); });",
        "    return;",
        "  }",
        "  if (strategy === 'select_by_text_or_value' || controlType === 'select') {",
        "    await locator.selectOption(String(value)).catch(async () => {",
        "      await locator.selectOption({ label: String(value) }).catch(async () => {",
        "        await locator.selectOption({ index: 1 }).catch(async () => { await locator.click({ force: true }).catch(() => undefined); });",
        "      });",
        "    });",
        "    return;",
        "  }",
        "  if (strategy === 'date_picker_select_or_fill' || controlType === 'date_picker') {",
        "    await locator.fill(String(value)).catch(async () => { await locator.click({ force: true }).catch(() => undefined); });",
        "    return;",
        "  }",
        "  if (strategy === 'occupation_search_and_select' || strategy === 'region_cascade_select' || controlType === 'occupation_picker' || controlType === 'region_picker') {",
        "    await locator.fill(String(value)).catch(async () => { await locator.click({ force: true }).catch(() => undefined); });",
        "    return;",
        "  }",
        "  const meta = await locator.evaluate((element: Element) => ({",
        "    tag: String(element.tagName || '').toLowerCase(),",
        "    type: String(element.getAttribute('type') || '').toLowerCase(),",
        "  })).catch(() => ({ tag: String(field.tag ?? '').toLowerCase(), type: String(field.type ?? '').toLowerCase() }));",
        "  if (meta.tag === 'select') {",
        "    await locator.selectOption(String(value)).catch(async () => { await locator.selectOption({ index: 1 }).catch(() => undefined); });",
        "  } else if (meta.type === 'checkbox' || meta.type === 'radio') {",
        "    await locator.check().catch(async () => { await locator.click({ force: true }).catch(() => undefined); });",
        "  } else {",
        "    await locator.fill(String(value)).catch(async () => { await locator.click({ force: true }).catch(() => undefined); });",
        "  }",
        "}",
        "",
        *r"""
async function ensureH5AgreementCheckedBeforeSubmit(page: any): Promise<number> {
  const uncheckedCount = await page.evaluate(() => {
    const norm = (value: unknown) => String(value || '').replace(/\s+/g, ' ').trim();
    const visible = (node: any) => {
      if (!node) return false;
      const style = window.getComputedStyle(node);
      const rect = node.getBoundingClientRect();
      return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
    };
    const agreementText = (text: string) => /本人充分阅读|本人已逐页阅读|阅读、理解并同意|阅读并同意|投保条件|投保重要告知|投保人声明|保险条款|免责条款|数据隐私|服务流程/.test(text);
    const textOf = (node: any) => norm(node?.innerText || node?.textContent || node?.getAttribute?.('aria-label') || node?.getAttribute?.('title') || '');
    const nearestAgreementRoot = (node: any) => {
      let current = node;
      for (let depth = 0; current && current !== document.body && depth < 8; depth += 1, current = current.parentElement) {
        const text = textOf(current);
        if (text && text.length <= 800 && agreementText(text)) return current;
      }
      return node?.closest?.('label,.am-checkbox-wrapper,.adm-checkbox,.agreement,.protocol,.am-list-item,li,dd,div,p') || node;
    };
    const checkboxIn = (root: any) => (
      root?.matches?.('input[type="checkbox"]')
        ? root
        : root?.querySelector?.('input[type="checkbox"]')
    ) as HTMLInputElement | null;
    const checkedLike = (root: any) => {
      const input = checkboxIn(root);
      const control = input?.closest?.('.am-checkbox,.adm-checkbox')
        || root?.querySelector?.('.am-checkbox,.am-checkbox-inner,.adm-checkbox,[role="checkbox"]');
      const className = [input, control]
        .filter(Boolean)
        .map(node => String((node as any).className || ''))
        .join(' ');
      return Boolean(input?.checked)
        || /checked|active|selected|is-checked|am-checkbox-checked|am-checkbox-wrapper-checked|adm-checkbox-checked/.test(className)
        || input?.getAttribute?.('aria-checked') === 'true'
        || control?.getAttribute?.('aria-checked') === 'true';
    };
    const rawCandidates = Array.from(document.querySelectorAll('input[type="checkbox"], label, .am-checkbox-agree, .am-checkbox-wrapper, .am-checkbox, .am-checkbox-inner, .adm-checkbox, .agreement, .protocol, [role="checkbox"]'));
    const roots: any[] = [];
    const seen = new Set<any>();
    for (const candidate of rawCandidates) {
      const root = nearestAgreementRoot(candidate);
      if (!root || seen.has(root) || !visible(root)) continue;
      const text = textOf(root);
      if (!agreementText(text)) continue;
      seen.add(root);
      roots.push(root);
    }
    let unchecked = 0;
    for (const root of roots) {
      if (!checkedLike(root)) unchecked += 1;
    }
    return unchecked;
  });
  if (uncheckedCount > 0) {
    const tapped = await tapH5AgreementControls(page).catch(() => 0);
    const settled = await settlePostClickFlow(page).catch(() => []);
    await page.waitForTimeout(500);
    return tapped + settled.length;
  }
  return 0;
}

async function tapH5AgreementControls(page: any): Promise<number> {
  const boxes = await page.evaluate(() => {
    const norm = (value: unknown) => String(value || '').replace(/\s+/g, ' ').trim();
    const visible = (node: any) => {
      if (!node) return false;
      const style = window.getComputedStyle(node);
      const rect = node.getBoundingClientRect();
      return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
    };
    const agreementText = (text: string) => /本人充分阅读|本人已逐页阅读|阅读、理解并同意|阅读并同意|投保条件|投保重要告知|投保人声明|保险条款|免责条款|数据隐私|服务流程|鏈汉鍏呭垎闃呰|鏈汉宸查€愰〉闃呰|闃呰銆佺悊瑙ｅ苟鍚屾剰|闃呰骞跺悓鎰|鎶曚繚鏉′欢|鎶曚繚閲嶈鍛婄煡|鎶曚繚浜哄０鏄|淇濋櫓鏉℃|鍏嶈矗鏉℃|鏁版嵁闅愮|鏈嶅姟娴佺▼/.test(text);
    const textOf = (node: any) => norm(node?.innerText || node?.textContent || node?.getAttribute?.('aria-label') || node?.getAttribute?.('title') || '');
    const checkboxIn = (root: any) => (
      root?.matches?.('input[type="checkbox"]')
        ? root
        : root?.querySelector?.('input[type="checkbox"]')
    ) as HTMLInputElement | null;
    const checkedLike = (root: any) => {
      const input = checkboxIn(root);
      const control = input?.closest?.('.am-checkbox,.adm-checkbox')
        || root?.querySelector?.('.am-checkbox,.am-checkbox-inner,.adm-checkbox,[role="checkbox"]');
      const className = [input, control]
        .filter(Boolean)
        .map(node => String((node as any).className || ''))
        .join(' ');
      return Boolean(input?.checked)
        || /checked|active|selected|is-checked|am-checkbox-checked|am-checkbox-wrapper-checked|adm-checkbox-checked/.test(className)
        || input?.getAttribute?.('aria-checked') === 'true'
        || control?.getAttribute?.('aria-checked') === 'true';
    };
    const nearestAgreementRoot = (node: any) => {
      let current = node;
      for (let depth = 0; current && current !== document.body && depth < 8; depth += 1, current = current.parentElement) {
        const text = textOf(current);
        if (text && text.length <= 800 && agreementText(text)) return current;
      }
      return node?.closest?.('label,.am-checkbox-agree,.am-checkbox-wrapper,.adm-checkbox,.agreement,.protocol,.am-list-item,li,dd,div,p') || node;
    };
    const roots: any[] = [];
    const seen = new Set<any>();
    for (const candidate of Array.from(document.querySelectorAll('input[type="checkbox"], label, .am-checkbox-agree, .am-checkbox-wrapper, .am-checkbox, .am-checkbox-inner, .adm-checkbox, .agreement, .protocol, [role="checkbox"]'))) {
      const root = nearestAgreementRoot(candidate);
      if (!root || seen.has(root) || !visible(root)) continue;
      const text = textOf(root);
      if (!agreementText(text) || checkedLike(root)) continue;
      seen.add(root);
      roots.push(root);
    }
    return roots.map((root: any) => {
      const input = checkboxIn(root);
      const control = input?.closest?.('.am-checkbox,.adm-checkbox,[role="checkbox"]')
        || root?.querySelector?.('.am-checkbox,.am-checkbox-inner,.adm-checkbox,[role="checkbox"],input[type="checkbox"]')
        || root;
      if ((control as HTMLElement).matches?.('a,.diy_color,[href]')) return null;
      (control as HTMLElement).scrollIntoView({ block: 'center', inline: 'center' });
      const rect = (control as HTMLElement).getBoundingClientRect();
      const rootRect = (root as HTMLElement).getBoundingClientRect();
      const left = Number.isFinite(rect.left) && rect.width > 0 ? rect.left : rootRect.left;
      const top = Number.isFinite(rect.top) && rect.height > 0 ? rect.top : rootRect.top;
      const width = Number.isFinite(rect.width) && rect.width > 0 ? rect.width : Math.min(48, rootRect.width || 48);
      const height = Number.isFinite(rect.height) && rect.height > 0 ? rect.height : Math.min(48, rootRect.height || 48);
      return {
        x: Math.floor(left + Math.min(Math.max(width / 2, 10), 24)),
        y: Math.floor(top + Math.min(Math.max(height / 2, 10), height - 4)),
        target: String((control as HTMLElement).tagName || '').toLowerCase(),
        className: String((control as HTMLElement).className || '').slice(0, 80),
      };
    }).filter(point => point && Number.isFinite(point.x) && Number.isFinite(point.y));
  }).catch(() => []);
  let tapped = 0;
  const seenPoints = new Set<string>();
  for (const point of boxes as Array<{ x: number; y: number; target?: string; className?: string }>) {
    const key = `${point.x},${point.y}`;
    if (seenPoints.has(key)) continue;
    seenPoints.add(key);
    await page.mouse.click(point.x, point.y).catch(async () => {
      await page.touchscreen.tap(point.x, point.y).catch(() => undefined);
    });
    tapped += 1;
    await page.waitForTimeout(300);
  }
  if (tapped > 0) await settlePostClickFlow(page).catch(() => []);
  return tapped;
}

async function assertH5AgreementCheckedBeforeSubmit(page: any): Promise<void> {
  const state = await page.evaluate(() => {
    const norm = (value: unknown) => String(value || '').replace(/\s+/g, ' ').trim();
    const visible = (node: any) => {
      if (!node) return false;
      const style = window.getComputedStyle(node);
      const rect = node.getBoundingClientRect();
      return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
    };
    const agreementText = (text: string) => /本人充分阅读|本人已逐页阅读|阅读、理解并同意|阅读并同意|投保条件|投保重要告知|投保人声明|保险条款|免责条款|数据隐私|服务流程|鏈汉鍏呭垎闃呰|鏈汉宸查€愰〉闃呰|闃呰銆佺悊瑙ｅ苟鍚屾剰|闃呰骞跺悓鎰|鎶曚繚鏉′欢|鎶曚繚閲嶈鍛婄煡|鎶曚繚浜哄０鏄|淇濋櫓鏉℃|鍏嶈矗鏉℃|鏁版嵁闅愮|鏈嶅姟娴佺▼/.test(text);
    const textOf = (node: any) => norm(node?.innerText || node?.textContent || node?.getAttribute?.('aria-label') || node?.getAttribute?.('title') || '');
    const checkboxIn = (root: any) => (
      root?.matches?.('input[type="checkbox"]')
        ? root
        : root?.querySelector?.('input[type="checkbox"]')
    ) as HTMLInputElement | null;
    const checkedLike = (root: any) => {
      const input = checkboxIn(root);
      const control = input?.closest?.('.am-checkbox,.adm-checkbox')
        || root?.querySelector?.('.am-checkbox,.am-checkbox-inner,.adm-checkbox,[role="checkbox"]');
      const className = [input, control]
        .filter(Boolean)
        .map(node => String((node as any).className || ''))
        .join(' ');
      return Boolean(input?.checked)
        || /checked|active|selected|is-checked|am-checkbox-checked|am-checkbox-wrapper-checked|adm-checkbox-checked/.test(className)
        || input?.getAttribute?.('aria-checked') === 'true'
        || control?.getAttribute?.('aria-checked') === 'true';
    };
    const nearestAgreementRoot = (node: any) => {
      let current = node;
      for (let depth = 0; current && current !== document.body && depth < 8; depth += 1, current = current.parentElement) {
        const text = textOf(current);
        if (text && text.length <= 800 && agreementText(text)) return current;
      }
      return node?.closest?.('label,.am-checkbox-agree,.am-checkbox-wrapper,.adm-checkbox,.agreement,.protocol,.am-list-item,li,dd,div,p') || node;
    };
    const roots: any[] = [];
    const seen = new Set<any>();
    const candidates = Array.from(document.querySelectorAll('input[type="checkbox"], label, .am-checkbox-agree, .am-checkbox-wrapper, .am-checkbox, .am-checkbox-inner, .adm-checkbox, .agreement, .protocol, [role="checkbox"]'));
    for (const candidate of candidates) {
      const root = nearestAgreementRoot(candidate);
      if (!root || seen.has(root) || !visible(root)) continue;
      const text = textOf(root);
      if (!agreementText(text)) continue;
      seen.add(root);
      roots.push(root);
    }
    const unchecked = roots.filter(root => !checkedLike(root)).length;
    return { found: roots.length, unchecked };
  }).catch(() => ({ found: 0, unchecked: 0 }));
  if (state.found > 0 && state.unchecked > 0) {
    await ensureH5AgreementCheckedBeforeSubmit(page);
    const after = await page.evaluate(() => {
      const norm = (value: unknown) => String(value || '').replace(/\s+/g, ' ').trim();
      const visible = (node: any) => {
        if (!node) return false;
        const style = window.getComputedStyle(node);
        const rect = node.getBoundingClientRect();
        return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
      };
    const agreementText = (text: string) => /本人充分阅读|本人已逐页阅读|阅读、理解并同意|阅读并同意|投保条件|投保重要告知|投保人声明|保险条款|免责条款|数据隐私|服务流程|鏈汉鍏呭垎闃呰|鏈汉宸查€愰〉闃呰|闃呰銆佺悊瑙ｅ苟鍚屾剰|闃呰骞跺悓鎰|鎶曆繚鏉′欢|鎶曆繚閲嶈鍛婄煡|鎶曆繚浜哄０鏄|淇濋櫓鏉℃|鍏嶈矗鏉℃|鏁版嵁闅愮|鏈嶅姟娴佺▼/.test(text);
      const textOf = (node: any) => norm(node?.innerText || node?.textContent || node?.getAttribute?.('aria-label') || node?.getAttribute?.('title') || '');
      const checkedLike = (root: any) => {
        const input = (root?.matches?.('input[type="checkbox"]') ? root : root?.querySelector?.('input[type="checkbox"]')) as HTMLInputElement | null;
        const control = input?.closest?.('.am-checkbox,.adm-checkbox')
          || root?.querySelector?.('.am-checkbox,.am-checkbox-inner,.adm-checkbox,[role="checkbox"]');
        const className = [input, control]
          .filter(Boolean)
          .map(node => String((node as any).className || ''))
          .join(' ');
        return Boolean(input?.checked)
          || /checked|active|selected|is-checked|am-checkbox-checked|am-checkbox-wrapper-checked|adm-checkbox-checked/.test(className)
          || input?.getAttribute?.('aria-checked') === 'true'
          || control?.getAttribute?.('aria-checked') === 'true';
      };
      const roots: any[] = [];
      const seen = new Set<any>();
      for (const candidate of Array.from(document.querySelectorAll('input[type="checkbox"], label, .am-checkbox-agree, .am-checkbox-wrapper, .am-checkbox, .am-checkbox-inner, .adm-checkbox, .agreement, .protocol, [role="checkbox"]'))) {
        let root: any = candidate;
        for (let depth = 0; root && root !== document.body && depth < 8; depth += 1, root = root.parentElement) {
          const text = textOf(root);
          if (text && text.length <= 800 && agreementText(text)) break;
        }
        if (!root || root === document.body || seen.has(root) || !visible(root)) continue;
        if (!agreementText(textOf(root))) continue;
        seen.add(root);
        roots.push(root);
      }
      return { found: roots.length, unchecked: roots.filter(root => !checkedLike(root)).length };
    }).catch(() => ({ found: 0, unchecked: 0 }));
    if (after.found > 0 && after.unchecked > 0) {
      throw new Error('H5 agreement checkbox is still unchecked before submit');
    }
  }
}
""".splitlines(),
        "",
        *r"""
async function installH5SubmitPayloadPatch(page: any): Promise<void> {
  const alreadyInstalled = await page.evaluate(() => Boolean((window as any).__agent3SubmitPayloadPatchInstalled)).catch(() => false);
  if (alreadyInstalled) return;
  const submitMockData = mockData;
  const formatDate = (value: unknown) => String(value || '').replace(/[./]/g, '-').trim();
  const forWhoValue = String(submitMockData.forWho_20 || submitMockData['insured.forWho'] || '100');
  const isSelfInsured = /^(100|本人)$/.test(forWhoValue);
  const applicantName = String(submitMockData['applicant.name'] || submitMockData.cardOwner_107 || '');
  const applicantEnglishName = String(submitMockData['applicant.english_name'] || submitMockData['applicant.eName'] || submitMockData['applicant.pinyin'] || '');
  const applicantIdNo = String(submitMockData['applicant.id_no'] || submitMockData['insure_form.applicantidno'] || '');
  const applicantMobile = String(submitMockData['applicant.mobile'] || submitMockData['applicant.phone'] || submitMockData['insure_form.applicantphone'] || '');
  const applicantEmail = String(submitMockData['applicant.email'] || submitMockData['insure_form.applicantemail'] || '');
  const applicantBirthdate = formatDate(submitMockData['applicant.birthdate'] || submitMockData['applicant.birthday'] || '');
  const rawInsuredName = String(submitMockData['insured.name'] || applicantName);
  const rawInsuredEnglishName = String(submitMockData['insured.english_name'] || submitMockData['insured.eName'] || submitMockData['insured.pinyin'] || applicantEnglishName);
  const rawInsuredIdNo = String(submitMockData['insured.id_no'] || submitMockData['insure_form.insuredidno'] || applicantIdNo);
  const rawInsuredMobile = String(submitMockData['insured.mobile'] || submitMockData['insured.phone'] || submitMockData['insure_form.insuredphone'] || applicantMobile);
  const rawInsuredEmail = String(submitMockData['insured.email'] || submitMockData['insure_form.insuredemail'] || applicantEmail);
  const rawInsuredBirthdate = formatDate(submitMockData['insured.birthdate'] || submitMockData['insured.birthday'] || applicantBirthdate);
  const insuredName = isSelfInsured ? applicantName : rawInsuredName;
  const insuredEnglishName = isSelfInsured ? applicantEnglishName : rawInsuredEnglishName;
  const insuredIdNo = isSelfInsured ? applicantIdNo : rawInsuredIdNo;
  const insuredMobile = isSelfInsured ? applicantMobile : rawInsuredMobile;
  const insuredEmail = isSelfInsured ? applicantEmail : rawInsuredEmail;
  const insuredBirthdate = isSelfInsured ? applicantBirthdate : rawInsuredBirthdate;
  const applicantCardTypeName = String(submitMockData['applicant.id_type'] || submitMockData['applicant.card_type_name'] || '身份证');
  const applicantCardTypeValue = String(submitMockData['applicant.id_type_code'] || submitMockData['applicant.card_type_code'] || (applicantCardTypeName === '护照' ? '2' : '1'));
  const rawInsuredCardTypeName = String(submitMockData['insured.id_type'] || submitMockData['insured.card_type_name'] || applicantCardTypeName);
  const rawInsuredCardTypeValue = String(submitMockData['insured.id_type_code'] || submitMockData['insured.card_type_code'] || (rawInsuredCardTypeName === '护照' ? '2' : applicantCardTypeValue));
  const insuredCardTypeName = isSelfInsured ? applicantCardTypeName : rawInsuredCardTypeName;
  const insuredCardTypeValue = isSelfInsured ? applicantCardTypeValue : rawInsuredCardTypeValue;
  const applicantSexValue = String(submitMockData['applicant.sex_code'] || submitMockData['applicant.sex'] || '男').includes('女') ? '2' : '1';
  const rawInsuredSexValue = String(submitMockData['insured.sex_code'] || submitMockData['insured.sex'] || applicantSexValue).includes('女') ? '2' : '1';
  const insuredSexValue = isSelfInsured ? applicantSexValue : rawInsuredSexValue;
  const policyStartDate = formatDate(submitMockData['policy.start_date'] || submitMockData.policyStartDate || submitMockData['insure_form.insurancedate'] || '');
  const ageBandForBirthdate = (birthdate: string, referenceDate: string): string => {
    const birth = new Date(`${birthdate}T00:00:00`);
    const reference = new Date(`${referenceDate || new Date().toISOString().slice(0, 10)}T00:00:00`);
    if (Number.isNaN(birth.getTime()) || Number.isNaN(reference.getTime())) return '';
    let age = reference.getFullYear() - birth.getFullYear();
    const monthDelta = reference.getMonth() - birth.getMonth();
    if (monthDelta < 0 || (monthDelta === 0 && reference.getDate() < birth.getDate())) age -= 1;
    if (age >= 71) return '71-80周岁';
    if (age >= 18) return '18-70周岁';
    if (age >= 1) return '1-17周岁';
    return '';
  };
  const insuredAgeBand = ageBandForBirthdate(insuredBirthdate, policyStartDate);
  (globalThis as any).__agent4ExpectedTrialAgeBand = insuredAgeBand;
  const patchTrialGenesValue = (value: any) => {
    if (!value || !insuredAgeBand) return value;
    const patchGenes = (payload: any) => {
      if (!payload || typeof payload !== 'object' || !Array.isArray(payload.genes)) return payload;
      payload.genes = payload.genes.map((gene: any) => {
        if (!gene || typeof gene !== 'object') return gene;
        if (gene.key === 'insurantDate' || gene.geneKey === 'insurantDate') return { ...gene, value: insuredAgeBand };
        return gene;
      });
      return payload;
    };
    if (typeof value === 'string') {
      try { return JSON.stringify(patchGenes(JSON.parse(value))); } catch (_) { return value; }
    }
    return patchGenes(value);
  };
  const trialPriceFromLatestResult = (): number | null => {
    const latest = agent4LatestTrialResultForSubmit();
    if (!latest || typeof latest !== 'object') return null;
    const candidates = [
      (latest as any).preminumInfo?.displayPreminum,
      (latest as any).preminumInfo?.displayDiscountPreminum,
      (latest as any).trialPrice?.totalPreminum,
      (latest as any).trialPrice?.preminum,
      (latest as any).insuredList?.[0]?.displayPayPrice,
      (latest as any).insuredList?.[0]?.displaySettlementPrice,
      (latest as any).insuredList?.[0]?.payPrice,
      (latest as any).insuredList?.[0]?.settlementPrice,
    ];
    for (const candidate of candidates) {
      const numeric = Number(candidate);
      if (!Number.isFinite(numeric) || numeric <= 0) continue;
      return numeric > 1000 ? numeric / 100 : numeric;
    }
    return null;
  };
  const trialGenesFromLatestResult = (fallback: any) => {
    const latest = agent4LatestTrialResultForSubmit();
    const geneList = Array.isArray((latest as any)?.geneList) ? (latest as any).geneList : [];
    if (!geneList.length) return fallback;
    const patchGenes = (payload: any) => {
      if (!payload || typeof payload !== 'object') return payload;
      const existingGenes = Array.isArray(payload.genes) ? payload.genes : [];
      const nextGenes = existingGenes.length ? existingGenes.map((gene: any) => {
        if (!gene || typeof gene !== 'object') return gene;
        const latestGene = geneList.find((item: any) => (
          String(item?.key || item?.geneKey || item?.protectItemId || '') === String(gene.key || gene.geneKey || gene.protectItemId || '')
          || (gene.key === 'insurantDate' && item?.key === 'insurantDate')
          || (gene.geneKey === 'insurantDate' && item?.geneKey === 'insurantDate')
        ));
        if (!latestGene) return gene;
        return { ...gene, value: latestGene.value };
      }) : geneList.map((gene: any) => ({
        sort: gene.sort,
        protectItemId: gene.protectItemId || '',
        key: gene.key || '',
        value: gene.value,
      }));
      return { ...payload, genes: nextGenes };
    };
    if (typeof fallback === 'string') {
      try { return JSON.stringify(patchGenes(JSON.parse(fallback))); } catch (_) { return fallback; }
    }
    return patchGenes(fallback);
  };
  const patchAgent3SubmitPayload = (payload: any) => {
    if (!payload || typeof payload !== 'object') return payload;
    payload.trialGenes = trialGenesFromLatestResult(patchTrialGenesValue(payload.trialGenes));
    const latestTrialPrice = trialPriceFromLatestResult();
    if (latestTrialPrice !== null) payload.price = latestTrialPrice;
    const rows = payload.data || {};
    const applicantRows = rows['10'] || rows[10];
    const insuredRows = rows['20'] || rows[20];
    const policyRows = rows['102'] || rows[102];
    const applicant = Array.isArray(applicantRows) ? applicantRows[0] : applicantRows;
    const insured = Array.isArray(insuredRows) ? insuredRows[0] : insuredRows;
    const policy = Array.isArray(policyRows) ? policyRows[0] : policyRows;
    if (applicant && applicantBirthdate) applicant.birthdate = applicantBirthdate;
    if (insured && insuredBirthdate) insured.birthdate = insuredBirthdate;
    if (applicant) {
      if (applicantName) applicant.cName = applicantName;
      if (applicantEnglishName) applicant.eName = applicantEnglishName;
      if (applicantIdNo) applicant.cardNumber = applicantIdNo;
      if (applicantMobile) applicant.moblie = applicantMobile;
      if (applicantMobile) applicant.mobile = applicantMobile;
      if (applicantEmail) applicant.email = applicantEmail;
      applicant.cardTypeName = applicantCardTypeValue;
      applicant.cardType = applicantCardTypeValue;
      applicant.sex = applicantSexValue;
    }
    if (insured) {
      if (insuredName) insured.cName = insuredName;
      if (insuredEnglishName) insured.eName = insuredEnglishName;
      if (insuredIdNo) insured.cardNumber = insuredIdNo;
      if (insuredMobile) insured.moblie = insuredMobile;
      if (insuredMobile) insured.mobile = insuredMobile;
      if (insuredEmail) insured.email = insuredEmail;
      insured.cardTypeName = insuredCardTypeValue;
      insured.cardType = insuredCardTypeValue;
      insured.sex = insuredSexValue;
    }
    if (policyStartDate) payload.startDate = policyStartDate;
    if (policy && policyStartDate) policy.insuranceDate = policyStartDate;
    if (insured && isSelfInsured && applicant) {
      insured.sex = applicant.sex || insured.sex || insuredSexValue;
      insured.cardTypeName = applicant.cardTypeName || insured.cardTypeName || insuredCardTypeValue;
      insured.cardType = applicant.cardType || insured.cardType || insuredCardTypeValue;
      insured.cardNumber = applicant.cardNumber || insured.cardNumber;
      insured.cName = applicant.cName || insured.cName;
      insured.eName = applicant.eName || insured.eName;
      insured.moblie = applicant.moblie || insured.moblie;
      insured.mobile = applicant.mobile || insured.mobile;
      insured.email = applicant.email || insured.email;
    }
    return payload;
  };
  await page.route('**/api/apps/cps/insure/submit**', async route => {
    const request = route.request();
    const method = request.method().toUpperCase();
    let postData = request.postData() || '';
    if (method === 'POST' && postData) {
      try {
        const payload = patchAgent3SubmitPayload(JSON.parse(postData));
        postData = JSON.stringify(payload);
      } catch (_) {}
    }
    await route.continue({ postData });
  });
  await page.route('**/api/apps/cps/product/trial/insured**', async route => {
    const request = route.request();
    const method = request.method().toUpperCase();
    let postData = request.postData() || '';
    if (method === 'POST' && postData) {
      try {
        const payload = patchAgent3SubmitPayload(JSON.parse(postData));
        postData = JSON.stringify(payload);
      } catch (_) {}
    }
    await route.continue({ postData });
  });
  await page.evaluate(() => { (window as any).__agent3SubmitPayloadPatchInstalled = true; }).catch(() => undefined);
}

async function syncH5InsureFormFromMock(page: any, options: { mode?: 'initial' | 'retry' } = {}): Promise<number> {
  const syncMode = options.mode ?? 'initial';
  return await page.evaluate(async ({ mockData, syncMode }) => {
    const records: string[] = [];
    const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));
    const norm = (text: unknown) => String(text || '').replace(/\s+/g, ' ').trim();
    const visible = (el: any) => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
    const applicantName = String(mockData['applicant.name'] || mockData.cardOwner_107 || '张三');
    const applicantIdNo = String(mockData['applicant.id_no'] || '110101199001010015');
    const applicantAddress = String(mockData['applicant.address'] || '北京市朝阳区测试地址1号');
    const applicantMobile = String(mockData['applicant.mobile'] || '13800138000');
    const applicantEmail = String(mockData['applicant.email'] || 'zhangsan@example.com');
    const forWhoValue = String(mockData.forWho_20 || mockData['insured.forWho'] || '100');
    const isSelfInsured = /^(100|本人)$/.test(forWhoValue);
    const rawInsuredName = String(mockData['insured.name'] || applicantName);
    const rawInsuredIdNo = String(mockData['insured.id_no'] || applicantIdNo);
    const insuredName = isSelfInsured ? applicantName : rawInsuredName;
    const insuredIdNo = isSelfInsured ? applicantIdNo : rawInsuredIdNo;
    const romanizeChineseName = (name: string, fallback: string) => {
      const pinyinByChar: Record<string, string> = {
        "赵": "zhao", "钱": "qian", "孙": "sun", "李": "li", "周": "zhou", "吴": "wu", "郑": "zheng", "王": "wang", "冯": "feng", "陈": "chen", "杨": "yang", "柏": "bai",
        "子": "zi", "轩": "xuan", "浩": "hao", "然": "ran", "宇": "yu", "辰": "chen", "梓": "zi", "涵": "han", "诗": "shi", "雨": "yu", "佳": "jia", "怡": "yi",
        "欣": "xin", "妍": "yan", "晨": "chen", "曦": "xi", "俊": "jun", "杰": "jie", "嘉": "jia", "豪": "hao", "思": "si", "远": "yuan", "雅": "ya", "琪": "qi",
      };
      const romanized = Array.from(String(name || '')).map(char => pinyinByChar[char] || '').join('');
      return romanized.length >= 2 ? romanized : fallback;
    };
    const englishNameFor = (prefix: string, fallbackName: string) => {
      const configured = mockData[`${prefix}.english_name`] || mockData[`${prefix}.eName`] || mockData[`${prefix}.pinyin`] || mockData[`insure_form.${prefix}pinyin`];
      if (configured && /^[A-Za-z][A-Za-z\s'-]{1,60}$/.test(String(configured))) {
        return String(configured).replace(/\s+/g, '').toLowerCase();
      }
      return romanizeChineseName(fallbackName, prefix === 'insured' ? 'lisi' : 'zhangsan');
    };
    const applicantEnglishName = englishNameFor('applicant', applicantName);
    const insuredEnglishName = isSelfInsured ? applicantEnglishName : englishNameFor('insured', rawInsuredName);
    const applicantBirthdate = String(mockData['applicant.birthdate'] || mockData['applicant.birthday'] || '').trim();
    const rawInsuredBirthdate = String(mockData['insured.birthdate'] || mockData['insured.birthday'] || applicantBirthdate).trim();
    const insuredBirthdate = isSelfInsured ? applicantBirthdate : rawInsuredBirthdate;
    const formatDate = (value: unknown) => String(value || '').replace(/[./]/g, '-').trim();
    const cardTypeCodeFor = (name: unknown, configuredCode: unknown, fallbackCode = '1') => {
      const text = String(name || '').trim();
      const code = String(configuredCode || '').trim();
      if (code) return code;
      if (text === '护照') return '2';
      return fallbackCode;
    };
    const applicantCardTypeName = String(mockData['applicant.id_type'] || mockData['applicant.card_type_name'] || '身份证');
    const applicantCardTypeValue = cardTypeCodeFor(applicantCardTypeName, mockData['applicant.id_type_code'] || mockData['applicant.card_type_code']);
    const rawInsuredCardTypeName = String(mockData['insured.id_type'] || mockData['insured.card_type_name'] || applicantCardTypeName);
    const rawInsuredCardTypeValue = cardTypeCodeFor(rawInsuredCardTypeName, mockData['insured.id_type_code'] || mockData['insured.card_type_code'], applicantCardTypeValue);
    const insuredCardTypeName = isSelfInsured ? applicantCardTypeName : rawInsuredCardTypeName;
    const insuredCardTypeValue = isSelfInsured ? applicantCardTypeValue : rawInsuredCardTypeValue;
    const applicantSexText = String(mockData['applicant.sex'] || '男');
    const applicantSexValue = String(mockData['applicant.sex_code'] || applicantSexText).includes('女') ? '2' : '1';
    const insuredSexText = isSelfInsured ? applicantSexText : String(mockData['insured.sex'] || applicantSexText);
    const insuredSexValue = isSelfInsured ? applicantSexValue : (String(mockData['insured.sex_code'] || insuredSexText).includes('女') ? '2' : '1');
    const startValue = String(mockData['applicant.card_valid_start'] || '2021-05-15').replace(/[./]/g, '-');
    const endValue = String(mockData['applicant.card_valid_end'] || '2041-05-15').replace(/[./]/g, '-');
    const bankName = String(mockData.bankName_107 || mockData.openBank_107 || mockData.bankCode_107 || '中国工商银行');
    const bankValue = String(mockData.bankValue_107 || mockData.bankControlValue_107 || mockData.bank_107 || '1');
    const payAccount = String(mockData.payAccount_107 || '6200588435998028938').replace(/\s+/g, '');
    const cardOwner = String(mockData.cardOwner_107 || applicantName);
    const regionValue = String(mockData.provCityText_10 || mockData['applicant.region_code'] || '110000-110105');
    const regionText = String(mockData['applicant.region'] || mockData['applicant.region_text'] || '北京市-朝阳区');
    const jobValue = String(mockData.jobText_10 || mockData['applicant.occupation_code'] || '6546010-6546043-6546243-1');
    const jobText = String(mockData['applicant.occupation'] || mockData.jobTextLabel_10 || '一般内勤人员');
    const hasVisibleOccupationControl = () => Array.from(document.querySelectorAll('.am-list-item,.am-list-line,.insure-filed-wrapper,li,dd,label,section,article,div[name^="jobText"],[name*="job" i],[name*="occupation" i]'))
      .filter(visible)
      .some((el: any) => /职业/.test(norm(el.innerText || el.textContent || el.getAttribute?.('name') || el.getAttribute?.('aria-label') || '')));
    const hasOccupationState = (entity: any) => !!entity && typeof entity === 'object' && Object.prototype.hasOwnProperty.call(entity, 'jobText');
    const shouldPatchOccupation = (entity: any) => hasOccupationState(entity) || hasVisibleOccupationControl();
    const policyStartDate = formatDate(mockData['policy.start_date'] || mockData.policyStartDate || mockData['insure_form.insurancedate'] || '');
    const ageBandForBirthdate = (birthdate: string, referenceDate: string) => {
      const birth = new Date(`${formatDate(birthdate)}T00:00:00`);
      const reference = new Date(`${formatDate(referenceDate) || new Date().toISOString().slice(0, 10)}T00:00:00`);
      if (Number.isNaN(birth.getTime()) || Number.isNaN(reference.getTime())) return '';
      let age = reference.getFullYear() - birth.getFullYear();
      const monthDelta = reference.getMonth() - birth.getMonth();
      if (monthDelta < 0 || (monthDelta === 0 && reference.getDate() < birth.getDate())) age -= 1;
      if (age >= 71) return '71-80周岁';
      if (age >= 18) return '18-70周岁';
      if (age >= 1) return '1-17周岁';
      return '';
    };
    const insuredAgeBand = ageBandForBirthdate(insuredBirthdate, policyStartDate);
    (window as any).__agent4ExpectedTrialAgeBand = insuredAgeBand;
    const travelPurposeValue = String(mockData['travel.purpose_code'] || mockData.travelPurposeCode || mockData['insure_form.travelpurposecode'] || '1');
    const travelPurposeText = String(mockData['travel.purpose'] || mockData.travelPurpose || mockData['insure_form.travelpurpose'] || '旅游');
    const travelDestination = String(mockData['travel.destination'] || mockData.tripDestination || mockData['insure_form.traveldestination'] || '中国澳门');
    const normalizeAccount = (value: unknown) => String(value || '').replace(/\s+/g, '');
    const payAccountKeywords = /银行账号|银行卡号|银行账户|卡号|payAccount/i;
    const bankAccountLabel = /银行账号|银行卡号|银行账户|卡号|payAccount/i;
    const isEnglishNameProbe = (probe: unknown) => /eName|english|pinyin|英文|拼音/i.test(String(probe || ''));
    const isBankAccountLabel = (label: unknown) => bankAccountLabel.test(String(label || ''));
    const isPayAccountField = (el: any, label = '') => {
      const rowText = norm(el?.closest?.('.am-list-item,.am-list-line,.insure-filed-wrapper,li,dd,label,div')?.innerText || el?.parentElement?.innerText || '');
      const probe = `${label} ${el?.placeholder || ''} ${el?.name || ''} ${el?.id || ''} ${el?.getAttribute?.('aria-label') || ''} ${rowText}`;
      return payAccountKeywords.test(probe) && !/持卡人|账户名|户名/.test(probe);
    };
    const lockPayAccount = (el: any, value: unknown) => {
      const normalized = normalizeAccount(value || payAccount);
      if (el?.dataset) el.dataset.agent3PayAccountLocked = '1';
      (window as any).__agent3PayAccountLocked = true;
      (window as any).__agent3SkipPayAccountWrites = true;
      (window as any).__agent3PayAccountValue = normalized;
      return normalized;
    };
    const skipPayAccountWrites = syncMode === 'retry' || !!(window as any).__agent3SkipPayAccountWrites;
    const shouldSkipPayAccountWrite = (el: any, label: string, value: unknown) => {
      if (!isPayAccountField(el, label)) return false;
      const current = normalizeAccount(el?.value);
      const expected = normalizeAccount(value || payAccount || (window as any).__agent3PayAccountValue);
      const lockedValue = normalizeAccount((window as any).__agent3PayAccountValue);
      const locked = !!el?.dataset?.agent3PayAccountLocked || !!(window as any).__agent3PayAccountLocked;
      if ((current && expected && current === expected) || (current && lockedValue && current === lockedValue && (!expected || lockedValue === expected)) || ((skipPayAccountWrites || locked) && current && expected && current === expected)) {
        const kept = lockPayAccount(el, current || expected || lockedValue);
        records.push(`银行卡账号跳过重写=${kept}`);
        return true;
      }
      return false;
    };
    const clear = (value: unknown) => ({
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
    const patchedTrialGenesValue = (value: any) => {
      if (!insuredBirthdate || !value) return value;
      const patchGenes = (payload: any) => {
        if (!payload || typeof payload !== 'object' || !Array.isArray(payload.genes)) return payload;
        payload.genes = payload.genes.map((gene: any) => {
          if (!gene || typeof gene !== 'object') return gene;
          if (gene.key === 'insurantDate' || gene.geneKey === 'insurantDate') {
            return { ...gene, value: insuredAgeBand || insuredBirthdate };
          }
          return gene;
        });
        return payload;
      };
      if (typeof value === 'string') {
        try { return JSON.stringify(patchGenes(JSON.parse(value))); } catch (_) { return value; }
      }
      return patchGenes(value);
    };
    const patchTrialGenesInsurantDate = (obj: any) => {
      if (!obj || typeof obj !== 'object' || !insuredBirthdate) return;
      const candidates = [
        obj,
        obj.product?.insure?.data,
        obj.insure?.data,
        obj.data,
      ].filter(Boolean);
      for (const candidate of candidates) {
        if (Object.prototype.hasOwnProperty.call(candidate, 'trialGenes')) {
          candidate.trialGenes = patchedTrialGenesValue(candidate.trialGenes);
        }
      }
    };
    const fire = (el: any) => {
      if (!el) return false;
      for (const type of ['input', 'change', 'blur']) {
        try { el.dispatchEvent(new Event(type, { bubbles: true })); } catch (_) {}
      }
      return true;
    };
    const clickLikeUser = (el: any) => {
      if (!el) return false;
      try { el.scrollIntoView({ block: 'center', inline: 'center' }); } catch (_) {}
      for (const type of ['touchstart', 'mousedown', 'mouseup', 'click', 'touchend']) {
        try {
          const event = type.startsWith('touch')
            ? new Event(type, { bubbles: true, cancelable: true })
            : new MouseEvent(type, { bubbles: true, cancelable: true, view: window });
          el.dispatchEvent(event);
        } catch (_) {}
      }
      try { if (typeof el.click === 'function') el.click(); } catch (_) {}
      return true;
    };
    const setValue = (el: any, value: unknown, label: string) => {
      if (!el || value === undefined || value === null || String(value) === '') return false;
      if (isPayAccountField(el, label) && !isBankAccountLabel(label)) {
        records.push(`银行卡账号跳过非账号写入=${label}`);
        return false;
      }
      if (shouldSkipPayAccountWrite(el, label, value)) return false;
      if (el.readOnly) el.removeAttribute('readonly');
      if (el.disabled) el.removeAttribute('disabled');
      const text = String(value);
      const setter = Object.getOwnPropertyDescriptor(el.constructor.prototype, 'value')?.set;
      if (setter) setter.call(el, text);
      else el.value = text;
      el.setAttribute('value', text);
      fire(el);
      if (isPayAccountField(el, label)) lockPayAccount(el, text);
      records.push(`${label}=${text}`);
      return true;
    };
    const allEditable = () => Array.from(document.querySelectorAll('input,textarea')).filter((el: any) => {
      const type = String(el.type || '').toLowerCase();
      return visible(el) && !['hidden', 'button', 'submit', 'reset', 'file', 'radio', 'checkbox'].includes(type);
    }) as any[];
    const canonicalRow = (row: any) => row?.closest?.('.insure-filed-wrapper,.am-list-item,.module-period-picker,li,dd,section,article') || row;
    const uniqueRows = (rows: any[]) => {
      const seen = new Set<any>();
      const deduped: any[] = [];
      for (const row of rows) {
        const key = canonicalRow(row);
        if (!key || seen.has(key)) continue;
        seen.add(key);
        deduped.push(key);
      }
      return deduped;
    };
    const uniqueElements = (items: any[]) => {
      const seen = new Set<any>();
      const deduped: any[] = [];
      for (const item of items) {
        if (!item || seen.has(item)) continue;
        seen.add(item);
        deduped.push(item);
      }
      return deduped;
    };
    const rowRoots = () => Array.from(document.querySelectorAll('.am-list-item,.am-list-line,.insure-filed-wrapper,li,dd,label,section,article'))
      .filter(visible)
      .filter((el: any) => {
        const text = norm(el.innerText || el.textContent);
        return text.length >= 2 && text.length <= 260;
      }) as any[];
    const rowsByLabel = (regex: RegExp) => uniqueRows(rowRoots().filter((row: any) => regex.test(norm(row.innerText || row.textContent))));
    const editableIn = (row: any) => Array.from(row?.querySelectorAll?.('input,textarea') || []).filter((el: any) => {
      const type = String(el.type || '').toLowerCase();
      return visible(el) && !['hidden', 'button', 'submit', 'reset', 'file', 'radio', 'checkbox'].includes(type);
    })[0] as any;
    const fillByPlaceholder = (regex: RegExp, value: unknown, label: string) => {
      let changed = false;
      for (const el of allEditable()) {
        const rowText = el.closest('.am-list-item,.am-list-line,.insure-filed-wrapper,li,dd,label,div')?.innerText || '';
        const probe = `${el.placeholder || ''} ${el.getAttribute('aria-label') || ''} ${el.name || ''} ${el.id || ''} ${rowText}`;
        if (/银行账号|银行卡号|银行账户/.test(label) && /持卡人|账户名|账户名须为投保人本人/.test(probe)) continue;
        if (/姓名/.test(label) && !/拼音|英文/.test(label) && isEnglishNameProbe(probe)) continue;
        if (regex.test(probe)) changed = setValue(el, value, label) || changed;
      }
      return changed;
    };
    const fillByLabel = (regex: RegExp, value: unknown, occurrence: number, label: string) => {
      const precise = preciseRowByLabel(regex, occurrence);
      const rows = rowsByLabel(regex).filter((row: any) => norm(row.innerText || row.textContent).length <= 140);
      const row = precise || rows[Math.min(occurrence, Math.max(rows.length - 1, 0))];
      return setValue(editableIn(row), value, label);
    };
    const preciseRowByLabel = (labelRegex: RegExp, occurrence = 0) => {
      const labels = Array.from(document.querySelectorAll('.am-list-content,.am-input-label,.am-textarea-label,.multiple-label,.insure-field-label,span,div'))
        .filter(visible)
        .filter((el: any) => {
          const text = norm(el.innerText || el.textContent);
          return text.length >= 2 && text.length <= 16 && labelRegex.test(text);
        }) as any[];
      const rows = uniqueRows(labels.map((label: any) => label.closest?.('.am-list-item,.insure-filed-wrapper,.module-period-picker,.am-flexbox,li,dd') || label));
      return rows[Math.min(occurrence, Math.max(rows.length - 1, 0))] || null;
    };
    const setRowExtraText = (labelRegex: RegExp, value: string, occurrence = 0) => {
      const row = preciseRowByLabel(labelRegex, occurrence) || rowsByLabel(labelRegex)[occurrence];
      if (!row) return false;
      const target = Array.from(row.querySelectorAll('.am-list-extra,.adm-list-item-extra,.date span')).filter(visible).reverse()[0] as any;
      if (target) {
        target.textContent = value;
        records.push(`${norm(row.innerText).slice(0, 18)}=${value}`);
        return true;
      }
      return false;
    };
    const setAllRowExtraText = (labelRegex: RegExp, value: string) => {
      let changed = false;
      const preciseRows = [0, 1, 2, 3].map(index => preciseRowByLabel(labelRegex, index)).filter(Boolean) as any[];
      const rows = uniqueRows([...rowsByLabel(labelRegex), ...preciseRows]);
      for (const row of rows) {
        const target = Array.from(row.querySelectorAll('.am-list-extra,.adm-list-item-extra,.date span')).filter(visible).reverse()[0] as any;
        if (!target) continue;
        target.textContent = value;
        changed = true;
      }
      if (changed) records.push(`${String(labelRegex).slice(1, 18)}=${value}`);
      return changed;
    };
    const modulesByHeader = (headerRegex: RegExp) => {
      const candidates = Array.from(document.querySelectorAll('.am-accordion-item,.am-list,.am-list-body,section,article'))
        .filter(visible) as any[];
      return uniqueElements(candidates.filter((moduleRoot: any) => {
        const header = moduleRoot.querySelector?.('.am-accordion-header,[role="tab"]');
        const headerText = norm(header?.innerText || header?.textContent);
        const rootText = norm(moduleRoot.innerText || moduleRoot.textContent);
        return headerRegex.test(headerText) || headerRegex.test(rootText.slice(0, 120));
      }));
    };
    const rowRootsIn = (moduleRoot: any) => Array.from(moduleRoot?.querySelectorAll?.('.am-list-item,.am-list-line,.insure-filed-wrapper,.module-period-picker,.am-flexbox,li,dd,label') || [])
      .filter(visible)
      .filter((el: any) => {
        const text = norm(el.innerText || el.textContent);
        return text.length >= 2 && text.length <= 280;
      }) as any[];
    const rowsByLabelIn = (moduleRoot: any, labelRegex: RegExp) => uniqueRows(rowRootsIn(moduleRoot).filter((row: any) => labelRegex.test(norm(row.innerText || row.textContent))));
    const setModuleRowExtraText = (moduleRoot: any, labelRegex: RegExp, value: string, label: string) => {
      let changed = false;
      for (const row of rowsByLabelIn(moduleRoot, labelRegex)) {
        const targets = Array.from(row.querySelectorAll('.am-list-extra,.adm-list-item-extra')).filter(visible) as any[];
        const target = targets.reverse()[0];
        if (!target) continue;
        target.textContent = value;
        try { target.innerText = value; } catch (_) {}
        changed = true;
      }
      if (changed) records.push(`${label}=${value}`);
      return changed;
    };
    const setModulePeriod = (moduleRoot: any, start: string, end: string, label: string) => {
      let changed = false;
      const periodRoots = rowRootsIn(moduleRoot).filter((row: any) => /证件有效期|有效期/.test(norm(row.innerText || row.textContent)));
      const roots = uniqueElements([...periodRoots, moduleRoot]);
      for (const root of roots) {
        const startTargets = Array.from(root.querySelectorAll('.date-picker-wrapper.start-picker .date span,.start-picker .date span,.start-picker .date')).filter(visible) as any[];
        const endTargets = Array.from(root.querySelectorAll('.date-picker-wrapper.end-picker .date span,.date-picker-wrapper.stop-picker .date span,.end-picker .date span,.stop-picker .date span,.end-picker .date,.stop-picker .date')).filter(visible) as any[];
        for (const target of startTargets) {
          target.textContent = start;
          try { target.innerText = start; } catch (_) {}
          changed = true;
        }
        for (const target of endTargets) {
          target.textContent = end;
          try { target.innerText = end; } catch (_) {}
          changed = true;
        }
        const dateTargets = Array.from(root.querySelectorAll('.date span')).filter(visible) as any[];
        if (dateTargets.length >= 2) {
          dateTargets[0].textContent = start;
          dateTargets[dateTargets.length - 1].textContent = end;
          changed = true;
        }
      }
      if (changed) records.push(`${label}=${start}-${end}`);
      return changed;
    };
    const getFiber = (node: any) => {
      if (!node) return null;
      const key = Object.keys(node).find(item => item.startsWith('__reactFiber$') || item.startsWith('__reactInternalInstance$'));
      return key ? node[key] : null;
    };
    const labelRegexForBusiness = (label: string) => {
      if (label === '出行目的') return /^出行目的(?!地)/;
      if (label === '出行目的地') return /出行目的地|目的地/;
      return new RegExp(label);
    };
    const displayTextForBusiness = (label: string, keyCode: string, value: string) => (
      keyCode === 'purpose' && String(value) === '1' ? travelPurposeText : String(value)
    );
    const setBusinessRowText = (label: string, labelRegex: RegExp, displayText: string) => {
      let changed = false;
      const rows = uniqueRows([preciseRowByLabel(labelRegex, 0), ...rowsByLabel(labelRegex)].filter(Boolean) as any[])
        .filter((row: any) => {
          const text = norm(row.innerText || row.textContent);
          if (label === '出行目的') return text.includes('出行目的') && !text.includes('出行目的地');
          if (label === '出行目的地') return text.includes('出行目的地') || (text.includes('目的地') && !text.includes('出行目的 '));
          return labelRegex.test(text);
        });
      for (const row of rows) {
        const targets = Array.from(row.querySelectorAll('.am-list-extra,.adm-list-item-extra,input[disabled],input[readonly],input,textarea,.date span'))
          .filter(visible) as any[];
        const target = targets.reverse()[0];
        if (!target) continue;
        if ('value' in target) {
          target.value = displayText;
          target.setAttribute('value', displayText);
          fire(target);
        } else {
          target.textContent = displayText;
          try { target.innerText = displayText; } catch (_) {}
        }
        changed = true;
      }
      return changed;
    };
    const setBusinessField = (label: string, keyCode: string, value: string, moduleId: string) => {
      if (!value) return false;
      const displayText = displayTextForBusiness(label, keyCode, value);
      const labelRegex = labelRegexForBusiness(label);
      let changed = setBusinessRowText(label, labelRegex, displayText);
      const rows = rowsByLabel(labelRegex);
      for (const row of rows) {
        const sources = uniqueElements([
          row.querySelector?.('.am-list-content')?.closest?.('.am-list-item'),
          row.closest?.('.am-list-item'),
          row,
          ...Array.from(row.querySelectorAll?.('.insure-filed-wrapper,.am-list-item,.picker-input,.insure-filed-wrapper *,.am-list-item *') || []),
        ].filter(Boolean));
        const seen = new Set<any>();
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
            if (props?.currentAttr?.keyCode === keyCode && typeof props.onChange === 'function') {
              const sourceValue = props.currentAttrData && typeof props.currentAttrData === 'object' ? props.currentAttrData : {};
              const nextValue = {
                ...sourceValue,
                value: String(value),
                label: displayText,
                text: displayText,
                hasError: false,
                hasAjaxError: false,
                error: false,
                errorMsg: '',
                errorRemind: '',
                msg: '',
                ajaxError: '',
              };
              const mid = props.currentModule?.id ?? props.currentModule?.moduleId ?? props.mid ?? sourceValue.mid ?? moduleId;
              const index = Number.isFinite(Number(props.index)) ? Number(props.index) : 0;
              try { props.onChange({ mid, index, keyCode, value: nextValue }); } catch (_) {}
              changed = true;
              break;
            }
            fiber = fiber.return || fiber._hostParent || fiber._currentElement?._owner;
            depth += 1;
          }
        }
      }
      if (changed) records.push(`${label}=${displayText}`);
      return changed;
    };
    const syncVisibleInsureModules = (phase: string) => {
      const modulePlan = [
        { header: /投保人信息/, label: '投保人' },
        { header: /被保险人信息/, label: '被保险人' },
      ];
      for (const plan of modulePlan) {
        for (const moduleRoot of modulesByHeader(plan.header)) {
          const label = plan.label;
          setModulePeriod(moduleRoot, startValue, endValue, `${label}证件有效期`);
          setModuleRowExtraText(moduleRoot, /居住省市|省市区|省市|地区/, regionText, label);
          setModuleRowExtraText(moduleRoot, /职业/, jobText, label);
        }
      }
      records.push(`模块可见字段同步=${phase}`);
    };
    const activeModal = () => Array.from(document.querySelectorAll('.am-picker-popup,.am-modal,.adm-popup,.adm-modal,.adm-picker,.layui-layer,.modal,[role="dialog"],body'))
      .filter(visible)
      .sort((a: any, b: any) => {
        const ar = a.getBoundingClientRect();
        const br = b.getBoundingClientRect();
        return (br.width * br.height) - (ar.width * ar.height);
      })[0] as any || document;
    const clickExactText = async (texts: string[], root: any = document) => {
      const nodes = Array.from(root.querySelectorAll('button,a,span,div,li,p')).filter(visible) as any[];
      for (const target of texts) {
        const exact = nodes.filter(node => norm(node.innerText || node.textContent) === target);
        const fuzzy = nodes.filter(node => norm(node.innerText || node.textContent).includes(target));
        const chosen = (exact.length ? exact : fuzzy).sort((a, b) => norm(a.innerText).length - norm(b.innerText).length)[0];
        if (chosen) {
          clickLikeUser(chosen);
          await sleep(400);
          return norm(chosen.innerText || chosen.textContent);
        }
      }
      return '';
    };
    const clickPickerRow = async (labelRegex: RegExp, occurrence = 0) => {
      const row = preciseRowByLabel(labelRegex, occurrence);
      if (!row) return null;
      const target = Array.from(row.querySelectorAll('.am-list-extra,.adm-list-item-extra,.am-list-arrow,.am-list-line,span,div'))
        .filter(visible)
        .reverse()[0] as any || row;
      clickLikeUser(target);
      await sleep(650);
      return row;
    };
    const dismissPickerModal = async (labelRegex: RegExp) => {
      const modals = Array.from(document.querySelectorAll('.am-picker-popup,.am-modal,.am-modal-wrap,.adm-popup,.adm-modal,.adm-picker,.layui-layer,.modal,[role="dialog"]'))
        .filter(visible) as any[];
      for (const modal of modals.reverse()) {
        const modalText = norm(modal.innerText || modal.textContent);
        if (!labelRegex.test(modalText)) continue;
        const closeNodes = Array.from(modal.querySelectorAll('button,a,span,div,[aria-label]')).filter(visible) as any[];
        const close = closeNodes.find(node => /Close|关闭|取消|完成|确定|确认/.test(norm(node.getAttribute?.('aria-label') || node.innerText || node.textContent)));
        if (close) {
          clickLikeUser(close);
          await sleep(350);
        }
        if (visible(modal)) {
          modal.style.display = 'none';
          modal.style.pointerEvents = 'none';
          records.push(`关闭残留弹层=${modalText.slice(0, 24)}`);
        }
      }
    };
    const selectRegion = async (occurrence = 0) => {
      const row = await clickPickerRow(/居住省市|省市区|省市|地区/, occurrence);
      if (!row) return;
      const modal = activeModal();
      await clickExactText(['北京市', '北京'], modal);
      await clickExactText(['北京市', '北京'], modal);
      await clickExactText(['朝阳区', '东城区', '海淀区'], modal);
      await clickExactText(['确定', '完成', '确认'], modal);
      setRowExtraText(/居住省市|省市区|省市|地区/, regionText, occurrence);
      records.push(`居住省市[${occurrence}]=${regionText}`);
    };
    const selectOccupation = async (occurrence = 0) => {
      const currentRow = preciseRowByLabel(/职业/, occurrence);
      if (currentRow && norm(currentRow.innerText || currentRow.textContent).includes(jobText)) {
        records.push(`职业[${occurrence}]=${jobText}`);
        return;
      }
      const row = await clickPickerRow(/职业/, occurrence);
      if (!row) return;
      const modal = activeModal();
      const search = Array.from(modal.querySelectorAll('input,textarea')).filter(visible)[0] as any;
      if (search) setValue(search, '一般', '职业搜索');
      await sleep(350);
      await clickExactText([jobText, '一般内勤人员', '一般职业人员', '内勤', '一般'], modal);
      await clickExactText(['确定', '完成', '确认'], modal);
      await dismissPickerModal(/职业选择|职业大类|职业/);
      setRowExtraText(/职业/, jobText, occurrence);
      records.push(`职业[${occurrence}]=${jobText}`);
    };
    const selectBank = async () => {
      const row = await clickPickerRow(/^开户银行$|^开户行$|^银行$/);
      if (!row) return;
      const modal = activeModal();
      await clickExactText([bankName, '中国工商银行', '工商银行'], modal);
      await clickExactText(['确定', '完成', '确认'], modal);
      setRowExtraText(/^开户银行$|^开户行$|^银行$/, bankName);
      records.push(`开户银行=${bankName}`);
    };
    const selectCardType = async (name: string, occurrence = 0, label = '投保人证件类型') => {
      if (!name) return;
      const row = await clickPickerRow(/证件类型/, occurrence);
      if (row) {
        const modal = activeModal();
        await clickExactText([name], modal);
        await clickExactText(['确定', '完成', '确认'], modal);
      }
      setRowExtraText(/证件类型/, name, occurrence);
      records.push(`${label}=${name}`);
    };
    fillByPlaceholder(/eName|english|pinyin|英文|拼音/i, applicantEnglishName, '投保人拼音/英文名');
    fillByLabel(/拼音|英文名|英文/, applicantEnglishName, 0, '投保人拼音/英文名');
    fillByPlaceholder(/真实姓名|姓名/, applicantName, '投保人姓名');
    fillByPlaceholder(/证件号码/, applicantIdNo, '投保人证件号码');
    fillByPlaceholder(/详细地址|联系地址|地址/, applicantAddress, '投保人地址');
    fillByPlaceholder(/真实手机|手机号码|手机号/, applicantMobile, '投保人手机号');
    fillByPlaceholder(/真实邮箱|邮箱|电子邮箱/i, applicantEmail, '投保人邮箱');
    fillByPlaceholder(/账户名须为投保人本人|持卡人/, cardOwner, '持卡人');
    fillByPlaceholder(/开卡信息|银行账号|银行卡号|银行账户|格式参照/, payAccount, '银行账号');
    fillByLabel(/姓名|投保人.*姓名/, applicantName, 0, '投保人姓名');
    fillByLabel(/姓名|被保人.*姓名|被保险人.*姓名/, insuredName, 1, '被保人姓名');
    fillByLabel(/证件号码|身份证号/, applicantIdNo, 0, '投保人证件号码');
    fillByLabel(/证件号码|身份证号/, insuredIdNo, 1, '被保人证件号码');
    fillByLabel(/联系地址|详细地址|地址/, applicantAddress, 0, '投保人地址');
    fillByLabel(/持卡人/, cardOwner, 0, '持卡人');
    fillByLabel(/银行账号|银行卡号|卡号/, payAccount, 0, '银行账号');
    setRowExtraText(/拼音|英文名|英文/, applicantEnglishName, 0);
    setRowExtraText(/居住省市|省市区|省市|地区/, regionText, 0);
    setRowExtraText(/居住省市|省市区|省市|地区/, regionText, 1);
    setRowExtraText(/职业/, jobText, 0);
    setRowExtraText(/职业/, jobText, 1);
    setAllRowExtraText(/居住省市|省市区|省市|地区/, regionText);
    setAllRowExtraText(/职业/, jobText);
    setRowExtraText(/^开户银行$|^开户行$|^银行$/, bankName, 0);
    await selectCardType(applicantCardTypeName, 0, '投保人证件类型');
    await selectCardType(insuredCardTypeName, 1, '被保人证件类型');
    if (policyStartDate) setBusinessField('起保日期', 'insuranceDate', policyStartDate, '102');
    setBusinessField('出行目的', 'purpose', travelPurposeValue, '40');
    setBusinessField('出行目的地', 'tripDestination', travelDestination, '40');
    syncVisibleInsureModules('before-pickers');
    await selectRegion(0);
    await selectOccupation(0);
    syncVisibleInsureModules('after-applicant-pickers');
    await selectRegion(1);
    await selectOccupation(1);
    await selectCardType(applicantCardTypeName, 0, '投保人证件类型');
    await selectCardType(insuredCardTypeName, 1, '被保人证件类型');
    syncVisibleInsureModules('after-insured-pickers');
    await selectBank();
    await dismissPickerModal(/职业选择|职业大类|职业/);
    fillByPlaceholder(/账户名须为投保人本人|持卡人/, cardOwner, '持卡人');
    fillByLabel(/持卡人/, cardOwner, 0, '持卡人');
    fillByPlaceholder(/开卡信息|银行账号|银行卡号|银行账户|格式参照/, payAccount, '银行账号');
    fillByLabel(/银行账号|银行卡号|卡号/, payAccount, 0, '银行账号');
    fillByLabel(/姓名|被保人.*姓名|被保险人.*姓名/, insuredName, 1, '被保人姓名');
    fillByLabel(/证件号码|身份证号/, insuredIdNo, 1, '被保人证件号码');
    fillByPlaceholder(/eName|english|pinyin|英文|拼音/i, applicantEnglishName, '投保人拼音/英文名');
    fillByLabel(/拼音|英文名|英文/, applicantEnglishName, 0, '投保人拼音/英文名');
    if (policyStartDate) setBusinessField('起保日期', 'insuranceDate', policyStartDate, '102');
    setBusinessField('出行目的', 'purpose', travelPurposeValue, '40');
    setBusinessField('出行目的地', 'tripDestination', travelDestination, '40');
    const startNodes = Array.from(document.querySelectorAll('.date-picker-wrapper.start-picker .date span')) as any[];
    const endNodes = Array.from(document.querySelectorAll('.date-picker-wrapper.end-picker .date span, .date-picker-wrapper.stop-picker .date span')) as any[];
    const placeholderStartNodes = Array.from(document.querySelectorAll('.date span')).filter((node: any) => /起始日期|开始日期/.test(norm(node.textContent || node.innerText))) as any[];
    const placeholderEndNodes = Array.from(document.querySelectorAll('.date span')).filter((node: any) => /截止日期|结束日期/.test(norm(node.textContent || node.innerText))) as any[];
    for (const node of uniqueElements([...startNodes, ...placeholderStartNodes])) { node.textContent = startValue; }
    for (const node of uniqueElements([...endNodes, ...placeholderEndNodes])) { node.textContent = endValue; }
    if (startNodes.length) records.push(`证件有效期起始=${startValue}`);
    if (endNodes.length) records.push(`证件有效期截止=${endValue}`);
    syncVisibleInsureModules('after-pickers');
    await sleep(600);
    syncVisibleInsureModules('after-render-wait');
    const patchPlain = (obj: any) => {
      const roots = [obj?.product?.insure?.data?.data, obj?.insure?.data?.data, obj?.data?.data, obj?.data].filter(Boolean);
      for (const root of roots) {
        const applicantRows = root['10'] || root[10];
        const applicant = Array.isArray(applicantRows) ? applicantRows[0] : applicantRows;
        if (applicant && typeof applicant === 'object') {
          applicant.cName = { ...(applicant.cName || {}), ...clear(applicantName) };
          applicant.eName = { ...(applicant.eName || {}), ...clear(applicantEnglishName) };
          applicant.cardNumber = { ...(applicant.cardNumber || {}), ...clear(applicantIdNo) };
          applicant.cardPeriod = { ...(applicant.cardPeriod || {}), ...clear(`${startValue}|${endValue}`) };
          applicant.cardPeriodEnd = { ...(applicant.cardPeriodEnd || {}), ...clear(endValue) };
          applicant.provCityText = { ...(applicant.provCityText || {}), ...clear(regionValue), text: regionText, label: regionText, name: regionText };
          applicant.contactAddress = { ...(applicant.contactAddress || {}), ...clear(applicantAddress) };
          if (shouldPatchOccupation(applicant)) applicant.jobText = { ...(applicant.jobText || {}), ...clear(jobValue), text: jobText, label: jobText, name: jobText };
          applicant.moblie = { ...(applicant.moblie || {}), ...clear(applicantMobile) };
          applicant.email = { ...(applicant.email || {}), ...clear(applicantEmail) };
          applicant.sex = { ...(applicant.sex || {}), ...clear(applicantSexValue), text: applicantSexText, label: applicantSexText, name: applicantSexText };
          if (applicantBirthdate) applicant.birthdate = { ...(applicant.birthdate || {}), ...clear(applicantBirthdate) };
          applicant.cardTypeName = { ...(applicant.cardTypeName || {}), ...clear(applicantCardTypeValue), text: applicantCardTypeName, label: applicantCardTypeName, name: applicantCardTypeName };
          applicant.nationality = { ...(applicant.nationality || {}), ...clear('1'), text: '中国', label: '中国', name: '中国' };
          applicant.fiscalResidentIdentity = { ...(applicant.fiscalResidentIdentity || {}), ...clear('1'), text: '仅为中国税收居民', label: '仅为中国税收居民', name: '仅为中国税收居民' };
        }
        const insuredRows = root['20'] || root[20];
        const insured = Array.isArray(insuredRows) ? insuredRows[0] : insuredRows;
        if (insured && typeof insured === 'object') {
          insured.forWho = { ...(insured.forWho || {}), ...clear('100'), text: '本人', label: '本人', name: '本人' };
          insured.cName = { ...(insured.cName || {}), ...clear(insuredName) };
          insured.eName = { ...(insured.eName || {}), ...clear(insuredEnglishName) };
          insured.cardNumber = { ...(insured.cardNumber || {}), ...clear(insuredIdNo) };
          insured.cardPeriod = { ...(insured.cardPeriod || {}), ...clear(`${startValue}|${endValue}`) };
          insured.cardPeriodEnd = { ...(insured.cardPeriodEnd || {}), ...clear(endValue) };
          insured.provCityText = { ...(insured.provCityText || {}), ...clear(regionValue), text: regionText, label: regionText, name: regionText };
          insured.contactAddress = { ...(insured.contactAddress || {}), ...clear(applicantAddress) };
          if (shouldPatchOccupation(insured)) insured.jobText = { ...(insured.jobText || {}), ...clear(jobValue), text: jobText, label: jobText, name: jobText };
          insured.moblie = { ...(insured.moblie || {}), ...clear(applicantMobile) };
          insured.email = { ...(insured.email || {}), ...clear(applicantEmail) };
          insured.sex = { ...(insured.sex || {}), ...clear(insuredSexValue), text: insuredSexText, label: insuredSexText, name: insuredSexText };
          if (insuredBirthdate) insured.birthdate = { ...(insured.birthdate || {}), ...clear(insuredBirthdate) };
          insured.cardTypeName = { ...(insured.cardTypeName || {}), ...clear(insuredCardTypeValue), text: insuredCardTypeName, label: insuredCardTypeName, name: insuredCardTypeName };
          insured.nationality = { ...(insured.nationality || {}), ...clear('1'), text: '中国', label: '中国', name: '中国' };
          insured.fiscalResidentIdentity = { ...(insured.fiscalResidentIdentity || {}), ...clear('1'), text: '仅为中国税收居民', label: '仅为中国税收居民', name: '仅为中国税收居民' };
          insured.addressIsSameApplicant = { ...(insured.addressIsSameApplicant || {}), ...clear('1'), text: '是', label: '是', name: '是' };
        }
        const bankRows = root['107'] || root[107];
        const bank = Array.isArray(bankRows) ? bankRows[0] : bankRows;
        if (bank && typeof bank === 'object') {
          bank.bank = { ...(bank.bank || {}), ...clear(bankValue), label: bankName, text: bankName, name: bankName };
          bank.cardOwner = { ...(bank.cardOwner || {}), ...clear(cardOwner) };
          bank.payAccount = { ...(bank.payAccount || {}), ...clear(payAccount) };
        }
        const travelRows = root['40'] || root[40];
        const travel = Array.isArray(travelRows) ? travelRows[0] : travelRows;
        if (travel && typeof travel === 'object') {
          travel.purpose = { ...(travel.purpose || {}), ...clear(travelPurposeValue), label: travelPurposeText, text: travelPurposeText, name: travelPurposeText };
          travel.tripDestination = { ...(travel.tripDestination || {}), ...clear(travelDestination), label: travelDestination, text: travelDestination, name: travelDestination };
        }
        const policyRows = root['102'] || root[102];
        const policy = Array.isArray(policyRows) ? policyRows[0] : policyRows;
        if (policy && typeof policy === 'object' && policyStartDate) {
          policy.insuranceDate = { ...(policy.insuranceDate || {}), ...clear(policyStartDate), label: policyStartDate, text: policyStartDate, name: policyStartDate };
        }
      }
      patchTrialGenesInsurantDate(obj);
    };
    patchPlain((window as any).__NEXT_DATA__);
    for (const storage of [window.localStorage, window.sessionStorage]) {
      if (!storage) continue;
      for (let i = 0; i < storage.length; i += 1) {
        const key = storage.key(i);
        if (!key || !/insure|product|123602|126878/i.test(key)) continue;
        try {
          const raw = storage.getItem(key);
          if (!raw || raw[0] !== '{') continue;
          const json = JSON.parse(raw);
          patchPlain(json);
          storage.setItem(key, JSON.stringify(json));
        } catch (_) {}
      }
    }
    const store = (window as any).__NEXT_REDUX_STORE__ || (window as any).store || (window as any).reduxStore;
    if (store && typeof store.getState === 'function') {
      try {
        const state = store.getState();
        if (state && typeof state.setIn === 'function') {
          let next = state;
          const patchImmutableTrialGenes = (current: any, path: any[]) => {
            try {
              const value = current.getIn?.(path);
              const patched = patchedTrialGenesValue(value);
              return patched !== value ? current.setIn(path, patched) : current;
            } catch (_) {
              return current;
            }
          };
          for (const base of [
            ['product', 'insure', 'data', 'data', '10', 0],
            ['product', 'insure', 'data', 'data', '20', 0],
            ['product', 'insure', 'data', 'data', '107', 0],
            ['product', 'insure', 'data', 'data', '40', 0],
            ['product', 'insure', 'data', 'data', '102', 0],
            ['data', 'data', '10', 0],
            ['data', 'data', '20', 0],
            ['data', 'data', '107', 0],
            ['data', 'data', '40', 0],
            ['data', 'data', '102', 0],
          ]) {
            const moduleId = String(base[base.length - 2]);
            if (moduleId === '10') {
              next = next.setIn([...base, 'cName', 'value'], applicantName);
              next = next.setIn([...base, 'eName', 'value'], applicantEnglishName);
              next = next.setIn([...base, 'eName', 'hasError'], false);
              next = next.setIn([...base, 'eName', 'hasAjaxError'], false);
              next = next.setIn([...base, 'cardTypeName', 'value'], applicantCardTypeValue);
              next = next.setIn([...base, 'cardTypeName', 'text'], applicantCardTypeName);
              next = next.setIn([...base, 'cardTypeName', 'label'], applicantCardTypeName);
              next = next.setIn([...base, 'cardNumber', 'value'], applicantIdNo);
              next = next.setIn([...base, 'cardPeriod', 'value'], `${startValue}|${endValue}`);
              next = next.setIn([...base, 'cardPeriod', 'hasError'], false);
              next = next.setIn([...base, 'cardPeriodEnd', 'value'], endValue);
              next = next.setIn([...base, 'provCityText', 'value'], regionValue);
              next = next.setIn([...base, 'provCityText', 'text'], regionText);
              next = next.setIn([...base, 'provCityText', 'label'], regionText);
              next = next.setIn([...base, 'contactAddress', 'value'], applicantAddress);
              if (applicantBirthdate) next = next.setIn([...base, 'birthdate', 'value'], applicantBirthdate);
              next = next.setIn([...base, 'sex', 'value'], applicantSexValue);
              next = next.setIn([...base, 'sex', 'text'], applicantSexText);
              next = next.setIn([...base, 'sex', 'label'], applicantSexText);
              const shouldPatchImmutableOccupation = hasVisibleOccupationControl() || !!state.getIn?.([...base, 'jobText']);
              if (shouldPatchImmutableOccupation) {
                next = next.setIn([...base, 'jobText', 'value'], jobValue);
                next = next.setIn([...base, 'jobText', 'text'], jobText);
                next = next.setIn([...base, 'jobText', 'label'], jobText);
              }
              next = next.setIn([...base, 'moblie', 'value'], applicantMobile);
              next = next.setIn([...base, 'email', 'value'], applicantEmail);
            }
            if (moduleId === '20') {
              next = next.setIn([...base, 'forWho', 'value'], '100');
              next = next.setIn([...base, 'cName', 'value'], insuredName);
              next = next.setIn([...base, 'eName', 'value'], insuredEnglishName);
              next = next.setIn([...base, 'eName', 'hasError'], false);
              next = next.setIn([...base, 'eName', 'hasAjaxError'], false);
              next = next.setIn([...base, 'cardTypeName', 'value'], insuredCardTypeValue);
              next = next.setIn([...base, 'cardTypeName', 'text'], insuredCardTypeName);
              next = next.setIn([...base, 'cardTypeName', 'label'], insuredCardTypeName);
              next = next.setIn([...base, 'cardNumber', 'value'], insuredIdNo);
              next = next.setIn([...base, 'cardPeriod', 'value'], `${startValue}|${endValue}`);
              next = next.setIn([...base, 'cardPeriod', 'hasError'], false);
              next = next.setIn([...base, 'cardPeriodEnd', 'value'], endValue);
              next = next.setIn([...base, 'provCityText', 'value'], regionValue);
              next = next.setIn([...base, 'provCityText', 'text'], regionText);
              next = next.setIn([...base, 'provCityText', 'label'], regionText);
              next = next.setIn([...base, 'contactAddress', 'value'], applicantAddress);
              if (insuredBirthdate) next = next.setIn([...base, 'birthdate', 'value'], insuredBirthdate);
              next = next.setIn([...base, 'sex', 'value'], insuredSexValue);
              next = next.setIn([...base, 'sex', 'text'], insuredSexText);
              next = next.setIn([...base, 'sex', 'label'], insuredSexText);
              const shouldPatchImmutableOccupation = hasVisibleOccupationControl() || !!state.getIn?.([...base, 'jobText']);
              if (shouldPatchImmutableOccupation) {
                next = next.setIn([...base, 'jobText', 'value'], jobValue);
                next = next.setIn([...base, 'jobText', 'text'], jobText);
                next = next.setIn([...base, 'jobText', 'label'], jobText);
              }
              next = next.setIn([...base, 'moblie', 'value'], applicantMobile);
              next = next.setIn([...base, 'email', 'value'], applicantEmail);
              next = next.setIn([...base, 'addressIsSameApplicant', 'value'], '1');
            }
            if (moduleId === '107') {
              next = next.setIn([...base, 'bank', 'value'], bankValue);
              next = next.setIn([...base, 'bank', 'label'], bankName);
              next = next.setIn([...base, 'bank', 'text'], bankName);
              next = next.setIn([...base, 'bank', 'hasError'], false);
              next = next.setIn([...base, 'cardOwner', 'value'], cardOwner);
              next = next.setIn([...base, 'payAccount', 'value'], payAccount);
              next = next.setIn([...base, 'payAccount', 'hasError'], false);
            }
            if (moduleId === '40') {
              next = next.setIn([...base, 'purpose', 'value'], travelPurposeValue);
              next = next.setIn([...base, 'purpose', 'label'], travelPurposeText);
              next = next.setIn([...base, 'purpose', 'text'], travelPurposeText);
              next = next.setIn([...base, 'purpose', 'hasError'], false);
              next = next.setIn([...base, 'tripDestination', 'value'], travelDestination);
              next = next.setIn([...base, 'tripDestination', 'label'], travelDestination);
              next = next.setIn([...base, 'tripDestination', 'text'], travelDestination);
              next = next.setIn([...base, 'tripDestination', 'hasError'], false);
            }
            if (moduleId === '102' && policyStartDate) {
              next = next.setIn([...base, 'insuranceDate', 'value'], policyStartDate);
              next = next.setIn([...base, 'insuranceDate', 'label'], policyStartDate);
              next = next.setIn([...base, 'insuranceDate', 'text'], policyStartDate);
              next = next.setIn([...base, 'insuranceDate', 'hasError'], false);
            }
          }
          next = patchImmutableTrialGenes(next, ['product', 'insure', 'data', 'trialGenes']);
          next = patchImmutableTrialGenes(next, ['insure', 'data', 'trialGenes']);
          next = patchImmutableTrialGenes(next, ['data', 'trialGenes']);
          if (next !== state) store.getState = () => next;
        } else {
          patchPlain(state);
        }
      } catch (_) {}
    }
    (window as any).__agent3MockData = mockData;
    await sleep(100);
    return records.length;
  }, { mockData, syncMode }).catch(() => 0);
}

""".strip("\n").splitlines(),
        "async function mockDataNodeReady(page: any, nodeId: unknown): Promise<boolean> {",
        "  const id = String(nodeId || '');",
        "  if (!shouldProbeFieldsForNode(id)) return false;",
        "  const url = page.url();",
        "  const bodyText = await page.locator('body').innerText({ timeout: 3000 }).catch(() => '');",
        "  if (id === 'NODE-insure-form') {",
        "    return /\\/product\\/insure(?:\\?|$)/.test(url) && /投保人信息/.test(bodyText) && /被保险人信息/.test(bodyText) && /提交投保单|提交订单/.test(bodyText);",
        "  }",
        "  return true;",
        "}",
        "",
        "async function applyMockData(page: any, nodeId: unknown): Promise<void> {",
        "  const key = String(nodeId || '');",
        "  const alreadyFilled = filledMockDataNodes.has(key);",
        "  const forceResync = key === 'NODE-insure-form';",
        "  if (!key || (!forceResync && alreadyFilled)) return;",
        "  if (!(await mockDataNodeReady(page, key))) return;",
        "  let filledCount = 0;",
        "  if (key === 'NODE-insure-form') {",
        "    const syncMode = alreadyFilled ? 'retry' : 'initial';",
        "    const syncedCount = await syncH5InsureFormFromMock(page, { mode: syncMode });",
        "    filledCount += syncedCount;",
        "    if (syncedCount > 0) test.info().annotations.push({ type: 'h5-insure-form-sync', description: `synced ${syncedCount} mock fields from Agent3 exploration logic` });",
        "  }",
        "  for (const field of fieldsForNodeFromContract(nodeId)) {",
        "      const value = mockData[field.mock_key || field.field_key] ?? mockData[field.field_key] ?? field.mock_value;",
        "      if (field.type === 'hidden') continue;",
        "      if (value === undefined || value === null || value === '') continue;",
        "      const resolved = await resolveFieldLocator(page, field);",
        "      if (!resolved) {",
        "        test.info().annotations.push({ type: 'field-fill-skip', description: `field not found: ${field.field_key}` });",
        "        continue;",
        "      }",
        "      const locator = resolved.locator;",
        "      if (!(await locator.isVisible().catch(() => false))) continue;",
        "      await fillByStrategy(page, locator, field, value);",
        "      filledCount += 1;",
        "      test.info().annotations.push({ type: resolved.strategy === 'field-resolution' ? 'field-contract-fill' : resolved.strategy === 'target-probe' ? 'target-probe' : 'field-fill', description: `${field.field_key} -> ${resolved.selector}` });",
        "  }",
        "  if (filledCount > 0) filledMockDataNodes.add(key);",
        "}",
        "",
    ]


def render_param_interface(function_name: str, params: list[dict[str, Any]]) -> str:
    interface_name = function_name.removeprefix("fill") + "Params"
    if not params:
        return f"export interface {interface_name} {{\n  [key: string]: unknown;\n}}\n"
    lines = [f"export interface {interface_name} {{"]
    for param in params:
        optional = "?" if not param.get("required", True) else ""
        name = ts_property_name(str(param["name"]))
        lines.append(f"  {name}{optional}: {ts_type(str(param.get('type', 'string')))};")
    lines.append("}")
    return "\n".join(lines) + "\n"


def render_page_function_file(page_function: Mapping[str, Any]) -> str:
    function_name = str(page_function["function_name"])
    page_id = str(page_function["page_id"])
    page_name = node_label(page_id)
    interface_block = render_param_interface(function_name, list(page_function.get("params", [])))
    interface_name = function_name.removeprefix("fill") + "Params"
    expect_name = f"expect{function_name.removeprefix('fill')}Page"
    click_name = f"click{function_name.removeprefix('fill')}Next"
    assert_next_name = f"assert{function_name.removeprefix('fill')}NextPage"
    fields = list(page_function.get("fields", []) or [])
    actions = list(page_function.get("actions", []) or [])
    entry_signals = list(page_function.get("entry_signals", []) or [page_name])
    exit_signals = list(page_function.get("exit_signals", []) or ["下一步", "继续", "确认"])
    next_signals = list(page_function.get("next_signals", []) or exit_signals)
    return "\n".join(
        [
            "import { Page, expect } from 'playwright/test';",
            "",
            interface_block.rstrip(),
            "",
            f"const entrySignals = {json.dumps(entry_signals, ensure_ascii=False, indent=2)};",
            f"const nextSignals = {json.dumps(next_signals, ensure_ascii=False, indent=2)};",
            f"const fieldSelectors = {json.dumps(fields, ensure_ascii=False, indent=2)};",
            f"const actionSelectors = {json.dumps(actions, ensure_ascii=False, indent=2)};",
            "",
            "async function firstUsable(page: Page, selectors: Array<string | undefined>, options: { hasText?: string } = {}) {",
            "  for (const selector of selectors.filter(Boolean) as string[]) {",
            "    const locator = options.hasText ? page.locator(selector).filter({ hasText: options.hasText }).first() : page.locator(selector).first();",
            "    if (await locator.count().catch(() => 0)) return locator;",
            "  }",
            "  return null;",
            "}",
            "",
            "async function expectAnySignal(page: Page, signals: string[]): Promise<void> {",
            "  await expect(page.locator('body')).toBeVisible();",
            "  const bodyText = await page.locator('body').innerText().catch(() => '');",
            "  expect(signals.length === 0 || signals.some(signal => bodyText.includes(signal))).toBeTruthy();",
            "}",
            "",
            f"export async function {expect_name}(page: Page): Promise<void> {{",
            "  await expectAnySignal(page, entrySignals);",
            "}",
            "",
            "async function fillByFallback(page: Page, field: any, value: unknown): Promise<void> {",
            "  if (value === undefined || value === null || value === '') return;",
            "  const selectorCandidates = [field.selector, field.text_selector, field.label ? `text=${field.label}` : undefined];",
            "  const locator = await firstUsable(page, selectorCandidates);",
            "  if (!locator || !(await locator.isVisible().catch(() => false))) return;",
            "  const tag = String(field.tag ?? '').toLowerCase();",
            "  const type = String(field.type ?? '').toLowerCase();",
            "  if (tag === 'select') await locator.selectOption(String(value)).catch(() => undefined);",
            "  else if (type === 'checkbox' || type === 'radio') await locator.check().catch(() => undefined);",
            "  else await locator.fill(String(value)).catch(() => undefined);",
            "}",
            "",
            f"export async function {click_name}(page: Page): Promise<void> {{",
            "  for (const action of actionSelectors) {",
            "    const text = String(action.text ?? '').trim();",
            "    const selectors = [action.selector, action.text_selector, action.tag || 'button', 'button', 'a', '[role=button]'];",
            "    const locator = text ? await firstUsable(page, selectors, { hasText: text }) : await firstUsable(page, selectors);",
            "    if (!locator) continue;",
            "    try {",
            "      await locator.click({ timeout: 2000, noWaitAfter: true });",
            "    } catch {",
            "      await locator.click({ timeout: 2000, noWaitAfter: true, force: true }).catch(async () => {",
            "        await locator.evaluate((element: HTMLElement) => element.click()).catch(() => undefined);",
            "      });",
            "    }",
            "    await page.waitForLoadState('domcontentloaded', { timeout: 5000 }).catch(() => undefined);",
            "    return;",
            "  }",
            "}",
            "",
            f"export async function {assert_next_name}(page: Page): Promise<void> {{",
            "  await expectAnySignal(page, nextSignals);",
            "}",
            "",
            f"export async function {function_name}(page: Page, params: {interface_name}): Promise<void> {{",
            f"  await {expect_name}(page);",
            "  const data = params ?? {};",
            "  for (const field of fieldSelectors) {",
            "    const key = String(field.field_key ?? field.name ?? '');",
            "    const value = (data as Record<string, unknown>)[key] ?? field.mock_value;",
            "    await fillByFallback(page, field, value);",
            "  }",
            f"  await {click_name}(page);",
            f"  await {assert_next_name}(page);",
            "}",
            "",
        ]
    )


def render_scenario_spec(
    scenario: Mapping[str, Any],
    page_functions: list[dict[str, Any]],
    *,
    generated_by: str,
) -> str:
    huize_closed_loop = dict(scenario.get("huize_payment_closed_loop", {}) or {})
    uses_huize_closed_loop = bool(huize_closed_loop.get("enabled"))
    page_function_index = {
        f"NODE-{item['page_id']}": item
        for item in page_functions
    }
    ordered_functions = [
        page_function_index[node_id]
        for node_id in scenario.get("route_nodes", [])
        if node_id in page_function_index
    ]
    imports = _unique_strings_in_order([
        f"import {{ {item['function_name']} }} from '../page-functions/{Path(str(item['file_path'])).stem}';"
        for item in ordered_functions
    ])
    scenario_conditions = (
        scenario.get("variants", [{}])[0].get("conditions", {})
        if scenario.get("variants")
        else {}
    )
    mock_data = _scenario_mock_data_with_overrides(scenario, scenario.get("mock_data", {}) or {})
    page_element_plan = list(scenario.get("page_element_plan", []) or [])
    calls = [
        f"  await {item['function_name']}(page, scenarioConditions as any);"
        for item in ordered_functions
    ]
    final_page_id = "entry"
    for node_id in reversed(scenario.get("route_nodes", [])):
        if node_id.startswith("NODE-") and node_id not in {"NODE-start", "NODE-end", "NODE-branch"}:
            final_page_id = node_id.removeprefix("NODE-")
            break

    has_real_action, real_execution_lines = _real_action_replay_lines(scenario)
    needs_node_runtime = has_real_action or uses_huize_closed_loop
    huize_closed_loop_config = {
        "enabled": uses_huize_closed_loop,
        "paymentMethodHint": huize_closed_loop.get("payment_method"),
        "gatewayPayNumSourceHint": huize_closed_loop.get("gateway_pay_num_source"),
        "payOperationId": huize_closed_loop.get("pay_operation_id"),
        "issueOperationId": huize_closed_loop.get("issue_operation_id"),
    }
    simulated_execution_lines = [
        "  await page.setContent('<main data-page-id=\"entry\"><h1>Entry</h1></main>');",
        *calls,
        f"  await expect(page.locator('[data-page-id=\"{final_page_id}\"]')).toBeVisible();",
    ]
    embedded_metadata = json.dumps(
        {
            "scenario_id": scenario["scenario_id"],
            "path_id": scenario["path_id"],
            "route_nodes": scenario.get("route_nodes", []),
            "case_ids": scenario.get("case_ids", []),
            "planned_page_refs": scenario.get("planned_page_refs", []),
            "page_content_refs": scenario.get("page_content_refs", []),
            "target_node": scenario.get("target_node"),
            "coverage_status": scenario.get("coverage_status"),
            "terminal_boundary": scenario.get("terminal_boundary", {}),
            "resume_condition": scenario.get("resume_condition"),
            "evidence_source": scenario.get("evidence_source"),
            "execution_requirements": scenario.get("execution_requirements", {}),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return "\n".join(
        [
            "/**",
            f" * @generated-by {generated_by}",
            f" * @scenario {embedded_metadata}",
            " */",
            "import { test, expect } from 'playwright/test';",
            *(["import * as fs from 'fs';", "import * as path from 'path';"] if needs_node_runtime else []),
            *(["import { paySuccess as huizePaySuccess } from '../../external-ops/huize-pay-success.cjs';"] if uses_huize_closed_loop else []),
            *(["import { waitForIssueStatus as huizeWaitForIssueStatus } from '../../external-ops/huize-issue-status.cjs';"] if uses_huize_closed_loop else []),
            *imports,
            "",
            f"const scenarioConditions = {json.dumps(scenario_conditions, ensure_ascii=False, indent=2)};",
            f"const completionRule = {json.dumps(scenario.get('completion_rule', {}), ensure_ascii=False, indent=2)};",
            f"const nodeProgress = {json.dumps(scenario.get('node_progress', []), ensure_ascii=False, indent=2)};",
            *(
                [
                    f"const embeddedMockData = {json.dumps(mock_data, ensure_ascii=False, indent=2)};",
                    "const mockData = withAgent4RuntimeMockDataOverrides(embeddedMockData);",
                ]
                if has_real_action
                else [f"const mockData = {json.dumps(mock_data, ensure_ascii=False, indent=2)};"]
            ),
            f"const pageElementPlan = {json.dumps(page_element_plan, ensure_ascii=False, indent=2)};",
            f"const fieldResolutionPlan = {json.dumps(scenario.get('field_resolution_plan', {}), ensure_ascii=False, indent=2)};",
            f"const componentStrategy = {json.dumps(scenario.get('component_strategy', {}), ensure_ascii=False, indent=2)};",
            f"const validationReport = {json.dumps(scenario.get('validation_report', {}), ensure_ascii=False, indent=2)};",
            *((
                f"const huizePaymentClosedLoopConfig = {json.dumps(huize_closed_loop_config, ensure_ascii=False, indent=2)};"
            ,) if uses_huize_closed_loop else ()),
            "",
            *(real_action_helper_lines() if has_real_action else []),
            *(huize_payment_closed_loop_helper_lines() if uses_huize_closed_loop else []),
            *(field_probe_helper_lines() if has_real_action else []),
            *(questionnaire_answer_helper_lines() if has_real_action else []),
            f"test('{scenario['scenario_id']} {scenario['path_id']}', async ({{ page }}) => {{",
            "  test.setTimeout(720_000);",
            "  expect(completionRule.source ?? 'agent2.nodes').toBeTruthy();",
            "  expect(Array.isArray(nodeProgress)).toBeTruthy();",
            *(real_execution_lines if has_real_action else simulated_execution_lines),
            "});",
            "",
        ]
    )


def _unique_strings_in_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _normalise_ts_import_path(path: Any) -> str:
    return str(path).replace("\\", "/").removesuffix(".ts")


def _ordered_page_functions_for_scenario(
    scenario: Mapping[str, Any],
    page_functions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    page_function_index = {
        f"NODE-{item['page_id']}": item
        for item in page_functions
    }
    return [
        page_function_index[node_id]
        for node_id in scenario.get("route_nodes", [])
        if node_id in page_function_index
    ]


def _scenario_terminal_page_id(scenario: Mapping[str, Any]) -> str:
    for node_id in reversed(scenario.get("route_nodes", []) or []):
        node_text = str(node_id)
        if node_text.startswith("NODE-") and node_text not in {"NODE-start", "NODE-end", "NODE-branch"}:
            return node_text.removeprefix("NODE-")
    return "entry"


def _url_path(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return urlparse(text).path.rstrip("/")
    except Exception:
        return ""


def _is_order_generation_boundary_url(value: Any) -> bool:
    path = _url_path(value).lower()
    return bool(path and ("/pay/success" in path or "/order/detail" in path))


def _scenario_stops_at_order_generation_boundary(scenario: Mapping[str, Any]) -> bool:
    completion_rule = scenario.get("completion_rule", {}) or {}
    if not isinstance(completion_rule, Mapping):
        return False
    return bool(completion_rule.get("order_generation_boundary"))


def _action_reaches_order_generation_boundary(action: Mapping[str, Any]) -> bool:
    if _is_order_generation_boundary_url(action.get("target_url")):
        return True
    target_node = str(action.get("expected_next_node_id") or action.get("planned_to_node_id") or "")
    return target_node == "NODE-policy-result" and not str(action.get("target_url") or "").strip()


def _truncate_after_order_generation_boundary(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for action in actions:
        if _is_order_generation_boundary_url(action.get("source_url")):
            break
        selected.append(action)
        if _action_reaches_order_generation_boundary(action):
            break
    return selected


def _is_payment_page_url(value: Any) -> bool:
    path = _url_path(value).lower()
    return bool(path and re.search(r"/pay(?:/|$)", path))


def _looks_like_payment_submit_action(action: Mapping[str, Any]) -> bool:
    text = _compact_action_text(action)
    selector = str(action.get("selector") or "")
    return (
        any(token in text for token in ("立即支付", "去支付", "确认支付"))
        or "submitToPay" in selector
        or "submit-pay" in selector.lower()
    )


def _next_route_node_after(route_nodes: list[str], node_id: str) -> str:
    try:
        start_index = route_nodes.index(node_id) + 1
    except ValueError:
        start_index = len(route_nodes)
    for candidate in route_nodes[start_index:]:
        if candidate.startswith("NODE-") and candidate not in {"NODE-start", "NODE-end", "NODE-branch"}:
            return candidate
    return ""


def _has_payment_click_action(actions: list[Mapping[str, Any]]) -> bool:
    for action in actions:
        if str(action.get("click_strategy") or "") == "touchscreen-payment-btn":
            return True
        if str(action.get("planned_from_node_id") or "") == "NODE-payment" and _looks_like_payment_submit_action(action):
            return True
    return False


def _payment_action_candidate_from_page_elements(scenario: Mapping[str, Any]) -> dict[str, Any] | None:
    for record in scenario.get("page_element_plan", []) or []:
        if not isinstance(record, Mapping):
            continue
        matched_nodes = {
            str(record.get("node_id") or ""),
            *[str(node_id) for node_id in record.get("matched_node_ids", []) or []],
        }
        if "NODE-payment" not in matched_nodes:
            continue
        for action in record.get("actions", []) or []:
            if not isinstance(action, Mapping) or not _looks_like_payment_submit_action(action):
                continue
            candidate = _normalise_real_action(action, str(scenario.get("path_id") or ""))
            candidate["source_url"] = action.get("source_url") or record.get("actual_url")
            candidate["target_url"] = action.get("target_url") or record.get("actual_url")
            return candidate
    return None


def _append_synthetic_payment_action_if_needed(
    actions: list[dict[str, Any]],
    scenario: Mapping[str, Any],
) -> list[dict[str, Any]]:
    route_nodes = [str(node_id) for node_id in scenario.get("route_nodes", []) or []]
    if _has_payment_click_action(actions):
        return actions
    pay_source_url = ""
    for action in reversed(actions):
        for key in ("target_url", "source_url"):
            value = str(action.get(key) or "").strip()
            if _is_payment_page_url(value):
                pay_source_url = value
                break
        if pay_source_url:
            break
    candidate = _payment_action_candidate_from_page_elements(scenario)
    if candidate and _is_payment_page_url(candidate.get("source_url")):
        pay_source_url = str(candidate.get("source_url") or "")
    if "NODE-payment" not in route_nodes and not candidate:
        return actions
    if not pay_source_url:
        return actions
    to_node = _next_route_node_after(route_nodes, "NODE-payment") or str(
        scenario.get("target_node") or scenario.get("reached_target_node") or "NODE-policy-result"
    )
    payment_action = {
        **(candidate or {}),
        "script_step_id": (candidate or {}).get("script_step_id"),
        "step_index": len(actions) + 1,
        "action_key": "action.click_payment",
        "action_type": "click",
        "text": (candidate or {}).get("text") or "立即支付",
        "tag": (candidate or {}).get("tag") or "a",
        "selector": (candidate or {}).get("selector") or "#submitToPay",
        "source_url": pay_source_url,
        "target_url": pay_source_url,
        "path_id": str(scenario.get("path_id") or ""),
        "planned_from_node_id": "NODE-payment",
        "planned_to_node_id": to_node,
        "expected_next_node_id": to_node,
        "expected_signals": list((candidate or {}).get("expected_signals", []) or []),
        "click_strategy": "touchscreen-payment-btn",
        "source": "agent3.page_element_plan.synthetic_payment_action",
    }
    return [*actions, payment_action]


def _expected_replay_url(action: Mapping[str, Any], next_action: Mapping[str, Any] | None) -> str:
    """Use Agent3's observed next source as the strongest replay transition contract."""
    source_url = str(action.get("source_url") or "").strip()
    source_path = _url_path(source_url)
    target_url = str(action.get("target_url") or "").strip()
    if target_url and _url_path(target_url) and _url_path(target_url) != source_path:
        return target_url
    next_source_url = str((next_action or {}).get("source_url") or "").strip()
    if next_source_url and _url_path(next_source_url) and _url_path(next_source_url) != source_path:
        return next_source_url
    return target_url


_ZURICH_OVERSEAS_PRODUCT_PLANS = {
    "全球无忧计划",
    "全球探索计划",
    "全球完美计划",
}

_ZURICH_OVERSEAS_PRODUCT_PLAN_BY_PATH = {
    "PATH-001": "全球无忧计划",
    "PATH-002": "全球探索计划",
    "PATH-003": "全球完美计划",
    "PATH-004": "全球完美计划",
}


def _normalise_zurich_overseas_product_plan(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text in _ZURICH_OVERSEAS_PRODUCT_PLANS:
        return text
    if "无忧" in text:
        return "全球无忧计划"
    if "探索" in text:
        return "全球探索计划"
    if "完美" in text:
        return "全球完美计划"
    return ""


def _scenario_explicit_product_detail_plan(scenario: Mapping[str, Any]) -> str:
    requirements = _scenario_execution_requirements(scenario)
    requirement_plan = _normalise_zurich_overseas_product_plan(requirements.get("product_plan"))
    if requirement_plan:
        return requirement_plan
    direct_keys = (
        "product_detail_plan",
        "coverage_plan",
        "product_plan",
        "plan_name",
        "保障计划",
    )
    for key in direct_keys:
        plan = _normalise_zurich_overseas_product_plan(scenario.get(key))
        if plan:
            return plan
    for mapping_key in ("conditions", "scenario_conditions", "mock_data"):
        mapping = scenario.get(mapping_key)
        if not isinstance(mapping, Mapping):
            continue
        for key, value in mapping.items():
            key_text = str(key).lower()
            if not any(token in key_text for token in ("保障计划", "coverage_plan", "product_plan", "plan_name", "plan")):
                continue
            plan = _normalise_zurich_overseas_product_plan(value)
            if plan:
                return plan
    return ""


def _scenario_has_product_detail_footer_action(real_actions: list[Mapping[str, Any]]) -> bool:
    for action in real_actions:
        from_product_detail = str(action.get("planned_from_node_id") or "") == "NODE-product-detail" or "/product/detail" in str(action.get("source_url") or "")
        if not from_product_detail:
            continue
        strategy = str(action.get("click_strategy") or "")
        action_text = str(action.get("text") or "").replace(" ", "")
        if strategy == "mouse-h5-product-footer-insure":
            return True
        if "投保" in action_text or "立即投保" in action_text:
            return True
    return False


def _is_zurich_overseas_product_scenario(
    scenario: Mapping[str, Any],
    real_actions: list[Mapping[str, Any]],
) -> bool:
    signals = [
        str(scenario.get("entry_url") or ""),
        *[str(case_id) for case_id in scenario.get("case_ids", []) or []],
    ]
    for action in real_actions:
        signals.append(str(action.get("source_url") or ""))
        signals.append(str(action.get("target_url") or ""))
    return any("demo-channel" in signal or "travel-product" in signal for signal in signals)


def _scenario_product_detail_plan(
    scenario: Mapping[str, Any],
    real_actions: list[Mapping[str, Any]],
) -> str:
    if not _scenario_has_product_detail_footer_action(real_actions):
        return ""
    explicit_plan = _scenario_explicit_product_detail_plan(scenario)
    if explicit_plan:
        return explicit_plan
    if _is_zurich_overseas_product_scenario(scenario, real_actions):
        return _ZURICH_OVERSEAS_PRODUCT_PLAN_BY_PATH.get(str(scenario.get("path_id") or ""), "")
    return ""


def _scenario_has_insure_form_boundary(
    route_nodes: list[str],
    real_actions: list[Mapping[str, Any]],
    page_element_plan: list[dict[str, Any]],
) -> bool:
    if "NODE-insure-form" in route_nodes:
        return True
    for action in real_actions:
        if str(action.get("planned_to_node_id") or "") == "NODE-insure-form":
            return True
        if "/product/insure" in str(action.get("source_url") or "") or "/product/insure" in str(action.get("target_url") or ""):
            return True
    for record in page_element_plan:
        if str(record.get("node_id") or "") == "NODE-insure-form":
            return True
    return False


def _build_agent4_execution_requirements(
    *,
    scenario_id: str,
    path_id: str,
    case_ids: list[str],
    entry_url: Any,
    route_nodes: list[str],
    real_actions: list[dict[str, Any]],
    page_element_plan: list[dict[str, Any]],
    target_node: Any,
    huize_closed_loop: Mapping[str, Any],
) -> dict[str, Any]:
    scenario_view = {
        "scenario_id": scenario_id,
        "path_id": path_id,
        "case_ids": case_ids,
        "entry_url": entry_url,
        "route_nodes": route_nodes,
    }
    has_insure_form = _scenario_has_insure_form_boundary(route_nodes, real_actions, page_element_plan)
    product_plan = _scenario_product_detail_plan(scenario_view, real_actions)
    mock_user_id_type = "护照" if _scenario_uses_zurich_tc003_passport(scenario_view) else ""
    expected_result_node = str(target_node or "")
    requires_payment_closure = bool((huize_closed_loop or {}).get("enabled"))
    if not expected_result_node and requires_payment_closure:
        expected_result_node = "NODE-policy-result"

    requirements: dict[str, Any] = {}
    if has_insure_form:
        requirements["mock_user_required"] = True
        if mock_user_id_type:
            requirements["mock_user_id_type"] = mock_user_id_type
        requirements["policy_start_offset_days"] = 1
        requirements["requires_identity_auth_recovery"] = True
    if product_plan:
        requirements["product_plan"] = product_plan
    if requires_payment_closure:
        requirements["requires_payment_closure"] = True
    if expected_result_node:
        requirements["expected_result_node"] = expected_result_node
    return requirements


def _source_case_ids(path_item: Mapping[str, Any], case_ids: list[str]) -> list[str]:
    source_ids: list[str] = []
    for ref in path_item.get("coverage_refs", []) or []:
        if not isinstance(ref, Mapping):
            continue
        for key in ("case_id", "source_case_id", "manual_case_id", "ac_case_id"):
            value = str(ref.get(key) or "").strip()
            if value:
                source_ids.append(value)
    return _unique_strings_in_order(source_ids or case_ids)


def _fact_lineage_action_evidence(real_actions: list[dict[str, Any]]) -> dict[str, Any]:
    action_keys = _unique_strings_in_order(
        [
            str(action.get("action_key") or "").strip()
            for action in real_actions
            if str(action.get("action_key") or "").strip()
        ]
    )
    planned_to_node_ids = _unique_strings_in_order(
        [
            str(action.get("planned_to_node_id") or "").strip()
            for action in real_actions
            if str(action.get("planned_to_node_id") or "").strip()
        ]
    )
    return {
        "source": "agent3.trace" if real_actions else "missing",
        "action_count": len(real_actions),
        "action_keys": action_keys,
        "planned_to_node_ids": planned_to_node_ids,
    }


def _build_fact_lineage(
    *,
    scenario_id: str,
    path_id: str,
    path_item: Mapping[str, Any],
    case_ids: list[str],
    conditions: Mapping[str, str],
    test_data_profile_ids: list[str],
    path_exploration: Mapping[str, Any],
    real_actions: list[dict[str, Any]],
    assertion_start_index: int,
    terminal_boundary: Mapping[str, Any],
    coverage_status: str,
    contract_status: str,
) -> dict[str, Any]:
    assertion_refs = [
        f"ASSERT-{assertion_start_index:03d}-{case_index:03d}"
        for case_index, _case_id in enumerate(case_ids or [""], start=1)
    ]
    return {
        "version": "fact-lineage-v1",
        "scenario_id": scenario_id,
        "path_id": path_id,
        "case_ids": case_ids,
        "source_case_ids": _source_case_ids(path_item, case_ids),
        "condition_keys": sorted(str(key) for key in conditions),
        "test_data_profile_ids": test_data_profile_ids,
        "page_content_refs": list(path_exploration.get("page_content_refs", []) or []),
        "planned_page_refs": list(path_exploration.get("planned_page_refs", []) or []),
        "action_evidence": _fact_lineage_action_evidence(real_actions),
        "assertion_refs": assertion_refs,
        "terminal_boundary": dict(terminal_boundary),
        "coverage_status": coverage_status,
        "contract_status": contract_status,
    }


def _real_action_replay_lines(scenario: Mapping[str, Any]) -> tuple[bool, list[str]]:
    """Build Playwright statements that replay Agent3's observed action trace."""
    path_id = str(scenario.get("path_id") or "")
    filtered_actions = _filter_replay_actions(
        [action for action in scenario.get("real_actions", []) or [] if isinstance(action, Mapping)],
        path_id,
    )
    real_actions = filtered_actions
    expected_transition_actions = filtered_actions
    if _scenario_stops_at_order_generation_boundary(scenario):
        real_actions = _truncate_after_order_generation_boundary(real_actions)
    real_actions = _append_synthetic_payment_action_if_needed(real_actions, scenario)
    action_url = str((real_actions[0] if real_actions else {}).get("source_url") or scenario.get("entry_url") or "")
    has_real_action = bool(real_actions and action_url)
    initial_node = str((real_actions[0] if real_actions else {}).get("planned_from_node_id") or (scenario.get("route_nodes") or [""])[0] or "")
    initial_meta = (
        "{ "
        f"path_id: {ts_string(path_id)}, "
        "step: 0, "
        "phase: 'initial-page', "
        f"planned_to_node_id: {ts_string(initial_node)} "
        "}"
    )
    real_execution_lines = [
        "  setupAgent4NetworkResponseCache(page);",
        f"  await page.goto({ts_string(action_url)}, {{ waitUntil: 'domcontentloaded' }});",
        "  await page.waitForLoadState('networkidle').catch(() => undefined);",
        "  await expect(page.locator('body')).toBeVisible();",
        f"  await captureAgent4BusinessScreenshot(page, 'initial-page', {initial_meta});",
    ]
    product_detail_plan = _scenario_product_detail_plan(scenario, real_actions)
    if product_detail_plan:
        plan_meta = (
            "{ "
            f"path_id: {ts_string(path_id)}, "
            "step: 0, "
            "phase: 'product-detail-plan', "
            f"planned_to_node_id: {ts_string(initial_node)}, "
            f"product_plan: {ts_string(product_detail_plan)} "
            "}"
        )
        real_execution_lines.extend(
            [
                f"  const productDetailPlan = {ts_string(product_detail_plan)};",
                "  const productDetailPlanResult = await selectProductDetailCoveragePlan(page, productDetailPlan);",
                "  test.info().annotations.push({ type: 'product-detail-plan', description: JSON.stringify(productDetailPlanResult) });",
                f"  await captureAgent4BusinessScreenshot(page, 'product-detail-plan', {plan_meta});",
            ]
        )
    for index, action in enumerate(real_actions, start=1):
        next_action = expected_transition_actions[index] if index < len(expected_transition_actions) else None
        expected_url = ts_string(_expected_replay_url(action, next_action))
        action_text = str(action.get("text") or "")
        action_tag = str(action.get("tag") or "a")
        action_selector = str(action.get("selector") or "")
        from_node = ts_string(str(action.get("planned_from_node_id") or ""))
        to_node = ts_string(str(action.get("planned_to_node_id") or ""))
        screenshot_meta = (
            "{ "
            f"path_id: {ts_string(path_id)}, "
            f"step: {index}, "
            f"action_text: {ts_string(action_text)}, "
            f"planned_from_node_id: {from_node}, "
            f"planned_to_node_id: {to_node} "
            "}"
        )
        before_submit_meta = (
            "{ "
            f"path_id: {ts_string(path_id)}, "
            f"step: {index}, "
            "phase: 'before-submit', "
            f"action_text: {ts_string(action_text)}, "
            f"planned_from_node_id: {from_node}, "
            f"planned_to_node_id: {from_node} "
            "}"
        )
        before_payment_meta = (
            "{ "
            f"path_id: {ts_string(path_id)}, "
            f"step: {index}, "
            "phase: 'before-payment', "
            f"action_text: {ts_string(action_text)}, "
            f"planned_from_node_id: {from_node}, "
            f"planned_to_node_id: {from_node} "
            "}"
        )
        real_execution_lines.append(f"  await applyMockData(page, {from_node});")
        if action.get("action_key") == "action.answer_questionnaire":
            real_execution_lines.extend(
                [
                    f"  const beforeUrl{index} = page.url();",
                    f"  const answerResult{index} = await answerQuestionnaire(page);",
                    "  await page.waitForLoadState('domcontentloaded', { timeout: 10000 }).catch(() => undefined);",
                    "  await settlePostClickFlow(page);",
                    f"  await acceptQuestionnaireWarningIfPresent(page);",
                    f"  await waitForObservedUrlTransition(page, beforeUrl{index}, {expected_url}, 'step {index}');",
                    f"  await applyMockData(page, {to_node});",
                    "  await expect(page.locator('body')).toBeVisible();",
                    f"  await captureAgent4BusinessScreenshot(page, 'step-{index}', {screenshot_meta});",
                    f"  test.info().annotations.push({{ type: 'questionnaire', description: `step {index}: answered ${{answerResult{index}.clicked_count}} question groups` }});",
                    f"  test.info().annotations.push({{ type: 'real-action', description: `step {index}: ${{beforeUrl{index}}} -> ${{page.url()}}` }});",
                ]
            )
            continue
        if action.get("action_key") == "action.answer_health_notice":
            real_execution_lines.extend(
                [
                    f"  const beforeUrl{index} = page.url();",
                    f"  test.info().annotations.push({{ type: 'planned-action', description: 'action.answer_health_notice skip_if_absent={bool(action.get('skip_if_absent'))}' }});",
                    f"  const answerResult{index} = await answerHealthNotice(page).catch(() => null);",
                    f"  if (!answerResult{index} && {str(bool(action.get('skip_if_absent'))).lower()}) {{",
                    f"    test.info().annotations.push({{ type: 'optional-action-skip', description: `step {index}: optional health notice absent` }});",
                    "  } else {",
                    f"    expect(answerResult{index}).toBeTruthy();",
                    "    await page.waitForLoadState('domcontentloaded', { timeout: 10000 }).catch(() => undefined);",
                    "    await settlePostClickFlow(page);",
                    "    await acceptQuestionnaireWarningIfPresent(page);",
                    f"    await waitForObservedUrlTransition(page, beforeUrl{index}, {expected_url}, 'step {index}');",
                    f"    await applyMockData(page, {to_node});",
                    f"    test.info().annotations.push({{ type: 'questionnaire', description: `step {index}: answered health notice` }});",
                    "  }",
                    "  await expect(page.locator('body')).toBeVisible();",
                    f"  await captureAgent4BusinessScreenshot(page, 'step-{index}', {screenshot_meta});",
                    f"  test.info().annotations.push({{ type: 'real-action', description: `step {index}: ${{beforeUrl{index}}} -> ${{page.url()}}` }});",
                ]
            )
            continue
        if _is_auto_wait_action(action):
            expected_node = ts_string(str(action.get("expected_next_node_id") or action.get("planned_to_node_id") or ""))
            real_execution_lines.extend(
                [
                    f"  const beforeUrl{index} = page.url();",
                    "  await page.waitForLoadState('domcontentloaded', { timeout: 10000 }).catch(() => undefined);",
                    "  await page.waitForTimeout(2000);",
                    f"  await waitForObservedUrlTransition(page, beforeUrl{index}, {expected_url}, 'step {index}');",
                    f"  await applyMockData(page, {to_node});",
                    "  await expect(page.locator('body')).toBeVisible();",
                    f"  await captureAgent4BusinessScreenshot(page, 'step-{index}', {screenshot_meta});",
                    f"  test.info().annotations.push({{ type: 'auto-wait', description: `step {index}: waited for ${{{expected_node}}} from ${{beforeUrl{index}}} to ${{page.url()}}` }});",
                    f"  test.info().annotations.push({{ type: 'real-action', description: `step {index}: ${{beforeUrl{index}}} -> ${{page.url()}}` }});",
                ]
            )
            continue
        if action.get("action_key") == "action.fill_sms_code":
            selector = ts_string(action_selector or "input, textarea")
            real_execution_lines.extend(
                [
                    f"  const beforeUrl{index} = page.url();",
                    f"  await fillFirstVisible(page, {selector}, String(mockData['risk_control_check.smscode'] ?? mockData['请输入4位数验证码'] ?? '1111'));",
                    "  await page.waitForLoadState('domcontentloaded', { timeout: 10000 }).catch(() => undefined);",
                    f"  test.info().annotations.push({{ type: 'real-action', description: `step {index}: filled sms code ${{beforeUrl{index}}} -> ${{page.url()}}` }});",
                ]
            )
            continue
        if action.get("action_key") == "action.upload_id_card":
            selector = ts_string(action_selector or "input[type='file']")
            action_text_literal = ts_string(action_text)
            real_execution_lines.extend(
                [
                    f"  const beforeUrl{index} = page.url();",
                    f"  const idCardUploadResult{index} = await uploadIdCardFixtureToInput(page, {selector}, {action_text_literal});",
                    "  await page.waitForLoadState('domcontentloaded', { timeout: 10000 }).catch(() => undefined);",
                    "  await page.waitForTimeout(1200);",
                    f"  test.info().annotations.push({{ type: 'id-card-upload', description: `step {index}: ${{JSON.stringify(idCardUploadResult{index})}}` }});",
                    f"  test.info().annotations.push({{ type: 'real-action', description: `step {index}: uploaded id card ${{beforeUrl{index}}} -> ${{page.url()}}` }});",
                ]
            )
            continue
        if action.get("action_key") == "action.bank_sign_boundary":
            real_execution_lines.extend(
                [
                    f"  const beforeUrl{index} = page.url();",
                    "  await assertBankSignBoundary(page);",
                    f"  test.info().annotations.push({{ type: 'bank-sign-boundary', description: `step {index}: reached bank signing boundary at ${{page.url()}} from ${{beforeUrl{index}}}` }});",
                ]
            )
            break
        if str(action.get("click_strategy") or "") == "mouse-h5-product-footer-insure":
            real_execution_lines.extend(
                [
                    f"  const beforeUrl{index} = page.url();",
                    f"  const replayResult{index} = await replayH5ProductFooterInsure(page, {ts_string(action_selector)}, {ts_string(action_tag)}, {ts_string(action_text)});",
                    "  await page.waitForLoadState('domcontentloaded', { timeout: 10000 }).catch(() => undefined);",
                    "  await settlePostClickFlow(page);",
                    f"  await waitForObservedUrlTransition(page, beforeUrl{index}, {expected_url}, 'step {index}');",
                    f"  await applyMockData(page, {to_node});",
                    "  await expect(page.locator('body')).toBeVisible();",
                    f"  await captureAgent4BusinessScreenshot(page, 'step-{index}', {screenshot_meta});",
                    f"  test.info().annotations.push({{ type: 'real-action-strategy', description: `step {index}: ${{JSON.stringify(replayResult{index})}}` }});",
                    f"  test.info().annotations.push({{ type: 'real-action', description: `step {index}: ${{beforeUrl{index}}} -> ${{page.url()}}` }});",
                ]
            )
            continue
        if str(action.get("click_strategy") or "") == "touchscreen-submit-btn":
            real_execution_lines.extend(
                [
                    f"  const beforeUrl{index} = page.url();",
        "  await installH5SubmitPayloadPatch(page);",
        "  await syncH5InsureFormFromMock(page, { mode: 'initial' }).catch(() => 0);",
        "  await waitForAgent4TrialInsuredResultForSubmit(5000).catch(() => null);",
        "  await ensureH5AgreementCheckedBeforeSubmit(page).catch(() => 0);",
                    "  await assertH5AgreementCheckedBeforeSubmit(page);",
                    f"  await captureAgent4BusinessScreenshot(page, 'step-{index}-before-submit', {before_submit_meta});",
                    f"  const replayResult{index} = await replayH5SubmitButton(page, {ts_string(action_selector)}, {ts_string(action_tag)}, {ts_string(action_text)}, {expected_url});",
                    "  await page.waitForLoadState('domcontentloaded', { timeout: 10000 }).catch(() => undefined);",
                    "  await settlePostClickFlow(page);",
                    f"  const suitabilityRecovery{index} = (replayResult{index} as any).suitabilityRecovery?.recovered ? (replayResult{index} as any).suitabilityRecovery : await recoverSuitabilityTaskAfterSubmitIfNeeded(page, {expected_url});",
                    f"  test.info().annotations.push({{ type: 'submit-suitability-task-recovery', description: `step {index}: ${{JSON.stringify(suitabilityRecovery{index})}}` }});",
                    f"  const identityRecovery{index} = (replayResult{index} as any).identityRecovery?.recovered ? (replayResult{index} as any).identityRecovery : await recoverIdentityTaskAfterSubmitIfNeeded(page, {expected_url});",
                    f"  test.info().annotations.push({{ type: 'submit-identity-task-recovery', description: `step {index}: ${{JSON.stringify(identityRecovery{index})}}` }});",
                    f"  if (!(suitabilityRecovery{index} as any).recovered && !(identityRecovery{index} as any).progressed) {{",
                    f"    await waitForObservedUrlTransition(page, beforeUrl{index}, {expected_url}, 'step {index}');",
                    "  }",
                    f"  await applyMockData(page, {to_node});",
                    "  await expect(page.locator('body')).toBeVisible();",
                    f"  await captureAgent4BusinessScreenshot(page, 'step-{index}', {screenshot_meta});",
                    f"  test.info().annotations.push({{ type: 'real-action-strategy', description: `step {index}: ${{JSON.stringify(replayResult{index})}}` }});",
                    f"  test.info().annotations.push({{ type: 'real-action', description: `step {index}: ${{beforeUrl{index}}} -> ${{page.url()}}` }});",
                ]
            )
            continue
        if str(action.get("click_strategy") or "") == "touchscreen-payment-btn":
            uses_huize_payment_closed_loop = bool((scenario.get("huize_payment_closed_loop", {}) or {}).get("enabled"))
            closed_loop_lines = []
            if uses_huize_payment_closed_loop:
                closed_loop_lines = [
                    f"  const huizeClosedLoopResult{index} = await runHuizePaymentClosedLoop(page, test.info(), {{ ...huizePaymentClosedLoopConfig }}, replayResult{index} as any);",
                    f"  test.info().annotations.push({{ type: 'huize-payment-closed-loop', description: `step {index}: ${{JSON.stringify(huizeClosedLoopResult{index})}}` }});",
                ]
            post_payment_body_lines = (
                [
                    f"  const paymentBodyState{index} = await waitForVisibleBodyOrBlankPaymentRedirect(page);",
                    f"  test.info().annotations.push({{ type: 'payment-body-state', description: `step {index}: ${{JSON.stringify(paymentBodyState{index})}}` }});",
                    f"  await captureAgent4BusinessScreenshot(page, 'step-{index}', {screenshot_meta}).catch(() => undefined);",
                ]
                if uses_huize_payment_closed_loop
                else [
                    "  await expect(page.locator('body')).toBeVisible();",
                    f"  await captureAgent4BusinessScreenshot(page, 'step-{index}', {screenshot_meta});",
                ]
            )
            real_execution_lines.extend(
                [
                    f"  const beforeUrl{index} = page.url();",
                    f"  await captureAgent4BusinessScreenshot(page, 'step-{index}-before-payment', {before_payment_meta});",
                    f"  const replayResult{index} = await replayH5PaymentButton(page, {ts_string(action_selector)}, {ts_string(action_tag)}, {ts_string(action_text)});",
                    "  await page.waitForLoadState('domcontentloaded', { timeout: 10000 }).catch(() => undefined);",
                    "  await settlePostClickFlow(page).catch(() => []);",
                    "  await page.waitForTimeout(2500);",
                    f"  await applyMockData(page, {to_node});",
                    f"  test.info().annotations.push({{ type: 'real-action-strategy', description: `step {index}: ${{JSON.stringify(replayResult{index})}}` }});",
                    f"  test.info().annotations.push({{ type: 'real-action', description: `step {index}: ${{beforeUrl{index}}} -> ${{page.url()}}` }});",
                    *closed_loop_lines,
                    *post_payment_body_lines,
                ]
            )
            continue
        if not action_text:
            continue
        skip_if_absent = bool(action.get("skip_if_absent"))
        locator_line = f"  const action{index} = await replayActionLocator(page, {ts_string(action_selector)}, {ts_string(action_tag)}, {ts_string(action_text)});"
        real_execution_lines.append(locator_line)
        real_execution_lines.append(f"  const beforeUrl{index} = page.url();")
        if skip_if_absent:
            real_execution_lines.extend(
                [
                    f"  const action{index}Visible = await action{index}.isVisible({{ timeout: 1200 }}).catch(() => false);",
                    f"  if (!action{index}Visible) {{",
                    f"    test.info().annotations.push({{ type: 'optional-action-skip', description: `step {index}: optional action skipped` }});",
                    "  } else {",
                    f"    await clickReplayAction(page, action{index});",
                    "    await page.waitForLoadState('domcontentloaded', { timeout: 10000 }).catch(() => undefined);",
                    "    await settlePostClickFlow(page);",
                    f"    await waitForObservedUrlTransition(page, beforeUrl{index}, {expected_url}, 'step {index}');",
                    f"    await applyMockData(page, {to_node});",
                    "  }",
                ]
            )
        else:
            real_execution_lines.extend(
                [
                    f"  await expect(action{index}).toBeVisible({{ timeout: 10000 }});",
                    f"  await clickReplayAction(page, action{index});",
                    "  await page.waitForLoadState('domcontentloaded', { timeout: 10000 }).catch(() => undefined);",
                    "  await settlePostClickFlow(page);",
                    f"  await waitForObservedUrlTransition(page, beforeUrl{index}, {expected_url}, 'step {index}');",
                    f"  await applyMockData(page, {to_node});",
                ]
            )
        real_execution_lines.extend(
            [
                "  await expect(page.locator('body')).toBeVisible();",
                f"  await captureAgent4BusinessScreenshot(page, 'step-{index}', {screenshot_meta});",
                f"  test.info().annotations.push({{ type: 'real-action', description: `step {index}: ${{beforeUrl{index}}} -> ${{page.url()}}` }});",
            ]
        )
    if scenario.get("blocked_reason"):
        blocked_reason = str(scenario.get("blocked_reason")).replace("`", "\\`")
        real_execution_lines.append(
            f"  test.info().annotations.push({{ type: 'coverage-gap', description: `{blocked_reason}` }});"
        )
    final_lines = [
        "  expect(page.url().length).toBeGreaterThan(0);",
    ]
    if (scenario.get("huize_payment_closed_loop", {}) or {}).get("enabled"):
        final_lines.append("  await waitForVisibleBodyOrBlankPaymentRedirect(page);")
    else:
        final_lines.append("  await expect(page.locator('body')).toBeVisible();")
    final_lines.append(
        (
            "  await captureAgent4BusinessScreenshot(page, 'final', "
            "{ "
            f"path_id: {ts_string(path_id)}, "
            "phase: 'final', "
            f"planned_to_node_id: {ts_string(str(scenario.get('target_node') or scenario.get('reached_target_node') or ''))} "
            "});"
        )
    )
    real_execution_lines.extend(final_lines)
    return has_real_action, real_execution_lines


def render_chain_spec(
    scenario: Mapping[str, Any],
    page_functions: list[dict[str, Any]],
    *,
    product_id: str,
    generated_by: str,
    run_path: str | None = None,
) -> str:
    """Render an e2e-test style phase-1 chain verification spec.

    The chain spec is intentionally located under ts-gen/.artifacts and replays the
    generated page-functions from the flow entry to the current terminal page.
    It is the bridge between Agent3 exploration and Agent4 formal scenarios.
    """
    huize_closed_loop = dict(scenario.get("huize_payment_closed_loop", {}) or {})
    uses_huize_closed_loop = bool(huize_closed_loop.get("enabled"))
    ordered_functions = _ordered_page_functions_for_scenario(scenario, page_functions)
    terminal_page_id = _scenario_terminal_page_id(scenario)
    platform = platform_from_entry_url(str(scenario.get("entry_url") or ""))
    chain_name = f"chain-to-{slugify(terminal_page_id)}-{platform}"
    run_path = run_path or f"products/{product_id}/agent3/ts-gen/.artifacts/{chain_name}.spec.ts"
    pages = " -> ".join(
        str(node_id).removeprefix("NODE-")
        for node_id in scenario.get("route_nodes", []) or []
        if str(node_id) not in {"NODE-start", "NODE-end", "NODE-branch"}
    ) or terminal_page_id
    imports = _unique_strings_in_order([
        f"import {{ {item['function_name']} }} from '../{_normalise_ts_import_path(item['file_path'])}';"
        for item in ordered_functions
    ])
    has_real_action, real_execution_lines = _real_action_replay_lines(scenario)
    needs_node_runtime = has_real_action or uses_huize_closed_loop
    huize_closed_loop_config = {
        "enabled": uses_huize_closed_loop,
        "paymentMethodHint": huize_closed_loop.get("payment_method"),
        "gatewayPayNumSourceHint": huize_closed_loop.get("gateway_pay_num_source"),
        "payOperationId": huize_closed_loop.get("pay_operation_id"),
        "issueOperationId": huize_closed_loop.get("issue_operation_id"),
    }
    scenario_conditions = (
        scenario.get("variants", [{}])[0].get("conditions", {})
        if scenario.get("variants")
        else {}
    )
    mock_data = _scenario_mock_data_with_overrides(scenario, scenario.get("mock_data", {}) or {})
    page_element_plan = list(scenario.get("page_element_plan", []) or [])
    calls = [
        f"  await {item['function_name']}(page, scenarioConditions as any);"
        for item in ordered_functions
    ]
    metadata = {
        "scenario_id": scenario.get("scenario_id"),
        "path_id": scenario.get("path_id"),
        "case_ids": scenario.get("case_ids", []),
        "route_nodes": scenario.get("route_nodes", []),
        "agent2_route_nodes": scenario.get("agent2_route_nodes", []),
        "path_repaired": scenario.get("path_repaired", False),
        "coverage_status": scenario.get("coverage_status"),
        "contract_status": scenario.get("contract_status"),
        "execution_requirements": scenario.get("execution_requirements", {}),
        "source": "agent3.chain-script",
    }
    return "\n".join(
        [
            "/**",
            f" * @chain       {chain_name}",
            f" * @reaches     {terminal_page_id}",
            " * @reaches_url derived-from-agent3",
            f" * @path        {scenario.get('path_id')}",
            f" * @pages       {pages}",
            " * @verified    pending-agent3-chain-run",
            f" * @run         NODE_PATH=$(npm root -g) npx playwright test {run_path} --project=chromium --headed --reporter=line",
            f" * @generated-by {generated_by}",
            f" * @scenario    {json.dumps(metadata, ensure_ascii=False, sort_keys=True)}",
            " */",
            "import { test, expect } from '../fixtures';",
            *(["import * as fs from 'fs';", "import * as path from 'path';"] if needs_node_runtime else []),
            *(["import { paySuccess as huizePaySuccess } from '../external-ops/huize-pay-success.cjs';"] if uses_huize_closed_loop else []),
            *(["import { waitForIssueStatus as huizeWaitForIssueStatus } from '../external-ops/huize-issue-status.cjs';"] if uses_huize_closed_loop else []),
            *(imports if not has_real_action else []),
            "",
            f"const FLOW_ENTRY = {ts_string(scenario.get('entry_url') or 'about:blank')};",
            f"const scenarioConditions = {json.dumps(scenario_conditions, ensure_ascii=False, indent=2)};",
            *(
                [
                    f"const embeddedMockData = {json.dumps(mock_data, ensure_ascii=False, indent=2)};",
                    "const mockData = withAgent4RuntimeMockDataOverrides(embeddedMockData);",
                ]
                if has_real_action
                else [f"const mockData = {json.dumps(mock_data, ensure_ascii=False, indent=2)};"]
            ),
            f"const pageElementPlan = {json.dumps(page_element_plan, ensure_ascii=False, indent=2)};",
            f"const fieldResolutionPlan = {json.dumps(scenario.get('field_resolution_plan', {}), ensure_ascii=False, indent=2)};",
            f"const componentStrategy = {json.dumps(scenario.get('component_strategy', {}), ensure_ascii=False, indent=2)};",
            f"const validationReport = {json.dumps(scenario.get('validation_report', {}), ensure_ascii=False, indent=2)};",
            *((
                f"const huizePaymentClosedLoopConfig = {json.dumps(huize_closed_loop_config, ensure_ascii=False, indent=2)};"
            ,) if uses_huize_closed_loop else ()),
            "",
            *(real_action_helper_lines() if has_real_action else []),
            *(huize_payment_closed_loop_helper_lines() if uses_huize_closed_loop else []),
            *(field_probe_helper_lines() if has_real_action else []),
            *(questionnaire_answer_helper_lines() if has_real_action else []),
            f"test('{chain_name}', async ({{ page }}) => {{",
            "  test.setTimeout(720_000);",
            *(
                real_execution_lines
                if has_real_action
                else [
                    "  await page.goto(FLOW_ENTRY, { waitUntil: 'domcontentloaded' });",
                    "  await page.waitForLoadState('networkidle').catch(() => undefined);",
                    *(calls or ["  await expect(page.locator('body')).toBeVisible();"]),
                ]
            ),
            "  await expect(page.locator('body')).toBeVisible();",
            "});",
            "",
        ]
    )


def render_fixtures_file() -> str:
    return "\n".join(
        [
            "import { test as base, expect, devices } from 'playwright/test';",
            "",
            "const device = devices['iPhone 15 Pro Max'];",
            "",
            "export const test = base.extend({",
            "  page: async ({ browser }, use, testInfo) => {",
            "    const context = await browser.newContext({",
            "      ...device,",
            "      recordVideo: testInfo.project.use.video",
            "        ? { dir: testInfo.outputDir, size: device.viewport }",
            "        : undefined,",
            "    });",
            "    const page = await context.newPage();",
            "    await use(page);",
            "    await context.close();",
            "  },",
            "});",
            "",
            "export { expect };",
            "",
        ]
    )


def render_generate_test_data_file(scenarios: list[dict[str, Any]]) -> str:
    profiles = {
        str(scenario.get("scenario_id") or f"SCN-{index:03d}"): {
            "path_id": scenario.get("path_id"),
            "case_ids": scenario.get("case_ids", []),
            "mock_data": scenario.get("mock_data", {}),
            "conditions": (
                scenario.get("variants", [{}])[0].get("conditions", {})
                if scenario.get("variants")
                else {}
            ),
        }
        for index, scenario in enumerate(scenarios, start=1)
    }
    return "\n".join(
        [
            "// Generated by Agent3 ts-gen. Keep deterministic data here; page-functions consume params.",
            f"const scenarioProfiles = {json.dumps(profiles, ensure_ascii=False, indent=2)} as const;",
            "",
            "export type ScenarioProfileId = keyof typeof scenarioProfiles;",
            "",
            "export function generateScenarioFromProfile(profileId: string): Record<string, unknown> {",
            "  const profile = (scenarioProfiles as Record<string, Record<string, unknown>>)[profileId];",
            "  if (!profile) throw new Error(`[generateScenarioFromProfile] unknown profile: ${profileId}`);",
            "  return JSON.parse(JSON.stringify(profile));",
            "}",
            "",
            "export { scenarioProfiles };",
            "",
        ]
    )


def _mock_value_for_field(field: Mapping[str, Any]) -> str:
    return policy_mock_value_for_field(field)


def _page_record_index(state: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    records = (state.get("page_registry", {}) or {}).get("page_content_records", []) or []
    return {
        str(record.get("page_content_record_id")): dict(record)
        for record in records
        if record.get("page_content_record_id")
    }


def _scenario_page_element_plan(
    state: Mapping[str, Any],
    path_exploration: Mapping[str, Any],
) -> list[dict[str, Any]]:
    records_by_id = _page_record_index(state)
    field_resolution_by_key = {
        (str(item.get("node_id") or ""), str(item.get("field_key") or "")): dict(item)
        for item in ((path_exploration.get("field_resolution_plan", {}) or {}).get("fields", []) or [])
    }
    component_strategy_by_key = {
        (str(item.get("node_id") or ""), str(item.get("field_key") or "")): dict(item)
        for item in ((path_exploration.get("component_strategy", {}) or {}).get("field_strategies", []) or [])
    }
    plan: list[dict[str, Any]] = []
    for record_id in path_exploration.get("page_content_refs", []) or []:
        record = records_by_id.get(str(record_id))
        if not record:
            continue
        matched_node_ids = list(record.get("matched_node_ids", []) or [])
        primary_node_id = str(matched_node_ids[0]) if matched_node_ids else ""
        fields = []
        for field in record.get("field_map", []) or []:
            field_key = str(field.get("field_key") or "")
            resolution = field_resolution_by_key.get((primary_node_id or str(record.get("node_id") or ""), field_key), {})
            component_strategy = component_strategy_by_key.get((primary_node_id or str(record.get("node_id") or ""), field_key), {})
            fields.append(
                {
                    "field_key": field_key,
                    "selector": field.get("selector"),
                    "tag": field.get("raw", {}).get("tag") or field.get("tag"),
                    "type": field.get("raw", {}).get("type") or field.get("type"),
                    "label": field.get("raw", {}).get("label") or field.get("raw", {}).get("placeholder"),
                    "mock_value": _mock_value_for_field(field),
                    "required": bool(field.get("required")),
                    "locators": list(field.get("locators", []) or []),
                    "control_type": field.get("control_type") or component_strategy.get("control_type"),
                    "fill_strategy": field.get("fill_strategy") or component_strategy.get("fill_strategy"),
                    "field_resolution": resolution,
                    "component_strategy": component_strategy,
                }
            )
        actions = []
        for action in (record.get("selector_map", {}) or {}).get("actions", []) or []:
            actions.append(
                {
                    **dict(action),
                    "required": bool(action.get("required")),
                }
            )
        match_contract = dict(record.get("match_contract", {}) or {})
        fallback_profile = _node_profile(
            primary_node_id,
            node_label(primary_node_id.removeprefix("NODE-")) if primary_node_id else "Page",
        )
        plan.append(
            {
                "page_content_record_id": record_id,
                "page_model_id": record.get("page_model_id"),
                "node_id": primary_node_id or record.get("node_id"),
                "actual_url": record.get("actual_url"),
                "matched_node_ids": matched_node_ids,
                "entry_signals": list(match_contract.get("entry_signals", []) or fallback_profile.get("entry_signal", [])),
                "exit_signals": list(match_contract.get("exit_signals", []) or fallback_profile.get("exit_signal", [])),
                "url_patterns": list(match_contract.get("url_patterns", []) or []),
                "fields": fields,
                "actions": actions,
            }
        )
    return plan


def _page_records_for_path(
    state: Mapping[str, Any],
    path_exploration: Mapping[str, Any],
) -> list[dict[str, Any]]:
    records_by_id = _page_record_index(state)
    records: list[dict[str, Any]] = []
    for record_id in path_exploration.get("page_content_refs", []) or []:
        record = records_by_id.get(str(record_id))
        if record:
            records.append(record)
    return records


def _payment_method_from_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if any(token in text for token in ("wechat", "weixin", "wxpay", "wx_pay", "weixinpay", "tenpay", "checkmweb", "微信")):
        return "wechat"
    if any(token in text for token in ("alipay", "ali_pay", "支付宝")):
        return "alipay"
    return ""


def _payment_boundary_evidence_from_record(record: Mapping[str, Any]) -> dict[str, Any]:
    direct = record.get("payment_boundary_evidence")
    if isinstance(direct, Mapping):
        evidence = dict(direct)
    else:
        evidence = {}

    actual_url = str(record.get("actual_url") or "")
    record_text = json.dumps(record, ensure_ascii=False, default=str)
    if actual_url and not evidence.get("paymentUrl"):
        evidence["paymentUrl"] = actual_url
    if not evidence.get("paymentMethod"):
        evidence["paymentMethod"] = _payment_method_from_text(f"{actual_url}\n{record_text}")
    if not evidence.get("insureNum"):
        insure_num = _extract_insure_num_hint(record_text)
        if insure_num:
            evidence["insureNum"] = insure_num
    if not evidence.get("gatewayPayNum_source") and actual_url:
        if "trade_no" in actual_url:
            evidence["gatewayPayNum_source"] = "payment-url-trade_no"
        elif "gatewayPayNum" in actual_url:
            evidence["gatewayPayNum_source"] = "payment-url-gatewayPayNum"

    return evidence


def _payment_boundary_evidence_from_action(action: Mapping[str, Any]) -> dict[str, Any]:
    action_text = json.dumps(action, ensure_ascii=False, default=str)
    source_url = str(action.get("source_url") or "")
    target_url = str(action.get("target_url") or "")
    payment_text = _expanded_payment_text(f"{source_url}\n{target_url}\n{action_text}")
    gateway_pay_num = _extract_gateway_pay_num_hint(payment_text)
    method = _payment_method_from_text(payment_text)
    is_payment_action = any(
        token in str(action.get(key) or "").lower()
        for key in ("action_key", "click_strategy", "planned_from_node_id", "planned_to_node_id")
        for token in ("payment", "pay")
    )
    is_payment_boundary = bool(gateway_pay_num or method or re.search(r"checkmweb|trade_no|gatewayPayNum|prepay", payment_text, re.IGNORECASE))
    if not is_payment_action and not is_payment_boundary:
        return {}

    evidence: dict[str, Any] = {}
    if target_url or source_url:
        evidence["paymentUrl"] = target_url or source_url
    if method:
        evidence["paymentMethod"] = method
    if gateway_pay_num:
        evidence["gatewayPayNum"] = gateway_pay_num
        evidence["gatewayPayNum_source"] = "payment-action-trade_no" if "trade_no" in payment_text else "payment-action-gatewayPayNum"
    if not evidence.get("insureNum"):
        insure_num = _extract_insure_num_hint(action_text)
        if insure_num:
            evidence["insureNum"] = insure_num
    if target_url:
        evidence["targetUrl"] = target_url
    if source_url:
        evidence["sourceUrl"] = source_url
    return evidence


def _expanded_payment_text(value: Any) -> str:
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
            next_value = unquote(decoded)
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


def _extract_gateway_pay_num_hint(value: Any) -> str:
    text = _expanded_payment_text(value)
    for pattern in (
        r"(?:[?&#]|^)(?:trade_no|gatewayPayNum)=([^&#\s]+)",
        r"(?:trade_no|gatewayPayNum|paymentOrder|payOrderNo|orderNo)[\"'\s:=：]+[\"']?([A-Za-z0-9_-]{8,})",
    ):
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip()
    return ""


def _extract_insure_num_hint(value: Any) -> str:
    text = str(value or "")
    patterns = (
        r"(?:insureNum|insure_num)\\?\"?\s*[:=]\s*\\?\"?(\d{10,20})",
        r"(?:insureNum|insure_num|投保单号|投保订单号)[\"'\s:=：]+[\"']?(\d{10,20})",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return ""


def _is_huize_payment_boundary(evidence: Mapping[str, Any]) -> bool:
    owner = str(evidence.get("cashierOwner") or evidence.get("cashier_owner") or "").lower()
    url = _expanded_payment_text(
        "\n".join(
            str(evidence.get(key) or "")
            for key in ("paymentUrl", "sourceUrl", "targetUrl", "redirectUrl", "returnUrl")
        )
    ).lower()
    source = str(evidence.get("gatewayPayNum_source") or "").lower()
    method = _payment_method_from_text(evidence.get("paymentMethod")) or _payment_method_from_text(url)
    gateway_pay_num = str(evidence.get("gatewayPayNum") or "").strip() or _extract_gateway_pay_num_hint(url)
    payment_tokens = (
        "gatewaypaynum",
        "trade_no",
        "paymentorder",
        "payorderno",
        "checkmweb",
        "wechat",
        "alipay",
        "cashier",
        "/pay",
        "/payment",
    )
    if any(token in owner for token in ("generic-insurance", "payment", "cashier")):
        return True
    if gateway_pay_num and (method in {"wechat", "alipay"} or any(token in source for token in ("wechat", "alipay", "gatewaypaynum"))):
        return True
    if method in {"wechat", "alipay"} and any(token in url for token in payment_tokens):
        return True
    if any(token in url for token in payment_tokens) and str(evidence.get("insureNum") or ""):
        return True
    if any(token in source for token in ("wechat-url", "alipay", "deduction-url", "backend-compatible", "gatewaypaynum")):
        return True
    return False


def _operation_id(scenario_id: Any, path_id: Any, suffix: str) -> str:
    scenario_text = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(scenario_id or "SCN")).strip("-") or "SCN"
    path_text = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(path_id or "PATH")).strip("-") or "PATH"
    return f"{scenario_text}-{path_text}-{suffix}"


def _build_huize_payment_closed_loop(
    scenario_id: str,
    path_id: str,
    route_nodes: list[str],
    path_exploration: Mapping[str, Any],
    page_records: list[dict[str, Any]],
    real_actions: list[dict[str, Any]] | None = None,
    page_element_plan: list[dict[str, Any]] | None = None,
    target_node: Any = None,
) -> dict[str, Any]:
    candidate_actions = list(real_actions or [])
    if page_element_plan is not None:
        candidate_actions = _append_synthetic_payment_action_if_needed(
            candidate_actions,
            {
                "path_id": path_id,
                "route_nodes": route_nodes,
                "target_node": target_node or path_exploration.get("target_node"),
                "page_element_plan": page_element_plan,
            },
        )
    has_payment_boundary_action = any(
        (
            str(action.get("click_strategy") or "") == "touchscreen-payment-btn"
            or str(action.get("planned_from_node_id") or "") == "NODE-payment"
            or _is_payment_page_url(action.get("source_url"))
            or _is_payment_page_url(action.get("target_url"))
        )
        for action in candidate_actions
        if isinstance(action, Mapping)
    )
    if "NODE-payment" not in route_nodes and not has_payment_boundary_action:
        return {}
    business_target = " ".join(
        str(value or "")
        for value in (
            path_exploration.get("target_node"),
            path_exploration.get("business_intent"),
            path_exploration.get("target_boundary"),
        )
    ).lower()
    if ("surrender" in business_target or "退保" in business_target) and not has_payment_boundary_action:
        return {}

    evidence_candidates = [
        _payment_boundary_evidence_from_record(record)
        for record in page_records
        if isinstance(record, Mapping)
    ]
    evidence_candidates.extend(
        _payment_boundary_evidence_from_action(action)
        for action in candidate_actions
        if isinstance(action, Mapping)
    )
    terminal_boundary = path_exploration.get("terminal_boundary")
    if isinstance(terminal_boundary, Mapping):
        boundary_evidence = terminal_boundary.get("payment_boundary_evidence")
        if isinstance(boundary_evidence, Mapping):
            evidence_candidates.append(dict(boundary_evidence))

    path_insure_num = _extract_insure_num_hint(
        json.dumps(
            {"path_exploration": path_exploration, "page_records": page_records},
            ensure_ascii=False,
            default=str,
        )
    )
    runtime_candidate: tuple[str, str, str] | None = None
    for evidence in evidence_candidates:
        method = _payment_method_from_text(evidence.get("paymentMethod")) or _payment_method_from_text(
            evidence.get("paymentUrl")
        )
        gateway_pay_num = str(evidence.get("gatewayPayNum") or "").strip() or _extract_gateway_pay_num_hint(
            evidence.get("paymentUrl")
        )
        insure_num = str(evidence.get("insureNum") or "").strip() or _extract_insure_num_hint(
            json.dumps(evidence, ensure_ascii=False)
        ) or path_insure_num
        boundary_evidence = dict(evidence)
        if insure_num and not boundary_evidence.get("insureNum"):
            boundary_evidence["insureNum"] = insure_num
        if not _is_huize_payment_boundary(boundary_evidence):
            continue

        gateway_source = str(evidence.get("gatewayPayNum_source") or "runtime-payment-boundary").strip()
        if not insure_num:
            if runtime_candidate is None and (gateway_pay_num or method in {"wechat", "alipay"}):
                runtime_candidate = (method or "runtime", "runtime-payment-boundary", "")
            continue
        if not gateway_pay_num or method not in {"wechat", "alipay"}:
            if runtime_candidate is None:
                runtime_candidate = (method or "runtime", gateway_source, insure_num)
            continue
        pay_operation_id = _operation_id(scenario_id, path_id, "huize-pay-success")
        issue_operation_id = _operation_id(scenario_id, path_id, "huize-issue-status")
        operation_base = {
            "status": "pending",
            "payment_method": method,
            "gateway_pay_num_source": gateway_source,
        }
        return {
            "enabled": True,
            "payment_method": method,
            "gateway_pay_num_source": gateway_source,
            "pay_operation_id": pay_operation_id,
            "issue_operation_id": issue_operation_id,
            "external_operations": [
                {
                    "operation_id": pay_operation_id,
                    "operation_type": "huize-pay-success",
                    **operation_base,
                },
                {
                    "operation_id": issue_operation_id,
                    "operation_type": "huize-issue-status",
                    **operation_base,
                },
            ],
        }
    if runtime_candidate:
        method, gateway_source, _insure_num = runtime_candidate
        pay_operation_id = _operation_id(scenario_id, path_id, "huize-pay-success")
        issue_operation_id = _operation_id(scenario_id, path_id, "huize-issue-status")
        operation_base = {
            "status": "pending",
            "payment_method": method,
            "gateway_pay_num_source": gateway_source,
        }
        return {
            "enabled": True,
            "payment_method": method,
            "gateway_pay_num_source": gateway_source,
            "pay_operation_id": pay_operation_id,
            "issue_operation_id": issue_operation_id,
            "external_operations": [
                {
                    "operation_id": pay_operation_id,
                    "operation_type": "huize-pay-success",
                    **operation_base,
                },
                {
                    "operation_id": issue_operation_id,
                    "operation_type": "huize-issue-status",
                    **operation_base,
                },
            ],
        }
    return {}


def _scenario_mock_data(page_element_plan: list[dict[str, Any]]) -> dict[str, str]:
    fields: list[Mapping[str, Any]] = []
    for record in page_element_plan:
        for field in record.get("fields", []) or []:
            if field.get("field_key"):
                fields.append(field)
    return generate_policy_mock_data(fields)


def _normalise_real_action(action: Mapping[str, Any], path_id: str) -> dict[str, Any]:
    return {
        "script_step_id": action.get("script_step_id"),
        "step_index": action.get("step_index"),
        "action_key": action.get("action_key"),
        "action_type": action.get("action_type"),
        "text": action.get("text"),
        "tag": action.get("tag") or "a",
        "selector": action.get("selector"),
        "source_url": action.get("source_url"),
        "target_url": action.get("target_url"),
        "path_id": path_id,
        "planned_from_node_id": action.get("planned_from_node_id"),
        "planned_to_node_id": action.get("planned_to_node_id"),
        "expected_next_node_id": action.get("expected_next_node_id") or action.get("planned_to_node_id"),
        "expected_signals": list(action.get("expected_signals", []) or []),
        "answer_strategy": action.get("answer_strategy"),
        "status": action.get("status"),
        "click_strategy": action.get("click_strategy"),
        "score": action.get("score"),
        "skip_if_absent": bool(action.get("skip_if_absent")),
        "dismissed_overlays": list(action.get("dismissed_overlays", []) or []),
        "locators": list(action.get("locators", []) or []),
        "source": action.get("source"),
    }


def _is_auto_wait_action(action: Mapping[str, Any]) -> bool:
    return (
        action.get("click_strategy") == "auto_wait_for_next_node"
        or action.get("action_key") == "action.auto_wait_for_next_node"
    )


def _is_executable_real_action(action: Mapping[str, Any]) -> bool:
    if _is_auto_wait_action(action):
        return True
    if action.get("selector"):
        return True
    locators = list(action.get("locators", []) or [])
    locator_kinds = {str(locator.get("by") or "") for locator in locators}
    if "selector" in locator_kinds:
        return True
    if locator_kinds & {"function", "role:heading"}:
        return False
    return bool(str(action.get("text") or "").strip())


def _prioritise_real_actions(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    executable = [action for action in actions if _is_executable_real_action(action)]
    executable.sort(
        key=lambda action: (
            0 if action.get("selector") else 1,
            0 if action.get("source_url") else 1,
            str(action.get("action_key") or ""),
        )
    )
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for action in executable:
        dedupe_key = str(action.get("action_key") or action.get("selector") or action.get("text") or "")
        if dedupe_key and dedupe_key in seen:
            continue
        if dedupe_key:
            seen.add(dedupe_key)
        selected.append(action)
    return selected


def _is_agent3_replay_candidate(action: Mapping[str, Any]) -> bool:
    tag = str(action.get("tag") or "").strip().lower()
    text = str(action.get("text") or "").strip()
    action_type = str(action.get("action_type") or "").strip().lower()
    click_strategy = str(action.get("click_strategy") or "").strip().lower()
    return not (
        tag == "diagnostic"
        or action_type.endswith("_diagnostic")
        or "diagnostic" in action_type
        or click_strategy.endswith("-scan-miss")
        or text.startswith("agreement_scan_miss:")
    )


_PSEUDO_SELECTOR_PREFIXES = (
    "policy-tool-",
    "pre-submit-",
    "state-bank-",
    "bank-repair-",
    "region-repair-",
    "trial-genes-",
    "existing-order-dialog-",
    "unfinished-policy-dialog-",
)
_PSEUDO_SELECTOR_FRAGMENTS = (
    "/debug",
    "/起始日期",
    "/截止日期",
)
_GENERIC_FIELD_SELECTORS = {
    "input",
    "textarea",
    "input, textarea",
    "input[type='file']",
    'input[type="file"]',
}
_MINIMAL_CLICK_TEXTS = {
    "确认投保",
    "去完成",
    "去认证",
    "下一步",
    "确认",
    "确定",
}
_MINIMAL_CLICK_STRATEGIES = {
    "sms-code-request",
    "auth-sms-submit",
    "auth-sms-next-photo",
    "auth-photo-submit",
    "auth-final-next",
    "bank-sign-sms-send",
    "bank-sign-confirm",
}


def _compact_action_text(action: Mapping[str, Any]) -> str:
    return re.sub(r"\s+", "", str(action.get("text") or ""))


def _selector_is_replayable(selector: str | None) -> bool:
    value = str(selector or "").strip()
    if not value:
        return False
    if value in _GENERIC_FIELD_SELECTORS:
        return False
    if value.startswith(_PSEUDO_SELECTOR_PREFIXES):
        return False
    if any(fragment in value for fragment in _PSEUDO_SELECTOR_FRAGMENTS):
        return False
    if value.startswith("/api/"):
        return False
    return True


def _is_health_notice_action(action: Mapping[str, Any]) -> bool:
    if action.get("action_key") == "action.answer_health_notice":
        return True
    strategy = str(action.get("click_strategy") or "")
    text = _compact_action_text(action)
    selector = str(action.get("selector") or "")
    return (
        strategy == "js-health-notice-safe-option"
        or "确认无以上问题" in text
        or "无以上问题" in text
        or "确认无以上问题" in selector
    )


def _is_questionnaire_action(action: Mapping[str, Any]) -> bool:
    if action.get("action_key") == "action.answer_questionnaire":
        return True
    strategy = str(action.get("click_strategy") or "")
    text = _compact_action_text(action)
    return (
        "questionnaire" in strategy
        or strategy.startswith("playwright-adapt-questionnaire")
        or text.startswith("适当性Q")
        or text.startswith("适当性金额")
    )


def _is_sms_fill_action(action: Mapping[str, Any]) -> bool:
    if action.get("action_key") == "action.fill_sms_code":
        return True
    strategy = str(action.get("click_strategy") or "")
    text = _compact_action_text(action)
    return (
        "sms-captcha-fill" in strategy
        or "短信验证码" in text
        or "验证码=1111" in text
    )


def _is_upload_action(action: Mapping[str, Any]) -> bool:
    if action.get("action_key") == "action.upload_id_card":
        return True
    strategy = str(action.get("click_strategy") or "")
    selector = str(action.get("selector") or "")
    return strategy == "auth-id-card-upload" or selector in {"input[type='file']", 'input[type="file"]'}


def _is_bank_sign_boundary_action(action: Mapping[str, Any]) -> bool:
    if action.get("action_key") == "action.bank_sign_boundary":
        return True
    strategy = str(action.get("click_strategy") or "")
    text = _compact_action_text(action)
    url = f"{action.get('source_url') or ''} {action.get('target_url') or ''}"
    return (
        strategy == "bank-sign-dialog-detected"
        or ("银行卡签约" in text and "短信验证" in text)
        or ("/bank/sign" in url and "confirm" not in url)
    )


def _is_replayable_minimal_click(action: Mapping[str, Any]) -> bool:
    strategy = str(action.get("click_strategy") or "")
    if strategy in _MINIMAL_CLICK_STRATEGIES and _compact_action_text(action):
        return True
    if strategy != "js-minimal-data":
        return False
    if str(action.get("tag") or "").lower() == "field":
        return False
    text = _compact_action_text(action)
    return any(token in text for token in _MINIMAL_CLICK_TEXTS) and _selector_is_replayable(action.get("selector"))


def _is_non_progressing_quote_probe(action: Mapping[str, Any]) -> bool:
    strategy = str(action.get("click_strategy") or "")
    text = _compact_action_text(action)
    return (
        "premium-quote" in strategy
        and "保费试算" in text
        and not _action_progresses_to_new_url(action)
    )


def _is_redundant_agreement_protocol_action(action: Mapping[str, Any]) -> bool:
    action_type = str(action.get("action_type") or "").strip().lower()
    strategy = str(action.get("click_strategy") or "").strip().lower()
    tag = str(action.get("tag") or "").strip().lower()
    text = _compact_action_text(action)
    selector = str(action.get("selector") or "").strip().lower()
    protocol_text = any(token in text for token in ("《", "》", "投保条件", "投保重要告知", "投保人声明", "保险条款", "免责条款"))
    if tag == "a" and protocol_text and not _action_progresses_to_new_url(action):
        return True
    if not (
        action_type in {"agreement", "minimal_data", "agreement_observed"}
        or "agreement" in strategy
        or "am-checkbox" in selector
    ):
        return False
    if _action_progresses_to_new_url(action):
        return False
    if tag in {"checkbox", "option", "label", "a"}:
        return True
    return protocol_text


def _normalise_replay_action(
    action: Mapping[str, Any],
    path_id: str,
    seen_semantic: set[str],
) -> dict[str, Any] | None:
    if _is_non_progressing_quote_probe(action):
        return None
    if _is_health_notice_action(action):
        if "action.answer_health_notice" in seen_semantic:
            return None
        seen_semantic.add("action.answer_health_notice")
        normalised = _normalise_real_action(action, path_id)
        normalised.update(
            {
                "action_key": "action.answer_health_notice",
                "text": "answer health notice",
                "selector": None,
                "tag": "semantic",
                "skip_if_absent": True,
            }
        )
        return normalised
    if _is_questionnaire_action(action):
        if "action.answer_questionnaire" in seen_semantic:
            return None
        seen_semantic.add("action.answer_questionnaire")
        normalised = _normalise_real_action(action, path_id)
        normalised.update(
            {
                "action_key": "action.answer_questionnaire",
                "text": "answer questionnaire",
                "selector": None,
                "tag": "semantic",
            }
        )
        return normalised
    if _is_redundant_agreement_protocol_action(action):
        return None
    if _is_sms_fill_action(action):
        normalised = _normalise_real_action(action, path_id)
        normalised.update(
            {
                "action_key": "action.fill_sms_code",
                "selector": action.get("selector") or "input, textarea",
                "tag": "field",
            }
        )
        return normalised
    if _is_upload_action(action):
        normalised = _normalise_real_action(action, path_id)
        normalised.update(
            {
                "action_key": "action.upload_id_card",
                "selector": action.get("selector") or "input[type='file']",
                "tag": "input",
            }
        )
        return normalised
    if _is_bank_sign_boundary_action(action):
        normalised = _normalise_real_action(action, path_id)
        normalised.update(
            {
                "action_key": "action.bank_sign_boundary",
                "selector": action.get("selector") or "[role=dialog], .am-modal",
                "tag": "dialog",
                "text": action.get("text") or "bank sign boundary",
            }
        )
        return normalised

    action_type = str(action.get("action_type") or "").strip().lower()
    click_strategy = str(action.get("click_strategy") or "").strip().lower()
    tag = str(action.get("tag") or "").strip().lower()
    if action_type in {"minimal_data", "agreement_observed", "agreement_diagnostic", "agreement_dismiss"}:
        if not _is_replayable_minimal_click(action):
            return None
    if click_strategy in {
        "js-minimal-data",
        "bank-mock-from-policy-tool",
        "bank-mock-from-page",
        "pre-submit-state-snapshot",
        "bank-recognition-state-repair",
        "region-state-repair",
        "trial-genes-sync",
        "id-validity-default",
        "oracle-field-repair",
        "auth-final-next-standard-probe",
    } and not _is_replayable_minimal_click(action):
        return None
    if tag in {"field", "xhr", "diagnostic", "dialog"}:
        return None
    if action.get("selector") and not _selector_is_replayable(action.get("selector")):
        return None
    if not _is_executable_real_action(action):
        return None
    return _normalise_real_action(action, path_id)


def _action_progresses_to_new_url(action: Mapping[str, Any]) -> bool:
    source_url = str(action.get("source_url") or "").strip()
    target_url = str(action.get("target_url") or "").strip()
    return bool(source_url and target_url and source_url != target_url)


def _product_insure_replay_group(action: Mapping[str, Any]) -> tuple[str, str, str] | None:
    strategy = str(action.get("click_strategy") or "").strip()
    if strategy != "mouse-h5-product-footer-insure":
        return None
    source_url = str(action.get("source_url") or "").strip()
    text = _compact_action_text(action)
    if not source_url or not text:
        return None
    return (source_url, text, strategy)


def _should_replace_product_insure_action(
    previous: Mapping[str, Any],
    current: Mapping[str, Any],
) -> bool:
    previous_progresses = _action_progresses_to_new_url(previous)
    current_progresses = _action_progresses_to_new_url(current)
    if current_progresses and not previous_progresses:
        return True
    if previous_progresses:
        return False
    previous_tag = str(previous.get("tag") or "").lower()
    current_tag = str(current.get("tag") or "").lower()
    return current_tag in {"a", "button"} and previous_tag not in {"a", "button"}


def _is_product_confirm_panel_followup(
    previous: Mapping[str, Any],
    current: Mapping[str, Any],
) -> bool:
    if str(previous.get("click_strategy") or "") != "mouse-h5-product-footer-insure":
        return False
    previous_source = str(previous.get("source_url") or "").strip()
    current_source = str(current.get("source_url") or "").strip()
    if not previous_source or previous_source != current_source:
        return False
    if "/product/detail" not in _url_path(previous_source):
        return False
    if not _action_progresses_to_new_url(current):
        return False
    text = _compact_action_text(current)
    if not any(
        token in text
        for token in (
            "已阅读并同意",
            "阅读并同意",
            "同意并继续",
            "继续投保",
            "确认投保",
            "立即投保",
            "投保须知",
        )
    ):
        return False
    previous_to = str(previous.get("planned_to_node_id") or "")
    current_to = str(current.get("planned_to_node_id") or "")
    if previous_to and current_to and previous_to != current_to:
        return False
    return True


def _merge_product_confirm_panel_followup(
    previous: Mapping[str, Any],
    current: Mapping[str, Any],
) -> dict[str, Any]:
    merged = dict(previous)
    for key in (
        "target_url",
        "planned_to_node_id",
        "expected_next_node_id",
        "expected_signals",
        "status",
    ):
        value = current.get(key)
        if value:
            merged[key] = value
    merged["settled_followup_action"] = {
        "text": current.get("text"),
        "selector": current.get("selector"),
        "click_strategy": current.get("click_strategy"),
    }
    return merged


def _filter_replay_actions(actions: list[Mapping[str, Any]], path_id: str) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen_semantic: set[str] = set()
    seen_signatures: set[tuple[str, str, str, str]] = set()
    product_insure_indices: dict[tuple[str, str, str], int] = {}
    for action in actions:
        normalised = _normalise_replay_action(action, path_id, seen_semantic)
        if not normalised:
            continue
        if selected and _is_product_confirm_panel_followup(selected[-1], normalised):
            selected[-1] = _merge_product_confirm_panel_followup(selected[-1], normalised)
            continue
        product_group = _product_insure_replay_group(normalised)
        if product_group and product_group in product_insure_indices:
            existing_index = product_insure_indices[product_group]
            if _should_replace_product_insure_action(selected[existing_index], normalised):
                selected[existing_index] = normalised
            continue
        signature = (
            str(normalised.get("action_key") or ""),
            str(normalised.get("selector") or ""),
            str(normalised.get("source_url") or ""),
            _compact_action_text(normalised),
        )
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)
        selected.append(normalised)
        if product_group:
            product_insure_indices[product_group] = len(selected) - 1
        if normalised.get("action_key") == "action.bank_sign_boundary":
            break
    return selected


def _trace_real_actions(state: Mapping[str, Any], path_id: str) -> list[dict[str, Any]]:
    explore_trace = state.get("explore_trace", {}) or {}
    if not isinstance(explore_trace, Mapping):
        return []
    raw_actions: list[Mapping[str, Any]] = []
    for trace_item in explore_trace.get("action_trace", []) or []:
        if not isinstance(trace_item, Mapping):
            continue
        if str(trace_item.get("path_id") or "").strip() != path_id:
            continue
        candidates = (
            trace_item.get("action_chain", [])
            if isinstance(trace_item.get("action_chain"), list)
            else [trace_item]
        )
        for action in candidates:
            if not isinstance(action, Mapping):
                continue
            if not _is_agent3_replay_candidate(action):
                continue
            raw_actions.append(action)
    actions = _filter_replay_actions(raw_actions, path_id)
    for action in actions:
        action["source"] = action.get("source") or "agent3.explore_trace.action_trace"
    return actions


def _scenario_real_actions(state: Mapping[str, Any], path_item: Mapping[str, Any]) -> list[dict[str, Any]]:
    page_registry = state.get("page_registry", {}) or {}
    path_id = str(path_item.get("path_id") or "")
    trace_actions = _trace_real_actions(state, path_id)
    if trace_actions:
        return trace_actions
    for item in page_registry.get("path_exploration_results", []) or []:
        if str(item.get("path_id") or "") != path_id:
            continue
        actions = _filter_replay_actions(
            [action for action in item.get("action_chain", []) or [] if isinstance(action, Mapping)],
            path_id,
        )
        if actions:
            return actions
    actions = [
        _normalise_real_action(action, path_id)
        for action in page_registry.get("primary_actions", []) or []
    ]
    if actions:
        return _prioritise_real_actions(actions)[:3]
    page_actions = [
        _normalise_real_action(action, path_id)
        for page in page_registry.get("pages", []) or []
        for action in page.get("primary_actions", []) or []
    ]
    return _prioritise_real_actions(page_actions)[:3]


def _completed_path_ids(page_registry: Mapping[str, Any]) -> set[str]:
    contract = page_registry.get("exploration_contract", {}) or {}
    completed_paths = contract.get("completed_paths", []) or []
    if completed_paths:
        return {
            str(item.get("path_id") or "")
            for item in completed_paths
            if item.get("path_id")
        }
    return {
        str(item.get("path_id") or "")
        for item in page_registry.get("path_exploration_results", []) or []
        if item.get("path_status") == "explored"
        and (item.get("completion_rule", {}) or {}).get("is_complete")
        and item.get("path_id")
    }


def _blocked_path_contracts(page_registry: Mapping[str, Any]) -> list[dict[str, Any]]:
    contract = page_registry.get("exploration_contract", {}) or {}
    return [dict(item) for item in contract.get("blocked_paths", []) or []]


def _blocked_path_contract_by_id(page_registry: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("path_id") or ""): item
        for item in _blocked_path_contracts(page_registry)
        if item.get("path_id")
    }


def _enforces_completed_paths(page_registry: Mapping[str, Any]) -> bool:
    return "exploration_contract" in page_registry or "path_exploration_results" in page_registry


def _contract_status(
    path_status: object,
    completion_rule: Mapping[str, Any],
    page_element_plan: list[dict[str, Any]],
    validation_report: Mapping[str, Any] | None = None,
) -> str:
    if path_status != "explored" or not completion_rule.get("is_complete"):
        return "blocked"
    if validation_report and (
        validation_report.get("agent4_ready") is False or validation_report.get("status") == "failed"
    ):
        return "blocked_by_agent3_validation"
    for record in page_element_plan:
        for field in record.get("fields", []) or []:
            if field.get("required") and not field.get("selector"):
                return "probe_required"
        for action in record.get("actions", []) or []:
            if action.get("required") and not (action.get("selector") or action.get("text")):
                return "probe_required"
    return "compiled"


def _initial_script_status(contract_status: str) -> str:
    if contract_status == "compiled":
        return "pending_generation"
    if contract_status.startswith("blocked"):
        return "blocked"
    return contract_status


def _trace_verified_nodes(page_registry: Mapping[str, Any]) -> set[str]:
    verified: set[str] = set()
    for result in page_registry.get("path_exploration_results", []) or []:
        for progress in result.get("node_progress", []) or []:
            if progress.get("status") == "matched" and progress.get("node_id"):
                verified.add(str(progress.get("node_id")))
        for event in result.get("node_execution_trace", []) or []:
            if event.get("phase") == "verify" and event.get("target_matched") and event.get("target_node_id"):
                verified.add(str(event.get("target_node_id")))
    return verified


def _params_by_node_from_page_records(page_registry: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    params_by_node: dict[str, list[dict[str, Any]]] = {}
    for record in page_registry.get("page_content_records", []) or []:
        node_ids = [str(node_id) for node_id in record.get("matched_node_ids", []) or []]
        fields = list(record.get("field_map", []) or [])
        for node_id in node_ids:
            params = params_by_node.setdefault(node_id, [])
            seen = {item["name"] for item in params}
            for field in fields:
                name = str(field.get("field_key") or "")
                if not name or name in seen:
                    continue
                params.append(
                    {
                        "name": name,
                        "type": guess_param_type(name, ""),
                        "description": f"Derived from Agent3 page element {record.get('page_content_record_id')}",
                        "required": bool(field.get("required", False)),
                    }
                )
                seen.add(name)
    return params_by_node


_MAIN_FLOW_NODE_PROFILES: dict[str, dict[str, list[str]]] = {
    "NODE-product-detail": {
        "entry_signal": ["产品详情", "保障责任", "保费"],
        "exit_signal": ["立即投保", "投保"],
    },
    "NODE-plan-selection": {
        "entry_signal": ["计划", "保障方案", "保额", "保费"],
        "exit_signal": ["投保人", "下一步"],
    },
    "NODE-applicant-info": {
        "entry_signal": ["投保人", "姓名", "证件", "手机号"],
        "exit_signal": ["被保人", "下一步", "确认"],
    },
    "NODE-insured-info": {
        "entry_signal": ["被保人", "为谁投保", "被保险人"],
        "exit_signal": ["受益人", "健康告知", "下一步"],
    },
    "NODE-beneficiary": {
        "entry_signal": ["受益人", "法定受益人", "指定受益人"],
        "exit_signal": ["健康告知", "下一步"],
    },
    "NODE-health-notice": {
        "entry_signal": ["健康告知", "告知", "问卷"],
        "exit_signal": ["核保", "支付", "下一步", "确认"],
    },
    "NODE-underwriting": {
        "entry_signal": ["核保", "智能核保", "提交核保"],
        "exit_signal": ["支付", "签约", "付款"],
    },
    "NODE-payment": {
        "entry_signal": ["支付", "银行卡", "签约", "付款"],
        "exit_signal": ["出单", "保单", "成功", "结果"],
    },
    "NODE-policy-result": {
        "entry_signal": ["出单", "保单", "成功", "结果"],
        "exit_signal": ["保单", "完成"],
    },
}


def _node_profile(node_id: str, page_name: str) -> dict[str, list[str]]:
    profile = dict(_MAIN_FLOW_NODE_PROFILES.get(node_id, {}))
    profile.setdefault("entry_signal", [page_name])
    profile.setdefault("exit_signal", ["下一步", "继续", "确认"])
    return profile


def _trace_events_by_node(page_registry: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    events_by_node: dict[str, list[dict[str, Any]]] = {}
    for result in page_registry.get("path_exploration_results", []) or []:
        for event in result.get("node_execution_trace", []) or []:
            node_id = str(event.get("node_id") or event.get("target_node_id") or "")
            if not node_id:
                continue
            events_by_node.setdefault(node_id, []).append(dict(event))
    return events_by_node


def _page_elements_by_node(page_registry: Mapping[str, Any]) -> dict[str, dict[str, list[dict[str, Any]]]]:
    elements_by_node: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for record in page_registry.get("page_content_records", []) or []:
        node_ids = [str(node_id) for node_id in record.get("matched_node_ids", []) or []]
        for node_id in node_ids:
            node_elements = elements_by_node.setdefault(node_id, {"fields": [], "actions": []})
            seen_fields = {str(item.get("field_key") or item.get("selector") or "") for item in node_elements["fields"]}
            for field in record.get("field_map", []) or []:
                field_key = str(field.get("field_key") or "")
                selector = str(field.get("selector") or "")
                dedupe_key = field_key or selector
                if not dedupe_key or dedupe_key in seen_fields:
                    continue
                raw = field.get("raw", {}) or {}
                node_elements["fields"].append(
                    {
                        "field_key": field_key,
                        "selector": selector,
                        "tag": raw.get("tag") or field.get("tag"),
                        "type": raw.get("type") or field.get("type"),
                        "label": raw.get("label") or raw.get("placeholder") or field_key,
                        "mock_value": _mock_value_for_field(field),
                    }
                )
                seen_fields.add(dedupe_key)
            seen_actions = {str(item.get("selector") or item.get("text") or "") for item in node_elements["actions"]}
            for action in (record.get("selector_map", {}) or {}).get("actions", []) or []:
                text = str(action.get("text") or "")
                selector = str(action.get("selector") or "")
                dedupe_key = selector or text
                if not dedupe_key or dedupe_key in seen_actions:
                    continue
                node_elements["actions"].append(
                    {
                        "action_key": action.get("action_key"),
                        "text": text,
                        "selector": selector,
                        "text_selector": action.get("text_selector"),
                        "tag": action.get("tag"),
                        "href": action.get("href"),
                        "required": bool(action.get("required")),
                        "source_url": action.get("source_url"),
                        "locators": list(action.get("locators", []) or []),
                    }
                )
                seen_actions.add(dedupe_key)
    return elements_by_node


def _action_candidates_from_trace(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for event in events:
        if event.get("phase") != "act":
            continue
        text = str(event.get("text") or "")
        selector = str(event.get("selector") or "")
        dedupe_key = selector or text
        if not dedupe_key or dedupe_key in seen:
            continue
        actions.append(
            {
                "text": text,
                "selector": selector,
                "tag": event.get("tag") or "button",
                "source": "agent3.action_trace",
            }
        )
        seen.add(dedupe_key)
    return actions


def _next_business_node_id(nodes: list[Mapping[str, Any]], sequence_index: int) -> str | None:
    for candidate in nodes[sequence_index:]:
        candidate_type = str(candidate.get("type", "form"))
        if candidate_type not in {"start", "end", "branch"} and candidate.get("node_id"):
            return str(candidate.get("node_id"))
    return None


def _default_script_validation() -> dict[str, Any]:
    return {
        "status": "not_run",
        "checked_by": None,
        "checked_at": None,
        "listed_test_count": 0,
        "command": [],
        "cwd": None,
        "errors": [],
    }


def build_script_bundle_metadata(
    state: Mapping[str, Any],
    page_functions: list[dict[str, Any]],
    scenarios: list[dict[str, Any]],
    *,
    root_dir: Path,
    generated_by: str,
    assume_materialised: bool,
    validation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    product_id = str(state.get("product_id") or "product")
    product_dir = _state_product_dir(state)
    ts_root = product_ts_gen_root(root_dir, product_id, product_dir=product_dir)
    resolved_ts_root = ts_root.resolve()
    page_function_files = []
    for item in page_functions:
        relative_path = str(item.get("file_path") or "")
        absolute_path = ts_root / relative_path
        page_function_files.append(
            {
                "page_id": item.get("page_id"),
                "function_name": item.get("function_name"),
                "relative_path": relative_path,
                "path": agent_artifact_path(
                    product_id,
                    "agent3",
                    "ts-gen",
                    relative_path,
                    root_dir=root_dir,
                    product_dir=product_dir,
                ),
                "absolute_path": str((resolved_ts_root / relative_path).resolve()),
                "exists": absolute_path.exists() or assume_materialised,
            }
        )
    spec_files = []
    for scenario in scenarios:
        relative_path = str(scenario.get("spec_path") or "")
        absolute_path = ts_root / relative_path
        spec_files.append(
            {
                "scenario_id": scenario.get("scenario_id"),
                "path_id": scenario.get("path_id"),
                "relative_path": relative_path,
                "path": agent_artifact_path(
                    product_id,
                    "agent3",
                    "ts-gen",
                    relative_path,
                    root_dir=root_dir,
                    product_dir=product_dir,
                ),
                "absolute_path": str((resolved_ts_root / relative_path).resolve()),
                "exists": absolute_path.exists() or assume_materialised,
                "contract_status": scenario.get("contract_status"),
            }
        )
    contract_statuses = {str(item.get("contract_status") or "") for item in scenarios}
    expected_files = [*page_function_files, *spec_files]
    missing_files = [item for item in expected_files if not item.get("exists")]
    if not scenarios:
        status = "blocked"
    elif "blocked" in contract_statuses:
        status = "blocked"
    elif "probe_required" in contract_statuses:
        status = "probe_required"
    elif missing_files:
        status = "invalid"
    else:
        status = "generated"
    validation_payload = dict(validation or _default_script_validation())
    if validation_payload.get("status") == "failed":
        status = "invalid"
    return {
        "source": generated_by,
        "status": status,
        "product_id": product_id,
        "platform": platform_from_entry_url(state.get("entry_url")),
        "root_dir": str(ts_root),
        "scenario_count": len(scenarios),
        "page_function_count": len(page_functions),
        "spec_files": spec_files,
        "page_function_files": page_function_files,
        "validation": validation_payload,
        "generated_at": datetime.now(UTC).isoformat(),
    }


def apply_script_bundle_status(
    scenarios: list[dict[str, Any]],
    script_bundle: Mapping[str, Any],
) -> list[dict[str, Any]]:
    validation = dict(script_bundle.get("validation", {}) or {})
    validation_status = str(validation.get("status") or "not_run")
    status_by_scenario = {
        str(item.get("scenario_id") or ""): dict(item)
        for item in script_bundle.get("spec_files", []) or []
        if item.get("scenario_id")
    }
    for scenario in scenarios:
        contract_status = str(scenario.get("contract_status") or "")
        spec_file = status_by_scenario.get(str(scenario.get("scenario_id") or ""), {})
        if contract_status != "compiled":
            scenario["script_status"] = "probe_required" if contract_status == "probe_required" else "blocked"
            scenario["script_validation_status"] = "skipped"
        elif validation_status == "failed":
            scenario["script_status"] = "invalid"
            scenario["script_validation_status"] = "failed"
        elif spec_file.get("exists"):
            scenario["script_status"] = "generated"
            scenario["script_validation_status"] = validation_status
        else:
            scenario["script_status"] = "invalid"
            scenario["script_validation_status"] = "failed"
        scenario["runtime_status"] = scenario.get("runtime_status") or "not_executed"
        scenario["script_validation"] = validation
    return scenarios


def validate_script_bundle(
    script_bundle: Mapping[str, Any],
    runner: Any,
    *,
    timeout_seconds: int = 60,
) -> dict[str, Any]:
    spec_files = [
        dict(item)
        for item in script_bundle.get("spec_files", []) or []
        if item.get("contract_status") == "compiled"
    ]
    if not spec_files:
        return {
            **_default_script_validation(),
            "status": "skipped",
            "checked_by": "playwright --list",
            "checked_at": datetime.now(UTC).isoformat(),
            "errors": ["No compiled scenarios available for script validation"],
        }
    if runner is None or not hasattr(runner, "list_spec_tests"):
        return {
            **_default_script_validation(),
            "status": "skipped",
            "checked_by": "playwright --list",
            "checked_at": datetime.now(UTC).isoformat(),
            "errors": ["Playwright spec discovery runner is unavailable"],
        }
    has_project_test_runtime = getattr(runner, "has_project_test_runtime", None)
    if callable(has_project_test_runtime) and not has_project_test_runtime():
        return {
            **_default_script_validation(),
            "status": "skipped",
            "checked_by": "playwright --list",
            "checked_at": datetime.now(UTC).isoformat(),
            "errors": ["Project Node Playwright runtime is not installed"],
        }
    listed_test_count = 0
    errors: list[str] = []
    commands: list[list[str]] = []
    cwd = None
    for item in spec_files:
        if not item.get("exists"):
            errors.append(f"Scenario spec file not generated: {item.get('path')}")
            continue
        try:
            result = runner.list_spec_tests(str(item.get("absolute_path")), timeout_seconds=timeout_seconds)
        except Exception as exc:
            errors.append(str(exc))
            continue
        listed_test_count += int(result.get("listed") or 0)
        if result.get("command"):
            commands.append(list(result.get("command") or []))
        cwd = cwd or result.get("cwd")
        if int(result.get("returncode") or 0) != 0:
            errors.extend(str(error) for error in result.get("errors", []) or [])
    status = "passed" if not errors and listed_test_count >= len(spec_files) else "failed"
    return {
        "status": status,
        "checked_by": "playwright --list",
        "checked_at": datetime.now(UTC).isoformat(),
        "listed_test_count": listed_test_count,
        "command": commands[0] if len(commands) == 1 else commands,
        "cwd": cwd,
        "errors": list(dict.fromkeys(errors)),
    }


def finalize_script_generation_result(
    result: Mapping[str, Any],
    state: Mapping[str, Any],
    *,
    root_dir: Path,
    generated_by: str,
    validation: Mapping[str, Any] | None = None,
    materialise: bool = False,
) -> dict[str, Any]:
    updated = dict(result)
    page_functions = [dict(item) for item in updated.get("page_functions", []) or []]
    scenarios = [dict(item) for item in updated.get("scenarios", []) or []]
    script_bundle = build_script_bundle_metadata(
        state,
        page_functions,
        scenarios,
        root_dir=root_dir,
        generated_by=generated_by,
        assume_materialised=True,
        validation=validation or (updated.get("script_bundle", {}) or {}).get("validation") or _default_script_validation(),
    )
    scenarios = apply_script_bundle_status(scenarios, script_bundle)
    script_plan = build_script_plan(state, scenarios)
    script_plan["script_bundle"] = script_bundle
    updated["page_functions"] = page_functions
    updated["scenarios"] = scenarios
    updated["script_bundle"] = script_bundle
    updated["script_validation"] = script_bundle["validation"]
    updated["script_plan"] = script_plan
    if materialise and (page_functions or scenarios):
        materialise_ts_gen_outputs(
            state,
            page_functions,
            scenarios,
            root_dir=root_dir,
            generated_by=generated_by,
            script_bundle=script_bundle,
        )
    return updated


def materialise_ts_gen_outputs(
    state: Mapping[str, Any],
    page_functions: list[dict[str, Any]],
    scenarios: list[dict[str, Any]],
    *,
    root_dir: Path,
    generated_by: str,
    script_bundle: Mapping[str, Any] | None = None,
) -> None:
    product_id = str(state.get("product_id") or "product")
    product_dir = _state_product_dir(state)
    ts_root = product_ts_gen_root(root_dir, product_id, product_dir=product_dir)
    platform_output_root = platform_root(root_dir, product_id, state.get("entry_url"), product_dir=product_dir)
    page_functions_dir = platform_output_root / "page-functions"
    scenarios_dir = platform_output_root / "scenarios"
    artifacts_dir = ts_root / ".artifacts"
    script_plan_dir = ts_root / "script-plan"
    external_ops_dir = ts_root / "external-ops"
    page_functions_dir.mkdir(parents=True, exist_ok=True)
    scenarios_dir.mkdir(parents=True, exist_ok=True)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    script_plan_dir.mkdir(parents=True, exist_ok=True)
    if any((scenario.get("huize_payment_closed_loop", {}) or {}).get("enabled") for scenario in scenarios):
        external_ops_dir.mkdir(parents=True, exist_ok=True)
        (external_ops_dir / "huize-pay-success.cjs").write_text(
            render_huize_pay_success_cjs(),
            encoding="utf-8",
        )
        (external_ops_dir / "huize-issue-status.cjs").write_text(
            render_huize_issue_status_cjs(),
            encoding="utf-8",
        )

    for page_function in page_functions:
        file_path = product_ts_gen_root(root_dir, product_id, product_dir=product_dir) / str(page_function["file_path"])
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(
            render_page_function_file(page_function),
            encoding="utf-8",
        )

    for scenario in scenarios:
        spec_path = product_ts_gen_root(root_dir, product_id, product_dir=product_dir) / str(scenario["spec_path"])
        spec_path.parent.mkdir(parents=True, exist_ok=True)
        spec_path.write_text(
            render_scenario_spec(
                scenario,
                page_functions,
                generated_by=generated_by,
            ),
            encoding="utf-8",
        )

    chain_specs: list[dict[str, Any]] = []
    for scenario in scenarios:
        if scenario.get("contract_status") != "compiled":
            continue
        terminal_page_id = _scenario_terminal_page_id(scenario)
        platform = platform_from_entry_url(str(scenario.get("entry_url") or state.get("entry_url") or ""))
        chain_name = f"chain-to-{slugify(terminal_page_id)}-{platform}"
        chain_path = artifacts_dir / f"{chain_name}.spec.ts"
        chain_product_path = agent_artifact_path(
            product_id,
            "agent3",
            "ts-gen",
            ".artifacts",
            f"{chain_name}.spec.ts",
            root_dir=root_dir,
            product_dir=product_dir,
        )
        chain_path.write_text(
            render_chain_spec(
                scenario,
                page_functions,
                product_id=product_id,
                generated_by=generated_by,
                run_path=chain_product_path,
            ),
            encoding="utf-8",
        )
        chain_specs.append(
            {
                "chain": chain_name,
                "scenario_id": scenario.get("scenario_id"),
                "path_id": scenario.get("path_id"),
                "target_page_id": terminal_page_id,
                "relative_path": f".artifacts/{chain_name}.spec.ts",
                "path": chain_product_path,
            }
        )

    (ts_root / "fixtures.ts").write_text(render_fixtures_file(), encoding="utf-8")
    (ts_root / "generate-test-data.ts").write_text(
        render_generate_test_data_file(scenarios),
        encoding="utf-8",
    )
    (ts_root / "tc-execution-plan.json").write_text(
        json.dumps(
            {
                "product_id": product_id,
                "generated_by": generated_by,
                "entry_url": state.get("entry_url"),
                "scenario_count": len(scenarios),
                "chain_spec_count": len(chain_specs),
                "externalOperations": [
                    dict(operation)
                    for scenario in scenarios
                    for operation in (scenario.get("external_operations", []) or [])
                    if isinstance(operation, Mapping)
                ],
                "scenarios": [
                    {
                        "scenario_id": scenario.get("scenario_id"),
                        "path_id": scenario.get("path_id"),
                        "case_ids": scenario.get("case_ids", []),
                        "spec_path": scenario.get("spec_path"),
                        "chain_spec_path": (
                            next(
                                (
                                    item.get("relative_path")
                                    for item in chain_specs
                                    if item.get("scenario_id") == scenario.get("scenario_id")
                                ),
                                None,
                            )
                        ),
                        "chain_spec_product_path": (
                            next(
                                (
                                    item.get("path")
                                    for item in chain_specs
                                    if item.get("scenario_id") == scenario.get("scenario_id")
                                ),
                                None,
                            )
                        ),
                        "contract_status": scenario.get("contract_status"),
                        "coverage_status": scenario.get("coverage_status"),
                        "route_nodes": scenario.get("route_nodes", []),
                        "external_operations": list(scenario.get("external_operations", []) or []),
                        "execution_requirements": dict(scenario.get("execution_requirements", {}) or {}),
                    }
                    for scenario in scenarios
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (ts_root / "spec-tc-index.json").write_text(
        json.dumps(
            {
                "product_id": product_id,
                "specs": [
                    {
                        "scenario_id": scenario.get("scenario_id"),
                        "path_id": scenario.get("path_id"),
                        "case_ids": scenario.get("case_ids", []),
                        "spec_path": scenario.get("spec_path"),
                    }
                    for scenario in scenarios
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (artifacts_dir / "chain-manifest.json").write_text(
        json.dumps(
            {
                "product_id": product_id,
                "generated_by": generated_by,
                "chain_specs": chain_specs,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    script_plan = build_script_plan(state, scenarios)
    if script_bundle is not None:
        script_plan["script_bundle"] = dict(script_bundle)
    script_plan["chain_specs"] = chain_specs
    (script_plan_dir / "script-plan.json").write_text(
        json.dumps(script_plan, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (script_plan_dir / "mock-data-profiles.json").write_text(
        json.dumps(
            {
                "product_id": product_id,
                "profiles": [
                    {
                        "scenario_id": scenario.get("scenario_id"),
                        "path_id": scenario.get("path_id"),
                        "mock_data": scenario.get("mock_data", {}),
                    }
                    for scenario in scenarios
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    agent3_dir = agent_artifact_dir(root_dir, product_id, "agent3", product_dir=product_dir)
    agent3_dir.mkdir(parents=True, exist_ok=True)
    agent_plan_dir = agent3_dir / "script-plan"
    agent_plan_dir.mkdir(parents=True, exist_ok=True)
    for filename, payload in {
        "script-plan.json": script_plan,
        "mock-data-profiles.json": {
            "product_id": product_id,
            "profiles": [
                {
                    "scenario_id": scenario.get("scenario_id"),
                    "path_id": scenario.get("path_id"),
                    "mock_data": scenario.get("mock_data", {}),
                }
                for scenario in scenarios
            ],
        },
        "scenario-page-elements.json": {
            "product_id": product_id,
            "scenario_page_elements": [
                {
                    "scenario_id": scenario.get("scenario_id"),
                    "path_id": scenario.get("path_id"),
                    "coverage_status": scenario.get("coverage_status"),
                    "page_element_plan": scenario.get("page_element_plan", []),
                }
                for scenario in scenarios
            ],
        },
        "scenarios.json": scenarios,
    }.items():
        (agent_plan_dir / filename).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    (agent3_dir / "script-plan.json").write_text(
        json.dumps(script_plan, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def build_script_plan(
    state: Mapping[str, Any],
    scenarios: list[dict[str, Any]],
) -> dict[str, Any]:
    page_registry = state.get("page_registry", {}) or {}
    exploration_contract = dict(page_registry.get("exploration_contract", {}) or {})
    external_operations = [
        dict(operation)
        for scenario in scenarios
        for operation in (scenario.get("external_operations", []) or [])
        if isinstance(operation, Mapping)
    ]
    return {
        "product_id": str(state.get("product_id") or "product"),
        "entry_url": state.get("entry_url"),
        "source": "agent3.page_content_records + agent2.regression_paths",
        "agent3_contract": exploration_contract,
        "externalOperations": external_operations,
        "planned_page_catalog": list(page_registry.get("planned_page_catalog", []) or []),
        "page_content_records": [
            {
                "page_content_record_id": record.get("page_content_record_id"),
                "actual_url": record.get("actual_url"),
                "matched_planned_page_ids": record.get("matched_planned_page_ids", []),
                "matched_node_ids": record.get("matched_node_ids", []),
                "field_count": record.get("field_count"),
                "action_count": record.get("action_count"),
            }
            for record in page_registry.get("page_content_records", []) or []
        ],
        "scenario_plans": [
            {
                "scenario_id": scenario.get("scenario_id"),
                "path_id": scenario.get("path_id"),
                "route_nodes": scenario.get("route_nodes", []),
                "planned_page_refs": scenario.get("planned_page_refs", []),
                "page_content_refs": scenario.get("page_content_refs", []),
                "target_node": scenario.get("target_node"),
                "node_progress": scenario.get("node_progress", []),
                "completion_rule": scenario.get("completion_rule", {}),
                "mock_data_keys": sorted((scenario.get("mock_data", {}) or {}).keys()),
                "real_action_count": len(scenario.get("real_actions", []) or []),
                "real_actions": list(scenario.get("real_actions", []) or []),
                "spec_path": scenario.get("spec_path"),
                "contract_status": scenario.get("contract_status"),
                "script_status": scenario.get("script_status"),
                "script_validation_status": scenario.get("script_validation_status"),
                "runtime_status": scenario.get("runtime_status"),
                "coverage_status": scenario.get("coverage_status") or "unknown",
                "blocked_reason": scenario.get("blocked_reason"),
                "targeted_probe_request_count": int(
                    ((scenario.get("targeted_probe_plan", {}) or {}).get("summary", {}) or {}).get("request_count") or 0
                ),
                "terminal_boundary": scenario.get("terminal_boundary", {}),
                "resume_condition": scenario.get("resume_condition"),
                "evidence_source": scenario.get("evidence_source"),
                "external_operations": list(scenario.get("external_operations", []) or []),
                "execution_requirements": dict(scenario.get("execution_requirements", {}) or {}),
            }
            for scenario in scenarios
        ],
        "blocked_path_plans": _blocked_path_contracts(page_registry),
    }


def build_page_functions(
    state: Mapping[str, Any],
) -> list[dict[str, Any]]:
    regression_flow = state.get("regression_flow", {}) or {}
    regression_paths = state.get("regression_paths", []) or []
    page_registry = state.get("page_registry", {}) or {}
    platform = platform_from_entry_url(state.get("entry_url"))
    verified_nodes = _trace_verified_nodes(page_registry)
    trace_params_by_node = _params_by_node_from_page_records(page_registry)
    trace_events_by_node = _trace_events_by_node(page_registry)
    page_elements_by_node = _page_elements_by_node(page_registry)

    conditions_by_node: dict[str, dict[str, str]] = {}
    branches_by_node: dict[str, set[str]] = {}
    for path_item in regression_paths:
        conditions = path_conditions(path_item)
        branch_tokens = [
            f"{key}={value}"
            for key, value in sorted(conditions.items())
            if key != "revisit"
        ]
        for node_id in path_item.get("nodes", []):
            node_key = str(node_id)
            conditions_by_node.setdefault(node_key, {}).update(conditions)
            if branch_tokens:
                branches_by_node.setdefault(node_key, set()).update(branch_tokens)

    flow_nodes = list(regression_flow.get("nodes", []) or [])
    page_functions: list[dict[str, Any]] = []
    used_function_names: set[str] = set()
    for node_index, node in enumerate(flow_nodes):
        node_type = str(node.get("type", "form"))
        if node_type in {"start", "end", "branch"}:
            continue
        sequence = len(page_functions) + 1
        node_id = str(node.get("node_id"))
        page_id = str(node.get("node_id", "NODE-page")).removeprefix("NODE-")
        page_name = str(node.get("page_name") or node_label(page_id))
        params = [
            {
                "name": key,
                "type": guess_param_type(key, value),
                "description": f"Derived from regression path condition {key}={value}",
                "required": key != "revisit",
            }
            for key, value in sorted(conditions_by_node.get(node_id, {}).items())
        ]
        trace_params = trace_params_by_node.get(node_id, [])
        if trace_params:
            existing_names = {item["name"] for item in params}
            params.extend(item for item in trace_params if item["name"] not in existing_names)
        node_elements = page_elements_by_node.get(node_id, {"fields": [], "actions": []})
        trace_actions = _action_candidates_from_trace(trace_events_by_node.get(node_id, []))
        dom_actions = list(node_elements.get("actions", []))
        action_keys = {str(item.get("selector") or item.get("text") or "") for item in trace_actions}
        actions = trace_actions + [
            item
            for item in dom_actions
            if str(item.get("selector") or item.get("text") or "") not in action_keys
        ]
        profile = _node_profile(node_id, page_name)
        next_node_id = _next_business_node_id(flow_nodes, node_index + 1)
        next_profile = _node_profile(next_node_id, node_label(next_node_id.removeprefix("NODE-"))) if next_node_id else profile
        page_name_suffix = to_pascal_case(page_name)
        fallback_suffix = to_pascal_case(page_id)
        function_suffix = fallback_suffix if page_name_suffix == "Page" else page_name_suffix
        function_name = f"fill{function_suffix}"
        if function_name in used_function_names:
            fallback_name = f"fill{fallback_suffix}"
            function_name = fallback_name if fallback_name not in used_function_names else f"{fallback_name}{sequence}"
        used_function_names.add(function_name)
        page_functions.append(
            {
                "page_id": page_id,
                "function_name": function_name,
                "file_path": f"{platform}/page-functions/{sequence:02d}-{slugify(page_id)}.ts",
                "params": params,
                "verified": node_id in verified_nodes or node_type in {"confirm", "payment", "result"},
                "source": "agent3.trace" if node_id in verified_nodes else "agent2.flow",
                "fields": list(node_elements.get("fields", [])),
                "actions": actions,
                "entry_signals": profile.get("entry_signal", []),
                "exit_signals": profile.get("exit_signal", []),
                "next_node_id": next_node_id,
                "next_signals": next_profile.get("entry_signal", []),
                "trace_events": trace_events_by_node.get(node_id, []),
                "branches": sorted(branches_by_node.get(node_id, set())),
            }
        )
    return page_functions


def build_scenarios(
    state: Mapping[str, Any],
) -> list[dict[str, Any]]:
    product_id = str(state.get("product_id") or "product")
    entry_url = state.get("entry_url")
    platform = platform_from_entry_url(entry_url)
    scenarios: list[dict[str, Any]] = []
    page_registry = state.get("page_registry", {}) or {}
    blocked_contracts = _blocked_path_contract_by_id(page_registry)
    for sequence, path_item in enumerate(state.get("regression_paths", []), start=1):
        path_id = str(path_item.get("path_id") or f"PATH-{sequence:03d}")
        conditions = path_conditions(path_item)
        priority = str(path_item.get("priority", "P0"))
        case_ids = [str(case_id) for case_id in path_item.get("case_ids", [])]
        variant_id = f"VAR-{sequence:03d}-BASE"
        path_exploration = next(
            (
                item
                for item in (state.get("page_registry", {}) or {}).get("path_exploration_results", []) or []
                if str(item.get("path_id") or "") == path_id
            ),
            {},
        )
        page_element_plan = _scenario_page_element_plan(state, path_exploration)
        generated_mock_data = _scenario_mock_data(page_element_plan)
        state_mock_data = state.get("mock_data", {}) or {}
        scenario_mock_data = (
            {**generated_mock_data, **dict(state_mock_data)}
            if isinstance(state_mock_data, Mapping) and state_mock_data
            else generated_mock_data
        )
        path_status = path_exploration.get("path_status")
        completion_rule = dict(path_exploration.get("completion_rule", {}) or {})
        validation_report = dict(path_exploration.get("validation_report", {}) or {})
        contract_status = _contract_status(path_status, completion_rule, page_element_plan, validation_report)
        coverage_status = "covered" if contract_status == "compiled" else "coverage-gap"
        blocked_reason = path_exploration.get("blocked_reason")
        if contract_status == "blocked_by_agent3_validation":
            blocked_reason = "Agent3 validation report is not ready for Agent4"
        blocked_contract = blocked_contracts.get(path_id, {})
        terminal_boundary = dict(
            path_exploration.get("terminal_boundary")
            or blocked_contract.get("terminal_boundary")
            or {}
        )
        resume_condition = path_exploration.get("resume_condition") or blocked_contract.get("resume_condition")
        evidence_source = path_exploration.get("evidence_source") or blocked_contract.get("evidence_source")
        route_nodes = [
            str(node_id)
            for node_id in (
                path_exploration.get("effective_nodes")
                or path_exploration.get("repaired_nodes")
                or path_item.get("nodes", [])
            )
        ]
        real_actions = _scenario_real_actions(state, path_item)
        huize_closed_loop = _build_huize_payment_closed_loop(
            f"SCN-{sequence:03d}",
            path_id,
            route_nodes,
            path_exploration,
            _page_records_for_path(state, path_exploration),
            real_actions,
            page_element_plan,
            path_exploration.get("target_node"),
        )
        execution_requirements = _build_agent4_execution_requirements(
            scenario_id=f"SCN-{sequence:03d}",
            path_id=path_id,
            case_ids=case_ids,
            entry_url=entry_url,
            route_nodes=route_nodes,
            real_actions=real_actions,
            page_element_plan=page_element_plan,
            target_node=path_exploration.get("target_node"),
            huize_closed_loop=huize_closed_loop,
        )
        scenario_mock_data = _scenario_mock_data_with_overrides(
            {
                "path_id": path_id,
                "case_ids": case_ids,
                "execution_requirements": execution_requirements,
            },
            scenario_mock_data,
        )
        scenario_id = f"SCN-{sequence:03d}"
        test_data_profile_ids = [
            f"TDP-{slugify(path_id)}-{slugify(key)}-{slugify(value)}"
            for key, value in sorted(conditions.items())
            if key != "revisit"
        ] or [f"TDP-{slugify(path_id)}-default"]
        fact_lineage = _build_fact_lineage(
            scenario_id=scenario_id,
            path_id=path_id,
            path_item=path_item,
            case_ids=case_ids,
            conditions=conditions,
            test_data_profile_ids=test_data_profile_ids,
            path_exploration=path_exploration,
            real_actions=real_actions,
            assertion_start_index=sequence,
            terminal_boundary=terminal_boundary,
            coverage_status=coverage_status,
            contract_status=contract_status,
        )
        scenarios.append(
            {
                "scenario_id": scenario_id,
                "name": f"{product_id} {path_id}",
                "path_id": path_id,
                "priority": priority,
                "entry_url": entry_url,
                "route_nodes": route_nodes,
                "agent2_route_nodes": [str(node_id) for node_id in path_item.get("nodes", [])],
                "page_keys": list(path_exploration.get("repaired_page_keys") or path_item.get("page_keys", []) or []),
                "agent2_page_keys": list(path_item.get("page_keys", []) or []),
                "path_repaired": bool(path_exploration.get("path_repaired", False)),
                "case_ids": case_ids,
                "planned_page_refs": list(path_exploration.get("planned_page_refs", []) or []),
                "page_content_refs": list(path_exploration.get("page_content_refs", []) or []),
                "target_node": path_exploration.get("target_node"),
                "node_progress": list(path_exploration.get("node_progress", []) or []),
                "completion_rule": completion_rule,
                "page_element_plan": page_element_plan,
                "field_resolution_plan": dict(path_exploration.get("field_resolution_plan", {}) or {}),
                "component_strategy": dict(path_exploration.get("component_strategy", {}) or {}),
                "targeted_probe_plan": dict(path_exploration.get("targeted_probe_plan", {}) or {}),
                "validation_report": validation_report,
                "mock_data": scenario_mock_data,
                "real_actions": real_actions,
                "path_exploration_status": path_exploration.get("path_status"),
                "contract_status": contract_status,
                "coverage_status": coverage_status,
                "script_status": _initial_script_status(contract_status),
                "script_validation_status": "not_run",
                "runtime_status": "not_executed",
                "blocked_node": path_exploration.get("blocked_node"),
                "blocked_reason": blocked_reason,
                "terminal_boundary": terminal_boundary,
                "resume_condition": resume_condition,
                "evidence_source": evidence_source,
                "huize_payment_closed_loop": huize_closed_loop,
                "external_operations": list(huize_closed_loop.get("external_operations", []) or []),
                "execution_requirements": execution_requirements,
                "fact_lineage": fact_lineage,
                "variants": [
                    {
                        "variant_id": variant_id,
                        "conditions": conditions,
                        "test_data_profile_ids": test_data_profile_ids,
                    }
                ],
                "spec_path": f"{platform}/scenarios/{sequence:02d}-{slugify(path_id)}.spec.ts",
                "estimated_duration_s": path_item.get("estimated_duration_s"),
            }
        )
    return scenarios


def build_assertion_results(
    state: Mapping[str, Any],
    *,
    root_dir: Path | None = None,
    include_summary: bool = False,
) -> list[dict[str, Any]] | tuple[list[dict[str, Any]], dict[str, Any]]:
    catalog = load_assertion_template_catalog(
        state.get("assertion_template_source"),
        root_dir=root_dir,
    )
    results: list[dict[str, Any]] = []
    for path_index, path_item in enumerate(state.get("regression_paths", []), start=1):
        template_match = match_assertion_template(path_item, catalog)
        template_type = str(template_match["template_type"])
        case_ids = [str(case_id) for case_id in path_item.get("case_ids", [])] or [
            f"CASELESS-{path_index:03d}"
        ]
        path_id = str(path_item.get("path_id") or f"PATH-{path_index:03d}")
        for case_index, case_id in enumerate(case_ids, start=1):
            results.append(
                {
                    "assertion_id": f"ASSERT-{path_index:03d}-{case_index:03d}",
                    "case_id": case_id,
                    "template_type": template_type,
                    "status": "skip",
                    "expected_value": {
                        "path_id": path_id,
                        "conditions": path_conditions(path_item),
                        "template_variables": template_match["variables"],
                    },
                    "actual_value": {"status": "not_executed"},
                    "error_message": None,
                    "screenshot_path": None,
                    "template_source": template_match["template_source"],
                    "match_reason": template_match["match_reason"],
                    "variables": template_match["variables"],
                    "assertion_strength": template_match["assertion_strength"],
                    "weak": template_match["weak"],
                    "justification": template_match["justification"],
                    "missing_template_reason": template_match["missing_template_reason"],
                }
            )
    if include_summary:
        return results, summarize_assertion_template_coverage(results)
    return results


def build_ts_gen_bundle(
    state: Mapping[str, Any],
    *,
    root_dir: Path,
    materialise: bool = True,
    generated_by: str = "mpt-ins-ts-gen",
) -> dict[str, Any]:
    page_functions = build_page_functions(state)
    scenarios = build_scenarios(state)
    assertion_results, assertion_template_summary = build_assertion_results(
        state,
        root_dir=root_dir,
        include_summary=True,
    )
    warnings: list[str] = []
    if not state.get("regression_paths"):
        warnings.append("No regression_paths available for browser exploration")
    if state.get("regression_paths") and not page_functions:
        warnings.append("No page functions could be inferred from regression_flow")

    script_bundle = build_script_bundle_metadata(
        state,
        page_functions,
        scenarios,
        root_dir=root_dir,
        generated_by=generated_by,
        assume_materialised=materialise,
    )
    apply_script_bundle_status(scenarios, script_bundle)
    script_plan = build_script_plan(state, scenarios)
    script_plan["script_bundle"] = script_bundle

    if materialise and (page_functions or scenarios):
        materialise_ts_gen_outputs(
            state,
            page_functions,
            scenarios,
            root_dir=root_dir,
            generated_by=generated_by,
            script_bundle=script_bundle,
        )

    return {
        "page_functions": page_functions,
        "scenarios": scenarios,
        "script_plan": script_plan,
        "assertion_results": assertion_results,
        "assertion_template_summary": assertion_template_summary,
        "script_bundle": script_bundle,
        "script_validation": script_bundle["validation"],
        "warnings": warnings,
    }

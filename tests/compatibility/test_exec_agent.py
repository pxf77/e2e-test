from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_load_llm_wrapper_skips_when_no_provider_credentials(monkeypatch):
    from e2e_agent.agents import exec_agent

    for key in (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "OPENAI_API_KEY",
        "DEEPSEEK_API_KEY",
        "LITELLM_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)

    assert exec_agent._load_llm_wrapper() is None


def test_detect_failure_category_treats_entry_request_failure_as_env_issue():
    from e2e_agent.agents import exec_agent

    assert exec_agent._detect_failure_category("Entry page did not match planned node; body_excerpt=请求失败") == "env_issue"
    assert exec_agent._detect_failure_category("locator timeout; body_excerpt=请求超时") == "env_issue"
    assert (
        exec_agent._detect_failure_category(
            "page.locator('body') failed; body=系统正在维护，由于系统内部发生错误(错误代码：502)"
        )
        == "env_issue"
    )


def test_detect_failure_category_treats_browser_launch_permission_as_env_issue():
    from e2e_agent.agents import exec_agent

    assert (
        exec_agent._detect_failure_category(
            "Error: browserType.launch: spawn EPERM Call log: <launching> "
            "C:\\Users\\demo\\AppData\\Local\\Google\\Chrome\\Application\\chrome.exe "
            "--remote-debugging-pipe"
        )
        == "env_issue"
    )


def test_detect_failure_category_treats_h5_bootstrap_null_state_as_env_issue():
    from e2e_agent.agents import exec_agent

    assert (
        exec_agent._detect_failure_category(
            "Health notice action did not find no-issue option\n"
            "body_excerpt=Cannot destructure property `merchantId` of 'undefined' or 'null'."
        )
        == "env_issue"
    )


def test_visible_runner_script_supports_business_questionnaire_strategy():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()

    assert "async function answerQuestionnaire" in script
    assert "action.answer_questionnaire" in script
    assert "business_questionnaire_rule" in script
    assert "first_option_per_group" in script


def test_visible_runner_script_supports_adapt_questionnaire_dom():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()

    assert "customQuestionNodes" in script
    assert ".adapt-question-wrap [data-number]" in script
    assert "[data-number].answer-radio" in script
    assert "input.insure-label" in script
    assert ".answer-radio-item" in script
    assert "!hasOptionChild(item.element)" in script
    assert "getAttribute?.('value')" in script
    assert "fillQuestionnaireInlineInputs" in script
    assert "js-questionnaire-inline-input" in script
    assert "const values = ['1', '50', '10', '20'];" in script


def test_visible_runner_maps_sms_request_alias_and_auth_page_node():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()

    assert "sms-code-request" in script
    assert "发送认证短信" in script
    assert "获取认证短信" in script
    assert "/\\/authentication\\/detail(?:\\?|$)/.test(url)" in script
    assert "身份认证|投保意愿认证|验证码|证件照片|提交认证|发送认证短信" in script
    assert "NODE-risk-control" in script


def test_visible_runner_script_uses_business_questionnaire_rule():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()

    assert "business_questionnaire_rule" in script
    assert "isPurposeQuestion" in script
    assert "保障需求" in script
    assert "保险需求" in script
    assert "目的" in script
    assert "为了什么" in script
    assert "想通过" not in script
    assert "主要解决" not in script
    assert "担心" not in script
    assert "options.length - 1" in script
    assert "preferredBusinessQuestionnaireChoice" in script
    assert "business-safe-option" in script
    assert "未来生活规划|养老|子女教育|退休收入|保单利益" in script
    assert "20%及以下" in script
    assert "一次性支付|一次性" in script


def test_visible_runner_script_auto_answers_followup_questionnaire_when_action_absent():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()

    assert "autoAnswerQuestionnaireIfPresent" in script
    assert "auto-followup-questionnaire" in script
    assert "auto_questionnaire_result" in script
    assert "button.js-adapt-question-btn" in script
    assert "button-link-text-submit" in script
    assert "tag: 'button, a, [role=\"button\"]'" in script
    assert "let questionCount = await page.locator(questionnaireSelector).count().catch(() => 0);" in script
    assert "const hasSuitabilityPage = isSuitabilityQuestionnairePageUrl(page.url()) || (action?.planned_from_node_id === 'NODE-suitability' && questionCount > 0);" in script
    assert "if (!questionCount && !hasHealthNotice && !hasSuitabilityPage) return null;" in script


def test_visible_runner_waits_for_suitability_questionnaire_controls_before_answering():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    auto_block = script[
        script.index("async function autoAnswerQuestionnaireIfPresent"):
        script.index("function isAutoWaitAction")
    ]
    recovery_block = script[
        script.index("async function recoverSuitabilityTaskAfterSubmitIfNeeded"):
        script.index("async function clickLastVisibleByPattern")
    ]

    assert "function questionnaireControlSelector()" in script
    assert "async function waitForQuestionnaireControls(page" in script
    assert "readiness = await waitForQuestionnaireControls(page" in auto_block
    assert "suitability questionnaire controls not rendered" in auto_block
    assert "|| navigation.opened" not in recovery_block


def test_visible_runner_normalizes_stale_suitability_advance_on_h5_insure_form():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    loop_start = script.index("for (let index = 0; index < actions.length; index += 1)")
    loop_block = script[loop_start: script.index("if (isSyntheticMinimalDataAction(action))", loop_start)]

    assert "function isStaleSuitabilityAdvanceOnH5InsureForm(action, currentUrl)" in script
    assert "function normalizeStaleSuitabilityAdvanceAsH5Submit(action)" in script
    assert "let action = actions[index];" in loop_block
    assert "action = normalizeStaleSuitabilityAdvanceAsH5Submit(action);" in loop_block
    assert "stale-suitability-h5-submit" in script
    assert "planned_from_node_id: 'NODE-insure-form'" in script


def test_visible_runner_auto_advances_suitability_questionnaire_before_next_click():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    normal_action_block = script[
        script.index("let locator = await locatorForAction(page, action);"):
        script.index("await locator.scrollIntoViewIfNeeded", script.index("let locator = await locatorForAction(page, action);"))
    ]

    assert "function isSuitabilityQuestionnairePageUrl(value)" in script
    assert "id === 'NODE-suitability'" in script
    assert "function isQuestionnaireAdvanceAction(action)" in script
    assert "if (isQuestionnaireAdvanceAction(action))" in normal_action_block
    assert (
        "await autoAnswerQuestionnaireIfPresent(page, action, index + 1, "
        "agent3SuitabilityAnswerActionsForStep(actions, index, appliedAgent3SuitabilityAnswerKeys));"
        in normal_action_block
    )
    assert "function agent3SuitabilityAnswerActionsForStep(actions, currentIndex, appliedKeys = new Set())" in script
    assert "const appliedAgent3SuitabilityAnswerKeys = new Set();" in script
    assert "function agent3SuitabilityAnswerKey(action)" in script
    assert "agent3-suitability-replay" in script
    assert "function isAlreadySelected(element)" in script
    assert "already_selected: true" in script
    assert "autoQuestionnaireResult?.submitted" in normal_action_block
    assert "type: 'questionnaire-auto-advance'" in normal_action_block
    assert "await captureScreenshot(page, index + 1, `step-${index + 1}-auto-questionnaire`);" in normal_action_block


def test_visible_runner_generic_questionnaire_does_not_toggle_selected_choices():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    answer_block = script[
        script.index("async function answerQuestionnaire(page, action)"):
        script.index("async function verifyAgent3SuitabilityChoiceSelection(page, marker)")
    ]

    assert "function choiceSelected(element)" in answer_block
    assert "if (choiceSelected(chosen.element))" in answer_block
    assert "already_selected: true" in answer_block
    assert "click_strategy: 'js-custom-questionnaire-already-selected'" in answer_block
    assert "if (choiceSelected(chosen.element))" in answer_block
    assert "click_strategy: 'js-questionnaire-already-selected'" in answer_block


def test_visible_runner_clicks_bottom_suitability_questionnaire_submit():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    auto_block = script[
        script.index("async function autoAnswerQuestionnaireIfPresent(page, action, step, agent3AnswerActions = [])"):
        script.index("function isAutoWaitAction(action)")
    ]

    assert "async function clickSuitabilityQuestionnaireSubmit(page)" in script
    assert "const submitClick = await clickSuitabilityQuestionnaireSubmit(page);" in auto_block
    assert "submitStrategy = submitClick.strategy;" in auto_block
    assert "submit_click: submitClick" in auto_block
    assert "clickLastVisibleByPattern(page, submitLocator" in script
    assert "/^(提交|下一步|确定|确认|继续|完成)$/" in script


def test_visible_runner_suitability_submit_uses_h5_touch_react_click():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    submit_block = script[
        script.index("async function clickSuitabilityQuestionnaireSubmit(page)"):
        script.index("async function autoAnswerQuestionnaireIfPresent")
    ]

    assert "async function bestSuitabilityQuestionnaireSubmitLocator(page)" in script
    assert "const scoredSubmit = await bestSuitabilityQuestionnaireSubmitLocator(page);" in submit_block
    assert "const tap = await clickH5SubmitLocator(page, scoredSubmit.locator);" in submit_block
    assert "tap," in submit_block
    assert "h5-questionnaire-bottom-fixed-score" in submit_block
    assert "bottom-visible-questionnaire-submit" in submit_block


def test_visible_runner_suitability_submit_api_fallback_after_ui_noop():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    auto_start = script.index("async function autoAnswerQuestionnaireIfPresent(page, action, step, agent3AnswerActions = [])")
    auto_block = script[
        auto_start:
        script.index("function cssAttrValue", auto_start)
    ]
    fallback_start = script.index("async function submitSuitabilityQuestionnaireViaApiIfNeeded")
    fallback_block = script[
        fallback_start:
        script.index("async function autoAnswerQuestionnaireIfPresent", fallback_start)
    ]

    assert "async function submitSuitabilityQuestionnaireViaApiIfNeeded(page, beforeUrl, answerResult, inlineResyncResult, submitClick)" in script
    assert "const apiFallbackResult = await submitSuitabilityQuestionnaireViaApiIfNeeded(page, before, answerResult, inlineResyncResult, submitClick);" in auto_block
    assert "api_fallback_result: apiFallbackResult" in auto_block
    assert "if (!isSuitabilityQuestionnairePageUrl(beforeUrl) || page.url() !== beforeUrl) return" in fallback_block
    assert "/api/apps/cps/risknotify/verify?md=" in fallback_block
    assert "questionnaireTemplate" in fallback_block
    assert "multipleAnswerMap" in fallback_block
    assert "answerExtMap" in fallback_block
    assert "window.location.href = resultUrl;" in fallback_block
    assert "strategy: 'risknotify-verify-api-fallback'" in fallback_block


def test_visible_runner_resyncs_suitability_inline_inputs_before_submit():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    auto_block = script[
        script.index("async function autoAnswerQuestionnaireIfPresent(page, action, step, agent3AnswerActions = [])"):
        script.index("function isAutoWaitAction(action)")
    ]

    assert "async function resyncSuitabilityInlineInputs(page, answerActions = [])" in script
    resync_block = script[
        script.index("async function resyncSuitabilityInlineInputs(page, answerActions = [])"):
        script.index("async function autoAnswerQuestionnaireIfPresent", script.index("async function resyncSuitabilityInlineInputs"))
    ]
    assert "await locator.fill('')" in resync_block
    assert "await locator.fill(String(value))" in resync_block
    assert "await locator.press('Tab')" in resync_block
    assert "suitability-inline-input-resync" in resync_block
    assert "const inlineResyncResult = hasSuitabilityPage" in auto_block
    assert "await resyncSuitabilityInlineInputs(page, agent3AnswerActions)" in auto_block
    assert "inline_resync_result: inlineResyncResult" in auto_block


def test_visible_runner_records_applied_agent3_suitability_answers_before_replay():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    loop_start = script.index("for (let index = 0; index < actions.length; index += 1)")
    loop_block = script[loop_start: script.index("const finalMatchedNodes = await matchNodes", loop_start)]

    assert "const appliedAgent3SuitabilityAnswerKeys = new Set();" in script
    assert "appliedAgent3SuitabilityAnswerKeys.add(agent3SuitabilityAnswerKey(action));" in loop_block
    assert "return !appliedKeys.has(agent3SuitabilityAnswerKey(action));" in script
    assert "agent3SuitabilityAnswerActionsForStep(actions, index, appliedAgent3SuitabilityAnswerKeys)" in loop_block


def test_visible_runner_submit_recovery_uses_forward_suitability_answers():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    recovery_block = script[
        script.index("async function recoverSuitabilityTaskAfterSubmitIfNeeded"):
        script.index("async function clickLastVisibleByPattern")
    ]
    helper_block = script[
        script.index("function agent3SuitabilityAnswerActionsForSubmitRecovery"):
        script.index("function isSuitabilityQuestionnairePageUrl")
    ]

    assert "function agent3SuitabilityAnswerActionsForSubmitRecovery(actions, currentIndex, appliedKeys = new Set())" in script
    assert "const answerActions = agent3SuitabilityAnswerActionsForSubmitRecovery(actions, currentIndex, appliedKeys);" in recovery_block
    assert ".slice(currentIndex + 1)" in helper_block
    assert "if (isQuestionnaireAdvanceAction(action) && collected.length) break;" in helper_block
    assert "collected.push(action);" in helper_block
    assert "return agent3SuitabilityAnswerActionsForStep(actions, currentIndex, appliedKeys);" in helper_block


def test_visible_runner_skips_stale_synthetic_actions_on_suitability_page():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    loop_start = script.index("for (let index = 0; index < actions.length; index += 1)")
    loop_block = script[loop_start: script.index("const finalMatchedNodes = await matchNodes", loop_start)]

    assert "if (isSuitabilityQuestionnairePageUrl(page.url()) && !isAgent3SuitabilityAnswerAction(action))" in loop_block
    assert "click_strategy: 'skip-stale-synthetic-on-suitability'" in loop_block
    assert "step-${index + 1}-skip-stale-synthetic-suitability" in loop_block


def test_visible_runner_accepts_auth_page_for_stale_health_notice_expectation():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()

    assert "function isStaleHealthNoticeExpectationOnAuthPage" in script
    assert "expectedNodeId !== 'NODE-health-notice'" in script
    assert "matchedNodes.includes('NODE-risk-control')" in script
    assert "/\\/authentication(?:\\/detail)?(?:\\?|$)/.test(afterPath)" in script
    assert "isStaleHealthNoticeExpectationOnAuthPage(action, expectedNodeId, afterPath, matchedNodes)" in script


def test_visible_runner_accepts_reached_target_url_over_stale_node_expectation():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()

    assert "function targetPathReached" in script
    assert "targetPathReached(afterPath, targetPath)" in script
    assert "if (targetPath !== beforePath && targetPathReached(afterPath, targetPath)) return;" in script


def test_visible_runner_treats_pay_url_as_payment_progress():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()

    assert "function isPaymentPageUrl" in script
    assert "String(nodeId).includes('payment') && isPaymentPageUrl(url, text)" in script
    assert "function isStaleNodeExpectationPastPaymentBoundary" in script
    assert "isStaleNodeExpectationPastPaymentBoundary(expectedNodeId, afterPath, matchedNodes)" in script


def test_visible_runner_verifies_agent3_suitability_checkbox_after_dom_click():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()

    assert "data-agent3-suitability-target" in script
    assert "verifyAgent3SuitabilityChoiceSelection" in script
    assert "forceAgent3SuitabilityChoiceSelection" in script
    assert "playwright-trusted-choice-click" in script
    assert "selected_after: selectionState.selected" in script
    assert "result.selected_after = Boolean" in script


def test_visible_runner_agent3_suitability_choice_uses_touch_react_click():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    block_start = script.index("async function applyAgent3SuitabilityAnswer")
    block_end = script.index("async function applyAgent3SuitabilityAnswers", block_start)
    block = script[block_start:block_end]

    assert "async function dispatchLikeUser(element)" in block
    assert "agent3_suitability_react_click" in block
    assert "react_choice_click" in block
    assert "click_strategy: 'react-touch-choice-click'" in block
    assert "for (const handlerName of ['onTouchStart', 'onTouchEnd', 'onMouseDown', 'onMouseUp', 'onClick'])" in block
    assert "new TouchEvent(type" in block


def test_visible_runner_agent3_suitability_choice_resyncs_with_trusted_click():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    block_start = script.index("async function applyAgent3SuitabilityAnswer")
    block_end = script.index("async function applyAgent3SuitabilityAnswers", block_start)
    block = script[block_start:block_end]

    assert "async function prepareAgent3SuitabilityChoiceForPlaywrightClick(page, marker)" in script
    assert "playwright-trusted-choice-click" in block
    assert "playwright_trusted_resync_prepare" in block
    assert "&& !result.already_selected" in block
    assert "&& !result.selected_after" not in block


def test_visible_runner_handles_suitability_result_before_stale_synthetic_bank_actions():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    loop_start = script.index("for (let index = 0; index < actions.length; index += 1)")
    loop_block = script[loop_start: script.index("const finalMatchedNodes = await matchNodes", loop_start)]

    assert "function isSuitabilityResultPageUrl(value)" in script
    assert "async function clickSuitabilityResultContinueIfPresent(page)" in script
    assert "if (isSuitabilityResultPageUrl(page.url()))" in loop_block
    assert "'suitability-result-continue'" in loop_block
    assert "'skip-stale-synthetic-on-suitability-result'" in loop_block
    assert loop_block.index("if (isSuitabilityResultPageUrl(page.url()))") < loop_block.index(
        "let taskModalResult = await clickTaskModalGoCompleteIfPresent(page);"
    )


def test_visible_runner_does_not_match_later_nodes_on_suitability_mismatch_result():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()

    assert "function isSuitabilityMismatchResultPage(url, text)" in script
    assert "if (isSuitabilityMismatchResultPage(url, text)) return String(nodeId) === 'NODE-suitability';" in script


def test_visible_runner_repairs_suitability_mismatch_by_reevaluating_period_answers():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    loop_start = script.index("for (let index = 0; index < actions.length; index += 1)")
    loop_block = script[loop_start: script.index("const finalMatchedNodes = await matchNodes", loop_start)]

    assert "async function repairSuitabilityMismatchAndResubmit(page)" in script
    assert "suitability-mismatch-repair" in script
    assert "reason_flags: reasonFlags" in script
    assert "'mismatch-period-longest-option'" in script
    assert "'mismatch-payment-longest-option'" in script
    assert "await repairSuitabilityMismatchAndResubmit(page)" in loop_block


def test_visible_runner_resumes_current_node_after_suitability_result_continue():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    loop_start = script.index("for (let index = 0; index < actions.length; index += 1)")
    loop_block = script[loop_start: script.index("const finalMatchedNodes = await matchNodes", loop_start)]

    assert "const resultResumeIndex = findTaskModalResumeIndex(actions, index, matchedNodes);" in loop_block
    assert "index = resultResumeIndex - 1;" in loop_block


def test_visible_runner_repairs_suitability_result_immediately_after_questionnaire_submit():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    normal_action_block = script[
        script.index("if (isQuestionnaireAdvanceAction(action))"):
        script.index("result.node_matches.push({ step: index + 1, label: `step-${index + 1}-auto-questionnaire`")
    ]

    assert "let suitabilityResultClick = null;" in normal_action_block
    assert "let suitabilityRepair = null;" in normal_action_block
    assert "if (isSuitabilityResultPageUrl(page.url()))" in normal_action_block
    assert "await repairSuitabilityMismatchAndResubmit(page)" in normal_action_block
    assert "auto-questionnaire+suitability-result-continue" in normal_action_block


def test_visible_runner_matches_only_suitability_node_on_adapt_pages():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()

    assert "if (isSuitabilityQuestionnairePageUrl(url)) return String(nodeId) === 'NODE-suitability';" in script
    assert "if (isSuitabilityResultPageUrl(url)) return String(nodeId) === 'NODE-suitability';" in script


def test_visible_runner_script_accepts_questionnaire_warning_modal():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()

    assert "acceptQuestionnaireWarningIfPresent" in script
    assert "投保风险警示确认书" in script
    assert "阅读并同意" in script
    assert "questionnaire-warning-confirm" in script


def test_visible_runner_clicks_agree_all_dialog_with_dom_fallback():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()

    assert "async function clickAgreeAllAction(page)" in script
    assert "action.action_key === 'action.agree_all'" in script
    assert "agreement-action-click" in script
    assert "element.click()" in script


def test_visible_runner_clicks_buy_now_by_visible_text_and_requires_progress():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()

    assert "async function clickBuyNowAction(page, action, beforeUrl, nextEntryUrl = '')" in script
    assert "action.action_key === 'action.buy_now'" in script
    assert "buy-now-action-click" in script
    assert "buy_now action did not open next step" in script


def test_visible_runner_replays_agent3_premium_quote_entry_flow():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    buy_now_block = script[
        script.index("async function clickBuyNowAction"):
        script.index("async function clickAgreeAllAction")
    ]

    assert "function isPremiumQuoteAction(action)" in script
    assert "async function clickPremiumQuoteAction(page)" in script
    assert "async function settleProductEntryFlow(page)" in script
    assert "保费试算" in script
    assert "trial-panel-confirm" in script
    assert "premium-quote-prerequisite" in script
    assert "prerequisite_only: true" in script
    assert buy_now_block.index("await clickPremiumQuoteAction(page)") < buy_now_block.index(
        "await clickActionByAgent3Locator"
    )


def test_visible_runner_replays_agent3_buy_now_locator_before_text_guessing():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    buy_now_block = script[
        script.index("async function clickBuyNowAction"):
        script.index("async function clickAgreeAllAction")
    ]

    assert "async function clickActionByAgent3Locator" in script
    assert "action.selector" in buy_now_block
    assert "action.locators" in buy_now_block
    assert "agent3-locator-replay" in buy_now_block
    assert buy_now_block.index("await clickActionByAgent3Locator") < buy_now_block.index("page.evaluate")
    assert "投\\\\s*保|投保|立即投保" in buy_now_block


def test_visible_runner_uses_mobile_context_for_h5_payloads():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()

    assert "function contextOptionsForPayload(payload)" in script
    assert "options.isMobile = true" in script
    assert "options.hasTouch = true" in script
    assert "options.userAgent" in script


def test_visible_runner_dispatches_agent3_click_strategy_without_normal_downgrade():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()

    assert "function isAgent3BuyNowStrategy(action)" in script
    assert "function isAgent3HealthNoticeStrategy(action)" in script
    assert "action.action_key === 'action.buy_now' || isAgent3BuyNowStrategy(action)" in script
    assert "action.action_key === 'action.answer_health_notice' || isAgent3HealthNoticeStrategy(action)" in script
    assert "Agent4 action did not advance toward expected target_url" in script


def test_visible_runner_health_notice_uses_real_click_and_progress_assertion():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    health_branch = script[
        script.index("if (action.action_key === 'action.answer_health_notice' || isAgent3HealthNoticeStrategy(action))"):
        script.index("if (isAutoWaitAction(action))")
    ]

    assert "async function clickHealthNoticeNoIssueCta(page)" in script
    assert "playwright_health_notice_click" in script
    assert "await clickHealthNoticeNoIssueCta(page)" in script
    assert "const primaryLocator = page.locator" in script
    assert "const target = chosen.element.closest" in script
    assert "async function clickHealthNoticeSafeOptionLikeAgent3(page)" in script
    assert "scrollHealthNoticeToBottom()" in script
    assert "agent3_health_notice_click" in script
    assert "async function enrichMatchedNodesWithReadyState(page, matchedNodes, expectedNodeId)" in script
    assert "matchedNodes = await enrichMatchedNodesWithReadyState(page, matchedNodes, action.expected_next_node_id || action.planned_to_node_id);" in health_branch
    assert "function isOracleAgreementAction(action)" in script
    assert "action.action_key === 'action.agree_all' || isOracleAgreementAction(action)" in script
    assert "const agreementResult = await ensureAllAgreementsConfirmed(page);" in script
    assert "await confirmAgreementDialogs(page, { allowBodyFallback: false });" in script
    assert "targetPath !== beforePath && targetPathReached(afterPath, targetPath)" in script
    assert (
        "assertAgent4ActionProgress({ ...action, skip_if_absent: false }, before, page.url(), matchedNodes, "
        "'health-notice-no-issue');"
    ) in health_branch


def test_visible_runner_syncs_policy_start_date_before_submit():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    submit_block = script[
        script.index("if ((action.action_key === 'action.submit' || isH5SubmitAction(action)) && action.planned_from_node_id === 'NODE-insure-form')"):
        script.index("if (action.action_key === 'action.answer_questionnaire'")
    ]

    assert "const rawPolicyStartDate = String(mockData.insuranceDate_102 || mockData.insuranceDate || mockData['policy.start_date'] || '');" in script
    assert "function normalizePolicyStartDate(value)" in script
    assert "const fallbackDate = new Date(today.getFullYear(), today.getMonth(), today.getDate() + 1);" in script
    assert "if (parsed && formatDate(parsed) === fallbackText) return fallbackText;" in script
    assert "if (parsed) return formatDate(parsed);" not in script
    assert "getDate() + 10" not in script
    assert "const policyStartDate = normalizePolicyStartDate(rawPolicyStartDate);" in script
    assert "setRowExtraText(/起保日期|保险起期|生效日期/, policyStartDate, '起保日期');" in script
    assert "plain.module.102" in script
    assert "next = next.setIn([...base, 'insuranceDate'], policyStartDate);" in script
    assert submit_block.count("await syncVisibleH5InsureModelState(page);") >= 2


def test_visible_runner_submit_uses_mouse_and_dom_fallback_after_touch_tap():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    submit_function = script[
        script.index("async function clickH5SubmitLocator"):
        script.index("async function inspectPostSubmitDiagnostics")
    ]

    assert "async function clickH5SubmitLocator(page, locator)" in script
    assert "await clickH5SubmitLocator(page, scoredSubmit.locator)" in submit_function
    assert "await clickH5SubmitLocator(page, locator)" in submit_function
    assert "mouse_click" in script
    assert "dom_click" in script
    assert "__reactProps$" in script
    assert "react_submit_click" in script
    assert "for (const handlerName of ['onTouchStart', 'onTouchEnd', 'onMouseDown', 'onMouseUp', 'onClick'])" in submit_function
    assert "isDefaultPrevented() { return this.defaultPrevented; }" in submit_function
    assert "react_submit_handler_error" in submit_function
    assert "react_submit_native_onclick" in submit_function
    assert "function submitClickableAncestor(element)" in submit_function
    assert "const target = submitClickableAncestor(element);" in submit_function
    assert "const viewportWidth = window.innerWidth || document.documentElement.clientWidth || 390;" in submit_function
    assert "if (rect.height > 180 || rect.width > viewportWidth * 0.96) continue;" in submit_function
    assert "if (!clickableLike) continue;" in submit_function
    assert "function submitAncestorChain(element)" in submit_function
    assert "submit_ancestor_chain: submitAncestorChain(element)" in submit_function


def test_visible_runner_preserves_h5_product_footer_strategy_before_agent3_locator_replay():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    buy_now_block = script[
        script.index("async function clickBuyNowAction"):
        script.index("async function clickAgreeAllAction")
    ]

    assert "function isH5ProductFooterInsureAction(action)" in script
    assert "async function clickH5ProductFooterInsureAction(page, beforeUrl)" in script
    assert "mouse-h5-product-footer-insure" in script
    assert "page.touchscreen.tap" in script
    assert buy_now_block.index("await clickH5ProductFooterInsureAction(page, beforeUrl)") < buy_now_block.index(
        "await clickActionByAgent3Locator"
    )


def test_visible_runner_scores_bottom_buy_now_before_broad_text_fallback():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    footer_block = script[
        script.index("async function clickH5ProductFooterInsureAction"):
        script.index("function isH5SubmitAction")
    ]

    assert "async function exactBottomH5BuyNowButtonLocator(page)" in script
    assert "async function bestH5BuyNowButtonLocator(page)" in script
    assert "const badCopy = /保费试算|投保须知|投保人|被保险人|告知书|声明|流程|说明" in script
    assert "strategy: 'h5-exact-bottom-buy-now'" in footer_block
    assert "strategy: 'h5-buy-now-bottom-fixed-score'" in footer_block
    assert "tap_error: String(error?.message || error)" in footer_block
    assert footer_block.index("const exactBottomBuyNow = await exactBottomH5BuyNowButtonLocator(page)") < footer_block.index(
        "const scoredBuyNow = await bestH5BuyNowButtonLocator(page)"
    )
    assert footer_block.index("const scoredBuyNow = await bestH5BuyNowButtonLocator(page)") < footer_block.index(
        "const broadButton = page"
    )
    assert "input[type=\"button\"], input[type=\"submit\"]')" in footer_block


def test_visible_runner_buy_now_progress_does_not_accept_product_detail_body_copy():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    progress_block = script[
        script.index("async function buyNowAdvanced"):
        script.index("async function clickBuyNowAction")
    ]

    assert "/\\/product\\/detail/.test(url)" in progress_block
    assert "return false" in progress_block
    assert "/healthInform|health|notice|inform|product\\/insure/i.test(url)" in progress_block


def test_visible_runner_product_confirm_panel_runs_before_continuation_dialog():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    settle_block = script[
        script.index("async function settleProductEntryFlow"):
        script.index("async function buyNowAdvanced")
    ]

    assert settle_block.index("acceptProductConfirmPanelIfPresent") < settle_block.index(
        "acceptContinuationDialogIfPresent"
    )
    assert "filter({ hasText: /继续投保|已有|未完成|重复投保/ })" in script


def test_visible_runner_preserves_h5_submit_strategy_after_form_repair():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    submit_block = script[
        script.index("if ((action.action_key === 'action.submit' || isH5SubmitAction(action)) && action.planned_from_node_id === 'NODE-insure-form')"):
        script.index("if (action.action_key === 'action.buy_now' || isAgent3BuyNowStrategy(action))")
    ]

    assert "function isH5SubmitAction(action)" in script
    assert "async function clickH5SubmitButton(page, action)" in script
    assert "touchscreen-submit-btn" in script
    assert "page.touchscreen.tap" in script
    assert "await clickH5SubmitButton(page, action)" in submit_block
    assert submit_block.index("await repairVisibleBankPickerLikeAgent3(page, action, 'submit-preflight');") < submit_block.index(
        "await clickH5SubmitButton(page, action)"
    )


def test_visible_runner_force_syncs_h5_input_values_before_submit():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    submit_block = script[
        script.index("if ((action.action_key === 'action.submit' || isH5SubmitAction(action)) && action.planned_from_node_id === 'NODE-insure-form')"):
        script.index("if (action.action_key === 'action.buy_now' || isAgent3BuyNowStrategy(action))")
    ]

    assert "async function forceSetLocatorValue(locator, value)" in script
    assert "async function syncVisibleH5InsureInputs(page)" in script
    assert "Object.getOwnPropertyDescriptor(element.constructor.prototype, 'value')" in script
    assert "await syncVisibleH5InsureInputs(page);" in submit_block
    assert submit_block.index("await syncVisibleH5InsureInputs(page);") < submit_block.index(
        "const agreementCheckedCount = await forceConfirmAgreementCheckboxes(page);"
    )


def test_visible_runner_syncs_h5_insure_model_state_like_agent3_before_submit():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    submit_block = script[
        script.index("if ((action.action_key === 'action.submit' || isH5SubmitAction(action)) && action.planned_from_node_id === 'NODE-insure-form')"):
        script.index("if (action.action_key === 'action.buy_now' || isAgent3BuyNowStrategy(action))")
    ]

    assert "async function syncVisibleH5InsureModelState(page)" in script
    assert "const startValue = String(mockData['applicant.card_valid_start'] || '2021-05-19');" in script
    assert "const endValue = String(mockData['applicant.card_valid_end'] || '2041-05-19');" in script
    assert "const regionText = String(mockData['applicant.region_value'] || mockData['applicant.region_code'] || mockData.provCityText_10 || mockData['insured.region_value'] || mockData['insured.region_code'] || mockData.provCityText_20 || '110000-110105');" in script
    assert "'110000-110100-79082'" not in script
    assert "const jobText = String(mockData['insured.occupation_value'] || mockData['applicant.occupation_value'] || mockData['insured.occupation_code'] || mockData['applicant.occupation_code'] || mockData.jobText_20 || mockData.jobText_10 || '6546010-6546043-6546243-1');" in script
    assert "'6532506-6532571-6533535-1'" not in script
    assert "const bankPair = String(mockData.bankAccountPair_107 || '').split('|');" in script
    assert "const bankValue = bankPair[1] || String(mockData.bankValue_107 || mockData.bankControlValue_107 || '1');" in script
    assert "const forWhoValue = String(mockData.forWho_20 || mockData['insured.forWho'] || '100');" in script
    assert "const isSelfInsured = /^(100|本人)$/.test(forWhoValue);" in script
    assert "fillByLabel(/姓名|被保人.*姓名|被保险人.*姓名/, insuredName, 1, '被保人姓名');" in script
    assert "fillByLabel(/证件号码|身份证号/, insuredIdNo, 1, '被保人证件号码');" in script
    assert "element.dataset.agent4PayAccountLocked = '1';" in script
    assert "window.__agent4PayAccountLocked = true;" in script
    assert "window.__agent4PayAccountValue = text;" in script
    assert "const patchPlain = obj =>" in script
    assert "const patchTopLevel = obj =>" in script
    assert "const patchImmutableStore = store =>" in script
    assert "container.startDate = policyStartDate;" in script
    assert "container.isHealthSuccess = true;" in script
    assert "records.push('plain.topLevel');" in script
    assert "records.push('plain.module.10');" in script
    assert "records.push('plain.module.20');" in script
    assert "records.push('plain.module.30');" in script
    assert "records.push('plain.module.101');" in script
    assert "if (!beneficiaryRows || (Array.isArray(beneficiaryRows) && !beneficiaryRows.length))" in script
    assert "if (!emergencyRows || (Array.isArray(emergencyRows) && !emergencyRows.length))" in script
    assert "records.push('plain.module.107');" in script
    assert "patchPlain(window.__NEXT_DATA__);" in script
    assert "window.__agent4InsureStatePatch = { startValue, endValue, regionText, jobText, bankValue, bankName, payAccount, records };" in script
    assert "fillByLabel(/持卡人/, cardOwner, 0, '持卡人');" in script
    assert "fillByLabel(/银行账号|银行卡号|卡号/, payAccount, 0, '银行账号');" in script
    assert "next = next.setIn([...base, 'cardPeriod', 'value'], `${startValue}|${endValue}`);" in script
    assert "next = next.setIn([...base, 'provCityText', 'value'], regionText);" in script
    assert "next = next.setIn([...base, 'jobText', 'value'], jobText);" in script
    assert "next = next.setIn([...base, 'relationInsureBeneficiary'], 1);" in script
    assert "next = next.setIn([...base, 'insurantIndex'], 0);" in script
    assert "next = next.setIn([...base, 'startDate'], policyStartDate);" in script
    assert "next = next.setIn([...base, 'isHealthSuccess'], true);" in script
    assert "next = next.setIn([...base, 'bank', 'value'], bankValue);" in script
    assert "next = next.setIn([...base, 'payAccount', 'value'], payAccount);" in script
    assert "next = next.setIn([...base, 'payAccount', 'hasAjaxError'], false);" in script
    assert "store.getState = () => next;" in script
    assert "await syncVisibleH5InsureModelState(page);" in submit_block
    assert submit_block.index("await syncVisibleH5InsureInputs(page);") < submit_block.index(
        "await syncVisibleH5InsureModelState(page);"
    )
    assert "await repairInsureFormBeforeSubmit(page);" not in submit_block
    assert "const agreementCheckedCount = await forceConfirmAgreementCheckboxes(page);" in submit_block
    assert "async function clearVisibleBankAccountError(page)" in script
    assert "row.classList.remove('am-input-error')" in script
    assert "await clearVisibleBankAccountError(page);" in submit_block
    assert submit_block.index("await clearVisibleBankAccountError(page);") < submit_block.index(
        "const formState = await inspectInsureFormState(page);"
    )


def test_visible_runner_keeps_self_insured_details_collapsed_like_successful_agent3_runs():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    submit_block = script[
        script.index("if ((action.action_key === 'action.submit' || isH5SubmitAction(action)) && action.planned_from_node_id === 'NODE-insure-form')"):
        script.index("if (action.action_key === 'action.buy_now' || isAgent3BuyNowStrategy(action))")
    ]

    assert "function collapseSelfInsuredDetailRows()" in script
    assert "function markSelfInsuredDetailFieldsOptional(record)" in script
    assert "if (!isSelfInsured) fillByLabel(/姓名|被保人.*姓名|被保险人.*姓名/, insuredName, 1, '被保人姓名');" in script
    assert "if (!isSelfInsured) fillByLabel(/证件号码|身份证号/, insuredIdNo, 1, '被保人证件号码');" in script
    assert "markSelfInsuredDetailFieldsOptional(insured);" in script
    assert "await collapseSelfInsuredDetails(page);" in submit_block


def test_visible_runner_selects_bank_picker_before_submit():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    submit_block = script[
        script.index("if ((action.action_key === 'action.submit' || isH5SubmitAction(action)) && action.planned_from_node_id === 'NODE-insure-form')"):
        script.index("if (action.action_key === 'action.buy_now' || isAgent3BuyNowStrategy(action))")
    ]

    assert "async function selectVisibleBankLikeAgent3(page)" in script
    assert "const bankPair = String(mock.bankAccountPair_107 || '').split('|');" in script
    assert "const bankValue = bankPair[1] || String(mock.bankValue_107 || mock.bankControlValue_107 || '');" in script
    assert "const bankValue = bankPair[1] || String(mockData.bankValue_107 || mockData.bankControlValue_107 || '1');" in script
    assert "const payAccount = String(bankPair[2] || mockData.payAccount_107 || '').replace(/\\s+/g, '');" in script
    assert "const fieldRows = label =>" in script
    assert "const openRow = async row =>" in script
    assert "const setPickerCol = async (colIndex, preferredTexts) =>" in script
    assert "const setSingleDirect = async (row, label, preferredTexts) =>" in script
    assert "props.onChange([String(chosen.value)]);" in script
    assert "type: 'bank-picker-agent3-select'" in script
    assert "async function repairVisibleBankSelectionAfterAccountInputLikeAgent3(page)" in script
    assert "async function clearVisibleBankRecognitionFeedbackLikeAgent3(page)" in script
    assert "type: 'bank-recognition-state-repair'" in script
    assert "type: 'bank-recognition-feedback-clear'" in script
    assert "function isBankPickerField(field)" in script
    assert "function isBankAccountField(field)" in script
    assert "payload.mock_data.payAccount_107" in script
    assert "if (isBankAccountField(field)) return false;" in script
    assert "if (/账户名须为投保人本人|持卡人/.test(probe) && (payload.mock_data.cardOwner_107 || payload.mock_data['applicant.name']))" in script
    assert "if (/真实姓名|姓名/.test(probe) && payload.mock_data['applicant.name']) return payload.mock_data['applicant.name'];" in script
    assert "if (/证件号码|身份证号/.test(probe) && payload.mock_data['applicant.id_no']) return payload.mock_data['applicant.id_no'];" in script
    assert "if (isBankPickerField(field)) continue;" in script
    assert "await waitForTransientToastGone(page);" in script
    assert "setRowExtraText(/^开户银行|^开户行$|^银行$/, bankName, '开户银行');" not in script
    assert "await repairVisibleBankPickerLikeAgent3(page, action, 'submit-preflight');" in submit_block
    assert "bankPickerManualRepairDone = true;" not in script
    assert submit_block.index("await repairVisibleBankPickerLikeAgent3(page, action, 'submit-preflight');") < submit_block.index(
        "const agreementCheckedCount = await forceConfirmAgreementCheckboxes(page);"
    )
    bank_flow_block = script[
        script.index("async function repairVisibleBankPickerLikeAgent3(page, action, reason, options = {})"):
        script.index("async function clickTaskModalGoCompleteIfPresent(page)")
    ]
    assert bank_flow_block.index("await selectVisibleBankLikeAgent3(page);") < bank_flow_block.index(
        "await fillVisibleBankAccountThenBlur(page);"
    )
    assert bank_flow_block.index("await fillVisibleBankAccountThenBlur(page);") < bank_flow_block.index(
        "await repairVisibleBankSelectionAfterAccountInputLikeAgent3(page);"
    )


def test_visible_runner_prefers_bottom_fixed_submit_button():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()

    assert "async function bestH5SubmitButtonLocator(page)" in script
    assert "position === 'fixed' || position === 'sticky'" in script
    assert "score: fixedScore + bottomScore + rightScore + exactScore" in script
    assert "const scoredSubmit = await bestH5SubmitButtonLocator(page);" in script


def test_visible_runner_treats_agent3_h5_submit_click_as_submit_action():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    submit_block = script[
        script.index("if ((action.action_key === 'action.submit' || isH5SubmitAction(action)) && action.planned_from_node_id === 'NODE-insure-form')"):
        script.index("if (action.action_key === 'action.buy_now' || isAgent3BuyNowStrategy(action))")
    ]

    assert "function isH5SubmitAction(action)" in script
    assert "text.includes('提交订单')" in script
    assert "strategy.includes('js-h5-action-button')" in script
    assert "await dismissBlockingOverlays(page);" in submit_block
    assert "h5-submit-overlay-retry-click" in submit_block


def test_visible_runner_dismisses_bank_recognition_toast_as_blocking_overlay():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    overlay_function = script[
        script.index("async function dismissBlockingOverlays(page)"):
        script.index("function isLikelyWholePageTransientError")
    ]

    assert "开户行识别失败|手动选择开户行" in overlay_function
    assert "bank-recognition-toast" in overlay_function


def test_visible_runner_treats_js_minimal_data_as_synthetic_action():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    loop_block = script[
        script.index("const actions = payload.real_actions || []"):
        script.index("if (action.action_key === 'action.submit'")
    ]

    assert "function isSyntheticMinimalDataAction(action)" in script
    assert "function isAgent3SuitabilityAnswerAction(action)" in script
    assert "function isBankRelatedSyntheticDataAction(action)" in script
    assert "selector === 'policy-tool-bank-mock'" in script
    assert "isSyntheticMinimalDataAction(action)" in loop_block
    assert "isAgent3SuitabilityAnswerAction(action) && isSuitabilityQuestionnairePageUrl(page.url())" in loop_block
    assert "applyAgent3SuitabilityAnswer(page, action)" in loop_block
    assert "click_strategy: 'agent3-suitability-answer'" in loop_block
    assert "repairVisibleBankPickerLikeAgent3(page, action, 'synthetic-bank-action', { skip_account_refill: recognitionBlocker })" in loop_block
    assert "let bankAgent3Flow = null;" in loop_block
    assert "bank_agent3_flow: bankAgent3Flow" in loop_block
    assert "stop synthetic action loop" not in script
    assert "bank-synthetic-skip-after-manual-select" not in script
    assert "const syntheticClickStrategy = taskModalResult.clicked ? 'js-minimal-data+task-modal-go-complete' : 'js-minimal-data';" in loop_block
    assert "click_strategy: syntheticClickStrategy" in loop_block
    assert "synthetic: true" in loop_block


def test_visible_runner_coalesces_h5_form_synthetic_data_before_submit():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    loop_block = script[
        script.index("const actions = payload.real_actions || []"):
        script.index("if (action.action_key === 'action.submit'")
    ]

    assert "function shouldCoalesceH5FormSyntheticAction(action, currentUrl)" in script
    assert "shouldCoalesceH5FormSyntheticAction(action, page.url())" in loop_block
    assert "click_strategy: 'coalesced-h5-form-synthetic-data'" in loop_block
    assert "coalesced: true" in loop_block
    assert loop_block.index("await syncVisibleH5InsureModelState(page);") < loop_block.index(
        "shouldCoalesceH5FormSyntheticAction(action, page.url())"
    )
    assert loop_block.index("shouldCoalesceH5FormSyntheticAction(action, page.url())") < loop_block.index(
        "const syntheticDiagnostics = await inspectPostSubmitDiagnostics(page);"
    )


def test_visible_runner_records_terminal_boundary_action_before_replay_continues():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    loop_block = script[
        script.index("const actions = payload.real_actions || []"):
        script.index("if (isStaleSuitabilityAdvanceOnH5InsureForm")
    ]
    final_block = script[
        script.index("const targetNode = (payload.completion_rule || {}).target_node || payload.target_node;"):
        script.index("result.duration_s = (Date.now() - startedAt) / 1000;")
    ]

    assert "function isTerminalBoundaryAction(action)" in script
    assert "'account-session-boundary'" in script
    assert "if (isTerminalBoundaryAction(action))" in loop_block
    assert "click_strategy: actionClickStrategy(action)" in loop_block
    assert "terminal_boundary: true" in loop_block
    assert "result.execution_boundary" in final_block
    assert "result.target_node_status = 'blocked';" in final_block


def test_visible_runner_clicks_task_modal_from_synthetic_data_flow():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    loop_block = script[
        script.index("const actions = payload.real_actions || []"):
        script.index("if (action.action_key === 'action.submit'")
    ]

    assert "await clickTaskModalGoCompleteIfPresent(page);" in loop_block
    assert "task_modal_result: taskModalResult" in loop_block
    assert "js-minimal-data+task-modal-go-complete" in loop_block
    assert "findTaskModalResumeIndex(actions, index, taskModalMatchedNodes)" in loop_block
    assert "index = resumeIndex - 1;" in loop_block
    assert loop_block.index("await clickTaskModalGoCompleteIfPresent(page);") < loop_block.index(
        "const matchedNodes = taskModalMatchedNodes || await matchNodes(page, payload);"
    )


def test_visible_runner_reselects_bank_without_refilling_account_on_recognition_failure():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    bank_flow_block = script[
        script.index("async function repairVisibleBankPickerLikeAgent3(page, action, reason, options = {})"):
        script.index("function findTaskModalResumeIndex")
    ]
    loop_block = script[
        script.index("const actions = payload.real_actions || []"):
        script.index("if (action.action_key === 'action.submit'")
    ]

    assert "const skipAccountRefill = Boolean(options.skip_account_refill)" in bank_flow_block
    assert "const bankAccountRefill = skipAccountRefill" in bank_flow_block
    assert "await fillVisibleBankAccountThenBlur(page)" in bank_flow_block
    assert bank_flow_block.index("await selectVisibleBankLikeAgent3(page);") < bank_flow_block.index(
        "const bankAccountRefill = skipAccountRefill"
    )
    assert "bank_account_refill: bankAccountRefill" in bank_flow_block
    assert "function isBankReadyToSubmitAfterManualSelection(diagnostics)" in script
    assert "let bankRecognitionManualSelectionPendingSubmit = false;" in script
    assert "const recognitionBlocker = hasBankRecognitionBlocker(syntheticDiagnostics) || bankRecognitionManualSelectionPendingSubmit;" in loop_block
    assert "skip_account_refill: recognitionBlocker" in loop_block
    assert "if (recognitionBlocker && isBankReadyToSubmitAfterManualSelection(bankAgent3Flow?.diagnostics))" in loop_block
    assert "await clickH5SubmitButton(page, action);" in loop_block
    assert "type: 'bank-recognition-submit-click'" in loop_block
    assert "bankRecognitionManualSelectionPendingSubmit = false;" in loop_block
    assert "if (!text && /am-input-error-extra/.test(className) && !isWarmColor) continue;" in script


def test_visible_runner_replays_agent3_insure_form_url_before_synthetic_data():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    start = script.index("if (isSyntheticMinimalDataAction(action))")
    loop_block = script[
        start:
        script.index("await syncVisibleH5InsureInputs(page);", start)
    ]

    assert "replayAgent3InsureFormUrlIfNeeded(page, action)" in script
    assert "await replayAgent3InsureFormUrlIfNeeded(page, action);" in loop_block
    assert "if (action.planned_to_node_id === 'NODE-insure-form' && !isSuitabilityQuestionnairePageUrl(page.url()))" in loop_block
    assert loop_block.index("await replayAgent3InsureFormUrlIfNeeded(page, action);") < loop_block.index(
        "await waitForMockDataNodeReady(page, action.planned_to_node_id, 60000);"
    )


def test_visible_runner_treats_buy_now_navigation_during_click_as_success():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()

    assert "tap_error: String(error?.message || error)" in script
    assert "if (await buyNowAdvanced(page, beforeUrl))" in script
    assert "already advanced before buy-now click" in script


def test_visible_runner_does_not_reload_live_product_detail_for_embedded_request_failure():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    transient_block = script[
        script.index("async function recoverTransientPageError(page)"):
        script.index("const FIELD_ALIAS_STOP_WORDS")
    ]

    assert "function isLikelyWholePageTransientError(bodyText, url)" in script
    assert "if (!isLikelyWholePageTransientError(bodyText, page.url())) return recovered;" in transient_block
    assert "/Request failed with status code/i.test(text) && /product\\/detail/i.test(currentUrl)" in script


def test_visible_runner_replays_next_agent3_entry_url_when_buy_now_does_not_advance():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    buy_now_function = script[
        script.index("async function clickBuyNowAction"):
        script.index("async function clickAgreeAllAction")
    ]
    loop_block = script[
        script.index("if (action.action_key === 'action.buy_now' || isAgent3BuyNowStrategy(action))"):
        script.index("const targetNode = (payload.completion_rule || {}).target_node || payload.target_node;")
    ]

    assert "function nextAgent3EntryUrlAfterBuyNow(actions, currentIndex)" in script
    assert "const nextEntryUrl = nextAgent3EntryUrlAfterBuyNow(actions, index);" in loop_block
    assert "clickResult = await clickBuyNowAction(page, action, before, nextEntryUrl);" in loop_block
    assert "if (nextEntryUrl && /healthInform|product\\/insure/i.test(nextEntryUrl))" in buy_now_function
    assert "strategy: 'agent3-next-entry-url-replay'" in buy_now_function


def test_visible_runner_replays_next_agent3_insure_url_from_broken_health_notice_page():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    health_function = script[
        script.index("async function answerHealthNotice"):
        script.index("async function autoAnswerQuestionnaireIfPresent")
    ]
    loop_block = script[
        script.index("if (action.action_key === 'action.answer_health_notice' || isAgent3HealthNoticeStrategy(action))"):
        script.index("if (isAutoWaitAction(action))")
    ]

    assert "function nextAgent3InsureFormUrlAfterHealthNotice(actions, currentIndex)" in script
    assert "const nextEntryUrl = nextAgent3InsureFormUrlAfterHealthNotice(actions, index);" in loop_block
    assert "const answerResult = await answerHealthNotice(page, action, nextEntryUrl);" in loop_block
    assert "isLikelyWholePageTransientError(bodyText, page.url())" in health_function
    assert "await page.goto(nextEntryUrl, { waitUntil: 'domcontentloaded', timeout: 60000 })" in health_function
    assert "strategy: 'health-notice-agent3-next-entry-url-replay'" in health_function


def test_visible_runner_recovers_bank_account_submit_blocker_and_task_modal():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    submit_block = script[
        script.index("if (action.action_key === 'action.submit' || isH5SubmitAction(action))"):
        script.index("if (action.action_key === 'action.buy_now' || isAgent3BuyNowStrategy(action))")
    ]

    assert "function hasBankAccountSubmitBlocker(diagnostics)" in script
    assert "String(item?.text || '').trim()" in script
    assert "function hasBankRecognitionBlocker(diagnostics)" in script
    assert "async function repairVisibleBankPickerLikeAgent3(page, action, reason, options = {})" in script
    assert "async function recoverH5SubmitBlockerAndRetry(page, action, diagnostics)" in script
    assert "async function clickTaskModalGoCompleteIfPresent(page)" in script
    assert "await recoverH5SubmitBlockerAndRetry(page, action, postSubmitDiagnostics)" in submit_block
    assert "const manualBankRepair = await repairVisibleBankPickerLikeAgent3(page, action, 'submit-blocker', { skip_account_refill: recognitionBlocker });" in script
    assert "await clearVisibleBankRecognitionFeedbackLikeAgent3(page);" in script
    assert "await repairVisibleBankSelectionAfterAccountInputLikeAgent3(page);" in script
    assert "stop before retrying submit" not in script
    assert "manual_bank_repair: manualBankRepair" in script
    assert "type: 'bank-agent3-flow-start'" in script
    assert "type: 'bank-agent3-flow-end'" in script
    assert "await clickTaskModalGoCompleteIfPresent(page);" in submit_block
    assert "type: 'h5-submit-retry-click'" in script
    assert "type: 'task-modal-go-complete'" in script


def test_visible_runner_routes_submit_suitability_blocker_before_progress_assertion():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    submit_block = script[
        script.index("if (action.action_key === 'action.submit' || isH5SubmitAction(action))"):
        script.index("if (action.action_key === 'action.buy_now' || isAgent3BuyNowStrategy(action))")
    ]

    assert "globalThis.__agent4NetworkResponses" in script
    assert "function recentSuitabilitySubmitBlocker()" in script
    assert "async function waitForSuitabilitySubmitBlocker(page, timeoutMs = 12000)" in script
    assert "async function recoverSuitabilityTaskAfterSubmitIfNeeded(page, action, actions, currentIndex, diagnostics" in script
    assert "await waitForSuitabilitySubmitBlocker(page);" in submit_block
    assert "const submitSuitabilityRecovery = await recoverSuitabilityTaskAfterSubmitIfNeeded" in submit_block
    assert submit_block.index("await waitForSuitabilitySubmitBlocker(page);") < submit_block.index(
        "const submitSuitabilityRecovery = await recoverSuitabilityTaskAfterSubmitIfNeeded"
    )
    assert submit_block.index("const submitSuitabilityRecovery = await recoverSuitabilityTaskAfterSubmitIfNeeded") < submit_block.index(
        "assertAgent4ActionProgress(action, before, page.url(), matchedNodes, clickResult.strategy);"
    )
    assert "'submit-suitability-task-recovery'" in submit_block
    assert "click_strategy: clickStrategy" in submit_block


def test_visible_runner_submits_browser_api_before_suitability_recovery():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    submit_block = script[
        script.index("if (action.action_key === 'action.submit' || isH5SubmitAction(action))"):
        script.index("if (action.action_key === 'action.buy_now' || isAgent3BuyNowStrategy(action))")
    ]

    assert "async function submitInsureViaBrowserApiIfNeeded(page, action, beforeUrl, diagnostics, options = {})" in script
    assert "const preSuitabilitySubmitApiResult = await submitInsureViaBrowserApiIfNeeded" in submit_block
    assert "{ allow_suitability_blocker: true }" in submit_block
    assert submit_block.index("const preSuitabilitySubmitApiResult = await submitInsureViaBrowserApiIfNeeded") < submit_block.index(
        "const submitSuitabilityRecovery = await recoverSuitabilityTaskAfterSubmitIfNeeded"
    )
    recovery_call = submit_block[
        submit_block.index("const submitSuitabilityRecovery = await recoverSuitabilityTaskAfterSubmitIfNeeded"):
        submit_block.index(");", submit_block.index("const submitSuitabilityRecovery = await recoverSuitabilityTaskAfterSubmitIfNeeded")) + 2
    ]
    assert "preSuitabilitySubmitApiResult" in recovery_call
    assert "function suitabilityTaskUrlsFromSubmit(page, action, submitApiResult = null)" in script
    assert "submitApiResult?.response_order?.encryptInsureNum" in script


def test_visible_runner_uses_browser_api_submit_after_ui_submit_noop():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    submit_block = script[
        script.index("if (action.action_key === 'action.submit' || isH5SubmitAction(action))"):
        script.index("if (action.action_key === 'action.buy_now' || isAgent3BuyNowStrategy(action))")
    ]

    assert "async function submitInsureViaBrowserApiIfNeeded(page, action, beforeUrl, diagnostics, options = {})" in script
    assert "let submitApiResult = preSuitabilitySubmitApiResult;" in submit_block
    assert "submitApiResult = await submitInsureViaBrowserApiIfNeeded(page, action, before, postSubmitDiagnostics);" in submit_block
    assert "post-progress-check" in submit_block
    assert "ui-click+browser-api-submit" in submit_block
    assert "submit_api_result: submitApiResult" in submit_block
    assert "action.auth_handoff_result?.completed" in script
    assert "submitApiResult?.direct_order || authHandoffResult?.completed" in submit_block
    assert "const looksLikeModuleRoot = value =>" in script
    assert "if (/^\\d+$/.test(key)) continue;" in script
    assert "payload.trialGenes = normalizeSubmitTrialGenes(payload.trialGenes, payload);" in script
    assert "function normalizeSubmitTrialGenes(rawTrialGenes, payload)" in script
    assert "setTrialGene('insurantDate', insuredRow.birthdate || applicantRow.birthdate);" in script
    assert "setTrialGene('sex', sexValue === '2' ? '女' : '男');" in script
    assert "applicantRegion: payload.data?.['10']?.[0]?.provCityText" in script
    assert "insuredRegion: payload.data?.['20']?.[0]?.provCityText" in script
    assert "applicantJob: payload.data?.['10']?.[0]?.jobText" in script
    assert "trialGenes: summarizeTrialGenes(payload.trialGenes)" in script
    assert "payload.isAp = payload.isAp ?? false;" in script
    assert "payload.isPay = payload.isPay ?? false;" in script


def test_visible_runner_does_not_treat_task_handoff_submit_as_order_boundary_without_downstream_evidence():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    boundary_block = script[
        script.index("function orderGenerationBoundaryReached"):
        script.index("function setupNetworkLogging")
    ]

    assert "async function completePostSubmitIdentityHandoff(page, submitApiResult)" in script
    assert "post-submit-auth-handoff" in script
    assert "completePostSubmitIdentityHandoff(page, submitApiResult)" in script
    assert "action.submit_api_result?.order_generated" not in boundary_block
    assert "action.auth_handoff_result?.completed" in boundary_block


def test_visible_runner_counts_recovered_suitability_submit_api_as_order_boundary():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    boundary_block = script[
        script.index("function orderGenerationBoundaryReached"):
        script.index("function setupNetworkLogging")
    ]

    assert "action.submit_suitability_recovery?.recovered" in boundary_block
    assert "action.submit_api_result?.suitability_task" in boundary_block


def test_visible_runner_probes_current_suitability_task_after_silent_submit():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()

    assert "function shouldProbeSuitabilityTaskAfterSubmit(action, currentUrl, diagnostics)" in script
    assert "const rawUrls = [page.url(), action?.target_url];" in script
    assert "for (const raw of rawUrls)" in script
    assert "shouldProbeSuitabilityTaskAfterSubmit(action, page.url(), diagnostics)" in script


def test_visible_runner_does_not_probe_suitability_without_submit_evidence():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    probe_block = script[
        script.index("function shouldProbeSuitabilityTaskAfterSubmit"):
        script.index("function suitabilityTaskUrlsFromSubmit")
    ]

    assert "const explicitSuitabilityTarget = /\\/product\\/adapt(?:\\/|$)/i.test(expectedPath)" in probe_block
    assert "const hasExplicitSuitabilityEvidence = diagnosticsNeedSuitabilityTask(diagnostics) || Boolean(recentSuitabilitySubmitBlocker());" in probe_block
    assert "if (!explicitSuitabilityTarget && !hasExplicitSuitabilityEvidence) return false;" in probe_block
    assert "expectedNodeId === 'NODE-underwriting'" not in probe_block


def test_visible_runner_logs_h5_submit_network_and_diagnostics():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    logging_start = script.index("function setupNetworkLogging(page)")
    logging_block = script[
        logging_start:
        script.index("(async () => {", logging_start)
    ]

    assert "page.on('request', request => {" in script
    assert "type: 'network-request'" in script
    assert "type: 'network-response'" in script
    assert "/api/apps/cps/insure/submit" in script
    assert "submitInsureViaBrowserApiIfNeeded(page, action, before, postSubmitDiagnostics)" in script
    assert "completePostSubmitIdentityHandoff(page, submitApiResult)" in script
    assert "authHandoffResult?.completed" in script
    assert "product\\/insure|insure|pay\\/bank" in script
    assert "product\\/insure|insure\\/submit|pay\\/bank|card\\/valid" in script
    assert "risknotify" in logging_block
    assert "risknotify|product\\/insure|insure\\/submit|pay\\/bank|card\\/valid" in script
    assert "type: 'h5-submit-click'" in script
    assert "async function inspectPostSubmitDiagnostics(page)" in script
    assert "type: 'post-submit-diagnostics'" in script


def test_visible_runner_direct_submit_aligns_bank_module_with_native_submit_shape():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    submit_api = script[
        script.index("async function submitInsureViaBrowserApiIfNeeded"):
        script.index("async function completePostSubmitIdentityHandoff")
    ]

    assert "payload.autoRenewal = payload.autoRenewal === true" in submit_api
    assert "payload.renewalCheck = 0;" in submit_api
    assert "delete payload.data['107'];" not in submit_api
    assert "delete payload.data[107];" not in submit_api
    assert "normalizeBankNameForSubmit" in submit_api
    assert "bankRow.bankName = bankName;" in submit_api
    assert "insuredRow.addressIsSameApplicant = insuredRow.addressIsSameApplicant ?? '';" in submit_api
    assert "payload.price = submitPriceFromTrialGenes(payload.trialGenes) || payload.price;" in submit_api
    assert "payload.isEmptyData = !hasSubmitData;" not in submit_api
    assert submit_api.index("alignNativeSubmitPayload(payload);") < submit_api.index(
        "let submitResult = await submitPayload(payload);"
    )
    assert "const response = await fetch(`/api/apps/cps/insure/submit" in submit_api
    assert "const retryDate = extractAllowedStartDate(text);" in submit_api
    assert "setPayloadStartDate(payload, retryDate);" in submit_api
    assert "retry_reason: 'policy-start-date-window'" in submit_api


def test_visible_runner_replays_agent3_submit_api_actions_without_css_locator():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    loop_body = script[
        script.index("for (let index = 0; index < actions.length; index += 1)"):
        script.index("let locator = await locatorForAction(page, action);")
    ]

    assert "function isAgent3SubmitApiAction(action)" in script
    assert "if (isAgent3SubmitApiAction(action))" in loop_body
    assert "selector: action.selector" in loop_body
    assert "click_strategy: 'agent3-submit-api-replay'" in loop_body
    assert script.index("if (isAgent3SubmitApiAction(action))") < script.index(
        "let locator = await locatorForAction(page, action);"
    )


def test_visible_runner_closes_attachment_document_tabs():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    agreement_block = script[
        script.index("async function readAgreementLineDocuments"):
        script.index("async function clickAgreementLineControl")
    ]

    assert "function isAttachmentDocumentPage(url, title = '')" in script
    assert "async function closeAttachmentPages(context, mainPage, reason)" in script
    assert r"(?:files?|docs?|documents?)\d*[.-]" in script
    assert "context.on('page', popup =>" in script
    assert "await closeAttachmentPages(page.context(), page, 'agreement-line-document')" in agreement_block


def test_visible_runner_restores_main_page_when_attachment_replaces_current_page():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    agreement_block = script[
        script.index("async function readAgreementLineDocuments"):
        script.index("async function clickAgreementLineControl")
    ]

    assert "async function restoreMainPageFromAttachment(page, fallbackUrl, reason)" in script
    assert "await page.goBack({ waitUntil: 'domcontentloaded'" in script
    assert "const beforeUrl = page.url();" in agreement_block
    assert "await restoreMainPageFromAttachment(page, beforeUrl, 'agreement-line-document')" in agreement_block


def test_visible_runner_logs_insure_model_summary_before_submit():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    form_state_block = script[
        script.index("async function inspectInsureFormState"):
        script.index("async function answerQuestionnaire")
    ]

    assert "function summarizeInsureModelData(raw)" in form_state_block
    assert "model_data_summary: summarizeInsureModelData(rawState)" in form_state_block
    assert "storage_model_summaries" in form_state_block
    assert "'relationInsureBeneficiary', 'insurantIndex'," in form_state_block


def test_visible_runner_buy_now_text_fallback_accepts_spaced_insure_button():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    buy_now_block = script[
        script.index("async function clickBuyNowAction"):
        script.index("async function clickAgreeAllAction")
    ]

    assert "new RegExp(buyTextRegexSource)" in buy_now_block
    assert "buyTextRegex.test(item.text)" in buy_now_block
    assert "exactBuyButtonText" in buy_now_block
    assert "投保须知|投保人|被保险人|告知书|声明|流程|说明" in buy_now_block


def test_agent4_attaches_agent3_action_trace_by_path():
    from e2e_agent.agents import exec_agent

    scenarios = [
        {
            "scenario_id": "SCN-001",
            "path_id": "PATH-001",
            "real_actions": [{"action_key": "action.buy_now", "selector": ".guessed"}],
        }
    ]
    state = {
        "explore_trace": {
            "action_trace": [
                {
                    "path_id": "PATH-001",
                    "action_key": "action.buy_now",
                    "selector": "#submit-by",
                    "text": "投 保",
                    "source_url": "https://example.com/detail",
                    "target_url": "https://example.com/insure",
                }
            ]
        }
    }

    enriched = exec_agent._attach_agent3_replay_actions(scenarios, state)

    assert enriched[0]["agent3_replay_source"] == "explore_trace.action_trace"
    assert enriched[0]["agent3_replay_actions"] == [
        {
            "path_id": "PATH-001",
            "action_key": "action.buy_now",
            "selector": "#submit-by",
            "text": "投 保",
            "source_url": "https://example.com/detail",
            "target_url": "https://example.com/insure",
        }
    ]
    assert enriched[0]["real_actions"][0]["selector"] == ".guessed"


def test_agent4_marks_path_replay_required_when_agent3_path_has_no_actions():
    from e2e_agent.agents import exec_agent

    enriched = exec_agent._attach_agent3_replay_actions(
        [{"scenario_id": "SCN-001", "path_id": "PATH-001"}],
        {"page_registry": {"path_exploration_results": [{"path_id": "PATH-001", "action_chain": []}]}},
    )

    assert enriched[0]["agent3_replay_required"] is True
    assert "agent3_replay_actions" not in enriched[0]


def test_agent4_filters_agent3_diagnostic_trace_items_from_replay():
    from e2e_agent.agents import exec_agent

    enriched = exec_agent._attach_agent3_replay_actions(
        [{"scenario_id": "SCN-001", "path_id": "PATH-001"}],
        {
            "explore_trace": {
                "action_trace": [
                    {
                        "path_id": "PATH-001",
                        "tag": "diagnostic",
                        "text": "agreement_scan_miss:body_len=1916",
                        "click_strategy": "agreement-scan-miss",
                        "action_type": "agreement_diagnostic",
                    },
                    {
                        "path_id": "PATH-001",
                        "selector": "div:nth-of-type(7)",
                        "text": "投 保",
                        "target_url": "https://example.com/product/insure",
                    },
                ]
            }
        },
    )

    assert len(enriched[0]["agent3_replay_actions"]) == 1
    assert enriched[0]["agent3_replay_actions"][0]["selector"] == "div:nth-of-type(7)"


def test_agent4_prunes_agent3_failed_buy_now_attempt_when_later_attempt_progresses():
    from e2e_agent.agents import exec_agent

    enriched = exec_agent._attach_agent3_replay_actions(
        [{"scenario_id": "SCN-001", "path_id": "PATH-001"}],
        {
            "explore_trace": {
                "action_trace": [
                    {
                        "path_id": "PATH-001",
                        "selector": "div:nth-of-type(7)",
                        "text": "鎶?淇?",
                        "click_strategy": "mouse-h5-product-footer-insure",
                        "source_url": "https://example.com/product/detail",
                        "target_url": "https://example.com/product/detail",
                    },
                    {
                        "path_id": "PATH-001",
                        "selector": "a:nth-of-type(8)",
                        "text": "鎶?淇?",
                        "click_strategy": "mouse-h5-product-footer-insure",
                        "source_url": "https://example.com/product/detail",
                        "target_url": "https://example.com/product/healthInform",
                    },
                ]
            }
        },
    )

    assert [action["selector"] for action in enriched[0]["agent3_replay_actions"]] == [
        "a:nth-of-type(8)"
    ]


def test_agent4_preserves_agent3_premium_quote_before_buy_now_replay():
    from e2e_agent.agents import exec_agent

    enriched = exec_agent._attach_agent3_replay_actions(
        [{"scenario_id": "SCN-001", "path_id": "PATH-001"}],
        {
            "explore_trace": {
                "action_trace": [
                    {
                        "path_id": "PATH-001",
                        "selector": "div:nth-of-type(7)",
                        "text": "保费\n试算",
                        "click_strategy": "mouse-h5-floating-premium-quote+js-fallback",
                        "source_url": "https://example.com/product/detail",
                        "target_url": "https://example.com/product/detail",
                    },
                    {
                        "path_id": "PATH-001",
                        "selector": "a:nth-of-type(8)",
                        "text": "投 保",
                        "click_strategy": "mouse-h5-product-footer-insure",
                        "source_url": "https://example.com/product/detail",
                        "target_url": "https://example.com/product/healthInform",
                    },
                ]
            }
        },
    )

    assert [action["click_strategy"] for action in enriched[0]["agent3_replay_actions"]] == [
        "mouse-h5-floating-premium-quote+js-fallback",
        "mouse-h5-product-footer-insure",
    ]


def test_agent4_infers_agent3_replay_action_key_from_click_strategy():
    from e2e_agent.agents import exec_agent

    buy_now = exec_agent._copy_agent3_replay_action(
        {
            "path_id": "PATH-001",
            "selector": "div:nth-of-type(7)",
            "click_strategy": "mouse-h5-product-footer-insure",
            "target_url": "https://example.com/product/insure",
        }
    )
    health_notice = exec_agent._copy_agent3_replay_action(
        {
            "path_id": "PATH-001",
            "selector": ".safe-option",
            "click_strategy": "js-health-notice-safe-option",
        }
    )
    submit = exec_agent._copy_agent3_replay_action(
        {
            "path_id": "PATH-001",
            "selector": ".submit-btn",
            "click_strategy": "touchscreen-submit-btn",
        }
    )

    assert buy_now is not None
    assert health_notice is not None
    assert submit is not None
    assert buy_now["action_key"] == "action.buy_now"
    assert health_notice["action_key"] == "action.answer_health_notice"
    assert submit["action_key"] == "action.submit"


def test_agent4_expands_agent3_action_trace_artifact_shape_for_replay():
    from e2e_agent.agents import exec_agent

    enriched = exec_agent._attach_agent3_replay_actions(
        [{"scenario_id": "SCN-001", "path_id": "PATH-001"}],
        {
            "explore_trace": {
                "action_trace": [
                    {
                        "path_id": "PATH-001",
                        "path_status": "explored",
                        "action_count": 2,
                        "action_chain": [
                            {
                                "action_key": "action.buy_now",
                                "selector": "#agent3-buy",
                                "text": "立即投保",
                            },
                            {
                                "action_key": "action.submit",
                                "selector": ".submit",
                                "text": "提交订单",
                            },
                        ],
                    }
                ]
            }
        },
    )

    assert enriched[0]["agent3_replay_source"] == "explore_trace.action_trace"
    assert [action["action_key"] for action in enriched[0]["agent3_replay_actions"]] == [
        "action.buy_now",
        "action.submit",
    ]
    assert [action["selector"] for action in enriched[0]["agent3_replay_actions"]] == [
        "#agent3-buy",
        ".submit",
    ]


@pytest.mark.asyncio
async def test_normalise_execution_result_blocks_when_visible_replay_required_but_missing(tmp_path, monkeypatch):
    from e2e_agent.agents import exec_agent

    monkeypatch.setenv("AGENT4_VISIBLE_BROWSER", "1")

    results, healing_inputs, duration = await exec_agent._normalise_execution_result(
        "demo-product",
        {
            "scenario_id": "SCN-001",
            "path_id": "PATH-001",
            "case_ids": ["TC-001"],
            "root_dir": str(tmp_path),
            "entry_url": "https://example.com/detail",
            "completion_rule": {"target_node": "NODE-result", "is_complete": True},
            "agent3_replay_required": True,
        },
        runner=None,  # type: ignore[arg-type]
        warnings=[],
    )

    assert duration == 0.0
    assert healing_inputs == []
    assert results[0]["execution_status"] == "blocked_by_agent3_replay_contract"
    assert results[0]["failure_category"] == "agent3_replay_missing"


@pytest.mark.asyncio
async def test_normalise_execution_result_uses_visible_runner_by_default(tmp_path, monkeypatch):
    from e2e_agent.agents import exec_agent

    monkeypatch.delenv("AGENT4_VISIBLE_BROWSER", raising=False)

    def fake_visible_runner(product_id, scenario):
        assert product_id == "demo-product"
        assert scenario["entry_url"] == "https://example.com/detail"
        return {
            "returncode": 0,
            "passed": 1,
            "failed": 0,
            "errors": [],
            "raw_output": "visible ok",
            "stderr": "",
            "duration_s": 0.2,
            "execution_entry": "agent4.visible-chromium",
            "formal_execution": True,
            "visible_browser": True,
            "artifacts_dir": str(tmp_path / "products" / "demo-product" / "agent4" / "exec" / "visible-runs"),
        }

    class FormalRunner:
        def check_node_available(self) -> bool:
            return True

        def has_project_test_runtime(self) -> bool:
            return True

        def run_formal_spec(self, spec_path_arg: str, report_dir: Path) -> dict[str, object]:
            raise AssertionError("Agent4 should use visible Chromium by default")

    monkeypatch.setattr(exec_agent, "_run_visible_chromium_scenario", fake_visible_runner)

    results, healing_inputs, duration = await exec_agent._normalise_execution_result(
        "demo-product",
        {
            "scenario_id": "SCN-001",
            "path_id": "PATH-001",
            "case_ids": ["TC-001"],
            "root_dir": str(tmp_path),
            "entry_url": "https://example.com/detail",
        },
        runner=FormalRunner(),  # type: ignore[arg-type]
        warnings=[],
        root_dir=tmp_path,
    )

    assert duration == 0.2
    assert healing_inputs == []
    assert results[0]["execution_status"] == "passed"
    assert results[0]["execution_entry"] == "agent4.visible-chromium"
    assert results[0]["formal_execution"] is True
    assert results[0]["visible_browser"] is True


@pytest.mark.asyncio
async def test_normalise_execution_result_runs_agent3_formal_spec_without_visible_script(tmp_path, monkeypatch):
    from e2e_agent.agents import exec_agent

    spec_path = tmp_path / "agent3" / "ts-gen" / "h5" / "scenarios" / "01-path-001.spec.ts"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text("// agent3 formal spec\n", encoding="utf-8")
    monkeypatch.delenv("AGENT4_VISIBLE_BROWSER", raising=False)

    def forbidden_visible_runner(product_id, scenario):
        raise AssertionError("Agent4 must not generate a visible runner when Agent3 formal spec exists")

    class FormalRunner:
        def check_node_available(self) -> bool:
            return True

        def has_project_test_runtime(self) -> bool:
            return True

        def run_formal_spec(self, spec_path_arg: str, report_dir: Path) -> dict[str, object]:
            assert spec_path_arg == str(spec_path)
            return {
                "returncode": 0,
                "passed": 1,
                "failed": 0,
                "errors": [],
                "raw_output": "formal ok",
                "stderr": "",
                "duration_s": 0.3,
                "execution_entry": "agent4.playwright-formal",
                "formal_execution": True,
                "visible_browser": True,
                "report_dir": str(report_dir),
                "reached_target_node": "NODE-result",
                "target_node_status": "reached",
            }

    monkeypatch.setattr(exec_agent, "_run_visible_chromium_scenario", forbidden_visible_runner)

    results, healing_inputs, duration = await exec_agent._normalise_execution_result(
        "demo-product",
        {
            "scenario_id": "SCN-001",
            "path_id": "PATH-001",
            "case_ids": ["TC-001"],
            "spec_path": str(spec_path),
            "root_dir": str(tmp_path),
            "entry_url": "https://example.com/detail",
            "completion_rule": {"target_node": "NODE-result", "is_complete": True},
            "agent3_formal_scenario_spec": True,
            "agent3_formal_scenario_spec_source": "agent3/ts-gen/tc-execution-plan.json",
        },
        runner=FormalRunner(),  # type: ignore[arg-type]
        warnings=[],
        root_dir=tmp_path,
    )

    assert duration == 0.3
    assert healing_inputs == []
    assert results[0]["execution_status"] == "passed"
    assert results[0]["execution_entry"] == "agent4.playwright-formal"
    assert results[0]["agent3_formal_scenario_spec"] is True
    assert results[0]["spec_path"] == str(spec_path)


@pytest.mark.asyncio
async def test_normalise_execution_result_accepts_formal_order_generation_boundary_target(
    tmp_path, monkeypatch
):
    from e2e_agent.agents import exec_agent

    spec_path = tmp_path / "agent3" / "ts-gen" / "h5" / "scenarios" / "01-path-001.spec.ts"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text("// agent3 formal spec\n", encoding="utf-8")
    monkeypatch.delenv("AGENT4_VISIBLE_BROWSER", raising=False)

    def forbidden_visible_runner(product_id, scenario):
        raise AssertionError("Formal order-generation evidence should not fall back to visible runner")

    class FormalRunner:
        def check_node_available(self) -> bool:
            return True

        def has_project_test_runtime(self) -> bool:
            return True

        def run_formal_spec(self, spec_path_arg: str, report_dir: Path) -> dict[str, object]:
            assert spec_path_arg == str(spec_path)
            return {
                "returncode": 0,
                "passed": 1,
                "failed": 0,
                "errors": [],
                "raw_output": "formal ok",
                "stderr": "",
                "duration_s": 0.3,
                "execution_entry": "agent4.playwright-formal",
                "formal_execution": True,
                "visible_browser": True,
                "report_dir": str(report_dir),
            }

    monkeypatch.setattr(exec_agent, "_run_visible_chromium_scenario", forbidden_visible_runner)

    results, healing_inputs, duration = await exec_agent._normalise_execution_result(
        "sample-product",
        {
            "scenario_id": "SCN-001",
            "path_id": "PATH-001",
            "case_ids": ["TC-001"],
            "spec_path": str(spec_path),
            "root_dir": str(tmp_path),
            "entry_url": "https://example.com/detail",
            "completion_rule": {
                "target_node": "NODE-policy-result",
                "required_nodes": ["NODE-insure-form", "NODE-payment", "NODE-policy-result"],
                "order_generation_boundary": True,
                "is_complete": True,
            },
            "agent3_formal_scenario_spec": True,
        },
        runner=FormalRunner(),  # type: ignore[arg-type]
        warnings=[],
        root_dir=tmp_path,
    )

    assert duration == 0.3
    assert healing_inputs == []
    assert results[0]["execution_status"] == "passed"
    assert results[0]["target_node_status"] == "reached"
    assert results[0]["reached_target_node"] == "NODE-policy-result"


@pytest.mark.asyncio
async def test_normalise_execution_result_accepts_visible_suitability_handoff_boundary(
    tmp_path, monkeypatch
):
    from e2e_agent.agents import exec_agent

    monkeypatch.setenv("AGENT4_VISIBLE_BROWSER", "1")

    def visible_runner(product_id, scenario):
        return {
            "returncode": 0,
            "passed": 0,
            "failed": 0,
            "errors": [],
            "duration_s": 0.4,
            "execution_entry": "agent4.visible-chromium",
            "visible_browser": True,
            "final_url": "https://example.test/product/adapt?encryptInsureNum=enc",
            "node_matches": [
                {
                    "step": 1,
                    "matched_nodes": ["NODE-suitability"],
                    "url": "https://example.test/product/adapt?encryptInsureNum=enc",
                }
            ],
            "executed_actions": [
                {
                    "click_strategy": "agent3-submit-api-replay",
                    "planned_to_node_id": "NODE-suitability",
                    "matched_nodes": ["NODE-suitability"],
                    "submit_api_result": {
                        "attempted": True,
                        "order_generated": True,
                        "suitability_task": True,
                        "code": "40015",
                    },
                    "submit_suitability_recovery": {"recovered": True},
                }
            ],
        }

    monkeypatch.setattr(exec_agent, "_run_visible_chromium_scenario", visible_runner)

    results, healing_inputs, duration = await exec_agent._normalise_execution_result(
        "sample-product",
        {
            "scenario_id": "SCN-001",
            "path_id": "PATH-001",
            "case_ids": ["TC-001"],
            "entry_url": "https://example.com/detail",
            "completion_rule": {
                "target_node": "NODE-policy-result",
                "required_nodes": ["NODE-insure-form", "NODE-suitability", "NODE-policy-result"],
                "order_generation_boundary": True,
                "is_complete": True,
            },
        },
        runner=None,
        warnings=[],
        root_dir=tmp_path,
    )

    assert duration == 0.4
    assert healing_inputs == []
    assert results[0]["execution_status"] == "passed"
    assert results[0]["target_node_status"] == "reached"
    assert results[0]["reached_target_node"] == "NODE-policy-result"
    assert results[0]["target_node_inference"] == "agent3.order_generation_boundary"


@pytest.mark.asyncio
async def test_normalise_execution_result_collects_huize_payment_closed_loop_evidence(
    tmp_path, monkeypatch
):
    from e2e_agent.agents import exec_agent

    spec_path = tmp_path / "agent3" / "ts-gen" / "h5" / "scenarios" / "01-path-001.spec.ts"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text("// agent3 formal spec\n", encoding="utf-8")
    monkeypatch.delenv("AGENT4_VISIBLE_BROWSER", raising=False)

    class FormalRunner:
        def check_node_available(self) -> bool:
            return True

        def has_project_test_runtime(self) -> bool:
            return True

        def run_formal_spec(self, spec_path_arg: str, report_dir: Path) -> dict[str, object]:
            external_ops = report_dir / "external-ops"
            external_ops.mkdir(parents=True)
            (external_ops / "SCN-001-PATH-001-huize-issue-status.json").write_text(
                json.dumps(
                    {
                        "operationId": "SCN-001-PATH-001-huize-issue-status",
                        "status": "passed-after-resume",
                        "evidence": {
                            "paymentMethod": "wechat",
                            "gatewayPayNumSource": "runtime-payment-boundary",
                            "hasGatewayPayNum": True,
                            "hasInsureNum": True,
                            "paymentUrlHost": "wx.tenpay.com",
                        },
                        "externalOperations": [
                            {
                                "operationId": "SCN-001-PATH-001-huize-pay-success",
                                "operationType": "huize-pay-success",
                                "status": "passed",
                                "paymentMethod": "wechat",
                                "gatewayPayNumSource": "runtime-payment-boundary",
                            },
                            {
                                "operationId": "SCN-001-PATH-001-huize-issue-status",
                                "operationType": "huize-issue-status",
                                "status": "passed",
                                "paymentMethod": "wechat",
                                "gatewayPayNumSource": "runtime-payment-boundary",
                                "issueStatus": 1,
                            },
                        ],
                        "payResult": {"success": True, "status": "00000"},
                        "issueResult": {"success": True, "issueStatus": 1, "payStatus": 1},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            return {
                "returncode": 0,
                "passed": 1,
                "failed": 0,
                "errors": [],
                "duration_s": 0.3,
                "execution_entry": "agent4.playwright-formal",
                "formal_execution": True,
                "visible_browser": True,
                "report_dir": str(report_dir),
            }

    results, healing_inputs, _ = await exec_agent._normalise_execution_result(
        "sample-product",
        {
            "scenario_id": "SCN-001",
            "path_id": "PATH-001",
            "case_ids": ["TC-001"],
            "spec_path": str(spec_path),
            "root_dir": str(tmp_path),
            "completion_rule": {
                "target_node": "NODE-policy-result",
                "order_generation_boundary": True,
                "is_complete": True,
            },
            "external_operations": [
                {
                    "operation_id": "SCN-001-PATH-001-huize-pay-success",
                    "operation_type": "huize-pay-success",
                    "status": "pending",
                    "payment_method": "wechat",
                    "gateway_pay_num_source": "runtime-payment-boundary",
                },
                {
                    "operation_id": "SCN-001-PATH-001-huize-issue-status",
                    "operation_type": "huize-issue-status",
                    "status": "pending",
                    "payment_method": "wechat",
                    "gateway_pay_num_source": "runtime-payment-boundary",
                },
            ],
        },
        runner=FormalRunner(),  # type: ignore[arg-type]
        warnings=[],
        root_dir=tmp_path,
    )

    assert healing_inputs == []
    assert results[0]["status"] == "passed"
    assert results[0]["payment_closed_loop"]["status"] == "passed-after-resume"
    assert results[0]["payment_closed_loop"]["payment_method"] == "wechat"
    assert [item["status"] for item in results[0]["external_operations"]] == ["passed", "passed"]
    assert results[0]["external_operations"][1]["issue_status"] == 1
    assert results[0]["external_operation_artifacts"][0].replace("\\", "/").endswith(
        "external-ops/SCN-001-PATH-001-huize-issue-status.json"
    )

    assertion_results = exec_agent._assertion_results_from_execution_results(results)
    assert assertion_results[0]["actual_value"]["payment_closed_loop"]["status"] == "passed-after-resume"
    assert assertion_results[0]["actual_value"]["external_operations"][1]["issue_status"] == 1


@pytest.mark.asyncio
async def test_normalise_execution_result_fails_when_required_huize_issue_status_missing(
    tmp_path, monkeypatch
):
    from e2e_agent.agents import exec_agent

    spec_path = tmp_path / "agent3" / "ts-gen" / "h5" / "scenarios" / "01-path-001.spec.ts"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text("// agent3 formal spec\n", encoding="utf-8")
    monkeypatch.delenv("AGENT4_VISIBLE_BROWSER", raising=False)

    class FormalRunner:
        def check_node_available(self) -> bool:
            return True

        def has_project_test_runtime(self) -> bool:
            return True

        def run_formal_spec(self, spec_path_arg: str, report_dir: Path) -> dict[str, object]:
            return {
                "returncode": 0,
                "passed": 1,
                "failed": 0,
                "errors": [],
                "execution_entry": "agent4.playwright-formal",
                "formal_execution": True,
                "visible_browser": True,
                "report_dir": str(report_dir),
            }

    results, healing_inputs, _ = await exec_agent._normalise_execution_result(
        "sample-product",
        {
            "scenario_id": "SCN-001",
            "path_id": "PATH-001",
            "case_ids": ["TC-001"],
            "spec_path": str(spec_path),
            "root_dir": str(tmp_path),
            "completion_rule": {
                "target_node": "NODE-policy-result",
                "order_generation_boundary": True,
                "is_complete": True,
            },
            "external_operations": [
                {
                    "operation_id": "SCN-001-PATH-001-huize-issue-status",
                    "operation_type": "huize-issue-status",
                    "status": "pending",
                },
            ],
        },
        runner=FormalRunner(),  # type: ignore[arg-type]
        warnings=[],
        root_dir=tmp_path,
    )

    assert results[0]["status"] == "failed"
    assert results[0]["failure_category"] == "test_data"
    assert results[0]["payment_closed_loop"]["status"] == "missing"
    assert "Huize payment closed loop did not prove issueStatus=1" in results[0]["error_message"]
    assert healing_inputs[0][0]["failure_category"] == "test_data"


@pytest.mark.asyncio
async def test_normalise_execution_result_scales_formal_timeout_for_multi_case_specs(tmp_path, monkeypatch):
    from e2e_agent.agents import exec_agent

    spec_path = tmp_path / "agent3" / "ts-gen" / "h5" / "scenarios" / "01-path-001.spec.ts"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text("// agent3 formal spec\n", encoding="utf-8")
    captured: dict[str, object] = {}

    class FormalRunner:
        def check_node_available(self) -> bool:
            return True

        def has_project_test_runtime(self) -> bool:
            return True

        def run_formal_spec(
            self,
            spec_path_arg: str,
            report_dir: Path,
            timeout_seconds: int | None = None,
        ) -> dict[str, object]:
            captured["timeout_seconds"] = timeout_seconds
            return {
                "returncode": 0,
                "passed": 1,
                "failed": 0,
                "errors": [],
                "raw_output": "formal ok",
                "stderr": "",
                "duration_s": 0.3,
                "execution_entry": "agent4.playwright-formal",
                "formal_execution": True,
                "visible_browser": True,
                "report_dir": str(report_dir),
                "reached_target_node": "NODE-result",
                "target_node_status": "reached",
            }

    def forbidden_visible_runner(product_id, scenario):
        raise AssertionError("Agent4 must use formal Playwright for generated specs")

    monkeypatch.setattr(exec_agent, "_run_visible_chromium_scenario", forbidden_visible_runner)

    await exec_agent._normalise_execution_result(
        "demo-product",
        {
            "scenario_id": "SCN-001",
            "path_id": "PATH-001",
            "case_ids": ["TC-001", "TC-002", "TC-003", "TC-004"],
            "spec_path": str(spec_path),
            "root_dir": str(tmp_path),
            "entry_url": "https://example.com/detail",
            "completion_rule": {"target_node": "NODE-result", "is_complete": True},
            "agent3_formal_scenario_spec": True,
        },
        runner=FormalRunner(),  # type: ignore[arg-type]
        warnings=[],
        root_dir=tmp_path,
    )

    assert captured["timeout_seconds"] == 720


@pytest.mark.asyncio
async def test_normalise_execution_result_force_visible_respects_agent3_blocks(tmp_path, monkeypatch):
    from e2e_agent.agents import exec_agent

    spec_path = tmp_path / "agent3" / "ts-gen" / "h5" / "scenarios" / "01-path-001.spec.ts"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text("// agent3 formal spec\n", encoding="utf-8")
    monkeypatch.setenv("AGENT4_FORCE_VISIBLE_BROWSER", "1")

    def fake_visible_runner(product_id, scenario):
        raise AssertionError("Agent4 should not start when Agent3 blocks the path")

    class FormalRunner:
        def check_node_available(self) -> bool:
            return True

        def has_project_test_runtime(self) -> bool:
            return True

        def run_formal_spec(self, spec_path_arg: str, report_dir: Path) -> dict[str, object]:
            raise AssertionError("Agent4 should not start when Agent3 blocks the path")

    monkeypatch.setattr(exec_agent, "_run_visible_chromium_scenario", fake_visible_runner)

    results, healing_inputs, duration = await exec_agent._normalise_execution_result(
        "sample-product",
        {
            "scenario_id": "SCN-001",
            "path_id": "PATH-001",
            "case_ids": ["TC-001"],
            "spec_path": str(spec_path),
            "root_dir": str(tmp_path),
            "entry_url": "https://example.com/detail",
            "coverage_status": "coverage-gap",
            "completion_rule": {"target_node": "NODE-result", "is_complete": False},
            "script_status": "blocked",
            "script_validation_status": "failed",
            "agent3_replay_required": True,
        },
        runner=FormalRunner(),  # type: ignore[arg-type]
        warnings=[],
        root_dir=tmp_path,
    )

    assert duration == 0.0
    assert healing_inputs == []
    assert results[0]["execution_status"] == "blocked_by_agent3_contract"
    assert results[0]["failure_category"] == "agent3_contract_blocked"


@pytest.mark.asyncio
async def test_normalise_execution_result_falls_back_to_adaptive_agent4_when_formal_path_control_fails(
    tmp_path, monkeypatch
):
    from e2e_agent.agents import exec_agent

    spec_path = tmp_path / "agent3" / "ts-gen" / "h5" / "scenarios" / "01-path-001.spec.ts"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text("// agent3 formal spec\n", encoding="utf-8")
    monkeypatch.delenv("AGENT4_VISIBLE_BROWSER", raising=False)
    monkeypatch.delenv("AGENT4_DISABLE_ADAPTIVE_FALLBACK", raising=False)

    class FormalRunner:
        def check_node_available(self) -> bool:
            return True

        def has_project_test_runtime(self) -> bool:
            return True

        def run_formal_spec(self, spec_path_arg: str, report_dir: Path) -> dict[str, object]:
            assert spec_path_arg == str(spec_path)
            return {
                "returncode": 1,
                "passed": 0,
                "failed": 1,
                "errors": [
                    "Agent4 action did not advance toward expected target_url; before=/product/detail; after=/product/detail"
                ],
                "raw_output": "1 failed",
                "stderr": "",
                "duration_s": 0.4,
                "execution_entry": "agent4.playwright-formal",
                "formal_execution": True,
                "visible_browser": False,
                "report_dir": str(report_dir),
            }

    def fake_visible_runner(product_id, scenario):
        assert product_id == "demo-product"
        assert scenario["entry_url"] == "https://example.com/m/apps/cps/product/detail"
        assert scenario["agent3_replay_actions"][0]["click_strategy"] == "mouse-h5-product-footer-insure"
        return {
            "returncode": 0,
            "passed": 1,
            "failed": 0,
            "errors": [],
            "raw_output": "adaptive ok",
            "stderr": "",
            "duration_s": 1.1,
            "execution_entry": "agent4.visible-chromium",
            "formal_execution": True,
            "visible_browser": True,
            "artifacts_dir": str(tmp_path / "adaptive"),
            "target_node_status": "reached",
            "reached_target_node": "NODE-insure-form",
        }

    monkeypatch.setattr(exec_agent, "_run_visible_chromium_scenario", fake_visible_runner)

    results, healing_inputs, duration = await exec_agent._normalise_execution_result(
        "demo-product",
        {
            "scenario_id": "SCN-001",
            "path_id": "PATH-001",
            "case_ids": ["TC-001"],
            "spec_path": str(spec_path),
            "root_dir": str(tmp_path),
            "entry_url": "https://example.com/m/apps/cps/product/detail",
            "completion_rule": {"target_node": "NODE-insure-form", "is_complete": True},
            "agent3_formal_scenario_spec": True,
            "agent3_replay_actions": [
                {
                    "selector": "div:nth-of-type(7)",
                    "text": "投 保",
                    "click_strategy": "mouse-h5-product-footer-insure",
                }
            ],
        },
        runner=FormalRunner(),  # type: ignore[arg-type]
        warnings=[],
        root_dir=tmp_path,
    )

    assert duration == 1.1
    assert healing_inputs == []
    assert results[0]["execution_status"] == "passed"
    assert results[0]["execution_entry"] == "agent4.visible-chromium"
    assert results[0]["agent4_adaptive_fallback"] is True
    assert results[0]["formal_failure_category"] == "script_bug"
    assert "did not advance toward expected target_url" in results[0]["formal_error_message"]


@pytest.mark.asyncio
async def test_normalise_execution_result_does_not_adaptive_fallback_for_formal_env_issue(
    tmp_path, monkeypatch
):
    from e2e_agent.agents import exec_agent

    spec_path = tmp_path / "agent3" / "ts-gen" / "h5" / "scenarios" / "01-path-001.spec.ts"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text("// agent3 formal spec\n", encoding="utf-8")
    monkeypatch.delenv("AGENT4_VISIBLE_BROWSER", raising=False)
    monkeypatch.delenv("AGENT4_DISABLE_ADAPTIVE_FALLBACK", raising=False)

    class FormalRunner:
        def check_node_available(self) -> bool:
            return True

        def has_project_test_runtime(self) -> bool:
            return True

        def run_formal_spec(self, spec_path_arg: str, report_dir: Path) -> dict[str, object]:
            return {
                "returncode": 1,
                "passed": 0,
                "failed": 1,
                "errors": ["系统正在维护，错误代码：502"],
                "raw_output": "1 failed",
                "stderr": "",
                "duration_s": 0.4,
                "execution_entry": "agent4.playwright-formal",
                "formal_execution": True,
                "visible_browser": False,
                "report_dir": str(report_dir),
            }

    def forbidden_visible_runner(product_id, scenario):
        raise AssertionError("Agent4 must not hide backend/env failures with adaptive fallback")

    monkeypatch.setattr(exec_agent, "_run_visible_chromium_scenario", forbidden_visible_runner)

    results, healing_inputs, duration = await exec_agent._normalise_execution_result(
        "demo-product",
        {
            "scenario_id": "SCN-001",
            "path_id": "PATH-001",
            "case_ids": ["TC-001"],
            "spec_path": str(spec_path),
            "root_dir": str(tmp_path),
            "entry_url": "https://example.com/m/apps/cps/product/detail",
            "completion_rule": {"target_node": "NODE-insure-form", "is_complete": True},
            "agent3_formal_scenario_spec": True,
            "agent3_replay_actions": [
                {
                    "selector": "div:nth-of-type(7)",
                    "text": "投 保",
                    "click_strategy": "mouse-h5-product-footer-insure",
                }
            ],
        },
        runner=FormalRunner(),  # type: ignore[arg-type]
        warnings=[],
        root_dir=tmp_path,
    )

    assert duration == 0.4
    assert results[0]["execution_status"] == "failed"
    assert results[0]["failure_category"] == "env_issue"
    assert healing_inputs[0][0]["failure_category"] == "env_issue"
    assert results[0].get("agent4_adaptive_fallback") is False


@pytest.mark.asyncio
async def test_normalise_execution_result_classifies_formal_error_context_502_as_env_issue(tmp_path, monkeypatch):
    from e2e_agent.agents import exec_agent

    spec_path = tmp_path / "scenario.spec.ts"
    spec_path.write_text("// @generated-by mpt-ins-ts-gen\n", encoding="utf-8")
    monkeypatch.delenv("AGENT4_VISIBLE_BROWSER", raising=False)

    class FormalRunner:
        def check_node_available(self) -> bool:
            return True

        def has_project_test_runtime(self) -> bool:
            return True

        def run_formal_spec(self, spec_path_arg: str, report_dir: Path) -> dict[str, object]:
            assert spec_path_arg == str(spec_path)
            error_context = report_dir / "test-results" / "scenario-chromium" / "error-context.md"
            error_context.parent.mkdir(parents=True, exist_ok=True)
            error_context.write_text(
                "Error: Agent3 replay transition failed; body=\n"
                "系统正在维护\n"
                "由于系统内部发生错误(错误代码：502)该页面暂时无法访问\n",
                encoding="utf-8",
            )
            return {
                "returncode": 1,
                "passed": 0,
                "failed": 1,
                "errors": [
                    "Error: Agent3 replay transition failed; body=; 10026 | throw new Error(...)"
                ],
                "raw_output": "1 failed",
                "stderr": "",
                "duration_s": 0.1,
                "execution_entry": "agent4.playwright-formal",
                "formal_execution": True,
                "visible_browser": False,
                "report_dir": str(report_dir),
            }

    results, healing_inputs, _ = await exec_agent._normalise_execution_result(
        "demo-product",
        {
            "scenario_id": "SCN-001",
            "path_id": "PATH-001",
            "case_ids": ["TC-001"],
            "spec_path": str(spec_path),
            "root_dir": str(tmp_path),
            "completion_rule": {"target_node": "NODE-result", "is_complete": True},
        },
        runner=FormalRunner(),  # type: ignore[arg-type]
        warnings=[],
        root_dir=tmp_path,
    )

    assert results[0]["failure_category"] == "env_issue"
    assert healing_inputs[0][0]["failure_category"] == "env_issue"
    assert "系统正在维护" in results[0]["error_message"]
    assert "错误代码：502" in results[0]["body_excerpt"]


def test_agent4_uses_agent3_replay_actions_in_visible_payload(tmp_path, monkeypatch):
    from e2e_agent.agents import exec_agent

    def fake_run(command, cwd, capture_output, text, encoding, errors, timeout):
        payload = json.loads(Path(command[2]).read_text(encoding="utf-8"))
        assert payload["agent3_replay"]["source"] == "explore_trace.action_trace"
        assert payload["agent3_replay"]["action_count"] == 1
        assert payload["agent3_replay"]["enforced"] is True
        assert payload["viewport"] == {"width": 390, "height": 844}
        assert payload["real_actions"] == [
            {
                "action_key": "action.buy_now",
                "selector": "#submit-by",
                "text": "投 保",
            }
        ]
        result_path = (
            tmp_path
            / "products"
            / "demo-product"
            / "agent4"
            / "exec"
            / "visible-runs"
            / "run-001"
            / "SCN-001"
            / "result.json"
        )
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(json.dumps({"returncode": 0, "passed": 1, "failed": 0}), encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(exec_agent.subprocess, "run", fake_run)
    monkeypatch.setenv("AGENT4_ARTIFACT_ROOT", str(tmp_path))

    result = exec_agent._run_visible_chromium_scenario(
        "demo-product",
        {
            "scenario_id": "SCN-001",
            "path_id": "PATH-001",
            "root_dir": str(tmp_path),
            "run_id": "run-001",
            "entry_url": "https://example.com/m/apps/cps/detail",
            "real_actions": [{"selector": ".guessed", "text": "立即投保"}],
            "agent3_replay_source": "explore_trace.action_trace",
            "agent3_replay_actions": [
                {
                    "action_key": "action.buy_now",
                    "selector": "#submit-by",
                    "text": "投 保",
                }
            ],
        },
    )

    assert result["execution_entry"] == "agent4.visible-chromium"


def test_agent4_visible_payload_carries_agent3_successful_insurance_date(tmp_path, monkeypatch):
    from e2e_agent.agents import exec_agent

    def fake_run(command, cwd, capture_output, text, encoding, errors, timeout):
        payload = json.loads(Path(command[2]).read_text(encoding="utf-8"))
        assert payload["mock_data"]["policy.start_date"] == "2026-06-11"
        assert payload["mock_data"]["insuranceDate_102"] == "2026-06-05"
        assert payload["mock_data"]["insuranceDate"] == "2026-06-05"
        result_path = (
            tmp_path
            / "products"
            / "demo-product"
            / "agent4"
            / "exec"
            / "visible-runs"
            / "run-001"
            / "SCN-001"
            / "result.json"
        )
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(json.dumps({"returncode": 0, "passed": 1, "failed": 0}), encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(exec_agent.subprocess, "run", fake_run)
    monkeypatch.setenv("AGENT4_ARTIFACT_ROOT", str(tmp_path))

    result = exec_agent._run_visible_chromium_scenario(
        "demo-product",
        {
            "scenario_id": "SCN-001",
            "path_id": "PATH-001",
            "root_dir": str(tmp_path),
            "run_id": "run-001",
            "entry_url": "https://example.com/m/apps/cps/detail",
            "mock_data": {"policy.start_date": "2026-06-11"},
            "agent3_replay_actions": [
                {
                    "action_key": "action.submit",
                    "selector": ".submit-btn",
                    "submit_diagnostics": [
                        {
                            "state": {
                                "pythonTrace": {
                                    "requests": [
                                        {
                                            "post_data": json.dumps(
                                                {
                                                    "startDate": "2026-06-05",
                                                    "data": {"102": [{"insuranceDate": "2026-06-05"}]},
                                                }
                                            )
                                        }
                                    ]
                                }
                            }
                        }
                    ],
                }
            ],
        },
    )

    assert result["execution_entry"] == "agent4.visible-chromium"


def test_agent4_normalizes_real_actions_in_visible_payload(tmp_path, monkeypatch):
    from e2e_agent.agents import exec_agent

    def fake_run(command, cwd, capture_output, text, encoding, errors, timeout):
        payload = json.loads(Path(command[2]).read_text(encoding="utf-8"))
        assert payload["real_actions"] == [
            {
                "selector": ".submit-btn",
                "click_strategy": "touchscreen-submit-btn",
                "planned_from_node_id": "NODE-insure-form",
                "action_key": "action.submit",
            }
        ]
        result_path = (
            tmp_path
            / "products"
            / "demo-product"
            / "agent4"
            / "exec"
            / "visible-runs"
            / "run-001"
            / "SCN-001"
            / "result.json"
        )
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(json.dumps({"returncode": 0, "passed": 1, "failed": 0}), encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(exec_agent.subprocess, "run", fake_run)
    monkeypatch.setenv("AGENT4_ARTIFACT_ROOT", str(tmp_path))

    result = exec_agent._run_visible_chromium_scenario(
        "demo-product",
        {
            "scenario_id": "SCN-001",
            "path_id": "PATH-001",
            "root_dir": str(tmp_path),
            "run_id": "run-001",
            "entry_url": "https://example.com/m/apps/cps/detail",
            "real_actions": [
                {
                    "selector": ".submit-btn",
                    "click_strategy": "touchscreen-submit-btn",
                    "planned_from_node_id": "NODE-insure-form",
                }
            ],
        },
    )

    assert result["execution_entry"] == "agent4.visible-chromium"


def test_agent4_visible_exec_dir_prefers_run_dir_for_formal_artifacts(tmp_path):
    from e2e_agent.agents import exec_agent

    run_dir = tmp_path / "products" / "demo-product.assets" / "runs" / "run-001"

    out_dir = exec_agent._visible_exec_dir(
        "demo-product",
        {
            "scenario_id": "SCN-001",
            "path_id": "PATH-001",
            "root_dir": str(tmp_path),
            "run_dir": str(run_dir),
            "run_id": "run-001",
        },
    )

    assert out_dir == run_dir / "agent4" / "exec" / "visible-runs" / "run-001" / "SCN-001"


def test_agent4_formal_exec_dir_uses_product_artifact_dir_without_run_dir(tmp_path):
    from e2e_agent.agents import exec_agent

    artifact_dir = tmp_path / "products" / "demo-product" / "demo.assets"

    out_dir = exec_agent._formal_exec_dir(
        "demo-product",
        {
            "root_dir": str(tmp_path),
            "product_artifact_dir": str(artifact_dir),
        },
        root_dir=tmp_path,
    )

    assert out_dir == artifact_dir / "agent4" / "tc-exec"


def test_visible_runner_script_uses_flexible_text_clickable_locator():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()

    assert "async function locatorForAction" in script
    assert "button, a, [role=\"button\"]" in script
    assert "input[type=\"button\"]" in script
    assert "getByText" in script


def test_visible_runner_defaults_to_chrome_not_edge():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()

    assert "PLAYWRIGHT_CHROMIUM_CHANNEL || 'msedge'" not in script
    assert "channel: process.env.PLAYWRIGHT_CHROMIUM_CHANNEL || 'chrome'" in script


def test_agent4_skill_timeout_scales_with_case_count(monkeypatch):
    from e2e_agent.agents import exec_agent

    monkeypatch.delenv("AGENT4_SKILL_TIMEOUT_S", raising=False)

    assert exec_agent._agent4_skill_timeout_seconds(
        [
            {"case_ids": ["TC-001", "TC-002"]},
            {"case_ids": ["TC-003", "TC-004", "TC-005"]},
        ]
    ) == 900


def test_agent4_builds_assertion_results_from_real_execution_results():
    from e2e_agent.agents import exec_agent

    assertion_results = exec_agent._assertion_results_from_execution_results(
        [
            {
                "case_id": "TC-001",
                "path_id": "PATH-001",
                "status": "passed",
                "execution_status": "passed",
                "target_node": "NODE-payment",
                "target_node_status": "reached",
                "reached_target_node": "NODE-payment",
                "final_url": "https://example.test/pay",
                "screenshots": [{"path": "agent4/screenshots/final.png"}],
            },
            {
                "case_id": "TC-002",
                "path_id": "PATH-002",
                "status": "failed",
                "execution_status": "failed",
                "target_node": "NODE-policy-service",
                "target_node_status": "not_reached",
                "blocked_reason": "not logged in",
            },
        ],
        [
            {"case_id": "TC-001", "business_intent": "payment"},
            {"case_id": "TC-002", "business_intent": "policy"},
        ],
    )

    assert [item["case_id"] for item in assertion_results] == ["TC-001", "TC-002"]
    assert assertion_results[0]["status"] == "passed"
    assert assertion_results[0]["expected_value"]["target_node"] == "NODE-payment"
    assert assertion_results[0]["actual_value"]["case_target_node_status"] == "reached"
    assert assertion_results[0]["actual_value"]["reached_target_node"] == "NODE-payment"
    assert assertion_results[0]["screenshot_path"] == "agent4/screenshots/final.png"
    assert assertion_results[1]["status"] == "failed"
    assert assertion_results[1]["expected_value"]["target_node"] == "NODE-policy-result"
    assert assertion_results[1]["error_message"] == "not logged in"


def test_agent4_assertion_results_preserve_fact_lineage_from_execution_results():
    from e2e_agent.agents import exec_agent

    lineage = {
        "version": "fact-lineage-v1",
        "scenario_id": "SCN-001",
        "path_id": "PATH-001",
        "case_ids": ["TC-001"],
        "test_data_profile_ids": ["TDP-path-001-default"],
        "action_evidence": {"source": "agent3.trace", "action_count": 1},
        "assertion_refs": ["ASSERT-001-001"],
        "coverage_status": "covered",
        "contract_status": "compiled",
    }

    assertion_results = exec_agent._assertion_results_from_execution_results(
        [
            {
                "case_id": "TC-001",
                "path_id": "PATH-001",
                "status": "passed",
                "execution_status": "passed",
                "target_node": "NODE-policy-result",
                "target_node_status": "reached",
                "reached_target_node": "NODE-policy-result",
                "fact_lineage": lineage,
            }
        ]
    )

    assert assertion_results[0]["fact_lineage"] == lineage
    assert assertion_results[0]["actual_value"]["fact_lineage"] == lineage


def test_agent4_does_not_treat_identity_task_handoff_as_downstream_assertion_pass():
    from e2e_agent.agents import exec_agent

    def handoff_result(case_id: str) -> dict:
        return {
            "case_id": case_id,
            "path_id": "PATH-001",
            "status": "passed",
            "execution_status": "passed",
            "target_node": "NODE-policy-result",
            "target_node_status": "reached",
            "reached_target_node": "NODE-policy-result",
            "target_node_inference": "agent3.order_generation_boundary",
            "final_url": "https://commerce.example.test/m/apps/cps/demo-channel/product/insure",
            "executed_actions": [
                {
                    "action_key": "action.submit",
                    "target_url": "https://commerce.example.test/m/apps/cps/demo-channel/product/insure",
                    "submit_api_result": {
                        "order_generated": True,
                        "task_handoff": True,
                        "direct_order": False,
                        "code": "37009",
                        "msg": "identity verification required",
                    },
                }
            ],
        }

    assertion_results = exec_agent._assertion_results_from_execution_results(
        [
            handoff_result("TC-policy"),
            handoff_result("TC-underwriting"),
            handoff_result("TC-payment"),
        ],
        [
            {"case_id": "TC-policy", "business_intent": "policy"},
            {"case_id": "TC-underwriting", "business_intent": "underwriting"},
            {"case_id": "TC-payment", "business_intent": "payment"},
        ],
    )

    assert {item["case_id"]: item["status"] for item in assertion_results} == {
        "TC-policy": "failed",
        "TC-underwriting": "failed",
        "TC-payment": "failed",
    }
    assert all(
        item["actual_value"]["case_target_node_status"] == "not_reached"
        for item in assertion_results
    )


def test_visible_runner_uses_submit_order_text_aliases():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()

    assert "function actionTextAliases(action)" in script
    assert "提交投保单" in script
    assert "提交投保" in script


def test_visible_runner_script_answers_health_notice_as_no_issue():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    health_block = script[
        script.index("if (action.action_key === 'action.answer_health_notice' || isAgent3HealthNoticeStrategy(action))"):
        script.index("if (isAutoWaitAction(action))")
    ]

    assert "action.answer_health_notice" in script
    assert "确认无以上问题" in script
    assert "health-notice-no-issue" in script
    assert "non-purpose-last-option" in script
    assert "if (action.planned_to_node_id === 'NODE-insure-form')" in health_block
    assert "await syncVisibleH5InsureInputs(page);" in health_block
    assert "await syncVisibleH5InsureModelState(page);" in health_block


def test_visible_runner_health_notice_clicks_exact_no_issue_control_not_parent_container():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    health_block = script[
        script.index("async function answerHealthNotice"):
        script.index("async function autoAnswerQuestionnaireIfPresent")
    ]

    assert "function healthNoticeNoIssueCandidates" in script
    assert "isExactHealthNoticeNoIssueText" in script
    assert "有部分问题" in script
    assert "input, button, a, label, [role=\"button\"], .insure-label" in health_block
    assert "input, button, a, label, span, div" not in health_block
    assert "choice_rule: 'health_notice_no_issue'" in health_block


def test_visible_runner_health_notice_does_not_use_questionnaire_fallback():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    health_block = script[
        script.index("async function answerHealthNotice"):
        script.index("async function autoAnswerQuestionnaireIfPresent")
    ]

    assert "answerQuestionnaire" not in health_block
    assert "health_notice_no_issue" in health_block


def test_visible_runner_health_notice_refuses_issue_option_fallback():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()

    assert "HEALTH_NOTICE_ISSUE_PATTERN" in script
    assert "HEALTH_NOTICE_NO_ISSUE_PATTERN" in script
    assert "Health notice no-issue control not found; refusing to submit issue state" in script
    assert "answerResult?.clicked_count" in script


def test_visible_runner_auto_followup_uses_health_notice_strategy():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()

    assert "const hasHealthNotice = await healthNoticeVisible(page);" in script
    assert "await answerHealthNotice(page, action)" in script


def test_visible_runner_auto_followup_prioritizes_health_notice_over_questionnaire_dom():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    auto_block = script[
        script.index("async function autoAnswerQuestionnaireIfPresent"):
        script.index("function isAutoWaitAction")
    ]

    assert "const answerResult = hasHealthNotice" in auto_block
    assert "? await answerHealthNotice(page, action)" in auto_block
    assert ": await answerQuestionnaire(page, { ...action, answer_strategy: 'business_questionnaire_rule' })" in auto_block
    assert "hasHealthNotice && !questionCount" not in auto_block


def test_visible_runner_script_skips_absent_optional_path_actions():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()

    assert "skip_if_absent" in script
    assert "optional-action-skip" in script
    assert "planned_from_node_id" in script


def test_visible_runner_script_skips_absent_optional_questionnaire_actions():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    questionnaire_block = script[
        script.index("if (action.action_key === 'action.answer_questionnaire')"):
        script.index("if (action.action_key === 'action.answer_health_notice' || isAgent3HealthNoticeStrategy(action))")
    ]

    assert "action.skip_if_absent" in questionnaire_block
    assert "planned optional questionnaire page not present" in questionnaire_block
    assert "click_strategy: 'optional-action-skip'" in questionnaire_block


def test_visible_runner_script_skips_absent_optional_auto_wait_actions():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    auto_wait_block = script[
        script.index("if (isAutoWaitAction(action))"):
        script.index("let locator = await locatorForAction(page, action);")
    ]

    assert "action.skip_if_absent" in auto_wait_block
    assert "planned optional auto-wait target not reached" in auto_wait_block
    assert ".click_strategy = 'optional-action-skip'" in auto_wait_block


def test_visible_runner_only_probes_mock_data_on_insure_form_nodes():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()

    assert "function shouldProbeFieldsForNode(nodeId)" in script
    assert "NODE-insure-form" in script
    assert "record.node_id === nodeId" in script
    assert "(record.matched_node_ids || []).includes(nodeId)" in script
    assert "await applyMockData(page, action.planned_from_node_id);" in script
    assert "await applyMockData(page, action.planned_to_node_id);" in script
    assert "await applyMockData(page, payload.page_element_plan || [], payload.mock_data || {});" not in script


def test_visible_runner_fills_only_runnable_mock_fields_once_per_node():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()

    assert "const filledMockDataNodes = new Set();" in script
    assert "filledMockDataNodes.has(nodeId)" in script
    assert "function isRunnableMockField(field)" in script
    assert "field.required" in script
    assert ".filter(isRunnableMockField)" in script
    assert "mock-node-not-ready" in script
    assert "mock-node-fill-empty" in script


def test_visible_runner_fills_optional_insure_form_fields_when_required_contract_is_empty():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()

    assert "function isFallbackRunnableMockField(nodeId, field)" in script
    assert "function mockValueForField(field)" in script
    assert "const requiredFields = contractFields.filter(isRunnableMockField);" in script
    assert "if (requiredFields.length) return requiredFields;" in script
    assert "contractFields.filter(field => isFallbackRunnableMockField(nodeId, field))" in script


def test_visible_runner_preserves_default_insure_form_fields():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()

    assert "function isDefaultPreservedField(field)" in script
    assert "policy.start_date" in script
    assert "insured.relation" in script
    assert "insure_form.insuredrelation" in script
    assert "insure_form.cardtype" in script
    assert "insure_form.insuredidtype" in script
    assert "insure_form.cardvalidtype" in script
    assert "!isDefaultPreservedField(field)" in script


def test_visible_runner_dedupes_field_contracts_before_filling():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()

    assert "function dedupeResolutionsByFieldKey(resolutions)" in script
    assert "function resolutionRank(resolution)" in script
    assert "verified_static" in script
    assert "dedupeResolutionsByFieldKey(resolutions).map" in script


def test_visible_runner_has_specialized_insure_form_widget_strategies():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()

    assert "async function fillDatePickerOrNativeInput(page, locator, field, value)" in script
    assert "async function markRelatedCascadeControls(page, locator, token, kind)" in script
    assert "async function selectCascadeControls(page, locator, kind, preferredValues)" in script
    assert "await fillDatePickerOrNativeInput(page, locator, field, value);" in script
    assert "await selectRegion(page, locator, value, field);" in script
    assert "await selectOccupation(page, locator, value, field);" in script
    assert ".hz-check-item" in script
    assert "insure-form-state-before-submit" in script


def test_visible_runner_uses_hz_named_widget_helpers_for_insure_form_controls():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()

    assert "async function fillCardValidityByNamePrefix(page, namePrefix, value)" in script
    assert "async function selectHzCascadeByNamePrefix(page, namePrefix, preferredValues)" in script
    assert "function namePrefixForInsureField(field, kind)" in script
    assert "provCityText_10" in script
    assert "jobText_10" in script
    assert "cardPeriod_10" in script


def test_visible_runner_uses_child_occupation_path_for_insured_jobtext20():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()

    assert "function occupationPreferredValues(value, namePrefix = '')" in script
    assert "namePrefix.includes('jobText_20')" in script
    assert "文教行业人员" in script
    assert "教育机构从业人员" in script
    assert "一般学生" in script
    assert "occupationPreferredValues(value, namePrefix)" in script


def test_visible_runner_verifies_cascade_control_changed_before_counting_success():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    cascade_block = script[
        script.index("async function selectHzCascadeByNamePrefix"):
        script.index("async function markRelatedCascadeControls")
    ]

    assert "const anchorBox = await control.boundingBox().catch(() => null);" in cascade_block
    assert "await clickVisibleDropdownOption(page, preferred, anchorBox);" in cascade_block
    assert "selected && isPlaceholderText(after)" in cascade_block
    assert "cascade-selection-still-placeholder" in cascade_block
    assert "if (!isPlaceholderText(after))" in cascade_block
    assert "selected || !isPlaceholderText(after)" not in cascade_block


def test_visible_runner_uses_direct_h5_submit_preflight_without_full_page_repair_scroll():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    submit_block = script[
        script.index("if ((action.action_key === 'action.submit' || isH5SubmitAction(action)) && action.planned_from_node_id === 'NODE-insure-form')"):
        script.index("if (action.action_key === 'action.answer_questionnaire'")
    ]

    assert "async function repairInsureFormBeforeSubmit(page)" in script
    assert "await repairInsureFormBeforeSubmit(page);" not in submit_block
    assert "const agreementCheckedCount = await forceConfirmAgreementCheckboxes(page);" in submit_block
    assert "h5-submit-preflight" in submit_block
    assert submit_block.index("await clearVisibleBankAccountError(page);") < submit_block.index("const formState = await inspectInsureFormState(page);")


def test_visible_runner_waits_for_insure_form_ready_before_submit_preflight():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    submit_block = script[
        script.index("if ((action.action_key === 'action.submit' || isH5SubmitAction(action)) && action.planned_from_node_id === 'NODE-insure-form')"):
        script.index("if (action.action_key === 'action.agree_all'")
    ]

    assert "async function waitForMockDataNodeReady(page, nodeId" in script
    assert "await waitForMockDataNodeReady(page, 'NODE-insure-form'" in submit_block
    assert submit_block.index("await waitForMockDataNodeReady(page, 'NODE-insure-form'") < submit_block.index("const agreementCheckedCount = await forceConfirmAgreementCheckboxes(page);")
    assert "insure form did not become ready before submit" in script


def test_visible_runner_clicks_first_visible_dropdown_option_not_hidden_first_match():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()

    assert "async function firstVisibleDropdownOption(page, pattern)" in script
    assert "async function firstVisibleNonPlaceholderDropdownOption(page)" in script
    assert "matching.nth(index)" in script
    assert "await firstVisibleDropdownOption(page, new RegExp" in script


def test_visible_runner_dropdown_fallback_skips_placeholder_options():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()

    assert "!/请选择|select|please/i.test(text)" in script
    assert "await firstVisibleNonPlaceholderDropdownOption(page)" in script


def test_visible_runner_recovers_transient_page_errors_before_form_probe():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()

    assert "async function recoverTransientPageError(page)" in script
    assert "请求超时|页面暂时无法访问|系统正在维护|502|Bad Gateway" in script
    assert "await recoverTransientPageError(page);" in script


def test_visible_runner_does_not_toggle_agreement_after_global_check():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    agreement_block = script[
        script.index("if (strategy === 'check_agreement'"):
        script.index("if (strategy === 'select_by_text_or_value'")
    ]

    assert "const checkedCount = await checkAllAgreementCheckboxes(page);" in agreement_block
    assert "if (!checkedCount)" in agreement_block


def test_visible_runner_agreement_strategy_uses_parent_check_items_and_global_confirm():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    check_block = script[
        script.index("async function checkAllAgreementCheckboxes"):
        script.index("async function fillByStrategy")
    ]

    assert "button.btn-agree" in script
    assert "const globalConfirm = page.locator('button.btn-agree, .btn-agree, button" in script
    assert ".layui-layer" in script
    assert "const custom = page.locator('.hz-check-item, .hz-checkbox" in check_block
    assert "const custom = page.locator('.hz-check-item, .hz-check-icon" not in check_block


def test_visible_runner_reads_protocol_tabs_before_agreement_confirm():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    confirm_block = script[
        script.index("async function confirmAgreementDialogs"):
        script.index("async function agreementCheckboxMeta")
    ]

    assert "async function readAgreementDialogTabs(page, dialog)" in script
    assert "[role=\"tab\"], .am-tabs-tab" in script
    assert "保险条款|责任免除|隐私|声明" in script
    assert "agreement-dialog-read" in script
    assert "await readAgreementDialogTabs(page, dialog);" in confirm_block
    assert confirm_block.index("await readAgreementDialogTabs(page, dialog);") < confirm_block.index("const scopedConfirm")


def test_visible_runner_agreement_dialog_locator_includes_ant_mobile_modal():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    confirm_block = script[
        script.index("async function confirmAgreementDialogs"):
        script.index("async function agreementCheckboxMeta")
    ]

    assert ".am-modal-wrap" in confirm_block
    assert ".am-modal-content" in confirm_block
    assert ".am-modal-body" in confirm_block
    assert "input[type=\"button\"]" in confirm_block
    assert "input[type=\"submit\"]" in confirm_block
    assert "agreement-dialog-scan" in confirm_block
    assert "await readAgreementDialogTabs(page, page.locator('body'));" in confirm_block


def test_visible_runner_agreement_meta_does_not_wait_for_missing_locator():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    meta_block = script[
        script.index("async function agreementCheckboxMeta"):
        script.index("function isAgreementText")
    ]

    assert "const visible = await locator.first().isVisible({ timeout: 500 }).catch(() => false);" in meta_block
    assert "if (!visible) return { text: '', checked: false, visible: false };" in meta_block
    assert "return await locator.first().evaluate" in meta_block


def test_visible_runner_rechecks_agreements_after_dialog_confirmation():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    repair_block = script[
        script.index("async function repairInsureFormBeforeSubmit"):
        script.index("async function inspectInsureFormState")
    ]

    assert "async function ensureAllAgreementsConfirmed(page)" in script
    assert "await ensureAllAgreementsConfirmed(page)" in repair_block
    assert "agreement-confirm-final-state" in script


def test_visible_runner_closes_protocol_dialog_when_no_confirm_button():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    confirm_block = script[
        script.index("async function confirmAgreementDialogs"):
        script.index("async function agreementCheckboxMeta")
    ]

    assert "async function closeAgreementDialogs(page)" in script
    assert "agreement-dialog-close" in script
    assert "await closeAgreementDialogs(page);" in confirm_block
    assert "return { confirmed: false, closed: true" in confirm_block


def test_visible_runner_clicks_protocol_checkbox_control_and_force_confirms():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    check_block = script[
        script.index("async function checkAllAgreementCheckboxes"):
        script.index("async function ensureAllAgreementsConfirmed")
    ]

    assert "async function clickAgreementLineControl(page, item)" in script
    assert "async function forceConfirmAgreementCheckboxes(page)" in script
    assert "await clickAgreementLineControl(page, item)" in check_block
    assert "checkedCount += await forceConfirmAgreementCheckboxes(page);" in script
    assert "agreement-force-confirm" in script


def test_visible_runner_force_confirms_framework_agreement_state():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    force_block = script[
        script.index("async function forceAgreementFrameworkState(page)"):
        script.index("async function forceConfirmAgreementCheckboxes(page)")
    ]

    assert "__vue__" in force_block
    assert "__vueParentComponent" in force_block
    assert "$forceUpdate" in force_block
    assert "agreement-framework-force" in script
    assert "await forceAgreementFrameworkState(page);" in script


def test_visible_runner_clicks_each_agreement_square_before_dialog_handling():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    click_block = script[
        script.index("async function clickAgreementLineControl(page, item)"):
        script.index("async function forceConfirmAgreementCheckboxes(page)")
    ]

    assert "await readAgreementLineDocuments(page, item);" not in click_block
    assert "agreement-square-click" in script
    assert "await confirmAgreementDialogs(page, { allowBodyFallback: false });" in click_block
    assert click_block.index("await target.click") < click_block.index("await confirmAgreementDialogs(page, { allowBodyFallback: false });")
    assert "checkedAfterDialog" in click_block


def test_visible_runner_processes_multiple_agreement_squares_without_text_link_reads():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    check_block = script[
        script.index("async function checkAllAgreementCheckboxes"):
        script.index("async function ensureAllAgreementsConfirmed")
    ]

    assert "for (let pass = 0; pass < 3; pass += 1)" in check_block
    assert "await clickAgreementLineControl(page, item)" in check_block
    assert "break" not in check_block[check_block.index("for (let pass = 0; pass < 3; pass += 1)"):]


def test_visible_runner_uses_real_wheel_scroll_for_agreement_dialog_reading():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()
    scroll_block = script[
        script.index("async function scrollAgreementDialogToBottom"):
        script.index("async function readAgreementDialogTabs")
    ]

    assert "const scrollArea = dialog" in scroll_block
    assert "const areaCount = Math.min(await scrollArea.count().catch(() => 0), 10);" in scroll_block
    assert "isVisible({ timeout: 200 })" in scroll_block
    assert "await page.mouse.move" in scroll_block
    assert "await page.mouse.wheel(0, 1800)" in scroll_block
    assert "await page.keyboard.press('PageDown')" in scroll_block


def test_visible_runner_does_not_use_generic_field_key_alias_tokens():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()

    assert "FIELD_ALIAS_STOP_WORDS" in script
    assert "'insure'" in script
    assert "token.length >= 4" in script
    assert "attr:insure" not in script


def test_visible_runner_consumes_agent3_resolution_contract():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()

    assert "function fieldsForNodeFromContract(nodeId)" in script
    assert "payload.field_resolution_plan" in script
    assert "payload.component_strategy" in script
    assert "field.field_resolution?.selected_locator" in script
    assert "async function fillByStrategy(page, locator, field, value)" in script
    assert "date_picker_select_or_fill" in script
    assert "occupation_search_and_select" in script
    assert "region_cascade_select" in script
    assert "check_agreement" in script
    assert "field-contract-fill" in script


def test_visible_runner_target_probes_fields_without_static_selector():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()

    assert "async function resolveFieldLocator" in script
    assert "targetProbeFieldSelector" in script
    assert "field.locators" in script
    assert "label_text" in script
    assert "data-agent-field-target" in script
    assert "field-probe-fill" in script


def test_visible_runner_handles_auto_wait_action_without_locator_click():
    from e2e_agent.agents import exec_agent

    script = exec_agent._visible_runner_script()

    assert "action.auto_wait_for_next_node" in script
    assert "auto_wait_for_next_node" in script
    assert "auto-wait" in script
    assert script.index("action.auto_wait_for_next_node") < script.index("let locator = await locatorForAction(page, action);")


@pytest.mark.asyncio
async def test_exec_healing_node_writes_schema_valid_test_report_artifacts(tmp_path, monkeypatch):
    from jsonschema import Draft7Validator

    from e2e_agent.agents import exec_agent

    class NoEntryLoader:
        def load_skill(self, _: str) -> object:
            raise FileNotFoundError("skip skill entry")

    monkeypatch.setattr(exec_agent, "_ROOT_DIR", tmp_path)
    monkeypatch.setattr(exec_agent, "SkillPackageLoader", lambda: NoEntryLoader())

    result = await exec_agent.exec_healing_node(
        {
            "product_id": "demo-product",
            "run_id": "run-schema",
            "scenarios": [],
            "assertion_results": [],
            "artifact_root_dir": str(tmp_path),
        }
    )

    schema = json.loads(
        (Path(__file__).resolve().parents[2] / "schemas" / "v1" / "test-report.schema.json").read_text(encoding="utf-8")
    )
    for filename in ("test-report.json", "reports.json"):
        artifact = json.loads((tmp_path / "products" / "demo-product" / "agent4" / filename).read_text(encoding="utf-8"))
        errors = sorted(Draft7Validator(schema).iter_errors(artifact), key=lambda err: list(err.path))
        assert errors == []

    legacy_reports = json.loads(
        (tmp_path / "products" / "demo-product" / "agent4" / "reports-legacy.json").read_text(encoding="utf-8")
    )
    assert legacy_reports == result["reports"]


@pytest.mark.asyncio
async def test_exec_healing_node_carries_governance_summary_into_report(tmp_path, monkeypatch):
    from e2e_agent.agents import exec_agent

    monkeypatch.setattr(exec_agent, "_ROOT_DIR", tmp_path)

    result = await exec_agent.exec_healing_node(
        {
            "product_id": "demo-product",
            "run_id": "run-001",
            "scenarios": [],
            "assertion_results": [],
            "runtime_context": {
                "session_key": "demo-product:pc",
                "session_reused": True,
                "account": {"account_id": "default-pc"},
                "storage_state_path": str(tmp_path / "pc-storage-state.json"),
            },
            "governance_summary": {
                "summary": {"total_page_keys": 3},
                "warnings": ["Path uses non-whitelisted state keys: ageBand"],
            },
        }
    )

    report = result["reports"][0]
    assert report["governance_source"] == "state.governance_summary"
    assert report["planned_page_key_count"] == 3
    assert report["governance_warning_count"] == 1
    assert report["session_reused"] is True
    assert result["teardown_report"]["session_key"] == "demo-product:pc"
    assert result["artifact_fingerprints"][-1]["artifact_type"] == "healing-events"


@pytest.mark.asyncio
async def test_exec_healing_node_does_not_return_error_for_info_only_fallback_warning(tmp_path, monkeypatch):
    from e2e_agent.agents import exec_agent

    class NoEntryLoader:
        def load_skill(self, _: str) -> object:
            raise FileNotFoundError("skip skill entry")

    async def fake_normalise_execution_result(product_id, scenario, runner, warnings, root_dir=None):
        warnings.append("Used Python fallback for generated scenario: scenario.spec.ts")
        return (
            [
                {
                    "case_id": "CASE-001",
                    "path_id": "PATH-001",
                    "status": "passed",
                    "execution_status": "passed",
                    "coverage_status": "covered",
                    "duration_s": 0.1,
                }
            ],
            [],
            0.1,
        )

    monkeypatch.setattr(exec_agent, "_ROOT_DIR", tmp_path)
    monkeypatch.setattr(exec_agent, "SkillPackageLoader", lambda: NoEntryLoader())
    monkeypatch.setattr(exec_agent, "_normalise_execution_result", fake_normalise_execution_result)

    result = await exec_agent.exec_healing_node(
        {
            "product_id": "demo-product",
            "run_id": "run-001",
            "scenarios": [
                {
                    "scenario_id": "SCN-001",
                    "case_ids": ["CASE-001"],
                    "path_id": "PATH-001",
                }
            ],
            "assertion_results": [],
        }
    )

    assert result["reports"][0]["summary"]["passed"] == 1
    assert result["error"] is None


@pytest.mark.asyncio
async def test_exec_healing_node_attaches_side_effect_probe_results(tmp_path, monkeypatch):
    from e2e_agent.agents import exec_agent

    class NoEntryLoader:
        def load_skill(self, _: str) -> object:
            raise FileNotFoundError("skip skill entry")

    monkeypatch.setattr(exec_agent, "_ROOT_DIR", tmp_path)
    monkeypatch.setattr(exec_agent, "SkillPackageLoader", lambda: NoEntryLoader())
    monkeypatch.setattr(exec_agent, "_playwright_python_available", lambda: False)

    result = await exec_agent.exec_healing_node(
        {
            "product_id": "demo-product",
            "run_id": "run-001",
            "scenarios": [],
            "assertion_results": [],
            "side_effect_probe_config": {
                "probes": [
                    {
                        "probe_id": "order-issued",
                        "expect": [{"field": "data.orderStatus", "equals": "issued"}],
                        "evidence_fields": ["data.orderStatus"],
                    },
                    {
                        "probe_id": "payment-query",
                        "expect": [{"field": "code", "equals": 0}],
                    },
                ]
            },
            "side_effect_probe_responses": {
                "order-issued": {"data": {"orderStatus": "issued"}},
            },
            "side_effect_probe_errors": {
                "payment-query": {"type": "permission", "message": "missing backend permission"},
            },
        }
    )

    probe_report = result["reports"][0]["side_effect_probes"]
    assert probe_report["summary"] == {"total": 2, "success": 1, "fail": 0, "na": 1}
    assert probe_report["results"][1]["downgrade_reason"] == "permission: missing backend permission"

    artifact = tmp_path / "products" / "demo-product" / "agent4" / "side-effect-probes.json"
    assert artifact.exists()
    assert json.loads(artifact.read_text(encoding="utf-8"))["summary"]["na"] == 1


@pytest.mark.asyncio
async def test_exec_healing_node_uses_local_side_effect_probe_transport(tmp_path, monkeypatch):
    from e2e_agent.agents import exec_agent

    class NoEntryLoader:
        def load_skill(self, _: str) -> object:
            raise FileNotFoundError("skip skill entry")

    def fake_transport(probes, *, variables=None, timeout_s=5.0):
        assert variables == {"orderId": "O-001"}
        assert probes[0]["probe_id"] == "order-issued"
        return {"responses": {"order-issued": {"data": {"orderStatus": "issued"}}}, "errors": {}}

    monkeypatch.setattr(exec_agent, "_ROOT_DIR", tmp_path)
    monkeypatch.setattr(exec_agent, "SkillPackageLoader", lambda: NoEntryLoader())
    monkeypatch.setattr(exec_agent, "_playwright_python_available", lambda: False)
    monkeypatch.setattr(exec_agent, "execute_local_http_probe_transport", fake_transport)

    result = await exec_agent.exec_healing_node(
        {
            "product_id": "demo-product",
            "run_id": "run-001",
            "scenarios": [],
            "assertion_results": [],
            "side_effect_probe_variables": {"orderId": "O-001"},
            "side_effect_probe_config": {
                "transport": {"type": "local-http"},
                "probes": [
                    {
                        "probe_id": "order-issued",
                        "url": "http://127.0.0.1/orders/{orderId}",
                        "expect": [{"field": "data.orderStatus", "equals": "issued"}],
                    },
                ],
            },
        }
    )

    assert result["reports"][0]["side_effect_probes"]["summary"] == {
        "total": 1,
        "success": 1,
        "fail": 0,
        "na": 0,
    }


@pytest.mark.asyncio
async def test_exec_healing_node_writes_quarantine_report(tmp_path, monkeypatch):
    from e2e_agent.agents import exec_agent

    class NoEntryLoader:
        def load_skill(self, _: str) -> object:
            raise FileNotFoundError("skip skill entry")

    async def fake_normalise_execution_result(product_id, scenario, runner, warnings, root_dir=None):
        return (
            [
                {
                    "case_id": "CASE-001",
                    "path_id": "PATH-ORDER",
                    "status": "failed",
                    "execution_status": "failed",
                    "coverage_status": "covered",
                    "failure_category": "product_bug",
                    "error_message": "order status mismatch",
                }
            ],
            [
                (
                    {
                        "case_id": "CASE-001",
                        "run_id": "run-001",
                        "failure_category": "product_bug",
                        "error_message": "order status mismatch",
                    },
                    "order status mismatch",
                )
            ],
            0.1,
        )

    monkeypatch.setattr(exec_agent, "_ROOT_DIR", tmp_path)
    monkeypatch.setattr(exec_agent, "SkillPackageLoader", lambda: NoEntryLoader())
    monkeypatch.setattr(exec_agent, "_normalise_execution_result", fake_normalise_execution_result)

    result = await exec_agent.exec_healing_node(
        {
            "product_id": "demo-product",
            "run_id": "run-001",
            "scenarios": [{"scenario_id": "SCN-001", "path_id": "PATH-ORDER", "case_ids": ["CASE-001"]}],
            "assertion_results": [],
        }
    )

    assert result["quarantine_report"]["summary"]["blocking"] == 1
    assert result["reports"][0]["quarantine_summary"]["total"] == 1
    assert any(item["artifact_type"] == "quarantine" for item in result["artifact_fingerprints"])
    artifact = tmp_path / "products" / "demo-product" / "agent4" / "quarantine.json"
    assert json.loads(artifact.read_text(encoding="utf-8"))["items"][0]["case_id"] == "CASE-001"


@pytest.mark.asyncio
async def test_exec_healing_node_ignores_invalid_side_effect_probe_transport_maps(tmp_path, monkeypatch):
    from e2e_agent.agents import exec_agent

    class NoEntryLoader:
        def load_skill(self, _: str) -> object:
            raise FileNotFoundError("skip skill entry")

    monkeypatch.setattr(exec_agent, "_ROOT_DIR", tmp_path)
    monkeypatch.setattr(exec_agent, "SkillPackageLoader", lambda: NoEntryLoader())
    monkeypatch.setattr(exec_agent, "_playwright_python_available", lambda: False)

    result = await exec_agent.exec_healing_node(
        {
            "product_id": "demo-product",
            "run_id": "run-001",
            "scenarios": [],
            "assertion_results": [],
            "side_effect_probe_config": {
                "probes": [
                    {
                        "probe_id": "order-issued",
                        "expect": [{"field": "data.orderStatus", "equals": "issued"}],
                    },
                ]
            },
            "side_effect_probe_responses": [{"order-issued": {"data": {"orderStatus": "issued"}}}],
            "side_effect_probe_errors": [{"order-issued": {"type": "permission"}}],
        }
    )

    probe_report = result["reports"][0]["side_effect_probes"]
    assert probe_report["summary"] == {"total": 1, "success": 0, "fail": 1, "na": 0}
    assert probe_report["results"][0]["failures"][0]["field"] == "data.orderStatus"


@pytest.mark.asyncio
async def test_normalise_execution_result_separates_execution_and_coverage(tmp_path):
    from e2e_agent.agents import exec_agent

    scenario = {
        "scenario_id": "SCN-001",
        "path_id": "PATH-001",
        "case_ids": ["TC-001"],
        "spec_path": "missing.spec.ts",
        "root_dir": str(tmp_path),
        "coverage_status": "coverage-gap",
        "target_node": "NODE-confirm",
        "blocked_node": "NODE-confirm",
        "blocked_reason": "Unexplored planned nodes: NODE-confirm",
        "node_progress": [{"node_id": "NODE-confirm", "status": "blocked"}],
        "completion_rule": {"target_node": "NODE-confirm", "is_complete": False},
    }

    results, healing_inputs, duration = await exec_agent._normalise_execution_result(
        "demo-product",
        scenario,
        runner=None,  # type: ignore[arg-type]
        warnings=[],
    )

    assert duration == 0.0
    assert healing_inputs == []
    assert results[0]["execution_status"] == "blocked_by_agent3_contract"
    assert results[0]["coverage_status"] == "blocked"
    assert results[0]["failure_category"] == "agent3_contract_blocked"
    assert results[0]["target_node"] == "NODE-confirm"
    assert exec_agent._build_summary(results)["skipped"] == 1


@pytest.mark.asyncio
async def test_normalise_execution_result_blocks_invalid_agent3_script(tmp_path):
    from e2e_agent.agents import exec_agent

    spec_path = tmp_path / "scenario.spec.ts"
    spec_path.write_text("// invalid script candidate\n", encoding="utf-8")
    scenario = {
        "scenario_id": "SCN-001",
        "path_id": "PATH-001",
        "case_ids": ["TC-001"],
        "spec_path": str(spec_path),
        "root_dir": str(tmp_path),
        "coverage_status": "covered",
        "contract_status": "compiled",
        "script_status": "invalid",
        "script_validation_status": "failed",
        "script_validation": {"status": "failed", "errors": ["SyntaxError"]},
        "completion_rule": {"target_node": "NODE-result", "is_complete": True},
    }

    results, healing_inputs, duration = await exec_agent._normalise_execution_result(
        "demo-product",
        scenario,
        runner=None,  # type: ignore[arg-type]
        warnings=[],
    )

    assert duration == 0.0
    assert healing_inputs == []
    assert results[0]["execution_status"] == "blocked_by_agent3_script"
    assert results[0]["coverage_status"] == "covered"
    assert results[0]["failure_category"] == "agent3_script_blocked"
    assert results[0]["blocked_reason"] == "SyntaxError"


@pytest.mark.asyncio
async def test_normalise_execution_result_uses_visible_chromium_runner(tmp_path, monkeypatch):
    from e2e_agent.agents import exec_agent

    def fake_run(command, cwd, capture_output, text, encoding, errors, timeout):
        payload_path = Path(command[2])
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        assert payload["field_resolution_plan"]["summary"]["verified_required_field_count"] == 1
        assert payload["component_strategy"]["field_strategies"][0]["fill_strategy"] == "fill_text"
        assert payload["validation_report"]["agent4_ready"] is True
        result_path = tmp_path / "products" / "demo-product" / "agent4" / "exec" / "visible-runs" / "run-001" / "SCN-001" / "result.json"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(
            json.dumps(
                {
                    "returncode": 0,
                    "passed": 1,
                    "failed": 0,
                    "errors": [],
                    "browser_actions_path": str(result_path.parent / "browser-actions.jsonl"),
                    "visible_browser": True,
                    "duration_s": 1.2,
                    "scenario_id": payload["scenario_id"],
                    "reached_target_node": "NODE-result",
                    "target_node_status": "reached",
                }
            ),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout="visible ok", stderr="")

    monkeypatch.setattr(exec_agent.subprocess, "run", fake_run)
    monkeypatch.setenv("AGENT4_VISIBLE_BROWSER", "1")

    scenario = {
        "scenario_id": "SCN-001",
        "path_id": "PATH-001",
        "case_ids": ["TC-001"],
        "root_dir": str(tmp_path),
        "run_id": "run-001",
        "entry_url": "https://example.com/detail",
        "real_actions": [{"selector": "#submit-by", "text": "立即投保", "tag": "a"}],
        "completion_rule": {"target_node": "NODE-result", "is_complete": True},
        "field_resolution_plan": {
            "summary": {"verified_required_field_count": 1},
            "fields": [
                {
                    "node_id": "NODE-insure-form",
                    "field_key": "applicant.name",
                    "selected_locator": {"by": "selector", "value": "#applicant-name"},
                    "locator_status": "verified_static",
                    "mock_status": "mapped",
                }
            ],
        },
        "component_strategy": {
            "field_strategies": [
                {
                    "node_id": "NODE-insure-form",
                    "field_key": "applicant.name",
                    "control_type": "input_text",
                    "fill_strategy": "fill_text",
                    "strategy_status": "supported",
                }
            ]
        },
        "validation_report": {"agent4_ready": True, "status": "passed"},
    }

    results, healing_inputs, duration = await exec_agent._normalise_execution_result(
        "demo-product",
        scenario,
        runner=None,  # type: ignore[arg-type]
        warnings=[],
    )

    assert duration == 1.2
    assert healing_inputs == []
    assert results[0]["execution_status"] == "passed"
    assert results[0]["visible_browser"] is True
    assert results[0]["execution_entry"] == "agent4.visible-chromium"
    assert "visible-runs" in results[0]["execution_artifacts_dir"]


@pytest.mark.asyncio
async def test_normalise_execution_result_preserves_sms_boundary_on_visible_timeout(tmp_path, monkeypatch):
    from e2e_agent.agents import exec_agent

    def fake_run(command, cwd, capture_output, text, encoding, errors, timeout):
        payload_path = Path(command[2])
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        result_dir = payload_path.parent
        (result_dir / "browser-actions.jsonl").write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "type": "network-response",
                            "url": "https://cps.example/api/apps/cps/insure/task/approve/insuredSms/verify",
                            "status": 200,
                            "body_excerpt": "{\"code\":-1,\"msg\":\"验证码不正确\",\"success\":false}",
                        },
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        {
                            "type": "action-start",
                            "message": "step 109: Account/session boundary while waiting submit processing",
                            "click_strategy": "account-session-boundary",
                        },
                        ensure_ascii=False,
                    ),
                ]
            ),
            encoding="utf-8",
        )
        raise exec_agent.subprocess.TimeoutExpired(command, timeout, output="visible stdout", stderr="visible stderr")

    monkeypatch.setattr(exec_agent.subprocess, "run", fake_run)
    monkeypatch.setenv("AGENT4_VISIBLE_BROWSER", "1")

    scenario = {
        "scenario_id": "SCN-001",
        "path_id": "PATH-001",
        "case_ids": ["TC-001"],
        "root_dir": str(tmp_path),
        "run_id": "run-timeout",
        "entry_url": "https://example.com/detail",
        "real_actions": [
            {
                "selector": "button",
                "text": "Account/session boundary while waiting submit processing",
                "click_strategy": "account-session-boundary",
                "planned_from_node_id": "NODE-risk-control",
            }
        ],
        "completion_rule": {"target_node": "NODE-policy-result", "is_complete": True},
    }

    results, healing_inputs, duration = await exec_agent._normalise_execution_result(
        "demo-product",
        scenario,
        runner=None,  # type: ignore[arg-type]
        warnings=[],
        root_dir=tmp_path,
    )

    assert duration > 0
    assert results[0]["execution_status"] == "failed"
    assert results[0]["failure_category"] == "test_data"
    assert "insuredSms/verify 验证码不正确" in results[0]["error_message"]
    assert healing_inputs[0][0]["failure_category"] == "test_data"


@pytest.mark.asyncio
async def test_normalise_execution_result_fails_visible_runner_without_target_evidence(tmp_path, monkeypatch):
    from e2e_agent.agents import exec_agent

    def fake_run(command, cwd, capture_output, text, encoding, errors, timeout):
        result_path = tmp_path / "products" / "demo-product" / "agent4" / "exec" / "visible-runs" / "run-002" / "SCN-002" / "result.json"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(
            json.dumps(
                {
                    "returncode": 0,
                    "passed": 1,
                    "failed": 0,
                    "errors": [],
                    "browser_actions_path": str(result_path.parent / "browser-actions.jsonl"),
                    "visible_browser": True,
                    "duration_s": 0.8,
                    "final_url": "https://example.com/detail",
                }
            ),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout="visible ok", stderr="")

    monkeypatch.setattr(exec_agent.subprocess, "run", fake_run)
    monkeypatch.setenv("AGENT4_VISIBLE_BROWSER", "1")

    scenario = {
        "scenario_id": "SCN-002",
        "path_id": "PATH-002",
        "case_ids": ["TC-002"],
        "root_dir": str(tmp_path),
        "run_id": "run-002",
        "entry_url": "https://example.com/detail",
        "real_actions": [],
        "completion_rule": {
            "target_node": "NODE-result",
            "required_nodes": ["NODE-detail", "NODE-result"],
            "is_complete": True,
        },
    }

    results, _, _ = await exec_agent._normalise_execution_result(
        "demo-product",
        scenario,
        runner=None,  # type: ignore[arg-type]
        warnings=[],
    )

    assert results[0]["execution_status"] == "failed"
    assert results[0]["failure_category"] == "script_bug"
    assert results[0]["target_node_status"] == "not_reached"
    assert results[0]["blocked_reason"] == "Agent4 did not prove target node reached: NODE-result"
    assert results[0]["visible_browser"] is True
    assert results[0]["execution_entry"] == "agent4.visible-chromium"


@pytest.mark.asyncio
async def test_normalise_execution_result_attaches_visible_runner_screenshots(tmp_path, monkeypatch):
    from e2e_agent.agents import exec_agent

    def fake_run(command, cwd, capture_output, text, encoding, errors, timeout):
        result_path = tmp_path / "products" / "demo-product" / "agent4" / "exec" / "visible-runs" / "run-003" / "SCN-003" / "result.json"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(
            json.dumps(
                {
                    "returncode": 0,
                    "passed": 1,
                    "failed": 0,
                    "errors": [],
                    "browser_actions_path": str(result_path.parent / "browser-actions.jsonl"),
                    "visible_browser": True,
                    "duration_s": 0.8,
                    "screenshots": [
                        {
                            "label": "initial-page",
                            "step": 0,
                            "path": str(result_path.parent / "screenshots" / "00-initial-page.png"),
                        },
                        {
                            "label": "step-1-after",
                            "step": 1,
                            "path": str(result_path.parent / "screenshots" / "01-step-1-after.png"),
                        },
                    ],
                    "reached_target_node": "NODE-result",
                    "target_node_status": "reached",
                }
            ),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout="visible ok", stderr="")

    monkeypatch.setattr(exec_agent.subprocess, "run", fake_run)
    monkeypatch.setenv("AGENT4_VISIBLE_BROWSER", "1")

    scenario = {
        "scenario_id": "SCN-003",
        "path_id": "PATH-003",
        "case_ids": ["TC-003", "TC-004"],
        "root_dir": str(tmp_path),
        "run_id": "run-003",
        "entry_url": "https://example.com/detail",
        "real_actions": [{"selector": "#submit-by", "text": "立即投保", "tag": "a"}],
        "completion_rule": {"target_node": "NODE-result", "is_complete": True},
    }

    results, _, _ = await exec_agent._normalise_execution_result(
        "demo-product",
        scenario,
        runner=None,  # type: ignore[arg-type]
        warnings=[],
    )

    assert results[0]["screenshots"] == results[1]["screenshots"]
    assert results[0]["screenshots"][0]["label"] == "initial-page"
    assert results[0]["screenshots"][1]["step"] == 1


@pytest.mark.asyncio
async def test_normalise_execution_result_fails_single_action_that_does_not_reach_target(tmp_path, monkeypatch):
    from e2e_agent.agents import exec_agent

    def fake_run(command, cwd, capture_output, text, encoding, errors, timeout):
        result_path = tmp_path / "products" / "demo-product" / "agent4" / "exec" / "visible-runs" / "run-004" / "SCN-004" / "result.json"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(
            json.dumps(
                {
                    "returncode": 0,
                    "passed": 1,
                    "failed": 0,
                    "errors": [],
                    "browser_actions_path": str(result_path.parent / "browser-actions.jsonl"),
                    "visible_browser": True,
                    "duration_s": 0.6,
                    "executed_action_count": 1,
                    "final_url": "https://example.com/detail",
                    "target_node_status": "not_reached",
                }
            ),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout="visible ok", stderr="")

    monkeypatch.setattr(exec_agent.subprocess, "run", fake_run)
    monkeypatch.setenv("AGENT4_VISIBLE_BROWSER", "1")

    scenario = {
        "scenario_id": "SCN-004",
        "path_id": "PATH-004",
        "case_ids": ["TC-004"],
        "root_dir": str(tmp_path),
        "run_id": "run-004",
        "entry_url": "https://example.com/detail",
        "real_actions": [{"selector": "#submit-by", "text": "立即投保", "tag": "a"}],
        "completion_rule": {
            "target_node": "NODE-policy-result",
            "required_nodes": ["NODE-product-detail", "NODE-insure-form", "NODE-policy-result"],
            "is_complete": True,
        },
    }

    results, healing_inputs, duration = await exec_agent._normalise_execution_result(
        "demo-product",
        scenario,
        runner=None,  # type: ignore[arg-type]
        warnings=[],
    )

    assert duration == 0.6
    assert healing_inputs[0][0]["failure_category"] == "script_bug"
    assert results[0]["execution_status"] == "failed"
    assert results[0]["target_node_status"] == "not_reached"
    assert results[0]["reached_target_node"] is None
    assert results[0]["blocked_reason"] == "Agent4 did not prove target node reached: NODE-policy-result"


@pytest.mark.asyncio
async def test_normalise_execution_result_uses_python_fallback_for_static_first_generated_spec(
    tmp_path, monkeypatch
):
    from e2e_agent.agents import exec_agent

    spec_path = tmp_path / "scenario.spec.ts"
    spec_path.write_text("// @generated-by agent3.static-first\n", encoding="utf-8")
    monkeypatch.delenv("AGENT4_VISIBLE_BROWSER", raising=False)

    class NoProjectRuntimeRunner:
        def check_node_available(self) -> bool:
            return True

        def has_project_test_runtime(self) -> bool:
            return False

        def run_formal_spec(self, *_args, **_kwargs) -> dict[str, object]:
            raise AssertionError("generated static-first specs should use Python fallback without project runtime")

    warnings: list[str] = []
    results, healing_inputs, duration = await exec_agent._normalise_execution_result(
        "demo-product",
        {
            "scenario_id": "SCN-001",
            "path_id": "PATH-001",
            "case_ids": ["TC-001"],
            "spec_path": str(spec_path),
            "root_dir": str(tmp_path),
            "completion_rule": {"target_node": "NODE-result", "is_complete": True},
            "route_nodes": ["NODE-start", "NODE-result", "NODE-end"],
            "coverage_status": "covered",
            "script_status": "generated",
            "script_validation_status": "skipped",
        },
        runner=NoProjectRuntimeRunner(),  # type: ignore[arg-type]
        warnings=warnings,
        root_dir=tmp_path,
    )

    assert healing_inputs == []
    assert duration > 0
    assert warnings == ["Used Python fallback for generated scenario: scenario.spec.ts"]
    assert results[0]["execution_status"] == "passed"
    assert results[0]["execution_entry"] == "agent4.playwright-python-fallback"
    assert results[0]["execution_artifacts_dir"] is None
    assert results[0]["screenshots"] == []


@pytest.mark.asyncio
async def test_exec_healing_node_runs_specs_from_artifact_root_dir(tmp_path, monkeypatch):
    from e2e_agent.agents import exec_agent

    product_id = "artifact-root-product"
    relative_spec = (
        "products/"
        f"{product_id}/"
        "agent3/ts-gen/pc/scenarios/generated-artifact-root.spec.ts"
    )
    spec_path = tmp_path / relative_spec
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(
        "// @generated-by mpt-ins-ts-gen\n"
        "import { test } from '@playwright/test';\n"
        "test('generated artifact root scenario', async () => {});\n",
        encoding="utf-8",
    )

    class MissingSkillLoader:
        def load_skill(self, _: str) -> object:
            raise FileNotFoundError("mpt-reg-exec missing")

    runner_roots: list[object] = []
    executed_specs: list[str] = []

    class RecordingRunner:
        def __init__(self, repo_root: object) -> None:
            runner_roots.append(repo_root)

        def check_node_available(self) -> bool:
            return True

        def has_project_test_runtime(self) -> bool:
            return True

        def run_spec(self, spec_path_arg: str) -> dict[str, object]:
            executed_specs.append(spec_path_arg)
            return {
                "returncode": 0,
                "passed": 1,
                "failed": 0,
                "errors": [],
                "raw_output": "",
                "stderr": "",
                "duration_s": 0.1,
            }

    monkeypatch.setattr(exec_agent, "SkillPackageLoader", lambda: MissingSkillLoader())
    monkeypatch.setattr(exec_agent, "PlaywrightTSRunner", RecordingRunner)

    result = await exec_agent.exec_healing_node(
        {
            "product_id": product_id,
            "run_id": "run-001",
            "artifact_root_dir": str(tmp_path),
            "runtime_context": {
                "session_key": f"{product_id}:pc",
                "storage_state_path": str(tmp_path / "missing-storage-state.json"),
            },
            "scenarios": [
                {
                    "scenario_id": "SCN-001",
                    "case_ids": ["CASE-001"],
                    "path_id": "PATH-001",
                    "spec_path": relative_spec,
                }
            ],
        }
    )

    report = result["reports"][0]
    assert report["results"][0]["status"] == "passed"
    assert report["summary"]["skipped"] == 0
    assert runner_roots == [tmp_path]
    assert executed_specs == [str(spec_path)]


@pytest.mark.asyncio
async def test_exec_healing_node_keeps_scenario_spec_when_only_chain_manifest_exists(tmp_path, monkeypatch):
    from e2e_agent.agents import exec_agent

    product_id = "demo-product"
    old_spec = tmp_path / "products" / product_id / "agent3" / "ts-gen" / "pc" / "scenarios" / "old.spec.ts"
    chain_spec = tmp_path / "products" / product_id / "agent3" / "ts-gen" / ".artifacts" / "chain-to-result-pc.spec.ts"
    old_spec.parent.mkdir(parents=True, exist_ok=True)
    chain_spec.parent.mkdir(parents=True, exist_ok=True)
    old_spec.write_text("// old scenario spec\n", encoding="utf-8")
    chain_spec.write_text("// formal chain spec\n", encoding="utf-8")
    (chain_spec.parent / "chain-manifest.json").write_text(
        json.dumps(
            {
                "product_id": product_id,
                "chain_specs": [
                    {
                        "scenario_id": "SCN-001",
                        "path_id": "PATH-001",
                        "relative_path": ".artifacts/chain-to-result-pc.spec.ts",
                        "path": f"products/{product_id}/agent3/ts-gen/.artifacts/chain-to-result-pc.spec.ts",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    class MissingSkillLoader:
        def load_skill(self, _: str) -> object:
            raise FileNotFoundError("mpt-reg-exec missing")

    executed_specs: list[str] = []

    class RecordingRunner:
        def __init__(self, repo_root: object) -> None:
            self.repo_root = repo_root

        def check_node_available(self) -> bool:
            return True

        def has_project_test_runtime(self) -> bool:
            return True

        def run_formal_spec(self, spec_path_arg: str, report_dir: Path) -> dict[str, object]:
            executed_specs.append(spec_path_arg)
            return {
                "returncode": 0,
                "passed": 1,
                "failed": 0,
                "errors": [],
                "raw_output": "1 passed",
                "stderr": "",
                "duration_s": 0.1,
                "execution_entry": "agent4.playwright-formal",
                "formal_execution": True,
                "visible_browser": False,
                "report_dir": str(report_dir),
            }

    monkeypatch.setattr(exec_agent, "SkillPackageLoader", lambda: MissingSkillLoader())
    monkeypatch.setattr(exec_agent, "PlaywrightTSRunner", RecordingRunner)
    monkeypatch.setattr(
        exec_agent,
        "_run_visible_chromium_scenario",
        lambda product_id_arg, scenario_arg: (_ for _ in ()).throw(
            AssertionError("Agent4 must execute the Agent3 spec, not generate a visible script")
        ),
    )

    result = await exec_agent.exec_healing_node(
        {
            "product_id": product_id,
            "run_id": "run-001",
            "artifact_root_dir": str(tmp_path),
            "runtime_context": {
                "session_key": f"{product_id}:pc",
                "storage_state_path": str(tmp_path / "missing-storage-state.json"),
            },
            "scenarios": [
                {
                    "scenario_id": "SCN-001",
                    "case_ids": ["CASE-001"],
                    "path_id": "PATH-001",
                    "spec_path": "pc/scenarios/old.spec.ts",
                    "entry_url": "https://example.com/detail",
                }
            ],
        }
    )

    assert result["reports"][0]["results"][0]["status"] == "passed"
    assert executed_specs == [str(old_spec)]


@pytest.mark.asyncio
async def test_exec_healing_node_uses_agent3_tc_execution_plan_formal_scenario_spec(tmp_path, monkeypatch):
    from e2e_agent.agents import exec_agent

    product_id = "demo-product"
    old_spec = tmp_path / "products" / product_id / "agent3" / "ts-gen" / "pc" / "scenarios" / "old.spec.ts"
    formal_spec = tmp_path / "products" / product_id / "agent3" / "ts-gen" / "pc" / "scenarios" / "01-path-001.spec.ts"
    chain_spec = tmp_path / "products" / product_id / "agent3" / "ts-gen" / ".artifacts" / "chain-to-result-pc.spec.ts"
    old_spec.parent.mkdir(parents=True, exist_ok=True)
    formal_spec.parent.mkdir(parents=True, exist_ok=True)
    chain_spec.parent.mkdir(parents=True, exist_ok=True)
    old_spec.write_text("// old scenario spec\n", encoding="utf-8")
    formal_spec.write_text("// formal scenario spec\n", encoding="utf-8")
    chain_spec.write_text("// formal chain spec\n", encoding="utf-8")
    (tmp_path / "products" / product_id / "agent3" / "ts-gen" / "tc-execution-plan.json").write_text(
        json.dumps(
            {
                "product_id": product_id,
                "scenarios": [
                    {
                        "scenario_id": "SCN-001",
                        "path_id": "PATH-001",
                        "spec_path": "pc/scenarios/01-path-001.spec.ts",
                        "chain_spec_path": ".artifacts/chain-to-result-pc.spec.ts",
                        "execution_requirements": {
                            "mock_user_required": True,
                            "policy_start_offset_days": 1,
                            "product_plan": "全球探索计划",
                        },
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    class MissingSkillLoader:
        def load_skill(self, _: str) -> object:
            raise FileNotFoundError("mpt-reg-exec missing")

    executed_specs: list[str] = []

    class RecordingRunner:
        def __init__(self, repo_root: object) -> None:
            self.repo_root = repo_root

        def check_node_available(self) -> bool:
            return True

        def has_project_test_runtime(self) -> bool:
            return True

        def run_formal_spec(self, spec_path_arg: str, report_dir: Path) -> dict[str, object]:
            executed_specs.append(spec_path_arg)
            return {
                "returncode": 0,
                "passed": 1,
                "failed": 0,
                "errors": [],
                "raw_output": "1 passed",
                "stderr": "",
                "duration_s": 0.1,
                "execution_entry": "agent4.playwright-formal",
                "formal_execution": True,
                "visible_browser": False,
                "report_dir": str(report_dir),
            }

    monkeypatch.setattr(exec_agent, "SkillPackageLoader", lambda: MissingSkillLoader())
    monkeypatch.setattr(exec_agent, "PlaywrightTSRunner", RecordingRunner)
    monkeypatch.setattr(
        exec_agent,
        "_run_visible_chromium_scenario",
        lambda product_id_arg, scenario_arg: (_ for _ in ()).throw(
            AssertionError("Agent4 must execute the current-run Agent3 spec, not generate a visible script")
        ),
    )

    result = await exec_agent.exec_healing_node(
        {
            "product_id": product_id,
            "run_id": "run-001",
            "artifact_root_dir": str(tmp_path),
            "runtime_context": {
                "session_key": f"{product_id}:pc",
                "storage_state_path": str(tmp_path / "missing-storage-state.json"),
            },
            "scenarios": [
                {
                    "scenario_id": "SCN-001",
                    "case_ids": ["CASE-001"],
                    "path_id": "PATH-001",
                    "spec_path": "pc/scenarios/old.spec.ts",
                    "entry_url": "https://example.com/detail",
                }
            ],
        }
    )

    assert result["reports"][0]["results"][0]["status"] == "passed"
    assert result["reports"][0]["results"][0]["spec_path"] == str(formal_spec)
    assert result["reports"][0]["results"][0]["execution_requirements"] == {
        "mock_user_required": True,
        "policy_start_offset_days": 1,
        "product_plan": "全球探索计划",
    }
    assert executed_specs == [str(formal_spec)]


@pytest.mark.asyncio
async def test_exec_healing_node_prefers_current_run_agent3_spec_over_stale_canonical_copy(
    tmp_path, monkeypatch
):
    from e2e_agent.agents import exec_agent

    product_id = "demo-product"
    stale_root = tmp_path / "products" / product_id / "ts-gen"
    assets_root = tmp_path / "products" / product_id / "demo.assets"
    current_run_root = assets_root / "agent3" / "ts-gen"
    run_dir = assets_root / "runs" / "run-001"
    stale_spec = stale_root / "pc" / "scenarios" / "01-path-001.spec.ts"
    current_run_spec = current_run_root / "pc" / "scenarios" / "01-path-001.spec.ts"
    old_spec = stale_root / "pc" / "scenarios" / "old.spec.ts"
    for path in (stale_spec, current_run_spec, old_spec):
        path.parent.mkdir(parents=True, exist_ok=True)
    run_dir.mkdir(parents=True, exist_ok=True)
    stale_spec.write_text("// stale canonical scenario spec\n", encoding="utf-8")
    current_run_spec.write_text("// current run scenario spec\n", encoding="utf-8")
    old_spec.write_text("// old scenario spec\n", encoding="utf-8")
    plan = {
        "product_id": product_id,
        "scenarios": [
            {
                "scenario_id": "SCN-001",
                "path_id": "PATH-001",
                "spec_path": "pc/scenarios/01-path-001.spec.ts",
            }
        ],
    }
    (stale_root / "tc-execution-plan.json").write_text(json.dumps(plan), encoding="utf-8")
    (current_run_root / "tc-execution-plan.json").parent.mkdir(parents=True, exist_ok=True)
    (current_run_root / "tc-execution-plan.json").write_text(json.dumps(plan), encoding="utf-8")

    class MissingSkillLoader:
        def load_skill(self, _: str) -> object:
            raise FileNotFoundError("mpt-reg-exec missing")

    executed_specs: list[str] = []

    class RecordingRunner:
        def __init__(self, repo_root: object) -> None:
            self.repo_root = repo_root

        def check_node_available(self) -> bool:
            return True

        def has_project_test_runtime(self) -> bool:
            return True

        def run_formal_spec(self, spec_path_arg: str, report_dir: Path) -> dict[str, object]:
            executed_specs.append(spec_path_arg)
            return {
                "returncode": 0,
                "passed": 1,
                "failed": 0,
                "errors": [],
                "raw_output": "1 passed",
                "stderr": "",
                "duration_s": 0.1,
                "execution_entry": "agent4.playwright-formal",
                "formal_execution": True,
                "visible_browser": False,
                "report_dir": str(report_dir),
            }

    monkeypatch.setattr(exec_agent, "SkillPackageLoader", lambda: MissingSkillLoader())
    monkeypatch.setattr(exec_agent, "PlaywrightTSRunner", RecordingRunner)
    monkeypatch.setattr(
        exec_agent,
        "_run_visible_chromium_scenario",
        lambda product_id_arg, scenario_arg: (_ for _ in ()).throw(
            AssertionError("Agent4 must execute the current-run Agent3 spec, not generate a visible script")
        ),
    )

    result = await exec_agent.exec_healing_node(
        {
            "product_id": product_id,
            "run_id": "run-001",
            "artifact_root_dir": str(tmp_path),
            "run_dir": str(run_dir),
            "runtime_context": {
                "session_key": f"{product_id}:pc",
                "storage_state_path": str(tmp_path / "missing-storage-state.json"),
            },
            "scenarios": [
                {
                    "scenario_id": "SCN-001",
                    "case_ids": ["CASE-001"],
                    "path_id": "PATH-001",
                    "spec_path": "pc/scenarios/old.spec.ts",
                    "entry_url": "https://example.com/detail",
                }
            ],
        }
    )

    assert result["reports"][0]["results"][0]["status"] == "passed"
    assert result["reports"][0]["results"][0]["spec_path"] == str(current_run_spec)
    assert executed_specs == [str(current_run_spec)]


@pytest.mark.asyncio
async def test_mpt_reg_exec_entry_keeps_scenario_spec_when_only_chain_manifest_exists(tmp_path, monkeypatch):
    script_path = Path("src/e2e_agent/skills/mpt-reg-exec/scripts/run_exec.py").resolve()
    spec = importlib.util.spec_from_file_location("mpt_reg_exec_run_exec_test", script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    product_id = "demo-product"
    old_spec = tmp_path / "products" / product_id / "agent3" / "ts-gen" / "pc" / "scenarios" / "old.spec.ts"
    chain_spec = tmp_path / "products" / product_id / "agent3" / "ts-gen" / ".artifacts" / "chain-to-result-pc.spec.ts"
    old_spec.parent.mkdir(parents=True, exist_ok=True)
    chain_spec.parent.mkdir(parents=True, exist_ok=True)
    old_spec.write_text("// old scenario spec\n", encoding="utf-8")
    chain_spec.write_text("// formal chain spec\n", encoding="utf-8")
    (chain_spec.parent / "chain-manifest.json").write_text(
        json.dumps(
            {
                "product_id": product_id,
                "chain_specs": [
                    {
                        "scenario_id": "SCN-001",
                        "path_id": "PATH-001",
                        "relative_path": ".artifacts/chain-to-result-pc.spec.ts",
                        "path": f"products/{product_id}/agent3/ts-gen/.artifacts/chain-to-result-pc.spec.ts",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    class RecordingRunner:
        def __init__(self, repo_root: object) -> None:
            self.repo_root = repo_root

        def check_node_available(self) -> bool:
            return True

    executed_specs: list[str] = []

    async def fake_normalise_execution_result(*, product_id, scenario, runner, warnings, root_dir=None):
        executed_specs.append(str(scenario.get("spec_path")))
        return (
            [
                {
                    "case_id": "CASE-001",
                    "status": "passed",
                    "coverage_status": "covered",
                    "duration_s": 0.1,
                }
            ],
            [],
            0.1,
        )

    monkeypatch.setattr(module, "PlaywrightTSRunner", RecordingRunner)
    monkeypatch.setattr(module, "_normalise_execution_result", fake_normalise_execution_result)

    result = await module._run(
        {
            "product_id": product_id,
            "run_id": "run-001",
            "root_dir": str(tmp_path),
            "scenarios": [
                {
                    "scenario_id": "SCN-001",
                    "case_ids": ["CASE-001"],
                    "path_id": "PATH-001",
                    "spec_path": "pc/scenarios/old.spec.ts",
                }
            ],
        }
    )

    assert result["reports"][0]["summary"]["passed"] == 1
    assert executed_specs == ["pc/scenarios/old.spec.ts"]


@pytest.mark.asyncio
async def test_exec_healing_node_returns_error_on_unexpected_exception(monkeypatch):
    from e2e_agent.agents import exec_agent

    class ExplodingLoader:
        def load_skill(self, _: str) -> object:
            raise RuntimeError("loader exploded")

    monkeypatch.setattr(exec_agent, "SkillPackageLoader", lambda: ExplodingLoader())

    result = await exec_agent.exec_healing_node({"product_id": "demo-product"})

    assert "exec_healing failed: loader exploded" == result["error"]


def test_finalize_runtime_context_ignores_empty_storage_state_path(tmp_path):
    from e2e_agent.core.runtime_context import finalize_runtime_context

    report = finalize_runtime_context(
        root_dir=tmp_path,
        product_id="demo-product",
        runtime_context={},
        reason="exec_complete",
    )

    assert report["released"] is True
    assert report["removed_paths"] == []
    assert (tmp_path / "products" / "demo-product" / "agent3" / "runtime" / "teardown-report.json").exists()

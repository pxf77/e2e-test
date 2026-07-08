from __future__ import annotations

import json

import pytest


def _gene_value(payload: dict, key: str) -> str:
    for gene in payload["genes"]:
        if gene.get("key") == key:
            return str(gene.get("value") or "")
    raise AssertionError(f"missing gene {key}")


def test_health_answer_save_payload_uses_mock_identity_for_trial_genes():
    from e2e_agent.core.page_exploration import _patch_health_notify_payload

    mock_data = {
        "applicant.birthdate": "1992-05-20",
        "applicant.id_no": "110105199205208192",
    }
    restrict = {
        "baseProductId": 107345,
        "baseProductPlanId": 114699,
        "genes": [
            {"key": "insurantDate", "value": "2025-02-26"},
            {"key": "sex", "value": "男"},
            {"key": "premium", "value": "10000"},
        ],
    }
    raw = json.dumps({"restrictReqParamStr": json.dumps(restrict, ensure_ascii=False)}, ensure_ascii=False)

    patched, changed = _patch_health_notify_payload(raw, mock_data)

    assert changed is True
    patched_restrict = json.loads(json.loads(patched)["restrictReqParamStr"])
    assert _gene_value(patched_restrict, "insurantDate") == "1992-05-20"
    assert _gene_value(patched_restrict, "sex") == "男"


def test_health_query_payload_preserves_top_level_trial_bands():
    from e2e_agent.core.page_exploration import _patch_health_notify_payload

    mock_data = {
        "applicant.birthdate": "1995-02-18",
        "applicant.id_no": "630103199502180519",
    }
    raw = json.dumps(
        {
            "genes": [
                {"key": "insurantDate", "value": "2025-02-26"},
                {"key": "sex", "value": "男"},
            ]
        },
        ensure_ascii=False,
    )

    patched, changed = _patch_health_notify_payload(raw, mock_data)

    assert changed is False
    patched_payload = json.loads(patched)
    assert _gene_value(patched_payload, "insurantDate") == "2025-02-26"
    assert _gene_value(patched_payload, "sex") == "男"


def test_trial_payload_uses_form_identity_and_preserves_cert_flag():
    from e2e_agent.core.page_exploration import _patch_trial_genes_payload

    raw = json.dumps(
        {
            "ignoreCertTask": False,
            "data": {
                "10": [{"birthdate": "1992-05-20", "sex": "1"}],
                "20": [{"birthdate": "1992-05-20", "sex": "1"}],
            },
            "trialGenes": json.dumps(
                {
                    "genes": [
                        {"key": "insurantDate", "value": "2025-02-26"},
                        {"key": "sex", "value": "男"},
                    ]
                },
                ensure_ascii=False,
            ),
        },
        ensure_ascii=False,
    )

    patched, changed = _patch_trial_genes_payload(raw, {})

    assert changed is True
    patched_payload = json.loads(patched)
    assert patched_payload["ignoreCertTask"] is False
    patched_trial = json.loads(patched_payload["trialGenes"])
    assert _gene_value(patched_trial, "insurantDate") == "1992-05-20"
    assert _gene_value(patched_trial, "sex") == "男"


def test_generate_link_payload_uses_mock_identity_for_trial_genes():
    from e2e_agent.core.page_exploration import _patch_trial_genes_payload

    raw = json.dumps(
        {
            "trialGenes": json.dumps(
                {
                    "genes": [
                        {"key": "insurantDate", "value": "2025-02-26"},
                        {"key": "sex", "value": "男"},
                    ]
                },
                ensure_ascii=False,
            )
        },
        ensure_ascii=False,
    )
    mock_data = {
        "applicant.birthdate": "1995-02-18",
        "applicant.id_no": "630103199502180519",
    }

    patched, changed = _patch_trial_genes_payload(raw, mock_data)

    assert changed is True
    patched_trial = json.loads(json.loads(patched)["trialGenes"])
    assert _gene_value(patched_trial, "insurantDate") == "1995-02-18"
    assert _gene_value(patched_trial, "sex") == "男"


def test_travel_payload_patch_fills_policy_date_purpose_and_destination():
    from e2e_agent.core.page_exploration import _patch_travel_form_payload

    raw = json.dumps(
        {
            "data": {
                "40": [{"travelNo": "", "purpose": "", "tripDestination": ""}],
                "102": [{"insuranceDate": ""}],
            }
        },
        ensure_ascii=False,
    )
    mock_data = {"policy.start_date": "2026-06-06"}

    patched, changed = _patch_travel_form_payload(raw, mock_data)

    assert changed is True
    payload = json.loads(patched)
    assert payload["data"]["102"][0]["insuranceDate"] == "2026-06-06"
    assert payload["data"]["40"][0]["purpose"] == "1"
    assert payload["data"]["40"][0]["tripDestination"] == "中国澳门"


def test_travel_payload_patch_uses_trial_start_date_when_mock_date_missing():
    from e2e_agent.core.page_exploration import _patch_travel_form_payload

    raw = json.dumps(
        {
            "trialStartDate": "2026-06-06",
            "data": {
                "40": [{"purpose": "", "tripDestination": ""}],
                "102": [{"insuranceDate": ""}],
            },
        },
        ensure_ascii=False,
    )

    patched, changed = _patch_travel_form_payload(raw, {})

    assert changed is True
    payload = json.loads(patched)
    assert payload["data"]["102"][0]["insuranceDate"] == "2026-06-06"


def test_hidden_trial_genes_patch_is_opt_in(monkeypatch):
    from e2e_agent.core.page_exploration import _should_patch_hidden_trial_genes

    monkeypatch.delenv("AGENT3_PATCH_TRIAL_GENES", raising=False)
    assert _should_patch_hidden_trial_genes() is False

    monkeypatch.setenv("AGENT3_PATCH_TRIAL_GENES", "1")
    assert _should_patch_hidden_trial_genes() is True


def test_health_notify_empty_fallback_is_opt_in(monkeypatch):
    from e2e_agent.core.page_exploration import _should_mock_health_notify_failure

    monkeypatch.delenv("AGENT3_MOCK_HEALTH_NOTIFY_FAILURE", raising=False)
    assert _should_mock_health_notify_failure() is False

    monkeypatch.setenv("AGENT3_MOCK_HEALTH_NOTIFY_FAILURE", "1")
    assert _should_mock_health_notify_failure() is True


def test_health_notify_empty_fallback_response_matches_success_contract():
    from e2e_agent.core.page_exploration import _empty_health_notify_success_response

    payload = _empty_health_notify_success_response("产品健康告知")

    assert payload["code"] == 0
    assert payload["success"] is True
    assert payload["data"]["needFillIn"] == 0
    assert payload["data"]["healthyModuleVos"] == []
    assert payload["data"]["title"] == "产品健康告知"


def test_submit_payload_can_skip_renewal_bank_sign():
    from e2e_agent.core.page_exploration import _patch_submit_skip_renewal_bank

    raw = json.dumps(
        {
            "autoRenewal": True,
            "renewalCheck": 1,
            "data": {
                "10": [{"cName": "A"}],
                "107": [{"bank": "1", "cardOwner": "A", "payAccount": "6222021234567890123"}],
            },
        },
        ensure_ascii=False,
    )

    patched, changed = _patch_submit_skip_renewal_bank(raw)

    assert changed is True
    payload = json.loads(patched)
    assert payload["autoRenewal"] is False
    assert payload["renewalCheck"] == 0
    assert "107" not in payload["data"]
    assert payload["data"]["10"] == [{"cName": "A"}]


def test_empty_bank_card_validation_url_uses_mock_pay_account():
    from e2e_agent.core.page_exploration import _patch_bank_card_validation_url

    patched, changed = _patch_bank_card_validation_url(
        "https://example.test/api/apps/cps/product/insure/card/valid?cardNum=&md=1",
        {"payAccount_107": "6217002352508817050"},
    )

    assert changed is True
    assert "cardNum=6217002352508817050" in patched
    assert "md=1" in patched


def test_bank_card_validation_url_preserves_existing_card_num():
    from e2e_agent.core.page_exploration import _patch_bank_card_validation_url

    original = "https://example.test/api/apps/cps/product/insure/card/valid?cardNum=6222021234567890123&md=1"
    patched, changed = _patch_bank_card_validation_url(original, {"payAccount_107": "6217002352508817050"})

    assert changed is False
    assert patched == original


def test_empty_bank_card_validation_payload_uses_mock_pay_account():
    from e2e_agent.core.page_exploration import _patch_bank_card_validation_payload

    raw = json.dumps({"cardNum": "", "platform": 2, "source": 2}, ensure_ascii=False)

    patched, changed = _patch_bank_card_validation_payload(raw, {"payAccount_107": "6217002352508817050"})

    assert changed is True
    payload = json.loads(patched)
    assert payload["cardNum"] == "6217002352508817050"
    assert payload["platform"] == 2


def test_bank_card_validation_payload_ignores_invalid_mock_pay_account():
    from e2e_agent.core.page_exploration import _patch_bank_card_validation_payload

    raw = json.dumps({"cardNum": "", "platform": 2}, ensure_ascii=False)

    patched, changed = _patch_bank_card_validation_payload(raw, {"payAccount_107": ""})

    assert changed is False
    assert patched == raw


def test_region_sync_preserves_redux_field_object_shape():
    import inspect

    from e2e_agent.core.page_exploration import _sync_region_mock_data_to_page

    source = inspect.getsource(_sync_region_mock_data_to_page)

    assert "row.provCityText = regionValue;" not in source
    assert "next = next.setIn([...base, moduleId, 0, 'provCityText'], regionValue);" not in source
    assert "next = next.setIn([...path, 'value'], regionValue);" in source


def test_minimal_form_data_handles_english_name_fields():
    import inspect

    from e2e_agent.core.page_exploration import _apply_minimal_form_data

    source = inspect.getsource(_apply_minimal_form_data)

    assert "const englishNameFor" in source
    assert "eName|english|pinyin" in source
    assert "syncEnglishNameState" in source
    assert "'eName', 'value'" in source
    assert "break;" in source
    assert "isEnglishNameField(el, label)" in source


def test_h5_picker_defaults_handle_travel_selectors():
    import inspect

    from e2e_agent.core.page_exploration import _apply_h5_select_defaults

    source = inspect.getsource(_apply_h5_select_defaults)

    assert "selectTravelPurpose" in source
    assert "selectTravelDestination" in source
    assert "setTravelDestinationDirect" in source
    assert "typeof node.doSubmit === 'function'" in source
    assert "node.doSubmit([destination])" in source
    assert "setBusinessFieldDirect(row, '出行目的', 'purpose', '1')" in source
    assert "setBusinessFieldDirect(row, '出行目的地', 'tripDestination', '中国澳门')" in source
    assert "出行目的" in source
    assert "出行目的地" in source


def test_h5_picker_defaults_handle_policy_start_date():
    import inspect

    from e2e_agent.core.page_exploration import _apply_h5_select_defaults

    source = inspect.getsource(_apply_h5_select_defaults)

    assert "selectPolicyStartDate" in source
    assert "setDatePickerDirect" in source
    assert "datePickerSources" in source
    assert "row.querySelectorAll('.am-list-item,.insure-filed-wrapper,.picker-input')" in source
    assert "new Date(Number(parts[1]), Number(parts[2]) - 1, Number(parts[3]))" in source
    assert "typeof props.onOk === 'function'" in source
    assert "setBusinessFieldDirect(row, '起保日期', 'insuranceDate', startDate)" in source
    assert "props?.currentAttr?.keyCode === keyCode" in source
    assert "props.onChange({ mid, index, keyCode, value: nextValue })" in source
    assert "const moduleIdForKey = code =>" in source
    assert "if (code === 'insuranceDate') return '102';" in source
    assert "if (code === 'purpose' || code === 'tripDestination') return '40';" in source
    assert "patchBusinessState();" in source
    assert "const startDate = normalizePolicyStartDate(mockData['policy.start_date'] || mockData.policyStartDate || '');" in source
    assert "if (parsed && parsed.getTime() === fallbackDate.getTime()) return fallbackText;" in source
    assert "policy.start_date" in source
    assert "起保日期" in source
    assert "rowLooksLikeField" in source
    assert "text.startsWith(label) && /请选择|请重新选择|>$/.test(text)" in source
    assert "rowNeedsValue(row, '出行目的地')" in source
    assert "if (text === label) return true" in source
    assert "fieldRowsMatching(/^出行目的(?!地)/)" in source
    assert "fieldRowsMatching(/^出行目的地|目的地$/)" in source
    assert "await selectPolicyStartDate();" in source
    assert source.index("await selectPolicyStartDate();") < source.index("await selectTravelPurpose();")


def test_minimal_form_data_dismisses_unfinished_policy_dialog_before_fill():
    import inspect

    from e2e_agent.core.page_exploration import _apply_minimal_form_data

    source = inspect.getsource(_apply_minimal_form_data)

    assert "unfinished_dialog = await _dismiss_unfinished_policy_dialog(page)" in source
    assert source.index("unfinished_dialog = await _dismiss_unfinished_policy_dialog(page)") < source.index("pre_filled = await _apply_placeholder_form_data")


def test_pay_account_skip_writes_does_not_lock_empty_account_field():
    import inspect

    from e2e_agent.core.page_exploration import _mark_pay_account_skip_writes

    source = inspect.getsource(_mark_pay_account_skip_writes)

    assert "window.__agent3SkipPayAccountWrites = true" in source
    assert "window.__agent3PayAccountLocked = true" not in source


def test_submit_click_repairs_visible_pay_account_before_business_wait():
    import inspect

    from e2e_agent.core.page_exploration import _click_primary_action

    source = inspect.getsource(_click_primary_action)

    assert "final_pay_account_filled = await _ensure_visible_pay_account_value(page, final_mock_data)" in source
    submit_branch = source.index("final_mock_data = await _load_agent3_mock_data_for_page(page)")
    assert source.index(
        "final_pay_account_filled = await _ensure_visible_pay_account_value(page, final_mock_data)",
        submit_branch,
    ) < source.index("await _wait_for_business_ready(page)", submit_branch)


def test_visible_pay_account_repair_does_not_treat_card_owner_as_account():
    import inspect

    from e2e_agent.core.page_exploration import _ensure_visible_pay_account_value

    source = inspect.getsource(_ensure_visible_pay_account_value)

    assert "账户名|持卡人" in source
    assert "return accountLike && !ownerOnly" in source


def test_visible_pay_account_repair_allows_certificate_consistency_placeholder():
    import inspect

    from e2e_agent.core.page_exploration import _ensure_visible_pay_account_value

    source = inspect.getsource(_ensure_visible_pay_account_value)

    assert "nonAccountIdentityField" in source
    assert "证件号码|身份证" in source
    assert "开卡信息|账号|储蓄卡" in source


@pytest.mark.asyncio
async def test_visible_pay_account_repair_reports_no_action_when_value_already_present():
    from e2e_agent.core.page_exploration import _ensure_visible_pay_account_value

    class FakePage:
        url = "https://example.test/product/insure"

        async def evaluate(self, *_args, **_kwargs):
            return {"changed": 1, "domChanged": 0, "stateChanged": 1, "inputCount": 1}

    actions = await _ensure_visible_pay_account_value(
        FakePage(),
        {"payAccount_107": "6217002352508817050"},
    )

    assert actions == []


def test_minimal_form_data_runs_h5_select_defaults_after_state_repairs():
    import inspect

    from e2e_agent.core.page_exploration import _apply_minimal_form_data

    source = inspect.getsource(_apply_minimal_form_data)

    assert "terminal_select_filled = await _apply_h5_select_defaults(page, mock_data)" in source
    assert source.index("trial_genes_filled = await _sync_trial_genes_from_mock_data(page, mock_data)") < source.index(
        "terminal_select_filled = await _apply_h5_select_defaults(page, mock_data)"
    )
    assert source.index("sensitive_state = await _capture_sensitive_form_state(page)") < source.index(
        "terminal_select_filled = await _apply_h5_select_defaults(page, mock_data)"
    )
    assert "post_sms_select_filled = await _apply_h5_select_defaults(page, mock_data)" in source
    assert source.index("actions.extend(await _force_fill_sms_captcha(page))") < source.index(
        "post_sms_select_filled = await _apply_h5_select_defaults(page, mock_data)"
    )


def test_page_mock_data_loader_prefers_window_mock_data():
    import inspect

    from e2e_agent.core.page_exploration import _load_agent3_mock_data_for_page

    source = inspect.getsource(_load_agent3_mock_data_for_page)

    assert "window.__agent3MockData" in source
    assert "return dict(mock_data)" in source
    assert "_load_agent3_mock_data_from_env()" in source


def test_blocking_overlay_dismiss_does_not_click_agreement_text_outside_modal():
    import inspect

    from e2e_agent.core.page_exploration import _dismiss_blocking_overlays

    source = inspect.getsource(_dismiss_blocking_overlays)

    assert "if not await item.locator(\"xpath=ancestor::*[contains(@class,'am-modal') or contains(@class,'adm-modal') or @role='dialog'][1]\").count():" in source


def test_h5_insure_form_data_clicks_hidden_agreement_labels():
    import inspect

    from e2e_agent.core.page_exploration import _apply_h5_insure_form_data

    source = inspect.getsource(_apply_h5_insure_form_data)

    assert "clickAgreementLabelControls" in source
    assert "本人充分阅读" in source
    assert "input[type=\"checkbox\"]" in source
    assert "label,.am-checkbox-wrapper,.adm-checkbox,.agreement,.protocol,div,span" not in source


def test_protocol_list_agreements_are_not_reclicked_after_confirm():
    import inspect

    from e2e_agent.core.page_exploration import _click_protocol_list_agreements

    source = inspect.getsource(_click_protocol_list_agreements)

    assert "__e2eAgentConfirmedProtocolListGroups" in source
    assert "protocol-list-already-confirmed" in source
    assert "window.__e2eAgentConfirmedProtocolListGroups[groupId] = true" in source


def test_auth_id_card_images_are_generated_from_current_mock(monkeypatch, tmp_path):
    pytest.importorskip("PIL")
    from e2e_agent.core.page_exploration import _resolve_id_card_image_paths

    monkeypatch.setenv("AGENT3_ID_CARD_ASSET_DIR", str(tmp_path))
    monkeypatch.delenv("AGENT3_ID_CARD_IMAGE_PATH", raising=False)
    monkeypatch.delenv("AGENT3_ID_CARD_FRONT_IMAGE_PATH", raising=False)
    monkeypatch.delenv("AGENT3_ID_CARD_BACK_IMAGE_PATH", raising=False)

    paths = _resolve_id_card_image_paths(
        {
            "applicant.name": "杨浩远",
            "applicant.id_no": "110105199008192259",
            "applicant.gender": "男",
            "applicant.birthdate": "1990-08-19",
            "applicant.certificate_address": "北京市朝阳区和平街道和平路541号华府花园5单元2234室",
            "applicant.certificate_validity_text": "2021.05.16-2041.05.16",
            "policy_tool.record.签发机关": "朝阳区公安局",
        }
    )

    assert len(paths) >= 2
    assert paths[0].name == "id-card-front.jpg"
    assert paths[1].name == "id-card-back.jpg"
    assert paths[0].exists()
    assert paths[1].exists()
    assert paths[0].read_bytes() != paths[1].read_bytes()
    assert paths[0].stat().st_size > 10_000
    assert paths[1].stat().st_size > 10_000


class _FakeBodyLocator:
    def __init__(self, text="提示 有未完成的投保单，是否继续投保? 取消 确定"):
        self.text = text

    async def inner_text(self, timeout=None):
        return self.text


class _FakeFallbackLocator:
    @property
    def last(self):
        return self

    def filter(self, **kwargs):
        return self

    async def is_visible(self, timeout=None):
        return False


class _FakeUnfinishedPolicyPage:
    url = "https://commerce.example.test/m/apps/cps/product/insure"
    body_text = "提示 有未完成的投保单，是否继续投保? 取消 确定"
    expected_mode = "unfinished_policy"

    def __init__(self):
        self.clicked = None

    def locator(self, selector):
        if selector == "body":
            return _FakeBodyLocator(self.body_text)
        return _FakeFallbackLocator()

    async def evaluate(self, script, *args):
        assert args == (self.expected_mode,)
        if self.expected_mode == "existing_order_view":
            self.clicked = "查看"
            return "查看"
        if "item.text === '确定'" in script or "item.text === '继续投保'" in script:
            self.clicked = "确定"
            return "确定"
        self.clicked = "取消"
        return "取消"

    async def wait_for_timeout(self, timeout):
        return None


@pytest.mark.asyncio
async def test_unfinished_policy_dialog_continues_existing_order():
    from e2e_agent.core.page_exploration import _dismiss_unfinished_policy_dialog

    page = _FakeUnfinishedPolicyPage()

    result = await _dismiss_unfinished_policy_dialog(page)

    assert page.clicked == "确定"
    assert result is not None
    assert result["selector"] == "unfinished-policy-dialog-continue"
    assert result["action_type"] == "unfinished_policy_continue"


class _FakeExistingOrderPage(_FakeUnfinishedPolicyPage):
    body_text = "提示 订单已存在，您是否需要再次提交? 查看 提交"
    expected_mode = "existing_order_view"


@pytest.mark.asyncio
async def test_existing_order_dialog_opens_existing_order_instead_of_resubmitting():
    from e2e_agent.core.page_exploration import _dismiss_unfinished_policy_dialog

    page = _FakeExistingOrderPage()

    result = await _dismiss_unfinished_policy_dialog(page)

    assert page.clicked == "查看"
    assert result is not None
    assert result["selector"] == "existing-order-dialog-view"
    assert result["action_type"] == "existing_order_view"


class _FakeMockDataInstallPage:
    def __init__(self):
        self.init_scripts: list[str] = []
        self.evaluated_scripts: list[str] = []

    async def add_init_script(self, script):
        self.init_scripts.append(script)

    async def evaluate(self, script):
        self.evaluated_scripts.append(script)


@pytest.mark.asyncio
async def test_agent3_live_installs_state_mock_data_for_browser_pages():
    from e2e_agent.core.page_exploration import _install_agent3_mock_data

    page = _FakeMockDataInstallPage()

    await _install_agent3_mock_data(page, {"payAccount_107": "6222021961126711862"})

    assert page.init_scripts
    assert page.evaluated_scripts
    assert "window.__agent3MockDataSource = 'state.mock_data'" in page.init_scripts[0]
    assert "6222021961126711862" in page.init_scripts[0]

from __future__ import annotations

from datetime import date
import json
from pathlib import Path


def _id_card_checksum_ok(id_no: str) -> bool:
    factors = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
    codes = "10X98765432"
    if len(id_no) != 18:
        return False
    total = sum(int(id_no[index]) * factors[index] for index in range(17))
    return codes[total % 11] == id_no[-1].upper()


def _write_policy_tool_fixture(path: Path) -> Path:
    path.write_text(
        """
const BANKS = {"ICBC":{"name":"中国工商银行","code":"102","prefix":"620058","length":19}};
const MOBILE_PREFIXES = ["138"];
const EMAIL_DOMAINS = ["example.test"];
const SURNAMES = ["赵"];
const GIVEN_NAME_CHARS = "雨欣";
const ADDRESS_STREETS = ["和平街道"];
const ADDRESS_ROADS = ["人民路"];
const ADDRESS_COMMUNITIES = ["阳光"];
const ADDRESS_SUFFIXES = ["小区"];
const NATIONS = ["汉"];
const AREA_TREE = [{"code":"110000","name":"北京市","cities":[{"code":"110100","name":"北京市","districts":[{"code":"110105","name":"朝阳区"}]}]}];
let BANK_CARD_TEMPLATES = {};
""".strip(),
        encoding="utf-8",
    )
    return path


def test_policy_info_generator_maps_policy_tool_rules_to_insure_form_mock_data():
    from e2e_agent.core.policy_info_generator import generate_policy_mock_data

    fields = [
        {"field_key": "insure_form.applicantname", "label": "applicantName"},
        {"field_key": "insure_form.applicantidno", "label": "applicantIdNo"},
        {"field_key": "insure_form.applicantphone", "label": "applicantPhone"},
        {"field_key": "insure_form.applicantemail", "label": "applicantEmail"},
        {"field_key": "insure_form.insuredname", "label": "insuredName"},
        {"field_key": "insure_form.insuredidno", "label": "insuredIdNo"},
        {"field_key": "insure_form.insuredphone", "label": "insuredPhone"},
        {"field_key": "applicant.address", "label": "address"},
        {"field_key": "applicant.annual_income", "label": "annual income"},
        {"field_key": "applicant.occupation", "label": "occupation"},
        {"field_key": "applicant.region", "label": "region"},
        {"field_key": "policy.start_date", "label": "start date"},
        {"field_key": "agreement.confirm", "label": "agree"},
    ]

    mock_data = generate_policy_mock_data(fields, seed=20260511, today=date(2026, 5, 11))

    assert mock_data["insure_form.applicantname"] != mock_data["insure_form.insuredname"]
    assert _id_card_checksum_ok(mock_data["insure_form.applicantidno"])
    assert _id_card_checksum_ok(mock_data["insure_form.insuredidno"])
    assert mock_data["insure_form.applicantidno"][6:14].startswith("199")
    assert mock_data["insure_form.insuredidno"][6:14].startswith("201")
    assert mock_data["insure_form.applicantphone"] != mock_data["insure_form.insuredphone"]
    assert "@" in mock_data["insure_form.applicantemail"]
    assert len(mock_data["applicant.address"]) >= 10
    assert mock_data["applicant.annual_income"] == "20"
    assert mock_data["applicant.occupation"]
    assert mock_data["applicant.region"]
    assert mock_data["policy.start_date"] == "2026-05-12"
    assert mock_data["agreement.confirm"] == "true"


def test_policy_info_generator_uses_student_occupation_for_child_insured():
    from e2e_agent.core.policy_info_generator import generate_policy_mock_data

    fields = [
        {"field_key": "insured.occupation", "label": "insured occupation"},
        {"field_key": "applicant.occupation", "label": "applicant occupation"},
    ]

    mock_data = generate_policy_mock_data(fields, seed=20260511, today=date(2026, 5, 11))

    assert mock_data["applicant.occupation"]
    assert mock_data["insured.occupation"]
    assert mock_data["applicant.occupation"] != mock_data["insured.occupation"]


def test_policy_info_generator_adds_english_name_and_travel_defaults():
    from e2e_agent.core.policy_info_generator import _romanize_chinese_name, generate_policy_mock_data

    mock_data = generate_policy_mock_data([], seed=20260511, today=date(2026, 5, 11))

    assert _romanize_chinese_name("陈雨欣") == "chenyuxin"
    assert _romanize_chinese_name("赵俊俊") == "zhaojunjun"
    assert mock_data["applicant.english_name"] == _romanize_chinese_name(mock_data["applicant.name"])
    assert mock_data["applicant.pinyin"] == mock_data["applicant.english_name"]
    assert mock_data["insured.english_name"] == _romanize_chinese_name(mock_data["insured.name"])
    assert mock_data["insured.pinyin"] == mock_data["insured.english_name"]
    assert mock_data["insure_form.applicantpinyin"] == mock_data["applicant.english_name"]
    assert mock_data["travel.purpose"] == "旅游"
    assert mock_data["travel.purpose_code"] == "1"
    assert mock_data["travel.destination"] == "中国澳门"
    assert mock_data["insure_form.traveldestination"] == "中国澳门"


def test_policy_info_generator_does_not_map_type_controls_to_person_names():
    from e2e_agent.core.policy_info_generator import generate_policy_mock_data

    fields = [
        {
            "field_key": "insure_form.cardtype",
            "selector": 'input:not([type="hidden"])[name*="cardType" i]',
            "label": "card type",
        },
        {
            "field_key": "insure_form.insuredidtype",
            "selector": 'input:not([type="hidden"])[name*="insuredIdType" i]',
            "label": "insured card type",
        },
        {
            "field_key": "insure_form.cardvalidtype",
            "selector": 'input:not([type="hidden"])[name*="cardValidType" i]',
            "label": "card validity type",
        },
    ]

    mock_data = generate_policy_mock_data(fields, seed=20260511, today=date(2026, 5, 11))

    assert mock_data["insure_form.cardtype"]
    assert mock_data["insure_form.insuredidtype"]
    assert mock_data["insure_form.cardvalidtype"]
    assert mock_data["insure_form.cardtype"] != mock_data["applicant.name"]
    assert mock_data["insure_form.insuredidtype"] != mock_data["insured.name"]


def test_policy_info_generator_uses_html_fixture_bank_and_records_complete_tool_data(tmp_path: Path):
    from e2e_agent.core.policy_info_generator import generate_policy_mock_data

    source_html_path = _write_policy_tool_fixture(tmp_path / "policy-tool-fixture.html")
    mock_data = generate_policy_mock_data(
        [],
        seed=20260515,
        today=date(2026, 5, 15),
        source_html_path=source_html_path,
        preferred_bank_type="ICBC",
    )

    record = json.loads(mock_data["policy_tool.record_json"])
    record_values = set(record.values())
    assert mock_data["policy_tool.source_path"].endswith("policy-tool-fixture.html")
    assert mock_data["bankName_107"] in record_values
    assert mock_data["bankHtmlCode_107"] == "102"
    assert mock_data["bankValue_107"] == "1"
    assert mock_data["payAccount_107"] == "6217002352508817050"
    assert len(mock_data["payAccount_107"]) == 19
    assert mock_data["payAccount_107"] in record_values
    assert mock_data["applicant.name"] in record_values
    assert mock_data["applicant.id_no"] in record_values
    assert mock_data["applicant.certificate_address"] in record_values
    assert mock_data["applicant.address"] == mock_data["applicant.certificate_address"]
    assert mock_data["applicant.certificate_validity_text"] in record_values
    assert mock_data["applicant.certificate_validity_text"] == "2021.05.15-2041.05.15"
    assert mock_data["applicant.card_valid_start"] == "2021-05-15"
    assert mock_data["applicant.card_valid_end"] == "2041-05-15"


def test_policy_info_generator_uses_policy_today_env_for_runtime_runs(monkeypatch):
    from e2e_agent.core.policy_info_generator import generate_policy_mock_data

    monkeypatch.setenv("AGENT3_POLICY_TODAY", "2026-05-15")

    mock_data = generate_policy_mock_data(
        [],
        seed=20260515,
        preferred_bank_type="ICBC",
    )

    assert mock_data["applicant.certificate_validity_text"] == "2021.05.15-2041.05.15"
    assert mock_data["applicant.card_valid_start"] == "2021-05-15"
    assert mock_data["applicant.card_valid_end"] == "2041-05-15"
    assert len(mock_data["applicant.name"]) >= 3


def test_policy_info_generator_keeps_applicant_in_live_trial_age_band():
    from e2e_agent.core.policy_info_generator import generate_policy_mock_data

    mock_data = generate_policy_mock_data(
        [],
        seed=1779702826417544500,
        today=date(2026, 5, 25),
    )

    applicant_birthdate = date.fromisoformat(mock_data["applicant.birthdate"])
    applicant_age = 2026 - applicant_birthdate.year - (
        (5, 25) < (applicant_birthdate.month, applicant_birthdate.day)
    )

    assert 30 <= applicant_age <= 32

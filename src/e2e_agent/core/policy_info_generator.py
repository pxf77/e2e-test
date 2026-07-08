"""Policy mock data generation using deterministic fallback rules."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import os
import json
from pathlib import Path
import random
import re
from typing import Any, Iterable, Mapping


ID_CARD_FACTORS: tuple[int, ...] = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
ID_CARD_CHECK_CODES = "10X98765432"
DEFAULT_REGION = "北京市 朝阳区"
DEFAULT_OCCUPATION = "一般内勤人员"
DEFAULT_CHILD_OCCUPATION = "一般学生"
DEFAULT_ANNUAL_INCOME_WAN = "20"
DEFAULT_HEIGHT_CM = "170"
DEFAULT_WEIGHT_KG = "60"
DEFAULT_APPLICANT_ENGLISH_NAME = "zhangsan"
DEFAULT_INSURED_ENGLISH_NAME = "lisi"
DEFAULT_TRAVEL_PURPOSE_TEXT = "旅游"
DEFAULT_TRAVEL_PURPOSE_CODE = "1"
DEFAULT_TRAVEL_DESTINATION = "中国澳门"
PINYIN_BY_CHAR = {
    "赵": "zhao",
    "钱": "qian",
    "孙": "sun",
    "李": "li",
    "周": "zhou",
    "吴": "wu",
    "郑": "zheng",
    "王": "wang",
    "冯": "feng",
    "陈": "chen",
    "杨": "yang",
    "柏": "bai",
    "子": "zi",
    "轩": "xuan",
    "浩": "hao",
    "然": "ran",
    "宇": "yu",
    "辰": "chen",
    "梓": "zi",
    "涵": "han",
    "诗": "shi",
    "雨": "yu",
    "佳": "jia",
    "怡": "yi",
    "欣": "xin",
    "妍": "yan",
    "晨": "chen",
    "曦": "xi",
    "俊": "jun",
    "杰": "jie",
    "嘉": "jia",
    "豪": "hao",
    "思": "si",
    "远": "yuan",
    "雅": "ya",
    "琪": "qi",
}
DEFAULT_TOOL_HTML: Path | None = None
PAGE_BANK_VALUE_BY_HTML_CODE = {
    "102": "1",  # 中国工商银行 / 工商银行
    "105": "2",
    "403": "3",
    "103": "4",
    "305": "5",
    "308": "6",
    "309": "7",
    "104": "8",
    "302": "9",
    "301": "10",
    "783": "11",
    "303": "12",
    "310": "13",
    "306": "14",
    "304": "16",
}
BANK_CARD_PREFIX_BY_HTML_CODE = {
    # External tool fixtures can carry OCR/template card prefixes that are not
    # accepted by the live page's bank-card BIN recognizer. Keep the bank
    # choice from the fixture, but generate a runtime-recognizable debit BIN.
    "102": "622202",  # ICBC debit card
}
SIGNING_TEST_CARD_BY_HTML_CODE = {
    # Verified by the legacy e2e-test H5 bank-signing exploration: this ICBC
    # debit card can pass /insure/bank/sign/apply in the pre-release sandbox.
    "102": "6217002352508817050",
}

FALLBACK_BANKS: dict[str, dict[str, Any]] = {
    "ICBC": {"name": "中国工商银行", "code": "102", "prefix": "620058", "length": 19},
    "HXB": {"name": "华夏银行", "code": "304", "prefix": "622632", "length": 16},
}
FALLBACK_MOBILE_PREFIXES = ("134", "135", "136", "137", "138", "139", "150", "151", "152", "157", "158", "159", "182", "183", "187", "188", "130", "131", "132", "145", "155", "156", "133", "149", "153")
FALLBACK_EMAIL_DOMAINS = ("qq.com", "163.com", "126.com", "aliyun.com", "test.com")
FALLBACK_SURNAMES = ("赵", "钱", "孙", "李", "周", "吴", "郑", "王", "冯", "陈", "杨", "柏")
FALLBACK_GIVEN_NAME_CHARS = "子轩浩然宇辰梓涵诗雨佳怡欣妍晨曦俊杰嘉豪思远雅琪"
FALLBACK_STREETS = ("幸福街道", "和平街道", "光明街道", "安宁街道")
FALLBACK_ROADS = ("和平路", "建设路", "人民路", "光明路")
FALLBACK_COMMUNITIES = ("兰庭", "锦绣", "阳光", "华府")
FALLBACK_SUFFIXES = ("花园", "小区", "家园", "公馆")
FALLBACK_NATIONS = ("汉", "满", "回", "苗", "壮")
FALLBACK_AREA_TREE = [
    {
        "code": "110000",
        "name": "北京市",
        "cities": [{"code": "110100", "name": "北京市", "districts": [{"code": "110105", "name": "朝阳区"}]}],
    }
]


@dataclass(frozen=True)
class ToolConfig:
    banks: dict[str, dict[str, Any]]
    mobile_prefixes: tuple[str, ...]
    email_domains: tuple[str, ...]
    surnames: tuple[str, ...]
    given_name_chars: str
    streets: tuple[str, ...]
    roads: tuple[str, ...]
    communities: tuple[str, ...]
    suffixes: tuple[str, ...]
    nations: tuple[str, ...]
    area_tree: list[dict[str, Any]]
    source_path: str


@dataclass(frozen=True)
class PolicyPerson:
    name: str
    id_no: str
    birthdate: str
    gender: str
    mobile: str
    email: str
    address: str
    region: str
    occupation: str
    annual_income: str
    height: str
    weight: str
    card_valid_start: str
    card_valid_end: str
    birth_place: str
    nation: str
    certificate_address: str
    certificate_authority: str
    certificate_validity_text: str


@dataclass(frozen=True)
class PolicyProfile:
    applicant: PolicyPerson
    insured: PolicyPerson
    bank: dict[str, str]
    policy_start_date: str
    beneficiary_type: str
    beneficiary_relation: str
    beneficiary_ratio: str
    agreement_confirm: str
    sms_code: str
    tool_record: dict[str, str]
    tool_source_path: str


def _rng(seed: int | None) -> random.Random:
    return random.Random(seed) if seed is not None else random.Random()


def _env_today() -> date | None:
    for key in ("AGENT3_POLICY_TODAY", "E2E_POLICY_TODAY"):
        raw = os.environ.get(key, "").strip()
        if not raw:
            continue
        try:
            return date.fromisoformat(raw)
        except ValueError:
            continue
    return None


def _random_digits(rand: random.Random, length: int) -> str:
    return "".join(str(rand.randint(0, 9)) for _ in range(length))


def _extract_js_json(text: str, const_name: str, fallback: Any) -> Any:
    match = re.search(rf"const\s+{re.escape(const_name)}\s*=\s*(.*?);", text, re.S)
    if not match:
        return fallback
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return fallback


def load_policy_tool_config(source_html_path: str | Path | None = None) -> ToolConfig:
    source = Path(source_html_path) if source_html_path else DEFAULT_TOOL_HTML
    text = ""
    if source:
        try:
            text = source.read_text(encoding="utf-8")
            if "let BANK_CARD_TEMPLATES" in text:
                text = text[: text.index("let BANK_CARD_TEMPLATES")]
        except OSError:
            source = None
    return ToolConfig(
        banks=_extract_js_json(text, "BANKS", FALLBACK_BANKS),
        mobile_prefixes=tuple(_extract_js_json(text, "MOBILE_PREFIXES", FALLBACK_MOBILE_PREFIXES)),
        email_domains=tuple(_extract_js_json(text, "EMAIL_DOMAINS", FALLBACK_EMAIL_DOMAINS)),
        surnames=tuple(_extract_js_json(text, "SURNAMES", FALLBACK_SURNAMES)),
        given_name_chars=str(_extract_js_json(text, "GIVEN_NAME_CHARS", FALLBACK_GIVEN_NAME_CHARS)),
        streets=tuple(_extract_js_json(text, "ADDRESS_STREETS", FALLBACK_STREETS)),
        roads=tuple(_extract_js_json(text, "ADDRESS_ROADS", FALLBACK_ROADS)),
        communities=tuple(_extract_js_json(text, "ADDRESS_COMMUNITIES", FALLBACK_COMMUNITIES)),
        suffixes=tuple(_extract_js_json(text, "ADDRESS_SUFFIXES", FALLBACK_SUFFIXES)),
        nations=tuple(_extract_js_json(text, "NATIONS", FALLBACK_NATIONS)),
        area_tree=list(_extract_js_json(text, "AREA_TREE", FALLBACK_AREA_TREE)),
        source_path=str(source) if source else "builtin-fallback",
    )


def _id_card_check_code(id17: str) -> str:
    total = sum(int(id17[index]) * ID_CARD_FACTORS[index] for index in range(17))
    return ID_CARD_CHECK_CODES[total % 11]


def _luhn_check_digit(prefix_and_body: str) -> str:
    total = 0
    reversed_digits = [int(ch) for ch in reversed(prefix_and_body)]
    for index, digit in enumerate(reversed_digits):
        if index % 2 == 0:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return str((10 - total % 10) % 10)


def _add_years(base: date, years: int) -> date:
    try:
        return base.replace(year=base.year + years)
    except ValueError:
        return base.replace(month=2, day=28, year=base.year + years)


def _validity_bounds(today: date, *, birthday: date) -> tuple[str, str]:
    start = _add_years(today, -5)
    age = start.year - birthday.year - ((start.month, start.day) < (birthday.month, birthday.day))
    if age < 16:
        valid_years = 5
    elif age < 26:
        valid_years = 10
    elif age < 46:
        valid_years = 20
    else:
        valid_years = 0
    end = _add_years(start, valid_years) if valid_years else _add_years(start, 20)
    return start.isoformat(), end.isoformat()


def _tool_default_validity_text(today: date) -> str:
    start = _add_years(today, -5)
    end = _add_years(today, 15)
    return f"{start:%Y.%m}.02-{end:%Y.%m}.01"


def _area_choices(area_tree: list[dict[str, Any]]) -> list[tuple[str, str, str, str]]:
    choices: list[tuple[str, str, str, str]] = []
    for province in area_tree:
        for city in province.get("cities", []) or []:
            for district in city.get("districts", []) or []:
                province_name = str(province.get("name") or "")
                city_name = str(city.get("name") or "")
                district_name = str(district.get("name") or "朝阳区")
                if province_name and city_name and province_name == city_name:
                    label = f"{province_name}{district_name}"
                else:
                    label = f"{province_name}{city_name}{district_name}"
                choices.append((str(district.get("code") or "110105"), label, district_name, province_name))
    return choices or [("110105", "北京市北京市朝阳区", "朝阳区", "北京市")]


def generate_id_card(*, birthday: date, gender: str, rand: random.Random | None = None, area_code: str = "110101") -> str:
    """Generate a valid mainland China ID card number with checksum."""
    local_rand = rand or random.Random()
    sequence_base = local_rand.randint(1, 499) * 2
    sequence = sequence_base + (1 if gender == "male" else 0)
    id17 = f"{area_code}{birthday:%Y%m%d}{sequence:03d}"
    return f"{id17}{_id_card_check_code(id17)}"


def _bank_card(bank: Mapping[str, Any], rand: random.Random) -> str:
    bank_code = str(bank.get("code") or "")
    if bank_code in SIGNING_TEST_CARD_BY_HTML_CODE:
        return SIGNING_TEST_CARD_BY_HTML_CODE[bank_code]
    prefix = BANK_CARD_PREFIX_BY_HTML_CODE.get(bank_code) or str(bank.get("prefix") or "620058")
    length = int(bank.get("length") or 19)
    body_len = max(0, length - len(prefix) - 1)
    without_check = f"{prefix}{_random_digits(rand, body_len)}"
    return f"{without_check}{_luhn_check_digit(without_check)}"


def generate_phone_number(rand: random.Random | None = None, config: ToolConfig | None = None) -> str:
    local_rand = rand or random.Random()
    prefixes = config.mobile_prefixes if config else FALLBACK_MOBILE_PREFIXES
    return f"{local_rand.choice(prefixes)}{_random_digits(local_rand, 8)}"


def generate_email_address(rand: random.Random | None = None, config: ToolConfig | None = None) -> str:
    local_rand = rand or random.Random()
    domains = config.email_domains if config else FALLBACK_EMAIL_DOMAINS
    segment_count = local_rand.randint(1, 3)
    segments = [
        "".join(local_rand.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(local_rand.randint(2, 6)))
        for _ in range(segment_count)
    ]
    username = local_rand.choice([".", "_"]).join(segments)[:20]
    return f"{username}@{local_rand.choice(domains)}"


def generate_chinese_name(rand: random.Random | None = None, config: ToolConfig | None = None) -> str:
    local_rand = rand or random.Random()
    surnames = config.surnames if config else FALLBACK_SURNAMES
    chars = config.given_name_chars if config else FALLBACK_GIVEN_NAME_CHARS
    given = "".join(local_rand.choice(chars) for _ in range(2))
    return f"{local_rand.choice(surnames)}{given}"


def _romanize_chinese_name(name: str, fallback: str = DEFAULT_APPLICANT_ENGLISH_NAME) -> str:
    parts = [PINYIN_BY_CHAR.get(char, "") for char in str(name or "")]
    romanized = "".join(parts)
    return romanized if romanized and len(romanized) >= 2 else fallback


def generate_detailed_address(rand: random.Random | None = None, config: ToolConfig | None = None) -> str:
    local_rand = rand or random.Random()
    cfg = config or load_policy_tool_config(Path(""))
    street = local_rand.choice(cfg.streets)
    road = local_rand.choice(cfg.roads)
    community = f"{local_rand.choice(cfg.communities)}{local_rand.choice(cfg.suffixes)}"
    road_no = local_rand.randint(1, 4999)
    unit = local_rand.randint(1, 20)
    room = local_rand.randint(101, 2808)
    return f"{street}{road}{road_no}号{community}{unit}单元{room}室"


def _person(
    *,
    rand: random.Random,
    birthday: date,
    gender: str,
    today: date,
    config: ToolConfig,
    area_code: str,
    birth_place: str,
    district_name: str,
    child: bool = False,
) -> PolicyPerson:
    start, end = _validity_bounds(today, birthday=birthday)
    validity_text = f"{start.replace('-', '.')}-{end.replace('-', '.')}"
    name = generate_chinese_name(rand, config)
    detail_address = generate_detailed_address(rand, config)
    id_no = generate_id_card(birthday=birthday, gender=gender, rand=rand, area_code=area_code)
    certificate_address = f"{birth_place}{detail_address}"
    return PolicyPerson(
        name=name,
        id_no=id_no,
        birthdate=birthday.isoformat(),
        gender="男" if gender == "male" else "女",
        mobile=generate_phone_number(rand, config),
        email=generate_email_address(rand, config),
        address=certificate_address,
        region=birth_place,
        occupation=DEFAULT_CHILD_OCCUPATION if child else DEFAULT_OCCUPATION,
        annual_income=DEFAULT_ANNUAL_INCOME_WAN,
        height=DEFAULT_HEIGHT_CM,
        weight=DEFAULT_WEIGHT_KG,
        card_valid_start=start,
        card_valid_end=end,
        birth_place=birth_place,
        nation=rand.choice(config.nations),
        certificate_address=certificate_address,
        certificate_authority=f"{district_name}公安局",
        certificate_validity_text=validity_text,
    )


def generate_policy_profile(
    *,
    seed: int | None = None,
    today: date | None = None,
    source_html_path: str | Path | None = None,
    preferred_bank_type: str = "ICBC",
) -> PolicyProfile:
    rand = _rng(seed)
    current = today or _env_today() or date.today()
    config = load_policy_tool_config(source_html_path)
    area_code, birth_place, district_name, _province_name = rand.choice(_area_choices(config.area_tree))
    # The live H5 trialGenes path is age-sensitive; keep fresh data inside the
    # verified adult band while preserving random month/day and ID checksum.
    applicant_birthday = date(current.year - 32, rand.randint(1, 12), rand.randint(1, 28))
    insured_birthday = date(2010 + rand.randint(0, 7), rand.randint(1, 12), rand.randint(1, 28))
    applicant = _person(
        rand=rand,
        birthday=applicant_birthday,
        gender="male",
        today=current,
        config=config,
        area_code=area_code,
        birth_place=birth_place,
        district_name=district_name,
    )
    insured = _person(
        rand=rand,
        birthday=insured_birthday,
        gender="female",
        today=current,
        config=config,
        area_code=area_code,
        birth_place=birth_place,
        district_name=district_name,
        child=True,
    )
    if insured.name == applicant.name:
        insured = PolicyPerson(**{**insured.__dict__, "name": f"{insured.name}一"})

    bank_type = preferred_bank_type if preferred_bank_type in config.banks else next(iter(config.banks or FALLBACK_BANKS))
    raw_bank = config.banks.get(bank_type, FALLBACK_BANKS["ICBC"])
    bank_code = str(raw_bank.get("code") or "")
    bank_name = str(raw_bank.get("name") or "中国工商银行")
    page_value = PAGE_BANK_VALUE_BY_HTML_CODE.get(bank_code, bank_code or "1")
    bank = {
        "type": bank_type,
        "name": bank_name,
        "html_code": bank_code,
        "page_value": page_value,
        "card_no": _bank_card(raw_bank, rand),
    }
    tool_record = {
        "银行": bank["name"],
        "银行卡号": bank["card_no"],
        "姓名": applicant.name,
        "手机号": applicant.mobile,
        "邮箱": applicant.email,
        "详细地址": applicant.address.removeprefix(applicant.birth_place),
        "身份证号": applicant.id_no,
        "出生地": applicant.birth_place,
        "生日": applicant.birthdate,
        "性别": applicant.gender,
        "民族": applicant.nation,
        "证件地址": applicant.certificate_address,
        "签发机关": applicant.certificate_authority,
        "证件有效期": applicant.certificate_validity_text,
    }
    return PolicyProfile(
        applicant=applicant,
        insured=insured,
        bank=bank,
        policy_start_date=(current + timedelta(days=1)).isoformat(),
        beneficiary_type="法定受益人",
        beneficiary_relation="法定",
        beneficiary_ratio="100",
        agreement_confirm="true",
        sms_code="1111",
        tool_record=tool_record,
        tool_source_path=config.source_path,
    )


def _field_text(field: Mapping[str, Any]) -> str:
    locators = " ".join(
        str(locator.get("value") or "")
        for locator in field.get("locators", []) or []
        if isinstance(locator, Mapping)
    )
    return " ".join(
        str(field.get(key) or "")
        for key in ("field_key", "label", "selector", "name", "mock_strategy", "value_strategy")
    ).lower() + " " + locators.lower()


def _is_insured_field(text: str) -> bool:
    return any(token in text for token in ("insured", "被保", "_20", "default_1"))


def _is_applicant_field(text: str) -> bool:
    return any(token in text for token in ("applicant", "投保", "_10"))


def _person_for_field(text: str, profile: PolicyProfile) -> PolicyPerson:
    if _is_insured_field(text):
        return profile.insured
    if _is_applicant_field(text):
        return profile.applicant
    return profile.applicant


def mock_value_for_field(
    field: Mapping[str, Any],
    *,
    profile: PolicyProfile | None = None,
) -> str:
    profile = profile or generate_policy_profile()
    text = _field_text(field)
    person = _person_for_field(text, profile)

    if re.search(r"verifycode|captcha|smscode|短信|验证码", text, re.IGNORECASE):
        return profile.sms_code
    if "agreement" in text or "confirmagreement" in text or "已阅读" in text or "同意" in text:
        return profile.agreement_confirm
    if "policy.start_date" in text or "startdate" in text or "起保" in text:
        return profile.policy_start_date
    if "beneficiary.ratio" in text or "ratio" in text:
        return profile.beneficiary_ratio
    if "beneficiary.relation" in text or "beneficiary.type" in text or "beneficiary" in text or "受益" in text:
        return profile.beneficiary_type
    if "annual_income" in text or "annualincome" in text or "yearlyincome" in text or "年收入" in text:
        return profile.applicant.annual_income
    if "occupation" in text or "jobtext" in text or "职业" in text:
        return person.occupation
    if "region" in text or "residencedistrict" in text or "province" in text or "city" in text or "居住省市" in text:
        return person.region
    if "address" in text or "联系地址" in text or "地址" in text:
        return person.address
    if "height" in text or "身高" in text:
        return person.height
    if "weight" in text or "体重" in text:
        return person.weight
    if "cardvalidstart" in text or ("cardperiod" in text and "end" not in text) or "有效期开始" in text:
        return person.card_valid_start
    if "cardvalidend" in text or "cardperiodend" in text or "有效期结束" in text:
        return person.card_valid_end
    if "cardvalidtype" in text or "有效期类型" in text:
        return "固定有效期"
    if "ename" in text or "english" in text or "pinyin" in text or "英文名" in text or "拼音" in text:
        return DEFAULT_INSURED_ENGLISH_NAME if _is_insured_field(text) else DEFAULT_APPLICANT_ENGLISH_NAME
    if "birthdate" in text or "birthday" in text or "出生日期" in text or "生日" in text:
        return person.birthdate
    if "gender" in text or "sex" in text or "性别" in text:
        return person.gender
    if "email" in text or "邮箱" in text:
        return person.email
    if "phone" in text or "mobile" in text or "moblie" in text or "tel" in text or "手机" in text:
        return person.mobile
    if "idcard" in text or "idno" in text or "idnumber" in text or "cardnumber" in text or "证件号码" in text or "身份证" in text:
        return person.id_no
    if "relation" in text or "relationship" in text or "关系" in text:
        return "子女" if _is_insured_field(text) else "本人"
    if "cardtype" in text or "idtype" in text or "证件类型" in text:
        return "身份证"
    if "payaccount" in text or "bankaccount" in text or "account" in text or "银行账号" in text or "银行卡号" in text:
        return profile.bank["card_no"]
    if "cardowner" in text or "持卡人" in text:
        return profile.applicant.name
    if "bankcode" in text or "bankname" in text or "openbank" in text or "开户银行" in text or "银行" in text:
        return profile.bank["name"]
    if "destination" in text or "tripdestination" in text or "目的地" in text:
        return DEFAULT_TRAVEL_DESTINATION
    if "purpose" in text or "出行目的" in text:
        return DEFAULT_TRAVEL_PURPOSE_TEXT
    if "name" in text or "姓名" in text:
        return person.name
    return "mock"


def _canonical_mock_data(profile: PolicyProfile) -> dict[str, str]:
    applicant = profile.applicant
    insured = profile.insured
    bank = profile.bank
    bank_pair = f"{bank['name']}|{bank['page_value']}|{bank['card_no']}"
    applicant_english_name = _romanize_chinese_name(applicant.name, DEFAULT_APPLICANT_ENGLISH_NAME)
    insured_english_name = _romanize_chinese_name(insured.name, DEFAULT_INSURED_ENGLISH_NAME)
    return {
        "policy_tool.source_path": profile.tool_source_path,
        "policy_tool.record_json": json.dumps(profile.tool_record, ensure_ascii=False, sort_keys=True),
        **{f"policy_tool.record.{key}": value for key, value in profile.tool_record.items()},
        "applicant.name": applicant.name,
        "applicant.english_name": applicant_english_name,
        "applicant.pinyin": applicant_english_name,
        "applicant.eName": applicant_english_name,
        "applicant.id_no": applicant.id_no,
        "applicant.mobile": applicant.mobile,
        "applicant.phone": applicant.mobile,
        "applicant.email": applicant.email,
        "applicant.birthdate": applicant.birthdate,
        "applicant.gender": applicant.gender,
        "applicant.address": applicant.address,
        "applicant.region": applicant.region,
        "applicant.occupation": applicant.occupation,
        "applicant.annual_income": applicant.annual_income,
        "applicant.height": applicant.height,
        "applicant.weight": applicant.weight,
        "applicant.card_valid_start": applicant.card_valid_start,
        "applicant.card_valid_end": applicant.card_valid_end,
        "applicant.birth_place": applicant.birth_place,
        "applicant.nation": applicant.nation,
        "applicant.certificate_address": applicant.certificate_address,
        "applicant.certificate_authority": applicant.certificate_authority,
        "applicant.certificate_validity_text": applicant.certificate_validity_text,
        "insured.name": insured.name,
        "insured.english_name": insured_english_name,
        "insured.pinyin": insured_english_name,
        "insured.eName": insured_english_name,
        "insured.id_no": insured.id_no,
        "insured.mobile": insured.mobile,
        "insured.phone": insured.mobile,
        "insured.email": insured.email,
        "insured.birthdate": insured.birthdate,
        "insured.gender": insured.gender,
        "insured.address": insured.address,
        "insured.region": insured.region,
        "insured.occupation": insured.occupation,
        "insured.height": insured.height,
        "insured.weight": insured.weight,
        "insured.card_valid_start": insured.card_valid_start,
        "insured.card_valid_end": insured.card_valid_end,
        "policy.start_date": profile.policy_start_date,
        "policyStartDate": profile.policy_start_date,
        "travel.purpose": DEFAULT_TRAVEL_PURPOSE_TEXT,
        "travel.purpose_code": DEFAULT_TRAVEL_PURPOSE_CODE,
        "travel.destination": DEFAULT_TRAVEL_DESTINATION,
        "agreement.confirm": profile.agreement_confirm,
        "beneficiary.type": profile.beneficiary_type,
        "beneficiary.relation": profile.beneficiary_relation,
        "beneficiary.ratio": profile.beneficiary_ratio,
        "insure_form.applicantname": applicant.name,
        "insure_form.applicantpinyin": applicant_english_name,
        "insure_form.applicantidno": applicant.id_no,
        "insure_form.applicantphone": applicant.mobile,
        "insure_form.applicantemail": applicant.email,
        "insure_form.insuredname": insured.name,
        "insure_form.insuredpinyin": insured_english_name,
        "insure_form.insuredidno": insured.id_no,
        "insure_form.insuredphone": insured.mobile,
        "insure_form.cardtype": "身份证",
        "insure_form.insuredidtype": "身份证",
        "insure_form.cardvalidstart": applicant.card_valid_start,
        "insure_form.cardvalidend": applicant.card_valid_end,
        "insure_form.cardvalidtype": "固定有效期",
        "insure_form.insurancedate": profile.policy_start_date,
        "insure_form.travelpurpose": DEFAULT_TRAVEL_PURPOSE_TEXT,
        "insure_form.travelpurposecode": DEFAULT_TRAVEL_PURPOSE_CODE,
        "insure_form.traveldestination": DEFAULT_TRAVEL_DESTINATION,
        "risk_control_check.smscode": profile.sms_code,
        "cardOwner_107": applicant.name,
        "payAccount_107": bank["card_no"],
        "bankCode_107": bank["html_code"],
        "bankHtmlCode_107": bank["html_code"],
        "bankControlValue_107": bank["page_value"],
        "bankValue_107": bank["page_value"],
        "bankName_107": bank["name"],
        "openBank_107": bank["name"],
        "bankAccountPair_107": bank_pair,
    }


def generate_policy_mock_data(
    fields: Iterable[Mapping[str, Any]],
    *,
    seed: int | None = None,
    today: date | None = None,
    source_html_path: str | Path | None = None,
    preferred_bank_type: str = "ICBC",
) -> dict[str, str]:
    profile = generate_policy_profile(
        seed=seed,
        today=today,
        source_html_path=source_html_path,
        preferred_bank_type=preferred_bank_type,
    )
    mock_data = _canonical_mock_data(profile)
    for field in fields:
        field_key = str(field.get("field_key") or "")
        if not field_key:
            continue
        mock_data[field_key] = mock_value_for_field(field, profile=profile)
    return mock_data

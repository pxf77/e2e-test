from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace
from pathlib import Path

import pytest

from e2e_agent.legacy.browser.runner import PlaywrightTSRunner


def test_parse_json_reporter_accepts_pretty_json(tmp_path):
    runner = PlaywrightTSRunner(tmp_path)
    stdout = """
noise before json
{
  "stats": {
    "expected": 2,
    "unexpected": 1
  },
  "suites": [
    {
      "specs": [
        {
          "tests": [
            {
              "results": [
                {
                  "status": "failed",
                  "error": {
                    "message": "locator timeout"
                  }
                }
              ]
            }
          ]
        }
      ]
    }
  ]
}
"""

    parsed = runner._parse_json_reporter(stdout)

    assert parsed == {
        "passed": 2,
        "failed": 1,
        "errors": ["locator timeout"],
    }


def test_run_spec_reports_missing_playwright_cli(tmp_path, monkeypatch):
    runner = PlaywrightTSRunner(tmp_path)
    monkeypatch.setattr(runner, "_resolve_playwright_command", lambda: None)

    assert runner.check_node_available() is False
    with pytest.raises(FileNotFoundError, match="Playwright CLI is not available"):
        runner.run_spec("demo.spec.ts")


def test_run_formal_spec_uses_project_config_and_report_dir(tmp_path, monkeypatch):
    runner = PlaywrightTSRunner(tmp_path)
    spec_path = tmp_path / "products" / "demo" / "ts-gen" / ".artifacts" / "chain-to-result.spec.ts"
    spec_path.parent.mkdir(parents=True)
    spec_path.write_text("import { test } from 'playwright/test';\n", encoding="utf-8")

    calls = []

    def fake_run(cmd, cwd, capture_output, text, encoding, errors, timeout, env):
        calls.append(
            {
                "cmd": cmd,
                "cwd": cwd,
                "env": env,
                "timeout": timeout,
            }
        )
        return SimpleNamespace(returncode=0, stdout="1 passed", stderr="")

    monkeypatch.setattr(runner, "_resolve_playwright_command", lambda: ["playwright"])
    monkeypatch.setattr(runner, "_command_cwd", lambda: tmp_path)
    monkeypatch.setattr("e2e_agent.legacy.browser.runner.subprocess.run", fake_run)

    result = runner.run_formal_spec(
        str(spec_path),
        report_dir=tmp_path / "products" / "demo" / "tc-exec",
    )

    assert result["execution_entry"] == "agent4.playwright-formal"
    assert result["formal_execution"] is True
    assert result["visible_browser"] is True
    assert result["passed"] == 1
    assert calls[0]["cwd"] == str(spec_path.parent.resolve())
    assert calls[0]["cmd"] == [
        "playwright",
        "test",
        spec_path.name,
        f"--config={tmp_path / '.tmp' / 'playwright.formal.config.ts'}",
        "--project=chromium",
    ]
    report_dir = tmp_path / "products" / "demo" / "tc-exec"
    isolated_report_dir = report_dir / "chain-to-result"
    assert calls[0]["env"]["REPORT_DIR"] == str(isolated_report_dir)
    assert calls[0]["env"]["HEADED"] == "1"
    assert calls[0]["timeout"] == 900
    assert "--reporter=json" not in calls[0]["cmd"]
    assert (tmp_path / ".tmp" / "playwright.formal.config.ts").exists()
    assert result["report_dir"] == str(isolated_report_dir)
    assert result["formal_primary_report_dir"] == str(report_dir)


def test_run_formal_spec_defaults_policy_start_date_to_next_day(tmp_path, monkeypatch):
    runner = PlaywrightTSRunner(tmp_path)
    spec_path = tmp_path / "products" / "demo" / "agent3" / "ts-gen" / "h5" / "scenarios" / "01-path-001.spec.ts"
    spec_path.parent.mkdir(parents=True)
    spec_path.write_text("import { test } from '@playwright/test';\n", encoding="utf-8")
    calls = []

    def fake_run(cmd, cwd, capture_output, text, encoding, errors, timeout, env):
        calls.append({"env": env})
        return SimpleNamespace(returncode=0, stdout="1 passed", stderr="")

    monkeypatch.delenv("AGENT4_POLICY_START_DATE", raising=False)
    monkeypatch.delenv("AGENT4_POLICY_START_OFFSET_DAYS", raising=False)
    monkeypatch.setenv("AGENT4_DISABLE_MOCK_USER", "1")
    monkeypatch.setattr(runner, "_resolve_playwright_command", lambda: ["playwright"])
    monkeypatch.setattr("e2e_agent.legacy.browser.runner.subprocess.run", fake_run)

    runner.run_formal_spec(str(spec_path), report_dir=tmp_path / "tc-exec")

    assert calls[0]["env"]["AGENT4_POLICY_START_OFFSET_DAYS"] == "1"
    assert "AGENT4_POLICY_START_DATE" not in calls[0]["env"]


def test_run_formal_spec_preserves_explicit_policy_start_date_override(tmp_path, monkeypatch):
    runner = PlaywrightTSRunner(tmp_path)
    spec_path = tmp_path / "products" / "demo" / "agent3" / "ts-gen" / "h5" / "scenarios" / "01-path-001.spec.ts"
    spec_path.parent.mkdir(parents=True)
    spec_path.write_text("import { test } from '@playwright/test';\n", encoding="utf-8")
    calls = []

    def fake_run(cmd, cwd, capture_output, text, encoding, errors, timeout, env):
        calls.append({"env": env})
        return SimpleNamespace(returncode=0, stdout="1 passed", stderr="")

    monkeypatch.setenv("AGENT4_POLICY_START_DATE", "2026-06-09")
    monkeypatch.delenv("AGENT4_POLICY_START_OFFSET_DAYS", raising=False)
    monkeypatch.setattr(runner, "_resolve_playwright_command", lambda: ["playwright"])
    monkeypatch.setattr("e2e_agent.legacy.browser.runner.subprocess.run", fake_run)

    runner.run_formal_spec(str(spec_path), report_dir=tmp_path / "tc-exec")

    assert calls[0]["env"]["AGENT4_POLICY_START_DATE"] == "2026-06-09"
    assert "AGENT4_POLICY_START_OFFSET_DAYS" not in calls[0]["env"]


def test_run_formal_spec_reads_execution_requirements_for_policy_date_and_passport_mock_user(tmp_path, monkeypatch):
    runner = PlaywrightTSRunner(tmp_path)
    spec_path = tmp_path / "products" / "demo" / "agent3" / "ts-gen" / "h5" / "scenarios" / "03-path-003.spec.ts"
    spec_path.parent.mkdir(parents=True)
    spec_path.write_text(
        "/**\n"
        " * @scenario {\"execution_requirements\":{\"mock_user_required\":true,\"mock_user_id_type\":\"护照\",\"policy_start_offset_days\":2}}\n"
        " */\n"
        "import { test } from '@playwright/test';\n",
        encoding="utf-8",
    )
    mock_user_script = tmp_path / "mock_user.cjs"
    mock_user_script.write_text("console.log('{}');\n", encoding="utf-8")
    calls = []
    mock_user_calls = []
    mock_user_payload = {
        "scenario": "self",
        "applicant": {
            "姓名": "谭博",
            "证件类型": "护照",
            "证件号码": "EA1342046",
            "手机号": "13103331433",
            "邮箱": "passport@example.com",
        },
    }

    def fake_run(cmd, cwd, capture_output, text, encoding, errors, timeout, env=None):
        if len(cmd) >= 2 and Path(cmd[1]) == mock_user_script:
            mock_user_calls.append(cmd)
            return SimpleNamespace(returncode=0, stdout=json.dumps(mock_user_payload, ensure_ascii=False), stderr="")
        calls.append({"cmd": cmd, "env": env})
        return SimpleNamespace(returncode=0, stdout="1 passed", stderr="")

    monkeypatch.delenv("AGENT4_POLICY_START_DATE", raising=False)
    monkeypatch.delenv("AGENT4_POLICY_START_OFFSET_DAYS", raising=False)
    monkeypatch.setenv("AGENT4_MOCK_USER_SCRIPT", str(mock_user_script))
    monkeypatch.delenv("AGENT4_MOCK_DATA_OVERRIDES", raising=False)
    monkeypatch.setattr(runner, "_resolve_playwright_command", lambda: ["playwright"])
    monkeypatch.setattr("e2e_agent.legacy.browser.runner.shutil.which", lambda name: "node" if name == "node" else None)
    monkeypatch.setattr("e2e_agent.legacy.browser.runner.subprocess.run", fake_run)

    result = runner.run_formal_spec(str(spec_path), report_dir=tmp_path / "tc-exec")

    assert result["returncode"] == 0
    assert mock_user_calls
    assert "--id-type" in mock_user_calls[0]
    assert "护照" in mock_user_calls[0]
    assert calls[0]["env"]["AGENT4_POLICY_START_OFFSET_DAYS"] == "2"
    overrides = json.loads(calls[0]["env"]["AGENT4_MOCK_DATA_OVERRIDES"])
    assert overrides["applicant.id_type"] == "护照"
    assert overrides["applicant.id_no"] == "EA1342046"


def test_run_formal_spec_fails_preflight_when_required_mock_user_overrides_are_unavailable(tmp_path, monkeypatch):
    runner = PlaywrightTSRunner(tmp_path)
    spec_path = tmp_path / "products" / "demo" / "agent3" / "ts-gen" / "h5" / "scenarios" / "01-path-001.spec.ts"
    spec_path.parent.mkdir(parents=True)
    spec_path.write_text(
        "/**\n"
        " * @scenario {\"execution_requirements\":{\"mock_user_required\":true,\"policy_start_offset_days\":1}}\n"
        " */\n"
        "import { test } from '@playwright/test';\n",
        encoding="utf-8",
    )

    def fail_if_playwright_runs(*_args, **_kwargs):
        raise AssertionError("Playwright should not run when required mock_user data is unavailable")

    monkeypatch.delenv("AGENT4_MOCK_USER_SCRIPT", raising=False)
    monkeypatch.delenv("AGENT4_MOCK_DATA_OVERRIDES", raising=False)
    monkeypatch.setenv("AGENT4_DISABLE_MOCK_USER", "1")
    monkeypatch.setattr(runner, "_resolve_playwright_command", lambda: ["playwright"])
    monkeypatch.setattr("e2e_agent.legacy.browser.runner.subprocess.run", fail_if_playwright_runs)

    result = runner.run_formal_spec(str(spec_path), report_dir=tmp_path / "tc-exec")

    assert result["returncode"] == 1
    assert result["passed"] == 0
    assert result["failed"] == 1
    assert "mock_user_required" in result["errors"][0]
    assert result["preflight_failed"] is True


def test_run_formal_spec_generates_mock_user_overrides_when_script_configured(tmp_path, monkeypatch):
    runner = PlaywrightTSRunner(tmp_path)
    spec_path = tmp_path / "products" / "demo" / "agent3" / "ts-gen" / "h5" / "scenarios" / "01-path-001.spec.ts"
    spec_path.parent.mkdir(parents=True)
    spec_path.write_text("import { test } from '@playwright/test';\n", encoding="utf-8")
    mock_user_script = tmp_path / "mock_user.cjs"
    mock_user_script.write_text("console.log('{}');\n", encoding="utf-8")
    calls = []
    mock_user_calls = []

    mock_user_payload = {
        "scenario": "self",
        "relation": "本人",
        "applicant": {
            "姓名": "曹华",
            "性别": "男",
            "出生日期": "1994-06-02",
            "年龄": 32,
            "证件号码": "110102199406026097",
            "证件有效期(起始)": "2017-06-08",
            "证件有效期(截止)": "2027-06-08",
            "手机号": "13830235275",
            "邮箱": "5536631647@qq.com",
            "居住省市": "北京市-市辖区-西城区",
            "地址": "北京市市辖区西城区测试路196号23栋21楼8号",
            "身高(cm)": 171,
            "体重(kg)": 84,
            "年收入(万元)": 70,
            "银行": "工商银行",
            "银行卡号": "6212265285066657661",
        },
    }

    def fake_run(cmd, cwd, capture_output, text, encoding, errors, timeout, env=None):
        if len(cmd) >= 2 and Path(cmd[1]) == mock_user_script:
            mock_user_calls.append(cmd)
            return SimpleNamespace(
                returncode=0,
                stdout='noise\n{"ignored": true}\n' + json.dumps(mock_user_payload, ensure_ascii=False),
                stderr="",
            )
        calls.append({"cmd": cmd, "env": env})
        return SimpleNamespace(returncode=0, stdout="1 passed", stderr="")

    monkeypatch.setenv("AGENT4_MOCK_USER_SCRIPT", str(mock_user_script))
    monkeypatch.delenv("AGENT4_MOCK_DATA_OVERRIDES", raising=False)
    monkeypatch.setattr(runner, "_resolve_playwright_command", lambda: ["playwright"])
    monkeypatch.setattr("e2e_agent.legacy.browser.runner.shutil.which", lambda name: "node" if name == "node" else None)
    monkeypatch.setattr("e2e_agent.legacy.browser.runner.subprocess.run", fake_run)

    runner.run_formal_spec(str(spec_path), report_dir=tmp_path / "tc-exec")

    assert mock_user_calls
    overrides = json.loads(calls[0]["env"]["AGENT4_MOCK_DATA_OVERRIDES"])
    assert overrides["applicant.name"] == "曹华"
    assert overrides["applicant.id_no"] == "110102199406026097"
    assert overrides["applicant.mobile"] == "13830235275"
    assert overrides["applicant.email"] == "5536631647@qq.com"
    assert overrides["insured.name"] == "曹华"
    assert overrides["insured.id_no"] == "110102199406026097"
    assert overrides["insure_form.applicantidno"] == "110102199406026097"
    assert overrides["cardOwner_107"] == "曹华"
    assert overrides["payAccount_107"] == "6212265285066657661"
    assert overrides["bankName_107"] == "工商银行"
    assert overrides["bankAccountPair_107"] == "工商银行|1|6212265285066657661"
    assert overrides["forWho_20"] == "100"


def test_run_formal_spec_maps_real_chinese_mock_user_payload_and_passport_spec(tmp_path, monkeypatch):
    runner = PlaywrightTSRunner(tmp_path)
    spec_path = tmp_path / "products" / "demo" / "agent3" / "ts-gen" / "h5" / "scenarios" / "03-path-003.spec.ts"
    spec_path.parent.mkdir(parents=True)
    spec_path.write_text('const embeddedMockData = {"applicant.id_type": "护照"};\n', encoding="utf-8")
    mock_user_script = tmp_path / "mock_user.cjs"
    mock_user_script.write_text("console.log('{}');\n", encoding="utf-8")
    calls = []
    mock_user_calls = []

    mock_user_payload = {
        "scenario": "self",
        "relation": "本人",
        "applicant": {
            "姓名": "胡宏",
            "性别": "男",
            "出生日期": "1994-03-25",
            "证件类型": "护照",
            "证件号码": "EA3752728",
            "证件有效期(起始)": "2025-03-25",
            "证件有效期(截止)": "2035-03-25",
            "手机号": "19674697908",
            "邮箱": "0300465769@qq.com",
            "居住省市": "北京市-市辖区-西城区",
            "地址": "北京市市辖区西城区测试路874号75栋22楼3号",
            "身高(cm)": 186,
            "体重(kg)": 67,
            "年收入(万元)": 65,
            "银行": "工商银行",
            "银行卡号": "6212268753566038796",
        },
    }

    def fake_run(cmd, cwd, capture_output, text, encoding, errors, timeout, env=None):
        if len(cmd) >= 2 and Path(cmd[1]) == mock_user_script:
            mock_user_calls.append(cmd)
            return SimpleNamespace(returncode=0, stdout=json.dumps(mock_user_payload, ensure_ascii=False), stderr="")
        calls.append({"cmd": cmd, "env": env})
        return SimpleNamespace(returncode=0, stdout="1 passed", stderr="")

    monkeypatch.setenv("AGENT4_MOCK_USER_SCRIPT", str(mock_user_script))
    monkeypatch.delenv("AGENT4_MOCK_DATA_OVERRIDES", raising=False)
    monkeypatch.setattr(runner, "_resolve_playwright_command", lambda: ["playwright"])
    monkeypatch.setattr("e2e_agent.legacy.browser.runner.shutil.which", lambda name: "node" if name == "node" else None)
    monkeypatch.setattr("e2e_agent.legacy.browser.runner.subprocess.run", fake_run)

    runner.run_formal_spec(str(spec_path), report_dir=tmp_path / "tc-exec")

    assert mock_user_calls
    assert "--id-type" in mock_user_calls[0]
    assert "护照" in mock_user_calls[0]
    overrides = json.loads(calls[0]["env"]["AGENT4_MOCK_DATA_OVERRIDES"])
    assert overrides["applicant.name"] == "胡宏"
    assert overrides["applicant.id_type"] == "护照"
    assert overrides["applicant.id_type_code"] == "2"
    assert overrides["applicant.id_no"] == "EA3752728"
    assert overrides["applicant.mobile"] == "19674697908"
    assert overrides["applicant.email"] == "0300465769@qq.com"
    assert overrides["insured.name"] == "胡宏"
    assert overrides["insured.id_type"] == "护照"
    assert overrides["insure_form.cardtype"] == "护照"
    assert overrides["insure_form.applicantidno"] == "EA3752728"
    assert overrides["cardOwner_107"] == "胡宏"
    assert overrides["payAccount_107"] == "6212268753566038796"


def test_agent4_mock_user_script_falls_back_to_shared_e2e_test_path(tmp_path, monkeypatch):
    repo_root = tmp_path / "aiming-e2e-testing"
    runner = PlaywrightTSRunner(repo_root)
    shared_path = tmp_path / "e2e-test" / ".claude" / "skills" / "mpt-ins-ts-gen" / "scripts" / "mock_user.cjs"

    monkeypatch.delenv("AGENT4_MOCK_USER_SCRIPT", raising=False)
    monkeypatch.setattr(Path, "is_file", lambda self: self == shared_path)

    assert runner._resolve_mock_user_script({}) == shared_path


def test_run_formal_spec_preserves_existing_mock_data_overrides(tmp_path, monkeypatch):
    runner = PlaywrightTSRunner(tmp_path)
    spec_path = tmp_path / "products" / "demo" / "agent3" / "ts-gen" / "h5" / "scenarios" / "01-path-001.spec.ts"
    spec_path.parent.mkdir(parents=True)
    spec_path.write_text("import { test } from '@playwright/test';\n", encoding="utf-8")
    mock_user_script = tmp_path / "mock_user.cjs"
    mock_user_script.write_text("throw new Error('should not be called');\n", encoding="utf-8")
    calls = []

    def fake_run(cmd, cwd, capture_output, text, encoding, errors, timeout, env=None):
        assert len(cmd) < 2 or Path(cmd[1]) != mock_user_script
        calls.append({"cmd": cmd, "env": env})
        return SimpleNamespace(returncode=0, stdout="1 passed", stderr="")

    explicit_overrides = '{"applicant.id_no":"110105199401093011"}'
    monkeypatch.setenv("AGENT4_MOCK_USER_SCRIPT", str(mock_user_script))
    monkeypatch.setenv("AGENT4_MOCK_DATA_OVERRIDES", explicit_overrides)
    monkeypatch.setattr(runner, "_resolve_playwright_command", lambda: ["playwright"])
    monkeypatch.setattr("e2e_agent.legacy.browser.runner.subprocess.run", fake_run)

    runner.run_formal_spec(str(spec_path), report_dir=tmp_path / "tc-exec")

    assert calls[0]["env"]["AGENT4_MOCK_DATA_OVERRIDES"] == explicit_overrides


def test_run_formal_spec_uses_repo_mock_user_script_by_default(tmp_path, monkeypatch):
    runner = PlaywrightTSRunner(tmp_path)
    spec_path = tmp_path / "products" / "demo" / "agent3" / "ts-gen" / "h5" / "scenarios" / "01-path-001.spec.ts"
    spec_path.parent.mkdir(parents=True)
    spec_path.write_text("import { test } from '@playwright/test';\n", encoding="utf-8")
    repo_script = tmp_path / "src" / "e2e_agent" / "skills" / "mpt-ins-ts-gen" / "scripts" / "mock_user.cjs"
    repo_script.parent.mkdir(parents=True)
    repo_script.write_text("console.log('{}');\n", encoding="utf-8")
    calls = []
    mock_user_calls = []

    def fake_run(cmd, cwd, capture_output, text, encoding, errors, timeout, env=None):
        if len(cmd) >= 2 and Path(cmd[1]) == repo_script:
            mock_user_calls.append(cmd)
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "scenario": "self",
                        "relation": "本人",
                        "applicant": {
                            "姓名": "李明轩",
                            "证件号码": "110105199401018714",
                            "手机号": "13812345678",
                            "邮箱": "repo@example.com",
                            "银行": "工商银行",
                            "银行卡号": "6212261234567890123",
                        },
                    },
                    ensure_ascii=False,
                ),
                stderr="",
            )
        calls.append({"cmd": cmd, "env": env})
        return SimpleNamespace(returncode=0, stdout="1 passed", stderr="")

    monkeypatch.delenv("AGENT4_MOCK_USER_SCRIPT", raising=False)
    monkeypatch.delenv("AGENT4_MOCK_DATA_OVERRIDES", raising=False)
    monkeypatch.setattr(runner, "_resolve_playwright_command", lambda: ["playwright"])
    monkeypatch.setattr("e2e_agent.legacy.browser.runner.shutil.which", lambda name: "node" if name == "node" else None)
    monkeypatch.setattr("e2e_agent.legacy.browser.runner.subprocess.run", fake_run)

    runner.run_formal_spec(str(spec_path), report_dir=tmp_path / "tc-exec")

    assert mock_user_calls
    overrides = json.loads(calls[0]["env"]["AGENT4_MOCK_DATA_OVERRIDES"])
    assert overrides["applicant.name"] == "李明轩"
    assert overrides["applicant.id_no"] == "110105199401018714"


def test_run_formal_spec_does_not_probe_workspace_siblings_for_mock_user(tmp_path, monkeypatch):
    runner = PlaywrightTSRunner(tmp_path)
    spec_path = tmp_path / "products" / "demo" / "agent3" / "ts-gen" / "h5" / "scenarios" / "01-path-001.spec.ts"
    spec_path.parent.mkdir(parents=True)
    spec_path.write_text("import { test } from '@playwright/test';\n", encoding="utf-8")
    sibling_script = (
        tmp_path.parent
        / "e2e-test"
        / ".claude"
        / "skills"
        / "mpt-ins-ts-gen"
        / "scripts"
        / "mock_user.cjs"
    )
    sibling_script.parent.mkdir(parents=True, exist_ok=True)
    sibling_script.write_text("console.log('{}');\n", encoding="utf-8")
    calls = []

    def fake_run(cmd, cwd, capture_output, text, encoding, errors, timeout, env=None):
        assert len(cmd) < 2 or Path(cmd[1]) != sibling_script
        calls.append({"cmd": cmd, "env": env})
        return SimpleNamespace(returncode=0, stdout="1 passed", stderr="")

    monkeypatch.delenv("AGENT4_MOCK_USER_SCRIPT", raising=False)
    monkeypatch.delenv("AGENT4_MOCK_DATA_OVERRIDES", raising=False)
    monkeypatch.setattr(runner, "_resolve_playwright_command", lambda: ["playwright"])
    monkeypatch.setattr("e2e_agent.legacy.browser.runner.shutil.which", lambda name: "node" if name == "node" else None)
    monkeypatch.setattr("e2e_agent.legacy.browser.runner.subprocess.run", fake_run)

    runner.run_formal_spec(str(spec_path), report_dir=tmp_path / "tc-exec")

    assert "AGENT4_MOCK_DATA_OVERRIDES" not in calls[0]["env"]


def test_run_formal_spec_isolates_output_by_spec_stem(tmp_path, monkeypatch):
    runner = PlaywrightTSRunner(tmp_path)
    first_spec = tmp_path / "products" / "demo" / "agent3" / "ts-gen" / "h5" / "scenarios" / "01-path-001.spec.ts"
    second_spec = first_spec.with_name("02-path-002.spec.ts")
    first_spec.parent.mkdir(parents=True)
    first_spec.write_text("import { test } from '@playwright/test';\n", encoding="utf-8")
    second_spec.write_text("import { test } from '@playwright/test';\n", encoding="utf-8")
    report_dir = tmp_path / "products" / "demo" / "agent4" / "tc-exec"
    calls = []

    def fake_run(cmd, cwd, capture_output, text, encoding, errors, timeout, env):
        calls.append({"cmd": cmd, "env": env})
        return SimpleNamespace(returncode=0, stdout="1 passed", stderr="")

    monkeypatch.setattr(runner, "_resolve_playwright_command", lambda: ["playwright"])
    monkeypatch.setattr("e2e_agent.legacy.browser.runner.subprocess.run", fake_run)

    first = runner.run_formal_spec(str(first_spec), report_dir=report_dir)
    second = runner.run_formal_spec(str(second_spec), report_dir=report_dir)

    assert first["report_dir"] == str(report_dir / "01-path-001")
    assert second["report_dir"] == str(report_dir / "02-path-002")
    assert first["report_dir"] != second["report_dir"]
    assert calls[0]["env"]["REPORT_DIR"] == str(report_dir / "01-path-001")
    assert calls[1]["env"]["REPORT_DIR"] == str(report_dir / "02-path-002")


def test_run_formal_spec_recovers_passed_last_run_after_timeout(tmp_path, monkeypatch):
    runner = PlaywrightTSRunner(tmp_path)
    spec_path = tmp_path / "products" / "demo" / "agent3" / "ts-gen" / "h5" / "scenarios" / "01-path-001.spec.ts"
    spec_path.parent.mkdir(parents=True)
    spec_path.write_text("import { test } from '@playwright/test';\n", encoding="utf-8")
    report_dir = tmp_path / "products" / "demo" / "agent4" / "tc-exec"

    def fake_run(cmd, cwd, capture_output, text, encoding, errors, timeout, env):
        last_run = Path(env["REPORT_DIR"]) / "test-results" / ".last-run.json"
        last_run.parent.mkdir(parents=True)
        last_run.write_text('{"status":"passed","failedTests":[]}', encoding="utf-8")
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout, output="1 passed", stderr="")

    monkeypatch.setattr(runner, "_resolve_playwright_command", lambda: ["playwright"])
    monkeypatch.setattr("e2e_agent.legacy.browser.runner.subprocess.run", fake_run)

    result = runner.run_formal_spec(str(spec_path), report_dir=report_dir, timeout_seconds=1)

    assert result["returncode"] == 0
    assert result["passed"] == 1
    assert result["failed"] == 0
    assert result["errors"] == []
    assert result["timed_out"] is True
    assert result["recovered_from_last_run"] is True
    assert result["last_run_status"] == "passed"
    assert result["report_dir"] == str(report_dir / "01-path-001")


def test_run_formal_spec_does_not_recover_from_stale_last_run(tmp_path, monkeypatch):
    runner = PlaywrightTSRunner(tmp_path)
    spec_path = tmp_path / "products" / "demo" / "agent3" / "ts-gen" / "h5" / "scenarios" / "01-path-001.spec.ts"
    spec_path.parent.mkdir(parents=True)
    spec_path.write_text("import { test } from '@playwright/test';\n", encoding="utf-8")
    report_dir = tmp_path / "products" / "demo" / "agent4" / "tc-exec"
    stale_last_run = report_dir / "01-path-001" / "test-results" / ".last-run.json"
    stale_last_run.parent.mkdir(parents=True)
    stale_last_run.write_text('{"status":"passed","failedTests":[]}', encoding="utf-8")

    def fake_run(cmd, cwd, capture_output, text, encoding, errors, timeout, env):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout, output="", stderr="")

    monkeypatch.setattr(runner, "_resolve_playwright_command", lambda: ["playwright"])
    monkeypatch.setattr("e2e_agent.legacy.browser.runner.subprocess.run", fake_run)

    result = runner.run_formal_spec(str(spec_path), report_dir=report_dir, timeout_seconds=1)

    assert result["returncode"] == 124
    assert result["passed"] == 0
    assert result["failed"] == 1
    assert result["recovered_from_last_run"] is False
    assert result["last_run_status"] is None


def test_formal_config_uses_mobile_context_for_h5_specs(tmp_path):
    runner = PlaywrightTSRunner(tmp_path)
    spec_path = tmp_path / "products" / "demo" / "agent3" / "ts-gen" / "h5" / "scenarios" / "01-path.spec.ts"
    spec_path.parent.mkdir(parents=True)
    spec_path.write_text(
        "import { test } from '@playwright/test';\n"
        "test('h5', async ({ page }) => { await page.goto('https://cps.example.com/m/apps/cps/demo/product/detail'); });\n",
        encoding="utf-8",
    )

    config_path = runner._write_formal_config(spec_path, tmp_path / "tc-exec")
    config = Path(config_path).read_text(encoding="utf-8")

    assert "timeout: 720_000" in config
    assert "viewport: { width: 390, height: 844 }" in config
    assert "screenshot: 'only-on-failure'" in config
    assert "video: {\n      mode: 'on',\n      size: { width: 390, height: 844 }" in config
    assert "isMobile: true" in config
    assert "hasTouch: true" in config
    assert "deviceScaleFactor: 3" in config
    assert "iPhone" in config
    assert "PLAYWRIGHT_CHROMIUM_CHANNEL" in config
    assert "channel: 'chrome' as const" in config
    assert "msedge" not in config


def test_local_playwright_command_is_absolute(tmp_path):
    local_bin = tmp_path / "node_modules" / ".bin"
    local_bin.mkdir(parents=True)
    command = local_bin / "playwright.cmd"
    command.write_text("", encoding="utf-8")

    resolved = PlaywrightTSRunner(tmp_path)._resolve_playwright_command()

    assert resolved == [str(command.resolve())]
    assert Path(resolved[0]).is_absolute()


@pytest.mark.asyncio
async def test_browser_session_creates_storage_state_parent(tmp_path):
    from e2e_agent.legacy.browser.session import BrowserSession

    storage_state_path = tmp_path / "reg" / "runtime" / "h5-storage-state.json"

    class FakeContext:
        async def storage_state(self, path: str) -> None:
            Path(path).write_text("{}", encoding="utf-8")

    session = BrowserSession(
        storage_state_path=str(storage_state_path),
        record_storage_state=True,
    )
    session._context = FakeContext()  # type: ignore[assignment]

    await session.__aexit__()

    assert storage_state_path.exists()

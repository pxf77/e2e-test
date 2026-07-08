"""PlaywrightTSRunner — subprocess compatibility mode for original .spec.ts files.

Strategy (T-FS-7):
    Primary:  subprocess.run(["npx", "playwright", "test", spec_path, "--reporter=json"])
              Reuses the original TypeScript spec files from D:\\huizecode\\e2e-test\\ without
              requiring a Python rewrite. Output is parsed from JSON reporter.
    Fallback: Pure Playwright Python (BrowserSession) if TS spec files are incompatible.

This design satisfies RULE-C14 (platform agnostic) and C5 (Playwright Python replaces MCP).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Mapping


class PlaywrightTSRunner:
    """Runs existing Playwright TypeScript .spec.ts files via subprocess.

    Requires Node.js and @playwright/test to be installed in repo_root.
    """

    def __init__(self, repo_root: str | Path) -> None:
        self.repo_root = Path(repo_root)

    def _runtime_roots(self) -> list[Path]:
        """Candidate roots that may contain the Node Playwright runtime."""
        roots = [self.repo_root]
        for parent in self.repo_root.parents:
            if (parent / "pyproject.toml").exists() or (parent / "package.json").exists():
                roots.append(parent)
                break
        return list(dict.fromkeys(roots))

    def _command_cwd(self) -> Path:
        for root in self._runtime_roots():
            if (root / "node_modules").exists() or (root / "package.json").exists():
                return root
        return self.repo_root

    def _resolve_playwright_command(self) -> list[str] | None:
        """Resolve a runnable Playwright CLI command prefix."""
        for root in self._runtime_roots():
            local_bin = root / "node_modules" / ".bin"
            local_candidates = [
                local_bin / "playwright.cmd",
                local_bin / "playwright",
            ]
            for candidate in local_candidates:
                if candidate.exists():
                    return [str(candidate.resolve())]

        npx_path = shutil.which("npx")
        if npx_path:
            return [npx_path, "playwright"]
        return None

    def has_project_test_runtime(self) -> bool:
        """Return True when the repo has a locally importable Playwright test runtime."""
        candidates = []
        for root in self._runtime_roots():
            candidates.extend(
                [
                    root / "node_modules" / "@playwright" / "test",
                    root / "node_modules" / "playwright",
                ]
            )
        return any(candidate.exists() for candidate in candidates)

    def run_spec(
        self,
        spec_path: str,
        timeout_seconds: int = 300,
        extra_args: list[str] | None = None,
    ) -> dict:
        """Run a single .spec.ts file and return structured results.

        Args:
            spec_path: Path to the spec file (relative to repo_root or absolute).
            timeout_seconds: subprocess timeout.
            extra_args: Additional Playwright CLI arguments.

        Returns:
            dict with keys: returncode, passed, failed, errors, raw_output
        """
        command_prefix = self._resolve_playwright_command()
        if command_prefix is None:
            raise FileNotFoundError("Playwright CLI is not available")

        spec = Path(spec_path)
        if not spec.is_absolute():
            spec = self.repo_root / spec
        run_cwd = spec.parent if spec.exists() else self._command_cwd()

        spec_arg = spec.name if run_cwd == spec.parent else str(spec)
        cmd = [*command_prefix, "test", spec_arg, "--reporter=json"]
        if extra_args:
            cmd.extend(extra_args)

        result = subprocess.run(
            cmd,
            cwd=str(run_cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
        )

        parsed = self._parse_json_reporter(result.stdout)
        return {
            "returncode": result.returncode,
            "passed": parsed.get("passed", 0),
            "failed": parsed.get("failed", 0),
            "errors": parsed.get("errors", []),
            "raw_output": result.stdout or "",
            "stderr": result.stderr or "",
        }

    def run_formal_spec(
        self,
        spec_path: str,
        timeout_seconds: int = 900,
        report_dir: str | Path | None = None,
        extra_args: list[str] | None = None,
    ) -> dict:
        """Run a generated spec as an Agent4 formal execution.

        This mirrors the e2e-test tc-exec contract: execute Playwright from the
        project root with the repository config, let the configured reporters
        write HTML/video/trace artifacts, and avoid forcing a JSON reporter.
        """
        command_prefix = self._resolve_playwright_command()
        if command_prefix is None:
            raise FileNotFoundError("Playwright CLI is not available")

        spec = Path(spec_path)
        if not spec.is_absolute():
            spec = self.repo_root / spec
        run_cwd = spec.resolve().parent if spec.exists() else self._command_cwd()
        spec_arg = spec.name if spec.exists() else str(spec)
        formal_primary_report_dir = Path(report_dir) if report_dir is not None else self.repo_root / "tc-exec"
        formal_report_dir = formal_primary_report_dir / self._formal_report_slug(spec)
        config_path = self._write_formal_config(spec, formal_report_dir)
        self._clear_formal_last_run(formal_report_dir)

        cmd = [
            *command_prefix,
            "test",
            spec_arg,
            f"--config={config_path}",
            "--project=chromium",
        ]
        if extra_args:
            cmd.extend(extra_args)

        env = os.environ.copy()
        env["REPORT_DIR"] = str(formal_report_dir)
        env["HEADED"] = "1"
        execution_requirements = self._agent4_spec_execution_requirements(spec)
        if not env.get("AGENT4_POLICY_START_DATE") and not env.get("AGENT4_POLICY_START_OFFSET_DAYS"):
            env["AGENT4_POLICY_START_OFFSET_DAYS"] = str(
                execution_requirements.get("policy_start_offset_days") or "1"
            )
        self._attach_agent4_mock_user_overrides(env, spec, execution_requirements)
        env.setdefault("PLAYWRIGHT_HTML_OPEN", "never")
        preflight_error = self._agent4_formal_preflight_error(env, execution_requirements)
        if preflight_error:
            return {
                "returncode": 1,
                "passed": 0,
                "failed": 1,
                "errors": [preflight_error],
                "raw_output": "",
                "stderr": preflight_error,
                "command": cmd,
                "cwd": str(run_cwd),
                "report_dir": str(formal_report_dir),
                "formal_primary_report_dir": str(formal_primary_report_dir),
                "execution_entry": "agent4.playwright-formal",
                "formal_execution": True,
                "visible_browser": True,
                "preflight_failed": True,
            }

        try:
            result = subprocess.run(
                cmd,
                cwd=str(run_cwd),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            return self._formal_timeout_result(
                exc,
                cmd=cmd,
                run_cwd=run_cwd,
                formal_report_dir=formal_report_dir,
                formal_primary_report_dir=formal_primary_report_dir,
                timeout_seconds=timeout_seconds,
            )

        parsed = self._parse_text_reporter(result.stdout, result.stderr, result.returncode)
        return {
            "returncode": result.returncode,
            "passed": parsed.get("passed", 0),
            "failed": parsed.get("failed", 0),
            "errors": parsed.get("errors", []),
            "raw_output": result.stdout or "",
            "stderr": result.stderr or "",
            "command": cmd,
            "cwd": str(run_cwd),
            "report_dir": str(formal_report_dir),
            "formal_primary_report_dir": str(formal_primary_report_dir),
            "execution_entry": "agent4.playwright-formal",
            "formal_execution": True,
            "visible_browser": True,
        }

    def _formal_timeout_result(
        self,
        exc: subprocess.TimeoutExpired,
        *,
        cmd: list[str],
        run_cwd: Path,
        formal_report_dir: Path,
        formal_primary_report_dir: Path,
        timeout_seconds: int,
    ) -> dict:
        raw_output = self._subprocess_text(exc.output)
        stderr = self._subprocess_text(exc.stderr)
        last_run = self._read_formal_last_run(formal_report_dir)
        last_run_status = str(last_run.get("status") or "").strip().lower()
        failed_tests = [
            str(item)
            for item in (last_run.get("failedTests") or [])
            if str(item).strip()
        ]
        recovered = last_run_status == "passed" and not failed_tests
        if recovered:
            returncode = 0
            passed = 1
            failed = 0
            errors: list[str] = []
        else:
            returncode = 124
            passed = 0
            failed = max(1, len(failed_tests))
            detail = f"Playwright formal execution timed out after {timeout_seconds}s"
            if last_run_status:
                detail = f"{detail}; last-run status={last_run_status}"
            if failed_tests:
                detail = f"{detail}; failedTests={', '.join(failed_tests[:5])}"
            errors = [detail]

        return {
            "returncode": returncode,
            "passed": passed,
            "failed": failed,
            "errors": errors,
            "raw_output": raw_output,
            "stderr": stderr,
            "command": cmd,
            "cwd": str(run_cwd),
            "report_dir": str(formal_report_dir),
            "formal_primary_report_dir": str(formal_primary_report_dir),
            "execution_entry": "agent4.playwright-formal",
            "formal_execution": True,
            "visible_browser": True,
            "timed_out": True,
            "recovered_from_last_run": recovered,
            "last_run_status": last_run_status or None,
            "last_run_failed_tests": failed_tests,
        }

    def _read_formal_last_run(self, formal_report_dir: Path) -> dict:
        path = formal_report_dir / "test-results" / ".last-run.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _clear_formal_last_run(self, formal_report_dir: Path) -> None:
        path = formal_report_dir / "test-results" / ".last-run.json"
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    def _subprocess_text(self, value: object) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)

    def _attach_agent4_mock_user_overrides(
        self,
        env: dict[str, str],
        spec_path: Path | None = None,
        execution_requirements: Mapping[str, object] | None = None,
    ) -> None:
        if env.get("AGENT4_MOCK_DATA_OVERRIDES"):
            return
        if str(env.get("AGENT4_DISABLE_MOCK_USER") or "").strip().lower() in {"1", "true", "yes"}:
            return
        overrides = self._agent4_mock_user_overrides(env, spec_path, execution_requirements)
        if overrides:
            env["AGENT4_MOCK_DATA_OVERRIDES"] = json.dumps(overrides, ensure_ascii=False)

    def _agent4_mock_user_overrides(
        self,
        env: dict[str, str],
        spec_path: Path | None = None,
        execution_requirements: Mapping[str, object] | None = None,
    ) -> dict[str, str]:
        script = self._resolve_mock_user_script(env)
        if script is None:
            return {}
        node_bin = env.get("AGENT4_NODE_BIN") or shutil.which("node")
        if not node_bin:
            return {}
        id_type = (
            str(env.get("AGENT4_MOCK_USER_ID_TYPE") or "").strip()
            or str((execution_requirements or {}).get("mock_user_id_type") or "").strip()
            or self._agent4_spec_id_type(spec_path)
        )

        cmd = [
            node_bin,
            str(script),
            "--scenario",
            str(env.get("AGENT4_MOCK_USER_SCENARIO") or "self"),
            "--age",
            str(env.get("AGENT4_MOCK_USER_AGE") or "32"),
            "--gender",
            str(env.get("AGENT4_MOCK_USER_GENDER") or "男"),
            "--region",
            str(env.get("AGENT4_MOCK_USER_REGION") or "北京"),
            "--bank",
            str(env.get("AGENT4_MOCK_USER_BANK") or "工商银行"),
        ]
        if id_type:
            cmd.extend(["--id-type", id_type])
        try:
            timeout = int(str(env.get("AGENT4_MOCK_USER_TIMEOUT_SECONDS") or "10"))
        except ValueError:
            timeout = 10
        result = subprocess.run(
            cmd,
            cwd=str(script.parent),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        if result.returncode != 0:
            return {}
        payload = self._parse_mock_user_output(result.stdout)
        return self._mock_user_payload_to_agent4_overrides(payload, script)

    def _agent4_spec_execution_requirements(self, spec_path: Path | None) -> dict[str, object]:
        if spec_path is None:
            return {}
        try:
            text = spec_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return {}
        match = re.search(r"@scenario\s+({.*})", text)
        if not match:
            return {}
        try:
            metadata = json.loads(match.group(1))
        except json.JSONDecodeError:
            return {}
        requirements = metadata.get("execution_requirements")
        return dict(requirements) if isinstance(requirements, dict) else {}

    def _agent4_formal_preflight_error(
        self,
        env: dict[str, str],
        execution_requirements: Mapping[str, object],
    ) -> str:
        if bool(execution_requirements.get("mock_user_required")) and not env.get("AGENT4_MOCK_DATA_OVERRIDES"):
            return "agent4 preflight failed: mock_user_required but AGENT4_MOCK_DATA_OVERRIDES was not generated"
        return ""

    def _agent4_spec_id_type(self, spec_path: Path | None) -> str:
        if spec_path is None:
            return ""
        try:
            text = spec_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return ""
        if '"applicant.id_type": "护照"' in text or "'applicant.id_type': '护照'" in text:
            return "护照"
        if '"insure_form.cardtype": "护照"' in text or "'insure_form.cardtype': '护照'" in text:
            return "护照"
        return ""

    def _resolve_mock_user_script(self, env: dict[str, str]) -> Path | None:
        explicit = str(env.get("AGENT4_MOCK_USER_SCRIPT") or "").strip()
        candidates: list[Path] = []
        if explicit:
            candidates.append(Path(explicit))
        if self._allow_agent4_shared_mock_user_script():
            candidates.append(
                self.repo_root.parent
                / "e2e-test"
                / ".claude"
                / "skills"
                / "mpt-ins-ts-gen"
                / "scripts"
                / "mock_user.cjs"
            )
        candidates.extend(
            [
                self.repo_root / "src" / "e2e_agent" / "skills" / "mpt-ins-ts-gen" / "scripts" / "mock_user.cjs",
                self.repo_root / ".claude" / "skills" / "mpt-ins-ts-gen" / "scripts" / "mock_user.cjs",
            ]
        )
        for candidate in candidates:
            try:
                resolved = candidate.expanduser().resolve()
            except OSError:
                resolved = candidate
            if resolved.is_file():
                return resolved
        if explicit:
            raise FileNotFoundError(f"mock_user.cjs is not available: {explicit}")
        return None

    def _allow_agent4_shared_mock_user_script(self) -> bool:
        return self.repo_root.name == "aiming-e2e-testing"

    def _parse_mock_user_output(self, stdout: str) -> dict:
        text = str(stdout or "").strip()
        if not text:
            return {}
        candidates = [text]
        candidates.extend(line.strip() for line in reversed(text.splitlines()) if line.strip().startswith("{"))
        candidates.extend(text[index:].strip() for index, char in enumerate(text) if char == "{")
        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
        return {}

    def _mock_user_payload_to_agent4_overrides(self, payload: dict, script: Path) -> dict[str, str]:
        if not isinstance(payload, dict):
            return {}
        applicant = payload.get("applicant") if isinstance(payload.get("applicant"), dict) else {}
        insured = (
            payload.get("insured")
            if isinstance(payload.get("insured"), dict)
            else payload.get("insurant")
            if isinstance(payload.get("insurant"), dict)
            else applicant
        )
        overrides: dict[str, str] = {
            "mock_user.source": script.name,
            "mock_user.scenario": self._text_value(payload, "scenario"),
            "mock_user.relation": self._text_value(payload, "relation"),
            "forWho_20": "100",
            "insured.forWho": "100",
            "relationInsureInsurant_20": "1",
        }
        overrides.update(self._mock_user_person_overrides("applicant", applicant))
        overrides.update(self._mock_user_person_overrides("insured", insured))

        name = self._text_value(applicant, "姓名", "name")
        id_type = self._text_value(applicant, "证件类型", "id_type", "idType")
        id_no = self._text_value(applicant, "证件号码", "身份证号", "id_no", "idNo")
        mobile = self._text_value(applicant, "手机号", "手机号码", "mobile", "phone")
        email = self._text_value(applicant, "邮箱", "email")
        bank_name = self._text_value(applicant, "银行", "bank", "bankName") or "工商银行"
        bank_account = self._text_value(applicant, "银行卡号", "银行卡", "bank_card", "bankCard", "card_no")
        bank_value = self._bank_value_for_name(bank_name)

        form_fields = {
            "insure_form.applicantname": name,
            "insure_form.applicantidno": id_no,
            "insure_form.applicantphone": mobile,
            "insure_form.applicantemail": email,
            "cardOwner_107": name,
            "bankName_107": bank_name,
            "openBank_107": bank_name,
            "bankValue_107": bank_value,
            "bankControlValue_107": bank_value,
            "bank_107": bank_value,
            "payAccount_107": bank_account,
            "bankAccountPair_107": "|".join(part for part in [bank_name, bank_value, bank_account] if part),
        }
        if id_type:
            id_type_code = "2" if id_type == "护照" else "1"
            form_fields.update(
                {
                    "insure_form.cardtype": id_type,
                    "insure_form.insuredidtype": id_type,
                    "applicant.id_type": id_type,
                    "applicant.id_type_code": id_type_code,
                    "insured.id_type": id_type,
                    "insured.id_type_code": id_type_code,
                }
            )
        overrides.update({key: value for key, value in form_fields.items() if value})
        return {key: value for key, value in overrides.items() if value}

    def _mock_user_person_overrides(self, prefix: str, person: object) -> dict[str, str]:
        if not isinstance(person, dict):
            return {}
        name = self._text_value(person, "姓名", "name")
        mobile = self._text_value(person, "手机号", "手机号码", "mobile", "phone")
        birthdate = self._text_value(person, "出生日期", "birthdate", "birthday")
        id_type = self._text_value(person, "证件类型", "id_type", "idType")
        id_type_code = "2" if id_type == "护照" else "1" if id_type else ""
        fields = {
            "name": name,
            "english_name": self._english_name_for(prefix, name),
            "id_type": id_type,
            "id_type_code": id_type_code,
            "id_no": self._text_value(person, "证件号码", "身份证号", "id_no", "idNo"),
            "mobile": mobile,
            "phone": mobile,
            "email": self._text_value(person, "邮箱", "email"),
            "gender": self._text_value(person, "性别", "gender"),
            "birthdate": birthdate,
            "birthday": birthdate,
            "address": self._text_value(person, "地址", "联系地址", "address"),
            "region": self._text_value(person, "居住省市", "居住地区", "region", "region_text"),
            "card_valid_start": self._text_value(person, "证件有效期(起始)", "证件有效期起始", "card_valid_start"),
            "card_valid_end": self._text_value(person, "证件有效期(截止)", "证件有效期截止", "card_valid_end"),
            "height": self._text_value(person, "身高(cm)", "身高", "height"),
            "weight": self._text_value(person, "体重(kg)", "体重", "weight"),
            "annual_income": self._text_value(person, "年收入(万元)", "年收入", "annual_income"),
        }
        return {f"{prefix}.{key}": value for key, value in fields.items() if value}

    def _text_value(self, data: object, *keys: str) -> str:
        if not isinstance(data, dict):
            return ""
        for key in keys:
            value = data.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
        return ""

    def _english_name_for(self, prefix: str, name: str) -> str:
        ascii_name = re.sub(r"[^A-Za-z]+", "", name or "").lower()
        if len(ascii_name) >= 2:
            return ascii_name[:60]
        return "agentinsured" if prefix == "insured" else "agentapplicant"

    def _bank_value_for_name(self, bank_name: str) -> str:
        return "1" if "工商" in str(bank_name or "") else "1"

    def _formal_report_slug(self, spec: Path) -> str:
        stem = spec.stem or "spec"
        if stem.endswith(".spec"):
            stem = stem[: -len(".spec")]
        slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", stem).strip("-._")
        return slug or "spec"

    def _is_h5_spec(self, spec: Path) -> bool:
        normalized_path = spec.as_posix().lower().replace("\\", "/")
        if "/h5/" in normalized_path or normalized_path.endswith("-h5.spec.ts") or "-h5-" in normalized_path:
            return True
        if spec.exists():
            try:
                content = spec.read_text(encoding="utf-8", errors="ignore")[:80_000].lower()
            except OSError:
                content = ""
            return "/m/apps/" in content or ("h5" in content and "/product/detail" in content)
        return False

    def _write_formal_config(self, spec: Path, report_dir: Path) -> str:
        spec_dir = spec.resolve().parent if spec.exists() else spec.parent.resolve()
        report_dir = report_dir.resolve()
        report_dir.mkdir(parents=True, exist_ok=True)
        config_dir = self.repo_root / ".tmp"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_path = config_dir / "playwright.formal.config.ts"
        is_h5_spec = self._is_h5_spec(spec)
        viewport_width = 390 if is_h5_spec else 1200
        viewport_height = 844 if is_h5_spec else 1080
        if is_h5_spec:
            mobile_context = (
                "    isMobile: true,\n"
                "    hasTouch: true,\n"
                "    deviceScaleFactor: 3,\n"
                "    userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
                "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1',\n"
            )
        else:
            mobile_context = ""
        channel_line = (
            "        ...(process.env.PLAYWRIGHT_CHROMIUM_CHANNEL\n"
            "          ? { channel: process.env.PLAYWRIGHT_CHROMIUM_CHANNEL as any }\n"
            "          : { channel: 'chrome' as const }),\n"
        )
        config = f"""import {{ defineConfig }} from '@playwright/test';
import path from 'path';

const reportDir = {json.dumps(str(report_dir), ensure_ascii=False)};

export default defineConfig({{
  testDir: {json.dumps(str(spec_dir), ensure_ascii=False)},
  timeout: 720_000,
  retries: 0,
  workers: 1,
  reporter: [
    ['list'],
    ['html', {{
      outputFolder: path.join(reportDir, 'reports'),
      open: 'never',
    }}],
  ],
  use: {{
    headless: !process.env.HEADED,
    viewport: {{ width: {viewport_width}, height: {viewport_height} }},
{mobile_context}    locale: 'zh-CN',
    screenshot: 'only-on-failure',
    video: {{
      mode: 'on',
      size: {{ width: {viewport_width}, height: {viewport_height} }},
    }},
    trace: 'on',
  }},
  projects: [
    {{
      name: 'chromium',
      use: {{
        browserName: 'chromium',
{channel_line}      }},
    }},
  ],
  outputDir: path.join(reportDir, 'test-results'),
}});
"""
        config_path.write_text(config, encoding="utf-8")
        return str(config_path)

    def list_spec_tests(
        self,
        spec_path: str,
        timeout_seconds: int = 60,
    ) -> dict:
        """Run Playwright discovery without executing the scenario."""
        command_prefix = self._resolve_playwright_command()
        if command_prefix is None:
            raise FileNotFoundError("Playwright CLI is not available")

        spec = Path(spec_path)
        if not spec.is_absolute():
            spec = self.repo_root / spec
        run_cwd = spec.parent if spec.exists() else self._command_cwd()
        spec_arg = spec.name if run_cwd == spec.parent else str(spec)
        cmd = [*command_prefix, "test", spec_arg, "--list"]
        result = subprocess.run(
            cmd,
            cwd=str(run_cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
        )
        output = "\n".join(part for part in (result.stdout, result.stderr) if part)
        match = re.search(r"Total:\s+(\d+)\s+tests?", output)
        listed = int(match.group(1)) if match else 0
        errors = []
        if result.returncode != 0:
            errors.append((result.stderr or result.stdout or "Playwright spec discovery failed").strip())
        return {
            "returncode": result.returncode,
            "listed": listed,
            "errors": [error for error in errors if error],
            "raw_output": result.stdout or "",
            "stderr": result.stderr or "",
            "command": cmd,
            "cwd": str(run_cwd),
        }

    def _parse_json_reporter(self, stdout: str) -> dict:
        """Parse the JSON reporter output from Playwright CLI."""
        start = stdout.find("{")
        end = stdout.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(stdout[start : end + 1])
                return self._parse_json_report(data)
            except json.JSONDecodeError:
                pass

        lines = stdout.splitlines()
        # Playwright JSON reporter outputs valid JSON on its own line
        for line in reversed(lines):
            line = line.strip()
            if line.startswith("{") and '"stats"' in line:
                try:
                    return self._parse_json_report(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return {"passed": 0, "failed": 0, "errors": []}

    def _parse_text_reporter(self, stdout: str, stderr: str, returncode: int) -> dict:
        """Parse Playwright's human reporter well enough for Agent4 summaries."""
        output = "\n".join(part for part in (stdout, stderr) if part)
        passed = 0
        failed = 0
        pass_match = re.search(r"(\d+)\s+passed", output)
        fail_match = re.search(r"(\d+)\s+failed", output)
        if pass_match:
            passed = int(pass_match.group(1))
        if fail_match:
            failed = int(fail_match.group(1))
        if not passed and not failed:
            if returncode == 0:
                passed = 1
            else:
                failed = 1

        errors: list[str] = []
        if returncode != 0:
            lines = [line.strip() for line in output.splitlines() if line.strip()]
            for line in lines:
                if re.search(r"Error:|Timeout|failed|expect\(", line, re.IGNORECASE):
                    errors.append(line)
            if not errors and output.strip():
                errors.append(output.strip()[-2000:])

        return {
            "passed": passed,
            "failed": failed,
            "errors": list(dict.fromkeys(errors)),
        }

    def _parse_json_report(self, data: dict) -> dict:
        stats = data.get("stats", {})
        errors = [
            str(item.get("message") or "")
            for item in data.get("errors", [])
            if str(item.get("message") or "").strip()
        ]

        def iter_specs(suites: list[dict]) -> list[dict]:
            specs: list[dict] = []
            for suite in suites:
                specs.extend(suite.get("specs", []) or [])
                specs.extend(iter_specs(suite.get("suites", []) or []))
            return specs

        for spec in iter_specs(data.get("suites", []) or []):
            for test in spec.get("tests", []) or []:
                for result in test.get("results", []) or []:
                    if result.get("status") in {"failed", "timedOut", "interrupted"}:
                        error = result.get("error") or {}
                        message = str(error.get("message") or "")
                        if message.strip():
                            errors.append(message)
        return {
            "passed": stats.get("expected", 0),
            "failed": stats.get("unexpected", 0),
            "errors": list(dict.fromkeys(errors + self._collect_error_messages(data.get("suites", [])))),
        }

    def _collect_error_messages(self, value: object) -> list[str]:
        messages: list[str] = []
        if isinstance(value, dict):
            error = value.get("error")
            if isinstance(error, dict) and str(error.get("message", "")).strip():
                messages.append(str(error["message"]))
            for child in value.values():
                messages.extend(self._collect_error_messages(child))
        elif isinstance(value, list):
            for item in value:
                messages.extend(self._collect_error_messages(item))
        return messages

    def check_node_available(self) -> bool:
        """Returns True if a runnable Playwright CLI is available."""
        return self._resolve_playwright_command() is not None

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any
from urllib import error, parse, request

from ..base import ExecutionPlan, ExecutionResult


class ApiRunner:
    """Execute declarative HTTP scenarios with the Python standard library."""

    name = "api"

    async def execute(self, plan: ExecutionPlan) -> ExecutionResult:
        started = time.monotonic()
        results = await asyncio.gather(
            *(asyncio.to_thread(self._execute_scenario, scenario, plan) for scenario in plan.scenarios)
        )
        passed = sum(item["passed"] for item in results)
        failed = len(results) - passed
        failures = [item["failure"] for item in results if item.get("failure")]
        artifacts = [item["artifact"] for item in results if item.get("artifact")]
        return ExecutionResult(
            run_id=plan.id,
            runner=self.name,
            status="passed" if failed == 0 else "failed",
            summary={
                "passed": passed,
                "failed": failed,
                "skipped": 0,
                "duration_ms": int((time.monotonic() - started) * 1000),
            },
            failures=failures,
            artifacts=artifacts,
            metrics={"scenario_count": len(results)},
        )

    def collect_artifacts(self, result: ExecutionResult) -> list[dict[str, Any]]:
        return list(result.artifacts)

    def _execute_scenario(self, scenario: dict[str, Any], plan: ExecutionPlan) -> dict[str, Any]:
        scenario_id = str(scenario.get("id") or scenario.get("scenario_id") or "api-scenario")
        req_spec = scenario.get("request") or scenario
        expected = scenario.get("expected") or {}
        method = str(req_spec.get("method") or "GET").upper()
        url = self._resolve_url(str(req_spec.get("url") or req_spec.get("path") or ""), plan)
        headers = {str(key): str(value) for key, value in (req_spec.get("headers") or {}).items()}
        body = req_spec.get("body")
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8") if not isinstance(body, str) else body.encode("utf-8")
            headers.setdefault("Content-Type", "application/json")
        timeout = float(req_spec.get("timeout_seconds") or plan.env.get("timeout_seconds") or 30)
        started = time.monotonic()
        response_status = 0
        response_headers: dict[str, str] = {}
        response_body = ""
        network_error: str | None = None
        try:
            req = request.Request(url, method=method, headers=headers, data=data)
            with request.urlopen(req, timeout=timeout) as response:  # noqa: S310 - test endpoint is user configured
                response_status = int(response.status)
                response_headers = dict(response.headers.items())
                response_body = response.read().decode("utf-8", errors="replace")
        except error.HTTPError as exc:
            response_status = int(exc.code)
            response_headers = dict(exc.headers.items()) if exc.headers else {}
            response_body = exc.read().decode("utf-8", errors="replace")
        except (error.URLError, TimeoutError, OSError) as exc:
            network_error = str(exc)

        parsed_body = self._parse_body(response_body, response_headers)
        expected_status = int(expected.get("status") or expected.get("status_code") or 200)
        checks: list[dict[str, Any]] = []
        status_ok = network_error is None and response_status == expected_status
        checks.append({"name": "status", "passed": status_ok, "actual": response_status, "expected": expected_status})
        for path, expected_value in (expected.get("json") or {}).items():
            actual_value, found = self._json_path(parsed_body, str(path))
            checks.append(
                {
                    "name": f"json:{path}",
                    "passed": found and actual_value == expected_value,
                    "actual": actual_value if found else None,
                    "expected": expected_value,
                }
            )
        contains = expected.get("body_contains")
        if contains is not None:
            checks.append(
                {
                    "name": "body_contains",
                    "passed": str(contains) in response_body,
                    "actual": response_body,
                    "expected": contains,
                }
            )

        passed = network_error is None and all(bool(item["passed"]) for item in checks)
        response_record = {
            "scenario_id": scenario_id,
            "request": {"method": method, "url": url},
            "response": {
                "status": response_status,
                "headers": response_headers,
                "body": parsed_body,
            },
            "checks": checks,
            "passed": passed,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "network_error": network_error,
        }
        artifact = self._write_artifact(plan, scenario_id, response_record)
        failure = None
        if not passed:
            failure = {
                "id": scenario_id,
                "scenario_id": scenario_id,
                "category": "network_error" if network_error else "assertion_failed",
                "message": network_error or f"API checks failed: {[item for item in checks if not item['passed']]}",
                "retryable": bool(network_error),
            }
        return {"passed": passed, "failure": failure, "artifact": artifact}

    @staticmethod
    def _resolve_url(value: str, plan: ExecutionPlan) -> str:
        if value.startswith(("http://", "https://")):
            return value
        base_url = str(plan.fixtures.get("base_url") or plan.env.get("base_url") or "")
        if not base_url:
            raise ValueError(f"API scenario URL is relative but no base_url was supplied: {value}")
        return parse.urljoin(base_url.rstrip("/") + "/", value.lstrip("/"))

    @staticmethod
    def _parse_body(body: str, headers: dict[str, str]) -> Any:
        content_type = next((value for key, value in headers.items() if key.lower() == "content-type"), "")
        if body and ("json" in content_type.lower() or body.lstrip().startswith(("{", "["))):
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                return body
        return body

    @staticmethod
    def _json_path(payload: Any, path: str) -> tuple[Any, bool]:
        current = payload
        for segment in path.strip("$.").split(".") if path.strip("$.") else []:
            if isinstance(current, dict) and segment in current:
                current = current[segment]
            elif isinstance(current, list) and segment.isdigit() and int(segment) < len(current):
                current = current[int(segment)]
            else:
                return None, False
        return current, True

    @staticmethod
    def _write_artifact(plan: ExecutionPlan, scenario_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        if not plan.artifacts_dir:
            return None
        target = Path(plan.artifacts_dir) / "api" / f"{_safe_name(scenario_id)}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"path": str(target), "kind": "api-response", "contract": None}


def _safe_name(value: str) -> str:
    return "".join(character if character.isalnum() or character in {"-", "_"} else "-" for character in value)

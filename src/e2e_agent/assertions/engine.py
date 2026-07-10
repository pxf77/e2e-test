from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

_MISSING = object()


@dataclass(frozen=True)
class AssertionCheckResult:
    template_id: str
    operator: str
    status: str
    passed: bool | None
    message: str
    actual: Any = None
    expected: Any = None


@dataclass(frozen=True)
class AssertionResult:
    template_id: str
    passed: bool
    message: str = ""
    details: dict[str, Any] | None = None


class AssertionEngine:
    """Execute domain Assertion Pack templates against a runtime context."""

    def __init__(self, assertion_pack: dict[str, Any]) -> None:
        self.assertion_pack = assertion_pack
        self.templates: dict[str, dict[str, Any]] = assertion_pack.get("templates") or {}

    def match_templates(self, *, page_types: list[str] | None = None, text: str = "") -> list[str]:
        page_type_set = {str(item) for item in page_types or []}
        haystack = text.lower()
        matched: list[str] = []
        for template_id, template in self.templates.items():
            match = template.get("match") or {}
            configured_types = {str(item) for item in match.get("page_types") or []}
            keywords = [
                str(item).lower()
                for item in (
                    match.get("keywords")
                    or match.get("assertion_keywords")
                    or match.get("route_keywords")
                    or []
                )
            ]
            page_ok = not configured_types or bool(page_type_set & configured_types)
            keyword_ok = not keywords or any(keyword in haystack for keyword in keywords)
            if page_ok and keyword_ok:
                matched.append(str(template_id))
        return matched

    def evaluate_literal(self, template_id: str, actuals: dict[str, Any]) -> list[AssertionResult]:
        """Backward-compatible literal API used by existing callers."""
        checks = self.evaluate_template(template_id, actuals)
        return [
            AssertionResult(
                template_id=item.template_id,
                passed=item.passed is True,
                message=item.message,
                details={
                    "operator": item.operator,
                    "status": item.status,
                    "actual": item.actual,
                    "expected": item.expected,
                },
            )
            for item in checks
        ]

    def evaluate_template(self, template_id: str, context: dict[str, Any]) -> list[AssertionCheckResult]:
        template = self.templates[template_id]
        results: list[AssertionCheckResult] = []
        for check in template.get("checks") or []:
            operator = str(check.get("operator") or "")
            actual = self._resolve(check.get("actual"), context)
            expected = self._resolve(check.get("expected"), context)
            message = str(check.get("message") or operator)
            if actual is _MISSING:
                results.append(
                    AssertionCheckResult(
                        template_id=template_id,
                        operator=operator,
                        status="skipped",
                        passed=None,
                        message=f"{message}: actual value unavailable",
                        actual=None,
                        expected=None if expected is _MISSING else expected,
                    )
                )
                continue
            if expected is _MISSING:
                expected = check.get("expected")
            try:
                outcome = self._evaluate_operator(operator, actual, expected, template, context)
            except (TypeError, ValueError, KeyError, re.error) as exc:
                results.append(
                    AssertionCheckResult(
                        template_id=template_id,
                        operator=operator,
                        status="error",
                        passed=False,
                        message=f"{message}: {exc}",
                        actual=actual,
                        expected=expected,
                    )
                )
                continue
            if outcome is None:
                status = "skipped"
                passed = None
            else:
                status = "passed" if outcome else "failed"
                passed = outcome
            results.append(
                AssertionCheckResult(
                    template_id=template_id,
                    operator=operator,
                    status=status,
                    passed=passed,
                    message=message,
                    actual=actual,
                    expected=expected,
                )
            )
        return results

    def run(
        self,
        *,
        page_types: list[str] | None,
        text: str,
        context: dict[str, Any],
        template_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        selected = template_ids or self.match_templates(page_types=page_types, text=text)
        checks: list[AssertionCheckResult] = []
        for template_id in selected:
            if template_id not in self.templates:
                raise KeyError(f"Unknown assertion template: {template_id}")
            checks.extend(self.evaluate_template(template_id, context))
        serialized = [asdict(item) for item in checks]
        summary = {
            "templates": len(selected),
            "checks": len(serialized),
            "passed": sum(item["status"] == "passed" for item in serialized),
            "failed": sum(item["status"] in {"failed", "error"} for item in serialized),
            "skipped": sum(item["status"] == "skipped" for item in serialized),
        }
        return {
            "pack_id": self.assertion_pack.get("id"),
            "pack_version": self.assertion_pack.get("version"),
            "template_ids": selected,
            "summary": summary,
            "checks": serialized,
            "status": "failed" if summary["failed"] else ("passed" if summary["passed"] else "skipped"),
        }

    @staticmethod
    def _resolve(value: Any, context: dict[str, Any]) -> Any:
        if not isinstance(value, str) or not value.startswith("${") or not value.endswith("}"):
            return value
        path = value[2:-1].strip()
        current: Any = context
        for segment in path.split("."):
            if isinstance(current, dict) and segment in current:
                current = current[segment]
            elif isinstance(current, list) and segment.isdigit() and int(segment) < len(current):
                current = current[int(segment)]
            else:
                return _MISSING
        return current

    @classmethod
    def _evaluate_operator(
        cls,
        operator: str,
        actual: Any,
        expected: Any,
        template: dict[str, Any],
        context: dict[str, Any],
    ) -> bool | None:
        if operator in {"exists", "visible"}:
            return actual is not None and actual is not False
        if operator == "text_equals":
            return str(actual) == str(expected)
        if operator == "text_contains":
            return str(expected) in str(actual)
        if operator == "url_matches":
            return re.search(str(expected), str(actual)) is not None
        if operator in {"status_in", "in"}:
            return actual in (expected or [])
        if operator == "number_equals":
            return float(actual) == float(expected)
        if operator == "number_between":
            bounds = expected or {}
            value = float(actual)
            if "min" in bounds and value < float(bounds["min"]):
                return False
            if "max" in bounds and value > float(bounds["max"]):
                return False
            return True
        if operator == "transition_allowed":
            transitions = template.get("valid_transitions") or {}
            previous = expected
            return actual in (transitions.get(str(previous)) or [])
        if operator == "business_rule":
            return cls._evaluate_business_rule(actual, expected, context)
        return None

    @staticmethod
    def _evaluate_business_rule(actual: Any, expected: Any, context: dict[str, Any]) -> bool | None:
        if isinstance(expected, bool):
            return bool(actual) is expected
        if isinstance(expected, (int, float, list, dict)):
            return actual == expected
        rule = str(expected or "")
        if rule.startswith("required_when(") and rule.endswith(")"):
            condition = rule[len("required_when(") : -1].strip()
            match = re.fullmatch(r"([A-Za-z0-9_.]+)\s*==\s*['\"]([^'\"]+)['\"]", condition)
            if not match:
                return None
            left = AssertionEngine._resolve("${" + match.group(1) + "}", context)
            if left is _MISSING or str(left) != match.group(2):
                return True
            return actual not in {None, "", False}
        return None

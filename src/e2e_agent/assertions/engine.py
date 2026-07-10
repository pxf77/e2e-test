from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AssertionResult:
    template_id: str
    passed: bool
    message: str = ""
    details: dict[str, Any] | None = None


class AssertionEngine:
    """Deterministic assertion-pack helper.

    The foundation implementation focuses on template discovery and simple
    literal operators. Business-rule execution remains delegated to domain
    packs or future plugins.
    """

    def __init__(self, assertion_pack: dict[str, Any]) -> None:
        self.assertion_pack = assertion_pack
        self.templates: dict[str, dict[str, Any]] = assertion_pack.get("templates") or {}

    def match_templates(self, *, page_type: str, text: str = "") -> list[str]:
        haystack = text.lower()
        matched: list[str] = []
        for template_id, template in self.templates.items():
            match = template.get("match") or {}
            page_types = [str(item) for item in match.get("page_types") or []]
            keywords = [str(item).lower() for item in match.get("keywords") or match.get("assertion_keywords") or []]
            page_ok = not page_types or page_type in page_types
            keyword_ok = not keywords or any(keyword in haystack for keyword in keywords)
            if page_ok and keyword_ok:
                matched.append(str(template_id))
        return matched

    def evaluate_literal(self, template_id: str, actuals: dict[str, Any]) -> list[AssertionResult]:
        template = self.templates[template_id]
        results: list[AssertionResult] = []
        for check in template.get("checks") or []:
            operator = str(check.get("operator") or "")
            actual_key = str(check.get("actual") or "")
            expected = check.get("expected")
            actual = actuals.get(actual_key, actuals.get(actual_key.strip("${}")))
            passed = self._evaluate_operator(operator, actual, expected)
            results.append(
                AssertionResult(
                    template_id=template_id,
                    passed=passed,
                    message=str(check.get("message") or operator),
                    details={"operator": operator, "actual": actual, "expected": expected},
                )
            )
        return results

    @staticmethod
    def _evaluate_operator(operator: str, actual: Any, expected: Any) -> bool:
        if operator in {"exists", "visible"}:
            return actual is not None and actual is not False
        if operator == "text_equals":
            return str(actual) == str(expected)
        if operator == "text_contains":
            return str(expected) in str(actual)
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
        return False

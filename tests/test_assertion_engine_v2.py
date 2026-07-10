from __future__ import annotations

from e2e_agent.assertions import AssertionEngine


def test_assertion_engine_resolves_context_and_reports_failures() -> None:
    pack = {
        "id": "test-pack",
        "version": "1.0.0",
        "templates": {
            "price": {
                "name": "price",
                "category": "price",
                "match": {"page_types": ["cart"], "keywords": ["total"]},
                "checks": [
                    {
                        "operator": "number_equals",
                        "actual": "${business.actual_total}",
                        "expected": "${expected.total}",
                        "message": "cart total",
                    }
                ],
            }
        },
    }
    engine = AssertionEngine(pack)

    passed = engine.run(
        page_types=["cart"],
        text="verify total",
        context={"business": {"actual_total": 100}, "expected": {"total": 100}},
    )
    failed = engine.run(
        page_types=["cart"],
        text="verify total",
        context={"business": {"actual_total": 90}, "expected": {"total": 100}},
    )

    assert passed["status"] == "passed"
    assert passed["summary"]["passed"] == 1
    assert failed["status"] == "failed"
    assert failed["checks"][0]["actual"] == 90


def test_missing_actual_is_skipped_not_failed() -> None:
    pack = {
        "templates": {
            "visible": {
                "name": "visible",
                "category": "ui",
                "checks": [{"operator": "visible", "actual": "${page.selector}", "expected": True}],
            }
        }
    }

    report = AssertionEngine(pack).run(
        page_types=[],
        text="",
        context={"page": {}},
        template_ids=["visible"],
    )

    assert report["status"] == "skipped"
    assert report["summary"]["failed"] == 0
    assert report["checks"][0]["status"] == "skipped"


def test_required_when_business_rule() -> None:
    pack = {
        "templates": {
            "issued": {
                "name": "issued",
                "category": "business",
                "checks": [
                    {
                        "operator": "business_rule",
                        "actual": "${business.policy_no}",
                        "expected": "required_when(business.status == 'issued')",
                    }
                ],
            }
        }
    }

    report = AssertionEngine(pack).run(
        page_types=[],
        text="",
        context={"business": {"status": "issued", "policy_no": ""}},
        template_ids=["issued"],
    )

    assert report["status"] == "failed"

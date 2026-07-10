from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from .failure_taxonomy import normalize_failure


def write_report_bundle(report: dict[str, Any], run_dir: Path) -> list[dict[str, Any]]:
    report_dir = run_dir / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    normalized = _normalize_report(report)
    json_path = report_dir / "report.json"
    html_path = report_dir / "report.html"
    junit_path = report_dir / "junit.xml"
    json_path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
    html_path.write_text(_render_html(normalized), encoding="utf-8")
    ET.ElementTree(_render_junit(normalized)).write(junit_path, encoding="utf-8", xml_declaration=True)
    return [
        {"path": str(json_path), "kind": "json-report", "contract": "test-report@v1"},
        {"path": str(html_path), "kind": "html-report", "contract": None},
        {"path": str(junit_path), "kind": "junit-report", "contract": None},
    ]


def _normalize_report(report: dict[str, Any]) -> dict[str, Any]:
    result = dict(report)
    failures = [normalize_failure(item) for item in result.get("failures") or [] if isinstance(item, dict)]
    assertions = result.get("assertions") or {}
    for check in assertions.get("checks") or []:
        if isinstance(check, dict) and check.get("status") in {"failed", "error"}:
            failures.append(
                normalize_failure(
                    {
                        "id": f"assertion:{check.get('template_id', 'unknown')}:{check.get('operator', 'check')}",
                        "category": "business_rule_failed" if check.get("operator") == "business_rule" else "assertion_failed",
                        "message": check.get("message") or "Assertion failed",
                        "retryable": False,
                    }
                )
            )
    result["failures"] = failures
    summary = dict(result.get("summary") or {})
    summary.setdefault("passed", 0)
    summary.setdefault("failed", len(failures))
    summary.setdefault("skipped", 0)
    result["summary"] = summary
    result.setdefault("status", "failed" if int(summary["failed"]) else "passed")
    return result


def _render_html(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('id', 'failure')))}</td>"
        f"<td>{html.escape(str(item.get('category', 'unknown')))}</td>"
        f"<td>{html.escape(str(item.get('message', '')))}</td>"
        "</tr>"
        for item in report.get("failures") or []
    ) or '<tr><td colspan="3">No failures</td></tr>'
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>E2E Report</title>
<style>body{{font-family:system-ui;margin:2rem}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #ddd;padding:.5rem;text-align:left}}code{{background:#f4f4f4;padding:.15rem}}</style></head>
<body><h1>E2E Regression Report</h1>
<p><strong>Run:</strong> <code>{html.escape(str(report.get('run_id', '')))}</code></p>
<p><strong>Status:</strong> {html.escape(str(report.get('status', 'unknown')))}</p>
<ul><li>Passed: {int(summary.get('passed') or 0)}</li><li>Failed: {int(summary.get('failed') or 0)}</li><li>Skipped: {int(summary.get('skipped') or 0)}</li></ul>
<h2>Failures</h2><table><thead><tr><th>ID</th><th>Category</th><th>Message</th></tr></thead><tbody>{rows}</tbody></table>
</body></html>"""


def _render_junit(report: dict[str, Any]) -> ET.Element:
    summary = report.get("summary") or {}
    passed = int(summary.get("passed") or 0)
    failed = int(summary.get("failed") or 0)
    skipped = int(summary.get("skipped") or 0)
    suite = ET.Element(
        "testsuite",
        {
            "name": str(report.get("workflow_id") or "e2e-regression"),
            "tests": str(passed + failed + skipped),
            "failures": str(failed),
            "skipped": str(skipped),
        },
    )
    failures = report.get("failures") or []
    for index, item in enumerate(failures, start=1):
        case = ET.SubElement(suite, "testcase", {"name": str(item.get("id") or f"failure-{index}")})
        node = ET.SubElement(case, "failure", {"type": str(item.get("category") or "unknown")})
        node.text = str(item.get("message") or "failure")
    for index in range(max(passed, 0)):
        ET.SubElement(suite, "testcase", {"name": f"passed-{index + 1}"})
    for index in range(max(skipped, 0)):
        case = ET.SubElement(suite, "testcase", {"name": f"skipped-{index + 1}"})
        ET.SubElement(case, "skipped")
    return suite

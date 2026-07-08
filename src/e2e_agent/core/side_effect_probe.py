"""Core Side-effect Probe utilities.

The probe layer is intentionally transport-agnostic. Agent4 or a skill entry can
decide how to execute HTTP calls; this module owns request templating and result
normalisation so R4 can review a stable contract.
"""
from __future__ import annotations

from pathlib import Path
from string import Formatter
from typing import Any, Mapping
from urllib import error, parse, request

import yaml


def load_side_effect_probe_config(path: str | Path) -> dict[str, Any]:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return payload if isinstance(payload, dict) else {}


def _format_value(value: Any, variables: Mapping[str, Any]) -> Any:
    if isinstance(value, str):
        names = [field_name for _, field_name, _, _ in Formatter().parse(value) if field_name]
        if not names:
            return value
        try:
            return value.format(**{name: variables.get(name, "") for name in names})
        except (KeyError, ValueError):
            return value
    if isinstance(value, Mapping):
        return {str(key): _format_value(item, variables) for key, item in value.items()}
    if isinstance(value, list):
        return [_format_value(item, variables) for item in value]
    return value


def build_probe_request(
    probe: Mapping[str, Any],
    variables: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    variables = variables or {}
    return {
        "method": str(probe.get("method") or "GET").upper(),
        "url": str(_format_value(probe.get("url") or "", variables)),
        "headers": _format_value(probe.get("headers", {}) or {}, variables),
        "query": _format_value(probe.get("query", {}) or {}, variables),
        "json": _format_value(probe.get("json"), variables) if "json" in probe else None,
    }


def _field_value(payload: Any, path: str) -> Any:
    current = payload
    for part in path.split("."):
        if isinstance(current, Mapping):
            current = current.get(part)
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return current


def _error_downgrade_reason(error: Any) -> str:
    if not error:
        return ""
    if isinstance(error, str):
        return f"error: {error}"
    if isinstance(error, Mapping):
        error_type = str(error.get("type") or "error")
        message = str(error.get("message") or error.get("error") or "").strip()
        return f"{error_type}: {message}" if message else error_type
    return f"error: {error}"


def _normalise_expectation(expectation: Any) -> dict[str, Any]:
    if isinstance(expectation, Mapping):
        return dict(expectation)
    return {}


def evaluate_probe_response(
    probe: Mapping[str, Any],
    response: Any | None = None,
    *,
    error: Any | None = None,
) -> dict[str, Any]:
    probe_id = str(probe.get("probe_id") or probe.get("id") or "probe")
    if error is not None:
        return {
            "probe_id": probe_id,
            "status": "na",
            "evidence": {},
            "failures": [],
            "downgrade_reason": _error_downgrade_reason(error),
        }

    payload = response if response is not None else {}
    evidence_fields = [str(item) for item in probe.get("evidence_fields", []) or []]
    evidence = {field: _field_value(payload, field) for field in evidence_fields}
    failures: list[dict[str, Any]] = []
    for raw in probe.get("expect", []) or []:
        expectation = _normalise_expectation(raw)
        field = str(expectation.get("field") or "")
        if not field:
            continue
        actual = _field_value(payload, field)
        if "equals" in expectation and actual != expectation["equals"]:
            failures.append(
                {
                    "field": field,
                    "operator": "equals",
                    "expected": expectation["equals"],
                    "actual": actual,
                }
            )
        if "in" in expectation and actual not in (expectation.get("in") or []):
            failures.append(
                {
                    "field": field,
                    "operator": "in",
                    "expected": expectation.get("in") or [],
                    "actual": actual,
                }
            )

    return {
        "probe_id": probe_id,
        "status": "fail" if failures else "success",
        "evidence": evidence,
        "failures": failures,
        "downgrade_reason": None,
    }


def _probe_id(probe: Mapping[str, Any]) -> str:
    return str(probe.get("probe_id") or probe.get("id") or "probe")


def evaluate_side_effect_probe_results(
    probes: list[Mapping[str, Any]],
    *,
    responses: Mapping[str, Any] | None = None,
    errors: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate a probe batch using injected transport results.

    The function deliberately does not perform network I/O. Agent4 or a skill
    entry owns transport; this layer normalises results for stable R4 reporting.
    """
    responses = responses or {}
    errors = errors or {}
    results: list[dict[str, Any]] = []
    summary = {"total": 0, "success": 0, "fail": 0, "na": 0}

    for probe in probes:
        probe_id = _probe_id(probe)
        result = evaluate_probe_response(
            probe,
            responses.get(probe_id),
            error=errors.get(probe_id),
        )
        status = str(result.get("status") or "na")
        if status not in {"success", "fail", "na"}:
            status = "na"
            result["status"] = status
        summary["total"] += 1
        summary[status] += 1
        results.append(result)

    return {
        "summary": summary,
        "results": results,
    }


def _is_local_url(url: str) -> bool:
    parsed = parse.urlparse(url)
    return parsed.scheme in {"http", "https"} and parsed.hostname in {"127.0.0.1", "localhost", "::1"}


def _url_with_query(url: str, query: Mapping[str, Any]) -> str:
    if not query:
        return url
    encoded = parse.urlencode({key: value for key, value in query.items() if value is not None})
    if not encoded:
        return url
    separator = "&" if parse.urlparse(url).query else "?"
    return f"{url}{separator}{encoded}"


def _execute_probe_request(probe_request: Mapping[str, Any], timeout_s: float) -> Any:
    body = None
    headers = {str(key): str(value) for key, value in (probe_request.get("headers") or {}).items()}
    if probe_request.get("json") is not None:
        import json

        body = json.dumps(probe_request["json"]).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")
    url = _url_with_query(str(probe_request.get("url") or ""), probe_request.get("query") or {})
    req = request.Request(
        url,
        data=body,
        headers=headers,
        method=str(probe_request.get("method") or "GET").upper(),
    )
    class _NoRedirectHandler(request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
            raise error.HTTPError(req.full_url, code, msg, headers, fp)

    opener = request.build_opener(_NoRedirectHandler)
    with opener.open(req, timeout=timeout_s) as response:  # noqa: S310 - URL is restricted to local hosts.
        raw = response.read()
        if not raw:
            return {}
        content_type = response.headers.get("Content-Type", "")
        if "json" in content_type.lower():
            import json

            return json.loads(raw.decode("utf-8"))
        return {"body": raw.decode("utf-8", errors="replace")}


def execute_local_http_probe_transport(
    probes: list[Mapping[str, Any]],
    *,
    variables: Mapping[str, Any] | None = None,
    timeout_s: float = 5.0,
) -> dict[str, dict[str, Any]]:
    """Execute probes against localhost-only HTTP endpoints."""
    responses: dict[str, Any] = {}
    errors: dict[str, Any] = {}
    for probe in probes:
        probe_id = _probe_id(probe)
        probe_request = build_probe_request(probe, variables)
        url = str(probe_request.get("url") or "")
        if not _is_local_url(url):
            errors[probe_id] = {
                "type": "transport_not_allowed",
                "message": "local-http probe transport only allows localhost URLs",
            }
            continue
        try:
            responses[probe_id] = _execute_probe_request(probe_request, timeout_s)
        except error.HTTPError as exc:
            if 300 <= int(exc.code) < 400:
                errors[probe_id] = {"type": "redirect_not_allowed", "message": f"{exc.code} {exc.reason}"}
            else:
                errors[probe_id] = {"type": "http_error", "message": f"{exc.code} {exc.reason}"}
        except Exception as exc:  # pragma: no cover - exact transport failures are environment-specific.
            errors[probe_id] = {"type": "transport_error", "message": str(exc)}
    return {"responses": responses, "errors": errors}

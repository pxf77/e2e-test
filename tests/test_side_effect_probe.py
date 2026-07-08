from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from e2e_agent.core.side_effect_probe import (
    build_probe_request,
    evaluate_probe_response,
    evaluate_side_effect_probe_results,
    execute_local_http_probe_transport,
    load_side_effect_probe_config,
)


def test_load_side_effect_probe_config_reads_yaml(tmp_path: Path) -> None:
    config_path = tmp_path / "side-effect-probes.yaml"
    config_path.write_text(
        """
probes:
  - probe_id: order-query
    method: GET
    url: https://api.example.com/orders/{orderId}
""",
        encoding="utf-8",
    )

    config = load_side_effect_probe_config(config_path)

    assert config["probes"][0]["probe_id"] == "order-query"


def test_evaluate_probe_response_marks_success_with_evidence() -> None:
    result = evaluate_probe_response(
        {
            "probe_id": "underwriting-standard",
            "expect": [
                {"field": "code", "equals": 0},
                {"field": "data.canPay", "equals": True},
            ],
            "evidence_fields": ["code", "data.taskType", "data.canPay"],
        },
        {"code": 0, "data": {"taskType": 3, "canPay": True}},
    )

    assert result["probe_id"] == "underwriting-standard"
    assert result["status"] == "success"
    assert result["evidence"] == {"code": 0, "data.taskType": 3, "data.canPay": True}
    assert result["downgrade_reason"] is None


def test_evaluate_probe_response_marks_fail_for_business_mismatch() -> None:
    result = evaluate_probe_response(
        {
            "probe_id": "order-issued",
            "expect": [{"field": "data.orderStatus", "equals": "issued"}],
            "evidence_fields": ["data.orderStatus"],
        },
        {"data": {"orderStatus": "underwriting"}},
    )

    assert result["status"] == "fail"
    assert result["failures"][0]["actual"] == "underwriting"


def test_evaluate_probe_response_downgrades_permission_or_environment_errors_to_na() -> None:
    result = evaluate_probe_response(
        {"probe_id": "payment-query", "expect": [{"field": "code", "equals": 0}]},
        error={"type": "permission", "message": "missing backend permission"},
    )

    assert result["status"] == "na"
    assert result["downgrade_reason"] == "permission: missing backend permission"


def test_build_probe_request_fills_template_variables() -> None:
    request = build_probe_request(
        {
            "probe_id": "order-query",
            "method": "GET",
            "url": "https://api.example.com/orders/{orderId}",
            "headers": {"x-env": "{env}"},
            "query": {"policyNo": "{policyNo}"},
        },
        {"orderId": "O-001", "policyNo": "P-001", "env": "uat"},
    )

    assert request == {
        "method": "GET",
        "url": "https://api.example.com/orders/O-001",
        "headers": {"x-env": "uat"},
        "query": {"policyNo": "P-001"},
        "json": None,
    }


def test_evaluate_side_effect_probe_results_uses_injected_responses_and_errors() -> None:
    probes = [
        {
            "probe_id": "order-issued",
            "expect": [{"field": "data.orderStatus", "equals": "issued"}],
            "evidence_fields": ["data.orderStatus"],
        },
        {
            "probe_id": "underwriting-standard",
            "expect": [{"field": "data.canPay", "equals": True}],
            "evidence_fields": ["data.canPay"],
        },
        {
            "probe_id": "payment-query",
            "expect": [{"field": "code", "equals": 0}],
        },
    ]

    result = evaluate_side_effect_probe_results(
        probes,
        responses={
            "order-issued": {"data": {"orderStatus": "issued"}},
            "underwriting-standard": {"data": {"canPay": False}},
        },
        errors={
            "payment-query": {"type": "permission", "message": "missing permission"},
        },
    )

    assert result["summary"] == {"total": 3, "success": 1, "fail": 1, "na": 1}
    statuses = {item["probe_id"]: item["status"] for item in result["results"]}
    assert statuses == {
        "order-issued": "success",
        "underwriting-standard": "fail",
        "payment-query": "na",
    }


def test_execute_local_http_probe_transport_calls_local_json_endpoint() -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path != "/orders/O-001":
                self.send_response(404)
                self.end_headers()
                return
            payload = json.dumps({"data": {"orderStatus": "issued"}}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        transport = execute_local_http_probe_transport(
            [
                {
                    "probe_id": "order-issued",
                    "method": "GET",
                    "url": f"http://127.0.0.1:{server.server_port}/orders/{{orderId}}",
                }
            ],
            variables={"orderId": "O-001"},
        )
    finally:
        server.shutdown()
        server.server_close()

    assert transport["responses"] == {"order-issued": {"data": {"orderStatus": "issued"}}}
    assert transport["errors"] == {}


def test_execute_local_http_probe_transport_rejects_non_local_urls() -> None:
    transport = execute_local_http_probe_transport(
        [{"probe_id": "external", "url": "https://api.example.com/orders/O-001"}]
    )

    assert transport["responses"] == {}
    assert transport["errors"]["external"]["type"] == "transport_not_allowed"


def test_execute_local_http_probe_transport_rejects_external_redirect() -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(302)
            self.send_header("Location", "https://api.example.com/orders/O-001")
            self.end_headers()

        def log_message(self, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        transport = execute_local_http_probe_transport(
            [
                {
                    "probe_id": "redirect",
                    "method": "GET",
                    "url": f"http://127.0.0.1:{server.server_port}/redirect",
                }
            ]
        )
    finally:
        server.shutdown()
        server.server_close()

    assert transport["responses"] == {}
    assert transport["errors"]["redirect"]["type"] == "redirect_not_allowed"

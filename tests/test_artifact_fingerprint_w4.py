from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


def test_artifact_fingerprint_records_route_chain_and_cost(tmp_path) -> None:
    from jsonschema import Draft7Validator

    from e2e_agent.artifacts.fingerprint import append_artifact_fingerprint

    schema_path = Path(__file__).resolve().parents[1] / "schemas" / "artifact-fingerprint.schema.json"

    record = append_artifact_fingerprint(
        root_dir=tmp_path,
        product_id="demo-product",
        run_id="run-w4",
        artifact_path="products/demo-product/agent4/test-report.json",
        artifact_type="test-report",
        payload={"ok": True},
        producer="exec_healing_agent",
        model_routed="gemini/gemini-1.5-pro",
        model_primary="gpt-4o-mini",
        is_fallback=True,
        fallback_chain=["gpt-4o-mini", "gemini/gemini-1.5-pro"],
        token_usage={"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
        cost_usd=0.003,
    )

    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = sorted(Draft7Validator(schema).iter_errors(record), key=lambda err: err.path)

    assert errors == []
    assert record["fallback_chain"] == ["gpt-4o-mini", "gemini/gemini-1.5-pro"]
    assert record["cost_usd"] == 0.003


@pytest.mark.asyncio
async def test_llm_wrapper_exposes_last_route_metadata(tmp_path) -> None:
    import yaml

    from e2e_agent.llm.wrapper import LLMWrapper

    config_path = tmp_path / "model-routing.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "agents": {
                    "exec_healing": {
                        "primary": "primary-model",
                        "fallback": ["fallback-model"],
                    }
                },
                "litellm": {"request_timeout": 5, "num_retries": 0},
            }
        ),
        encoding="utf-8",
    )
    response = SimpleNamespace(
        model="fallback-model",
        usage=SimpleNamespace(
            prompt_tokens=10,
            completion_tokens=2,
            total_tokens=12,
        ),
        _hidden_params={"response_cost": 0.003},
        choices=[],
    )
    wrapper = LLMWrapper(config_path=config_path)

    with patch("litellm.acompletion", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = response
        await wrapper.call(
            agent_name="exec_healing",
            messages=[{"role": "user", "content": "classify"}],
        )

    assert wrapper.get_last_route_metadata() == {
        "agent_name": "exec_healing",
        "model_primary": "primary-model",
        "model_routed": "fallback-model",
        "is_fallback": True,
        "fallback_chain": ["primary-model", "fallback-model"],
        "token_usage": {
            "prompt_tokens": 10,
            "completion_tokens": 2,
            "total_tokens": 12,
        },
        "cost_usd": 0.003,
    }

"""Unit tests for LLMWrapper (mocked — no actual API calls)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import yaml
from jsonschema import Draft7Validator

pytest.importorskip("litellm")

from e2e_agent.llm.wrapper import LLMWrapper
from e2e_agent.llm.router import get_model_for_agent, list_agents


CONFIG_PATH = Path(__file__).parent.parent / "config" / "model-routing.yaml"


@pytest.fixture
def wrapper():
    return LLMWrapper(config_path=CONFIG_PATH)


def test_wrapper_loads_config(wrapper: LLMWrapper):
    assert "tc_merge" in wrapper._agents
    assert "path_extract" in wrapper._agents
    assert "explore" in wrapper._agents
    assert "exec_healing" in wrapper._agents


def test_get_primary_model(wrapper: LLMWrapper):
    assert wrapper.get_primary_model("tc_merge") == "gpt-5.5"
    assert wrapper.get_primary_model("path_extract") == "gpt-5.5"


def test_get_fallbacks(wrapper: LLMWrapper):
    fallbacks = wrapper.get_fallbacks("tc_merge")
    assert isinstance(fallbacks, list)
    assert len(fallbacks) >= 1


@pytest.mark.parametrize("route_key", ["tc_merge", "path_extract", "explore", "exec_healing"])
def test_routes_use_gpt55_high_and_gemini31_fallback(wrapper: LLMWrapper, route_key: str):
    primary = wrapper.get_primary_model(route_key)
    fallbacks = wrapper.get_fallbacks(route_key)

    assert primary == "gpt-5.5"
    assert fallbacks == ["gemini/gemini-3.1-pro"]
    assert wrapper.get_model_parameters(route_key) == {"reasoning_effort": "high"}


def test_model_routing_config_matches_schema():
    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    schema = yaml.safe_load((CONFIG_PATH.parent.parent / "schemas" / "model-routing.schema.json").read_text(encoding="utf-8"))
    errors = sorted(Draft7Validator(schema).iter_errors(config), key=lambda err: err.path)

    assert errors == []


def test_unknown_agent_raises(wrapper: LLMWrapper):
    with pytest.raises(ValueError, match="not found"):
        wrapper.get_primary_model("nonexistent_agent")


@pytest.mark.asyncio
async def test_call_routes_to_primary(wrapper: LLMWrapper):
    mock_response = AsyncMock()
    mock_response.choices = []

    with patch("litellm.acompletion", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = mock_response
        await wrapper.call(
            agent_name="tc_merge",
            messages=[{"role": "user", "content": "test"}],
        )
        call_kwargs = mock_llm.call_args.kwargs
        assert call_kwargs["model"] == "gpt-5.5"
        assert call_kwargs["reasoning_effort"] == "high"
        assert call_kwargs["drop_params"] is True


@pytest.mark.asyncio
async def test_call_passes_fallbacks(wrapper: LLMWrapper):
    mock_response = AsyncMock()
    with patch("litellm.acompletion", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = mock_response
        await wrapper.call(
            agent_name="tc_merge",
            messages=[{"role": "user", "content": "test"}],
        )
        call_kwargs = mock_llm.call_args.kwargs
        assert call_kwargs["fallbacks"] is not None
        assert len(call_kwargs["fallbacks"]) >= 1


@pytest.mark.asyncio
async def test_call_records_fallback_route_metadata(wrapper: LLMWrapper):
    mock_response = {
        "model": "gemini/gemini-3.1-pro",
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        "_hidden_params": {"response_cost": 0.02},
    }

    with patch("litellm.acompletion", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = mock_response
        await wrapper.call(
            agent_name="tc_merge",
            messages=[{"role": "user", "content": "test"}],
        )

    metadata = wrapper.get_last_route_metadata("tc_merge")
    assert metadata is not None
    assert metadata["model_primary"] == "gpt-5.5"
    assert metadata["model_routed"] == "gemini/gemini-3.1-pro"
    assert metadata["is_fallback"] is True
    assert metadata["fallback_chain"] == ["gpt-5.5", "gemini/gemini-3.1-pro"]
    assert metadata["token_usage"] == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    assert metadata["cost_usd"] == 0.02


def test_router_get_model():
    model = get_model_for_agent("explore", config_path=CONFIG_PATH)
    assert model == "gpt-5.5"


def test_router_list_agents():
    agents = list_agents(config_path=CONFIG_PATH)
    assert set(agents) == {"tc_merge", "path_extract", "explore", "exec_healing"}

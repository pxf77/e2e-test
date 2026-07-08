"""RULE-REG-9: LiteLLM unified wrapper — the ONLY LLM call point in business code.

All agent code must call LLMWrapper.call() instead of importing model SDKs directly.
Direct imports of anthropic / openai / google.generativeai are forbidden in src/.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import litellm
import yaml


_DEFAULT_CONFIG = Path(__file__).parent.parent.parent.parent / "config" / "model-routing.yaml"
_AGENT_MODEL_PARAMETER_KEYS = ("reasoning_effort",)


class LLMWrapper:
    """Thin wrapper around litellm.acompletion with model routing from YAML config."""

    def __init__(self, config_path: str | Path | None = None) -> None:
        resolved = Path(config_path) if config_path else _DEFAULT_CONFIG
        with open(resolved, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        self._agents: dict[str, dict] = cfg.get("agents", {})
        self._litellm_cfg: dict = cfg.get("litellm", {})
        self._last_route_metadata: dict[str, Any] | None = None

    def _get_agent_config(self, agent_name: str) -> dict:
        if agent_name not in self._agents:
            raise ValueError(
                f"Agent '{agent_name}' not found in model-routing.yaml. "
                f"Known agents: {list(self._agents)}"
            )
        return self._agents[agent_name]

    async def call(
        self,
        agent_name: str,
        messages: list[dict[str, str]],
        tools: list[dict] | None = None,
        **kwargs: Any,
    ) -> litellm.ModelResponse:
        """Call LiteLLM with routing from model-routing.yaml.

        Args:
            agent_name: Key in the 'agents' section of model-routing.yaml.
            messages: List of chat messages (role/content dicts).
            tools: Optional tool definitions (LiteLLM format).
            **kwargs: Forwarded to litellm.acompletion.

        Returns:
            LiteLLM ModelResponse.
        """
        cfg = self._get_agent_config(agent_name)
        primary = cfg["primary"]
        fallbacks: list[str] = cfg.get("fallback", [])
        model_parameters = self.get_model_parameters(agent_name)

        timeout = kwargs.pop("timeout", self._litellm_cfg.get("request_timeout", 120))
        num_retries = kwargs.pop("num_retries", self._litellm_cfg.get("num_retries", 2))
        for key, value in model_parameters.items():
            kwargs.setdefault(key, value)
        if "drop_params" in self._litellm_cfg:
            kwargs.setdefault("drop_params", bool(self._litellm_cfg["drop_params"]))

        response = await litellm.acompletion(
            model=primary,
            messages=messages,
            tools=tools or None,
            fallbacks=fallbacks or None,
            timeout=timeout,
            num_retries=num_retries,
            **kwargs,
        )
        model_routed = _response_model(response) or primary
        self._last_route_metadata = {
            "agent_name": agent_name,
            "model_primary": primary,
            "model_routed": model_routed,
            "is_fallback": model_routed != primary,
            "fallback_chain": [primary, *fallbacks],
            "token_usage": _response_usage(response),
            "cost_usd": _response_cost(response),
        }
        return response

    def get_primary_model(self, agent_name: str) -> str:
        return self._get_agent_config(agent_name)["primary"]

    def get_fallbacks(self, agent_name: str) -> list[str]:
        return self._get_agent_config(agent_name).get("fallback", [])

    def get_model_parameters(self, agent_name: str) -> dict[str, Any]:
        cfg = self._get_agent_config(agent_name)
        return {key: cfg[key] for key in _AGENT_MODEL_PARAMETER_KEYS if key in cfg}

    def get_last_route_metadata(self, agent_name: str | None = None) -> dict[str, Any] | None:
        """Return routing metadata captured from the most recent LiteLLM call."""
        if self._last_route_metadata is None:
            return None
        if agent_name is not None and self._last_route_metadata.get("agent_name") != agent_name:
            return None
        return dict(self._last_route_metadata)


def _response_model(response: Any) -> str | None:
    model = response.get("model") if isinstance(response, dict) else getattr(response, "model", None)
    return str(model) if model else None


def _usage_value(usage: Any, key: str) -> int | None:
    if usage is None:
        return None
    value = usage.get(key) if isinstance(usage, dict) else getattr(usage, key, None)
    return int(value) if value is not None else None


def _response_usage(response: Any) -> dict[str, int] | None:
    usage = response.get("usage") if isinstance(response, dict) else getattr(response, "usage", None)
    result = {
        key: value
        for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        if (value := _usage_value(usage, key)) is not None
    }
    return result or None


def _response_cost(response: Any) -> float | None:
    hidden = response.get("_hidden_params") if isinstance(response, dict) else getattr(response, "_hidden_params", None)
    candidates = [
        response.get("cost") if isinstance(response, dict) else getattr(response, "cost", None),
        response.get("cost_usd") if isinstance(response, dict) else getattr(response, "cost_usd", None),
        hidden.get("response_cost") if isinstance(hidden, dict) else None,
    ]
    for value in candidates:
        if value is not None:
            return float(value)
    return None

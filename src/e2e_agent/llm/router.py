"""Model routing helpers — used by tests and CLI tooling."""
from __future__ import annotations

from pathlib import Path

import yaml


def get_model_for_agent(agent_name: str, config_path: str | Path | None = None) -> str:
    """Returns the primary model name configured for the given agent."""
    from e2e_agent.llm.wrapper import _DEFAULT_CONFIG  # noqa: PLC0415

    resolved = Path(config_path) if config_path else _DEFAULT_CONFIG
    with open(resolved, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    agents = cfg.get("agents", {})
    if agent_name not in agents:
        raise ValueError(f"Agent '{agent_name}' not found in model-routing.yaml")
    return agents[agent_name]["primary"]


def list_agents(config_path: str | Path | None = None) -> list[str]:
    """Returns the list of configured agent names."""
    from e2e_agent.llm.wrapper import _DEFAULT_CONFIG  # noqa: PLC0415

    resolved = Path(config_path) if config_path else _DEFAULT_CONFIG
    with open(resolved, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return list(cfg.get("agents", {}).keys())

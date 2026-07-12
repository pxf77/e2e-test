from __future__ import annotations

from pathlib import Path

from e2e_agent.config.resolver import ConfigResolver
from e2e_agent.workflow import WorkflowRuntime

ROOT = Path(__file__).resolve().parents[2]


def test_config_resolver_applies_precedence(tmp_path: Path) -> None:
    env_path = tmp_path / "staging.yaml"
    env_path.write_text("runner:\n  retries: 2\nfeature:\n  value: env\n", encoding="utf-8")
    app = {
        "config": {"runner": {"timeout": 30, "retries": 1}, "feature": {"value": "app"}},
        "execution": {"environments": {"staging": "staging.yaml"}},
    }
    domain = {"config": {"runner": {"timeout": 20}, "domain_only": True}}

    result = ConfigResolver().resolve(
        defaults={"runner": {"timeout": 10, "browser": "chromium"}},
        domain=domain,
        app=app,
        app_root=tmp_path,
        env="staging",
        runtime={"runner": {"retries": 3}},
    )

    assert result["runner"] == {"timeout": 30, "browser": "chromium", "retries": 3}
    assert result["feature"]["value"] == "env"
    assert result["domain_only"] is True
    assert result["environment"] == "staging"


def test_runtime_exposes_effective_config() -> None:
    state = WorkflowRuntime(repo_root=ROOT).prepare_state(
        app_path=Path("apps/demo-generic-form/app.yaml"),
        workflow="smoke-static-web",
        inputs={"config_overrides": {"runner": {"headless": False}}},
        run_id="config-runtime",
    )

    assert state["config"]["runner"]["default"] == "playwright"
    assert state["config"]["runner"]["headless"] is False
    assert state["config"]["workflow"]["id"] == "smoke-static-web"

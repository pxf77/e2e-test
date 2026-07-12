from __future__ import annotations

import json
from pathlib import Path

import pytest

from e2e_agent.data import DataResolver
from e2e_agent.workflow import WorkflowRuntime

ROOT = Path(__file__).resolve().parents[2]


def test_secret_ref_is_redacted(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("E2E_TEST_PASSWORD", "super-secret-value")
    pack = {
        "profiles": {
            "login_secret": {
                "provider": "secret_ref",
                "secret": "E2E_TEST_PASSWORD",
                "field": "password",
            }
        }
    }

    resolution = DataResolver().resolve(pack, ["login_secret"], base_dir=tmp_path)

    assert resolution.actual["login_secret"]["password"] == "super-secret-value"
    assert resolution.public["login_secret"]["password"] == "***REDACTED***"
    assert resolution.metadata["profiles"]["login_secret"]["sensitive"] is True


def test_faker_provider_is_deterministic(tmp_path: Path) -> None:
    pack = {
        "profiles": {
            "user": {
                "provider": "faker",
                "seed": "fixed",
                "fields": {"name": "person.name", "email": "internet.email", "phone": "phone_number"},
            }
        }
    }

    first = DataResolver().resolve(pack, ["user"], base_dir=tmp_path)
    second = DataResolver().resolve(pack, ["user"], base_dir=tmp_path)

    assert first.actual == second.actual
    assert "***@example.test" in first.public["user"]["email"]
    assert "****" in first.public["user"]["phone"]


@pytest.mark.asyncio
async def test_workflow_keeps_actual_data_out_of_run_context(tmp_path: Path) -> None:
    runtime = WorkflowRuntime(repo_root=ROOT)
    result = await runtime.run(
        app_path=Path("apps/demo-generic-form/app.yaml"),
        workflow="smoke-static-web",
        run_id="data-isolation-test",
        inputs={
            "data_profiles": ["random_user"],
            "assertion_templates": ["visible_element"],
            "assertion_context": {"page": {"selector": "#main"}},
        },
        metadata={"artifacts_dir": str(tmp_path / "run"), "gate_checkpoint_dir": ""},
    )

    raw_email = result["runtime_data"]["test_data"]["random_user"]["email"]
    assert raw_email.endswith("@example.test")
    assert "***@example.test" in result["artifacts"]["test_data"]["random_user"]["email"]
    assert result["artifacts"]["assertion_report"]["status"] == "passed"
    run_context_text = (tmp_path / "run" / "run-context.json").read_text(encoding="utf-8")
    run_context = json.loads(run_context_text)
    assert "runtime_data" not in run_context
    assert raw_email not in run_context_text

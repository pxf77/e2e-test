from __future__ import annotations

from pathlib import Path
from typing import Any

from e2e_agent.browser.runner import PlaywrightTSRunner

from ..base import ExecutionPlan, ExecutionResult


class PlaywrightRunner:
    """v2 adapter around the existing Playwright TypeScript runner."""

    name = "playwright"

    def __init__(self, repo_root: Path | None = None) -> None:
        self.repo_root = repo_root or Path(__file__).resolve().parents[4]

    def prepare(self, context: Any) -> ExecutionPlan:
        run_id = getattr(context, "run_id", "manual")
        scenarios = []
        if hasattr(context, "get_artifact"):
            scenarios = context.get_artifact("scenarios", []) or []
        return ExecutionPlan(id=f"{run_id}-playwright", runner=self.name, scenarios=list(scenarios))

    async def execute(self, plan: ExecutionPlan) -> ExecutionResult:
        """Execute generated TypeScript specs when supplied by the plan.

        This adapter is deliberately thin in the foundation PR. The current
        production path can keep using ``PlaywrightTSRunner`` directly while
        new workflow code depends on the generic ``ExecutionRunner`` contract.
        """
        spec_path = plan.fixtures.get("spec_path") if isinstance(plan.fixtures, dict) else None
        if not spec_path:
            return ExecutionResult(
                run_id=plan.id,
                runner=self.name,
                status="skipped",
                summary={"passed": 0, "failed": 0, "skipped": len(plan.scenarios)},
                metrics={"reason": "no spec_path supplied"},
            )
        runner = PlaywrightTSRunner(repo_root=self.repo_root)
        result = runner.run_spec(str(spec_path))
        failed = int(result.get("failed") or 0)
        passed = int(result.get("passed") or 0)
        ok = int(result.get("returncode") or 0) == 0 and failed == 0
        return ExecutionResult(
            run_id=plan.id,
            runner=self.name,
            status="passed" if ok else "failed",
            summary={"passed": passed, "failed": failed or (0 if ok else 1), "skipped": 0},
            failures=[] if ok else [{"id": "playwright", "category": "runner_error", "message": str(result)}],
            artifacts=[],
            metrics=result,
        )

    def collect_artifacts(self, result: ExecutionResult) -> list[dict[str, Any]]:
        return list(result.artifacts)

from __future__ import annotations

from pathlib import Path
from typing import Any

from e2e_agent.artifacts import collect_files
from e2e_agent.legacy.browser.runner import PlaywrightTSRunner

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
        """Execute a generated TypeScript spec and return runner-neutral output."""
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
        formal_execution = bool(plan.fixtures.get("formal_execution"))
        artifact_root = Path(plan.artifacts_dir) if plan.artifacts_dir else None
        if formal_execution:
            report_dir = artifact_root / "playwright-raw" if artifact_root else None
            result = runner.run_formal_spec(str(spec_path), report_dir=report_dir)
        else:
            result = runner.run_spec(str(spec_path))

        failed = int(result.get("failed") or 0)
        passed = int(result.get("passed") or 0)
        ok = int(result.get("returncode") or 0) == 0 and failed == 0
        artifacts = self._collect_result_artifacts(
            spec_path=Path(str(spec_path)),
            result=result,
            artifact_root=artifact_root,
        )
        return ExecutionResult(
            run_id=plan.id,
            runner=self.name,
            status="passed" if ok else "failed",
            summary={"passed": passed, "failed": failed or (0 if ok else 1), "skipped": 0},
            failures=[] if ok else [{"id": "playwright", "category": "runner_error", "message": str(result)}],
            artifacts=artifacts,
            metrics=result,
        )

    def collect_artifacts(self, result: ExecutionResult) -> list[dict[str, Any]]:
        return list(result.artifacts)

    def _collect_result_artifacts(
        self,
        *,
        spec_path: Path,
        result: dict[str, Any],
        artifact_root: Path | None,
    ) -> list[dict[str, Any]]:
        resolved_spec = spec_path if spec_path.is_absolute() else self.repo_root / spec_path
        roots: list[Path] = []
        for key in ("report_dir", "formal_primary_report_dir"):
            if result.get(key):
                roots.append(Path(str(result[key])))
        if artifact_root is not None:
            roots.append(artifact_root / "playwright-raw")
        roots.extend(
            [
                resolved_spec.parent / "test-results",
                resolved_spec.parent / "playwright-report",
            ]
        )
        return collect_files(roots)

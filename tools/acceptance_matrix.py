"""Run the release acceptance matrix for the generalized E2E framework."""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from e2e_agent.graph.graph import build_graph  # noqa: E402
from e2e_agent.workflow import WorkflowRuntime  # noqa: E402


STATIC_COMMANDS = [
    [sys.executable, "tools/validate_repository.py"],
    [sys.executable, "tools/validate_docs.py"],
    [sys.executable, "tools/validate_schemas.py"],
    [sys.executable, "tools/validate_domains.py"],
    [sys.executable, "tools/validate_workflows.py"],
    [sys.executable, "tools/validate_runners.py"],
    [sys.executable, "tools/validate_plugins.py"],
    [sys.executable, "tools/ci_rule_check.py"],
    [sys.executable, "tools/check_domain_boundaries.py"],
]

APP_MATRIX = [
    ("generic-web", "apps/demo-generic-form/app.yaml"),
    ("ecommerce", "apps/demo-ecommerce/app.yaml"),
    ("insurance", "apps/demo-insurance/app.yaml"),
    ("saas", "apps/demo-saas/app.yaml"),
]


def _run_static_checks() -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for command in STATIC_COMMANDS:
        completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
        results.append(
            {
                "name": Path(command[1]).stem,
                "passed": completed.returncode == 0,
                "returncode": completed.returncode,
                "output": (completed.stdout + completed.stderr)[-1000:],
            }
        )
    return results


async def _run_workflows(temp_root: Path) -> list[dict[str, Any]]:
    runtime = WorkflowRuntime(repo_root=ROOT)
    results: list[dict[str, Any]] = []
    for domain_id, app_path in APP_MATRIX:
        run_dir = temp_root / domain_id
        result = await runtime.run(
            app_path=Path(app_path),
            workflow="smoke-static-web",
            run_id=f"acceptance-{domain_id}",
            metadata={"artifacts_dir": str(run_dir), "gate_checkpoint_dir": ""},
        )
        artifacts = result.get("artifacts") or {}
        report_paths = {Path(str(item["path"])).name for item in artifacts.get("report_artifacts") or []}
        results.append(
            {
                "name": f"smoke:{domain_id}",
                "passed": {
                    "report.json",
                    "report.html",
                    "junit.xml",
                }
                <= report_paths
                and bool((result.get("artifact_manifest") or {}).get("artifacts")),
                "status": (result.get("artifact_manifest") or {}).get("status"),
                "report_paths": sorted(report_paths),
            }
        )

    plugin_runtime = WorkflowRuntime(repo_root=ROOT, plugin_roots=[ROOT / "examples" / "plugins"])
    plugin_result = await plugin_runtime.run(
        app_path=Path("apps/demo-generic-form/app.yaml"),
        workflow=Path("examples/workflows/plugin-smoke.yaml"),
        run_id="acceptance-plugin",
        inputs={"message": "acceptance"},
        metadata={"artifacts_dir": str(temp_root / "plugin"), "gate_checkpoint_dir": ""},
    )
    results.append(
        {
            "name": "plugin:echo",
            "passed": (plugin_result.get("artifacts") or {}).get("plugin_echo", {}).get("message") == "acceptance",
        }
    )
    return results


def main() -> int:
    checks = _run_static_checks()
    try:
        build_graph(":memory:")
        checks.append({"name": "legacy-graph", "passed": True})
    except Exception as exc:  # pragma: no cover - CLI reporting
        checks.append({"name": "legacy-graph", "passed": False, "error": str(exc)})

    with tempfile.TemporaryDirectory(prefix="e2e-acceptance-") as temporary:
        checks.extend(asyncio.run(_run_workflows(Path(temporary))))

    passed = sum(bool(item.get("passed")) for item in checks)
    summary = {
        "status": "passed" if passed == len(checks) else "failed",
        "passed": passed,
        "failed": len(checks) - passed,
        "checks": checks,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

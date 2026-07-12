from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "pyproject.toml").exists())
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from e2e_agent.legacy.agents.agent2_path_extract.node import _build_regression_artifacts


def main() -> int:
    payload = json.loads(sys.stdin.read() or "{}")
    regression_flow, regression_paths, governance_summary, warnings = _build_regression_artifacts(payload)
    json.dump(
        {
            "regression_flow": regression_flow,
            "regression_paths": regression_paths,
            "governance_summary": governance_summary,
            "warnings": warnings,
        },
        sys.stdout,
        ensure_ascii=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

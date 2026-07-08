from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "pyproject.toml").exists())
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from e2e_agent.core.case_skeleton import build_stage1_skeleton, materialise_skeleton
from e2e_agent.core.knowledge_base import KnowledgeLoader, normalise_workflow_cases


def main() -> int:
    payload = json.loads(sys.stdin.read() or "{}")
    product_id = str(payload.get("product_id") or "product")
    prd_analysis = payload.get("prd_analysis")
    if not isinstance(prd_analysis, dict):
        raise SystemExit("prd_analysis is required")
    root_dir = Path(str(payload.get("root_dir") or REPO_ROOT))

    workflow_cases = None
    if isinstance(payload.get("workflow_cases"), dict):
        workflow_cases = normalise_workflow_cases(payload["workflow_cases"])
    else:
        loaded = KnowledgeLoader(root_dir=root_dir).load(product_id)
        if loaded.workflow_cases:
            workflow_cases = loaded.workflow_case_payload

    skeleton = build_stage1_skeleton(prd_analysis, workflow_cases=workflow_cases)
    if payload.get("materialise", True):
        materialise_skeleton(root_dir, product_id, skeleton)
    json.dump({"skeleton": skeleton}, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "pyproject.toml").exists())
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from e2e_agent.core.case_merge import (
    ensure_skeleton_cases,
    load_manual_cases,
    merge_cases,
    normalise_stage1_cases,
    select_regression_cases,
)


def main() -> int:
    payload = json.loads(sys.stdin.read() or "{}")
    product_id = str(payload.get("product_id") or "product")
    product_name = str(payload.get("product_name") or "")
    prd_path = str(payload.get("prd_path") or "")
    manual_cases_path = payload.get("manual_cases_path")
    skeleton_cases = payload.get("skeleton_cases")
    prd_analysis = payload.get("prd_analysis")

    warnings: list[str] = []
    manual_cases, parse_trace, manual_warnings = load_manual_cases(manual_cases_path)
    warnings.extend(manual_warnings)

    prd_analysis, skeleton_cases, stage_warnings = ensure_skeleton_cases(
        product_id=product_id,
        prd_path=prd_path,
        prd_analysis=prd_analysis if isinstance(prd_analysis, dict) else None,
        skeleton_cases=skeleton_cases if isinstance(skeleton_cases, list) else None,
    )
    warnings.extend(stage_warnings)

    candidate_cases, conflicts = merge_cases(
        product_id,
        manual_cases,
        normalise_stage1_cases(skeleton_cases),
    )
    merged_cases, excluded_cases, selection_trace = select_regression_cases(
        candidate_cases,
        conflicts,
        product_id=product_id,
        product_name=product_name,
    )
    json.dump(
        {
            "candidate_cases": candidate_cases,
            "merged_cases": merged_cases,
            "excluded_cases": excluded_cases,
            "conflicts": conflicts,
            "selection_trace": selection_trace,
            "parse_trace": parse_trace,
            "warnings": warnings,
        },
        sys.stdout,
        ensure_ascii=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

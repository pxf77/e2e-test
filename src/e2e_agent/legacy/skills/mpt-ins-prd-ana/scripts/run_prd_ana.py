from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "pyproject.toml").exists())
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from e2e_agent.core.prd_analysis import (
    build_prd_analysis,
    load_text,
    materialise_analysis,
)


def main() -> int:
    payload = json.loads(sys.stdin.read() or "{}")
    product_id = str(payload.get("product_id") or "product")
    prd_path = str(payload.get("prd_path") or "").strip()
    if not prd_path:
        raise SystemExit("prd_path is required")

    root_dir = Path(str(payload.get("root_dir") or REPO_ROOT))
    text = load_text(prd_path)
    analysis = build_prd_analysis(product_id, text)
    if payload.get("materialise", True):
        materialise_analysis(root_dir, product_id, analysis)
    json.dump(analysis, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

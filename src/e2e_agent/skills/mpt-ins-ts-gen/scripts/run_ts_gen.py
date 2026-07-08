from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "pyproject.toml").exists())
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from e2e_agent.core.script_generation import build_ts_gen_bundle


def main() -> int:
    payload = json.loads(sys.stdin.read() or "{}")
    root_dir = Path(str(payload.get("root_dir") or REPO_ROOT))
    result = build_ts_gen_bundle(
        payload,
        root_dir=root_dir,
        materialise=bool(payload.get("materialise", True)),
        generated_by=str(payload.get("generated_by") or "mpt-ins-ts-gen"),
    )
    json.dump(result, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

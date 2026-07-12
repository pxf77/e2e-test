"""Compatibility wrapper for ``tools.diagnostics.model_acceptance``."""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.diagnostics.model_acceptance import main, parse_args  # noqa: E402,F401


if __name__ == "__main__":
    raise SystemExit(main())

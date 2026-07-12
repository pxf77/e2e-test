"""Compatibility wrapper for ``tools.validate.boundaries``."""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.validate.boundaries import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())

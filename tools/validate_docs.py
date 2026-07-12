"""Compatibility wrapper for ``tools.validate.docs``."""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.validate.docs import local_target, main, markdown_files, validate  # noqa: E402,F401


if __name__ == "__main__":
    raise SystemExit(main())

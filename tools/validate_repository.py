"""Compatibility wrapper for ``tools.validate.repository``."""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.validate.repository import (  # noqa: E402,F401
    BANNED_TRACKED_PATTERNS,
    find_violations,
    main,
    tracked_files,
)


if __name__ == "__main__":
    raise SystemExit(main())

"""Compatibility wrapper for ``tools.validate.rules``."""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.validate.rules import (  # noqa: E402,F401
    BANNED_IMPORT_PREFIXES,
    BANNED_MODEL_PATTERNS,
    check_python_file_reg9,
    check_skill_md_reg10,
    main,
)


if __name__ == "__main__":
    raise SystemExit(main())

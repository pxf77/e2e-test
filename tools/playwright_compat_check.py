"""Compatibility wrapper for ``tools.diagnostics.playwright_compat``."""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.diagnostics.playwright_compat import (  # noqa: E402,F401
    check_node,
    check_npx_playwright,
    check_playwright_python,
    find_spec_files,
    main,
    parse_args,
    run,
    run_sample_spec,
    write_report,
)


if __name__ == "__main__":
    raise SystemExit(main())

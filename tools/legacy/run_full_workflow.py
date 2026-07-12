"""Categorized import alias for the legacy full-workflow command.

The 1.x root script remains the compatibility implementation because the legacy
CLI imports it directly. The physical move is a 2.0 breaking-change item.
"""
from __future__ import annotations

from tools.run_full_workflow import main

__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())

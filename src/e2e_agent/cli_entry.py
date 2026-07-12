"""Compatibility wrapper for the canonical command package.

New code and the installed ``e2e-agent`` console script use
``e2e_agent.commands.main``. This module remains import-compatible for 1.x.
"""

from e2e_agent.commands.main import main

__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())

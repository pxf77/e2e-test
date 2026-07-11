"""Deprecated compatibility wrapper for the canonical CLI entrypoint."""

from e2e_agent.commands.main import main

__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())

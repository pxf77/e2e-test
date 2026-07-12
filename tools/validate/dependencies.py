"""Validate direct dependency declarations and root instruction ownership."""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REMOVED_DIRECT_PYTHON_DEPS = {"aiofiles", "click", "pydantic"}


def _normalise_requirement(requirement: str) -> str:
    value = requirement.split(";", 1)[0].strip()
    for token in ("[", "<", ">", "=", "!", "~", " "):
        value = value.split(token, 1)[0]
    return value.lower().replace("_", "-")


def validate(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    project_dependencies = {
        _normalise_requirement(item)
        for item in (pyproject.get("project", {}).get("dependencies") or [])
    }
    unexpected = sorted(REMOVED_DIRECT_PYTHON_DEPS & project_dependencies)
    if unexpected:
        errors.append(f"unused direct Python dependencies were reintroduced: {unexpected}")

    console_script = (
        pyproject.get("project", {})
        .get("scripts", {})
        .get("e2e-agent")
    )
    if console_script != "e2e_agent.commands.main:main":
        errors.append(f"canonical e2e-agent entrypoint changed: {console_script!r}")

    import json

    package_json = json.loads((root / "package.json").read_text(encoding="utf-8"))
    node_direct = set((package_json.get("devDependencies") or {}).keys())
    if "playwright" in node_direct:
        errors.append("direct Node dependency 'playwright' duplicates @playwright/test")
    if "@playwright/test" not in node_direct:
        errors.append("@playwright/test must remain the canonical Node Playwright dependency")

    claude = (root / "CLAUDE.md").read_text(encoding="utf-8", errors="replace")
    if "AGENTS.md" not in claude:
        errors.append("CLAUDE.md must point to canonical AGENTS.md")
    if "D:\\huizecode" in claude or "D:/huizecode" in claude:
        errors.append("CLAUDE.md contains a machine-specific source path")
    return errors


def main() -> int:
    errors = validate()
    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: direct dependency and root instruction ownership validated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

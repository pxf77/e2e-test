"""Validate repository-local Markdown links and documentation layout."""
from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
LINK_RE = re.compile(r"!?(?:\[[^\]]*\])\(([^)]+)\)")
OBSOLETE_DOCS = {
    "architecture-v2.md",
    "assertion-engine-v2.md",
    "config-ownership.md",
    "data-pack-runtime.md",
    "domain-pack-dev.md",
    "gate-runtime-v2.md",
    "migration-v1-to-v2.md",
    "plugin-sdk.md",
    "release-v1.md",
    "reporting-v2.md",
    "runner-sdk.md",
    "workflow-dsl.md",
}


def markdown_files(root: Path = ROOT) -> list[Path]:
    files = [root / "README.md"]
    files.extend(sorted((root / "docs").rglob("*.md")))
    return [path for path in files if path.exists()]


def local_target(source: Path, raw_target: str) -> Path | None:
    value = raw_target.strip().strip("<>")
    if not value or value.startswith(("#", "http://", "https://", "mailto:")):
        return None
    path_part = unquote(value.split("#", 1)[0].split("?", 1)[0])
    if not path_part:
        return None
    return (source.parent / path_part).resolve()


def validate(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    docs_root = root / "docs"
    for name in sorted(OBSOLETE_DOCS):
        if (docs_root / name).exists():
            errors.append(f"obsolete flat documentation path exists: docs/{name}")

    for source in markdown_files(root):
        text = source.read_text(encoding="utf-8", errors="replace")
        for match in LINK_RE.finditer(text):
            target = local_target(source, match.group(1))
            if target is None:
                continue
            try:
                target.relative_to(root.resolve())
            except ValueError:
                errors.append(f"{source.relative_to(root)}: link escapes repository: {match.group(1)}")
                continue
            if not target.exists():
                errors.append(f"{source.relative_to(root)}: missing link target: {match.group(1)}")
    return errors


def main() -> int:
    errors = validate()
    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        print(f"\n{len(errors)} documentation error(s) found.", file=sys.stderr)
        return 1
    print(f"PASS: {len(markdown_files())} Markdown files and local links validated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""One-time migration of legacy schemas into ``schemas/v1``.

Deleted after the migration commit is created and verified.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "schemas"
V1 = SCHEMAS / "v1"
TEXT_SUFFIXES = {".py", ".md", ".yaml", ".yml", ".json", ".toml"}


def rewrite_schema_id(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    replacement = f'  "$id": "https://pxf77.github.io/e2e-test/schemas/v1/{path.name}",'
    updated, count = re.subn(r'^\s*"\$id"\s*:\s*"[^"]*"\s*,?$', replacement, text, count=1, flags=re.MULTILINE)
    if count != 1:
        raise RuntimeError(f"expected exactly one $id in {path}")
    path.write_text(updated, encoding="utf-8")


def rewrite_references(schema_names: list[str]) -> None:
    roots = [ROOT / "src", ROOT / "tests", ROOT / "tools", ROOT / "docs", ROOT / "AGENTS.md", ROOT / "README.md"]
    files: list[Path] = []
    for root in roots:
        if root.is_file():
            files.append(root)
        elif root.exists():
            files.extend(path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES)

    for path in files:
        if path == Path(__file__).resolve():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        updated = text
        for name in schema_names:
            updated = updated.replace(f"schemas/{name}", f"schemas/v1/{name}")
            updated = updated.replace(
                f'/ "schemas" / "{name}"',
                f'/ "schemas" / "v1" / "{name}"',
            )
        updated = updated.replace('ROOT / "schemas" / name', 'ROOT / "schemas" / "v1" / name')
        updated = updated.replace(
            'CONFIG_PATH.parent.parent / "schemas" / "model-routing.schema.json"',
            'CONFIG_PATH.parent.parent / "schemas" / "v1" / "model-routing.schema.json"',
        )
        if updated != text:
            path.write_text(updated, encoding="utf-8")


def main() -> int:
    root_schemas = sorted(SCHEMAS.glob("*.schema.json"))
    if not root_schemas:
        print("schemas/v1 migration already applied")
        return 0
    V1.mkdir(parents=True, exist_ok=True)
    names = [path.name for path in root_schemas]
    for source in root_schemas:
        target = V1 / source.name
        if target.exists():
            raise FileExistsError(target)
        shutil.move(str(source), str(target))
        rewrite_schema_id(target)
    rewrite_references(names)
    (V1 / "README.md").write_text(
        "# v1 Contracts\n\nLegacy four-Agent and Skill Package contracts. New framework contracts live in `schemas/v2/`.\n",
        encoding="utf-8",
    )
    print(f"moved {len(names)} schemas to schemas/v1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

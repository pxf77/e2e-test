"""Validate versioned JSON Schema files under ``schemas/v1`` and ``schemas/v2``."""
from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    import jsonschema
    from jsonschema import Draft7Validator
except ModuleNotFoundError:  # pragma: no cover
    jsonschema = None
    Draft7Validator = None

ROOT = Path(__file__).resolve().parents[2]
SCHEMAS_DIR = ROOT / "schemas"
VERSIONS = ("v1", "v2")


def validate_layout(root: Path = ROOT) -> list[str]:
    schemas = root / "schemas"
    errors: list[str] = []
    root_contracts = sorted(path.name for path in schemas.glob("*.schema.json"))
    if root_contracts:
        errors.append(f"unversioned root schemas remain: {root_contracts}")
    for version in VERSIONS:
        directory = schemas / version
        if not directory.exists():
            errors.append(f"missing schema version directory: schemas/{version}")
            continue
        if not list(directory.glob("*.schema.json")):
            errors.append(f"schema version directory is empty: schemas/{version}")
    return errors


def validate_schema_file(path: Path) -> list[str]:
    errors: list[str] = []
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"JSON parse error: {exc}"]

    if Draft7Validator is not None and jsonschema is not None:
        try:
            Draft7Validator.check_schema(schema)
        except jsonschema.SchemaError as exc:
            return [f"Schema error: {exc.message}"]

    for field, message in (
        ("$schema", "Missing '$schema' field"),
        ("$id", "Missing '$id' field"),
        ("title", "Missing 'title' field"),
        ("description", "Missing 'description' field (required for documentation)"),
    ):
        if field not in schema:
            errors.append(message)

    schema_id = str(schema.get("$id") or "")
    relative = path.relative_to(SCHEMAS_DIR).as_posix()
    version = relative.split("/", 1)[0]
    if version in VERSIONS and f"/schemas/{version}/" not in schema_id:
        errors.append(f"$id does not identify schemas/{version}: {schema_id}")
    return errors


def iter_schema_files() -> list[Path]:
    return sorted(SCHEMAS_DIR.rglob("*.schema.json"))


def main() -> int:
    if not SCHEMAS_DIR.exists():
        print(f"ERROR: schemas/ directory not found at {SCHEMAS_DIR}", file=sys.stderr)
        return 1
    layout_errors = validate_layout()
    if layout_errors:
        for error in layout_errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1
    if Draft7Validator is None:
        print("WARN: jsonschema is not installed; running JSON syntax + metadata checks only.")

    schema_files = iter_schema_files()
    passed = 0
    failed = 0
    for path in schema_files:
        errors = validate_schema_file(path)
        display = path.relative_to(SCHEMAS_DIR)
        if errors:
            failed += 1
            print(f"  FAIL  {display}")
            for error in errors:
                print(f"        → {error}")
        else:
            passed += 1
            print(f"  pass  {display}")

    print(f"\nResults: {passed}/{len(schema_files)} passed", end="")
    if failed:
        print(f", {failed} FAILED")
        return 1
    print(" OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

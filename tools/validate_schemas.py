"""Validate all JSON Schema files under schemas/ recursively.

Usage:
    python tools/validate_schemas.py
    make validate-schemas
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    import jsonschema
    from jsonschema import Draft7Validator
except ModuleNotFoundError:  # pragma: no cover - depends on local environment
    jsonschema = None
    Draft7Validator = None


SCHEMAS_DIR = Path(__file__).parent.parent / "schemas"


def validate_schema_file(path: Path) -> list[str]:
    errors: list[str] = []
    try:
        with open(path, encoding="utf-8") as f:
            schema = json.load(f)
    except json.JSONDecodeError as e:
        errors.append(f"JSON parse error: {e}")
        return errors

    # Check it is a valid JSON Schema (Draft-07) when jsonschema is installed.
    if Draft7Validator is not None and jsonschema is not None:
        try:
            Draft7Validator.check_schema(schema)
        except jsonschema.SchemaError as e:
            errors.append(f"Schema error: {e.message}")
            return errors

    # Enforce required metadata fields.
    if "$schema" not in schema:
        errors.append("Missing '$schema' field")
    if "$id" not in schema:
        errors.append("Missing '$id' field")
    if "title" not in schema:
        errors.append("Missing 'title' field")
    if "description" not in schema:
        errors.append("Missing 'description' field (required for documentation)")

    return errors


def iter_schema_files() -> list[Path]:
    return sorted(SCHEMAS_DIR.rglob("*.schema.json"))


def main() -> int:
    if not SCHEMAS_DIR.exists():
        print(f"ERROR: schemas/ directory not found at {SCHEMAS_DIR}", file=sys.stderr)
        return 1
    if Draft7Validator is None:
        print("WARN: jsonschema is not installed; running JSON syntax + metadata checks only.")

    schema_files = iter_schema_files()
    if not schema_files:
        print("ERROR: No *.schema.json files found under schemas/", file=sys.stderr)
        return 1

    total = len(schema_files)
    passed = 0
    failed = 0

    for path in schema_files:
        errors = validate_schema_file(path)
        display = path.relative_to(SCHEMAS_DIR)
        if errors:
            failed += 1
            print(f"  FAIL  {display}")
            for err in errors:
                print(f"        → {err}")
        else:
            passed += 1
            print(f"  pass  {display}")

    print(f"\nResults: {passed}/{total} passed", end="")
    if failed:
        print(f", {failed} FAILED")
        return 1
    print(" OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Validate all JSON Schema files under schemas/ recursively."""
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

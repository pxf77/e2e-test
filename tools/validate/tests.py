"""Validate categorized test layout and repository-root path rewrites."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TESTS_ROOT = ROOT / "tests"
CATEGORIES = ("unit", "integration", "compatibility", "acceptance")
STALE_PATH_PATTERNS = (
    "Path(__file__).resolve().parents[1]",
    "Path(__file__).parent.parent /",
)


def validate(root: Path = ROOT) -> list[str]:
    tests_root = root / "tests"
    errors: list[str] = []
    root_tests = sorted(path.name for path in tests_root.glob("test_*.py"))
    if root_tests:
        errors.append(f"uncategorized root tests remain: {root_tests}")

    seen: dict[str, str] = {}
    for category in CATEGORIES:
        category_root = tests_root / category
        if not category_root.exists():
            errors.append(f"missing test category: tests/{category}")
            continue
        files = sorted(category_root.glob("test_*.py"))
        if not files:
            errors.append(f"empty test category: tests/{category}")
        for path in files:
            previous = seen.get(path.name)
            if previous:
                errors.append(f"duplicate test filename {path.name}: {previous}, {category}")
            seen[path.name] = category
            text = path.read_text(encoding="utf-8", errors="replace")
            for pattern in STALE_PATH_PATTERNS:
                if pattern in text:
                    errors.append(f"{path.relative_to(root)} uses stale moved-file path expression: {pattern}")
    return errors


def main() -> int:
    errors = validate()
    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1
    counts = {
        category: len(list((TESTS_ROOT / category).glob("test_*.py")))
        for category in CATEGORIES
    }
    print(f"PASS: categorized test layout validated: {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

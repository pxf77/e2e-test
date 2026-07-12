"""Validate that generated or local-only files are not tracked by Git."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[2]

BANNED_TRACKED_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"(^|/)\.assets/",
        r"(^|/)playwright-report/",
        r"(^|/)test-results/",
        r"(^|/)\.local/",
        r"(^|/)pytest\.log$",
        r"(^|/)trace\.zip$",
        r"\.webm$",
        r"(^|/)__pycache__/",
        r"\.py[co]$",
    )
)


def tracked_files(repo_root: Path = ROOT) -> list[str]:
    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=repo_root,
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        error = completed.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"git ls-files failed: {error or completed.returncode}")
    return sorted(
        item.decode("utf-8", errors="strict").replace("\\", "/")
        for item in completed.stdout.split(b"\0")
        if item
    )


def find_violations(paths: Iterable[str]) -> list[str]:
    violations: list[str] = []
    for raw_path in paths:
        path = raw_path.replace("\\", "/")
        if any(pattern.search(path) for pattern in BANNED_TRACKED_PATTERNS):
            violations.append(path)
    return sorted(set(violations))


def main() -> int:
    try:
        violations = find_violations(tracked_files())
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if violations:
        print("ERROR: generated or local-only files are tracked by Git:", file=sys.stderr)
        for path in violations:
            print(f"  - {path}", file=sys.stderr)
        return 1
    print("PASS: repository hygiene check passed; no generated runtime artifacts are tracked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Check that core runtime layers and global config stay domain-agnostic."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCAN_DIRS = [
    ROOT / "src" / "e2e_agent" / "contracts",
    ROOT / "src" / "e2e_agent" / "workflow",
    ROOT / "src" / "e2e_agent" / "runners",
    ROOT / "src" / "e2e_agent" / "plugins",
]
BANNED_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"\bunderwriting\b",
        r"\bpolicyNo\b",
        r"\bhealthNotice\b",
        r"\bpremium\b",
        r"\b保费\b",
        r"\b核保\b",
        r"\b保单\b",
    ]
]
CONFIG_BANNED_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"\bunderwriting\b",
        r"\bpolicyNo\b",
        r"\bhealthNotice\b",
        r"\bpremium\b",
        r"\bcart\b",
        r"\bcheckout\b",
        r"\bworkspace\b",
        r"\b保费\b",
        r"\b核保\b",
        r"\b保单\b",
    ]
]


def _scan_file(path: Path, patterns: list[re.Pattern[str]]) -> list[str]:
    violations: list[str] = []
    text = path.read_text(encoding="utf-8", errors="replace")
    for lineno, line in enumerate(text.splitlines(), start=1):
        if "BANNED_PATTERNS" in line:
            continue
        for pattern in patterns:
            if pattern.search(line):
                violations.append(f"{path.relative_to(ROOT)}:{lineno}: {line.strip()}")
    return violations


def main() -> int:
    violations: list[str] = []
    for scan_dir in SCAN_DIRS:
        if not scan_dir.exists():
            continue
        for path in sorted(scan_dir.rglob("*.py")):
            violations.extend(_scan_file(path, BANNED_PATTERNS))

    config_dir = ROOT / "config"
    if config_dir.exists():
        for path in sorted(config_dir.rglob("*")):
            if path.is_file() and path.suffix.lower() in {".yaml", ".yml", ".json", ".toml"}:
                violations.extend(_scan_file(path, CONFIG_BANNED_PATTERNS))

    if violations:
        for violation in violations:
            print(f"VIOLATION: {violation}", file=sys.stderr)
        print(f"\n{len(violations)} domain-boundary violation(s) found.", file=sys.stderr)
        return 1
    print("PASS: domain boundary check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

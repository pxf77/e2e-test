"""T-FS-16: CI check for RULE-REG-9 and RULE-REG-10 compliance.

RULE-REG-9: Python source files in src/ must not directly import model SDKs.
RULE-REG-10: Skill Package SKILL.md files must not hardcode model names.

Usage:
    python tools/ci_rule_check.py
    make ci-check

Exit codes:
    0 — all checks passed
    1 — one or more violations found
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path


# ── RULE-REG-9: banned SDK imports ─3──────────────────────────────────────────

BANNED_IMPORT_PREFIXES = {
    "anthropic",
    "openai",
    "google.generativeai",
    "google.cloud.aiplatform",
    "boto3",       # AWS Bedrock direct
    "cohere",
    "mistralai",
    "groq",
}

SRC_DIR = Path(__file__).parent.parent / "src"


def check_python_file_reg9(path: Path) -> list[str]:
    violations = []
    try:
        source = path.read_text(encoding="utf-8")
    except Exception as e:
        return [f"{path}: cannot read file: {e}"]

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as e:
        return [f"{path}: syntax error: {e}"]

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                for banned in BANNED_IMPORT_PREFIXES:
                    if alias.name == banned or alias.name.startswith(banned + "."):
                        violations.append(
                            f"{path}:{node.lineno}: [RULE-REG-9] banned import '{alias.name}'"
                        )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for banned in BANNED_IMPORT_PREFIXES:
                if module == banned or module.startswith(banned + "."):
                    violations.append(
                        f"{path}:{node.lineno}: [RULE-REG-9] banned import from '{module}'"
                    )
    return violations


# ── RULE-REG-10: banned model names in SKILL.md ──────────────────────────────

BANNED_MODEL_PATTERNS = [
    re.compile(r"\bclaude-(opus|sonnet|haiku)-\d+", re.IGNORECASE),
    re.compile(r"\bgpt-4[o\w-]*\b", re.IGNORECASE),
    re.compile(r"\bgemini[-/]\w+", re.IGNORECASE),
    re.compile(r"\bdeepseek[-/]\w+", re.IGNORECASE),
    re.compile(r"\bclaude-[23]-\w+", re.IGNORECASE),
]

SKILL_PACKAGES_DIR = Path(__file__).parent.parent / "src" / "e2e_agent" / "skills"


def check_skill_md_reg10(path: Path) -> list[str]:
    violations = []
    try:
        content = path.read_text(encoding="utf-8")
    except Exception as e:
        return [f"{path}: cannot read file: {e}"]

    for lineno, line in enumerate(content.splitlines(), start=1):
        # Skip comments and metadata lines that explain the rule
        if line.strip().startswith("#") or "RULE-REG" in line:
            continue
        for pattern in BANNED_MODEL_PATTERNS:
            match = pattern.search(line)
            if match:
                violations.append(
                    f"{path}:{lineno}: [RULE-REG-10] hardcoded model name "
                    f"'{match.group()}' in SKILL.md (use model-routing.yaml instead)"
                )
    return violations


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    all_violations: list[str] = []

    # RULE-REG-9: scan Python source files
    if SRC_DIR.exists():
        for py_file in sorted(SRC_DIR.rglob("*.py")):
            all_violations.extend(check_python_file_reg9(py_file))
    else:
        print(f"WARNING: src/ directory not found at {SRC_DIR}", file=sys.stderr)

    # RULE-REG-10: scan SKILL.md files
    if SKILL_PACKAGES_DIR.exists():
        for skill_md in sorted(SKILL_PACKAGES_DIR.rglob("SKILL.md")):
            all_violations.extend(check_skill_md_reg10(skill_md))
    else:
        print(f"WARNING: Skill directory not found at {SKILL_PACKAGES_DIR}", file=sys.stderr)

    if all_violations:
        for v in all_violations:
            print(f"VIOLATION: {v}", file=sys.stderr)
        print(
            f"\n❌ {len(all_violations)} violation(s) found. "
            "Fix before committing.",
            file=sys.stderr,
        )
        return 1

    print("PASS: RULE-REG-9/10 check passed -- no violations found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

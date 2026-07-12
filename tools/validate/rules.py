"""CI checks for model SDK and Skill model-routing boundaries."""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT / "src"
SKILL_PACKAGES_DIR = ROOT / "src" / "e2e_agent" / "skills"

BANNED_IMPORT_PREFIXES = {
    "anthropic",
    "openai",
    "google.generativeai",
    "google.cloud.aiplatform",
    "boto3",
    "cohere",
    "mistralai",
    "groq",
}
BANNED_MODEL_PATTERNS = [
    re.compile(r"\bclaude-(opus|sonnet|haiku)-\d+", re.IGNORECASE),
    re.compile(r"\bgpt-4[o\w-]*\b", re.IGNORECASE),
    re.compile(r"\bgemini[-/]\w+", re.IGNORECASE),
    re.compile(r"\bdeepseek[-/]\w+", re.IGNORECASE),
    re.compile(r"\bclaude-[23]-\w+", re.IGNORECASE),
]


def check_python_file_reg9(path: Path) -> list[str]:
    try:
        source = path.read_text(encoding="utf-8")
    except Exception as exc:
        return [f"{path}: cannot read file: {exc}"]
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return [f"{path}: syntax error: {exc}"]

    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                for banned in BANNED_IMPORT_PREFIXES:
                    if alias.name == banned or alias.name.startswith(banned + "."):
                        violations.append(f"{path}:{node.lineno}: [RULE-REG-9] banned import '{alias.name}'")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for banned in BANNED_IMPORT_PREFIXES:
                if module == banned or module.startswith(banned + "."):
                    violations.append(f"{path}:{node.lineno}: [RULE-REG-9] banned import from '{module}'")
    return violations


def check_skill_md_reg10(path: Path) -> list[str]:
    try:
        content = path.read_text(encoding="utf-8")
    except Exception as exc:
        return [f"{path}: cannot read file: {exc}"]

    violations: list[str] = []
    for lineno, line in enumerate(content.splitlines(), start=1):
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


def main() -> int:
    violations: list[str] = []
    if SRC_DIR.exists():
        for path in sorted(SRC_DIR.rglob("*.py")):
            violations.extend(check_python_file_reg9(path))
    else:
        print(f"WARNING: src/ directory not found at {SRC_DIR}", file=sys.stderr)

    if SKILL_PACKAGES_DIR.exists():
        for path in sorted(SKILL_PACKAGES_DIR.rglob("SKILL.md")):
            violations.extend(check_skill_md_reg10(path))
    else:
        print(f"WARNING: Skill directory not found at {SKILL_PACKAGES_DIR}", file=sys.stderr)

    if violations:
        for violation in violations:
            print(f"VIOLATION: {violation}", file=sys.stderr)
        print(f"\n{len(violations)} violation(s) found. Fix before committing.", file=sys.stderr)
        return 1
    print("PASS: RULE-REG-9/10 check passed -- no violations found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

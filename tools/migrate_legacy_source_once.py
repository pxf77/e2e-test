"""One-time migration of legacy packages into ``e2e_agent.legacy``.

Deleted after the migration commit is created and verified.
"""
from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "e2e_agent"
LEGACY = PACKAGE / "legacy"
MOVES = ("agents", "browser", "graph", "skills")
TEXT_SUFFIXES = {".py", ".md", ".yaml", ".yml", ".json", ".toml", ".ts", ".js", ".txt", ".ps1"}
REPLACEMENTS = {
    "e2e_agent.agents": "e2e_agent.legacy.agents",
    "e2e_agent.browser": "e2e_agent.legacy.browser",
    "e2e_agent.graph": "e2e_agent.legacy.graph",
    "e2e_agent.skills": "e2e_agent.legacy.skills",
    "src/e2e_agent/agents": "src/e2e_agent/legacy/agents",
    "src/e2e_agent/browser": "src/e2e_agent/legacy/browser",
    "src/e2e_agent/graph": "src/e2e_agent/legacy/graph",
    "src/e2e_agent/skills": "src/e2e_agent/legacy/skills",
}


def iter_text_files() -> list[Path]:
    roots = [
        ROOT / "src",
        ROOT / "tests",
        ROOT / "tools",
        ROOT / "docs",
        ROOT / "workflows",
        ROOT / "config",
        ROOT / "examples",
        ROOT / "products",
        ROOT / "README.md",
        ROOT / "AGENTS.md",
        ROOT / "CLAUDE.md",
        ROOT / "Makefile",
        ROOT / "pyproject.toml",
    ]
    files: list[Path] = []
    for root in roots:
        if root.is_file():
            files.append(root)
        elif root.exists():
            files.extend(path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES)
    return sorted(set(files))


def rewrite_references() -> None:
    for path in iter_text_files():
        if path == Path(__file__).resolve():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        updated = text
        for old, new in REPLACEMENTS.items():
            updated = updated.replace(old, new)
        if updated != text:
            path.write_text(updated, encoding="utf-8")


def fix_moved_package_depth() -> None:
    agents = LEGACY / "agents"
    for relative in (
        "agent1_tc_merge/node.py",
        "agent2_path_extract/__init__.py",
        "agent2_path_extract/node.py",
        "agent3_explore/node.py",
        "agent4_exec/node.py",
    ):
        path = agents / relative
        text = path.read_text(encoding="utf-8")
        updated = text.replace("Path(__file__).resolve().parents[4]", "Path(__file__).resolve().parents[5]")
        if updated == text:
            raise RuntimeError(f"expected Agent root fallback in {path}")
        path.write_text(updated, encoding="utf-8")

    loader = LEGACY / "skills" / "loader.py"
    text = loader.read_text(encoding="utf-8")
    updated = text.replace("Path(__file__).resolve().parents[3]", "Path(__file__).resolve().parents[4]")
    if updated == text:
        raise RuntimeError("expected SkillPackageLoader cwd expression")
    loader.write_text(updated, encoding="utf-8")


def main() -> int:
    LEGACY.mkdir(parents=True, exist_ok=True)
    init = LEGACY / "__init__.py"
    if not init.exists():
        init.write_text(
            '"""Legacy four-Agent runtime isolated from the current framework core."""\n',
            encoding="utf-8",
        )

    moved = 0
    for name in MOVES:
        source = PACKAGE / name
        target = LEGACY / name
        if source.exists():
            if target.exists():
                raise FileExistsError(target)
            shutil.move(str(source), str(target))
            moved += 1
    if moved == 0:
        print("legacy source migration already applied")
        return 0

    rewrite_references()
    fix_moved_package_depth()
    print(f"moved {moved} legacy packages into {LEGACY.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

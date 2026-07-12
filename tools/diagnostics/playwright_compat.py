"""Validate Playwright TypeScript subprocess and Python fallback compatibility."""
from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT = ROOT / ".local" / "e2e-agent" / "diagnostics" / "playwright-compat-report.md"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        default=str(ROOT),
        help="Repository containing package.json and optional *.spec.ts files.",
    )
    parser.add_argument("--report", default=str(DEFAULT_REPORT), help="Markdown report output path.")
    parser.add_argument("--sample-limit", type=int, default=1)
    return parser.parse_args(argv)


def check_node() -> tuple[bool, str]:
    try:
        result = subprocess.run(["node", "--version"], capture_output=True, text=True, timeout=5, check=False)
        return result.returncode == 0, result.stdout.strip() or result.stderr.strip()
    except Exception as exc:
        return False, str(exc)


def check_npx_playwright(repo_root: Path) -> tuple[bool, str]:
    if not repo_root.exists():
        return False, f"repo not found: {repo_root}"
    try:
        result = subprocess.run(
            ["npx", "playwright", "--version"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        return result.returncode == 0, result.stdout.strip() or result.stderr.strip()
    except Exception as exc:
        return False, str(exc)


def find_spec_files(repo_root: Path, limit: int = 5) -> list[Path]:
    return sorted(repo_root.rglob("*.spec.ts"))[: max(0, limit)]


def run_sample_spec(repo_root: Path, spec: Path) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            [
                "npx",
                "playwright",
                "test",
                str(spec.relative_to(repo_root)),
                "--reporter=list",
                "--timeout=10000",
            ],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        return result.returncode in (0, 1), (result.stdout + result.stderr)[:500]
    except subprocess.TimeoutExpired:
        return False, "timeout (60s)"
    except Exception as exc:
        return False, str(exc)


async def check_playwright_python() -> tuple[bool, str]:
    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto("about:blank")
            title = await page.title()
            await browser.close()
        return True, f"Playwright Python OK (about:blank title={title!r})"
    except Exception as exc:
        return False, str(exc)


def write_report(repo_root: Path, report_path: Path, results: dict) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    subprocess_ok = results["npx_playwright"][0]
    python_ok = results["playwright_python"][0]
    if subprocess_ok:
        conclusion = "TypeScript subprocess strategy is available."
    elif python_ok:
        conclusion = "TypeScript subprocess is unavailable; Playwright Python fallback is available."
    else:
        conclusion = "Neither Playwright execution strategy is currently available."

    lines = [
        "# Playwright compatibility report",
        "",
        f"> Generated: {timestamp}",
        f"> Repository: `{repo_root}`",
        "",
        "## Conclusion",
        "",
        conclusion,
        "",
        "## Checks",
        "",
        "| Check | Passed | Details |",
        "|---|---:|---|",
        f"| Node.js | {results['node'][0]} | {results['node'][1]} |",
        f"| npx Playwright | {results['npx_playwright'][0]} | {results['npx_playwright'][1]} |",
        f"| Playwright Python | {results['playwright_python'][0]} | {results['playwright_python'][1]} |",
        "",
        "## Discovered TypeScript specs",
        "",
    ]
    for spec in results.get("discovered_specs", []):
        lines.append(f"- `{spec.relative_to(repo_root)}`")
    if not results.get("discovered_specs"):
        lines.append("- None")

    if results.get("spec_samples"):
        lines.extend(["", "## Sample execution", ""])
        for spec, (passed, output) in results["spec_samples"]:
            lines.append(f"- `{spec.relative_to(repo_root)}`: passed={passed}; {output[:200]}")

    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Report written to: {report_path}")


async def run(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    report_path = Path(args.report).resolve()
    results: dict = {
        "node": check_node(),
        "npx_playwright": check_npx_playwright(repo_root),
        "playwright_python": await check_playwright_python(),
    }
    specs = find_spec_files(repo_root)
    results["discovered_specs"] = specs
    if results["npx_playwright"][0] and specs and args.sample_limit > 0:
        results["spec_samples"] = [
            (spec, run_sample_spec(repo_root, spec)) for spec in specs[: args.sample_limit]
        ]
    write_report(repo_root, report_path, results)
    return 0 if results["npx_playwright"][0] or results["playwright_python"][0] else 1


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(run(argv))


if __name__ == "__main__":
    raise SystemExit(main())

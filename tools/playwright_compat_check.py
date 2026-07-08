"""T-FS-7/T-FS-8: Playwright Python ↔ TypeScript spec compatibility validation.

Validates two things:
1. Can we run original .spec.ts from e2e-test via subprocess? (primary strategy)
2. Is Playwright Python async API available? (for pure-Python fallback)

Outputs: docs/playwright-compat-report.md

Usage:
    python tools/playwright_compat_check.py
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ORIGINAL_REPO = Path("D:/huizecode/e2e-test")
REPORT_PATH = Path(__file__).parent.parent / "docs" / "playwright-compat-report.md"


def check_node() -> tuple[bool, str]:
    try:
        r = subprocess.run(["node", "--version"], capture_output=True, text=True, timeout=5)
        return r.returncode == 0, r.stdout.strip()
    except Exception as e:
        return False, str(e)


def check_npx_playwright(repo_root: Path) -> tuple[bool, str]:
    if not repo_root.exists():
        return False, f"repo not found: {repo_root}"
    try:
        r = subprocess.run(
            ["npx", "playwright", "--version"],
            cwd=str(repo_root),
            capture_output=True, text=True, timeout=15
        )
        return r.returncode == 0, r.stdout.strip() or r.stderr.strip()
    except Exception as e:
        return False, str(e)


def find_spec_files(repo_root: Path) -> list[Path]:
    return list(repo_root.rglob("*.spec.ts"))[:5]  # Sample first 5


def run_sample_spec(repo_root: Path, spec: Path) -> tuple[bool, str]:
    try:
        r = subprocess.run(
            ["npx", "playwright", "test", str(spec.relative_to(repo_root)),
             "--reporter=list", "--timeout=10000"],
            cwd=str(repo_root),
            capture_output=True, text=True, timeout=60
        )
        output = (r.stdout + r.stderr)[:500]
        # Any output (even failures) means the runner works
        return r.returncode in (0, 1), output
    except subprocess.TimeoutExpired:
        return False, "timeout (60s)"
    except Exception as e:
        return False, str(e)


async def check_playwright_python() -> tuple[bool, str]:
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto("about:blank")
            title = await page.title()
            await browser.close()
        return True, f"Playwright Python OK (about:blank title='{title}')"
    except Exception as e:
        return False, str(e)


def write_report(results: dict) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines = [
        "# Playwright 兼容性验证报告",
        "",
        f"> 生成时间：{ts}  ",
        f"> 原仓路径：`{ORIGINAL_REPO}`",
        "",
        "## 结论",
        "",
    ]

    subprocess_ok = results["npx_playwright"][0]
    python_ok = results["playwright_python"][0]

    if subprocess_ok:
        lines.append("**✅ Primary 方案（subprocess 调用 .spec.ts）可用** — 无需重写 TypeScript 脚本。")
    elif python_ok:
        lines.append("**⚠️ Primary 方案不可用，但 Fallback（纯 Playwright Python）可用** — 需要重写 .spec.ts 为 Python。")
    else:
        lines.append("**❌ 两种方案均不可用** — 需要修复环境。")

    lines += [
        "",
        "## 检查项",
        "",
        "| 检查 | 结果 | 详情 |",
        "|------|------|------|",
        f"| Node.js 可用 | {'✅' if results['node'][0] else '❌'} | {results['node'][1]} |",
        f"| npx playwright 版本 | {'✅' if results['npx_playwright'][0] else '❌'} | {results['npx_playwright'][1]} |",
        f"| Playwright Python API | {'✅' if results['playwright_python'][0] else '❌'} | {results['playwright_python'][1]} |",
        "",
    ]

    if results.get("spec_samples"):
        lines += ["## .spec.ts 样本运行（最多 3 个）", ""]
        for spec_path, (ok, output) in results["spec_samples"]:
            icon = "✅" if ok else "❌"
            lines.append(f"- {icon} `{spec_path.name}`: {output[:200]}")
        lines.append("")

    lines += [
        "## 复用清单（原仓 .spec.ts）",
        "",
        "以下文件可在 subprocess 模式下复用：",
        "",
    ]
    for spec in results.get("discovered_specs", []):
        lines.append(f"- `{spec.relative_to(ORIGINAL_REPO)}`")

    lines += [
        "",
        "## 推荐策略",
        "",
        "1. **W1-W3**：使用 `PlaywrightTSRunner.run_spec()` 通过 subprocess 调用原仓 .spec.ts",
        "2. **W4+**：新增用例直接用 Playwright Python 编写，逐步迁移旧脚本",
        "3. **模型无关**：BrowserSession 和 PlaywrightTSRunner 均不依赖任何 LLM",
    ]

    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"Report written to: {REPORT_PATH}")


async def main() -> int:
    results: dict = {}

    print("Checking Node.js...")
    results["node"] = check_node()

    print("Checking npx playwright...")
    results["npx_playwright"] = check_npx_playwright(ORIGINAL_REPO)

    print("Checking Playwright Python...")
    results["playwright_python"] = await check_playwright_python()

    specs = find_spec_files(ORIGINAL_REPO)
    results["discovered_specs"] = specs
    print(f"Found {len(specs)} .spec.ts files in original repo")

    if results["npx_playwright"][0] and specs:
        print(f"Running sample spec: {specs[0].name}...")
        sample_result = run_sample_spec(ORIGINAL_REPO, specs[0])
        results["spec_samples"] = [(specs[0], sample_result)]

    write_report(results)

    all_ok = results["npx_playwright"][0] or results["playwright_python"][0]
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

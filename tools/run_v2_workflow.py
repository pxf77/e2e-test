"""Execute a v2 App Pack through the Workflow Runtime."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from e2e_agent.workflow import WorkflowRuntime  # noqa: E402


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", required=True, help="App Pack YAML path")
    parser.add_argument("--workflow", default="smoke-static-web", help="Workflow id or YAML path")
    parser.add_argument("--env", default="local")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--output", default=None, help="Optional result JSON path")
    return parser


async def _run(args: argparse.Namespace) -> dict:
    runtime = WorkflowRuntime(repo_root=ROOT)
    return await runtime.run(
        app_path=Path(args.app),
        workflow=args.workflow,
        env=args.env,
        run_id=args.run_id,
    )


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        result = asyncio.run(_run(args))
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    rendered = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
        print(output)
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

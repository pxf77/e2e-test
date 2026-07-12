from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = next(parent for parent in Path(__file__).resolve().parents if (parent / "pyproject.toml").exists())
SRC_ROOT = ROOT_DIR / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from e2e_agent.core.model_acceptance import build_fallback_acceptance_evidence, build_model_acceptance_report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build an offline model acceptance report.")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--product-id", required=True)
    parser.add_argument("--candidates", required=True)
    parser.add_argument("--fallback-attempts")
    parser.add_argument("--output", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    candidates = json.loads(Path(args.candidates).read_text(encoding="utf-8"))
    if not isinstance(candidates, list):
        raise ValueError("Candidates payload must be a JSON array")
    fallback_evidence = None
    if args.fallback_attempts:
        attempts = json.loads(Path(args.fallback_attempts).read_text(encoding="utf-8"))
        if not isinstance(attempts, list):
            raise ValueError("Fallback attempts payload must be a JSON array")
        fallback_evidence = build_fallback_acceptance_evidence(
            run_id=args.run_id,
            product_id=args.product_id,
            route_key="all",
            attempts=[item for item in attempts if isinstance(item, dict)],
        )
    report = build_model_acceptance_report(
        run_id=args.run_id,
        product_id=args.product_id,
        candidates=[item for item in candidates if isinstance(item, dict)],
        fallback_evidence=fallback_evidence,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "summary": report["summary"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

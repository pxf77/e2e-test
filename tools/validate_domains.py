"""Validate all Domain Pack manifests under domains/."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from e2e_agent.domains import DomainPackLoader  # noqa: E402


def main() -> int:
    loader = DomainPackLoader(ROOT / "domains")
    domain_ids = loader.list_domain_ids()
    if not domain_ids:
        print("ERROR: no domain packs found under domains/", file=sys.stderr)
        return 1
    failed = 0
    for domain_id in domain_ids:
        try:
            pack = loader.load(domain_id)
        except Exception as exc:  # pragma: no cover - command-line reporting
            failed += 1
            print(f"  FAIL  {domain_id}: {exc}")
            continue
        print(f"  pass  {pack.id}@{pack.version} ({len(pack.page_types)} page types)")
    if failed:
        print(f"\nResults: {len(domain_ids) - failed}/{len(domain_ids)} passed, {failed} FAILED")
        return 1
    print(f"\nResults: {len(domain_ids)}/{len(domain_ids)} passed OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

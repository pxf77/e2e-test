"""Validate Domain Pack manifests, inheritance and sub-contracts."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from e2e_agent.contracts import ContractRegistry  # noqa: E402
from e2e_agent.domains import DomainPackLoader  # noqa: E402


def main() -> int:
    registry = ContractRegistry(ROOT / "schemas").discover()
    loader = DomainPackLoader(ROOT / "domains", registry=registry)
    domain_ids = loader.list_domain_ids()
    if not domain_ids:
        print("ERROR: no domain packs found under domains/", file=sys.stderr)
        return 1
    failed = 0
    for domain_id in domain_ids:
        try:
            pack = loader.load(domain_id)
            registry.validate("ontology", "v2", pack.ontology)
            registry.validate("assertion-pack", "v2", pack.assertion_pack)
            registry.validate("data-pack", "v2", pack.data_pack)
            page_types = set(pack.ontology.get("page_types") or {})
            for intent, chain in (pack.ontology.get("flow_chains") or {}).items():
                unknown = [item for item in chain or [] if item not in page_types]
                if unknown:
                    raise ValueError(f"flow chain {intent} references unknown page types: {unknown}")
        except Exception as exc:  # pragma: no cover - command-line reporting
            failed += 1
            print(f"  FAIL  {domain_id}: {exc}")
            continue
        lineage = ",".join(str(item) for item in pack.manifest.get("resolved_lineage") or []) or "root"
        print(f"  pass  {pack.id}@{pack.version} ({len(pack.page_types)} page types, lineage={lineage})")
    if failed:
        print(f"\nResults: {len(domain_ids) - failed}/{len(domain_ids)} passed, {failed} FAILED")
        return 1
    print(f"\nResults: {len(domain_ids)}/{len(domain_ids)} passed OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Validate runner manifests and registered workflow implementations."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from e2e_agent.config.yaml_loader import load_yaml_file  # noqa: E402
from e2e_agent.contracts import ContractRegistry  # noqa: E402
from e2e_agent.workflow.defaults import build_default_node_registry  # noqa: E402


def main() -> int:
    contract_registry = ContractRegistry(ROOT / "schemas").discover()
    node_registry = build_default_node_registry(ROOT)
    files = sorted((ROOT / "runners").glob("*.yaml"))
    if not files:
        print("ERROR: no runner manifests found", file=sys.stderr)
        return 1
    failed = 0
    for path in files:
        try:
            payload = load_yaml_file(path)
            contract_registry.validate("execution-runner", "v2", payload)
            implementation = str(payload["implementation"])
            registered = node_registry.get(implementation)
            if registered.kind != "runner":
                raise ValueError(f"{implementation} is registered as {registered.kind}, expected runner")
            print(f"  pass  {payload['id']}@{payload['version']} -> {implementation}")
        except Exception as exc:  # pragma: no cover
            failed += 1
            print(f"  FAIL  {path.relative_to(ROOT)}: {exc}")
    print(f"\nResults: {len(files) - failed}/{len(files)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Validate plugin manifests, entries and workflow registrations."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from e2e_agent.contracts import ContractRegistry  # noqa: E402
from e2e_agent.plugins import PluginManager  # noqa: E402
from e2e_agent.workflow.registry import NodeRegistry  # noqa: E402


def main() -> int:
    manager = PluginManager(
        ROOT / "plugins",
        contract_registry=ContractRegistry(ROOT / "schemas").discover(),
    )
    try:
        manifests = manager.discover()
        registry = NodeRegistry()
        manager.register_nodes(registry)
        for manifest in manifests:
            registered = registry.get(manifest.implementation_id) if manifest.kind in {"node", "skill"} else None
            suffix = f" -> {registered.id}" if registered else ""
            print(f"  pass  {manifest.id}@{manifest.version} ({manifest.runtime_type}){suffix}")
    except Exception as exc:  # pragma: no cover - CLI reporting
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(f"\nResults: {len(manifests)}/{len(manifests)} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

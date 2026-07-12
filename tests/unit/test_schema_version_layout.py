from __future__ import annotations

from pathlib import Path

from e2e_agent.contracts import ContractRegistry
from tools.validate.schemas import validate_layout

ROOT = Path(__file__).resolve().parents[2]


def test_repository_schemas_are_versioned() -> None:
    assert validate_layout() == []
    assert not list((ROOT / "schemas").glob("*.schema.json"))
    assert len(list((ROOT / "schemas" / "v1").glob("*.schema.json"))) == 18
    assert list((ROOT / "schemas" / "v2").glob("*.schema.json"))


def test_contract_registry_discovers_same_name_by_version() -> None:
    registry = ContractRegistry(ROOT / "schemas").discover()

    assert registry.has("test-report", "v1")
    assert registry.has("execution-result", "v2")


def test_layout_validator_rejects_unversioned_contract(tmp_path: Path) -> None:
    (tmp_path / "schemas" / "v1").mkdir(parents=True)
    (tmp_path / "schemas" / "v2").mkdir(parents=True)
    for version in ("v1", "v2"):
        (tmp_path / "schemas" / version / "sample.schema.json").write_text("{}", encoding="utf-8")
    (tmp_path / "schemas" / "legacy.schema.json").write_text("{}", encoding="utf-8")

    assert validate_layout(tmp_path) == ["unversioned root schemas remain: ['legacy.schema.json']"]

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jsonschema
from jsonschema import Draft7Validator


class ContractValidationError(ValueError):
    """Raised when a payload does not satisfy a registered contract."""


@dataclass(frozen=True)
class ContractRef:
    name: str
    version: str
    schema_path: Path

    @property
    def key(self) -> str:
        return f"{self.name}@{self.version}"


class ContractRegistry:
    """Discovers and validates JSON Schema contracts.

    The registry intentionally stays domain-agnostic. Domain Pack semantics are
    loaded by ``e2e_agent.domains``; this class only knows schema names,
    versions, and validation mechanics.
    """

    def __init__(self, schemas_root: Path | None = None) -> None:
        self.schemas_root = schemas_root or Path(__file__).resolve().parents[3] / "schemas"
        self._contracts: dict[str, ContractRef] = {}
        self._schemas: dict[str, dict[str, Any]] = {}

    def discover(self) -> "ContractRegistry":
        if not self.schemas_root.exists():
            return self
        for schema_path in sorted(self.schemas_root.rglob("*.schema.json")):
            version = self._infer_version(schema_path)
            name = schema_path.name.removesuffix(".schema.json")
            self.register(name=name, version=version, schema_path=schema_path)
        return self

    def register(self, name: str, version: str, schema_path: Path) -> None:
        ref = ContractRef(name=name, version=version, schema_path=schema_path)
        self._contracts[ref.key] = ref

    def has(self, name: str, version: str) -> bool:
        return f"{name}@{version}" in self._contracts

    def get(self, name: str, version: str) -> ContractRef:
        key = f"{name}@{version}"
        try:
            return self._contracts[key]
        except KeyError as exc:
            known = ", ".join(sorted(self._contracts)) or "<none>"
            raise KeyError(f"Unknown contract {key}. Known: {known}") from exc

    def list(self) -> list[ContractRef]:
        return [self._contracts[key] for key in sorted(self._contracts)]

    def load_schema(self, name: str, version: str) -> dict[str, Any]:
        key = f"{name}@{version}"
        if key not in self._schemas:
            ref = self.get(name, version)
            self._schemas[key] = json.loads(ref.schema_path.read_text(encoding="utf-8"))
        return self._schemas[key]

    def validate(self, name: str, version: str, payload: dict[str, Any]) -> None:
        schema = self.load_schema(name, version)
        try:
            Draft7Validator(schema).validate(payload)
        except jsonschema.ValidationError as exc:
            path = ".".join(str(item) for item in exc.absolute_path) or "<root>"
            raise ContractValidationError(f"{name}@{version} validation failed at {path}: {exc.message}") from exc

    def migrate(
        self,
        name: str,
        from_version: str,
        to_version: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Compatibility hook for future v1 -> v2 migrations.

        The initial implementation is intentionally conservative: it only
        returns the input payload when the source and target versions match.
        Version-changing migrations must be added explicitly in adapters.
        """
        if from_version != to_version:
            raise NotImplementedError(f"Migration {name}@{from_version} -> {to_version} is not implemented")
        self.validate(name, to_version, payload)
        return dict(payload)

    @staticmethod
    def _infer_version(schema_path: Path) -> str:
        for parent in schema_path.parents:
            if parent.name.startswith("v") and parent.name[1:].isdigit():
                return parent.name
        return "v1"

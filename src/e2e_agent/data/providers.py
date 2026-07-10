from __future__ import annotations

import csv
import json
import os
import random
import sqlite3
import string
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib import request


@dataclass(frozen=True)
class ProviderValue:
    value: Any
    sensitive: bool = False
    metadata: dict[str, Any] | None = None


class DataProvider(Protocol):
    name: str

    def load(self, profile: dict[str, Any], base_dir: Path) -> ProviderValue:
        ...


class StaticJsonProvider:
    name = "static_json"

    def load(self, profile: dict[str, Any], base_dir: Path) -> ProviderValue:
        file_name = profile.get("file")
        if not file_name:
            raise ValueError("static_json profile requires 'file'")
        path = base_dir / str(file_name)
        payload = json.loads(path.read_text(encoding="utf-8"))
        return ProviderValue(payload, metadata={"path": str(path)})


class CsvProvider:
    name = "csv"

    def load(self, profile: dict[str, Any], base_dir: Path) -> ProviderValue:
        file_name = profile.get("file")
        if not file_name:
            raise ValueError("csv profile requires 'file'")
        path = base_dir / str(file_name)
        with path.open(encoding=str(profile.get("encoding") or "utf-8-sig"), newline="") as stream:
            rows = list(csv.DictReader(stream))
        if not rows:
            raise ValueError(f"csv profile has no data rows: {path}")
        index = int(profile.get("index") or 0)
        if index < 0 or index >= len(rows):
            raise IndexError(f"csv profile index {index} out of range for {path}")
        return ProviderValue(dict(rows[index]), metadata={"path": str(path), "row_index": index})


class FakerProvider:
    """Small deterministic synthetic-data provider without a production dependency."""

    name = "faker"
    _FIRST_NAMES = ("Alex", "Taylor", "Jordan", "Morgan", "Casey", "Riley")
    _LAST_NAMES = ("Chen", "Li", "Wang", "Smith", "Garcia", "Kim")
    _STREETS = ("Main Street", "Oak Road", "River Avenue", "Lake Lane")

    def load(self, profile: dict[str, Any], base_dir: Path) -> ProviderValue:
        seed = str(profile.get("seed") or profile.get("locale") or "e2e-agent")
        rng = random.Random(seed)
        fields = profile.get("fields") or {}
        result: dict[str, Any] = {}
        for field_name, generator in fields.items():
            result[str(field_name)] = self._generate(str(generator), rng)
        return ProviderValue(result, metadata={"seed": seed, "synthetic": True})

    def _generate(self, generator: str, rng: random.Random) -> str:
        token = generator.lower()
        first = rng.choice(self._FIRST_NAMES)
        last = rng.choice(self._LAST_NAMES)
        if token in {"person.name", "name"}:
            return f"{first} {last}"
        if token in {"internet.email", "email"}:
            suffix = rng.randint(100, 999)
            return f"{first.lower()}.{last.lower()}{suffix}@example.test"
        if token in {"phone_number", "phone", "mobile"}:
            return "1" + "".join(rng.choice(string.digits) for _ in range(10))
        if token in {"address", "street_address"}:
            return f"{rng.randint(1, 999)} {rng.choice(self._STREETS)}"
        if token in {"uuid", "id"}:
            return "".join(rng.choice(string.ascii_lowercase + string.digits) for _ in range(16))
        return "".join(rng.choice(string.ascii_letters + string.digits) for _ in range(12))


class SecretRefProvider:
    name = "secret_ref"

    def load(self, profile: dict[str, Any], base_dir: Path) -> ProviderValue:
        env_name = str(profile.get("secret") or profile.get("env") or "")
        if not env_name:
            raise ValueError("secret_ref profile requires 'secret' or 'env'")
        value = os.environ.get(env_name)
        if value is None:
            raise KeyError(f"Required secret environment variable is not set: {env_name}")
        field = str(profile.get("field") or "value")
        return ProviderValue({field: value}, sensitive=True, metadata={"env": env_name})


class AccountPoolProvider:
    name = "account_pool"

    def load(self, profile: dict[str, Any], base_dir: Path) -> ProviderValue:
        file_name = profile.get("file")
        if not file_name:
            raise ValueError("account_pool profile requires 'file'")
        path = base_dir / str(file_name)
        payload = json.loads(path.read_text(encoding="utf-8"))
        accounts = payload.get("accounts") if isinstance(payload, dict) else payload
        if not isinstance(accounts, list) or not accounts:
            raise ValueError(f"account pool must contain a non-empty list: {path}")
        index = int(os.environ.get("E2E_ACCOUNT_INDEX", profile.get("index") or 0)) % len(accounts)
        account = accounts[index]
        if not isinstance(account, dict):
            raise ValueError(f"account pool entry must be an object: {path}#{index}")
        return ProviderValue(dict(account), sensitive=True, metadata={"path": str(path), "account_index": index})


class ApiSeedProvider:
    name = "api_seed"

    def load(self, profile: dict[str, Any], base_dir: Path) -> ProviderValue:
        url = str(profile.get("url") or "")
        if not url:
            raise ValueError("api_seed profile requires 'url'")
        method = str(profile.get("method") or "POST").upper()
        body = profile.get("body")
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {str(key): str(value) for key, value in (profile.get("headers") or {}).items()}
        headers.setdefault("Content-Type", "application/json")
        req = request.Request(url, data=data, headers=headers, method=method)
        timeout = float(profile.get("timeout_seconds") or 15)
        with request.urlopen(req, timeout=timeout) as response:  # noqa: S310 - explicit test-data endpoint
            raw = response.read().decode("utf-8")
            content_type = response.headers.get("Content-Type", "")
            value = json.loads(raw) if "json" in content_type and raw else {"body": raw}
            return ProviderValue(value, metadata={"url": url, "status": response.status})


class DbSeedProvider:
    name = "db_seed"

    def load(self, profile: dict[str, Any], base_dir: Path) -> ProviderValue:
        driver = str(profile.get("driver") or "sqlite")
        if driver != "sqlite":
            raise ValueError(f"Only sqlite db_seed is supported by the built-in provider, got: {driver}")
        database = Path(str(profile.get("database") or ":memory:"))
        database_path = database if database.is_absolute() or str(database) == ":memory:" else base_dir / database
        statements = [str(item) for item in profile.get("statements") or []]
        query = profile.get("query")
        with sqlite3.connect(str(database_path)) as connection:
            for statement in statements:
                connection.execute(statement)
            rows: list[dict[str, Any]] = []
            if query:
                cursor = connection.execute(str(query))
                columns = [str(item[0]) for item in cursor.description or []]
                rows = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
            connection.commit()
        return ProviderValue({"rows": rows}, metadata={"database": str(database_path), "statement_count": len(statements)})


class DataProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, DataProvider] = {}
        for provider in (
            StaticJsonProvider(),
            CsvProvider(),
            FakerProvider(),
            SecretRefProvider(),
            AccountPoolProvider(),
            ApiSeedProvider(),
            DbSeedProvider(),
        ):
            self.register(provider)

    def register(self, provider: DataProvider) -> None:
        self._providers[provider.name] = provider

    def get(self, name: str) -> DataProvider:
        try:
            return self._providers[name]
        except KeyError as exc:
            raise KeyError(f"Unknown data provider: {name}") from exc

    def list_names(self) -> list[str]:
        return sorted(self._providers)

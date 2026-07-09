from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class PluginResult:
    status: str
    outputs: dict[str, Any] = field(default_factory=dict)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    error: dict[str, Any] | None = None


class PluginRuntime:
    """Subprocess JSON stdin/stdout runtime for v2 plugins."""

    def run_python(self, entry: Path, payload: dict[str, Any], timeout_seconds: int = 300) -> PluginResult:
        completed = subprocess.run(
            [sys.executable, str(entry)],
            input=json.dumps(payload, ensure_ascii=False),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
        if completed.returncode != 0:
            return PluginResult(
                status="failed",
                error={
                    "type": "PluginRuntimeError",
                    "message": completed.stderr or completed.stdout,
                    "returncode": completed.returncode,
                    "retryable": False,
                },
            )
        try:
            raw = json.loads(completed.stdout or "{}")
        except json.JSONDecodeError as exc:
            return PluginResult(
                status="failed",
                error={"type": "PluginProtocolError", "message": str(exc), "retryable": False},
            )
        return PluginResult(
            status=str(raw.get("status") or "success"),
            outputs=raw.get("outputs") or {},
            artifacts=raw.get("artifacts") or [],
            metrics=raw.get("metrics") or {},
            warnings=[str(item) for item in raw.get("warnings") or []],
            error=raw.get("error"),
        )

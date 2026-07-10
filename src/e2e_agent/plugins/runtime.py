from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .loader import PluginManifest


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

    def run(self, manifest: PluginManifest, payload: dict[str, Any]) -> PluginResult:
        timeout = int((manifest.payload.get("runtime") or {}).get("timeout_seconds") or 300)
        if manifest.runtime_type == "python":
            command = [sys.executable, str(manifest.entry_path)]
        elif manifest.runtime_type == "node":
            node = shutil.which("node")
            if not node:
                raise FileNotFoundError("Node.js is required for this plugin")
            command = [node, str(manifest.entry_path)]
        else:
            raise ValueError(f"Unsupported plugin runtime: {manifest.runtime_type}")
        return self._run_command(command, manifest.root, payload, timeout)

    def run_python(self, entry: Path, payload: dict[str, Any], timeout_seconds: int = 300) -> PluginResult:
        return self._run_command([sys.executable, str(entry)], entry.parent, payload, timeout_seconds)

    @staticmethod
    def _run_command(
        command: list[str],
        cwd: Path,
        payload: dict[str, Any],
        timeout_seconds: int,
    ) -> PluginResult:
        try:
            completed = subprocess.run(
                command,
                cwd=str(cwd),
                input=json.dumps(payload, ensure_ascii=False),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return PluginResult(
                status="failed",
                error={
                    "type": "PluginTimeoutError",
                    "message": f"Plugin timed out after {timeout_seconds}s",
                    "retryable": True,
                    "stdout": str(exc.stdout or ""),
                    "stderr": str(exc.stderr or ""),
                },
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
                error={
                    "type": "PluginProtocolError",
                    "message": str(exc),
                    "stdout": completed.stdout,
                    "retryable": False,
                },
            )
        if not isinstance(raw, dict):
            return PluginResult(
                status="failed",
                error={"type": "PluginProtocolError", "message": "Plugin stdout must be a JSON object", "retryable": False},
            )
        return PluginResult(
            status=str(raw.get("status") or "success"),
            outputs=raw.get("outputs") or {},
            artifacts=raw.get("artifacts") or [],
            metrics=raw.get("metrics") or {},
            warnings=[str(item) for item in raw.get("warnings") or []],
            error=raw.get("error"),
        )

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

_FALLBACK_BASETEMP_ATTR = "_e2e_agent_fallback_basetemp"


def _unique_basetemp_name() -> str:
    return f"e2e-agent-pytest-{os.getpid()}-{uuid.uuid4().hex[:8]}"


def _select_safe_basetemp(raw_basetemp: str | os.PathLike[str]) -> tuple[Path, bool]:
    requested = Path(os.path.abspath(str(raw_basetemp)))
    if not requested.exists():
        return requested, False

    # Stale Windows basetemp directories can become unreadable or locked.
    # Use a fresh sibling so pytest does not fail while trying to rm_rf them.
    parent = requested.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        parent = Path(tempfile.gettempdir())
    return parent / _unique_basetemp_name(), True


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config) -> None:
    raw_basetemp = getattr(config.option, "basetemp", None)
    if not raw_basetemp:
        return

    selected_basetemp, used_fallback = _select_safe_basetemp(raw_basetemp)
    if not used_fallback:
        return

    config.option.basetemp = str(selected_basetemp)
    setattr(config, _FALLBACK_BASETEMP_ATTR, selected_basetemp)

    tmp_path_factory = getattr(config, "_tmp_path_factory", None)
    if tmp_path_factory is not None:
        tmp_path_factory._given_basetemp = selected_basetemp
        tmp_path_factory._basetemp = None


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    fallback_basetemp = getattr(session.config, _FALLBACK_BASETEMP_ATTR, None)
    if fallback_basetemp is not None:
        shutil.rmtree(fallback_basetemp, ignore_errors=True)

from __future__ import annotations

from pathlib import Path

from tests import conftest as test_config


def test_select_safe_basetemp_keeps_missing_requested_path(tmp_path: Path) -> None:
    requested = tmp_path / "fresh-basetemp"

    selected, used_fallback = test_config._select_safe_basetemp(str(requested))

    assert selected == requested
    assert used_fallback is False


def test_select_safe_basetemp_falls_back_when_requested_path_exists(
    tmp_path: Path,
    monkeypatch,
) -> None:
    requested = tmp_path / "stale-basetemp"
    requested.mkdir()
    monkeypatch.setattr(
        test_config,
        "_unique_basetemp_name",
        lambda: "e2e-agent-pytest-fallback-test",
    )

    selected, used_fallback = test_config._select_safe_basetemp(str(requested))

    assert selected == requested.parent / "e2e-agent-pytest-fallback-test"
    assert used_fallback is True

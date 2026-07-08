"""Entry-page probing helpers for knowledge-base generation."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from e2e_agent.browser.session import BrowserSession
from e2e_agent.core.page_exploration import (
    _body_text_excerpt,
    _collect_actions,
    _collect_fields,
    _page_signature,
    _primary_actions,
)
from e2e_agent.core.script_generation import platform_from_entry_url


def _viewport_for_url(entry_url: str) -> dict[str, int]:
    if platform_from_entry_url(entry_url) == "h5":
        return {"width": 390, "height": 844}
    return {"width": 1280, "height": 720}


async def _safe_load_settle(page: Any) -> None:
    for state in ("domcontentloaded", "networkidle"):
        try:
            await page.wait_for_load_state(state, timeout=5_000)
        except Exception:
            continue
    try:
        await page.wait_for_timeout(500)
    except Exception:
        pass


async def probe_entry_page(
    entry_url: str,
    *,
    screenshot_dir: str | Path,
    session_factory: Any = BrowserSession,
    headless: bool = True,
    viewport: dict[str, int] | None = None,
    timeout_ms: int = 30_000,
) -> dict[str, Any]:
    """Open an entry URL and collect a stable UI knowledge snapshot."""
    url = str(entry_url or "").strip()
    if not url:
        raise ValueError("entry_url is required for page-probe mode")

    screenshot_root = Path(screenshot_dir)
    screenshot_root.mkdir(parents=True, exist_ok=True)
    screenshot_path = screenshot_root / "entry.png"

    async with session_factory(
        headless=headless,
        viewport=viewport or _viewport_for_url(url),
    ) as session:
        page = session.page
        if page is None:
            raise RuntimeError("BrowserSession did not create a page")
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        await _safe_load_settle(page)

        fields = await _collect_fields(page)
        actions = await _collect_actions(page)
        page_url = str(getattr(page, "url", url) or url)
        primary_actions = _primary_actions(page_url, actions)
        title = await page.title()
        body_text_excerpt = await _body_text_excerpt(page)
        dom_signature = await _page_signature(page)
        await page.screenshot(path=str(screenshot_path), full_page=True)

    return {
        "status": "completed",
        "mode": "page-probe",
        "entry_url": url,
        "url": page_url,
        "title": title,
        "dom_signature": dom_signature,
        "body_text_excerpt": body_text_excerpt,
        "field_count": len(fields),
        "action_count": len(actions),
        "primary_action_count": len(primary_actions),
        "fields": fields,
        "actions": actions,
        "primary_actions": primary_actions,
        "screenshot_path": str(screenshot_path),
    }

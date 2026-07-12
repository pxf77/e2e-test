"""BrowserSession — async context manager for Playwright Python browser sessions."""
from __future__ import annotations

from playwright.async_api import Browser, BrowserContext, Page, async_playwright


class BrowserSession:
    """Manages a single Playwright Chromium browser session.

    Usage:
        async with BrowserSession(headless=True) as session:
            await session.page.goto("https://example.com")

    Viewport defaults to H5 (390×844) per the pingan-xiaoshentong7 pilot spec.
    Pass viewport={"width": 1280, "height": 720} for PC.
    """

    def __init__(
        self,
        headless: bool = True,
        viewport: dict | None = None,
        slow_mo: int = 0,
        storage_state_path: str | None = None,
        record_storage_state: bool = False,
    ) -> None:
        self._headless = headless
        self._viewport = viewport or {"width": 390, "height": 844}
        self._slow_mo = slow_mo
        self._storage_state_path = storage_state_path
        self._record_storage_state = record_storage_state
        self._pw = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self.page: Page | None = None

    async def __aenter__(self) -> "BrowserSession":
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=self._headless,
            slow_mo=self._slow_mo,
        )
        context_args = {"viewport": self._viewport}
        if int(self._viewport.get("width", 0) or 0) <= 500:
            context_args.update(
                {
                    "is_mobile": True,
                    "has_touch": True,
                    "user_agent": (
                        "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 "
                        "Mobile/15E148 Safari/604.1"
                    ),
                }
            )
        if self._storage_state_path:
            from pathlib import Path

            storage_state = Path(self._storage_state_path)
            if storage_state.exists():
                context_args["storage_state"] = str(storage_state)
        self._context = await self._browser.new_context(**context_args)
        self.page = await self._context.new_page()
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._context and self._record_storage_state and self._storage_state_path:
            from pathlib import Path

            Path(self._storage_state_path).parent.mkdir(parents=True, exist_ok=True)
            await self._context.storage_state(path=self._storage_state_path)
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()

    async def navigate(self, url: str, timeout: int = 30_000) -> None:
        if self.page is None:
            raise RuntimeError("BrowserSession not started — use as async context manager")
        await self.page.goto(url, timeout=timeout)

    async def screenshot(self, path: str) -> None:
        if self.page is None:
            raise RuntimeError("BrowserSession not started")
        await self.page.screenshot(path=path)

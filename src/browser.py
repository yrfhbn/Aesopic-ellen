"""
browser.py
==========
A thin wrapper around Playwright that exposes ONLY vision-friendly, selector-free
primitives: take a screenshot, click at a pixel coordinate, type text, press keys,
and scroll. The agent never queries the DOM by CSS/XPath; every action is driven by
what the vision model "sees" in a screenshot. This is the core of the
"resilient to HTML changes" requirement.

The screenshots are taken at a fixed viewport size so that the pixel coordinates the
vision model returns map deterministically onto the page.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from playwright.sync_api import sync_playwright, Browser, Page, Playwright


# We pin the viewport so the coordinate space the model reasons about is stable.
VIEWPORT_WIDTH = 1280
VIEWPORT_HEIGHT = 900


@dataclass
class Screenshot:
    """Container for a screenshot plus the viewport it was taken in."""
    png_bytes: bytes
    width: int
    height: int

    def to_base64(self) -> str:
        return base64.standard_b64encode(self.png_bytes).decode("ascii")

    def save(self, path: str | Path) -> None:
        Path(path).write_bytes(self.png_bytes)


class BrowserSession:
    """
    Manages a single Playwright browser session.

    Usage:
        with BrowserSession(headless=True) as b:
            b.goto("https://github.com")
            shot = b.screenshot()
            b.click(640, 120)
    """

    def __init__(self, headless: bool = True, slow_mo_ms: int = 0):
        self._headless = headless
        self._slow_mo = slow_mo_ms
        self._pw: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._page: Optional[Page] = None

    # -- lifecycle ---------------------------------------------------------
    def __enter__(self) -> "BrowserSession":
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=self._headless,
            slow_mo=self._slow_mo,
        )
        context = self._browser.new_context(
            viewport={"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT},
            # A real UA string reduces the chance of being served a degraded page.
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
            ),
        )
        self._page = context.new_page()
        self._page.set_default_timeout(15_000)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if self._browser:
                self._browser.close()
        finally:
            if self._pw:
                self._pw.stop()

    # -- guards ------------------------------------------------------------
    @property
    def page(self) -> Page:
        if self._page is None:
            raise RuntimeError("BrowserSession used outside of its context manager.")
        return self._page

    # -- navigation primitives --------------------------------------------
    def goto(self, url: str) -> None:
        self.page.goto(url, wait_until="domcontentloaded")
        self._settle()

    def current_url(self) -> str:
        return self.page.url

    def screenshot(self) -> Screenshot:
        png = self.page.screenshot(type="png")
        return Screenshot(png_bytes=png, width=VIEWPORT_WIDTH, height=VIEWPORT_HEIGHT)

    # -- vision-driven actions (coordinates are top-left origin) -----------
    def click(self, x: int, y: int) -> None:
        self._clamp(x, y)
        self.page.mouse.click(x, y)
        self._settle()

    def type_text(self, text: str) -> None:
        # Types into whatever currently has focus (e.g. a search box just clicked).
        self.page.keyboard.type(text, delay=20)

    def press(self, key: str) -> None:
        self.page.keyboard.press(key)
        self._settle()

    def scroll(self, dy: int) -> None:
        """Scroll vertically by dy pixels (positive = down)."""
        self.page.mouse.wheel(0, dy)
        self.page.wait_for_timeout(400)

    # -- helpers -----------------------------------------------------------
    def _settle(self) -> None:
        """Best-effort wait for the page to quiet down after an action."""
        try:
            self.page.wait_for_load_state("networkidle", timeout=5_000)
        except Exception:
            # networkidle can legitimately never fire on chatty pages; that's fine.
            self.page.wait_for_timeout(600)

    def _clamp(self, x: int, y: int) -> None:
        if not (0 <= x <= VIEWPORT_WIDTH and 0 <= y <= VIEWPORT_HEIGHT):
            raise ValueError(
                f"Coordinate ({x},{y}) is outside the "
                f"{VIEWPORT_WIDTH}x{VIEWPORT_HEIGHT} viewport."
            )

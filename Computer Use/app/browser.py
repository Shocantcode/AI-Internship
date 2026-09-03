"""Small Playwright abstraction used by the computer-use agent."""

from __future__ import annotations

import os
from contextlib import suppress

from dotenv import load_dotenv
from playwright.sync_api import Browser, Page, Playwright, TimeoutError, sync_playwright

load_dotenv()


class BrowserController:
    def __init__(self, headless: bool | None = None, timeout_ms: int | None = None):
        self.headless = headless if headless is not None else os.getenv("HEADLESS", "true").lower() == "true"
        self.timeout_ms = timeout_ms or int(os.getenv("BROWSER_TIMEOUT", "30000"))
        self.playwright: Playwright | None = None
        self.browser: Browser | None = None
        self.page: Page | None = None

    def start(self) -> Page:
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(headless=self.headless)
        self.page = self.browser.new_page(viewport={"width": 1440, "height": 900})
        self.page.set_default_timeout(self.timeout_ms)
        return self.page

    def _require_page(self) -> Page:
        if self.page is None:
            raise RuntimeError("BrowserController.start() must be called first")
        return self.page

    def open_url(self, url: str) -> None:
        page = self._require_page()
        page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)

    def screenshot(self, path: str | None = None) -> bytes:
        return self._require_page().screenshot(path=path, full_page=False)

    def click(self, selector: str, *, text: str | None = None) -> None:
        page = self._require_page()
        locator = page.locator(selector)
        if text:
            locator = locator.filter(has_text=text)
        locator.first.click()

    def type_text(self, selector: str, text: str, *, submit: bool = False) -> None:
        locator = self._require_page().locator(selector).first
        locator.fill(text)
        if submit:
            locator.press("Enter")

    def scroll(self, amount: int = 700) -> None:
        self._require_page().mouse.wheel(0, amount)

    def go_back(self) -> None:
        self._require_page().go_back(wait_until="domcontentloaded", timeout=self.timeout_ms)

    def wait(self, milliseconds: int = 1000) -> None:
        self._require_page().wait_for_timeout(milliseconds)

    def get_current_url(self) -> str:
        return self._require_page().url

    def read(self) -> str:
        return self._require_page().locator("body").inner_text(timeout=self.timeout_ms)

    def close(self) -> None:
        with suppress(Exception):
            if self.browser:
                self.browser.close()
        with suppress(Exception):
            if self.playwright:
                self.playwright.stop()
        self.page = None
        self.browser = None
        self.playwright = None

    def __enter__(self) -> "BrowserController":
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

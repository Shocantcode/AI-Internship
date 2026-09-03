"""DOM-based article extraction and text cleanup."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from .models import NewsArticle

if TYPE_CHECKING:
    from playwright.sync_api import Page


class NewsExtractor:
    REMOVE_SELECTORS = (
        "script", "style", "noscript", "iframe", "nav", "header", "footer",
        "aside", "form", ".ad", ".ads", "[class*='advert' i]", "[class*='banner' i]",
        "[class*='social' i]", "[class*='related' i]", "[class*='recommend' i]",
        "[class*='share' i]", "[id*='related' i]", "[id*='advert' i]",
    )

    @staticmethod
    def clean_text(value: str | None) -> str:
        value = re.sub(r"\s+", " ", value or "").strip()
        return value

    @classmethod
    def clean_paragraphs(cls, paragraphs: list[str]) -> str:
        cleaned: list[str] = []
        seen: set[str] = set()
        for paragraph in paragraphs:
            text = cls.clean_text(paragraph)
            lowered = text.lower()
            if any(marker in lowered for marker in ("baca juga:", "advertisement", "sponsored", "ikuti kami")):
                continue
            if text and text not in seen:
                seen.add(text)
                cleaned.append(text)
        return "\n\n".join(cleaned)

    @staticmethod
    def _first_text(page: Any, selectors: tuple[str, ...]) -> str:
        for selector in selectors:
            try:
                text = page.locator(selector).first.inner_text(timeout=1200)
                if text.strip():
                    return NewsExtractor.clean_text(text)
            except Exception:
                continue
        return ""

    def extract(self, page: Any, fallback_title: str = "") -> NewsArticle:
        title = self._first_text(page, ("h1", "[property='og:title']")) or fallback_title
        if not title:
            raise ValueError("Judul artikel tidak ditemukan")
        paragraphs = []
        for selector in ("article p", "main p", "[role='main'] p"):
            try:
                paragraphs.extend(page.locator(selector).all_inner_texts())
            except Exception:
                pass
        content = self.clean_paragraphs(paragraphs)
        if not content:
            raw = self._first_text(page, ("article", "main", "[role='main']"))
            content = self.clean_text(raw)
        if not content:
            raise ValueError("Isi artikel tidak dapat dibaca")
        host = urlparse(page.url).netloc
        category = self._first_text(page, ("[rel='category']", "[class*='category' i]")) or "Finance"
        return NewsArticle(
            title=title,
            source="Detik Finance",
            category=category,
            author=self._first_text(page, ("[rel='author']", "[class*='author' i]")) or None,
            published_at=self._first_text(page, ("time", "[datetime]", "[property='article:published_time']")) or None,
            url=page.url,
            content=content,
        )

    @staticmethod
    def is_detik_finance_url(url: str) -> bool:
        parsed = urlparse(url)
        host = parsed.netloc.lower().split(":", 1)[0]
        return host == "finance.detik.com" or host.endswith(".finance.detik.com")

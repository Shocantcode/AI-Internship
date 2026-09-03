"""Collect visible news articles through browser interaction."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from urllib.parse import urljoin

from playwright.sync_api import Browser, Page, TimeoutError as PlaywrightTimeoutError, sync_playwright


DEFAULT_TIMEOUT_MS = 30_000
MAX_ARTICLES = 20


class NewsAgentError(RuntimeError):
    """Expected failure while using the public website UI."""


class AuthenticationRequired(NewsAgentError):
    """The website requires authentication before the content can be read."""


def _clean(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _is_authentication_page(page: Page) -> bool:
    text = _clean(page.locator("body").inner_text(timeout=3_000)).lower()
    markers = ("log in", "login", "sign in", "authentication required", "masuk")
    return any(marker in text for marker in markers) and any(
        marker in page.url.lower() for marker in ("login", "signin", "auth", "account")
    )


def _dismiss_popups(page: Page) -> None:
    for label in ("Accept", "Accept all", "I agree", "Got it", "Close", "Tutup"):
        try:
            page.get_by_role("button", name=re.compile(rf"^{re.escape(label)}$", re.I)).first.click(timeout=800)
        except (PlaywrightTimeoutError, Exception):
            continue


def _find_search_box(page: Page):
    for locator in (
        page.get_by_role("searchbox").first,
        page.locator('input[type="search"]').first,
        page.locator('input[name*="search" i], input[placeholder*="search" i]').first,
    ):
        try:
            if locator.is_visible(timeout=800):
                return locator
        except (PlaywrightTimeoutError, Exception):
            continue
    return None


def _search(page: Page, keyword: str) -> None:
    search_box = _find_search_box(page)
    if search_box is None:
        return
    search_box.fill(keyword)
    search_box.press("Enter")
    try:
        page.wait_for_load_state("domcontentloaded", timeout=DEFAULT_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        pass


def _candidate_links(page: Page, keyword: str) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    keyword_terms = {term for term in re.findall(r"\w+", keyword.lower()) if len(term) > 2}
    links = page.locator("a:visible")
    for index in range(min(links.count(), 150)):
        link = links.nth(index)
        try:
            title = _clean(link.inner_text(timeout=500))
            href = link.get_attribute("href")
        except Exception:
            continue
        if not title or not href or href.startswith(("#", "mailto:", "javascript:")):
            continue
        absolute_url = urljoin(page.url, href)
        if absolute_url.startswith(("http://", "https://")) and keyword_terms.intersection(title.lower().split()):
            candidates.append((title, absolute_url))
    return list(dict.fromkeys(candidates))


def _first_text(page: Page, selectors: tuple[str, ...]) -> str:
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            text = _clean(locator.inner_text(timeout=1_000))
            if text:
                return text
        except Exception:
            continue
    return ""


def _extract_article(page: Page, fallback_title: str, source: str) -> dict[str, Any]:
    if _is_authentication_page(page):
        raise AuthenticationRequired("Website membutuhkan authentication.")

    title = _first_text(page, ("h1", "[property='og:title']")) or fallback_title
    published = _first_text(
        page,
        ("time", "[datetime]", "[property='article:published_time']", "[class*='date' i]"),
    )
    author = _first_text(page, ("[rel='author']", "[class*='author' i]", "[property='article:author']"))
    category = _first_text(page, ("[rel='category']", "[class*='category' i]", "nav[aria-label*='breadcrumb' i]"))
    body = _first_text(page, ("article", "main", "[role='main']"))
    if not body:
        raise NewsAgentError("WARNING: Artikel ditemukan tetapi konten tidak dapat diekstrak.")

    paragraphs = [
        _clean(text)
        for text in page.locator("article p, main p, [role='main'] p").all_inner_texts()
        if _clean(text)
    ]
    content = "\n\n".join(dict.fromkeys(paragraphs)) or body
    return {
        "title": title,
        "source": source,
        "url": page.url,
        "published_date": published,
        "content": content,
        "author": author,
        "category": category,
    }


def format_news_text(articles: list[dict[str, Any]]) -> str:
    blocks = []
    for index, article in enumerate(articles, 1):
        blocks.append(
            "\n".join(
                (
                    f"NEWS {index}",
                    "",
                    f"Title: {article.get('title', '')}",
                    f"Source: {article.get('source', '')}",
                    f"Published: {article.get('published_date', '')}",
                    f"URL: {article.get('url', '')}",
                    f"Author: {article.get('author', '')}",
                    f"Category: {article.get('category', '')}",
                    "",
                    "Content:",
                    str(article.get("content", "")),
                )
            )
        )
    return "\n\n".join(blocks)


def collect_news(website_url: str, keyword: str, jumlah_berita: int = 5, timeout_seconds: int = 60) -> dict[str, Any]:
    if not website_url.strip() or not keyword.strip():
        return {"status": "error", "service": "computer-use-news", "message": "website_url dan keyword wajib diisi."}
    try:
        requested = max(1, min(int(jumlah_berita), MAX_ARTICLES))
        timeout_seconds = max(5, min(int(timeout_seconds), 300))
    except (TypeError, ValueError):
        return {"status": "error", "service": "computer-use-news", "message": "jumlah_berita dan timeout_seconds harus berupa angka."}

    deadline = datetime.now().timestamp() + timeout_seconds
    articles: list[dict[str, Any]] = []
    seen: set[str] = set()
    try:
        with sync_playwright() as playwright:
            browser: Browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.set_default_timeout(min(DEFAULT_TIMEOUT_MS, timeout_seconds * 1_000))
            try:
                page.goto(website_url, wait_until="domcontentloaded", timeout=timeout_seconds * 1_000)
                page.wait_for_load_state("networkidle", timeout=5_000)
            except PlaywrightTimeoutError:
                if not page.url:
                    return {"status": "error", "service": "computer-use-news", "message": "ERROR: Website tidak dapat diakses."}
            _dismiss_popups(page)
            if _is_authentication_page(page):
                raise AuthenticationRequired("ERROR: Website membutuhkan authentication.")
            _search(page, keyword)
            page.mouse.wheel(0, 700)
            candidates = _candidate_links(page, keyword)
            if not candidates:
                return {"status": "error", "service": "computer-use-news", "message": "ERROR: Tidak ditemukan berita yang sesuai keyword."}

            source = page.locator("meta[property='og:site_name']").get_attribute("content") or page.url.split("/")[2]
            for fallback_title, url in candidates:
                if len(articles) >= requested or datetime.now().timestamp() >= deadline:
                    break
                if url in seen:
                    continue
                seen.add(url)
                try:
                    link = page.locator("a:visible").filter(has_text=fallback_title).first
                    try:
                        link.click(timeout=2_000)
                        page.wait_for_load_state("domcontentloaded", timeout=10_000)
                    except (PlaywrightTimeoutError, Exception):
                        page.goto(url, wait_until="domcontentloaded", timeout=min(timeout_seconds * 1_000, 30_000))
                    _dismiss_popups(page)
                    article = _extract_article(page, fallback_title, source)
                    article_id = article["url"] or article["title"]
                    if article_id not in {item["url"] for item in articles}:
                        articles.append(article)
                    page.go_back(wait_until="domcontentloaded", timeout=10_000)
                except AuthenticationRequired:
                    raise
                except (PlaywrightTimeoutError, NewsAgentError):
                    continue
            if not articles:
                return {"status": "warning", "service": "computer-use-news", "message": "WARNING: Artikel ditemukan tetapi konten tidak dapat diekstrak."}
            return {
                "status": "success",
                "service": "computer-use-news",
                "website_url": website_url,
                "keyword": keyword,
                "requested": requested,
                "count": len(articles),
                "articles": articles,
                "text": format_news_text(articles),
            }
    except AuthenticationRequired as exc:
        return {"status": "error", "service": "computer-use-news", "message": str(exc)}
    except (OSError, PlaywrightTimeoutError, NewsAgentError):
        return {"status": "error", "service": "computer-use-news", "message": "ERROR: Website tidak dapat diakses."}
    except Exception as exc:
        return {"status": "error", "service": "computer-use-news", "message": f"ERROR: Website tidak dapat diakses. ({exc})"}

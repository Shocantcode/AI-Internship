"""Detik Finance computer-use workflow."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import logging
import os
import re
from datetime import datetime
import shutil
import json
from uuid import uuid4
from pathlib import Path
from typing import Callable, Any
from urllib.parse import quote, urljoin

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

from .browser import BrowserController
from .extractor import NewsExtractor
from .models import NewsArticle, NewsSearchResult
from .prompts import ACTION_NAMES, AGENT_SYSTEM_PROMPT
from .rag_integration import expand_query, normalize_query, retrieve_context

LOG = logging.getLogger(__name__)
HOME_URL = "https://finance.detik.com/"
MAX_SEARCH_ATTEMPTS = 4


def _debug(message: str, *args: object) -> None:
    if os.getenv("DEBUG_MODE", "false").lower() == "true":
        LOG.info("[DEBUG] " + message, *args)


class ComputerUseAgent:
    def __init__(self, browser: BrowserController | None = None, max_steps: int | None = None, timeout_seconds: int | None = None, progress_callback: Callable | None = None):
        self.browser = browser or BrowserController()
        self.max_steps = max_steps or int(os.getenv("MAX_STEPS", "30"))
        self.timeout_seconds = timeout_seconds or int(os.getenv("TIMEOUT", "120"))
        self.extractor = NewsExtractor()
        self.steps = 0
        self.deadline = 0.0
        self.debug_folder = Path(os.getenv("DEBUGGING_FOLDER", Path(__file__).resolve().parents[1] / "DebuggingFolder"))
        self.current_query = ""
        self.progress_callback = progress_callback
        self._activities = []
        self._current_request_id = ""

    def _report_activity(self, stage: str, action: str, status: str, label: str, duration_ms: int | None = None, result_count: int | None = None, error: dict | None = None, tool: str = "collect_detik_finance_news") -> None:
        payload = {
            "type": "activity",
            "request_id": self._current_request_id,
            "stage": stage,
            "action": action,
            "status": status,
            "label": label,
            "tool": tool,
            "timestamp": datetime.now().astimezone().isoformat()
        }
        if duration_ms is not None:
            payload["duration_ms"] = duration_ms
        if result_count is not None:
            payload["result_count"] = result_count
        if error is not None:
            payload["error"] = error
            
        self._activities.append(payload)
        self._write_debug("activity.json", self._activities)
        
        if self.progress_callback:
            try:
                import inspect
                sig = inspect.signature(self.progress_callback)
                if len(sig.parameters) >= 4:
                    self.progress_callback(0.0, 100.0, label, payload)
                else:
                    self.progress_callback(0.0, 100.0, label)
            except Exception as exc:
                LOG.debug("Progress notification failed: %s", exc)

    def _report_progress(self, progress: float, message: str) -> None:
        if self.progress_callback:
            try:
                self.progress_callback(progress, 100.0, message)
            except Exception as exc:
                LOG.debug("Progress notification failed: %s", exc)

    def _start_debug_session(self, query: str, request_id: str | None) -> None:
        self.debug_folder.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.research_session_id = f"research_{timestamp}_{uuid4().hex[:6]}"
        self.session_folder = self.debug_folder / self.research_session_id
        self.session_folder.mkdir(parents=True, exist_ok=False)
        query_analysis = self._analyze_query(query)
        self._write_debug("query.json", {
            "research_session_id": self.research_session_id,
            "request_id": request_id,
            "query": query,
            "created_at": datetime.now().astimezone().isoformat(),
            **query_analysis,
        })
        (self.session_folder / "actions.log").write_text(f"QUERY: {query}\n", encoding="utf-8")

    @staticmethod
    def _analyze_query(query: str) -> dict[str, object]:
        lowered = query.lower()
        current = bool(re.search(r"bitcoin|crypto|btc|harga|turun|naik|berita|terbaru|hari ini|market|pasar", lowered))
        forecast = bool(re.search(r"forecast|prediksi|proyeksi|masa depan", lowered))
        document = bool(re.search(r"laporan|report|dokumen|invoice|revenue", lowered))
        intent = "document_question" if document and not current else "current_market_analysis" if current else "general_question"
        return {"intent": intent, "requires_external_research": current and not document, "requires_forecast": forecast}

    def _write_debug(self, name: str, payload: object) -> None:
        if getattr(self, "session_folder", None) is None:
            return
        target = self.session_folder / name
        if isinstance(payload, str):
            target.write_text(payload, encoding="utf-8")
        else:
            target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    def _step(self, action: str) -> None:
        if action not in ACTION_NAMES:
            raise ValueError(f"Unsupported action: {action}")
        self.steps += 1
        if self.steps > self.max_steps:
            raise RuntimeError(f"MAX_STEPS tercapai ({self.max_steps})")
        LOG.debug("[%s] step=%s", action, self.steps)
        if os.getenv("DEBUG_SCREENSHOTS", "true").lower() == "true":
            with (self.session_folder / "actions.log").open("a", encoding="utf-8") as log_file:
                log_file.write(f"STEP {self.steps:03d} | {action}\n")
        LOG.info("[STEP %03d] %s", self.steps, action)
        self._report_progress(min(self.steps * 5, 35), f"Step {self.steps}: {action}")

    def _capture(self, browser: BrowserController, action: str) -> None:
        """Save a numbered screenshot so browser actions can be inspected later."""
        if os.getenv("DEBUG_SCREENSHOTS", "true").lower() != "true":
            return
        try:
            self.session_folder.mkdir(parents=True, exist_ok=True)
            filename = f"{self.steps:03d}_{action.lower()}.png"
            browser.screenshot(str(self.session_folder / filename))
            LOG.info("[DEBUG] Screenshot saved: %s", self.session_folder / filename)
        except Exception as exc:
            LOG.warning("[DEBUG] Screenshot failed for %s: %s", action, exc)

    @staticmethod
    def _clean(value: str | None) -> str:
        return re.sub(r"\s+", " ", value or "").strip()

    def _dismiss_popups(self, page: Page) -> None:
        for label in ("Accept", "Accept all", "I agree", "Got it", "Close", "Tutup"):
            try:
                page.get_by_role("button", name=re.compile(rf"^{re.escape(label)}$", re.I)).first.click(timeout=700)
            except Exception:
                continue

    def _observe_search_box(self, page: Page):
        self._step("OBSERVE")
        for locator in (page.get_by_role("searchbox").first, page.locator("input[type='search']").first, page.locator("input[name*='search' i], input[placeholder*='search' i]").first):
            try:
                if locator.is_visible(timeout=700):
                    return locator
            except Exception:
                continue
        return None

    def _find_candidates(self, page: Page, query: str) -> list[tuple[str, str]]:
        terms = {term for term in re.findall(r"\w+", query.lower()) if len(term) > 2}
        candidates: list[tuple[int, str, str]] = []
        links = page.locator("a:visible")
        for index in range(min(links.count(), 180)):
            link = links.nth(index)
            try:
                title = self._clean(link.inner_text(timeout=500))
                href = link.get_attribute("href")
            except Exception:
                continue
            url = urljoin(page.url, href or "")
            if not title or not self.extractor.is_detik_finance_url(url):
                continue
            title_lower = title.lower()
            score = sum(term in title_lower for term in terms)
            related_markers = ("saham", "ihsg", "bursa", "rupiah", "emas", "perak", "silver", "investor", "bank", "pasar", "ekonomi")
            commodity_query = terms & {"silver", "perak", "emas", "gold", "xau"}
            if commodity_query and not any(marker in title_lower for marker in ("silver", "perak", "emas", "gold", "logam mulia")):
                continue
            if score or any(marker in title_lower for marker in related_markers):
                candidates.append((score, title, url))
        unique = {(title, url): (score, title, url) for score, title, url in candidates}
        return [(title, url) for _, title, url in sorted(unique.values(), reverse=True)]

    def _search(self, browser: BrowserController, page: Page, search_query: str) -> list[tuple[str, str]]:
        _debug("Navigating to: %s", page.url)
        search_box = self._observe_search_box(page)
        if search_box:
            self._step("TYPE")
            search_box.fill(search_query)
            search_box.press("Enter")
            self._capture(browser, "search_submitted")
            try:
                page.wait_for_load_state("domcontentloaded", timeout=browser.timeout_ms)
            except PlaywrightTimeoutError:
                LOG.warning("Search page timed out; using visible results")
        else:
            self._step("OPEN")
            browser.open_url(f"https://www.detik.com/search/searchall?query={quote(search_query)}")
            page = browser.page
            assert page is not None
            self._capture(browser, "search_page_opened")
        _debug("Current URL: %s", page.url)
        try:
            _debug("Page title: %s", page.title())
        except Exception:
            pass
        self._step("SCROLL")
        browser.scroll()
        self._capture(browser, "search_results_scrolled")
        candidates = self._find_candidates(page, search_query)
        _debug("Search executed: %s; Search result count: %d", search_query, len(candidates))
        return candidates

    def _extract_candidate_sync(self, title: str, url: str, index: int) -> NewsArticle:
        """Read one article in its own browser context."""
        timeout_ms = int(os.getenv("ARTICLE_TIMEOUT", "30")) * 1000
        browser = BrowserController(headless=self.browser.headless, timeout_ms=timeout_ms)
        try:
            with browser:
                browser.open_url(url)
                page = browser.page
                assert page is not None
                self._dismiss_popups(page)
                page.wait_for_timeout(500)
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(500)
                article = self.extractor.extract(page, title)
                if not article.title or not article.url or article.source != "Detik Finance" or not article.content:
                    raise ValueError("EXTRACTION_ERROR: Artikel tidak memenuhi validasi minimum")
                if os.getenv("DEBUG_SCREENSHOTS", "true").lower() == "true":
                    self.session_folder.mkdir(parents=True, exist_ok=True)
                    browser.screenshot(str(self.session_folder / f"article_{index:03d}_read.png"))
                LOG.info("[ASYNC %03d] Article extracted: %s", index, article.title)
                self._report_progress(40 + index * 5, f"Artikel {index} selesai diekstrak")
                return article
        except Exception as exc:
            LOG.error("EXTRACTION_ERROR: async article %s failed: %s", index, exc)
            raise

    async def _extract_candidate_async(self, title: str, url: str, index: int) -> NewsArticle:
        """Schedule the blocking Playwright Sync API outside the event loop."""
        return await asyncio.to_thread(self._extract_candidate_sync, title, url, index)

    async def _extract_candidates_async(
        self, candidates: list[tuple[str, str]], requested: int
    ) -> tuple[list[NewsArticle], list[str]]:
        concurrency = max(1, min(int(os.getenv("MAX_CONCURRENT", "3")), requested))
        semaphore = asyncio.Semaphore(concurrency)

        async def worker(index: int, item: tuple[str, str]):
            async with semaphore:
                return await self._extract_candidate_async(item[0], item[1], index)

        tasks = [asyncio.create_task(worker(index, candidate)) for index, candidate in enumerate(candidates, 1)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        articles: list[NewsArticle] = []
        failed: list[str] = []
        for candidate, result in zip(candidates, results):
            if isinstance(result, BaseException):
                LOG.error("EXTRACTION_ERROR: %s failed in async batch: %s", candidate[0], result)
                failed.append(candidate[0])
            else:
                article = result
                if article.url not in {item.url for item in articles}:
                    articles.append(article)
            if len(articles) >= requested:
                break
        return articles[:requested], failed

    def _run_async_extraction(self, candidates: list[tuple[str, str]], requested: int):
        """Bridge sync CLI and MCP event-loop callers to the async extractor."""
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(asyncio.run, self._extract_candidates_async(candidates, requested))
            return future.result()

    def run(self, query: str, limit: int = 5, request_id: str | None = None) -> NewsSearchResult:
        self.steps = 0
        self.current_query = normalize_query(query)
        self._current_request_id = request_id or ""
        self._activities = []
        
        start_time = datetime.now()
        self._report_activity("current_research", "search_web", "running", "Searching current information")
        
        self._start_debug_session(self.current_query, request_id)
        requested = max(1, min(int(limit), int(os.getenv("MAX_ARTICLES", "20"))))
        self.deadline = datetime.now().timestamp() + self.timeout_seconds
        articles: list[NewsArticle] = []
        failed: list[str] = []
        seen: set[str] = set()
        normalized_query = normalize_query(query)
        search_queries = expand_query(normalized_query)
        _debug("User query: %s", query)
        _debug("Normalized query: %s", normalized_query)
        try:
            rag_context = retrieve_context(normalized_query)
        except Exception as exc:
            rag_context = []
            LOG.error("RAG_ERROR: context retrieval failed: %s", exc)
        _debug("RAG query: %s", normalized_query)
        _debug("RAG results: %d", len(rag_context))
        _debug("Search strategy: %s", search_queries)
        LOG.info("[INFO] Starting Computer Use Agent")
        
        search_start = datetime.now()
        self._report_activity("current_research", "search_site", "running", f"Searching DetikFinance", tool="collect_detik_finance_news")
        
        try:
            with self.browser as browser:
                self._step("OPEN")
                LOG.info("[INFO] Opening Detik Finance")
                browser.open_url(HOME_URL)
                page = browser.page
                assert page is not None
                self._capture(browser, "open_home")
                self._dismiss_popups(page)
                candidates: list[tuple[str, str]] = []
                for attempt, search_query in enumerate(search_queries[:MAX_SEARCH_ATTEMPTS], 1):
                    LOG.info("[INFO] Searching attempt %d/%d: %s", attempt, MAX_SEARCH_ATTEMPTS, search_query)
                    if attempt > 1:
                        self._step("OPEN")
                        browser.open_url(HOME_URL)
                        page = browser.page
                        assert page is not None
                        self._capture(browser, f"fallback_{attempt}_home")
                        self._dismiss_popups(page)
                    candidates.extend(self._search(browser, page, search_query))
                    candidates = list(dict.fromkeys(candidates))
                    _debug("Candidate article count: %d", len(candidates))
                    if len(candidates) >= requested:
                        break
                if not candidates:
                    self._write_debug("search_results.json", {"query": normalized_query, "results": [], "status": "no_results"})
                    self._report_activity("current_research", "search_site", "completed", "Searching DetikFinance", duration_ms=int((datetime.now() - search_start).total_seconds() * 1000), result_count=0)
                    self._report_activity("current_research", "search_web", "completed", "Searching current information", duration_ms=int((datetime.now() - start_time).total_seconds() * 1000), result_count=0)
                    return NewsSearchResult(query=query, requested=requested, request_id=request_id, research_session_id=self.research_session_id, status="failed", error_type="COMPUTER_USE_ERROR", message="COMPUTER_USE_ERROR: Tidak ditemukan kandidat artikel Detik Finance.")
                
                self._report_activity("current_research", "search_site", "completed", "Searching DetikFinance", duration_ms=int((datetime.now() - search_start).total_seconds() * 1000), result_count=len(candidates))
                
                self._write_debug("search_results.json", {"query": normalized_query, "results": [{"title": title, "url": url, "publisher": "Detik Finance"} for title, url in candidates]})
                unique_candidates = [(title, url) for title, url in candidates if url not in seen]
                unique_candidates = unique_candidates[:max(requested * 2, requested)]
                
                self._report_activity("current_research", "open_sources", "running", "Opening relevant sources", result_count=len(unique_candidates))
                self._report_activity("current_research", "open_sources", "completed", "Opening relevant sources", result_count=len(unique_candidates))
                
                for _, url in unique_candidates:
                    seen.add(url)
                LOG.info("[INFO] Extracting %d articles asynchronously (concurrency=%s)", len(unique_candidates), os.getenv("MAX_CONCURRENT", "3"))
                read_start = datetime.now()
                self._report_activity("current_research", "read_sources", "running", "Reading source content", result_count=len(unique_candidates))
                async_articles, async_failed = self._run_async_extraction(unique_candidates, requested)
                self._report_activity("current_research", "read_sources", "completed", "Reading source content", duration_ms=int((datetime.now() - read_start).total_seconds() * 1000), result_count=len(async_articles))
                articles.extend(async_articles)
                failed.extend(async_failed)
                for index, article in enumerate(articles, 1):
                    article.evidence_id = f"evidence_{index:03d}"
                    article.research_session_id = self.research_session_id
                    article.request_id = request_id
                    self._write_debug(f"article_{index:03d}.txt", f"TITLE:\n{article.title}\n\nPUBLISHER:\n{article.source}\n\nURL:\n{article.url}\n\nPUBLISHED_AT:\n{article.published_at or ''}\n\nCONTENT:\n\n{article.content}")
                self._write_debug("articles.json", {"research_session_id": self.research_session_id, "articles": [{"evidence_id": article.evidence_id, "title": article.title, "publisher": article.source, "url": article.url, "published_at": article.published_at, "content_file": f"article_{index:03d}.txt", "content_length": len(article.content)} for index, article in enumerate(articles, 1)]})
                query_terms = {term for term in re.findall(r"\w+", normalized_query.lower()) if len(term) > 2}
                for article in articles:
                    article_text = f"{article.title} {article.content}".lower()
                    matched_terms = sum(term in article_text for term in query_terms)
                    article.relevance_score = round(min(1.0, matched_terms / max(1, min(len(query_terms), 4))), 3)
                _debug("Valid article count: %d", len(articles))
                _debug("Extracted article count: %d", len(articles))
                self._report_activity("current_research", "search_web", "completed", "Searching current information", duration_ms=int((datetime.now() - start_time).total_seconds() * 1000), result_count=len(articles))
                status = "success" if articles else "failed"
                message = None if articles else "Artikel ditemukan tetapi tidak ada isi yang berhasil dibaca."
                LOG.info("[INFO] Finished: %s articles", len(articles))
                self._write_debug("execution.json", {"research_session_id": self.research_session_id, "request_id": request_id, "status": status, "pages_read": len(articles), "articles_extracted": len(articles), "articles_with_content": sum(bool(article.content.strip()) for article in articles)})
                LOG.info("[CU] pages_read=%d articles_extracted=%d articles_with_content=%d", len(articles), len(articles), sum(bool(article.content.strip()) for article in articles))
                return NewsSearchResult(query=query, requested=requested, request_id=request_id, research_session_id=self.research_session_id, articles=articles, failed_articles=failed, status=status, message=message)
        except Exception as exc:
            LOG.error("[ERROR] Agent stopped: %s", exc)
            error_type = "BROWSER_ERROR" if not articles else "COMPUTER_USE_ERROR"
            LOG.error("%s: Agent stopped: %s", error_type, exc)
            self._report_activity("current_research", "search_web", "failed", "Searching current information", duration_ms=int((datetime.now() - start_time).total_seconds() * 1000), error={"code": error_type, "message": str(exc)})
            return NewsSearchResult(query=query, requested=requested, request_id=request_id, research_session_id=self.research_session_id, articles=articles, failed_articles=failed, status="failed", error_type=error_type, message=f"{error_type}: {exc}")

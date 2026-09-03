"""Optional Chroma/Gemini RAG integration for live Detik Finance articles."""

from __future__ import annotations

import logging
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)
CHUNK_SIZE = 4500
CHUNK_OVERLAP = 500
COLLECTION_NAME = os.getenv("RAG_COLLECTION_NAME", "stock_research")
HOME_URL = "https://finance.detik.com/"


def debug(message: str, *args: Any) -> None:
    if os.getenv("DEBUG_MODE", "false").lower() == "true":
        LOG.info("[DEBUG] " + message, *args)


def normalize_query(query: str) -> str:
    cleaned = re.sub(r"\s*application context\s*:.*$", "", query or "", flags=re.I)
    cleaned = re.sub(r"^\s*(why did|why has|why is|kenapa|mengapa)\s+", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\bprize\b", "price", cleaned, flags=re.I)
    return re.sub(r"\s+", " ", cleaned).strip(" ?.!\t\r\n")


def expand_query(query: str) -> list[str]:
    """Return bounded search variants; these are intentionally deterministic."""
    normalized = normalize_query(query)
    lowered = normalized.lower()
    related: list[str] = []
    if "ihsg" in lowered or "saham" in lowered or "bbca" in lowered:
        related = ["indeks saham Indonesia", "bursa saham Indonesia", f"{normalized} hari ini"]
    elif "emas" in lowered or "gold" in lowered or "xau" in lowered:
        related = ["emas Antam", "harga emas dunia", "logam mulia Indonesia"]
    elif "perak" in lowered or "silver" in lowered:
        related = ["harga perak turun", "silver price drop", "berita harga perak", "pasar logam mulia perak Indonesia"]
    elif "rupiah" in lowered or "bank indonesia" in lowered or "bi" == lowered:
        related = ["nilai tukar rupiah", "kebijakan Bank Indonesia", "kurs rupiah hari ini"]
    elif "bitcoin" in lowered or "crypto" in lowered:
        related = ["harga Bitcoin turun", "berita Bitcoin terbaru", "aset kripto Indonesia", "pasar kripto"]
    else:
        related = [f"berita {normalized}", f"ekonomi {normalized}", f"{normalized} terbaru"]
    return list(dict.fromkeys([normalized, *related]))[:4]


def _client_and_collection():
    from google import genai
    from google.genai import types
    import chromadb

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY tidak ditemukan")

    default_root = Path(os.getenv("RAG_ROOT", Path(__file__).resolve().parents[2] / "RAG")) / "Data"
    root = Path(os.getenv("RAG_DATA_ROOT", default_root))
    client = chromadb.PersistentClient(path=str(root / "chroma_db"))
    collection = client.get_or_create_collection(name=COLLECTION_NAME)
    return genai.Client(
        api_key=api_key,
        http_options={"timeout": 300000},
    ), types, collection


def _embedding(client: Any, types: Any, text: str, task_type: str) -> list[float]:
    result = client.models.embed_content(
        model=os.getenv("EMBEDDING_MODEL", "gemini-embedding-001"),
        contents=text,
        config=types.EmbedContentConfig(task_type=task_type, output_dimensionality=768),
    )
    return result.embeddings[0].values


def _read_minio_article(article: Any) -> dict[str, Any]:
    import boto3
    from botocore.config import Config

    endpoint = os.getenv("MINIO_ENDPOINT", "http://minio:9000")
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=os.getenv("MINIO_ACCESS_KEY", "minioadmin"),
        aws_secret_access_key=os.getenv("MINIO_SECRET_KEY", "minioadmin123"),
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )
    bucket = os.getenv("MINIO_BUCKET_NEWS", "news")
    key = _article_object_key(article)
    body = client.get_object(Bucket=bucket, Key=key)["Body"].read()
    stored = json.loads(body.decode("utf-8"))
    if not str(stored.get("content", "")).strip():
        raise ValueError(f"MinIO article has empty content: {key}")
    return {"bucket": bucket, "object_key": key, **stored}


def retrieve_context(query: str, top_k: int = 5) -> list[dict[str, Any]]:
    client, types, collection = _client_and_collection()
    if collection.count() == 0:
        debug("RAG results: 0 (empty collection)")
        return []
    result = collection.query(
        query_embeddings=[_embedding(client, types, query, "RETRIEVAL_QUERY")],
        n_results=min(max(top_k, 1), collection.count()),
        include=["documents", "metadatas", "distances"],
    )
    rows = []
    for document, metadata, distance in zip(
        result["documents"][0], result["metadatas"][0], result["distances"][0]
    ):
        rows.append({"text": document, "metadata": metadata, "distance": float(distance)})
    debug("RAG results: %d", len(rows))
    return rows


def index_articles(articles: list[Any], query: str) -> int:
    if not articles:
        return 0
    client, types, collection = _client_and_collection()
    indexed = 0
    retrieved_at = datetime.now(timezone.utc).isoformat()
    for article in articles:
        stored = _read_minio_article(article)
        content = str(stored["content"]).strip()
        start = 0
        chunk_number = 0
        while start < len(content):
            chunk = content[start : start + CHUNK_SIZE].strip()
            if chunk:
                indexed_text = f"{stored.get('title', article.title)}\n\n{chunk}"
                article_id = re.sub(r"[^a-zA-Z0-9]+", "-", str(stored.get("url", article.url))).strip("-")[:100]
                chunk_id = f"{article_id}-{chunk_number}"
                metadata = {
                    "evidence_id": stored.get("evidence_id", article.evidence_id or ""),
                    "research_session_id": stored.get("research_session_id", article.research_session_id or ""),
                    "request_id": stored.get("request_id", article.request_id or ""),
                    "article_id": article_id,
                    "title": stored.get("title", article.title),
                    "url": stored.get("url", article.url),
                    "source": "Detik Finance",
                    "source_type": "external_news",
                    "storage": "minio",
                    "bucket": os.getenv("MINIO_BUCKET_NEWS", "news"),
                    "object_key": stored["object_key"],
                    "category": stored.get("category", article.category or "Finance"),
                    "published_at": stored.get("published_at", article.published_at or ""),
                    "query": query,
                    "retrieved_at": retrieved_at,
                    "collected_at": retrieved_at,
                    "chunk": chunk_number,
                }
                collection.upsert(
                    ids=[chunk_id],
                    documents=[indexed_text],
                    embeddings=[_embedding(client, types, indexed_text, "RETRIEVAL_DOCUMENT")],
                    metadatas=[metadata],
                )
                indexed += 1
            if len(chunk) < CHUNK_SIZE:
                break
            start += CHUNK_SIZE - CHUNK_OVERLAP
            chunk_number += 1
    debug("RAG indexed documents: %d", indexed)
    LOG.info("[RAG] query=%r collection=%s indexed_chunks=%d articles=%d", query, COLLECTION_NAME, indexed, len(articles))
    return indexed


def _article_object_key(article: Any) -> str:
    """Use the same deterministic key as news_storage without importing MCP code."""
    source = re.sub(r"[^a-z0-9]+", "_", (article.source or "unknown_source").lower()).strip("_") or "unknown_source"
    match = re.search(r"(?:^|/)d-(\d+)(?:/|$|-)", article.url or "")
    filename = f"d-{match.group(1)}.json" if match else f"{hashlib.sha256((article.url or '').encode('utf-8')).hexdigest()}.json"
    session_id = str(getattr(article, "research_session_id", "") or "").strip()
    if session_id:
        collected = datetime.now(timezone.utc)
        return f"computer_use/{collected:%Y/%m/%d}/{session_id}/{source}/{filename}"
    return f"{source}/{filename}"


def retrieve_relevant_articles(query: str, articles: list[Any], top_k: int) -> list[Any]:
    if not articles:
        return []
    try:
        rows = retrieve_context(query, top_k=max(top_k * 3, 5))
    except Exception as exc:
        LOG.error("RAG_ERROR: semantic retrieval failed: %s", exc)
        return articles[:top_k]
    scores: dict[str, float] = {}
    for row in rows:
        url = row["metadata"].get("url")
        if url:
            scores[url] = max(scores.get(url, 0.0), 1.0 / (1.0 + row["distance"]))
    ranked = sorted(articles, key=lambda item: scores.get(item.url, 0.0), reverse=True)
    for article in ranked:
        article.relevance_score = round(scores.get(article.url, 0.0), 6)
    return ranked[:top_k]


def health_check() -> dict[str, str]:
    checks = {name: "FAILED" for name in ("Browser", "Computer Use", "Detik Finance", "Search", "Article Extraction", "Embedding", "Vector DB", "RAG Retrieval")}
    try:
        from .browser import BrowserController
        from .extractor import NewsExtractor

        with BrowserController(timeout_ms=15_000) as browser:
            checks["Browser"] = "OK"
            browser.open_url(HOME_URL)
            page = browser.page
            if page is None or not NewsExtractor.is_detik_finance_url(page.url):
                raise RuntimeError("Detik Finance domain tidak aktif")
            checks["Computer Use"] = "OK"
            checks["Detik Finance"] = "OK"
            browser.open_url("https://www.detik.com/search/searchall?query=IHSG")
            page = browser.page
            assert page is not None
            links = page.locator("a:visible")
            article_url = None
            article_title = ""
            for index in range(min(links.count(), 150)):
                link = links.nth(index)
                href = link.get_attribute("href")
                if href and NewsExtractor.is_detik_finance_url(href):
                    article_url = href
                    article_title = link.inner_text()
                    break
            if article_url:
                checks["Search"] = "OK"
                page.goto(article_url, wait_until="domcontentloaded", timeout=15_000)
                NewsExtractor().extract(page, article_title)
                checks["Article Extraction"] = "OK"
    except Exception as exc:
        LOG.error("BROWSER_ERROR: health browser check failed: %s", exc)
    try:
        client, types, collection = _client_and_collection()
        _embedding(client, types, "health check", "RETRIEVAL_QUERY")
        checks["Embedding"] = "OK"
        checks["Vector DB"] = "OK"
        if collection.count():
            retrieve_context("IHSG", top_k=1)
        checks["RAG Retrieval"] = "OK"
    except Exception as exc:
        LOG.error("RAG_ERROR: health RAG check failed: %s", exc)
    checks["status"] = "READY" if all(value == "OK" for name, value in checks.items() if name != "status") else "FAILED"
    return checks

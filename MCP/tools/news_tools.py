import logging
import asyncio
from concurrent.futures import ThreadPoolExecutor
import os
from threading import Lock
from uuid import uuid4
import sys
import json
from pathlib import Path
from mcp.server.mcpserver import Context
from tools.news_storage import _bucket, _s3_client, store_articles, topic_name


LOG = logging.getLogger(__name__)
_JOB_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="mcp-news")
_JOBS: dict[str, dict] = {}
_JOBS_LOCK = Lock()


def _to_serializable(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _to_serializable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_to_serializable(item) for item in value]
    if hasattr(value, "model_dump"):
        try:
            return _to_serializable(value.model_dump())
        except Exception:
            pass
    if hasattr(value, "dict"):
        try:
            return _to_serializable(value.dict())
        except Exception:
            pass
    if hasattr(value, "__dict__"):
        try:
            return _to_serializable(vars(value))
        except Exception:
            pass
    return str(value)


def _load_computer_use_agent():
    """Load the shared Computer Use project from its mounted root folder."""
    project_root = Path(os.getenv("COMPUTER_USE_ROOT", Path(__file__).parents[2] / "Computer Use"))
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from app.agent import ComputerUseAgent

    return ComputerUseAgent


def _load_health_check():
    project_root = Path(os.getenv("COMPUTER_USE_ROOT", Path(__file__).parents[2] / "Computer Use"))
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from app.rag_integration import health_check
    return health_check


def _format_result_text(result) -> str:
    lines = ["=" * 50, "DETIK FINANCE NEWS", "=" * 50, ""]
    for index, article in enumerate(result.articles, 1):
        lines.extend([
            f"{index}. JUDUL:", article.title, "", f"KATEGORI:\n{article.category}",
            f"\nTANGGAL:\n{article.published_at or '-'}", f"\nAUTHOR:\n{article.author or '-'}",
            "\nSOURCE:\nDetik Finance", f"\nURL:\n{article.url}",
            f"\nISI:\n{article.content}", "\n" + "-" * 50, "",
        ])
    if result.failed_articles:
        lines.extend(["ARTIKEL GAGAL:", *result.failed_articles, ""])
    if result.message:
        lines.extend([f"STATUS: {result.status.upper()}", result.message])
    return "\n".join(lines).rstrip() + "\n"


def _collect(query: str, limit: int, timeout_seconds: int = 120, progress_callback=None, request_id: str | None = None) -> dict:
    LOG.info("[COMPUTER_USE] request=%s status=starting query=%r", request_id or "unknown", query)
    result = _load_computer_use_agent()(timeout_seconds=timeout_seconds, progress_callback=progress_callback).run(query=query, limit=limit, request_id=request_id)
    payload = _to_serializable(result)
    if not isinstance(payload, dict):
        payload = {"status": "failed", "message": str(result), "articles": []}
    articles = list(getattr(result, "articles", []) or [])
    LOG.info("[COMPUTER_USE] request=%s status=%s articles=%d with_content=%d", request_id or "unknown", payload.get("status"), len(articles), sum(1 for article in articles if getattr(article, "content", "") and str(article.content).strip()))
    payload["text"] = _format_result_text(result) if hasattr(result, "articles") else str(result)
    computer_use_root = Path(os.getenv("COMPUTER_USE_ROOT", Path(__file__).parents[2] / "Computer Use"))
    session_folder = computer_use_root / "DebuggingFolder" / (result.research_session_id or "")
    payload["lifecycle"] = [{"status": "research_completed"}]
    try:
        payload["lifecycle"].append({"status": "saving_to_minio"})
        payload["storage"] = store_articles(result.articles, query=query)
        (session_folder / "minio_upload.json").write_text(json.dumps({**payload["storage"], "status": "verified" if payload["storage"].get("verified") == len(result.articles) else "failed"}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        payload["lifecycle"].append({"status": "minio_verified", "verified": payload["storage"].get("verified", 0)})
        if payload["storage"].get("failed", 0):
            payload["status"] = "failed"
        if payload["storage"].get("verified", 0) and result.articles:
            from app.rag_integration import index_articles
            payload["lifecycle"].append({"status": "indexing_fresh_evidence"})
            payload["rag_indexed_chunks"] = index_articles(result.articles, query)
            (session_folder / "rag_ingestion.json").write_text(json.dumps({"research_session_id": result.research_session_id, "source": "minio", "bucket": payload["storage"].get("bucket"), "objects_processed": payload["storage"].get("verified", 0), "documents_created": len(result.articles), "chunks_created": payload["rag_indexed_chunks"], "embeddings_created": payload["rag_indexed_chunks"], "status": "completed" if payload["rag_indexed_chunks"] else "failed"}, ensure_ascii=False, indent=2), encoding="utf-8")
            payload["lifecycle"].append({"status": "indexed", "chunks": payload["rag_indexed_chunks"]})
        LOG.info("[RESEARCH] request=%s minio_verified=%d rag_chunks=%d", request_id or "unknown", payload.get("storage", {}).get("verified", 0), payload.get("rag_indexed_chunks", 0))
    except Exception as exc:
        LOG.exception("News MinIO storage initialization failed")
        payload["status"] = "failed"
        payload["lifecycle"].append({"status": "failed", "stage": "persistence_or_indexing"})
        payload["storage"] = {
            "stored": 0, "skipped": 0, "failed": len(result.articles),
            "errors": [{"error": str(exc)}], "bucket": os.getenv("MINIO_BUCKET_NEWS", "news"),
        }
    return payload


def _run_news_job(job_id: str, query: str, limit: int) -> None:
    def report(progress: float, total: float, message: str, payload: dict | None = None) -> None:
        with _JOBS_LOCK:
            job = _JOBS.get(job_id)
            if job:
                job.update({"progress": progress, "total": total, "message": message})
                if payload:
                    job.setdefault("activities", []).append(payload)

    try:
        with _JOBS_LOCK:
            _JOBS[job_id].update({"status": "running", "phase": "computer_use"})
        result = _collect(query, limit, 300, report, _JOBS[job_id].get("request_id"))
        with _JOBS_LOCK:
            final_status = "completed" if result.get("status") == "success" and not result.get("storage", {}).get("failed") else "failed"
            _JOBS[job_id].update({"status": final_status, "phase": result.get("lifecycle", [{}])[-1].get("status", "failed"), "progress": 100, "result": result})
    except Exception as exc:
        LOG.exception("Background news collection failed")
        with _JOBS_LOCK:
            _JOBS[job_id].update({"status": "failed", "message": str(exc)})


def _stored_news_articles() -> list[dict]:
    client = _s3_client()
    bucket = _bucket()
    paginator = client.get_paginator("list_objects_v2")
    articles = []
    for page in paginator.paginate(Bucket=bucket):
        for item in page.get("Contents", []):
            key = item.get("Key", "")
            if not key.endswith(".json"):
                continue
            try:
                article = json.loads(client.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8"))
            except Exception:
                LOG.warning("Skipping unreadable news object %s", key, exc_info=True)
                continue
            if article.get("source_type") != "external_news":
                continue
            article["bucket"] = bucket
            article["object_key"] = key
            article.setdefault("topic", "General News")
            article.setdefault("artifact_id", key.rsplit("/", 1)[-1].rsplit(".", 1)[0])
            articles.append(article)
    return articles


def _news_topic_summary(articles: list[dict]) -> list[dict]:
    grouped: dict[str, list[dict]] = {}
    for article in articles:
        grouped.setdefault(article.get("topic") or "General News", []).append(article)
    summaries = []
    for topic, items in grouped.items():
        impacts = {"positive": 0, "negative": 0, "neutral": 0}
        for item in items:
            impact = str(item.get("impact") or "neutral").lower()
            impacts[impact if impact in impacts else "neutral"] += 1
        latest = max((item.get("collected_at") or "" for item in items), default=None)
        summaries.append({"topic": topic, "article_count": len(items), **impacts, "last_updated": latest})
    return sorted(summaries, key=lambda item: item.get("last_updated") or "", reverse=True)


def register_news_tools(mcp):
    @mcp.tool()
    def list_news_intelligence_topics(source: str | None = None, impact: str | None = None) -> dict:
        """List persisted News Intelligence topics and article counts."""
        try:
            articles = _stored_news_articles()
            if source:
                articles = [item for item in articles if str(item.get("source", "")).lower() == source.lower()]
            if impact:
                articles = [item for item in articles if str(item.get("impact", "neutral")).lower() == impact.lower()]
            return {"status": "success", "storage": "minio", "bucket": _bucket(), "topics": _news_topic_summary(articles)}
        except Exception as exc:
            LOG.exception("News Intelligence topic listing failed")
            return {"status": "failed", "message": str(exc), "topics": []}

    @mcp.tool()
    def get_news_intelligence_topic(topic: str, search: str | None = None, source: str | None = None, impact: str | None = None) -> dict:
        """Return persisted articles belonging to one News Intelligence topic."""
        try:
            articles = [item for item in _stored_news_articles() if (item.get("topic") or "General News").casefold() == topic.casefold()]
            if search:
                needle = search.casefold()
                articles = [item for item in articles if needle in str(item.get("title", "")).casefold() or needle in str(item.get("content", "")).casefold()]
            if source:
                articles = [item for item in articles if str(item.get("source", "")).casefold() == source.casefold()]
            if impact:
                articles = [item for item in articles if str(item.get("impact", "neutral")).casefold() == impact.casefold()]
            return {"status": "success", "storage": "minio", "bucket": _bucket(), "topic": topic, "articles": articles}
        except Exception as exc:
            LOG.exception("News Intelligence topic retrieval failed")
            return {"status": "failed", "message": str(exc), "topic": topic, "articles": []}

    @mcp.tool()
    def get_news_intelligence_article(article_id: str) -> dict:
        """Return one persisted News Intelligence article by deterministic identity."""
        try:
            article = next((item for item in _stored_news_articles() if item.get("artifact_id") == article_id), None)
            return {"status": "success", "article": article} if article else {"status": "not_found", "article": None}
        except Exception as exc:
            LOG.exception("News Intelligence article retrieval failed")
            return {"status": "failed", "message": str(exc), "article": None}

    @mcp.tool()
    async def collect_detik_finance_news(query: str, limit: int = 5, async_mode: bool = True, request_id: str | None = None, ctx: Context = None) -> dict:
        """Collect Detik Finance news; async_mode returns immediately with a job_id."""
        if async_mode:
            job_id = f"news_{uuid4().hex}"
            with _JOBS_LOCK:
                _JOBS[job_id] = {
                    "job_id": job_id, "status": "queued", "progress": 0,
                    "total": 100, "query": query, "requested": limit,
                    "message": "Menunggu worker browser...",
                    "request_id": request_id,
                }
            _JOB_EXECUTOR.submit(_run_news_job, job_id, query, limit)
            return {
                "status": "processing", "job_id": job_id,
                "message": "Pengumpulan berita berjalan di background. Panggil get_news_job_status dengan job_id untuk progress dan hasil.",
            }

        try:
            loop = asyncio.get_running_loop()

            def report(progress: float, total: float, message: str, payload: dict | None = None) -> None:
                if ctx is not None:
                    if payload:
                        asyncio.run_coroutine_threadsafe(
                            ctx.log("info", payload, logger_name="activity"), loop
                        )
                    else:
                        asyncio.run_coroutine_threadsafe(
                            ctx.report_progress(progress, total, message), loop
                        )

            return await asyncio.to_thread(_collect, query, limit, 120, report, request_id)
        except Exception as exc:
            LOG.exception("Computer Use news collection failed")
            return {
                "status": "failed", "source": "Detik Finance", "query": query,
                "requested": limit, "articles": [], "failed_articles": [],
                "message": f"Computer Use news collection failed: {exc}",
            }

    @mcp.tool()
    def get_news_job_status(job_id: str) -> dict:
        """Return progress and result for a background news collection job."""
        with _JOBS_LOCK:
            job = _JOBS.get(job_id)
            if job is None:
                return {"status": "error", "message": f"Job tidak ditemukan: {job_id}"}
            
            result = dict(job)
            if "activities" in job:
                job["activities"] = []
            return result

    @mcp.tool()
    def debug_detik_finance() -> dict:
        """Run browser, Detik Finance, extraction, embedding, and RAG diagnostics."""
        try:
            checks = _load_health_check()()
            return {"status": "success" if checks.pop("status") == "READY" else "failed", "checks": checks}
        except Exception as exc:
            LOG.exception("Detik Finance health check failed")
            return {"status": "failed", "error_type": "COMPUTER_USE_ERROR", "message": str(exc)}

    @mcp.tool()
    async def get_news(
        website_url: str = "https://finance.detik.com/",
        keyword: str = "ekonomi Indonesia",
        jumlah_berita: int = 5,
        timeout_seconds: int = 60,
        async_mode: bool = True,
        ctx: Context = None,
    ) -> dict:
        """Backward-compatible news tool; async_mode returns immediately with a job_id."""
        if "detik.com" not in website_url:
            return {
                "status": "failed", "source": "Detik Finance", "query": keyword,
                "requested": jumlah_berita, "failed_articles": [], "articles": [],
                "message": "MVP ini hanya mendukung Detik Finance.",
            }
        if async_mode:
            return await collect_detik_finance_news(keyword, jumlah_berita, True, ctx=ctx)
        try:
            loop = asyncio.get_running_loop()

            def report(progress: float, total: float, message: str, payload: dict | None = None) -> None:
                if ctx is not None:
                    if payload:
                        asyncio.run_coroutine_threadsafe(
                            ctx.log("info", payload, logger_name="activity"), loop
                        )
                    else:
                        asyncio.run_coroutine_threadsafe(
                            ctx.report_progress(progress, total, message), loop
                        )

            return await asyncio.to_thread(_collect, keyword, jumlah_berita, timeout_seconds, report)
        except Exception as exc:
            LOG.exception("Computer Use news collection failed")
            return {
                "status": "failed", "source": "Detik Finance", "query": keyword,
                "requested": jumlah_berita, "failed_articles": [], "articles": [],
                "message": f"Computer Use news collection failed: {exc}",
            }

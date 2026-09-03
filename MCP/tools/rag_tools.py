import asyncio
import base64
import json
import logging
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import boto3
from botocore.config import Config

from mcp.server.mcpserver import Context
from pydantic import BaseModel, ConfigDict, Field


LOG = logging.getLogger(__name__)


class SearchRAGEvidence(BaseModel):
    model_config = ConfigDict(extra="allow")
    content: str
    document_id: str | None = None
    document_name: str | None = None
    page: int = 0
    relevance: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class SearchRAGError(BaseModel):
    code: str
    message: str
    retryable: bool = False


class SearchRAGResult(BaseModel):
    success: bool
    query: str
    results: list[SearchRAGEvidence] = Field(default_factory=list)
    count: int = 0
    error: SearchRAGError | None = None


def _error(message: str) -> dict:
    return {"status": "error", "service": "rag", "message": message}


def _answer_error(code: str, message: str, status: int | None = None) -> dict:
    return {
        "success": False,
        "status": "error",
        "service": "rag",
        "stage": "answer_generation",
        "error": {
            "code": code,
            "status": status,
            "message": message,
            "retryable": code == "RESOURCE_EXHAUSTED" or status == 429,
            "provider": "gemini",
        },
    }


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_evidence_item(item: Any, query: str = "") -> dict[str, Any] | None:
    if item is None:
        return None
    if isinstance(item, BaseModel):
        try:
            return _normalize_evidence_item(item.model_dump(), query)
        except Exception:
            return _normalize_evidence_item(item.dict(), query)
    if isinstance(item, dict):
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        content = (
            item.get("content")
            or item.get("text")
            or item.get("snippet")
            or item.get("document")
            or item.get("chunk")
            or ""
        )
        if isinstance(content, (dict, list)):
            content = json.dumps(content, ensure_ascii=False, default=str)
        content = str(content).strip()
        if not content and metadata:
            content = str(metadata.get("content") or metadata.get("text") or "").strip()
        if not content:
            return None

        object_key = metadata.get("object_key") or item.get("object_key")
        bucket = metadata.get("bucket") or item.get("bucket")
        source_url = metadata.get("url") or item.get("url")
        if object_key and not source_url:
            source_url = _source_url(object_key, bucket_name=bucket).get("url") if isinstance(_source_url(object_key, bucket_name=bucket), dict) else None

        document_id = (
            item.get("document_id")
            or item.get("id")
            or metadata.get("document_id")
            or object_key
            or f"{query or 'rag'}-{abs(hash(str(item))) % 1000000}"
        )
        document_name = (
            item.get("document_name")
            or item.get("title")
            or item.get("filename")
            or metadata.get("document_name")
            or metadata.get("title")
            or metadata.get("filename")
            or "internal_document"
        )
        page = _coerce_int(item.get("page", metadata.get("page", 0)), 0)
        relevance = _coerce_float(item.get("relevance", item.get("relevance_score", metadata.get("relevance", metadata.get("relevance_score", 0.0)))), 0.0)
        metadata_payload = dict(metadata)
        if object_key:
            metadata_payload.setdefault("object_key", object_key)
        if bucket:
            metadata_payload.setdefault("bucket", bucket)
        if source_url:
            metadata_payload.setdefault("url", source_url)
        result = {
            "content": content,
            "document_id": str(document_id),
            "document_name": str(document_name),
            "page": page,
            "relevance": relevance,
            "metadata": metadata_payload or {"source_type": "internal_document"},
            "url": source_url,
            "bucket": bucket,
            "object_key": object_key,
        }
        return result
    if isinstance(item, str):
        stripped = item.strip()
        if not stripped:
            return None
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            parsed = None
        if parsed is not None:
            normalized = normalize_tool_result(parsed, query)
            return normalized["results"][0] if normalized["results"] else None
        return {
            "content": stripped,
            "document_id": f"{query or 'text'}-{abs(hash(stripped)) % 1000000}",
            "document_name": "text_result",
            "page": 0,
            "relevance": 0.0,
            "metadata": {"source_type": "text"},
        }
    return None


def normalize_tool_result(result: Any, query: str = "") -> dict[str, Any]:
    try:
        if result is None:
            return {"success": True, "query": query, "results": [], "count": 0}
        if isinstance(result, BaseModel):
            return normalize_tool_result(result.model_dump(), query)
        if isinstance(result, dict):
            if "results" in result:
                items = result.get("results") or []
                normalized_items = []
                for item in items:
                    normalized = _normalize_evidence_item(item, query)
                    if normalized:
                        normalized_items.append(normalized)
                payload = {
                    "success": bool(result.get("success", True)),
                    "query": str(result.get("query", query)),
                    "results": normalized_items,
                    "count": len(normalized_items),
                }
                if result.get("error"):
                    payload["error"] = result["error"]
                return payload
            if "error" in result:
                error = result.get("error", {})
                return {
                    "success": False,
                    "query": str(result.get("query", query)),
                    "results": [],
                    "count": 0,
                    "error": {
                        "code": error.get("code", "SERIALIZATION_ERROR"),
                        "message": error.get("message", "Expected structured result but received dict."),
                        "retryable": bool(error.get("retryable", False)),
                    },
                }
            normalized_items = []
            for item in [result]:
                normalized = _normalize_evidence_item(item, query)
                if normalized:
                    normalized_items.append(normalized)
            return {"success": True, "query": query, "results": normalized_items, "count": len(normalized_items)}
        if isinstance(result, list):
            normalized_items = []
            for item in result:
                normalized = _normalize_evidence_item(item, query)
                if normalized:
                    normalized_items.append(normalized)
            return {"success": True, "query": query, "results": normalized_items, "count": len(normalized_items)}
        if isinstance(result, str):
            stripped = result.strip()
            if not stripped:
                return {"success": True, "query": query, "results": [], "count": 0}
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                parsed = None
            if parsed is not None:
                return normalize_tool_result(parsed, query)
            return {
                "success": True,
                "query": query,
                "results": [_normalize_evidence_item(stripped, query)],
                "count": 1,
            }
        return {
            "success": False,
            "query": query,
            "results": [],
            "count": 0,
            "error": {
                "code": "SERIALIZATION_ERROR",
                "message": f"Expected structured result but received {type(result).__name__}.",
                "retryable": False,
            },
        }
    except Exception as exc:  # pragma: no cover - defensive guard for tool boundary
        LOG.exception("RAG serialization normalization failed for query=%r type=%s", query, type(result).__name__)
        return {
            "success": False,
            "query": query,
            "results": [],
            "count": 0,
            "error": {
                "code": "SERIALIZATION_ERROR",
                "message": f"Expected structured result but received {type(result).__name__}: {exc}",
                "retryable": False,
            },
        }


def normalize_search_rag_result(result: Any, query: str = "") -> dict[str, Any]:
    return normalize_tool_result(result, query)


def _load_rag():
    root = os.getenv("RAG_ROOT", "/opt/integrations/RAG")
    src = os.path.join(root, "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    from rag import generate_answer
    from retriever import retrieve
    return retrieve, generate_answer


def _source_url(object_key: str, expires: int = 300, bucket_name: str | None = None) -> dict:
    root = os.getenv("RAG_ROOT", "/opt/integrations/RAG")
    src = os.path.join(root, "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    from object_storage import bucket, client, presigned_url

    if not object_key or Path(object_key).is_absolute() or ".." in Path(object_key).parts:
        return _error("object_key tidak valid.")
    target_bucket = bucket_name or (os.getenv("MINIO_BUCKET_NEWS", "news") if object_key.startswith("detik_finance/") else bucket())
    return {"status": "success", "source": "minio", "bucket": target_bucket, "object_key": object_key, "url": presigned_url(object_key, min(expires, 900), client(), target_bucket)}


async def search_rag(query: str, top_k: int = 5, request_id: str | None = None, ctx: Context = None) -> dict:
    if not query.strip() or top_k < 1 or top_k > 20:
        return _error("query wajib diisi dan top_k harus antara 1 sampai 20.")

    LOG.debug("search_rag query_type=%s query_repr=%r", type(query).__name__, repr(query))
    start_time = datetime.now()
    payload = {
        "type": "activity", "request_id": request_id or "", "stage": "current_research",
        "action": "search_rag", "status": "running", "label": "Searching documents",
        "tool": "search_rag", "timestamp": start_time.astimezone().isoformat()
    }
    if ctx:
        await ctx.log("info", payload, logger_name="activity")

    try:
        retrieve, _ = _load_rag()
        raw_result = await asyncio.to_thread(retrieve, query, final_k=top_k)
        LOG.debug("search_rag raw_result_type=%s raw_result_repr=%r", type(raw_result).__name__, repr(raw_result))

        normalized = normalize_tool_result(raw_result, query)
        LOG.debug("search_rag normalized_type=%s normalized=%r", type(normalized).__name__, normalized)

        results = normalized.get("results", [])
        for result in results:
            metadata = result.get("metadata", {}) if isinstance(result.get("metadata"), dict) else {}
            object_key = result.get("object_key") or metadata.get("object_key")
            if object_key:
                url_data = _source_url(object_key, bucket_name=metadata.get("bucket") or result.get("bucket"))
                if isinstance(url_data, dict) and url_data.get("url"):
                    metadata["url"] = url_data["url"]
                    result["url"] = url_data["url"]
                    result["metadata"] = metadata
                    result["bucket"] = url_data.get("bucket") or result.get("bucket") or metadata.get("bucket")
                    result["object_key"] = object_key

        payload.update({"status": "completed", "result_count": len(results), "duration_ms": int((datetime.now() - start_time).total_seconds() * 1000)})
        if ctx:
            await ctx.log("info", payload, logger_name="activity")

        if not normalized.get("success", True):
            return {
                "success": False,
                "query": query,
                "results": [],
                "count": 0,
                "status": "error",
                "error": normalized.get("error", {"code": "SERIALIZATION_ERROR", "message": "Search failed", "retryable": False}),
            }

        if not results:
            return {
                "success": True,
                "query": query,
                "results": [],
                "count": 0,
                "status": "no_relevant_information",
                "answer": "No relevant information found in Documents",
            }

        return {
            "success": True,
            "query": query,
            "results": results,
            "count": len(results),
            "status": "completed",
        }
    except Exception as exc:
        LOG.exception("RAG search failed")
        payload.update({"status": "failed", "error": {"message": str(exc)}, "duration_ms": int((datetime.now() - start_time).total_seconds() * 1000)})
        if ctx:
            await ctx.log("info", payload, logger_name="activity")
        return {
            "success": False,
            "query": query,
            "results": [],
            "count": 0,
            "status": "error",
            "error": {"code": type(exc).__name__, "message": str(exc), "retryable": False},
        }


async def ask_rag(question: str, evidence: list[dict] | None = None, include_internal: bool = True, request_id: str | None = None, ctx: Context = None) -> dict:
    """Generate an answer based on evidence from RAG."""
    if not question.strip():
        return _error("question wajib diisi.")

    normalized_evidence: list[dict] = []
    for item in evidence or []:
        if isinstance(item, dict):
            text = item.get("text") or item.get("content") or item.get("snippet") or ""
            if str(text).strip():
                normalized_evidence.append({**item, "text": str(text).strip()})
        elif hasattr(item, "model_dump"):
            try:
                item_dict = item.model_dump()
            except Exception:
                item_dict = getattr(item, "dict", lambda: {})()
            text = item_dict.get("text") or item_dict.get("content") or ""
            if str(text).strip():
                normalized_evidence.append({**item_dict, "text": str(text).strip()})

    if not normalized_evidence:
        return {
            "success": True,
            "status": "no_relevant_information",
            "answer": "No relevant information found in Documents",
            "sources": [],
            "question": question,
        }

    start_time = datetime.now()
    payload = {
        "type": "activity", "request_id": request_id or "", "stage": "prepare_answer",
        "action": "generate_answer", "status": "running", "label": "Preparing answer",
        "tool": "ask_rag", "timestamp": start_time.astimezone().isoformat()
    }
    if ctx:
        await ctx.log("info", payload, logger_name="activity")

    try:
        _, generate_answer = _load_rag()
        response = await asyncio.to_thread(generate_answer, question, normalized_evidence, include_internal=include_internal)

        if isinstance(response, dict):
            answer = str(response.get("answer", "") or "").strip()
            if not answer:
                payload.update({"status": "failed", "error": {"message": "LLM returned an empty answer."}, "duration_ms": int((datetime.now() - start_time).total_seconds() * 1000)})
                if ctx:
                    await ctx.log("info", payload, logger_name="activity")
                return {"success": False, "status": "error", "answer": "No relevant information found in Documents", "sources": [], "question": question, "error": {"code": "EMPTY_ANSWER", "message": "LLM returned an empty answer."}}
            payload.update({"status": "completed", "duration_ms": int((datetime.now() - start_time).total_seconds() * 1000)})
            if ctx:
                await ctx.log("info", payload, logger_name="activity")
            response["question"] = question
            response["success"] = True
            response["status"] = response.get("status") or "completed"
            return response

        answer, sources = response
        if not str(answer).strip():
            payload.update({"status": "failed", "error": {"message": "LLM returned an empty answer."}, "duration_ms": int((datetime.now() - start_time).total_seconds() * 1000)})
            if ctx:
                await ctx.log("info", payload, logger_name="activity")
            return {"success": False, "status": "error", "answer": "No relevant information found in Documents", "sources": [], "question": question, "error": {"code": "EMPTY_ANSWER", "message": "LLM returned an empty answer."}}

        payload.update({"status": "completed", "duration_ms": int((datetime.now() - start_time).total_seconds() * 1000)})
        if ctx:
            await ctx.log("info", payload, logger_name="activity")

        return {"question": question, "success": True, "answer": answer, "sources": sources, "status": "success"}
    except Exception as exc:
        LOG.exception("RAG answer failed")
        message = str(exc)
        quota = re.search(r"429 RESOURCE_EXHAUSTED", message, re.I)
        payload.update({
            "status": "failed",
            "error": {"code": "RESOURCE_EXHAUSTED" if quota else type(exc).__name__, "message": message, "retryable": bool(quota)},
            "duration_ms": int((datetime.now() - start_time).total_seconds() * 1000)
        })
        if ctx:
            await ctx.log("info", payload, logger_name="activity")
        return {
            "success": False,
            "status": "error",
            "answer": "No relevant information found in Documents",
            "sources": [],
            "question": question,
            "error": {"code": "RESOURCE_EXHAUSTED" if quota else type(exc).__name__, "message": message, "retryable": bool(quota)}
        }


def upload_document_to_minio(filename: str, content_base64: str, content_type: str | None = None, object_key: str | None = None, folder: str = "documents") -> dict:
    """Upload a PDF or document into the configured MinIO RAG bucket for later indexing and semantic retrieval."""
    if not filename.strip():
        return _error("filename wajib diisi.")
    if not content_base64:
        return _error("content_base64 wajib diisi.")
    try:
        body = base64.b64decode(content_base64, validate=True)
    except Exception as exc:
        return _error(f"Base64 document tidak valid: {exc}")

    endpoint = os.getenv("RAG_MINIO_ENDPOINT", os.getenv("MINIO_ENDPOINT", "http://minio:9000"))
    if not endpoint.startswith(("http://", "https://")):
        endpoint = f"http://{endpoint}"
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=os.getenv("RAG_MINIO_ACCESS_KEY", os.getenv("MINIO_ACCESS_KEY", "minioadmin")),
        aws_secret_access_key=os.getenv("RAG_MINIO_SECRET_KEY", os.getenv("MINIO_SECRET_KEY", "minioadmin123")),
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )
    bucket = os.getenv("RAG_MINIO_BUCKET", os.getenv("MINIO_BUCKET", "nexus-rag"))
    try:
        client.head_bucket(Bucket=bucket)
    except Exception:
        client.create_bucket(Bucket=bucket)
    safe_name = os.path.basename(filename).strip() or "document.pdf"
    target_key = object_key.strip("/") if object_key else f"{folder.strip('/')}/{safe_name}"
    mime = content_type or "application/octet-stream"
    client.put_object(
        Bucket=bucket,
        Key=target_key,
        Body=body,
        ContentType=mime,
        Metadata={"source_type": "internal_document", "filename": safe_name},
    )
    url = client.generate_presigned_url("get_object", Params={"Bucket": bucket, "Key": target_key}, ExpiresIn=300)
    return {"status": "success", "message": "Dokumen berhasil diunggah ke MinIO.", "bucket": bucket, "object_key": target_key, "url": url, "filename": safe_name}


def ingest_rag_documents() -> dict:
    """Index internal RAG files and stored Detik Finance articles into Chroma."""
    root = os.getenv("RAG_ROOT", "/opt/integrations/RAG")
    script = os.path.join(root, "src", "embed_documents.py")
    if not os.path.isfile(script):
        return _error(f"Indexer RAG tidak ditemukan: {script}")
    try:
        completed = subprocess.run(
            [sys.executable, script], cwd=os.path.join(root, "src"),
            capture_output=True, text=True, timeout=900, check=False,
        )
        if completed.returncode != 0:
            return _error(completed.stderr[-4000:] or "Indexing RAG gagal.")
        return {"status": "success", "message": "Internal files dan Detik Finance berhasil di-index.", "output": completed.stdout[-4000:]}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _error(f"Indexing RAG gagal: {exc}")


def get_rag_source_url(object_key: str, expires: int = 300, bucket_name: str | None = None) -> dict:
    """Create a short-lived URL for a MinIO-backed RAG source object."""
    try:
        return _source_url(object_key, expires, bucket_name)
    except Exception as exc:
        LOG.exception("RAG source URL failed")
        return _error(f"Source URL gagal: {exc}")


def register_rag_tools(mcp):
    mcp.tool(name="upload_document_to_minio")(upload_document_to_minio)
    mcp.tool(name="ingest_rag_documents")(ingest_rag_documents)
    mcp.tool(name="search_rag")(search_rag)
    mcp.tool(name="get_rag_source_url")(get_rag_source_url)
    mcp.tool(name="ask_rag")(ask_rag)
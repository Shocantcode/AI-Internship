"""MinIO persistence for article JSON used by the news RAG ingestion pipeline."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import boto3
from botocore.exceptions import ClientError
from botocore.config import Config


DEFAULT_BUCKET = "news"
_REQUIRED_FIELDS = (
    "evidence_id",
    "research_session_id",
    "request_id",
    "title",
    "source",
    "category",
    "author",
    "url",
    "content",
    "relevance_score",
)


def _s3_client():
    endpoint = os.getenv("MINIO_ENDPOINT", "http://minio:9000")
    if not endpoint.startswith(("http://", "https://")):
        endpoint = f"http://{endpoint}"
    secure = os.getenv("MINIO_SECURE", "").strip().lower()
    if secure in {"true", "1", "yes"}:
        endpoint = re.sub(r"^http://", "https://", endpoint)
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=os.getenv("MINIO_ACCESS_KEY", "minioadmin"),
        aws_secret_access_key=os.getenv("MINIO_SECRET_KEY", "minioadmin123"),
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def _bucket() -> str:
    return os.getenv("MINIO_BUCKET_NEWS", DEFAULT_BUCKET).strip() or DEFAULT_BUCKET


def _ensure_bucket(s3, bucket: str) -> None:
    try:
        s3.head_bucket(Bucket=bucket)
    except ClientError as exc:
        error_code = str(exc.response.get("Error", {}).get("Code", ""))
        if error_code not in {"404", "NoSuchBucket", "NotFound"}:
            raise
        s3.create_bucket(Bucket=bucket)


def _source_slug(source: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", source.lower()).strip("_")
    return slug or "unknown_source"


def topic_name(query: str) -> str:
    value = re.sub(r"\b(news|berita|hari ini|today|terbaru|latest)\b", " ", query or "", flags=re.IGNORECASE)
    value = re.sub(r"\s+", " ", value).strip(" ,.-")
    return value.title() or "General News"


def object_key(article: Any) -> str:
    source = str(getattr(article, "source", "") or "unknown_source")
    url = str(getattr(article, "url", "") or "")
    detik_id = re.search(r"(?:^|/)d-(\d+)(?:/|$|-)", url)
    filename = f"d-{detik_id.group(1)}.json" if detik_id else f"{hashlib.sha256(url.encode('utf-8')).hexdigest()}.json"
    return f"{_source_slug(source)}/{filename}"


def _article_payload(article: Any, query: str | None = None) -> dict[str, Any]:
    if hasattr(article, "model_dump"):
        values = article.model_dump()
    elif hasattr(article, "dict"):
        values = article.dict()
    else:
        values = vars(article)
    payload = {field: values.get(field) for field in _REQUIRED_FIELDS if values.get(field) is not None}
    if query is not None:
        payload.update({
            "published_at": values.get("published_at"),
            "query": query,
            "topic": topic_name(query),
            "artifact_id": hashlib.sha256(str(values.get("url") or "").encode("utf-8")).hexdigest(),
        })
    return {key: value for key, value in payload.items() if value is not None}


def store_articles(articles: list[Any], query: str | None = None) -> dict[str, Any]:
    """Store complete, unchunked article content and isolate per-object failures."""
    result = {"stored": 0, "skipped": 0, "failed": 0, "verified": 0, "objects": [], "errors": [], "bucket": _bucket()}
    if not articles:
        return result

    s3 = _s3_client()
    bucket = result["bucket"]
    _ensure_bucket(s3, bucket)
    for index, article in enumerate(articles, 1):
        key = object_key(article)
        try:
            try:
                existing_object = s3.head_object(Bucket=bucket, Key=key)
                existing_metadata = existing_object.get("Metadata")
                if existing_metadata is None or existing_metadata.get("source_type") == "external_news":
                    result["skipped"] += 1
                    result["verified"] += 1
                    result["objects"].append({"bucket": bucket, "object_key": key, "url": getattr(article, "url", None), "etag": existing_object.get("ETag", "").strip('"'), "content_length": existing_object.get("ContentLength", 0)})
                    continue
            except ClientError as exc:
                if str(exc.response.get("Error", {}).get("Code", "")) not in {"404", "NoSuchKey", "NotFound"}:
                    raise
            payload = _article_payload(article, query)
            payload["source_type"] = "external_news"
            payload["storage"] = "minio"
            payload["collected_at"] = datetime.now(timezone.utc).isoformat()
            body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            s3.put_object(
                Bucket=bucket,
                Key=key,
                Body=body,
                ContentType="application/json",
                Metadata={
                    "source": str(payload["source"] or "unknown"),
                    "category": str(payload["category"] or "unknown"),
                },
            )
            stored_object = s3.head_object(Bucket=bucket, Key=key)
            if stored_object.get("ContentLength", 0) != len(body):
                raise IOError(f"MinIO object verification size mismatch for {key}")
            downloaded = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
            if downloaded != body:
                raise IOError(f"MinIO object verification download mismatch for {key}")
            result["stored"] += 1
            result["verified"] += 1
            result["objects"].append({"bucket": bucket, "object_key": key, "url": getattr(article, "url", None), "etag": stored_object.get("ETag", "").strip('"'), "content_length": stored_object.get("ContentLength", 0)})
        except Exception as exc:
            result["failed"] += 1
            result["errors"].append({"article": index, "key": key, "error": str(exc)})
    if query and result["verified"]:
        topic = topic_name(query)
        session_id = str(getattr(articles[0], "research_session_id", "") or "unknown_session")
        metadata_key = f"intelligence/{_source_slug(topic)}/{session_id}/metadata.json"
        metadata = {
            "artifact": "articles.json",
            "type": "news_intelligence",
            "topic": topic,
            "query": query,
            "job_id": session_id,
            "source": "Computer Use",
            "storage": "minio",
            "bucket": bucket,
            "object_key": metadata_key,
            "article_count": result["verified"],
            "status": "completed" if not result["failed"] else "failed",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "objects": result["objects"],
        }
        body = json.dumps(metadata, ensure_ascii=False, indent=2, default=str).encode("utf-8")
        s3.put_object(Bucket=bucket, Key=metadata_key, Body=body, ContentType="application/json")
        verified_metadata = s3.head_object(Bucket=bucket, Key=metadata_key)
        if int(verified_metadata.get("ContentLength", 0)) != len(body):
            raise IOError(f"MinIO metadata verification size mismatch for {metadata_key}")
        result["artifact"] = metadata
    return result

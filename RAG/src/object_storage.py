"""Centralized S3-compatible object storage access for the RAG service."""

from __future__ import annotations

import os
import re
from typing import Any, Iterator

import boto3
from botocore.config import Config


def _endpoint() -> str:
    endpoint = os.getenv("RAG_MINIO_ENDPOINT", os.getenv("MINIO_ENDPOINT", "http://minio:9000"))
    if not endpoint.startswith(("http://", "https://")):
        endpoint = f"http://{endpoint}"
    if os.getenv("MINIO_SECURE", "false").lower() in {"1", "true", "yes"}:
        endpoint = re.sub(r"^http://", "https://", endpoint)
    return endpoint


def client():
    return boto3.client(
        "s3",
        endpoint_url=_endpoint(),
        aws_access_key_id=os.getenv("RAG_MINIO_ACCESS_KEY", os.getenv("MINIO_ACCESS_KEY", "minioadmin")),
        aws_secret_access_key=os.getenv("RAG_MINIO_SECRET_KEY", os.getenv("MINIO_SECRET_KEY", "minioadmin123")),
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def bucket() -> str:
    return os.getenv("RAG_MINIO_BUCKET", "nexus-rag").strip() or "nexus-rag"


def prefix() -> str:
    return os.getenv("RAG_MINIO_DOCUMENT_PREFIX", "documents").strip("/")


def ensure_bucket(storage_client, name: str | None = None) -> str:
    name = name or bucket()
    try:
        storage_client.head_bucket(Bucket=name)
    except Exception as exc:
        code = str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))
        if code not in {"404", "NoSuchBucket", "NotFound"}:
            raise
        storage_client.create_bucket(Bucket=name)
    return name


def list_objects(storage_client=None, name: str | None = None, object_prefix: str | None = None) -> Iterator[dict[str, Any]]:
    storage_client = storage_client or client()
    name = name or bucket()
    paginator = storage_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=name, Prefix=object_prefix if object_prefix is not None else prefix() + "/"):
        yield from page.get("Contents", [])


def get_object(key: str, storage_client=None, name: str | None = None) -> tuple[bytes, dict[str, Any]]:
    storage_client = storage_client or client()
    name = name or bucket()
    response = storage_client.get_object(Bucket=name, Key=key)
    return response["Body"].read(), response


def upload_object(key: str, body: bytes, content_type: str, metadata: dict[str, str] | None = None, storage_client=None, name: str | None = None) -> None:
    storage_client = storage_client or client()
    name = ensure_bucket(storage_client, name)
    storage_client.put_object(Bucket=name, Key=key, Body=body, ContentType=content_type, Metadata=metadata or {})


def presigned_url(key: str, expires: int = 300, storage_client=None, name: str | None = None) -> str:
    storage_client = storage_client or client()
    return storage_client.generate_presigned_url("get_object", Params={"Bucket": name or bucket(), "Key": key}, ExpiresIn=expires)

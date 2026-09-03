"""Normalize model output into safe, user-facing RAG response data."""

from __future__ import annotations

import json
import re
from typing import Any


def clean_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    text = value.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\r\n", "\n")
    text = re.sub(r"(?is)>{2,}\s*(?:sources?|source)\s*>{2,}.*?(?:<{2,}|$)", "", text)
    text = re.sub(r"(?im)^\s*>{2,}\s*(?:sources?|source)\s*>{2,}\s*$", "", text)
    text = re.sub(r"(?im)^\s*<{2,}\s*$", "", text)
    text = re.sub(r"(?im)^\s*\[?sources?\]?\s*:?\s*$", "", text)
    text = re.sub(r"(?m)^\s*[*]{3,}\s*$", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def normalize_response(answer: Any, results: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    if isinstance(answer, dict):
        answer = answer.get("answer", "")
    if isinstance(answer, str):
        try:
            decoded = json.loads(answer)
            if isinstance(decoded, dict):
                answer = decoded.get("answer", answer)
        except json.JSONDecodeError:
            pass
    clean_answer = clean_text(answer)
    sources = []
    for result in results or []:
        metadata = result.get("metadata", {})
        sources.append({
            "title": metadata.get("filename") or metadata.get("title") or result.get("source", "Unknown source"),
            "source_type": metadata.get("source_type", "internal_document"),
            "storage": "minio" if metadata.get("bucket") else None,
            "bucket": metadata.get("bucket"),
            "object_key": metadata.get("object_key"),
            "page": metadata.get("page", result.get("page", 0)),
            "relevance": result.get("relevance_score", result.get("gaussian_score", 0)),
            "published_at": metadata.get("published_at"),
            "url": metadata.get("url"),
        })
    if not clean_answer and not sources:
        return {"answer": "Saya belum menemukan informasi yang cukup relevan untuk menjawab pertanyaan tersebut.", "status": "no_relevant_information", "sources": [], "confidence": None}
    return {"answer": clean_answer, "status": "success", "sources": sources, "confidence": None}

"""Data models for collected Detik Finance articles."""

from __future__ import annotations

from pydantic import BaseModel, Field


class NewsArticle(BaseModel):
    evidence_id: str | None = None
    research_session_id: str | None = None
    request_id: str | None = None
    title: str
    source: str = "Detik Finance"
    category: str = "Finance"
    author: str | None = None
    published_at: str | None = None
    url: str
    content: str
    relevance_score: float = 0.0


class NewsSearchResult(BaseModel):
    research_session_id: str | None = None
    request_id: str | None = None
    query: str
    source: str = "Detik Finance"
    requested: int
    articles: list[NewsArticle] = Field(default_factory=list)
    failed_articles: list[str] = Field(default_factory=list)
    status: str = "success"
    message: str | None = None
    error_type: str | None = None

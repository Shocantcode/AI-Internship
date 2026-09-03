"""Deterministic query understanding used before hybrid RAG retrieval."""

from __future__ import annotations

import re
from dataclasses import dataclass

NORMALIZATION = {
    "pendapatan": "revenue",
    "penjualan": "sales",
    "penjualan": "sales",
    "suku bunga": "interest rate",
    "ramalan": "forecast",
    "prediksi": "forecast",
    "berita terbaru": "latest news",
    "berita": "news",
    "dampak": "impact",
    "bca": "bbca",
}
STOPWORDS = {"apa", "bagaimana", "kenapa", "mengapa", "yang", "dan", "atau", "kita", "dari", "untuk", "terhadap", "the", "our", "what", "how", "why", "about", "does", "this", "that"}


@dataclass(frozen=True)
class QueryProfile:
    normalized: str
    keywords: tuple[str, ...]
    entities: tuple[str, ...]
    topics: tuple[str, ...]
    intent: str
    freshness: str


def understand_query(query: str) -> QueryProfile:
    normalized = re.sub(r"\s+", " ", query.strip().lower())
    for source, target in sorted(NORMALIZATION.items(), key=lambda item: -len(item[0])):
        normalized = normalized.replace(source, target)
    words = re.findall(r"[a-z0-9][a-z0-9-]*", normalized)
    keywords = tuple(dict.fromkeys(word for word in words if len(word) > 2 and word not in STOPWORDS))
    entities = tuple(dict.fromkeys(re.findall(r"\b(?:product\s+[a-z]|q[1-4]|20\d{2}|bca|bank indonesia|south|north|selatan|utara)\b", normalized, re.I)))
    topic_terms = {"revenue", "sales", "forecast", "interest", "rate", "inflation", "news", "market", "economy", "regulation", "inventory", "contract", "report", "bbca"}
    topics = tuple(word for word in keywords if word in topic_terms)
    if re.search(r"news|market|economy|economic|latest|current|recent|today|terbaru|terkini|hari ini", normalized):
        intent = "NEWS"
    elif re.search(r"forecast|prediction|future|next month|next \d+ days|bulan depan|prediksi", normalized):
        intent = "FORECAST"
    elif re.search(r"invoice|contract|agreement|document|report|laporan|dokumen", normalized):
        intent = "DOCUMENT"
    else:
        intent = "INTERNAL_KNOWLEDGE"
    freshness = "recent" if re.search(r"latest|current|recent|today|terbaru|terkini|hari ini|minggu ini", normalized) else "historical"
    return QueryProfile(normalized, keywords, entities, topics, intent, freshness)


def expanded_query(profile: QueryProfile) -> str:
    expansions = {"interest": "rate suku bunga biaya pinjaman", "revenue": "pendapatan omzet", "sales": "penjualan transaksi", "forecast": "prediksi proyeksi"}
    return " ".join([profile.normalized, *(expansions.get(keyword, "") for keyword in profile.keywords)]).strip()

# MCP News Storage

`get_news` stores every returned article as one UTF-8 JSON object in the configured
MinIO news bucket. The object key is `detik_finance/d-<article-id>.json` for Detik
URLs, or `<source-slug>/<sha256-url>.json` for other sources. Existing keys are
skipped, so repeated requests do not create duplicates.

The JSON contains `title`, `source`, `category`, `author`, `url`, `content`, and
`relevance_score`. `published_at` is intentionally excluded. Content is stored
complete and unchunked; RAG ingestion should list objects, download each JSON,
extract `content`, create a Document, then chunk and embed it.

Configure storage with `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`,
`MINIO_BUCKET_NEWS` (default `news`), and `MINIO_SECURE`.

Internal RAG documents are read from MinIO by `RAG/src/embed_documents.py`, not
from a local documents directory. Configure `RAG_MINIO_BUCKET` (default
`nexus-rag`), `RAG_MINIO_DOCUMENT_PREFIX` (default `documents`), and optionally
`RAG_MINIO_ENDPOINT`. Run `python RAG/scripts/migrate_documents_to_minio.py`
once to migrate legacy internal files before calling `ingest_rag_documents`.
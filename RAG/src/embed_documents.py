import json
import mimetypes
import os
from pathlib import Path

import chromadb
from dotenv import load_dotenv
from google import genai
from google.genai import types
from chunking import split_text
from object_storage import bucket as rag_bucket
from object_storage import client as storage_client
from object_storage import ensure_bucket
from object_storage import get_object, list_objects, prefix as rag_prefix


# =========================================================
# PATH
# =========================================================

BASE_DIR = Path(__file__).resolve().parent.parent

CHUNKS_FILE = (
    BASE_DIR
    / "Data"
    / "extracted"
    / "chunks.json"
)

CHROMA_DIR = (
    BASE_DIR
    / "Data"
    / "chroma_db"
)


# =========================================================
# CONFIG
# =========================================================

MODEL_NAME = "gemini-embedding-001"

EMBEDDING_DIMENSION = 768

COLLECTION_NAME = "stock_research"


# =========================================================
# ENVIRONMENT
# =========================================================

load_dotenv(BASE_DIR.parent / ".env")

api_key = os.getenv("GEMINI_API_KEY")

if not api_key:
    raise ValueError(
        "GEMINI_API_KEY tidak ditemukan di .env"
    )


client = genai.Client(
    api_key=api_key
)


# =========================================================
# CHROMADB
# =========================================================

chroma_client = chromadb.PersistentClient(
    path=str(CHROMA_DIR)
)

collection = chroma_client.get_or_create_collection(
    name=COLLECTION_NAME
)


def load_news_documents():
    """Read complete article JSON from MinIO; chunk only for vector indexing."""
    s3 = storage_client()
    bucket = os.getenv("MINIO_BUCKET_NEWS", "news")
    prefix = os.getenv("MINIO_NEWS_PREFIX", "detik_finance/").strip("/") + "/"
    paginator = s3.get_paginator("list_objects_v2")
    documents = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for item in page.get("Contents", []):
            key = item["Key"]
            if not key.endswith(".json"):
                continue
            article = json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read())
            content = str(article.get("content", "")).strip()
            if not content:
                continue
            for chunk_number, text in enumerate(split_text(content)):
                documents.append({
                    "id": f"news:{key}:{chunk_number}",
                    "text": text,
                    "metadata": {
                        "source": article.get("source", "Unknown"),
                        "source_type": "external_news",
                        "category": article.get("category", ""),
                        "author": article.get("author", ""),
                        "url": article.get("url", ""),
                        "title": article.get("title", ""),
                        "bucket": bucket,
                        "object_key": key,
                        "filename": key.rsplit("/", 1)[-1],
                        "etag": item.get("ETag", "").strip('"'),
                        "page": 0,
                        "chunk": chunk_number,
                    },
                })
    return documents


def _extract_text(body: bytes, key: str) -> list[tuple[int, str]]:
    """Extract from an object in memory; PDFs use a temporary parser buffer only."""
    suffix = Path(key).suffix.lower()
    if suffix == ".pdf":
        import fitz
        document = fitz.open(stream=body, filetype="pdf")
        return [(page_number, page.get_text("text")) for page_number, page in enumerate(document, 1)]
    text = body.decode("utf-8", errors="replace")
    if suffix == ".json":
        try:
            value = json.loads(text)
            text = json.dumps(value, ensure_ascii=False, indent=2)
        except json.JSONDecodeError:
            pass
    return [(0, text)]


def load_internal_documents():
    """Load internal source documents directly from the configured RAG MinIO bucket."""
    s3 = storage_client()
    bucket = rag_bucket()
    ensure_bucket(s3, bucket)
    documents = []
    supported = {".pdf", ".txt", ".md", ".csv", ".json"}
    objects = [item for item in list_objects(s3, bucket, rag_prefix() + "/") if Path(item["Key"]).suffix.lower() in supported]
    print(f"[RAG] Loading documents from MinIO bucket={bucket}; found {len(objects)} objects")
    for item in objects:
        key = item["Key"]
        body, response = get_object(key, s3, bucket)
        etag = item.get("ETag", "").strip('"')
        for page_number, page_text in _extract_text(body, key):
            for chunk_number, chunk in enumerate(split_text(page_text)):
                documents.append({
                    "id": f"minio:{bucket}:{key}:{etag}:{page_number}:{chunk_number}",
                    "text": chunk,
                    "metadata": {
                        "source": "MinIO internal document",
                        "source_type": "internal_document",
                        "document_type": Path(key).stem,
                        "bucket": bucket,
                        "object_key": key,
                        "filename": key.rsplit("/", 1)[-1],
                        "content_type": response.get("ContentType", mimetypes.guess_type(key)[0] or "application/octet-stream"),
                        "etag": etag,
                        "last_modified": item.get("LastModified").isoformat() if item.get("LastModified") else "",
                        "page": page_number,
                        "chunk": chunk_number,
                    },
                })
    return documents


# =========================================================
# GEMINI EMBEDDING
# =========================================================

def create_embedding(text):

    result = client.models.embed_content(
        model=MODEL_NAME,
        contents=text,
        config=types.EmbedContentConfig(
            task_type="RETRIEVAL_DOCUMENT",
            output_dimensionality=EMBEDDING_DIMENSION
        )
    )

    return result.embeddings[0].values


# =========================================================
# MAIN
# =========================================================

def main():
    chunks = load_internal_documents()
    try:
        chunks.extend(load_news_documents())
    except Exception as exc:
        print(f"News MinIO indexing skipped: {exc}")

    # Object keys are the identity; remove prior vectors for changed objects before upsert.
    object_keys = {chunk["metadata"].get("object_key") for chunk in chunks if chunk["metadata"].get("object_key")}
    for object_key in object_keys:
        try:
            collection.delete(where={"object_key": object_key})
        except Exception as exc:
            print(f"[RAG] Could not refresh vectors for {object_key}: {exc}")

    print(f"Total chunks: {len(chunks)}")

    for i, chunk in enumerate(chunks):

        print(
            f"Embedding {i + 1}/{len(chunks)}"
        )

        text = chunk["text"]

        embedding = create_embedding(
            text
        )

        collection.upsert(
            ids=[chunk["id"]],

            documents=[
                text
            ],

            embeddings=[
                embedding
            ],

            metadatas=[
                {
                    **chunk["metadata"]
                }
            ]
        )

    print()
    print("=" * 60)
    print("CHROMADB INDEXING SELESAI")
    print("=" * 60)

    print(
        f"Collection : {COLLECTION_NAME}"
    )

    print(
        f"Documents  : {collection.count()}"
    )

    print(
        f"Database   : {CHROMA_DIR}"
    )


if __name__ == "__main__":
    main()
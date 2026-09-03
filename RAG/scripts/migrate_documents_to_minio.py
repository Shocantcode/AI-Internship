"""One-time migration of legacy RAG source files into MinIO.

This script is intentionally outside the runtime ingestion path. After migration,
`embed_documents.py` reads only the configured MinIO bucket.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import mimetypes
import sys

SRC = Path(__file__).resolve().parents[1] / "Data"
sys.path.insert(0, str(SRC.parent / "src"))
from object_storage import bucket, client, ensure_bucket, upload_object  # noqa: E402

SUPPORTED = {".pdf", ".txt", ".md", ".csv", ".json"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=SRC / "internal")
    parser.add_argument("--prefix", default="documents")
    args = parser.parse_args()

    storage = client()
    name = ensure_bucket(storage)
    files = sorted(path for path in args.source.rglob("*") if path.is_file() and path.suffix.lower() in SUPPORTED)
    for path in files:
        key = f"{args.prefix.strip('/')}/{path.relative_to(args.source).as_posix()}"
        upload_object(key, path.read_bytes(), mimetypes.guess_type(path.name)[0] or "application/octet-stream", {"source_type": "internal_document", "filename": path.name}, storage, name)
        print(f"Migrated {path} -> s3://{name}/{key}")
    print(f"Migrated {len(files)} documents to bucket={name}")


if __name__ == "__main__":
    main()

import os

import httpx


def register_system_tools(mcp):
    @mcp.tool()
    def hello(name: str) -> str:
        return f"Hello {name}, MCP Hub berhasil!"

    @mcp.tool()
    def get_status() -> dict:
        rag_status = _rag_status()
        return {
            "status": "online",
            "service": "MCP Hub",
            "version": "1.0.0",
            "services": {
                "dag": _airflow_reachable(),
                "rag": rag_status["connected"],
                "rag_storage": rag_status,
                "vision": os.path.isfile(os.getenv("VISION_CODE", "/opt/integrations/VisionProcessing/Code.py")),
            },
        }

    @mcp.tool()
    def add_numbers(a: float, b: float) -> float:
        return a + b


def _airflow_reachable() -> bool:
    url = os.getenv("AIRFLOW_URL", "http://airflow-apiserver:8080").rstrip("/")
    try:
        response = httpx.get(f"{url}/api/v2/monitor/health", timeout=3.0)
        return response.is_success
    except httpx.HTTPError:
        return False


def _rag_status() -> dict:
    try:
        import boto3
        endpoint = os.getenv("RAG_MINIO_ENDPOINT", os.getenv("MINIO_ENDPOINT", "http://minio:9000"))
        client = boto3.client("s3", endpoint_url=endpoint, aws_access_key_id=os.getenv("MINIO_ACCESS_KEY", "minioadmin"), aws_secret_access_key=os.getenv("MINIO_SECRET_KEY", "minioadmin123"))
        bucket = os.getenv("RAG_MINIO_BUCKET", "nexus-rag")
        client.head_bucket(Bucket=bucket)
        count = sum(len(page.get("Contents", [])) for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=os.getenv("RAG_MINIO_DOCUMENT_PREFIX", "documents").strip("/") + "/"))
        return {"connected": True, "bucket": bucket, "documents": count}
    except Exception as exc:
        return {"connected": False, "bucket": os.getenv("RAG_MINIO_BUCKET", "nexus-rag"), "documents": 0, "message": str(exc)}
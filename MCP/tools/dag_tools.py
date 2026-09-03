import logging
import os
from datetime import datetime, timezone
from uuid import uuid4

import httpx


LOG = logging.getLogger(__name__)


def _client() -> httpx.Client:
    url = os.getenv("AIRFLOW_URL", "http://airflow-apiserver:8080").rstrip("/")
    username = os.getenv("AIRFLOW_USERNAME", "airflow")
    password = os.getenv("AIRFLOW_PASSWORD", "airflow")
    if not username or not password:
        raise ValueError("AIRFLOW_USERNAME dan AIRFLOW_PASSWORD wajib diisi.")

    token_response = httpx.post(
        f"{url}/auth/token/cli",
        json={"username": username, "password": password},
        timeout=20.0,
    )
    token_response.raise_for_status()
    access_token = token_response.json().get("access_token")
    if not access_token:
        raise ValueError("Airflow tidak mengembalikan access token.")

    return httpx.Client(
        base_url=f"{url}/api/v2",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=20.0,
    )


def _error(message: str) -> dict:
    return {"status": "error", "service": "dag", "message": message}


def register_dag_tools(mcp):
    @mcp.tool()
    def run_dag(dag_id: str) -> dict:
        if not dag_id.strip():
            return _error("dag_id wajib diisi.")
        dag_run_id = f"mcp__{uuid4().hex}"
        payload = {
            "dag_run_id": dag_run_id,
            "logical_date": datetime.now(timezone.utc).isoformat(),
            "conf": {},
        }
        try:
            with _client() as client:
                response = client.post(f"/dags/{dag_id}/dagRuns", json=payload)
                response.raise_for_status()
                data = response.json()
            return {"status": "success", "dag_id": dag_id, "dag_run_id": data.get("dag_run_id", dag_run_id), "run": data}
        except (httpx.HTTPError, ValueError) as exc:
            LOG.exception("Airflow DAG trigger failed for %s", dag_id)
            return _error(f"Tidak dapat menjalankan DAG: {exc}")

    @mcp.tool()
    def get_dag_status(dag_id: str) -> dict:
        if not dag_id.strip():
            return _error("dag_id wajib diisi.")
        try:
            with _client() as client:
                response = client.get(f"/dags/{dag_id}/dagRuns", params={"limit": 1, "order_by": "-logical_date"})
                response.raise_for_status()
                data = response.json()
            runs = data.get("dag_runs", [])
            return {"status": "success", "dag_id": dag_id, "latest": runs[0] if runs else None}
        except (httpx.HTTPError, ValueError) as exc:
            LOG.exception("Airflow DAG status failed for %s", dag_id)
            return _error(f"Tidak dapat mengambil status DAG: {exc}")
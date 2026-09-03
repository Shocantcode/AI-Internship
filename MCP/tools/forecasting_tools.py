import io
import json
import logging
import os

LOG = logging.getLogger(__name__)


def _error(message: str) -> dict:
    return {"status": "error", "service": "forecasting", "message": message}


def _s3_client():
    import boto3

    return boto3.client(
        "s3",
        endpoint_url=os.getenv("MINIO_ENDPOINT", "http://minio:9000"),
        aws_access_key_id=os.getenv("MINIO_ACCESS_KEY", "minioadmin"),
        aws_secret_access_key=os.getenv("MINIO_SECRET_KEY", "minioadmin123"),
        config=boto3.session.Config(
            signature_version="s3v4", s3={"addressing_style": "path"}
        ),
    )


def _prefix() -> str:
    root = os.getenv("MINIO_PREFIX", "Data").strip("/")
    return f"{root}/Gold/Mobile_Sales_Data/forecasting"


def _read_parquet(client, bucket: str, key: str, limit: int):
    import pyarrow.parquet as parquet

    body = client.get_object(Bucket=bucket, Key=key)["Body"].read()
    table = parquet.read_table(io.BytesIO(body))
    rows = table.slice(0, limit).to_pandas().to_dict(orient="records")
    for row in rows:
        for name, value in row.items():
            if hasattr(value, "isoformat"):
                row[name] = value.isoformat()
            elif hasattr(value, "item"):
                row[name] = value.item()
    return rows, table.num_rows, [field.name for field in table.schema]


def register_forecasting_tools(mcp):
    @mcp.tool()
    def get_forecasting_preview(limit: int = 7) -> dict:
        """Preview forecast results, model evaluation, and source profile from MinIO."""
        if limit < 1 or limit > 100:
            return _error("limit harus antara 1 sampai 100.")

        bucket = os.getenv("MINIO_BUCKET", "mobilesaledata")
        prefix = _prefix()
        client = None
        try:
            client = _s3_client()
            keys = {
                item["Key"]
                for item in client.list_objects_v2(
                    Bucket=bucket, Prefix=f"{prefix}/"
                ).get("Contents", [])
            }
            forecast_key = f"{prefix}/forecast.parquet"
            evaluation_key = f"{prefix}/evaluation.parquet"
            profile_key = f"{prefix}/profile.json"
            if forecast_key not in keys:
                return _error(
                    f"Output forecast belum ditemukan di s3://{bucket}/{prefix}/. "
                    "Jalankan task forecasting terlebih dahulu."
                )

            forecast, forecast_count, forecast_columns = _read_parquet(
                client, bucket, forecast_key, limit
            )
            evaluation, _, _ = _read_parquet(client, bucket, evaluation_key, limit) if evaluation_key in keys else ([], 0, [])
            profile = {}
            if profile_key in keys:
                profile = json.loads(
                    client.get_object(Bucket=bucket, Key=profile_key)["Body"].read()
                )
            return {
                "status": "success",
                "service": "forecasting",
                "source": f"s3://{bucket}/{prefix}/",
                "forecast": {
                    "columns": forecast_columns,
                    "total_records": forecast_count,
                    "rows": forecast,
                },
                "evaluation": evaluation,
                "profile": profile,
                "artifacts": {
                    "forecast": f"s3://{bucket}/{prefix}/forecast.parquet",
                    "evaluation": f"s3://{bucket}/{prefix}/evaluation.parquet",
                    "profile": f"s3://{bucket}/{prefix}/profile.json",
                    "historical_chart": f"s3://{bucket}/{prefix}/historical_forecast.png",
                    "validation_chart": f"s3://{bucket}/{prefix}/validation_actual_vs_predicted.png",
                },
            }
        except Exception as exc:
            LOG.exception("Forecasting preview failed")
            return _error(f"Preview forecasting gagal: {exc}")
import io
import json
import math
import os
import threading
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

import boto3
import numpy as np
import pandas as pd
from botocore.exceptions import ClientError
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sklearn.linear_model import LinearRegression

MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "http://localhost:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin123")
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "mobilesaledata")

app = FastAPI(title="NEXUS Forecasting API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://0.0.0.0:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
        "http://0.0.0.0:5174",
        "http://localhost:5175",
        "http://127.0.0.1:5175",
        "http://0.0.0.0:5175",
        "http://localhost:5176",
        "http://127.0.0.1:5176",
        "http://0.0.0.0:5176",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

JOBS: Dict[str, Dict[str, Any]] = {}


def _get_minio_client():
    endpoint_url = MINIO_ENDPOINT if MINIO_ENDPOINT.startswith(("http://", "https://")) else f"http://{MINIO_ENDPOINT}"
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=MINIO_ACCESS_KEY,
        aws_secret_access_key=MINIO_SECRET_KEY,
        config=boto3.session.Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def _ensure_minio_bucket(bucket_name: str = MINIO_BUCKET) -> None:
    client = _get_minio_client()
    try:
        client.head_bucket(Bucket=bucket_name)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code in {"404", "NoSuchBucket", "403"}:
            client.create_bucket(Bucket=bucket_name)
        else:
            raise


def _write_minio_bytes(key: str, payload: bytes, content_type: str = "application/octet-stream") -> str:
    client = _get_minio_client()
    _ensure_minio_bucket()
    client.put_object(Bucket=MINIO_BUCKET, Key=key, Body=payload, ContentType=content_type)
    return key


def _write_minio_json(key: str, payload: Dict[str, Any]) -> str:
    body = json.dumps(payload, ensure_ascii=False, default=str, indent=2).encode("utf-8")
    return _write_minio_bytes(key, body, content_type="application/json")


def _write_minio_parquet(key: str, frame: pd.DataFrame) -> str:
    buffer = io.BytesIO()
    frame.to_parquet(buffer, index=False)
    return _write_minio_bytes(key, buffer.getvalue(), content_type="application/vnd.apache.parquet")


def _verify_minio_object(key: str, expected_content_type: Optional[str] = None) -> Dict[str, Any]:
    response = _get_minio_client().head_object(Bucket=MINIO_BUCKET, Key=key)
    size = int(response.get("ContentLength", 0))
    if size <= 0:
        raise ValueError(f"MinIO object '{key}' is empty.")
    content_type = response.get("ContentType") or ""
    if expected_content_type and content_type not in {expected_content_type, "application/octet-stream"}:
        raise ValueError(f"MinIO object '{key}' has unexpected content type '{content_type}'.")
    return {"size": size, "content_type": content_type}


def _read_minio_bytes(key: str) -> bytes:
    client = _get_minio_client()
    response = client.get_object(Bucket=MINIO_BUCKET, Key=key)
    return response["Body"].read()


def _read_minio_parquet(key: str) -> pd.DataFrame:
    payload = _read_minio_bytes(key)
    return pd.read_parquet(io.BytesIO(payload))


def _read_minio_csv(key: str) -> pd.DataFrame:
    payload = _read_minio_bytes(key)
    return pd.read_csv(io.BytesIO(payload))


def _list_job_ids_from_minio() -> List[str]:
    client = _get_minio_client()
    _ensure_minio_bucket()
    paginator = client.get_paginator("list_objects_v2")
    job_ids: set[str] = set()
    for page in paginator.paginate(Bucket=MINIO_BUCKET, Prefix="jobs/"):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            parts = key.split("/")
            if len(parts) >= 3 and parts[0] == "jobs":
                job_ids.add(parts[1])
    return sorted(job_ids)


def _get_stage_key(job_id: str, stage: str) -> str:
    lookup = {
        "bronze": f"jobs/{job_id}/bronze/raw.csv",
        "silver": f"jobs/{job_id}/silver/data.parquet",
        "gold": f"jobs/{job_id}/gold/data.parquet",
        "forecast": f"jobs/{job_id}/forecast/forecast.parquet",
    }
    if stage not in lookup:
        raise ValueError(f"Unsupported stage '{stage}'")
    return lookup[stage]


class ForecastRequest(BaseModel):
    job_id: str
    date_column: Optional[str] = None
    datetime_column: Optional[str] = None
    target_column: Optional[str] = None
    horizon: Optional[int] = Field(default=None, ge=1, le=365)
    forecast_horizon: Optional[int] = Field(default=None, ge=1, le=365)

    @property
    def effective_date_column(self) -> str:
        return (self.date_column or self.datetime_column or "").strip()

    @property
    def effective_target_column(self) -> str:
        return (self.target_column or "").strip()

    @property
    def effective_horizon(self) -> int:
        return int(self.horizon if self.horizon is not None else self.forecast_horizon if self.forecast_horizon is not None else 14)


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _detect_datetime_columns(frame: pd.DataFrame) -> List[str]:
    matches = []
    for name in frame.columns:
        lowered = str(name).lower()
        if any(token in lowered for token in ("date", "time", "timestamp", "datetime")):
            matches.append(name)
    if matches:
        return matches
    for name in frame.columns:
        converted = pd.to_datetime(frame[name], errors="coerce")
        if converted.notna().mean() >= 0.8:
            return [name]
    return []


def _is_identifier_column(name: str, frame: pd.DataFrame) -> bool:
    """Check if a column appears to be an identifier or code field."""
    lowered = str(name).lower()
    # Check for identifier patterns
    identifier_patterns = {
        "id", "_id", "postcode", "zip", "zipcode", "postal_code",
        "code", "_code", "reference", "ref_", "number", "no_"
    }
    for pattern in identifier_patterns:
        if pattern in lowered or lowered.endswith(pattern) or lowered.startswith(pattern):
            return True
    
    # Check if column has very high cardinality (too many unique values)
    metric_tokens = ("sales", "revenue", "amount", "value", "total", "price", "qty", "quantity", "units", "count", "cost", "profit")
    if len(frame) > 10 and not any(token in lowered for token in metric_tokens):
        unique_ratio = frame[name].nunique() / len(frame)
        if unique_ratio > 0.9:  # 90%+ unique values suggests ID-like
            return True
    
    return False


def _detect_numeric_columns(frame: pd.DataFrame) -> List[str]:
    numeric = [name for name in frame.columns if pd.api.types.is_numeric_dtype(frame[name])]
    if numeric:
        return numeric
    converted = []
    for name in frame.columns:
        coerced = pd.to_numeric(frame[name], errors="coerce")
        if coerced.notna().mean() >= 0.8:
            converted.append(name)
    return converted


def _choose_target_column(frame: pd.DataFrame, numeric_columns: List[str]) -> str:
    """Choose and rank potential target columns, penalizing identifiers."""
    ranking = []
    for name in numeric_columns:
        lower = str(name).lower()
        
        # Don't recommend identifier columns
        if _is_identifier_column(name, frame):
            continue
        
        score = 0
        # Strong positive signals
        if any(token in lower for token in ("sales", "revenue", "amount", "value", "total", "price")):
            score += 15
        if any(token in lower for token in ("qty", "quantity", "units", "count")):
            score += 12
        # Mild positive signals
        if any(token in lower for token in ("metric", "score", "rate", "percent", "profit", "cost")):
            score += 8
        # Negative signals
        if any(token in lower for token in ("year", "month", "day", "hour", "minute", "second")):
            score -= 5
        if any(token in lower for token in ("id", "code", "key")):
            score -= 20
        
        # Bonus for reasonable variance (not flat)
        if name in frame.columns:
            std_dev = frame[name].std()
            mean_val = frame[name].mean()
            if mean_val != 0 and std_dev / abs(mean_val) > 0.1:  # CV > 10%
                score += 3
        
        ranking.append((score, name))
    
    if not ranking:
        # Fallback: return first numeric column if all others were identifiers
        if numeric_columns:
            return numeric_columns[0]
        raise ValueError("No numeric target column was found in the uploaded file.")
    
    # Return highest-ranked column
    return max(ranking, key=lambda item: item[0])[1]


def _build_forecast(frame: pd.DataFrame, date_column: str, target_column: str, horizon: int):
    if date_column not in frame.columns:
        raise ValueError(f"Date column '{date_column}' not found in the uploaded data.")
    if target_column not in frame.columns:
        raise ValueError(f"Target column '{target_column}' not found in the uploaded data.")

    data = frame[[date_column, target_column]].copy()
    data.columns = ["date", "value"]
    data["date"] = pd.to_datetime(data["date"], errors="coerce")
    data["value"] = pd.to_numeric(data["value"], errors="coerce")
    data = data.dropna(subset=["date", "value"]).sort_values("date").reset_index(drop=True)

    if len(data) < 3:
        raise ValueError("At least 3 valid rows are required to build a forecast.")

    actual_window = min(7, len(data))
    historical = data.tail(actual_window).copy()
    holdout_size = min(5, max(2, len(historical) // 3))
    if len(historical) <= holdout_size + 2:
        holdout_size = max(1, len(historical) // 2)
    if holdout_size >= len(historical):
        holdout_size = max(1, len(historical) - 1)

    train = historical.iloc[:-holdout_size].copy()
    validation = historical.iloc[-holdout_size:].copy()

    time_index_train = np.arange(len(train), dtype=float).reshape(-1, 1)
    time_index_valid = np.arange(len(train), len(train) + len(validation), dtype=float).reshape(-1, 1)
    model = LinearRegression()
    model.fit(time_index_train, train["value"].to_numpy())
    valid_pred = model.predict(time_index_valid)
    valid_errors = validation["value"].to_numpy() - valid_pred

    mae = float(np.mean(np.abs(valid_errors))) if len(valid_errors) > 0 else 0.0
    rmse = float(np.sqrt(np.mean(valid_errors ** 2))) if len(valid_errors) > 0 else 0.0
    mape = None
    if np.any(validation["value"].to_numpy() != 0):
        actual_nonzero = validation["value"].to_numpy()[validation["value"].to_numpy() != 0]
        pred_nonzero = valid_pred[validation["value"].to_numpy() != 0]
        mape = float(np.mean(np.abs((actual_nonzero - pred_nonzero) / actual_nonzero)) * 100.0)

    future_index = np.arange(len(historical), len(historical) + horizon, dtype=float).reshape(-1, 1)
    future_pred = model.predict(future_index)
    residual_std = float(np.std(valid_errors)) if len(valid_errors) > 0 else 0.0
    lower = future_pred - 1.96 * residual_std
    upper = future_pred + 1.96 * residual_std

    forecast_dates = pd.date_range(historical["date"].max() + pd.Timedelta(days=1), periods=horizon, freq="D")
    combined = []
    for item in historical.to_dict("records"):
        combined.append({"date": pd.Timestamp(item["date"]).strftime("%Y-%m-%d"), "actual": float(item["value"]), "forecast": None})

    for forecast_date, prediction, low, high in zip(forecast_dates, future_pred, lower, upper):
        combined.append({
            "date": forecast_date.strftime("%Y-%m-%d"),
            "actual": None,
            "forecast": float(prediction),
            "lower_bound": float(low),
            "upper_bound": float(high),
        })

    metrics = {
        "MAE": mae,
        "RMSE": rmse,
        "MAPE": mape,
        "forecast_horizon": horizon,
        "record_count": int(len(historical)),
    }

    output_rows = [
        {
            "date": row["date"],
            "actual": row.get("actual"),
            "forecast": row.get("forecast"),
            "lower_bound": row.get("lower_bound"),
            "upper_bound": row.get("upper_bound"),
        }
        for row in combined
    ]
    return {
        "series": output_rows,
        "metrics": metrics,
        "rows": output_rows,
        "download_rows": pd.DataFrame(
            output_rows
        ),
    }


def _job_or_404(job_id: str) -> Dict[str, Any]:
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Forecast job '{job_id}' was not found.")
    return job


def _build_manifest(job_id: str, filename: str, created_at: str, stages: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "job_id": job_id,
        "source_file": filename,
        "created_at": created_at,
        "stages": stages,
    }


def _safe_preview(frame: pd.DataFrame, page: int, per_page: int, search: Optional[str] = None, sort_by: Optional[str] = None, sort_desc: bool = False) -> Dict[str, Any]:
    preview = frame.copy()
    if search:
        needle = str(search).strip().lower()
        mask = preview.astype(str).apply(lambda row: row.str.lower().str.contains(needle, na=False)).any(axis=1)
        preview = preview[mask].reset_index(drop=True)
    if sort_by and sort_by in preview.columns:
        preview = preview.sort_values(by=sort_by, ascending=not sort_desc, na_position="last").reset_index(drop=True)
    total = len(preview)
    start = (page - 1) * per_page
    end = start + per_page
    page_frame = preview.iloc[start:end]
    rows = page_frame.astype(object).where(pd.notna(page_frame), None).to_dict(orient="records")
    return {
        "columns": list(preview.columns),
        "rows": rows,
        "total": total,
        "page": page,
        "per_page": per_page,
    }


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.post("/api/forecast/upload")
async def upload_forecast(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="A CSV file is required.")

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    try:
        frame = pd.read_csv(io.BytesIO(raw))
    except Exception as exc:  # pragma: no cover - defensive fallback for non-CSV uploads
        raise HTTPException(status_code=400, detail=f"Unable to read uploaded CSV: {exc}") from exc

    if frame.empty:
        raise HTTPException(status_code=400, detail="Uploaded CSV contains no rows.")

    datetime_columns = _detect_datetime_columns(frame)
    numeric_columns = _detect_numeric_columns(frame)
    if not datetime_columns:
        raise HTTPException(status_code=400, detail="No valid time/date column was detected in the uploaded CSV.")
    if not numeric_columns:
        raise HTTPException(status_code=400, detail="No valid numeric metric column was detected in the uploaded CSV.")

    target_column = _choose_target_column(frame, numeric_columns)
    date_column = datetime_columns[0]

    job_id = uuid.uuid4().hex
    created_at = datetime.now().astimezone().isoformat()
    job_prefix = f"jobs/{job_id}"
    bronze_raw_key = f"{job_prefix}/bronze/raw.csv"
    bronze_metadata_key = f"{job_prefix}/bronze/metadata.json"

    bronze_metadata = {
        "job_id": job_id,
        "filename": file.filename,
        "file_size": len(raw),
        "row_count": int(len(frame)),
        "column_count": int(len(frame.columns)),
        "columns": list(frame.columns),
        "data_types": {str(col): str(dtype) for col, dtype in frame.dtypes.items()},
        "created_at": created_at,
        "checksum": None,
    }

    _write_minio_bytes(bronze_raw_key, raw, content_type="text/csv")
    _write_minio_json(bronze_metadata_key, bronze_metadata)

    JOBS[job_id] = {
        "job_id": job_id,
        "filename": file.filename,
        "status": "uploaded",
        "stage": "uploaded",
        "progress": 10,
        "error": None,
        "frame": frame,
        "date_column": date_column,
        "target_column": target_column,
        "row_count": int(len(frame)),
        "columns": [{"name": name, "type": str(dtype)} for name, dtype in frame.dtypes.items()],
        "column_names": list(frame.columns),
        "datetime_columns": datetime_columns,
        "numeric_columns": numeric_columns,
        "download_rows": None,
        "result": None,
        "created_at": created_at,
        "csv_bytes": raw,
        "minio_paths": {
            "bronze_raw": bronze_raw_key,
            "bronze_metadata": bronze_metadata_key,
        },
    }

    return {
        "job_id": job_id,
        "status": "uploaded",
        "stage": "uploaded",
        "filename": file.filename,
        "row_count": int(len(frame)),
        "columns": [{"name": name, "type": str(dtype)} for name, dtype in frame.dtypes.items()],
        "column_names": list(frame.columns),
        "datetime_columns": datetime_columns,
        "numeric_columns": numeric_columns,
        "date_column": date_column,
        "target_column": target_column,
        "recommended_target_column": target_column,
        "recommended_datetime_column": date_column,
        "progress": 10,
    }


@app.post("/api/forecast/process")
async def process_forecast(payload: ForecastRequest):
    if not payload.effective_date_column:
        raise HTTPException(status_code=400, detail="A date column is required.")
    if not payload.effective_target_column:
        raise HTTPException(status_code=400, detail="A target column is required.")
    
    # Validate that target is not an identifier/code column
    job = _job_or_404(payload.job_id)
    if _is_identifier_column(payload.effective_target_column, job["frame"]):
        raise HTTPException(
            status_code=400,
            detail=f"{payload.effective_target_column} is not recommended as a forecasting target because it "
                   f"appears to be a postal-code/identifier field. Select a continuous numeric metric such as "
                   f"price, revenue, sales, or quantity."
        )

    job["date_column"] = payload.effective_date_column
    job["target_column"] = payload.effective_target_column
    job["status"] = "processing"
    job["stage"] = "bronze"
    job["progress"] = 15
    job["error"] = None

    def worker():
        try:
            job["stage"] = "bronze"
            job["progress"] = 25
            job["status"] = "processing"
            job["error"] = None

            source_frame = job["frame"].copy()
            bronze_metadata = {
                "job_id": job["job_id"],
                "filename": job["filename"],
                "file_size": len(job.get("csv_bytes", b"")),
                "row_count": int(len(source_frame)),
                "column_count": int(len(source_frame.columns)),
                "columns": list(source_frame.columns),
                "data_types": {str(col): str(dtype) for col, dtype in source_frame.dtypes.items()},
                "created_at": job["created_at"],
                "status": "completed",
            }
            _write_minio_json(f"jobs/{job['job_id']}/bronze/metadata.json", bronze_metadata)

            silver = source_frame.copy()
            # SILVER: Normalize column names and types
            silver.columns = [str(col).strip().lower().replace(' ', '_') for col in silver.columns]
            
            for column in silver.columns:
                lowered = str(column).lower()
                if "date" in lowered or "time" in lowered or "datetime" in lowered:
                    silver[column] = pd.to_datetime(silver[column], errors="coerce")
                elif pd.api.types.is_numeric_dtype(silver[column]):
                    silver[column] = pd.to_numeric(silver[column], errors="coerce")
                else:
                    coerced = pd.to_numeric(silver[column], errors="coerce")
                    if coerced.notna().mean() >= 0.8:
                        silver[column] = coerced
            
            duplicates_before = len(source_frame)
            silver = silver.drop_duplicates().reset_index(drop=True)
            duplicates_removed = duplicates_before - len(silver)
            
            missing_values = int(silver.isna().sum().sum())
            quality_score = round(max(0.0, 100.0 * (1 - (missing_values / max(1, silver.size)))) , 2)
            silver_key = f"jobs/{job['job_id']}/silver/data.parquet"
            silver_metadata = {
                "layer": "silver",
                "job_id": job["job_id"],
                "row_count": int(len(silver)),
                "column_count": int(len(silver.columns)),
                "columns": list(silver.columns),
                "schema": {col: str(dtype) for col, dtype in silver.dtypes.items()},
                "quality_score": quality_score,
                "missing_values": int(missing_values),
                "missing_percentage": round(missing_values / max(1, silver.size) * 100, 2),
                "duplicates_removed": int(duplicates_removed),
                "processing_timestamp": datetime.now().astimezone().isoformat(),
                "source_bronze_object": f"jobs/{job['job_id']}/bronze/raw.csv",
                "object": silver_key,
                "status": "completed",
            }
            _write_minio_parquet(silver_key, silver)
            _write_minio_json(f"jobs/{job['job_id']}/silver/metadata.json", silver_metadata)

            job["stage"] = "silver"
            job["progress"] = 50

            # GOLD: Forecast-ready dataset with proper time series structure (only date + target)
            date_col = payload.effective_date_column.strip().lower().replace(' ', '_')
            target_col = payload.effective_target_column.strip().lower().replace(' ', '_')
            
            # Select only the date and target columns for analytics-ready dataset
            if date_col in silver.columns and target_col in silver.columns:
                gold = silver[[date_col, target_col]].copy()
            else:
                # Fallback if columns not found
                gold = silver.copy()
            
            # Sort by date and ensure no missing values in key columns
            if date_col in gold.columns:
                gold = gold.sort_values(by=date_col).reset_index(drop=True)
            if date_col in gold.columns and target_col in gold.columns:
                gold = gold.dropna(subset=[date_col, target_col]).reset_index(drop=True)
            
            gold_key = f"jobs/{job['job_id']}/gold/data.parquet"
            gold_metadata = {
                "layer": "gold",
                "job_id": job["job_id"],
                "row_count": int(len(gold)),
                "column_count": int(len(gold.columns)),
                "columns": list(gold.columns),
                "schema": {col: str(dtype) for col, dtype in gold.dtypes.items()},
                "features": list(gold.columns),
                "date_column": date_col,
                "target_column": target_col,
                "forecast_ready": True,
                "source_silver_object": silver_key,
                "object": gold_key,
                "processing_timestamp": datetime.now().astimezone().isoformat(),
                "status": "completed",
            }
            _write_minio_parquet(gold_key, gold)
            _write_minio_json(f"jobs/{job['job_id']}/gold/metadata.json", gold_metadata)

            job["stage"] = "gold"
            job["progress"] = 65

            job["stage"] = "training"
            job["progress"] = 80

            forecast = _build_forecast(gold, date_col, target_col, int(payload.effective_horizon))
            forecast_key = f"jobs/{job['job_id']}/forecast/forecast.parquet"
            forecast_metadata_key = f"jobs/{job['job_id']}/forecast/metadata.json"
            forecast_df = pd.DataFrame(forecast["download_rows"])
            if forecast_df.empty or not {"date", "forecast"}.issubset(forecast_df.columns):
                raise ValueError("Forecast output is empty or missing required columns.")
            _write_minio_parquet(forecast_key, forecast_df)
            forecast_object = _verify_minio_object(forecast_key, "application/vnd.apache.parquet")
            forecast_metadata = {
                "layer": "forecast",
                "job_id": job["job_id"],
                "artifact_name": "forecast.parquet",
                "storage": "minio",
                "bucket": MINIO_BUCKET,
                "object_key": forecast_key,
                "object": forecast_key,
                "source_gold_object": gold_key,
                "row_count": int(len(forecast_df)),
                "rows": int(len(forecast_df)),
                "column_count": int(len(forecast_df.columns)),
                "columns": list(forecast_df.columns),
                "file_size": forecast_object["size"],
                "MAE": forecast["metrics"].get("MAE"),
                "RMSE": forecast["metrics"].get("RMSE"),
                "MAPE": forecast["metrics"].get("MAPE"),
                "model_name": "LinearRegression",
                "forecast_horizon": int(payload.effective_horizon),
                "processing_timestamp": datetime.now().astimezone().isoformat(),
                "status": "completed",
            }
            _write_minio_json(forecast_metadata_key, forecast_metadata)
            print(
                "[FORECAST_PERSIST] "
                f"job_id={job['job_id']} stage=forecast artifact=forecast.parquet "
                f"bucket={MINIO_BUCKET} object_key={forecast_key} "
                f"row_count={len(forecast_df)} file_size={forecast_object['size']} status=uploaded"
            )

            manifest = _build_manifest(
                job_id=job["job_id"],
                filename=job["filename"],
                created_at=job["created_at"],
                stages={
                    "bronze": {
                        "status": "completed",
                        "object": f"jobs/{job['job_id']}/bronze/raw.csv",
                        "metadata": f"jobs/{job['job_id']}/bronze/metadata.json",
                    },
                    "silver": {
                        "status": "completed",
                        "object": silver_key,
                        "metadata": f"jobs/{job['job_id']}/silver/metadata.json",
                    },
                    "gold": {
                        "status": "completed",
                        "object": gold_key,
                        "metadata": f"jobs/{job['job_id']}/gold/metadata.json",
                    },
                    "forecast": {
                        "status": "completed",
                        "object": forecast_key,
                        "object_key": forecast_key,
                        "bucket": MINIO_BUCKET,
                        "artifact_name": "forecast.parquet",
                        "metadata": f"jobs/{job['job_id']}/forecast/metadata.json",
                    },
                },
            )
            _write_minio_json(f"jobs/{job['job_id']}/manifest.json", manifest)
            job["manifest"] = manifest

            job["stage"] = "forecasting"
            job["progress"] = 90

            job["result"] = {
                "series": forecast["series"],
                "metrics": forecast["metrics"],
                "rows": forecast["rows"],
            }
            job["download_rows"] = forecast["download_rows"]
            job["status"] = "completed"
            job["stage"] = "completed"
            job["progress"] = 100
        except Exception as exc:  # pragma: no cover - runtime failure path
            job["status"] = "failed"
            job["stage"] = "failed"
            job["progress"] = 100
            job["error"] = str(exc)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    return {
        "job_id": payload.job_id,
        "status": "processing",
        "stage": "bronze",
        "progress": 15,
        "message": "The CSV is being processed through Bronze, Silver, Gold and forecast generation.",
    }


@app.get("/api/forecast/{job_id}/status")
async def get_forecast_status(job_id: str):
    job = _job_or_404(job_id)
    return {
        "job_id": job_id,
        "status": job.get("status", "processing"),
        "stage": job.get("stage", "bronze"),
        "progress": int(job.get("progress", 0)),
        "message": "Processing in progress." if job.get("status") == "processing" else "Forecast completed.",
        "error": job.get("error"),
    }


@app.get("/api/forecast/{job_id}/result")
async def get_forecast_result(job_id: str):
    job = _job_or_404(job_id)
    if job.get("status") != "completed":
        raise HTTPException(status_code=409, detail="Forecast is not complete yet.")
    forecast_key = _get_stage_key(job_id, "forecast")
    try:
        persisted = _read_minio_parquet(forecast_key)
        result_rows = persisted.astype(object).where(pd.notna(persisted), None).to_dict(orient="records")
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Persisted forecast artifact is unavailable: {exc}") from exc
    result = job.get("result") or {}
    return {
        "job_id": job_id,
        "status": "completed",
        "stage": "completed",
        "series": result.get("series", []),
        "metrics": result.get("metrics", {}),
        "rows": result_rows,
    }


@app.get("/api/forecast/{job_id}/download")
async def download_forecast(job_id: str):
    key = _get_stage_key(job_id, "forecast")
    try:
        payload = _read_minio_bytes(key)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"Forecast artifact was not found in MinIO: {exc}") from exc
    return StreamingResponse(
        io.BytesIO(payload),
        media_type="application/vnd.apache.parquet",
        headers={"Content-Disposition": 'attachment; filename="forecast.parquet"'},
    )


@app.get("/api/datasets/jobs")
async def list_dataset_jobs():
    try:
        job_ids = _list_job_ids_from_minio()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Unable to read MinIO datasets: {exc}") from exc

    jobs = []
    for job_id in job_ids:
        manifest_key = f"jobs/{job_id}/manifest.json"
        try:
            manifest_payload = json.loads(_read_minio_bytes(manifest_key).decode("utf-8"))
        except Exception:
            manifest_payload = None
        entry = {
            "job_id": job_id,
            "source_file": manifest_payload.get("source_file") if manifest_payload else JOBS.get(job_id, {}).get("filename"),
            "created_at": manifest_payload.get("created_at") if manifest_payload else JOBS.get(job_id, {}).get("created_at"),
            "status": JOBS.get(job_id, {}).get("status", "completed"),
            "stages": list((manifest_payload or {}).get("stages", {}).keys()) if manifest_payload else ["bronze", "silver", "gold", "forecast"],
        }
        jobs.append(entry)
    return {"jobs": jobs}


@app.get("/api/datasets/{job_id}/preview")
async def preview_dataset(job_id: str, stage: str = "gold", page: int = Query(default=1, ge=1), per_page: int = Query(default=20, ge=1, le=200), search: Optional[str] = None, sort_by: Optional[str] = None, sort_desc: bool = False):
    try:
        key = _get_stage_key(job_id, stage)
        metadata_key = f"jobs/{job_id}/{stage}/metadata.json"
        try:
            metadata = json.loads(_read_minio_bytes(metadata_key).decode("utf-8"))
            key = metadata.get("object_key") or metadata.get("object") or key
        except Exception:
            pass
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        if key.endswith(".csv"):
            frame = _read_minio_csv(key)
        else:
            frame = _read_minio_parquet(key)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"Dataset '{stage}' for job '{job_id}' was not found in MinIO: {exc}") from exc

    return _safe_preview(frame, page=page, per_page=per_page, search=search, sort_by=sort_by, sort_desc=sort_desc)


@app.get("/api/datasets/{job_id}/metadata")
async def get_dataset_metadata(job_id: str, stage: str = "gold"):
    """Get metadata for a specific layer"""
    try:
        _get_stage_key(job_id, stage)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    metadata_key = f"jobs/{job_id}/{stage}/metadata.json"
    try:
        metadata_bytes = _read_minio_bytes(metadata_key)
        metadata = json.loads(metadata_bytes.decode("utf-8"))
        return metadata
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"Metadata for '{stage}' not found: {exc}") from exc


@app.get("/api/datasets/{job_id}/manifest")
async def get_dataset_manifest(job_id: str):
    """Get the complete manifest for a job showing all layers"""
    manifest_key = f"jobs/{job_id}/manifest.json"
    try:
        manifest_bytes = _read_minio_bytes(manifest_key)
        manifest = json.loads(manifest_bytes.decode("utf-8"))
        return manifest
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"Manifest for job '{job_id}' not found: {exc}") from exc

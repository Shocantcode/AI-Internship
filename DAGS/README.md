# MinIO-backed PySpark ETL (mobile sales)

This project demonstrates a PySpark ETL pipeline that reads/writes data from/to MinIO using S3A (`s3a://`) paths.

Files of interest:

- `config/config.py` — MinIO and package configuration (via env vars).
- `utils/spark_utils.py` — `create_spark_session()` and helper to read Excel via pandas.
- `utils/minio_utils.py` — list and download helpers using `boto3`.
- `utils/validation_utils.py` — small validators and reporting.
- `jobs/etl_pipeline.py` — orchestration: `raw_to_bronze`, `bronze_to_silver`, `silver_to_gold`, `run_full_pipeline()`.

Install dependencies:

```bash
pip install -r requirements.txt
```

Environment variables (example):

```bash
export MINIO_ENDPOINT=http://localhost:9000
export MINIO_ACCESS_KEY=minioadmin
export MINIO_SECRET_KEY=minioadmin123
export MINIO_BUCKET=mobilesaledata
```

If you want to write ETL outputs to a different prefix (for example `foto`) instead of the default `Data`, set these env vars:

```bash
export MINIO_INPUT_PREFIX=Data      # where source files live (default)
export MINIO_OUTPUT_PREFIX=foto    # where ETL results will be written (change to 'foto')
```

With `MINIO_OUTPUT_PREFIX=foto` the pipeline will write to:

- `s3a://mobilesaledata/foto/Bronze/`
- `s3a://mobilesaledata/foto/Silver/`
- `s3a://mobilesaledata/foto/Gold/`

This keeps all reads/writes inside MinIO; local disk is used only temporarily for reading `.xlsx` files.

MinIO Console (browser) links (for convenience):

 - Bucket browser: http://localhost:9001/browser/mobilesaledata
 - Bronze folder: http://localhost:9001/browser/mobilesaledata/Data%2FBronze%2F

Note: use `MINIO_CONSOLE` env var to change the console URL if your MinIO UI runs on a different port.

Run pipeline (from project root):

```bash
python jobs/etl_pipeline.py
```

Notes:
- Excel files are read via `boto3` -> `pandas.read_excel()` and converted to Spark DataFrames; temporary local files are used only as buffers.
- All main reads/writes use `s3a://mobilesaledata/...` paths.
- If you run inside a Dockerized Airflow environment, ensure the container has network access to MinIO and the correct environment variables.

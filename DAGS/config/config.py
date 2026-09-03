import os

MINIO_ENDPOINT = os.environ.get('MINIO_ENDPOINT', 'http://localhost:9000')
MINIO_ACCESS_KEY = os.environ.get('MINIO_ACCESS_KEY', 'minioadmin')
MINIO_SECRET_KEY = os.environ.get('MINIO_SECRET_KEY', 'minioadmin123')
MINIO_BUCKET = os.environ.get('MINIO_BUCKET', 'mobilesaledata')

# Allow separate prefixes for input vs output so you can read from one folder
# and write results to another (e.g. write into 'foto' instead of 'Data').
MINIO_INPUT_PREFIX = os.environ.get('MINIO_INPUT_PREFIX', 'Data')
MINIO_OUTPUT_PREFIX = os.environ.get('MINIO_OUTPUT_PREFIX', 'Data')

# Base s3a paths (reads use INPUT_PREFIX, writes use OUTPUT_PREFIX)
RAW_PATH = f"s3a://{MINIO_BUCKET}/{MINIO_INPUT_PREFIX}/Raw/"
BRONZE_PATH = f"s3a://{MINIO_BUCKET}/{MINIO_OUTPUT_PREFIX}/Bronze/"
SILVER_PATH = f"s3a://{MINIO_BUCKET}/{MINIO_OUTPUT_PREFIX}/Silver/"
GOLD_PATH = f"s3a://{MINIO_BUCKET}/{MINIO_OUTPUT_PREFIX}/Gold/"

# JVM packages used by Spark for S3 and Excel support
SPARK_JARS_PACKAGES = [
    'org.apache.hadoop:hadoop-aws:3.3.4',
    'com.amazonaws:aws-java-sdk-bundle:1.11.901',
    'com.crealytics:spark-excel_2.12:0.13.5',
]

# MinIO console (browser) URL - useful for quick access in browser (not S3 API)
MINIO_CONSOLE = os.environ.get('MINIO_CONSOLE', 'http://localhost:9001')
# Browser links for convenience
BRONZE_BROWSER_URL = f"{MINIO_CONSOLE}/browser/{MINIO_BUCKET}/Data%2FBronze%2F"
BUCKET_BROWSER_URL = f"{MINIO_CONSOLE}/browser/{MINIO_BUCKET}"

import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

# Configure MinIO connection and prefixes here (override as needed)
os.environ.setdefault('MINIO_ENDPOINT', 'http://localhost:9000')
os.environ.setdefault('MINIO_ACCESS_KEY', 'minioadmin')
os.environ.setdefault('MINIO_SECRET_KEY', 'minioadmin123')
os.environ.setdefault('MINIO_BUCKET', 'mobilesaledata')
# Read from Data, write to foto
os.environ.setdefault('MINIO_INPUT_PREFIX', 'Data')
os.environ.setdefault('MINIO_OUTPUT_PREFIX', 'foto')

from jobs.etl_pipeline import run_full_pipeline

if __name__ == '__main__':
    run_full_pipeline()

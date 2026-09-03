import boto3
from urllib.parse import urlparse
from config.config import MINIO_ENDPOINT, MINIO_ACCESS_KEY, MINIO_SECRET_KEY, MINIO_BUCKET


def _s3_client():
    # Create a boto3 client pointing at MinIO
    endpoint = MINIO_ENDPOINT
    if endpoint.startswith('http://') or endpoint.startswith('https://'):
        endpoint_url = endpoint
    else:
        endpoint_url = f'http://{endpoint}'

    # Use path-style addressing for MinIO and signature v4
    return boto3.client(
        's3',
        endpoint_url=endpoint_url,
        aws_access_key_id=MINIO_ACCESS_KEY,
        aws_secret_access_key=MINIO_SECRET_KEY,
        config=boto3.session.Config(signature_version='s3v4', s3={'addressing_style': 'path'})
    )


def list_minio_files(prefix: str) -> list:
    """List objects under a given prefix (e.g. 'Data/Raw/') in the configured bucket.
    Returns list of keys (strings).
    """
    s3 = _s3_client()
    paginator = s3.get_paginator('list_objects_v2')
    page_iterator = paginator.paginate(Bucket=MINIO_BUCKET, Prefix=prefix)
    keys = []
    for page in page_iterator:
        for obj in page.get('Contents', []) if page.get('Contents') else []:
            keys.append(obj['Key'])
    return keys


def parse_s3a_path(path: str):
    # expects s3a://bucket/prefix/
    if path.startswith('s3a://'):
        _, rest = path.split('s3a://', 1)
    else:
        rest = path
    parts = rest.split('/', 1)
    bucket = parts[0]
    prefix = parts[1] if len(parts) > 1 else ''
    return bucket, prefix


def download_object_to_bytes(key: str) -> bytes:
    s3 = _s3_client()
    obj = s3.get_object(Bucket=MINIO_BUCKET, Key=key)
    return obj['Body'].read()


def upload_fileobj_to_minio(file_path: str, dest_key: str):
    s3 = _s3_client()
    with open(file_path, 'rb') as f:
        s3.put_object(Bucket=MINIO_BUCKET, Key=dest_key, Body=f)


def upload_directory_to_minio(local_dir: str, dest_prefix: str):
    """Upload all files under local_dir to MinIO under dest_prefix.
    Preserves relative paths. dest_prefix should not start with '/'."""
    import os
    s3 = _s3_client()
    for root, _, files in os.walk(local_dir):
        for fname in files:
            local_path = os.path.join(root, fname)
            rel_path = os.path.relpath(local_path, local_dir).replace('\\', '/')
            key = dest_prefix.rstrip('/') + '/' + rel_path
            with open(local_path, 'rb') as f:
                s3.put_object(Bucket=MINIO_BUCKET, Key=key, Body=f)

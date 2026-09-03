import tempfile
import os
from pyspark.sql import SparkSession
from config.config import MINIO_ENDPOINT, MINIO_ACCESS_KEY, MINIO_SECRET_KEY, SPARK_JARS_PACKAGES


def create_spark_session(app_name: str = 'minio_etl') -> SparkSession:
    packages = ','.join(SPARK_JARS_PACKAGES)
    builder = SparkSession.builder.appName(app_name)
    if packages:
        builder = builder.config('spark.jars.packages', packages)

    # Common S3A settings for MinIO
    spark = builder.getOrCreate()

    # Configure Hadoop S3A settings via spark context hadoop configuration
    hadoop_conf = spark.sparkContext._jsc.hadoopConfiguration()
    endpoint = MINIO_ENDPOINT
    # Accept either with or without scheme
    if endpoint.startswith('http://') or endpoint.startswith('https://'):
        endpoint_host = endpoint
    else:
        endpoint_host = f'http://{endpoint}'

    hadoop_conf.set('fs.s3a.endpoint', endpoint_host.replace('http://', '').replace('https://', ''))
    hadoop_conf.set('fs.s3a.access.key', MINIO_ACCESS_KEY)
    hadoop_conf.set('fs.s3a.secret.key', MINIO_SECRET_KEY)
    hadoop_conf.set('fs.s3a.path.style.access', 'true')
    hadoop_conf.set('fs.s3a.connection.ssl.enabled', 'false' if endpoint_host.startswith('http://') else 'true')
    hadoop_conf.set('fs.s3a.impl', 'org.apache.hadoop.fs.s3a.S3AFileSystem')

    # Optional: reduce socket timeout for faster failure on bad networks
    hadoop_conf.set('fs.s3a.connection.maximum', '1000')
    # Set numeric timeouts (milliseconds) to avoid Hadoop parsing values like "60s"
    hadoop_conf.set('fs.s3a.connection.timeout', '60000')
    hadoop_conf.set('fs.s3a.connection.establish.timeout', '60000')
    hadoop_conf.set('fs.s3a.attempts.maximum', '3')
    hadoop_conf.set('fs.s3a.threads.max', '50')
    # Use simple credentials provider (works with MinIO)
    hadoop_conf.set('fs.s3a.aws.credentials.provider', 'org.apache.hadoop.fs.s3a.SimpleAWSCredentialsProvider')

    return spark


def spark_read_excel_via_pandas(spark, s3_client, bucket: str, key: str):
    """Download an .xlsx object from MinIO via boto3 S3 client and convert to Spark DataFrame.
    This uses a local temporary file only as a short-lived buffer."""
    import pandas as pd
    import io
    from tempfile import NamedTemporaryFile

    obj = s3_client.get_object(Bucket=bucket, Key=key)
    with NamedTemporaryFile(suffix='.xlsx', delete=True) as tmp:
        tmp.write(obj['Body'].read())
        tmp.flush()
        pdf = pd.read_excel(tmp.name)
    return spark.createDataFrame(pdf)

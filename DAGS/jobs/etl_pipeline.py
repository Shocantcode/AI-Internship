import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from pyspark.sql import functions as F
from config.config import RAW_PATH, BRONZE_PATH, SILVER_PATH, GOLD_PATH, MINIO_BUCKET
from utils.spark_utils import create_spark_session, spark_read_excel_via_pandas
from utils.minio_utils import list_minio_files, parse_s3a_path
from utils.validation_utils import validate_dataframe, print_layer_report


def _list_files_under_path(s3a_path: str):
    # returns keys relative to bucket
    bucket, prefix = parse_s3a_path(s3a_path)
    if bucket != MINIO_BUCKET:
        # for simplicity, only listing within configured bucket
        raise ValueError('Listing currently supports configured MINIO_BUCKET only')
    # ensure prefix doesn't start with bucket
    return list_minio_files(prefix)


def read_from_minio(spark, path: str, file_format: str = None):
    """Read all files under `path` in MinIO and return a Spark DataFrame.
    file_format can be 'csv', 'parquet', 'xlsx' or None (auto-detect by extension).
    """
    bucket, prefix = parse_s3a_path(path)
    keys = list_minio_files(prefix)
    if not keys:
        raise FileNotFoundError(f'No objects found under {path}')

    dfs = []
    for key in keys:
        if key.endswith('/'):
            continue
        ext = key.split('.')[-1].lower()
        if file_format:
            f = file_format.lower()
        else:
            f = 'parquet' if ext == 'parquet' else ('xlsx' if ext in ('xlsx', 'xls') else 'csv')

        full_s3a = f's3a://{bucket}/{key}'
        if f == 'csv':
            df = spark.read.option('header', 'true').option('inferSchema', 'true').csv(full_s3a)
            dfs.append(df)
        elif f == 'parquet':
            df = spark.read.parquet(full_s3a)
            dfs.append(df)
        elif f == 'xlsx':
            # use pandas roundtrip via boto3 inside spark_utils
            from utils.minio_utils import _s3_client
            s3 = _s3_client()
            df = spark_read_excel_via_pandas(spark, s3, bucket, key)
            dfs.append(df)
        else:
            raise ValueError(f'Unsupported format: {f} for key {key}')

    # union all dfs
    if not dfs:
        raise ValueError('No dataframes read')
    from functools import reduce
    df_all = reduce(lambda a, b: a.unionByName(b, allowMissingColumns=True), dfs)
    return df_all


def write_to_minio(df, path: str, file_format: str = 'parquet', mode: str = 'overwrite'):
    try:
        if file_format == 'parquet':
            df.write.mode(mode).parquet(path)
        elif file_format == 'csv':
            df.write.mode(mode).option('header', 'true').csv(path)
        else:
            raise ValueError('Unsupported write format: ' + file_format)
    except Exception as e:
        # Fallback: write to local temp dir then upload to MinIO via boto3
        import tempfile
        import shutil
        from utils.minio_utils import upload_directory_to_minio, parse_s3a_path

        print(f'Warning: direct S3A write failed, falling back to local upload. Error: {e}')
        tmpdir = tempfile.mkdtemp()
        try:
            if file_format == 'parquet':
                df.write.mode(mode).parquet(tmpdir)
            elif file_format == 'csv':
                df.write.mode(mode).option('header', 'true').csv(tmpdir)

            # compute destination prefix inside bucket
            bucket, prefix = parse_s3a_path(path)
            dest_prefix = prefix.rstrip('/')
            upload_directory_to_minio(tmpdir, dest_prefix)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


# --- Layer implementations ---

def raw_to_bronze(spark):
    input_path = RAW_PATH
    output_path = BRONZE_PATH
    df = read_from_minio(spark, input_path)

    # standardize column names
    def normalize(col):
        return F.regexp_replace(F.lower(F.trim(F.col(col))), '[^0-9a-z]+', '_')

    # rename columns to normalized names
    new_names = [c for c in df.columns]
    for c in df.columns:
        new_col = c.strip().lower()
        new_col = __import__('re').sub(r'[^0-9a-zA-Z]+', '_', new_col)
        df = df.withColumnRenamed(c, new_col)

    # add metadata
    df = df.withColumn('ingestion_timestamp', F.current_timestamp())
    df = df.withColumn('source_file', F.lit(input_path))

    # basic fills
    df = df.na.fill({'customergender': 'Unknown'})

    write_to_minio(df, output_path, 'parquet', mode='overwrite')
    info = validate_dataframe(df)
    print_layer_report('bronze', input_path, output_path, info)
    return info


def bronze_to_silver(spark):
    input_path = BRONZE_PATH
    output_path = SILVER_PATH
    df = read_from_minio(spark, input_path)

    # cleaning: cast numeric columns if present
    for c in ['price', 'totalrevenue', 'unitssold', 'customerage']:
        if c in df.columns:
            df = df.withColumn(c, F.col(c).cast('double'))

    # date normalization
    if 'date' in df.columns:
        df = df.withColumn('date', F.to_date(F.col('date'), 'yyyy-MM-dd'))

    # remove invalid rows
    required = [c for c in ['transactionid', 'date', 'price', 'totalrevenue'] if c in df.columns]
    for c in required:
        df = df.filter(F.col(c).isNotNull())

    df = df.dropDuplicates()

    write_to_minio(df, output_path, 'parquet', mode='overwrite')
    info = validate_dataframe(df)
    print_layer_report('silver', input_path, output_path, info)
    return info


def silver_to_gold(spark):
    input_path = SILVER_PATH
    output_base = GOLD_PATH
    df = read_from_minio(spark, input_path)

    # summary outputs
    # base
    write_to_minio(df, f'{output_base}/base', 'parquet', mode='overwrite')

    # by model
    if 'mobilemodel' in df.columns:
        model_df = (
            df.groupBy('mobilemodel')
            .agg(F.sum('totalrevenue').alias('total_sales'), F.sum('unitssold').alias('total_quantity'))
            .orderBy(F.desc('total_sales'))
        )
        write_to_minio(model_df, f'{output_base}/product_analysis', 'parquet', mode='overwrite')

    # by location
    if 'location' in df.columns:
        loc_df = (
            df.groupBy('location')
            .agg(F.sum('totalrevenue').alias('total_sales'), F.sum('unitssold').alias('total_quantity'))
            .orderBy(F.desc('total_sales'))
        )
        write_to_minio(loc_df, f'{output_base}/location_analysis', 'parquet', mode='overwrite')

    # time-based
    if 'date' in df.columns:
        time_df = df.withColumn('year', F.year('date')).withColumn('month', F.date_format('date', 'yyyy-MM'))
        write_to_minio(time_df.select('date', 'year', 'month', 'totalrevenue', 'unitssold'), f'{output_base}/time_analysis', 'parquet', mode='overwrite')

        monthly_df = (
            time_df.groupBy('year', 'month')
            .agg(F.sum('totalrevenue').alias('total_sales'), F.sum('unitssold').alias('total_quantity'))
            .orderBy('year', 'month')
        )
        write_to_minio(monthly_df, f'{output_base}/monthly_sales', 'parquet', mode='overwrite')

    # overall summary
    summary_df = df.agg(
        F.sum('totalrevenue').alias('total_sales'),
        F.sum('unitssold').alias('total_quantity'),
        F.avg('totalrevenue').alias('average_sales')
    )
    write_to_minio(summary_df, f'{output_base}/sales_summary', 'parquet', mode='overwrite')

    info = validate_dataframe(df)
    print_layer_report('gold', input_path, output_base, info)
    return info


def run_full_pipeline():
    spark = create_spark_session('mobile_sales_pipeline')
    try:
        print('Starting Raw -> Bronze')
        raw_to_bronze(spark)
        print('Starting Bronze -> Silver')
        bronze_to_silver(spark)
        print('Starting Silver -> Gold')
        silver_to_gold(spark)
    finally:
        spark.stop()


if __name__ == '__main__':
    run_full_pipeline()

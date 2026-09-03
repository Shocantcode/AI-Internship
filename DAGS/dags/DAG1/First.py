import io
import logging
import os
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from airflow import DAG
from airflow.decorators import task
from airflow.utils import timezone
from datetime import timedelta
# PySpark and boto3 are optional at parse time for Airflow. Import lazily inside functions.

# ---------------------------------------------------------------------------
# Fixes applied in this version (2026-08-19):
#   1. S3A "60s" NumberFormatException -> explicit plain-number timeout
#      overrides in get_spark_session(). Confirmed working (this error is gone
#      from the latest run log).
#   2. Raw file uploaded as .xls (not .xlsx/.csv) -> find_raw_object_key() /
#      get_raw_input_path() now also detect .xls, and stage_raw_to_bronze()
#      picks openpyxl vs xlrd based on the actual extension.
#      Needs `pip install xlrd` in the Airflow image for .xls support.
#   3. Latent bug (hadn't been hit yet, found by code review): numeric_casts
#      used the key 'unitsold' while every stage_silver_to_gold() aggregation
#      used 'unitssold' -> would have crashed silver_to_gold with
#      "cannot resolve column" once the pipeline got that far. Both now
#      resolve through COLUMN_ALIASES onto one canonical name, and gold
#      aggregations degrade gracefully (skip total_quantity, keep total_sales)
#      if no known alias is found at all, instead of hard-crashing.
#   Also wired up find_excel_sheet_name() / find_excel_sheet_name_from_bytes(),
#   which existed in the file but were never actually called -> .xlsx reads
#   now use the detected sheet instead of silently defaulting to sheet 0.
# ---------------------------------------------------------------------------

LOG = logging.getLogger(__name__)
LOG.setLevel(logging.INFO)

DATA_DIR = Path(os.environ.get('DATA_DIR', '/opt/airflow/Data'))
BASE_DIR = Path(__file__).resolve().parents[2]
if not DATA_DIR.exists():
    DATA_DIR = BASE_DIR / 'Data'

RAW_DIR = DATA_DIR / 'Raw'
BRONZE_DIR = DATA_DIR / 'Bronze'
SILVER_DIR = DATA_DIR / 'Silver'
GOLD_DIR = DATA_DIR / 'Gold'
RAW_XLSX = RAW_DIR / 'Mobile Sales Data.xlsx'
RAW_CSV = RAW_DIR / 'mobile_sales.csv'

# --- MinIO / S3 configuration ---
# USE_MINIO defaults to true now: this DAG reads Raw and writes Bronze/Silver/Gold
# to a MinIO bucket instead of local disk. Set USE_MINIO=false to fall back to local files.
USE_MINIO = os.environ.get('USE_MINIO', 'true').strip().lower() == 'true'
MINIO_ENDPOINT = os.environ.get('MINIO_ENDPOINT', 'http://minio:9000')
MINIO_ACCESS_KEY = os.environ.get('MINIO_ACCESS_KEY', 'minioadmin')
MINIO_SECRET_KEY = os.environ.get('MINIO_SECRET_KEY', 'minioadmin123')
# Bucket name matches the bucket you already created in the MinIO console.
MINIO_BUCKET = os.environ.get('MINIO_BUCKET', 'mobilesaledata')
# Your bucket has a top-level "Data" folder containing Raw/Bronze/Silver/Gold,
# matching what you showed in the Object Browser screenshot.
MINIO_PREFIX = os.environ.get('MINIO_PREFIX', 'Data').strip('/')

# Avoid the Spark Excel Maven plugin. It is fragile with PySpark 4.x and causes Java gateway startup
# failures during package resolution. We read Excel files with pandas/openpyxl (.xlsx) or
# pandas/xlrd (.xls) instead.
EXCEL_CONNECTOR_PACKAGES = []
MINIO_PACKAGES = [
    'org.apache.hadoop:hadoop-aws:3.3.4',
    'com.amazonaws:aws-java-sdk-bundle:1.12.262',
]
MINIO_JARS = [
    '/opt/spark-extra-jars/hadoop-aws-3.3.4.jar',
    '/opt/spark-extra-jars/aws-java-sdk-bundle-1.12.262.jar',
    '/opt/spark-extra-jars/wildfly-openssl-1.0.7.Final.jar',
]

# Some source headers ("Units Sold", "UnitsSold", "unit_sold", "qty", ...) all describe the
# same logical field but normalize to different column names. clean_bronze_dataframe() folds
# any of these aliases onto ONE canonical name so silver/gold code can rely on a single
# spelling instead of guessing. (A mismatch here -- 'unitsold' vs 'unitssold' -- was a real
# internal inconsistency in this file; see the changelog comment above.)
COLUMN_ALIASES = {
    'unitssold': [
        'unitssold', 'unitsold', 'units_sold', 'unit_sold',
        'qty', 'quantity', 'quantitysold', 'quantity_sold',
    ],
}


def normalize_column_name(name: str) -> str:
    cleaned = re.sub(r'[^0-9a-zA-Z]+', '_', name.strip().lower())
    return re.sub(r'_+', '_', cleaned).strip('_')


def sanitize_numeric_string(value):
    """Normalize non-numeric noise like '60s', '1,200.50', 'N/A' to values Spark can cast."""
    if value is None:
        return None

    text = str(value).strip()
    if text == '':
        return None

    normalized = text.upper()
    if normalized in {'NULL', 'N/A', 'NA', 'NONE', 'UNKNOWN'}:
        return None

    normalized = text.replace(',', '').replace(' ', '')
    normalized = re.sub(r'(?i)([0-9]+(?:\.[0-9]+)?)(?:[a-zA-Z]+)$', r'\1', normalized)
    if normalized in {'', 'NAN', 'INF', '-INF'}:
        return None
    return normalized


def get_spark_session(use_minio: bool, need_excel: bool):
    from pyspark.sql import SparkSession

    builder = SparkSession.builder.appName('mobile_sales_etl')
    packages = []
    if use_minio:
        packages.extend(MINIO_PACKAGES)
    if need_excel:
        packages.extend(EXCEL_CONNECTOR_PACKAGES)
    if packages:
        builder = builder.config('spark.jars', ','.join(MINIO_JARS))

    if use_minio:
        endpoint = MINIO_ENDPOINT
        host = endpoint.replace('http://', '').replace('https://', '')
        builder = builder.config('spark.hadoop.fs.s3a.impl', 'org.apache.hadoop.fs.s3a.S3AFileSystem')
        builder = builder.config('spark.hadoop.fs.s3a.access.key', MINIO_ACCESS_KEY)
        builder = builder.config('spark.hadoop.fs.s3a.secret.key', MINIO_SECRET_KEY)
        builder = builder.config('spark.hadoop.fs.s3a.endpoint', host)
        builder = builder.config('spark.hadoop.fs.s3a.path.style.access', 'true')
        builder = builder.config('spark.hadoop.fs.s3a.connection.ssl.enabled', 'false' if endpoint.startswith('http://') else 'true')
        builder = builder.config('spark.hadoop.impl.disable.cache', 'true')
        builder = builder.config(
            'spark.hadoop.fs.s3a.aws.credentials.provider',
            'org.apache.hadoop.fs.s3a.SimpleAWSCredentialsProvider',
        )
        # --- Fix for: java.lang.NumberFormatException: For input string: "60s" ---
        # The hadoop-common jar bundled with newer PySpark ships core-default.xml with a few
        # S3A timeout defaults written in suffixed duration form (e.g. "60s"). hadoop-aws:3.3.4
        # (pinned above) still parses these via Configuration.getLong() inside
        # S3AFileSystem.initThreadPools(), which can't handle the "s" suffix and throws
        # NumberFormatException before any data is even read. Overriding them here with plain
        # millisecond integers avoids the crash. (Confirmed fixed against the real error log.)
        builder = builder.config('spark.hadoop.fs.s3a.connection.timeout', '200000')
        builder = builder.config('spark.hadoop.fs.s3a.connection.establish.timeout', '5000')
        builder = builder.config('spark.hadoop.fs.s3a.connection.request.timeout', '60000')
        builder = builder.config('spark.hadoop.fs.s3a.connection.acquisition.timeout', '60000')

    spark = builder.getOrCreate()
    if use_minio:
        # hadoop-aws:3.3.4 reads these pool values with getLong(), while newer
        # Hadoop defaults may use duration strings such as "60s".
        hadoop_conf = spark.sparkContext._jsc.hadoopConfiguration()
        s3a_numeric_settings = {
            'fs.s3a.connection.timeout': '200000',
            'fs.s3a.connection.establish.timeout': '5000',
            'fs.s3a.connection.request.timeout': '60000',
            'fs.s3a.connection.acquisition.timeout': '60000',
            'fs.s3a.connection.idle.time': '60000',
            'fs.s3a.connection.ttl': '300000',
            'fs.s3a.threads.keepalivetime': '60000',
            'fs.s3a.multipart.purge.age': '86400000',
        }
        for key, value in s3a_numeric_settings.items():
            hadoop_conf.set(key, value)
    spark.sparkContext.setLogLevel('WARN')
    return spark


def get_s3_client():
    """Plain boto3 client pointed at MinIO, used only for lightweight lookups
    (listing the Raw folder, peeking at an xlsx file to find its sheet name).
    The actual big reads/writes happen in Spark via the s3a:// filesystem."""
    import boto3

    return boto3.client(
        's3',
        endpoint_url=MINIO_ENDPOINT,
        aws_access_key_id=MINIO_ACCESS_KEY,
        aws_secret_access_key=MINIO_SECRET_KEY,
        config=boto3.session.Config(
            signature_version='s3v4',
            s3={'addressing_style': 'path'},
        ),
    )


def find_raw_object_key() -> str:
    """Look inside <bucket>/<prefix>/Raw/ on MinIO for an .xlsx, .xls, or .csv file
    and return its object key. Prefers .xlsx, then .xls, then .csv."""
    client = get_s3_client()
    prefix = f'{MINIO_PREFIX}/Raw/'
    response = client.list_objects_v2(Bucket=MINIO_BUCKET, Prefix=prefix)
    contents = response.get('Contents', [])

    xlsx_key = None
    xls_key = None
    csv_key = None
    for obj in contents:
        key = obj['Key']
        lower = key.lower()
        if lower.endswith('.xlsx') and xlsx_key is None:
            xlsx_key = key
        elif lower.endswith('.xls') and xls_key is None:
            xls_key = key
        elif lower.endswith('.csv') and csv_key is None:
            csv_key = key

    if xlsx_key:
        return xlsx_key
    if xls_key:
        return xls_key
    if csv_key:
        return csv_key
    raise FileNotFoundError(
        f'Tidak ditemukan file .xlsx, .xls, atau .csv di s3://{MINIO_BUCKET}/{prefix}. '
        'Upload dulu file raw-nya ke folder Raw lewat MinIO console.'
    )


def find_excel_sheet_name_from_bytes(file_bytes: bytes) -> str:
    """Only valid for .xlsx (OOXML/zip format). Do not call this on .xls bytes,
    the old binary .xls format is not a zip archive and zipfile will raise."""
    with zipfile.ZipFile(io.BytesIO(file_bytes), 'r') as archive:
        workbook_xml = archive.read('xl/workbook.xml')
        workbook = ET.fromstring(workbook_xml)
        ns = {'x': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        sheets = workbook.findall('.//x:sheets/x:sheet', ns)
        for sheet in sheets:
            name = sheet.attrib.get('name', '')
            if 'mobile' in name.lower() or 'sales' in name.lower():
                return name
        if sheets:
            return sheets[0].attrib.get('name', '')
    return 'Sheet1'


def find_excel_sheet_name(raw_path: Path) -> str:
    """Local-disk fallback, only used when USE_MINIO=false. Only valid for .xlsx."""
    if not raw_path.exists():
        raise FileNotFoundError(f'Excel file not found: {raw_path}')
    with zipfile.ZipFile(raw_path, 'r') as archive:
        workbook_xml = archive.read('xl/workbook.xml')
        workbook = ET.fromstring(workbook_xml)
        ns = {'x': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        sheets = workbook.findall('.//x:sheets/x:sheet', ns)
        for sheet in sheets:
            name = sheet.attrib.get('name', '')
            if 'mobile' in name.lower() or 'sales' in name.lower():
                return name
        if sheets:
            return sheets[0].attrib.get('name', '')
    return 'Sheet1'


def get_raw_input_path():
    """Returns (path_or_uri, extension) where extension is 'xlsx', 'xls', or 'csv'.
    When USE_MINIO=true, looks up the raw file inside the MinIO bucket and
    returns an s3a:// URI that Spark can read directly.
    Otherwise falls back to local files under Data/Raw."""
    if USE_MINIO:
        key = find_raw_object_key()
        uri = f's3a://{MINIO_BUCKET}/{key}'
        ext = key.lower().rsplit('.', 1)[-1]
        return uri, ext

    if RAW_XLSX.exists():
        return str(RAW_XLSX), 'xlsx'
    if RAW_CSV.exists():
        return str(RAW_CSV), 'csv'
    raise FileNotFoundError('Tidak ditemukan input raw di Data/Raw. Pastikan Mobile Sales Data.xlsx atau mobile_sales.csv tersedia.')


def get_layer_path(layer: str) -> str:
    """Bronze/Silver/Gold output location.
    On MinIO this maps to s3a://<bucket>/<prefix>/<Layer>/Mobile_Sales_Data,
    matching the Raw/Bronze/Silver/Gold folders you already have in the bucket."""
    if USE_MINIO:
        return f's3a://{MINIO_BUCKET}/{MINIO_PREFIX}/{layer.capitalize()}/Mobile_Sales_Data'
    target = {
        'raw': RAW_DIR,
        'bronze': BRONZE_DIR,
        'silver': SILVER_DIR,
        'gold': GOLD_DIR,
    }.get(layer)
    if target is None:
        raise ValueError(f'Unknown layer: {layer}')
    return str(target / 'Mobile_Sales_Data')


def log_dataframe_info(stage: str, df, message: str = '') -> None:
    LOG.info('=== %s ===', stage)
    LOG.info('message: %s', message)
    LOG.info('schema: %s', [f"{field.name}:{field.dataType.simpleString()}" for field in df.schema.fields])
    LOG.info('record_count: %s', df.count())


def clean_bronze_dataframe(df):
    from pyspark.sql import functions as F
    from pyspark.sql.types import DateType, DoubleType, IntegerType, StringType, TimestampType

    renamed = {col_name: normalize_column_name(col_name) for col_name in df.columns}
    df = df.select([F.col(old).alias(new) for old, new in renamed.items()])

    # Fold known alias spellings (see COLUMN_ALIASES) onto one canonical column name,
    # so downstream casts/filters/aggregations only ever have to know one spelling.
    for canonical, aliases in COLUMN_ALIASES.items():
        if canonical in df.columns:
            continue
        for alias in aliases:
            if alias in df.columns:
                df = df.withColumnRenamed(alias, canonical)
                break

    string_columns = [field.name for field in df.schema.fields if isinstance(field.dataType, StringType)]

    for column_name in string_columns:
        df = df.withColumn(
            column_name,
            F.trim(F.col(column_name))
        )
        df = df.withColumn(
            column_name,
            F.when(F.col(column_name).isin('', 'NULL', 'null'), None)
            .otherwise(F.regexp_replace(F.col(column_name), r'[\t\n\r]+', ' '))
        )

    numeric_casts = {
        'price': DoubleType(),
        'totalrevenue': DoubleType(),
        'unitssold': IntegerType(),
        'customerage': IntegerType(),
    }
    for column_name, dtype in numeric_casts.items():
        if column_name in df.columns:
            df = df.withColumn(column_name, F.col(column_name).cast(dtype))

    if 'date' in df.columns:
        date_type = next(field.dataType for field in df.schema.fields if field.name == 'date')
        if isinstance(date_type, TimestampType):
            df = df.withColumn('date', F.to_date(F.col('date')))
        elif isinstance(date_type, DateType):
            df = df.withColumn('date', F.col('date'))
        else:
            df = df.withColumn(
                'date',
                F.coalesce(
                    F.to_date(F.col('date'), 'yyyy-MM-dd'),
                    F.to_date(F.col('date'), 'yyyy-MM-dd HH:mm:ss'),
                )
            )

    gender_col = normalize_column_name('customer_gender')
    if gender_col in df.columns:
        df = df.na.fill({gender_col: 'Unknown'})
    df = df.dropDuplicates()
    return df


def data_quality_report(df):
    from pyspark.sql import functions as F

    null_counts = {field.name: df.filter(F.col(field.name).isNull()).count() for field in df.schema.fields}
    duplicate_count = max(0, df.count() - df.dropDuplicates().count())
    return {
        'null_counts': null_counts,
        'duplicate_count': duplicate_count,
    }


def write_dataframe(df, path: str, format: str = 'parquet', mode: str = 'overwrite', **options):
    if format == 'csv':
        writer = df.write.mode(mode).option('header', 'true')
        for name, value in options.items():
            writer = writer.option(name, value)
        writer.csv(path)
    else:
        writer = df.write.mode(mode)
        for name, value in options.items():
            writer = writer.option(name, value)
        writer.parquet(path)


def read_spark_table(spark, path: str, format: str = 'csv'):
    if format == 'csv':
        return spark.read.option('header', 'true').option('inferSchema', 'true').csv(path)
    return spark.read.parquet(path)


def stage_raw_to_bronze():
    spark = None
    try:
        source_path, ext = get_raw_input_path()
        use_excel = ext in ('xlsx', 'xls')
        spark = get_spark_session(USE_MINIO, use_excel)

        from pyspark.sql import functions as F

        LOG.info('Reading RAW source: %s (ext=%s)', source_path, ext)
        if use_excel:
            import pandas as pd
            excel_engine = 'openpyxl' if ext == 'xlsx' else 'xlrd'
            if USE_MINIO:
                client = get_s3_client()
                key = source_path.split(f's3a://{MINIO_BUCKET}/', 1)[1]
                obj = client.get_object(Bucket=MINIO_BUCKET, Key=key)
                file_bytes = obj['Body'].read()
                sheet_name = find_excel_sheet_name_from_bytes(file_bytes) if ext == 'xlsx' else 0
                pdf = pd.read_excel(io.BytesIO(file_bytes), engine=excel_engine, sheet_name=sheet_name)
            else:
                sheet_name = find_excel_sheet_name(Path(source_path)) if ext == 'xlsx' else 0
                pdf = pd.read_excel(source_path, engine=excel_engine, sheet_name=sheet_name)
            raw_df = spark.createDataFrame(pdf)
        else:
            raw_df = spark.read.option('header', 'true').option('inferSchema', 'false').csv(source_path)
            candidate_columns = {
                'price': 'double',
                'totalrevenue': 'double',
                'unitsold': 'int',
                'unitssold': 'int',
                'units_sold': 'int',
                'customerage': 'int',
            }
            for target_name, dtype in candidate_columns.items():
                actual_name = next((c for c in raw_df.columns if c.lower() == target_name.lower()), None)
                if actual_name is None:
                    continue

                numeric_text = F.trim(F.col(actual_name).cast('string'))
                numeric_text = F.when(
                    numeric_text.isNull()
                    | F.upper(numeric_text).isin('NULL', 'N/A', 'NA', 'NONE', 'UNKNOWN'),
                    None,
                ).otherwise(numeric_text)
                numeric_text = F.regexp_replace(numeric_text, r'[, ]', '')
                numeric_text = F.regexp_replace(
                    numeric_text,
                    r'(?i)^([0-9]+(?:\.[0-9]+)?)[a-zA-Z]+$',
                    r'\1',
                )
                raw_df = raw_df.withColumn(
                    actual_name,
                    numeric_text,
                )
                raw_df = raw_df.withColumn(actual_name, F.col(actual_name).cast(dtype))

        log_dataframe_info('RAW', raw_df, f'raw_source={source_path}')
        bronze_df = raw_df.withColumn('source_file', F.lit(Path(source_path).name))
        bronze_df = bronze_df.withColumn('ingestion_timestamp', F.current_timestamp())

        output_path = get_layer_path('bronze')
        LOG.info('Writing BRONZE to %s', output_path)
        write_dataframe(bronze_df, output_path, format='csv')
        LOG.info('BRONZE write complete: %s', output_path)
    except Exception:
        LOG.exception('FAIL raw_to_bronze')
        raise
    finally:
        if spark is not None:
            spark.stop()


def stage_bronze_to_silver():
    spark = None
    try:
        spark = get_spark_session(USE_MINIO, False)
        bronze_path = get_layer_path('bronze')
        LOG.info('Reading BRONZE from %s', bronze_path)
        bronze_df = read_spark_table(spark, bronze_path, format='csv')
        input_count = bronze_df.count()
        log_dataframe_info('BRONZE', bronze_df, 'before_cleaning')

        from pyspark.sql import functions as F

        duplicate_count = bronze_df.count() - bronze_df.dropDuplicates().count()
        cleaned = clean_bronze_dataframe(bronze_df)
        LOG.info('Normalized SILVER columns: %s', cleaned.columns)

        required_columns = ['transactionid', 'date', 'price', 'totalrevenue']
        missing_required = [c for c in required_columns if c not in cleaned.columns]
        if missing_required:
            raise ValueError(
                f'Kolom wajib tidak ditemukan setelah normalisasi nama kolom: {missing_required}. '
                f'Kolom yang tersedia: {cleaned.columns}. '
                'Cek nama header di file raw dan sesuaikan normalize_column_name/COLUMN_ALIASES kalau perlu.'
            )

        quality = data_quality_report(cleaned)
        LOG.info('BRONZE quality report: %s', quality)
        LOG.info('BRONZE duplicate_count=%s', duplicate_count)

        invalid_df = cleaned.filter(
            F.col('transactionid').isNull()
            | F.col('date').isNull()
            | F.col('price').isNull()
            | F.col('totalrevenue').isNull()
        )
        invalid_count = invalid_df.count()
        LOG.info('BRONZE invalid_records=%s', invalid_count)

        cleaned = cleaned.filter(
            F.col('transactionid').isNotNull()
            & F.col('date').isNotNull()
            & F.col('price').isNotNull()
            & F.col('totalrevenue').isNotNull()
        )

        silver_path = get_layer_path('silver')
        LOG.info('Writing SILVER to %s', silver_path)
        write_dataframe(cleaned, silver_path, format='parquet')
        output_count = cleaned.count()
        LOG.info('SILVER records: input=%s output=%s', input_count, output_count)
    except Exception:
        LOG.exception('FAIL bronze_to_silver')
        raise
    finally:
        if spark is not None:
            spark.stop()


def stage_silver_to_gold():
    spark = None
    try:
        spark = get_spark_session(USE_MINIO, False)
        silver_path = get_layer_path('silver')
        LOG.info('Reading SILVER from %s', silver_path)
        silver_df = spark.read.parquet(silver_path)
        input_count = silver_df.count()
        log_dataframe_info('SILVER', silver_df, 'analytics_source')

        base_gold_path = get_layer_path('gold')
        base_output_path = f'{base_gold_path}/base'
        LOG.info('Writing GOLD base dataset to %s', base_output_path)
        write_dataframe(silver_df, base_output_path, format='parquet')

        from pyspark.sql import functions as F

        # qty_col is resolved defensively: clean_bronze_dataframe() should already have
        # canonicalized it to 'unitssold' via COLUMN_ALIASES, but if the real source header
        # didn't match any known alias, degrade gracefully (skip total_quantity) instead of
        # crashing every gold aggregation below.
        qty_col = 'unitssold' if 'unitssold' in silver_df.columns else None
        if qty_col is None:
            LOG.warning(
                'Quantity column not found in SILVER (checked alias: unitssold); '
                'total_quantity aggregates will be skipped. Available columns: %s',
                silver_df.columns,
            )

        def sales_agg_exprs():
            exprs = [F.sum('totalrevenue').alias('total_sales')]
            if qty_col:
                exprs.append(F.sum(qty_col).alias('total_quantity'))
            return exprs

        if 'mobile_model' in silver_df.columns:
            product_path = f'{base_gold_path}/product_analysis'
            LOG.info('Writing GOLD product analysis to %s', product_path)
            product_df = (
                silver_df.groupBy('mobile_model')
                .agg(*sales_agg_exprs())
                .orderBy(F.desc('total_sales'))
            )
            write_dataframe(product_df, product_path, format='parquet')

        if 'location' in silver_df.columns:
            location_path = f'{base_gold_path}/location_analysis'
            LOG.info('Writing GOLD location analysis to %s', location_path)
            location_df = (
                silver_df.groupBy('location')
                .agg(*sales_agg_exprs())
                .orderBy(F.desc('total_sales'))
            )
            write_dataframe(location_df, location_path, format='parquet')

        if 'date' in silver_df.columns:
            time_df = silver_df.withColumn('year', F.year('date')).withColumn('month', F.date_format('date', 'yyyy-MM'))
            time_path = f'{base_gold_path}/time_analysis'
            LOG.info('Writing GOLD time analysis to %s', time_path)
            daily_df = (
                time_df.groupBy('date', 'year', 'month')
                .agg(*sales_agg_exprs())
                .orderBy('date')
            )
            write_dataframe(
                daily_df,
                time_path,
                format='parquet',
            )

            monthly_path = f'{base_gold_path}/monthly_sales'
            LOG.info('Writing GOLD monthly sales to %s', monthly_path)
            monthly_df = (
                time_df.groupBy('year', 'month')
                .agg(*sales_agg_exprs())
                .orderBy('year', 'month')
            )
            write_dataframe(monthly_df, monthly_path, format='parquet')

        summary_path = f'{base_gold_path}/sales_summary'
        LOG.info('Writing GOLD sales summary to %s', summary_path)
        summary_exprs = [
            F.sum('totalrevenue').alias('total_sales'),
            F.avg('totalrevenue').alias('average_sales'),
        ]
        if qty_col:
            summary_exprs.append(F.sum(qty_col).alias('total_quantity'))
        summary_df = silver_df.agg(*summary_exprs)
        write_dataframe(summary_df, summary_path, format='parquet')

        output_count = silver_df.count()
        LOG.info('GOLD records: input=%s output=%s', input_count, output_count)
    except Exception:
        LOG.exception('FAIL silver_to_gold')
        raise
    finally:
        if spark is not None:
            spark.stop()


def stage_forecasting():
    from Forecasting.forecasting import run_forecasting

    return run_forecasting(int(os.environ.get('FORECAST_PERIOD', '7')))


def create_dag() -> DAG:
    default_args = {
        'owner': 'airflow',
        'depends_on_past': False,
        'retries': 1,
    }
    dag = DAG(
        dag_id='mobile_sales_medallion_etl',
        default_args=default_args,
        start_date=timezone.utcnow() - timedelta(days=1),
        schedule='@daily',
        catchup=False,
        tags=['spark', 'etl', 'medallion', 'minio'],
    )

    with dag:
        raw_to_bronze = task(task_id='raw_to_bronze')(stage_raw_to_bronze)
        bronze_to_silver = task(task_id='bronze_to_silver')(stage_bronze_to_silver)
        silver_to_gold = task(task_id='silver_to_gold')(stage_silver_to_gold)
        forecasting = task(task_id='forecasting')(stage_forecasting)

        raw_to_bronze() >> bronze_to_silver() >> silver_to_gold() >> forecasting()

    return dag


dag = create_dag()
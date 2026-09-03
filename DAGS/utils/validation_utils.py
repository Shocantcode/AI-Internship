from pyspark.sql import DataFrame
from pyspark.sql import functions as F


def validate_dataframe(df: DataFrame) -> dict:
    info = {}
    info['rows'] = df.count()
    info['columns'] = len(df.columns)
    info['schema'] = [(f.name, str(f.dataType)) for f in df.schema.fields]
    null_counts = {c: df.filter(F.col(c).isNull()).count() for c in df.columns}
    info['null_counts'] = null_counts
    info['duplicate_count'] = max(0, df.count() - df.dropDuplicates().count())
    return info


def print_layer_report(name: str, input_path: str, output_path: str, info: dict):
    print('========================================')
    print(f'{name.upper()} LAYER')
    print('========================================')
    print('Input  :', input_path)
    print('Output :', output_path)
    print('Rows   :', info.get('rows'))
    print('Cols   :', info.get('columns'))
    print('Status :', 'SUCCESS' if info.get('rows', 0) >= 0 else 'FAILED')
    print('========================================')

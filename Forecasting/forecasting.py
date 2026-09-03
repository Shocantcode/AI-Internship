"""Forecast Gold/time_analysis data with Prophet and a lag regression baseline."""

import io
import json
import logging
import os

import numpy as np
import pandas as pd

LOG = logging.getLogger(__name__)


def _s3_client():
    import boto3

    return boto3.client(
        's3', endpoint_url=os.environ.get('MINIO_ENDPOINT', 'http://minio:9000'),
        aws_access_key_id=os.environ.get('MINIO_ACCESS_KEY', 'minioadmin'),
        aws_secret_access_key=os.environ.get('MINIO_SECRET_KEY', 'minioadmin123'),
        config=boto3.session.Config(signature_version='s3v4', s3={'addressing_style': 'path'}),
    )


def _prefix(suffix=''):
    root = os.environ.get('MINIO_PREFIX', 'Data').strip('/')
    return f'{root}/Gold/Mobile_Sales_Data/{suffix}'.rstrip('/')


def read_time_analysis(client=None):
    import pyarrow.parquet as pq

    client = client or _s3_client()
    bucket = os.environ.get('MINIO_BUCKET', 'mobilesaledata')
    source = f'{_prefix("time_analysis")}/'
    keys = [item['Key'] for item in client.list_objects_v2(Bucket=bucket, Prefix=source).get('Contents', []) if item['Key'].lower().endswith('.parquet')]
    if not keys:
        raise FileNotFoundError(f'No Parquet files found under s3://{bucket}/{source}')
    return pd.concat([pq.read_table(io.BytesIO(client.get_object(Bucket=bucket, Key=key)['Body'].read())).to_pandas() for key in keys], ignore_index=True)


def profile_dataframe(dataframe):
    profile = {
        'columns': list(dataframe.columns),
        'dtypes': {column: str(dtype) for column, dtype in dataframe.dtypes.items()},
        'record_count': int(len(dataframe)),
        'missing_values': {column: int(value) for column, value in dataframe.isna().sum().items()},
        'duplicate_rows': int(dataframe.duplicated().sum()),
        'numeric_columns': list(dataframe.select_dtypes(include='number').columns),
    }
    candidates = list(dataframe.select_dtypes(include=['datetime', 'datetimetz']).columns)
    candidates += [column for column in dataframe.columns if str(column).lower() in {'date', 'ds', 'timestamp', 'datetime'} and column not in candidates]
    profile['datetime_candidates'] = candidates
    if candidates:
        dates = pd.to_datetime(dataframe[candidates[0]], errors='coerce').dropna()
        profile['date_range'] = [dates.min().strftime('%Y-%m-%d'), dates.max().strftime('%Y-%m-%d')]
    else:
        profile['date_range'] = None
    return profile


def choose_columns(dataframe):
    profile = profile_dataframe(dataframe)
    scores = {}
    for column in dataframe.columns:
        parsed = pd.to_datetime(dataframe[column], errors='coerce')
        scores[column] = (2 if parsed.notna().mean() > .8 else 0) + (3 if str(column).lower() in {'date', 'ds', 'timestamp', 'datetime'} else 0)
    date_column = max(scores, key=scores.get)
    if scores[date_column] < 2:
        raise ValueError(f'No usable date/time column found. Schema: {profile["dtypes"]}')
    numeric = [column for column in dataframe.select_dtypes(include='number').columns if str(column).lower() != 'year']
    if not numeric:
        raise ValueError(f'No numeric metric column found. Schema: {profile["dtypes"]}')
    def metric_score(column):
        name = str(column).lower()
        return (10 if any(word in name for word in ('sales', 'revenue', 'amount', 'value')) else 0) + (3 if any(word in name for word in ('quantity', 'units', 'count')) else 0)
    value_column = max(numeric, key=lambda column: (metric_score(column), dataframe[column].notna().sum()))
    LOG.info('Schema selected ds=%s y=%s; numeric alternatives=%s. Sales/revenue is preferred because it directly measures sales value.', date_column, value_column, numeric)
    return date_column, value_column, profile


def prepare_series(dataframe, date_column, value_column):
    series = dataframe[[date_column, value_column]].copy()
    series.columns = ['ds', 'y']
    series['ds'] = pd.to_datetime(series['ds'], errors='coerce').dt.normalize()
    series['y'] = pd.to_numeric(series['y'], errors='coerce')
    before = len(series)
    series = series.dropna(subset=['ds', 'y'])
    duplicate_dates = int(series['ds'].duplicated().sum())
    series = series.groupby('ds', as_index=False)['y'].sum().sort_values('ds')
    dense = series.set_index('ds').asfreq('D')
    missing_dates = int(dense['y'].isna().sum())
    if missing_dates:
        LOG.warning('Interpolating %s missing daily dates for regular lag features.', missing_dates)
        dense['y'] = dense['y'].interpolate(method='time').ffill().bfill()
    if len(dense) < 30:
        raise ValueError(f'At least 30 usable periods are required; found {len(dense)}.')
    LOG.info('Prepared %s daily periods; dropped_rows=%s duplicate_dates=%s missing_dates=%s.', len(dense), before - len(series), duplicate_dates, missing_dates)
    return dense.reset_index(), {'duplicate_dates': duplicate_dates, 'missing_dates': missing_dates}


def describe_time_series(history):
    values = history.set_index('ds')['y']
    time_index = np.arange(len(values), dtype=float)
    slope = float(np.polyfit(time_index, values.to_numpy(), 1)[0]) if len(values) > 1 else 0.0
    weekday = history.assign(day_of_week=history.ds.dt.day_name()).groupby('day_of_week', sort=False).y.mean()
    monthly = history.assign(month=history.ds.dt.strftime('%Y-%m')).groupby('month', sort=True).y.mean()
    quartiles = values.quantile([.25, .75])
    spread = float(quartiles.loc[.75] - quartiles.loc[.25])
    outliers = int(((values < quartiles.loc[.25] - 1.5 * spread) | (values > quartiles.loc[.75] + 1.5 * spread)).sum()) if spread else 0
    return {
        'trend_slope_per_day': slope,
        'trend_direction': 'increasing' if slope > 0 else 'decreasing' if slope < 0 else 'flat',
        'weekday_mean': {str(key): float(value) for key, value in weekday.items()},
        'monthly_mean': {str(key): float(value) for key, value in monthly.items()},
        'outlier_count_iqr': outliers,
        'seasonality': {'weekly_enabled': len(history) >= 14, 'yearly_enabled': False, 'daily_enabled': False},
    }


def _metrics(actual, predicted):
    actual, predicted = np.asarray(actual, dtype=float), np.asarray(predicted, dtype=float)
    error = actual - predicted
    nonzero = actual != 0
    return {'MAE': float(np.mean(np.abs(error))), 'RMSE': float(np.sqrt(np.mean(error ** 2))), 'MAPE': float(np.mean(np.abs(error[nonzero] / actual[nonzero])) * 100) if nonzero.any() else None}


def _prophet_forecast(history, periods):
    try:
        from prophet import Prophet
    except ImportError as error:
        raise RuntimeError('Prophet is required. Install dependencies from requirements.txt and rebuild Airflow.') from error
    model = Prophet(weekly_seasonality=True, yearly_seasonality=False, daily_seasonality=False)
    model.fit(history[['ds', 'y']])
    result = model.predict(model.make_future_dataframe(periods=periods, freq='D'))
    return result[['ds', 'yhat', 'yhat_lower', 'yhat_upper']], model


def _regression_features(values):
    features = pd.DataFrame({'ds': values.index, 'y': values.values})
    features['lag_1'], features['lag_7'] = features['y'].shift(1), features['y'].shift(7)
    features['rolling_mean_7'] = features['y'].shift(1).rolling(7).mean()
    features['rolling_std_7'] = features['y'].shift(1).rolling(7).std().fillna(0)
    features['day_of_week'], features['month'] = features['ds'].dt.dayofweek, features['ds'].dt.month
    features['time_index'] = np.arange(len(features))
    return features.dropna()


def _regression_fit_predict(history, future_dates):
    from sklearn.linear_model import LinearRegression

    columns = ['lag_1', 'lag_7', 'rolling_mean_7', 'rolling_std_7', 'day_of_week', 'month', 'time_index']
    features = _regression_features(history.set_index('ds')['y'])
    model = LinearRegression().fit(features[columns], features['y'])
    values = history.set_index('ds')['y'].copy()
    predictions = []
    for date in future_dates:
        window = values.reindex(pd.date_range(end=date - pd.Timedelta(days=1), periods=7, freq='D'))
        row = pd.DataFrame([{'lag_1': values.iloc[-1], 'lag_7': values.iloc[-7], 'rolling_mean_7': window.mean(), 'rolling_std_7': window.std() or 0, 'day_of_week': date.dayofweek, 'month': date.month, 'time_index': len(values)}])
        prediction = float(model.predict(row[columns])[0])
        predictions.append(prediction)
        values.loc[date] = prediction
    return np.asarray(predictions), model


def _upload(client, key, body, content_type='application/octet-stream'):
    client.put_object(Bucket=os.environ.get('MINIO_BUCKET', 'mobilesaledata'), Key=key, Body=body, ContentType=content_type)


def _upload_plots(client, history, forecast, validation):
    import matplotlib.pyplot as plt

    for name, plot in (
        ('historical_forecast.png', lambda: (plt.plot(history.ds, history.y, label='Historical actual'), plt.plot(forecast.forecast_date, forecast.predicted, label='Forecast'), plt.fill_between(forecast.forecast_date, forecast.lower_bound, forecast.upper_bound, alpha=.2, label='Confidence interval'))),
        ('validation_actual_vs_predicted.png', lambda: (plt.plot(validation.forecast_date, validation.actual, label='Actual'), plt.plot(validation.forecast_date, validation.predicted, label='Predicted'))),
    ):
        plt.figure(figsize=(12, 5)); plot(); plt.legend(); plt.tight_layout(); buffer = io.BytesIO(); plt.savefig(buffer, format='png'); plt.close(); _upload(client, f'{_prefix("forecasting")}/{name}', buffer.getvalue(), 'image/png')


def run_forecasting(forecast_period=7):
    if forecast_period not in {3, 7, 14, 30}:
        raise ValueError('forecast_period must be one of 3, 7, 14, or 30 days.')
    client = _s3_client()
    dataframe = read_time_analysis(client)
    date_column, value_column, profile = choose_columns(dataframe)
    history, prep = prepare_series(dataframe, date_column, value_column)
    profile.update({'selected_ds': date_column, 'selected_y': value_column, **prep, 'frequency': 'daily', 'periods_used': len(history), **describe_time_series(history)})
    validation_size = max(14, int(len(history) * .2))
    train, validation = history.iloc[:-validation_size], history.iloc[-validation_size:]
    prophet_frame, _ = _prophet_forecast(train, len(validation))
    prophet_validation = prophet_frame.tail(len(validation)).yhat.to_numpy()
    regression_validation, _ = _regression_fit_predict(train, validation.ds)
    evaluation = pd.DataFrame([{'model': 'Prophet', **_metrics(validation.y, prophet_validation)}, {'model': 'Regression', **_metrics(validation.y, regression_validation)}])
    best_model = evaluation.sort_values(['RMSE', 'MAE']).iloc[0]['model']
    prophet_full, _ = _prophet_forecast(history, forecast_period)
    future_dates = pd.date_range(history.ds.max() + pd.Timedelta(days=1), periods=forecast_period, freq='D')
    regression_future, _ = _regression_fit_predict(history, future_dates)
    prophet_future = prophet_full[prophet_full.ds > history.ds.max()]
    if best_model == 'Prophet':
        predicted, lower, upper = prophet_future.yhat.to_numpy(), prophet_future.yhat_lower.to_numpy(), prophet_future.yhat_upper.to_numpy()
    else:
        predicted = regression_future; interval = 1.96 * float(np.std(validation.y.to_numpy() - regression_validation)); lower, upper = predicted - interval, predicted + interval
    final = pd.DataFrame({'forecast_date': future_dates, 'actual': np.nan, 'predicted': predicted, 'lower_bound': lower, 'upper_bound': upper, 'model': best_model})
    validation_output = pd.DataFrame({'forecast_date': validation.ds, 'actual': validation.y, 'predicted': prophet_validation if best_model == 'Prophet' else regression_validation, 'model': best_model})
    output = _prefix('forecasting')
    for frame, name in ((final, 'forecast.parquet'), (evaluation, 'evaluation.parquet'), (validation_output, 'validation.parquet')):
        buffer = io.BytesIO(); frame.to_parquet(buffer, index=False); _upload(client, f'{output}/{name}', buffer.getvalue())
    _upload(client, f'{output}/profile.json', json.dumps(profile, indent=2).encode(), 'application/json')
    _upload_plots(client, history, final, validation_output)
    LOG.info('Forecast complete: target=%s best_model=%s output=s3://%s/%s', value_column, best_model, os.environ.get('MINIO_BUCKET', 'mobilesaledata'), output)
    forecast_records = json.loads(
        final.to_json(orient='records', date_format='iso')
    )
    return {'profile': profile, 'evaluation': evaluation.to_dict('records'), 'best_model': best_model, 'forecast': forecast_records}
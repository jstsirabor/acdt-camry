"""shared/influx_io.py — InfluxDB read/write."""
from datetime import datetime, timezone
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS
from shared.config import INFLUX_URL, INFLUX_TOKEN, INFLUX_ORG, INFLUX_BUCKET

_client = _write_api = _query_api = None

def _get_client():
    global _client, _write_api, _query_api
    if _client is None:
        _client = InfluxDBClient(
            url=INFLUX_URL,
            token=INFLUX_TOKEN,
            org=INFLUX_ORG,
            timeout=30_000,        # 30 seconds in milliseconds
        )
        _write_api = _client.write_api(write_options=SYNCHRONOUS)
        _query_api = _client.query_api()
    return _write_api, _query_api

def write_point(measurement: str, tags: dict, fields: dict):
    write_api, _ = _get_client()
    point = Point(measurement)
    for k, v in tags.items():
        point = point.tag(k, v)
    for k, v in fields.items():
        if v is not None:
            point = point.field(k, float(v))
    point = point.time(datetime.now(timezone.utc))
    write_api.write(bucket=INFLUX_BUCKET, record=point)

def get_latest(field: str) -> float | None:
    _, query_api = _get_client()
    query = f'''
    from(bucket:"{INFLUX_BUCKET}")
      |> range(start: -5m)
      |> filter(fn: (r) => r._measurement == "asset_telemetry")
      |> filter(fn: (r) => r._field == "{field}")
      |> last()
    '''
    for table in query_api.query(query=query):
        for record in table.records:
            return record.get_value()
    return None

def get_recent(field: str, minutes: int = 10):
    _, query_api = _get_client()
    query = f'''
    from(bucket:"{INFLUX_BUCKET}")
      |> range(start: -{minutes}m)
      |> filter(fn: (r) => r._measurement == "asset_telemetry")
      |> filter(fn: (r) => r._field == "{field}")
    '''
    return [
        (r.get_time(), r.get_value())
        for table in query_api.query(query=query)
        for r in table.records
    ]

def get_all_latest() -> dict:
    from shared.config import SENSOR_FIELDS
    return {f: get_latest(f) for f in SENSOR_FIELDS}

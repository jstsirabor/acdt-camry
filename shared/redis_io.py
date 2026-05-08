"""shared/redis_io.py — Redis cache for latest sensor values."""
import json
import redis
from shared.config import REDIS_HOST, REDIS_PORT

_client = None

def _get_client():
    global _client
    if _client is None:
        _client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT,
                              decode_responses=True)
    return _client

def cache_sensor(field: str, value: float):
    _get_client().set(f"sensor:{field}", value, ex=60)

def get_cached_sensor(field: str) -> float | None:
    val = _get_client().get(f"sensor:{field}")
    return float(val) if val is not None else None

def cache_health_score(score: float):
    _get_client().set("health_score", score, ex=30)

def get_health_score() -> float | None:
    val = _get_client().get("health_score")
    return float(val) if val is not None else None

def cache_agent_alert(agent: str, alert: str):
    _get_client().set(f"alert:{agent}", alert, ex=300)

def get_agent_alert(agent: str) -> str | None:
    return _get_client().get(f"alert:{agent}")

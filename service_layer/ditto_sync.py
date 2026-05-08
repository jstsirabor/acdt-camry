"""
service_layer/ditto_sync.py
────────────────────────────
Periodically syncs live sensor data and health score to Eclipse Ditto.
"""
import time
import threading
from shared.influx_io import get_all_latest
from shared.redis_io import get_health_score
from service_layer.ditto_client import update_telemetry, update_health

def _compute_health(readings: dict) -> tuple[float, str]:
    from shared.config import THRESHOLDS
    score = 100.0
    violations = 0
    for field, val in readings.items():
        if val is None:
            continue
        thresh = THRESHOLDS.get(field, {})
        if "critical" in thresh and val >= thresh["critical"]:
            score -= 20
            violations += 1
        elif "warning" in thresh and val >= thresh["warning"]:
            score -= 8
            violations += 1
        if "min" in thresh and val < thresh["min"]:
            score -= 8
            violations += 1
        if "max" in thresh and val > thresh["max"]:
            score -= 8
            violations += 1
    score = max(0.0, score)
    if score >= 85:
        status = "nominal"
    elif score >= 60:
        status = "warning"
    elif score >= 30:
        status = "degraded"
    else:
        status = "critical"
    return score, status

def sync_loop(interval: int = 5):
    print("[DITTO SYNC] Starting sync loop...")
    while True:
        try:
            readings = get_all_latest()
            if any(v is not None for v in readings.values()):
                update_telemetry({k: v for k, v in readings.items() if v is not None})
                score, status = _compute_health(readings)
                update_health(score, status)
                from shared.redis_io import cache_health_score
                cache_health_score(score)
                print(f"[DITTO SYNC] Health: {score:.1f} ({status})")
        except Exception as e:
            print(f"[DITTO SYNC] Error: {e}")
        time.sleep(interval)

def start_sync(interval: int = 5):
    t = threading.Thread(target=sync_loop, args=(interval,), daemon=True)
    t.start()
    return t

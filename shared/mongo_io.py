"""shared/mongo_io.py — MongoDB event log."""
from datetime import datetime, timezone
from pymongo import MongoClient
from shared.config import MONGO_URI, MONGO_DB

_client = _db = None

def _get_db():
    global _client, _db
    if _db is None:
        _client = MongoClient(MONGO_URI)
        _db     = _client[MONGO_DB]
    return _db

def log_event(event_type: str, details: dict, severity: str = "info"):
    db = _get_db()
    db.events.insert_one({
        "event_type": event_type,
        "severity":   severity,
        "details":    details,
        "timestamp":  datetime.now(timezone.utc),
    })

def get_recent_events(limit: int = 10) -> list:
    db = _get_db()
    return list(db.events.find(
        {}, {"_id": 0}
    ).sort("timestamp", -1).limit(limit))

def log_maintenance(service: str, km: int, notes: str = ""):
    db = _get_db()
    db.maintenance.insert_one({
        "service":   service,
        "km":        km,
        "notes":     notes,
        "timestamp": datetime.now(timezone.utc),
    })

def get_maintenance_history(service: str = None) -> list:
    db = _get_db()
    query = {"service": service} if service else {}
    return list(db.maintenance.find(query, {"_id": 0}).sort("timestamp", -1))

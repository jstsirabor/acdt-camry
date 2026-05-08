"""
shared/config.py
────────────────
Central configuration for the ACDT system.
"""
import os
from dotenv import load_dotenv
load_dotenv()

# ── Asset identity ─────────────────────────────────────────────────
ASSET_ID   = os.getenv("ASSET_ID", "VIN_1234567890")
ASSET_TYPE = os.getenv("ASSET_TYPE", "Toyota_Camry_2018_ICE")

# ── Ollama Cloud ───────────────────────────────────────────────────
OLLAMA_HOST      = os.getenv("OLLAMA_HOST", "https://ollama.com")
OLLAMA_API_KEY   = os.getenv("OLLAMA_API_KEY", "")
PREDICTIVE_MODEL = os.getenv("PREDICTIVE_MODEL", "gpt-oss:120b")
SAFETY_MODEL     = os.getenv("SAFETY_MODEL", "gemma3:12b")
PREVENTIVE_MODEL = os.getenv("PREVENTIVE_MODEL", "ministral-3:8b")

# ── OBD-II sensor fields ───────────────────────────────────────────
SENSOR_FIELDS = [
    "engine_rpm",
    "vehicle_speed",
    "coolant_temp",
    "o2_sensor1_voltage",
    "o2_sensor2_voltage",
    "mass_air_flow",
    "engine_load",
    "throttle_position",
    "fuel_trim_short",
    "fuel_trim_long",
]

# ── Safety thresholds ──────────────────────────────────────────────
THRESHOLDS = {
    "coolant_temp":        {"warning": 105, "critical": 115},
    "engine_rpm":          {"warning": 5500, "critical": 6500},
    "engine_load":         {"warning": 85,  "critical": 95},
    "o2_sensor1_voltage":  {"min": 0.1,     "max": 0.9},
    "o2_sensor2_voltage":  {"min": 0.1,     "max": 0.9},
    "fuel_trim_short":     {"warning": 15,  "critical": 25},
    "fuel_trim_long":      {"warning": 15,  "critical": 25},
}

# ── Maintenance intervals (km) ─────────────────────────────────────
MAINTENANCE_INTERVALS = {
    "oil_change":            {"interval_km": 8000,  "last_km": 0},
    "air_filter":            {"interval_km": 20000, "last_km": 0},
    "spark_plugs":           {"interval_km": 50000, "last_km": 0},
    "brake_fluid":           {"interval_km": 40000, "last_km": 0},
    "transmission_fluid":    {"interval_km": 60000, "last_km": 0},
    "catalytic_converter":   {"interval_km": 160000,"last_km": 0},
    "oxygen_sensors":        {"interval_km": 100000,"last_km": 0},
}

# ── Database ───────────────────────────────────────────────────────
INFLUX_URL    = os.getenv("INFLUX_URL",    "http://localhost:8086")
INFLUX_TOKEN  = os.getenv("INFLUX_TOKEN",  "my-super-secret-token")
INFLUX_ORG    = os.getenv("INFLUX_ORG",    "digital_twin")
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET", "asset_telemetry")

MONGO_URI = os.getenv("MONGO_URI", "mongodb://admin:password@localhost:27017")
MONGO_DB  = os.getenv("MONGO_DB",  "acdt")

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))

NEO4J_URI      = os.getenv("NEO4J_URI",      "bolt://localhost:7687")
NEO4J_USER     = os.getenv("NEO4J_USER",     "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")

DITTO_URL      = os.getenv("DITTO_URL",      "http://localhost:8080")
DITTO_USER     = os.getenv("DITTO_USER",     "ditto")
DITTO_PASSWORD = os.getenv("DITTO_PASSWORD", "ditto")

API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("API_PORT", 8501))

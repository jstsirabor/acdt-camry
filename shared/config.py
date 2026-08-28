"""
shared/config.py
────────────────
Central configuration for ACDT v3.
"""
import os
from dotenv import load_dotenv
load_dotenv()

# ── Asset ──────────────────────────────────────────────────────────
ASSET_ID   = os.getenv("ASSET_ID",   "VIN_1234567890")
ASSET_TYPE = os.getenv("ASSET_TYPE", "Toyota_Camry_2018_ICE")

# ── Ollama Cloud ───────────────────────────────────────────────────
OLLAMA_HOST      = os.getenv("OLLAMA_HOST",      "https://ollama.com")
OLLAMA_API_KEY   = os.getenv("OLLAMA_API_KEY",   "")
PREDICTIVE_MODEL = os.getenv("PREDICTIVE_MODEL", "gpt-oss:120b")
SAFETY_MODEL     = os.getenv("SAFETY_MODEL",     "gpt-oss:120b")
PREVENTIVE_MODEL = os.getenv("PREVENTIVE_MODEL", "gpt-oss:120b")
DIAGNOSTIC_MODEL = os.getenv("DIAGNOSTIC_MODEL", "gpt-oss:120b")
GUIDANCE_MODEL = os.getenv("GUIDANCE_MODEL", "gpt-oss:120b") 

# ── OBD-II sensors ─────────────────────────────────────────────────
SENSOR_FIELDS = [
    # Engine
    "engine_rpm",
    "vehicle_speed",
    "coolant_temp",
    "engine_load",
    "throttle_position",
    "intake_air_temp",
    "barometric_pressure",
    # Fuel & Emissions
    "mass_air_flow",
    "fuel_trim_short",
    "fuel_trim_long",
    "fuel_level",
    "o2_sensor1_voltage",
    "o2_sensor2_voltage",
    # Electrical
    "battery_voltage",
    "alternator_voltage",
    # Brakes & Wheels
    "brake_pressure",
    "tyre_pressure_fl",
    "tyre_pressure_fr",
    "tyre_pressure_rl",
    "tyre_pressure_rr",
    # Engine health
    "oil_temp",
    "oil_pressure",
    "misfire_count_cyl1",
    "misfire_count_cyl2",
    "misfire_count_cyl3",
    "misfire_count_cyl4",
    # Turbo (if applicable)
    "boost_pressure",
]

# ── Safety thresholds ──────────────────────────────────────────────
THRESHOLDS = {
    "coolant_temp":       {"warning": 105,  "critical": 115},
    "engine_rpm":         {"warning": 5500, "critical": 6500},
    "engine_load":        {"warning": 85,   "critical": 95},
    "oil_temp":           {"warning": 130,  "critical": 150},
    "oil_pressure":       {"min": 20,       "warning_low": 25, "critical_low": 20},
    "battery_voltage":    {"min": 11.5,     "warning_low": 12.0, "max": 15.0},
    "alternator_voltage": {"min": 13.5,     "max": 14.8},
    "brake_pressure":     {"min": 0,        "max": 150},
    "tyre_pressure_fl":   {"min": 28,       "max": 36},
    "tyre_pressure_fr":   {"min": 28,       "max": 36},
    "tyre_pressure_rl":   {"min": 28,       "max": 36},
    "tyre_pressure_rr":   {"min": 28,       "max": 36},
    "fuel_trim_short":    {"warning": 15,   "critical": 25},
    "fuel_trim_long":     {"warning": 15,   "critical": 25},
    "boost_pressure":     {"warning": 18,   "critical": 22},
    "misfire_count_cyl1": {"warning": 5,    "critical": 20},
    "misfire_count_cyl2": {"warning": 5,    "critical": 20},
    "misfire_count_cyl3": {"warning": 5,    "critical": 20},
    "misfire_count_cyl4": {"warning": 5,    "critical": 20},
    "o2_sensor1_voltage": {"min": 0.1,      "max": 0.9},
    "o2_sensor2_voltage": {"min": 0.1,      "max": 0.9},
    "fuel_level":         {"warning_low": 15, "critical_low": 5},
}

# ── Fault scenarios ────────────────────────────────────────────────
FAULT_SCENARIOS = {
    "catalytic_degradation": {
        "description": "Catalytic converter efficiency degrading",
        "dtc": "P0420",
        "sensors": ["o2_sensor1_voltage", "o2_sensor2_voltage"],
    },
    "engine_misfire": {
        "description": "Engine misfiring on one or more cylinders",
        "dtc": "P0300",
        "sensors": ["misfire_count_cyl1", "misfire_count_cyl2",
                    "misfire_count_cyl3", "misfire_count_cyl4"],
    },
    "battery_drain": {
        "description": "Battery voltage dropping below safe level",
        "dtc": "B0001",
        "sensors": ["battery_voltage", "alternator_voltage"],
    },
    "low_tyre_pressure": {
        "description": "One or more tyres below safe pressure",
        "dtc": "C0750",
        "sensors": ["tyre_pressure_fl", "tyre_pressure_fr",
                    "tyre_pressure_rl", "tyre_pressure_rr"],
    },
    "coolant_leak": {
        "description": "Possible coolant leak — temperature rising with low pressure",
        "dtc": "P0217",
        "sensors": ["coolant_temp", "oil_temp"],
    },
    "oil_pressure_low": {
        "description": "Oil pressure below safe operating range",
        "dtc": "P0520",
        "sensors": ["oil_pressure", "oil_temp"],
    },
}

# ── Maintenance intervals (km) ─────────────────────────────────────
MAINTENANCE_INTERVALS = {
    "oil_change":          {"interval_km": 8000,   "last_km": 0},
    "air_filter":          {"interval_km": 20000,  "last_km": 0},
    "spark_plugs":         {"interval_km": 50000,  "last_km": 0},
    "brake_fluid":         {"interval_km": 40000,  "last_km": 0},
    "brake_pads":          {"interval_km": 50000,  "last_km": 0},
    "transmission_fluid":  {"interval_km": 60000,  "last_km": 0},
    "coolant_flush":       {"interval_km": 50000,  "last_km": 0},
    "tyre_rotation":       {"interval_km": 10000,  "last_km": 0},
    "battery_check":       {"interval_km": 30000,  "last_km": 0},
    "catalytic_converter": {"interval_km": 160000, "last_km": 0},
    "oxygen_sensors":      {"interval_km": 100000, "last_km": 0},
    "timing_belt":         {"interval_km": 100000, "last_km": 0},
}

# ── OBD-II hardware ────────────────────────────────────────────────
OBD_MODE     = os.getenv("OBD_MODE",     "auto")
OBD_PORT     = os.getenv("OBD_PORT",     "/dev/ttyUSB0")
OBD_BAUDRATE = int(os.getenv("OBD_BAUDRATE", "38400"))

# ── Cloud MQTT (ESP32 telemetry bridge) ────────────────────────────
MQTT_SERVER           = os.getenv("MQTT_SERVER",           "")
MQTT_PORT              = int(os.getenv("MQTT_PORT", "8883"))
MQTT_USER              = os.getenv("MQTT_USER",             "")
MQTT_PASS              = os.getenv("MQTT_PASS",             "")
MQTT_TELEMETRY_TOPIC   = os.getenv("MQTT_TELEMETRY_TOPIC",  f"v2c/{os.getenv('ASSET_ID', 'VIN_1234567890')}/telemetry")
MQTT_LOCATION_TOPIC    = os.getenv("MQTT_LOCATION_TOPIC",   f"v2c/{os.getenv('ASSET_ID', 'VIN_1234567890')}/location")

# ── External APIs ──────────────────────────────────────────────────
OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY", "")
OPENROUTE_API_KEY   = os.getenv("OPENROUTE_API_KEY",   "")

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

MECHANIC_DITTO_URL      = os.getenv("MECHANIC_DITTO_URL",      "http://localhost:8090")
MECHANIC_DITTO_USER     = os.getenv("MECHANIC_DITTO_USER",     "ditto")
MECHANIC_DITTO_PASSWORD = os.getenv("MECHANIC_DITTO_PASSWORD", "ditto")
MECHANIC_THING_ID       = os.getenv("MECHANIC_THING_ID",       "org.example:MECHANIC_001")

API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("API_PORT", 8501))

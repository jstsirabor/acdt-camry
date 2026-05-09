"""
physical/simulator.py
──────────────────────
Simulates a 2018 Toyota Camry OBD-II stream with hidden
catalytic converter degradation. Writes directly to InfluxDB.
"""
import random
import time
from shared.config import ASSET_ID, SENSOR_FIELDS
from shared.influx_io import write_point

NOMINAL = {
    "engine_rpm":         1450,
    "vehicle_speed":      60,
    "coolant_temp":       90,
    "o2_sensor1_voltage": 0.45,
    "o2_sensor2_voltage": 0.55,
    "mass_air_flow":      12.0,
    "engine_load":        45.0,
    "throttle_position":  20.0,
    "fuel_trim_short":    0.0,
    "fuel_trim_long":     0.0,
}
NOISE = {
    "engine_rpm":         30,
    "vehicle_speed":      2,
    "coolant_temp":       0.3,
    "o2_sensor1_voltage": 0.02,
    "o2_sensor2_voltage": 0.02,
    "mass_air_flow":      0.3,
    "engine_load":        2,
    "throttle_position":  0.5,
    "fuel_trim_short":    0.5,
    "fuel_trim_long":     0.5,
}

class CatalyticDegradation:
    def __init__(self):
        self.health = 1.0
        self.rate   = 0.0002  # per second

    def step(self):
        self.health = max(0.0, self.health - self.rate)
        return self.health

_cat = CatalyticDegradation()

def generate_reading() -> dict:
    data = {}
    for field in SENSOR_FIELDS:
        val = NOMINAL[field] + random.gauss(0, NOISE[field])
        if field == "o2_sensor1_voltage":
            val = 0.45 + random.gauss(0, 0.1)
        elif field == "o2_sensor2_voltage":
            health   = _cat.step()
            upstream = data.get("o2_sensor1_voltage", 0.45)
            steady   = 0.55 + random.gauss(0, 0.02)
            mirror   = upstream + random.gauss(0, 0.05)
            val      = health * steady + (1 - health) * mirror
        if random.random() < 0.005:
            val += random.uniform(10, 30)
        data[field] = round(val, 2)
    data["asset_id"] = ASSET_ID
    return data

def run():
    print("[SIMULATOR] Writing OBD-II telemetry to InfluxDB...")
    consecutive_errors = 0
    while True:
        try:
            payload = generate_reading()
            fields  = {k: v for k, v in payload.items() if k in SENSOR_FIELDS}
            write_point("asset_telemetry", {"asset_id": ASSET_ID}, fields)
            print(f"[SIMULATOR] {fields}")
            consecutive_errors = 0
        except Exception as e:
            consecutive_errors += 1
            print(f"[SIMULATOR] Write error ({consecutive_errors}): {e.__class__.__name__} — retrying in 3s...")
            time.sleep(3)
            continue
        time.sleep(0.2)

if __name__ == "__main__":
    run()

"""
physical/simulator.py
──────────────────────
Expanded OBD-II simulator with multiple fault scenarios:
- Catalytic converter degradation (hidden, slow)
- Engine misfire (random, intermittent)
- Battery drain (gradual)
- Tyre pressure loss (slow leak on rear left)
- Oil pressure drop (triggered after high load)
"""
import random
import time
import threading
from shared.config import ASSET_ID, SENSOR_FIELDS
from shared.influx_io import write_point

# ── Nominal values ─────────────────────────────────────────────────
NOMINAL = {
    "engine_rpm":          1450,
    "vehicle_speed":       60,
    "coolant_temp":        90,
    "o2_sensor1_voltage":  0.45,
    "o2_sensor2_voltage":  0.55,
    "mass_air_flow":       12.0,
    "engine_load":         45.0,
    "throttle_position":   20.0,
    "fuel_trim_short":     0.0,
    "fuel_trim_long":      0.0,
    "fuel_level":          65.0,
    "intake_air_temp":     35.0,
    "barometric_pressure": 101.0,
    "battery_voltage":     12.6,
    "alternator_voltage":  14.2,
    "brake_pressure":      0.0,
    "tyre_pressure_fl":    33.0,
    "tyre_pressure_fr":    33.0,
    "tyre_pressure_rl":    33.0,
    "tyre_pressure_rr":    33.0,
    "oil_temp":            95.0,
    "oil_pressure":        45.0,
    "misfire_count_cyl1":  0,
    "misfire_count_cyl2":  0,
    "misfire_count_cyl3":  0,
    "misfire_count_cyl4":  0,
    "boost_pressure":      0.0,
}

NOISE = {
    "engine_rpm":          30,
    "vehicle_speed":       2,
    "coolant_temp":        0.3,
    "o2_sensor1_voltage":  0.02,
    "o2_sensor2_voltage":  0.02,
    "mass_air_flow":       0.3,
    "engine_load":         2,
    "throttle_position":   0.5,
    "fuel_trim_short":     0.5,
    "fuel_trim_long":      0.3,
    "fuel_level":          0.05,
    "intake_air_temp":     0.5,
    "barometric_pressure": 0.2,
    "battery_voltage":     0.05,
    "alternator_voltage":  0.1,
    "brake_pressure":      0.5,
    "tyre_pressure_fl":    0.1,
    "tyre_pressure_fr":    0.1,
    "tyre_pressure_rl":    0.1,
    "tyre_pressure_rr":    0.1,
    "oil_temp":            0.5,
    "oil_pressure":        1.0,
    "misfire_count_cyl1":  0,
    "misfire_count_cyl2":  0,
    "misfire_count_cyl3":  0,
    "misfire_count_cyl4":  0,
    "boost_pressure":      0.2,
}


# ── Fault simulators ───────────────────────────────────────────────
class CatalyticDegradation:
    def __init__(self):
        self.health = 1.0
        self.rate   = 0.0002

    def step(self):
        self.health = max(0.0, self.health - self.rate)
        return self.health


class BatteryDrain:
    def __init__(self):
        self.voltage = 12.6
        self.rate    = 0.00005

    def step(self):
        self.voltage = max(10.5, self.voltage - self.rate)
        return self.voltage


class TyreLeak:
    """Slow leak on rear left tyre."""
    def __init__(self):
        self.pressure = 33.0
        self.rate     = 0.0001

    def step(self):
        self.pressure = max(20.0, self.pressure - self.rate)
        return self.pressure


class MisfireSimulator:
    """Intermittent misfire on cylinder 3."""
    def __init__(self):
        self.count = 0

    def step(self):
        if random.random() < 0.02:
            self.count += random.randint(1, 3)
        return self.count


_cat     = CatalyticDegradation()
_battery = BatteryDrain()
_tyre    = TyreLeak()
_misfire = MisfireSimulator()
_tick    = 0

# Set by main.py's watcher thread to stop run() cleanly when the live
# data source is no longer "simulator"/"auto"-falling-back-to-simulator,
# so simulated telemetry isn't written to InfluxDB while a real source
# (adapter/mqtt) is selected.
stop_event = threading.Event()


def generate_reading() -> dict:
    global _tick
    _tick += 1
    data = {}

    for field in SENSOR_FIELDS:
        noise = NOISE.get(field, 0)
        val   = NOMINAL[field] + (random.gauss(0, noise) if noise > 0 else 0)

        # Apply fault models
        if field == "o2_sensor1_voltage":
            val = 0.45 + random.gauss(0, 0.1)

        elif field == "o2_sensor2_voltage":
            health   = _cat.step()
            upstream = data.get("o2_sensor1_voltage", 0.45)
            steady   = 0.55 + random.gauss(0, 0.02)
            mirror   = upstream + random.gauss(0, 0.05)
            val      = health * steady + (1 - health) * mirror

        elif field == "battery_voltage":
            val = _battery.step() + random.gauss(0, 0.05)

        elif field == "alternator_voltage":
            # Alternator drops when battery is very low
            val = 14.2 if _battery.voltage > 12.0 else 13.0 + random.gauss(0, 0.2)

        elif field == "tyre_pressure_rl":
            val = _tyre.step() + random.gauss(0, 0.1)

        elif field == "misfire_count_cyl3":
            val = _misfire.step()

        elif field == "fuel_level":
            # Slowly decreasing fuel
            val = max(0, NOMINAL["fuel_level"] - (_tick * 0.0002)) + random.gauss(0, 0.05)

        elif field == "oil_pressure":
            # Oil pressure drops slightly under high load
            load = data.get("engine_load", 45)
            val  = 45 - (load * 0.1) + random.gauss(0, 1.0)

        # Occasional random spike
        if random.random() < 0.003 and field not in (
            "misfire_count_cyl1", "misfire_count_cyl2",
            "misfire_count_cyl3", "misfire_count_cyl4"
        ):
            val += random.uniform(5, 15)

        data[field] = round(val, 2) if isinstance(val, float) else int(val)

    data["asset_id"] = ASSET_ID
    return data


def run():
    """Write simulated telemetry to InfluxDB in a loop until stop_event
    is set. main.py's watcher thread sets/clears stop_event and restarts
    this in a fresh thread as the live data-source mode changes, so this
    only runs while simulator/auto-fallback mode is actually active."""
    print("[SIMULATOR] Writing expanded OBD-II telemetry to InfluxDB...")
    stop_event.clear()
    consecutive_errors = 0
    while not stop_event.is_set():
        try:
            payload = generate_reading()
            fields  = {k: v for k, v in payload.items()
                       if k in SENSOR_FIELDS and v is not None}
            write_point("asset_telemetry", {"asset_id": ASSET_ID}, fields)
            print(f"[SIMULATOR] tick={_tick} fuel={fields.get('fuel_level','?')}% "
                  f"batt={fields.get('battery_voltage','?')}V "
                  f"tyre_rl={fields.get('tyre_pressure_rl','?')}psi "
                  f"rpm={fields.get('engine_rpm','?')}")
            consecutive_errors = 0
        except Exception as e:
            consecutive_errors += 1
            print(f"[SIMULATOR] Write error ({consecutive_errors}): {e.__class__.__name__} — retrying in 3s")
            if stop_event.wait(3):
                break
            continue
        if stop_event.wait(0.2):
            break
    print("[SIMULATOR] Stopped.")


if __name__ == "__main__":
    run()

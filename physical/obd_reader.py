"""
physical/obd_reader.py
───────────────────────
OBD-II reader with automatic fallback to simulator.

OBD_MODE=simulator  → always use simulator
OBD_MODE=adapter    → always use real hardware (fails if not found)
OBD_MODE=mqtt       → read telemetry published by the ESP32 over a cloud
                      MQTT broker (see physical/mqtt_reader.py)
OBD_MODE=auto       → try hardware, fall back to simulator if not found

Live override (no restart needed): set the Redis key "obd:mode_override"
to "adapter" | "mqtt" | "simulator" | "auto" to force a source for
testing/demoing, and delete the key (or set it to "" ) to go back to
whatever OBD_MODE / auto-detection would normally choose. Checked on
every read_sensors() call, so it can be flipped live without touching
.env or restarting the service.

When running in simulator mode due to missing adapter, a system
message is pushed to the driver chat explaining the situation.
"""
import time
from shared.config import OBD_MODE, OBD_PORT, OBD_BAUDRATE

# ── Current data source ────────────────────────────────────────────
_data_source = None   # 'adapter' | 'mqtt' | 'simulator' | 'none'
_obd_conn    = None
_active_override = None  # last override value applied, so we only re-init on change

# Whether the last read from a "live" source (adapter/mqtt) actually
# contained real sensor values, as opposed to a connection/message being
# present but carrying only nulls (e.g. ELM327 handshake failing, or the
# ESP32 publishing a cycle where every PID query failed). get_data_source()
# uses this to avoid reporting "LIVE" when nothing meaningful is flowing.
_last_read_had_data = True

_OVERRIDE_REDIS_KEY = "obd:mode_override"

# Fields that don't count as "real sensor data" on their own — battery
# voltage is read directly off the ELM327's voltage-sense pin and can
# succeed even when the ECU never responds to any OBD PID query, and
# asset_id/timestamp are just metadata.
_NON_SENSOR_FIELDS = {"asset_id", "battery_voltage", "timestamp"}


def _get_live_override() -> str | None:
    """Check Redis for a live mode override. Returns None if unset/unreachable."""
    try:
        import redis
        from shared.config import REDIS_HOST, REDIS_PORT
        r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
        val = r.get(_OVERRIDE_REDIS_KEY)
        if val and val.strip():
            return val.strip().lower()
        return None
    except Exception:
        return None


def detect_adapter() -> bool:
    """Check if an ELM327 adapter is connected."""
    try:
        import obd
        ports = obd.scan_serial()
        return len(ports) > 0
    except ImportError:
        return False
    except Exception:
        return False


def connect_adapter() -> bool:
    """Attempt to connect to the OBD-II adapter."""
    global _obd_conn
    try:
        import obd
        _obd_conn = obd.OBD(portstr=OBD_PORT, baudrate=OBD_BAUDRATE, fast=False)
        return _obd_conn.is_connected()
    except Exception as e:
        print(f"[OBD READER] Adapter connection failed: {e}")
        return False


def _initialise_for_mode(mode: str) -> str:
    """Initialise the data source for a specific mode string
    ('simulator' | 'adapter' | 'mqtt' | 'auto'). Shared by initialise()
    and the live-override re-init path."""
    global _data_source, _last_read_had_data

    # Reset the "had data" flag on every (re)init so a stale value from a
    # previous mode doesn't linger and misreport the new mode's status.
    _last_read_had_data = True

    if mode == "simulator":
        _data_source = "simulator"
        print("[OBD READER] Mode: SIMULATOR (forced)")
        _notify_driver_simulator(forced=True)
        return _data_source

    if mode == "adapter":
        print("[OBD READER] Mode: ADAPTER (forced)")
        if connect_adapter():
            _data_source = "adapter"
            print("[OBD READER] ✅ Adapter connected")
        else:
            _data_source = "none"
            print("[OBD READER] ⚠ Adapter not found — no live data available")
            _notify_driver_no_adapter()
        return _data_source

    if mode == "mqtt":
        print("[OBD READER] Mode: MQTT (cloud broker)")
        from physical.mqtt_reader import start_mqtt
        if start_mqtt():
            _data_source = "mqtt"
            print("[OBD READER] ✅ MQTT subscriber started, waiting for ESP32 data...")
            _notify_driver_mqtt_started()
        else:
            _data_source = "none"
            print("[OBD READER] ⚠ MQTT connection failed — no live data available")
            _notify_driver_no_adapter()
        return _data_source

    # auto mode
    print("[OBD READER] Mode: AUTO — scanning for adapter...")
    if detect_adapter() and connect_adapter():
        _data_source = "adapter"
        print("[OBD READER] ✅ Adapter found and connected")
        _notify_driver_adapter_connected()
    else:
        _data_source = "simulator"
        print("[OBD READER] ℹ No adapter found — using simulator")
        _notify_driver_no_adapter()

    return _data_source


def initialise() -> str:
    """
    Initialise the data source. A live Redis override takes priority
    over OBD_MODE if set; otherwise falls back to OBD_MODE as before.
    Returns: 'adapter' | 'mqtt' | 'simulator' | 'none'
    """
    global _active_override
    override = _get_live_override()
    _active_override = override
    mode = override if override in ("simulator", "adapter", "mqtt", "auto") else OBD_MODE
    if override and override not in ("simulator", "adapter", "mqtt", "auto"):
        print(f"[OBD READER] Ignoring invalid override value '{override}', using OBD_MODE={OBD_MODE}")
    return _initialise_for_mode(mode)


def get_data_source() -> str:
    """Report the data source the UI should reflect.

    For 'adapter'/'mqtt' this is downgraded to 'none' when the last
    actual read didn't contain any real sensor values — e.g. the ELM327
    handshake with the ECU is failing, or the ESP32 is publishing
    cycles where every PID query came back null. A connection or
    message existing isn't the same as real vehicle data existing, and
    the chip shouldn't say "LIVE" when it isn't. _data_source itself is
    left alone so reconnection/retry logic for the underlying mode
    keeps working."""
    if _data_source in ("adapter", "mqtt") and not _last_read_had_data:
        return "none"
    return _data_source or "simulator"


def _has_real_sensor_data(data: dict) -> bool:
    """True if at least one field beyond battery_voltage/asset_id/timestamp
    is non-null."""
    return any(
        v is not None
        for k, v in data.items()
        if k not in _NON_SENSOR_FIELDS
    )


def read_sensors() -> dict:
    """Read sensors from whichever source is active. Re-checks the live
    override each call (cheap Redis GET) so a change takes effect on the
    next read without restarting the service."""
    global _active_override, _last_read_had_data
    override = _get_live_override()
    if override != _active_override:
        print(f"[OBD READER] Live override changed ({_active_override!r} → {override!r}) — reinitialising")
        _active_override = override
        mode = override if override in ("simulator", "adapter", "mqtt", "auto") else OBD_MODE
        _initialise_for_mode(mode)

    if _data_source == "adapter":
        data = _read_from_adapter()
        _last_read_had_data = _has_real_sensor_data(data)
        return data
    if _data_source == "mqtt":
        data = _read_from_mqtt()
        _last_read_had_data = _has_real_sensor_data(data)
        return data
    if _data_source == "none":
        return _no_data_reading()
    return _read_from_simulator()


def _read_from_adapter() -> dict:
    """Read live OBD-II data from ELM327 adapter."""
    global _data_source
    try:
        import obd
        commands = {
            "engine_rpm":         obd.commands.RPM,
            "vehicle_speed":      obd.commands.SPEED,
            "coolant_temp":       obd.commands.COOLANT_TEMP,
            "engine_load":        obd.commands.ENGINE_LOAD,
            "throttle_position":  obd.commands.THROTTLE_POS,
            "intake_air_temp":    obd.commands.INTAKE_TEMP,
            "mass_air_flow":      obd.commands.MAF,
            "fuel_trim_short":    obd.commands.SHORT_FUEL_TRIM_1,
            "fuel_trim_long":     obd.commands.LONG_FUEL_TRIM_1,
            "fuel_level":         obd.commands.FUEL_LEVEL,
            "o2_sensor1_voltage": obd.commands.O2_B1S1,
            "o2_sensor2_voltage": obd.commands.O2_B1S2,
            "barometric_pressure":obd.commands.BAROMETRIC_PRESSURE,
            "oil_temp":           obd.commands.OIL_TEMP,
        }
        data = {}
        for field, cmd in commands.items():
            try:
                response = _obd_conn.query(cmd)
                if not response.is_null():
                    data[field] = round(float(response.value.magnitude), 2)
            except Exception:
                pass

        # Battery voltage via ELM327 direct command
        try:
            data["battery_voltage"] = float(_obd_conn.query(obd.commands.ELM_VOLTAGE).value)
        except Exception:
            pass

        # Fill missing fields with None
        from shared.config import SENSOR_FIELDS
        for f in SENSOR_FIELDS:
            if f not in data:
                data[f] = None

        return data

    except Exception as e:
        print(f"[OBD READER] Read error: {e} — no live data available")
        _data_source = "none"
        _notify_driver_adapter_lost()
        return _no_data_reading()


def _read_from_mqtt() -> dict:
    """Read the latest telemetry published by the ESP32 over the cloud broker."""
    from physical.mqtt_reader import read_latest, is_receiving_data

    if not is_receiving_data():
        # No message in the last 30s — ESP32 may be offline, out of range,
        # or mid-cycle switching between the ELM327 and home WiFi. Reflect
        # that honestly instead of quietly serving fake numbers labeled
        # "LIVE (MQTT)". Stay in "mqtt" mode (don't permanently fall back)
        # since the ESP32 publishes every ~5s and may just be mid-cycle.
        return _no_data_reading()

    data = read_latest()

    # A message arriving isn't the same as real vehicle data arriving.
    # The ESP32 publishes every cycle even when the ELM327 couldn't get a
    # single PID response from the car — in that case everything except
    # battery_voltage (read directly off the adapter's voltage-sense pin,
    # not through the OBD protocol handshake) comes through as null.
    # Report that as "no data" rather than "LIVE" so the mechanic isn't
    # misled into thinking the car is actually responding.
    if not _has_real_sensor_data(data):
        return _no_data_reading()

    return data


def _read_from_simulator() -> dict:
    """Get latest reading from the simulator."""
    from physical.simulator import generate_reading
    return generate_reading()


def _no_data_reading() -> dict:
    """All-None reading used whenever a live source is selected but not
    currently producing data. Used instead of the simulator so the driver
    never sees fabricated numbers presented as real."""
    from shared.config import SENSOR_FIELDS, ASSET_ID
    data = {f: None for f in SENSOR_FIELDS}
    data["asset_id"] = ASSET_ID
    return data


# ── Driver notifications ───────────────────────────────────────────
def _notify_driver_no_adapter():
    _push_system_message(
        "No live OBD-II data available right now. Once your adapter/ESP32 "
        "is connected and the car is on, I'll switch to live data automatically."
    )


def _notify_driver_simulator(forced: bool = False):
    if forced:
        _push_system_message(
            "Running in simulator mode. All vehicle data is simulated. "
            "To connect a real OBD-II adapter, plug in your ELM327 device "
            "and change OBD_MODE to 'auto' in your settings."
        )


def _notify_driver_adapter_connected():
    _push_system_message(
        "OBD-II adapter connected successfully. I'm now reading live data "
        "directly from your vehicle."
    )


def _notify_driver_adapter_lost():
    _push_system_message(
        "Lost connection to the OBD-II adapter. No live data is available "
        "right now. Please check the adapter connection and restart if needed."
    )


def _notify_driver_mqtt_started():
    _push_system_message(
        "Connected to the cloud telemetry broker. I'm now listening for "
        "live data from your ESP32 — this works from anywhere, not just "
        "your home network. No data will show until the first reading arrives."
    )


def _push_system_message(text: str):
    """Push a system message to the driver chat via Redis."""
    try:
        import redis, json
        from datetime import datetime, timezone
        from shared.config import REDIS_HOST, REDIS_PORT
        r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
        msg = {
            "role":      "system",
            "content":   text,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source":    "obd_reader",
        }
        r.lpush("driver:messages", json.dumps(msg))
        r.ltrim("driver:messages", 0, 199)
    except Exception as e:
        print(f"[OBD READER] Could not push message: {e}")


def run_live_writer(poll_interval: float = 2.0):
    """Writes real adapter/mqtt telemetry to InfluxDB whenever it's the
    active data source — mirrors simulator.run(), but sources data from
    read_sensors() instead of generate_reading(). Runs forever in a
    daemon thread; skips writing (but keeps looping) whenever the
    active source isn't adapter/mqtt, or when a live source has no
    real data yet (get_data_source() reports 'none' in that case)."""
    from shared.influx_io import write_point
    from shared.config import SENSOR_FIELDS, ASSET_ID
    import time as _time

    while True:
        try:
            source = get_data_source()
            if source in ("adapter", "mqtt"):
                payload = read_sensors()
                fields = {k: v for k, v in payload.items()
                          if k in SENSOR_FIELDS and v is not None}
                if fields:
                    write_point("asset_telemetry", {"asset_id": ASSET_ID}, fields)
        except Exception as e:
            print(f"[OBD READER] Live writer error: {e}")
        _time.sleep(poll_interval)

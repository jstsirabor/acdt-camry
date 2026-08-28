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
_data_source = None   # 'adapter' | 'mqtt' | 'simulator'
_obd_conn    = None
_active_override = None  # last override value applied, so we only re-init on change

_OVERRIDE_REDIS_KEY = "obd:mode_override"


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
    global _data_source

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
            _data_source = "simulator"
            print("[OBD READER] ⚠ Adapter not found — falling back to simulator")
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
            _data_source = "simulator"
            print("[OBD READER] ⚠ MQTT connection failed — falling back to simulator")
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
    Returns: 'adapter' | 'mqtt' | 'simulator'
    """
    global _active_override
    override = _get_live_override()
    _active_override = override
    mode = override if override in ("simulator", "adapter", "mqtt", "auto") else OBD_MODE
    if override and override not in ("simulator", "adapter", "mqtt", "auto"):
        print(f"[OBD READER] Ignoring invalid override value '{override}', using OBD_MODE={OBD_MODE}")
    return _initialise_for_mode(mode)


def get_data_source() -> str:
    return _data_source or "simulator"


def read_sensors() -> dict:
    """Read sensors from whichever source is active. Re-checks the live
    override each call (cheap Redis GET) so a change takes effect on the
    next read without restarting the service."""
    global _active_override
    override = _get_live_override()
    if override != _active_override:
        print(f"[OBD READER] Live override changed ({_active_override!r} → {override!r}) — reinitialising")
        _active_override = override
        mode = override if override in ("simulator", "adapter", "mqtt", "auto") else OBD_MODE
        _initialise_for_mode(mode)

    if _data_source == "adapter":
        return _read_from_adapter()
    if _data_source == "mqtt":
        return _read_from_mqtt()
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
        print(f"[OBD READER] Read error: {e} — switching to simulator")
        _data_source = "simulator"
        _notify_driver_adapter_lost()
        return _read_from_simulator()


def _read_from_mqtt() -> dict:
    """Read the latest telemetry published by the ESP32 over the cloud broker."""
    global _data_source
    from physical.mqtt_reader import read_latest, is_receiving_data

    if not is_receiving_data():
        # No message in the last 30s — ESP32 may be offline, out of range,
        # or mid-cycle switching between the ELM327 and home WiFi.
        # Don't permanently fall back, just serve the simulator for this
        # cycle and keep listening; the ESP32 publishes every ~5s.
        return _read_from_simulator()

    return read_latest()


def _read_from_simulator() -> dict:
    """Get latest reading from the simulator."""
    from physical.simulator import generate_reading
    return generate_reading()


# ── Driver notifications ───────────────────────────────────────────
def _notify_driver_no_adapter():
    _push_system_message(
        "No OBD-II adapter detected. I'm running on simulated vehicle data "
        "for now. When your ELM327 adapter arrives, plug it into your car's "
        "OBD-II port and set OBD_MODE=auto in your settings — I'll switch "
        "to live data automatically. Would you like to continue with the simulator?"
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
        "Lost connection to the OBD-II adapter. Switching to simulated data. "
        "Please check the adapter connection and restart if needed."
    )


def _notify_driver_mqtt_started():
    _push_system_message(
        "Connected to the cloud telemetry broker. I'm now listening for "
        "live data from your ESP32 — this works from anywhere, not just "
        "your home network. Simulated data will show until the first "
        "reading arrives."
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

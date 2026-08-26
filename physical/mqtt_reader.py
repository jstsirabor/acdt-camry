"""
physical/mqtt_reader.py
─────────────────────────
Cloud MQTT data source for the ESP32/ELM327 telemetry bridge.

The ESP32 firmware publishes JSON telemetry to a cloud MQTT broker
(HiveMQ Cloud) on the topic v2c/<ASSET_ID>/telemetry. This module
subscribes to that topic in a background thread and hands off the
latest reading whenever obd_reader.read_sensors() asks for one.

This is the third data source alongside 'adapter' (wired ELM327) and
'simulator', selected via OBD_MODE=mqtt.
"""
import json
import ssl
import threading
import time

import paho.mqtt.client as mqtt

from shared.config import (
    MQTT_SERVER, MQTT_PORT, MQTT_USER, MQTT_PASS,
    MQTT_TELEMETRY_TOPIC, MQTT_LOCATION_TOPIC, SENSOR_FIELDS,
)

_lock            = threading.Lock()
_latest_reading  = {}
_latest_location = {}
_last_update_ts  = 0.0
_client          = None
_STALE_AFTER_S   = 30  # if no message in this long, treat data as stale

# Map ESP32 firmware field names -> backend field names (mostly identical,
# kept explicit here in case either side's naming drifts later)
_FIELD_MAP = {
    "engine_rpm":              "engine_rpm",
    "vehicle_speed":           "vehicle_speed",
    "coolant_temp":            "coolant_temp",
    "engine_load":             "engine_load",
    "throttle_position":       "throttle_position",
    "intake_air_temp":         "intake_air_temp",
    "mass_air_flow":           "mass_air_flow",
    "fuel_trim_short":         "fuel_trim_short",
    "fuel_trim_long":          "fuel_trim_long",
    "fuel_level":              "fuel_level",
    "barometric_pressure":     "barometric_pressure",
    "oil_temp":                "oil_temp",
    "battery_voltage":         "battery_voltage",
    "intake_manifold_pressure":"intake_manifold_pressure",
    "absolute_load":           "absolute_load",
}


def _on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        print(f"[MQTT READER] Connected to {MQTT_SERVER}")
        client.subscribe(MQTT_TELEMETRY_TOPIC, qos=1)
        client.subscribe(MQTT_LOCATION_TOPIC, qos=1)
        print(f"[MQTT READER] Subscribed to {MQTT_TELEMETRY_TOPIC} and {MQTT_LOCATION_TOPIC}")
    else:
        print(f"[MQTT READER] Connect failed, rc={rc}")


def _on_message(client, userdata, msg):
    global _last_update_ts
    try:
        payload = json.loads(msg.payload.decode("utf-8"))
    except Exception as e:
        print(f"[MQTT READER] Bad payload on {msg.topic}: {e}")
        return

    with _lock:
        if msg.topic == MQTT_TELEMETRY_TOPIC:
            for esp_field, backend_field in _FIELD_MAP.items():
                if esp_field in payload:
                    _latest_reading[backend_field] = payload[esp_field]
            _last_update_ts = time.time()
        elif msg.topic == MQTT_LOCATION_TOPIC:
            _latest_location.update(payload)


def _on_disconnect(client, userdata, rc, properties=None):
    print(f"[MQTT READER] Disconnected, rc={rc} — paho will auto-reconnect")


def start_mqtt() -> bool:
    """Connect to the cloud broker and start the background network loop.
    Returns True once the connect call has been issued (connection itself
    happens asynchronously; readiness is reflected in is_receiving_data())."""
    global _client
    try:
        _client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="acdt_backend")
        _client.username_pw_set(MQTT_USER, MQTT_PASS)
        _client.tls_set(cert_reqs=ssl.CERT_NONE)   # matches ESP32's setInsecure() for now
        _client.tls_insecure_set(True)
        _client.on_connect    = _on_connect
        _client.on_message    = _on_message
        _client.on_disconnect = _on_disconnect
        _client.connect(MQTT_SERVER, MQTT_PORT, keepalive=60)
        _client.loop_start()   # runs networking in its own background thread
        return True
    except Exception as e:
        print(f"[MQTT READER] Could not start: {e}")
        return False


def is_receiving_data() -> bool:
    """True if a telemetry message has arrived within the last _STALE_AFTER_S seconds."""
    with _lock:
        return (time.time() - _last_update_ts) < _STALE_AFTER_S if _last_update_ts else False


def read_latest() -> dict:
    """Return the latest telemetry reading, filling any field the ESP32
    doesn't send (e.g. O2 sensor voltages) with None so the shape always
    matches SENSOR_FIELDS, same contract as the wired adapter path."""
    with _lock:
        data = dict(_latest_reading)
    for f in SENSOR_FIELDS:
        if f not in data:
            data[f] = None
    return data


def read_latest_location() -> dict:
    with _lock:
        return dict(_latest_location)

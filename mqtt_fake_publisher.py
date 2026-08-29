"""
mqtt_fake_publisher.py
Publishes fake OBD telemetry to the same MQTT topic/format the ESP32
bridge (esp32_obd_bridge.ino) uses, so you can test the full pipeline
before the car/adapter is available. Swap this off once the real
ESP32 is running — same topic, same field names, no backend changes needed.
"""

import json
import random
import time
import ssl
import paho.mqtt.client as mqtt

MQTT_SERVER = "77426ba5e0b34670b30bf6f00a354af9.s1.eu.hivemq.cloud"
MQTT_PORT = 8883
MQTT_USER = "acdt_esp32"
MQTT_PASS = "mmm@#100223"   # same as in the .env / .ino

ASSET_ID = "VIN_1234567890"
TELEMETRY_TOPIC = f"v2c/{ASSET_ID}/telemetry"
LOCATION_TOPIC = f"v2c/{ASSET_ID}/location"


def fake_telemetry():
    return {
        "engine_rpm": round(random.uniform(700, 3000), 2),
        "vehicle_speed": round(random.uniform(0, 100), 2),
        "coolant_temp": round(random.uniform(80, 100), 2),
        "engine_load": round(random.uniform(10, 60), 2),
        "throttle_position": round(random.uniform(0, 40), 2),
        "intake_air_temp": round(random.uniform(20, 40), 2),
        "mass_air_flow": round(random.uniform(2, 20), 2),
        "fuel_trim_short": round(random.uniform(-5, 5), 2),
        "fuel_trim_long": round(random.uniform(-5, 5), 2),
        "fuel_level": round(random.uniform(30, 90), 2),
        "barometric_pressure": round(random.uniform(95, 102), 2),
        "oil_temp": round(random.uniform(85, 105), 2),
        "battery_voltage": round(random.uniform(12.0, 14.5), 2),
        "latitude": 6.5244 + random.uniform(-0.01, 0.01),   # Lagos-ish
        "longitude": 3.3792 + random.uniform(-0.01, 0.01),
    }


def main():
    client = mqtt.Client(client_id="fake-publisher-test")
    client.username_pw_set(MQTT_USER, MQTT_PASS)
    client.tls_set(cert_reqs=ssl.CERT_NONE)
    client.tls_insecure_set(True)

    client.connect(MQTT_SERVER, MQTT_PORT, keepalive=60)
    client.loop_start()

    print(f"[FAKE] Publishing to {TELEMETRY_TOPIC} every 5s. Ctrl+C to stop.")
    try:
        while True:
            payload = fake_telemetry()
            client.publish(TELEMETRY_TOPIC, json.dumps(payload))
            print(f"[FAKE] Published: {payload}")

            loc = {
                "latitude": payload["latitude"],
                "longitude": payload["longitude"],
                "speed_kmh": payload["vehicle_speed"],
            }
            client.publish(LOCATION_TOPIC, json.dumps(loc))

            time.sleep(5)
    except KeyboardInterrupt:
        print("\n[FAKE] Stopping.")
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()

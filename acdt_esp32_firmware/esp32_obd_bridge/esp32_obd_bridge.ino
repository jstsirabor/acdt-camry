/*
  esp32_obd_bridge.ino
  ─────────────────────
  Bridges a WiFi ELM327 OBD-II adapter + GPS module to the ACDT backend
  over MQTT (HiveMQ Cloud), matching physical/mqtt_reader.py's expected
  topics and JSON field names exactly.

  IMPORTANT — single-radio WiFi limitation:
  The ELM327 WiFi adapter runs its own access point (typically
  192.168.0.10:35000). The ESP32 has only one WiFi radio, so it CANNOT
  be connected to the ELM327's AP and your home/hotspot WiFi (needed to
  reach the internet/MQTT broker) at the same time. This sketch handles
  that by cycling:

      1. Connect to ELM327 AP
      2. Query OBD PIDs over its TCP socket
      3. Disconnect from ELM327 AP
      4. Connect to home WiFi
      5. Publish the batch to MQTT
      6. Disconnect from home WiFi, go back to step 1

  This matches the "mid-cycle switching" comment already present in
  physical/obd_reader.py on the backend, and why is_receiving_data()
  there tolerates gaps up to 30s before falling back to the simulator
  for a single read — a full ELM327→publish cycle should comfortably
  finish well inside that window.

  GPS reads continuously in the background (independent of WiFi state)
  and the latest fix is included with each telemetry publish, and also
  published separately to the location topic.

  Libraries required (already installed per your Arduino IDE):
    - PubSubClient   (MQTT)
    - ArduinoJson    (payload building)
    - TinyGPSPlus    (GPS parsing)

  ── SPEED TUNING (this revision) ────────────────────────────────────
  The end-to-end cycle time was slow, dominated by:
    1. delay(5000) at the end of every loop() — pure dead time
    2. Conservative WiFi connect timeouts (worst-case ceilings, not
       typical times)
    3. Per-PID query timeout (3000ms) — only matters if a PID genuinely
       doesn't respond; on a healthy connection this rarely gets hit
    4. Auto protocol detection (ATSP0) triggering a multi-second
       "SEARCHING..." on the warm-up query, every single cycle, even
       though the vehicle's protocol never changes

  Tuned here: (1)-(3) shortened conservatively. (4) is now switchable —
  set FORCE_PROTOCOL below to your car's known protocol (find it once
  via the Serial log's warm-up response, or ELM327 docs) to skip the
  search entirely on every cycle. Leave FORCE_PROTOCOL empty/"0" to
  keep auto-detect (safe default, works but slower).
*/

#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <TinyGPSPlus.h>
#include <HardwareSerial.h>

// ── CONFIG — fill these in ──────────────────────────────────────────

// ELM327 WiFi adapter (its own AP — usually no password, or check yours)
const char* ELM_SSID     = "WIFI_OBDII";
const char* ELM_PASSWORD = "";
const char* ELM_HOST     = "192.168.0.10";
const uint16_t ELM_PORT  = 35000;

// Home / hotspot WiFi (needs real internet access to reach HiveMQ Cloud)
const char* HOME_SSID     = "Emperor Justus I";
const char* HOME_PASSWORD = "mmm100223";

// HiveMQ Cloud — must match shared/config.py exactly
const char* MQTT_SERVER = "77426ba5e0b34670b30bf6f00a354af9.s1.eu.hivemq.cloud";
const uint16_t MQTT_PORT = 8883;
const char* MQTT_USER = "acdt_esp32";
const char* MQTT_PASS = "mmm@#100223";

// Must match ASSET_ID in shared/config.py (default "VIN_1234567890")
const char* ASSET_ID = "VIN_1234567890";        // TODO confirm

// ── ELM327 forced protocol (speed tuning) ───────────────────────────
// Set this to skip the multi-second auto protocol search on every
// cycle. Common values: "6" = ISO 15765-4 CAN (11 bit, 500 kbaud) —
// the most common protocol on 2018+ vehicles including most Toyotas.
// Leave as "0" for auto-detect (slower but safest starting point).
// To find your car's actual protocol: run once with "0", check the
// Serial log for the warm-up response, then look up "ATDP" in ELM327
// docs, or just try "6" first since it's the most common modern one.
const char* FORCE_PROTOCOL = "0";   // "0" = auto, "6" = ISO 15765-4 CAN

String TELEMETRY_TOPIC;
String LOCATION_TOPIC;

// GPS module wiring — adjust to your actual pins
#define GPS_RX_PIN 16   // ESP32 RX  <- GPS TX
#define GPS_TX_PIN 17   // ESP32 TX  -> GPS RX
HardwareSerial GPSSerial(2);
TinyGPSPlus gps;

// Latest GPS fix, updated continuously in the background
volatile double g_lat = 0, g_lon = 0, g_speed_kmh = 0;
volatile bool g_gps_valid = false;

// One WiFiClientSecure reused for MQTT (TLS)
WiFiClientSecure secureClient;
PubSubClient mqttClient(secureClient);

// Buffer for a batch of PID readings collected from the ELM327
StaticJsonDocument<512> telemetry;

// ── OBD-II PIDs to poll — mapped to the exact field names
//    physical/mqtt_reader.py's _FIELD_MAP expects ──────────────────
struct PidEntry {
  const char* field;   // backend field name
  const char* pid;     // OBD-II PID (mode 01)
};

PidEntry PIDS[] = {
  {"engine_rpm",          "010C"},
  {"vehicle_speed",       "010D"},
  {"coolant_temp",        "0105"},
  {"engine_load",         "0104"},
  {"throttle_position",   "0111"},
  {"intake_air_temp",     "010F"},
  {"mass_air_flow",       "0110"},
  {"fuel_trim_short",     "0106"},
  {"fuel_trim_long",      "0107"},
  {"fuel_level",          "012F"},
  {"barometric_pressure", "0133"},
  {"oil_temp",            "015C"},   // not supported on all vehicles
  // battery_voltage is read via ELM327's own AT RV command, not a PID
};

const int NUM_PIDS = sizeof(PIDS) / sizeof(PIDS[0]);

// ─────────────────────────────────────────────────────────────────
// WiFi helpers
// ─────────────────────────────────────────────────────────────────

bool connectWiFi(const char* ssid, const char* password, uint32_t timeoutMs) {
  Serial.printf("[WIFI] Connecting to %s...\n", ssid);
  WiFi.disconnect(true);
  delay(200);
  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid, password);

  uint32_t start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < timeoutMs) {
    delay(250);
    Serial.print(".");
  }
  Serial.println();

  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("[WIFI] Connected to %s, IP: %s\n", ssid, WiFi.localIP().toString().c_str());
    return true;
  }
  Serial.printf("[WIFI] Failed to connect to %s, status code: %d\n", ssid, WiFi.status());
  // Status code reference: 0=IDLE 1=NO_SSID_AVAIL 3=CONNECTED 4=CONNECT_FAILED
  // 5=CONNECTION_LOST 6=DISCONNECTED (most common) 255=WL_NO_SHIELD
  return false;
}

// Scans for nearby networks and prints them — helps confirm whether
// the target SSID is actually visible/broadcasting right now.
void scanAndPrintNetworks(const char* lookingFor) {
  Serial.println("[WIFI] Scanning nearby networks...");
  int n = WiFi.scanNetworks();
  if (n == 0) {
    Serial.println("[WIFI] No networks found at all.");
  } else {
    bool found = false;
    for (int i = 0; i < n; i++) {
      String ssid = WiFi.SSID(i);
      Serial.printf("[WIFI]   %d: %s (RSSI %d)\n", i, ssid.c_str(), WiFi.RSSI(i));
      if (ssid == lookingFor) found = true;
    }
    Serial.printf("[WIFI] Target SSID '%s' %s in scan results.\n",
                  lookingFor, found ? "WAS FOUND" : "was NOT found");
  }
  WiFi.scanDelete();
}

// ─────────────────────────────────────────────────────────────────
// ELM327 (OBD-II over WiFi/TCP) helpers
// ─────────────────────────────────────────────────────────────────

WiFiClient elmClient;

bool elmSendCommand(const String& cmd, String& response, uint32_t timeoutMs = 1500) {
  if (!elmClient.connected()) return false;

  elmClient.print(cmd + "\r");
  response = "";
  uint32_t start = millis();
  while (millis() - start < timeoutMs) {
    while (elmClient.available()) {
      char c = elmClient.read();
      if (c == '>') {              // ELM327 prompt = end of response
        response.trim();
        return true;
      }
      response += c;
    }
  }
  response.trim();
  return response.length() > 0;
}

bool elmInit() {
  String resp;
  elmSendCommand("ATZ", resp, 3000);   // reset
  delay(300);
  elmSendCommand("ATE0", resp);        // echo off
  elmSendCommand("ATL0", resp);        // linefeeds off
  elmSendCommand("ATS0", resp);        // spaces off
  elmSendCommand("ATH0", resp);        // headers off

  bool usingForcedProtocol = FORCE_PROTOCOL[0] != '0';
  if (usingForcedProtocol) {
    String setProtoCmd = String("ATSP") + FORCE_PROTOCOL;
    elmSendCommand(setProtoCmd.c_str(), resp);
    Serial.printf("[ELM327] Forced protocol %s (skipping auto-detect search)\n", FORCE_PROTOCOL);

    // Even with a forced protocol, the very first query still needs a
    // bit more time than steady-state queries, but nowhere near the
    // multi-second auto-detect search — 3000ms is generous headroom.
    bool warmed = elmSendCommand("0100", resp, 3000);
    Serial.printf("[ELM327] Warm-up response (%s): %s\n", warmed ? "got reply" : "TIMED OUT", resp.c_str());
  } else {
    elmSendCommand("ATSP0", resp);       // auto protocol

    // First query after ATSP0 triggers a protocol search, which can take
    // several seconds ("SEARCHING..." before the real reply). If we don't
    // give this first query a long timeout, EVERY subsequent PID query
    // will silently fail even though the connection itself is fine — this
    // is why only ATRV (battery, no protocol search needed) was working.
    // Once you've confirmed your protocol from this warm-up response,
    // set FORCE_PROTOCOL above to skip this search on every future cycle.
    Serial.println("[ELM327] Warming up (auto-detect protocol search)...");
    bool warmed = elmSendCommand("0100", resp, 7000);
    Serial.printf("[ELM327] Warm-up response (%s): %s\n", warmed ? "got reply" : "TIMED OUT", resp.c_str());
  }
  return true;
}

// Parse a single-PID mode-01 response like "41 0C 1A F8" into a raw value
bool parsePidResponse(const String& raw, int expectedBytes, long& outValue) {
  // Strip any leading "41 XX " (mode+pid echo) and non-hex noise
  String hex = raw;
  hex.replace(" ", "");
  hex.replace("\r", "");
  hex.replace("\n", "");
  hex.toUpperCase();

  int idx = hex.indexOf("41");
  if (idx < 0) return false;

  // Skip "41" + PID (2 chars) to get to the data bytes
  int dataStart = idx + 2 + 2;
  if ((int)hex.length() < dataStart + expectedBytes * 2) return false;

  String dataHex = hex.substring(dataStart, dataStart + expectedBytes * 2);
  outValue = strtol(dataHex.c_str(), nullptr, 16);
  return true;
}

// Convert a raw PID value to the physical unit, per SAE J1979 formulas
float convertPid(const String& field, long raw, int numBytes) {
  if (field == "engine_rpm")          return raw / 4.0;
  if (field == "vehicle_speed")       return raw;                       // km/h, 1 byte
  if (field == "coolant_temp")        return raw - 40;
  if (field == "engine_load")         return raw * 100.0 / 255.0;
  if (field == "throttle_position")   return raw * 100.0 / 255.0;
  if (field == "intake_air_temp")     return raw - 40;
  if (field == "mass_air_flow")       return raw / 100.0;               // grams/sec
  if (field == "fuel_trim_short")     return (raw - 128) * 100.0 / 128.0;
  if (field == "fuel_trim_long")      return (raw - 128) * 100.0 / 128.0;
  if (field == "fuel_level")          return raw * 100.0 / 255.0;
  if (field == "barometric_pressure") return raw;                       // kPa
  if (field == "oil_temp")            return raw - 40;
  return raw;
}

bool pollElmBattery(float& voltage) {
  String resp;
  if (!elmSendCommand("ATRV", resp)) return false;
  resp.replace("V", "");
  resp.trim();
  voltage = resp.toFloat();
  return voltage > 0;
}

// Connects to the ELM327's own AP, polls all PIDs, fills `telemetry`,
// then disconnects. Returns true if at least one PID was read.
bool pollElm327IntoTelemetry() {
  telemetry.clear();
  telemetry["engine_rpm"] = nullptr; // placeholder so keys always exist

  if (!connectWiFi(ELM_SSID, ELM_PASSWORD, 6000)) {
    return false;
  }

  Serial.printf("[ELM327] Connecting to %s:%d...\n", ELM_HOST, ELM_PORT);
  if (!elmClient.connect(ELM_HOST, ELM_PORT)) {
    Serial.println("[ELM327] TCP connect failed");
    WiFi.disconnect(true);
    return false;
  }

  elmInit();

  int successCount = 0;
  for (int i = 0; i < NUM_PIDS; i++) {
    String resp;
    // 2s per PID — plenty once the protocol is already selected from
    // the warm-up query above; the search delay only happens once
    // (or never, if FORCE_PROTOCOL is set).
    bool gotReply = elmSendCommand(PIDS[i].pid, resp, 2000);
    Serial.printf("[ELM327] %s (%s) -> raw: \"%s\"\n",
                   PIDS[i].field, PIDS[i].pid, resp.c_str());

    if (gotReply) {
      long raw;
      // engine_rpm/mass_air_flow use 2 data bytes, most others use 1
      int expectedBytes = (strcmp(PIDS[i].field, "engine_rpm") == 0 ||
                            strcmp(PIDS[i].field, "mass_air_flow") == 0) ? 2 : 1;
      if (parsePidResponse(resp, expectedBytes, raw)) {
        float val = convertPid(PIDS[i].field, raw, expectedBytes);
        telemetry[PIDS[i].field] = val;
        successCount++;
      } else {
        Serial.printf("[ELM327]   ^ parse FAILED for %s\n", PIDS[i].field);
      }
    }
    delay(30);
  }

  float batteryV;
  if (pollElmBattery(batteryV)) {
    telemetry["battery_voltage"] = batteryV;
    successCount++;
  }

  elmClient.stop();
  WiFi.disconnect(true);
  Serial.printf("[ELM327] Read %d/%d fields\n", successCount, NUM_PIDS + 1);
  return successCount > 0;
}

// ─────────────────────────────────────────────────────────────────
// MQTT (HiveMQ Cloud) — publish over home WiFi
// ─────────────────────────────────────────────────────────────────

bool mqttConnectAndPublish() {
  if (!connectWiFi(HOME_SSID, HOME_PASSWORD, 7000)) {
    return false;
  }

  secureClient.setInsecure();  // matches backend's tls_insecure_set(True) for now
  mqttClient.setServer(MQTT_SERVER, MQTT_PORT);

  Serial.println("[MQTT] Connecting to broker...");
  String clientId = "esp32-" + String((uint32_t)ESP.getEfuseMac(), HEX);
  if (!mqttClient.connect(clientId.c_str(), MQTT_USER, MQTT_PASS)) {
    Serial.printf("[MQTT] Connect failed, rc=%d\n", mqttClient.state());
    WiFi.disconnect(true);
    return false;
  }
  Serial.println("[MQTT] Connected");

  // Attach the latest GPS fix to the telemetry payload as well
  if (g_gps_valid) {
    telemetry["latitude"]  = g_lat;
    telemetry["longitude"] = g_lon;
  }

  char payload[512];
  size_t n = serializeJson(telemetry, payload, sizeof(payload));
  bool ok = mqttClient.publish(TELEMETRY_TOPIC.c_str(), (const uint8_t*)payload, n, false);
  Serial.printf("[MQTT] Published OBD data (%s): %s\n", ok ? "ok" : "FAILED", payload);

  if (g_gps_valid) {
    StaticJsonDocument<128> loc;
    loc["latitude"]  = g_lat;
    loc["longitude"] = g_lon;
    loc["speed_kmh"] = g_speed_kmh;
    char locPayload[128];
    size_t ln = serializeJson(loc, locPayload, sizeof(locPayload));
    mqttClient.publish(LOCATION_TOPIC.c_str(), (const uint8_t*)locPayload, ln, false);
  }

  mqttClient.loop();
  delay(150);
  mqttClient.disconnect();
  WiFi.disconnect(true);
  return ok;
}

// ─────────────────────────────────────────────────────────────────
// GPS — read continuously regardless of which WiFi we're on
// ─────────────────────────────────────────────────────────────────

void pollGps() {
  while (GPSSerial.available() > 0) {
    if (gps.encode(GPSSerial.read())) {
      if (gps.location.isValid()) {
        g_lat = gps.location.lat();
        g_lon = gps.location.lng();
        g_gps_valid = true;
      }
      if (gps.speed.isValid()) {
        g_speed_kmh = gps.speed.kmph();
      }
    }
  }
}

// ─────────────────────────────────────────────────────────────────
// Setup / loop
// ─────────────────────────────────────────────────────────────────

void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println("\n[ESP32] ACDT OBD-II bridge starting...");

  // PubSubClient defaults to a 256-byte MQTT packet buffer, which is too
  // small once real OBD data fills in — bump it up before first use.
  mqttClient.setBufferSize(1024);

  GPSSerial.begin(9600, SERIAL_8N1, GPS_RX_PIN, GPS_TX_PIN);

  TELEMETRY_TOPIC = String("v2c/") + ASSET_ID + "/telemetry";
  LOCATION_TOPIC  = String("v2c/") + ASSET_ID + "/location";
  Serial.printf("[ESP32] Telemetry topic: %s\n", TELEMETRY_TOPIC.c_str());
  Serial.printf("[ESP32] Location topic:  %s\n", LOCATION_TOPIC.c_str());
}

void loop() {
  // Keep GPS parsing continuously between cycles (best-effort — GPS
  // wiring should be independent of WiFi radio, so this runs whenever
  // loop() gets CPU time between the blocking WiFi steps below)
  pollGps();

  Serial.println("\n[CYCLE] Step 1/2 — polling ELM327...");
  scanAndPrintNetworks(ELM_SSID);
  bool gotObd = pollElm327IntoTelemetry();

  if (!gotObd) {
    Serial.println("[CYCLE] No OBD data this cycle — will retry next loop");
  }

  pollGps();

  Serial.println("[CYCLE] Step 2/2 — publishing over home WiFi...");
  bool published = mqttConnectAndPublish();

  if (!published) {
    Serial.println("[CYCLE] Publish failed — will retry next loop");
  }

  // Backend treats data older than 30s as stale, so keep full cycles
  // comfortably under that. Shortened from 5000ms — this was pure dead
  // time at the end of every cycle. Adjust based on how long your
  // ELM327 poll + MQTT publish actually takes in practice (watch the
  // Serial logs) — don't go so low that cycles start overlapping.
  delay(1500);
}

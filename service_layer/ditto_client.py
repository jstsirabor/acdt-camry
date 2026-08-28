"""
service_layer/ditto_client.py
──────────────────────────────
Eclipse Ditto client — creates and updates the vehicle digital twin,
and (separately) provisions the MECHANIC's digital twin on the second
Ditto stack (mechanic-ditto-*, ports 8090/8091).
"""
import httpx
from shared.config import (
    DITTO_URL, DITTO_USER, DITTO_PASSWORD, ASSET_ID, ASSET_TYPE,
    MECHANIC_DITTO_URL, MECHANIC_DITTO_USER, MECHANIC_DITTO_PASSWORD,
    MECHANIC_THING_ID,
)
AUTH    = (DITTO_USER, DITTO_PASSWORD)
TIMEOUT = httpx.Timeout(30.0)

MECHANIC_AUTH = (MECHANIC_DITTO_USER, MECHANIC_DITTO_PASSWORD)


def build_thing_template() -> dict:
    return {
        "thingId":  f"org.example:{ASSET_ID}",
        "policyId": "org.example:default_policy",
        "attributes": {
            "name":         "2018 Toyota Camry",
            "manufacturer": "Toyota",
            "model":        "Camry 2018 ICE",
            "vin":          ASSET_ID,
            "asset_type":   ASSET_TYPE,
        },
        "features": {
            "telemetry": {"properties": {}},
            "health":    {"properties": {"score": 100, "status": "nominal"}},
            "control":   {"properties": {"mode": "normal"}},
        },
    }


def provision_ditto():
    """Create policy and thing for the VEHICLE twin — idempotent."""
    policy = {
        "policyId": "org.example:default_policy",
        "entries": {
            "DEFAULT": {
                "subjects": {"ditto:ditto": {"type": "nginx basic auth user"}},
                "resources": {
                    "thing:/":   {"grant": ["READ", "WRITE"], "revoke": []},
                    "policy:/":  {"grant": ["READ", "WRITE"], "revoke": []},
                    "message:/": {"grant": ["READ", "WRITE"], "revoke": []},
                },
            }
        },
    }
    try:
        r = httpx.put(
            f"{DITTO_URL}/api/2/policies/org.example:default_policy",
            auth=AUTH, timeout=TIMEOUT, json=policy,
        )
        print(f"[DITTO] Policy: {r.status_code}")
        thing = build_thing_template()
        r = httpx.put(
            f"{DITTO_URL}/api/2/things/{thing['thingId']}",
            auth=AUTH, timeout=TIMEOUT, json=thing,
        )
        print(f"[DITTO] Thing: {r.status_code}")
    except Exception as e:
        print(f"[DITTO] Provisioning error: {e}")


def build_mechanic_thing_template() -> dict:
    return {
        "thingId":  MECHANIC_THING_ID,
        "policyId": "org.example:mechanic_default_policy",
        "attributes": {
            "name":     "Default Mechanic",
            "workshop": "ACDT Service Console",
        },
        "features": {
            "emergency_queue": {
                "properties": {
                    "active_emergencies": [],
                    "last_updated":       None,
                }
            },
            "maintenance_queue": {
                "properties": {
                    "pending_services": [],
                    "last_updated":     None,
                }
            },
        },
    }


def provision_mechanic_ditto():
    """Create policy and thing for the MECHANIC twin on the mechanic's
    own Ditto stack (separate instance, port 8090/8091) — idempotent.

    Without this, push_to_mechanic()/_get_queue() in mechanic_client.py
    will always hit 'thing.notfound' (404) even though the Ditto
    gateway itself is reachable — is_mechanic_connected() treats 404
    as 'connected' (correct for gateway reachability) but that doesn't
    mean the Thing has actually been created yet.
    """
    policy = {
        "policyId": "org.example:mechanic_default_policy",
        "entries": {
            "DEFAULT": {
                "subjects": {"ditto:ditto": {"type": "nginx basic auth user"}},
                "resources": {
                    "thing:/":   {"grant": ["READ", "WRITE"], "revoke": []},
                    "policy:/":  {"grant": ["READ", "WRITE"], "revoke": []},
                    "message:/": {"grant": ["READ", "WRITE"], "revoke": []},
                },
            }
        },
    }
    try:
        r = httpx.put(
            f"{MECHANIC_DITTO_URL}/api/2/policies/org.example:mechanic_default_policy",
            auth=MECHANIC_AUTH, timeout=TIMEOUT, json=policy,
        )
        print(f"[DITTO] Mechanic policy: {r.status_code}")
        thing = build_mechanic_thing_template()
        r = httpx.put(
            f"{MECHANIC_DITTO_URL}/api/2/things/{thing['thingId']}",
            auth=MECHANIC_AUTH, timeout=TIMEOUT, json=thing,
        )
        print(f"[DITTO] Mechanic thing: {r.status_code}")
    except Exception as e:
        print(f"[DITTO] Mechanic provisioning error: {e}")


def update_telemetry(fields: dict):
    try:
        httpx.patch(
            f"{DITTO_URL}/api/2/things/org.example:{ASSET_ID}/features/telemetry/properties",
            auth=AUTH, timeout=TIMEOUT, json=fields,
        )
    except Exception:
        pass


def update_health(score: float, status: str):
    try:
        httpx.patch(
            f"{DITTO_URL}/api/2/things/org.example:{ASSET_ID}/features/health/properties",
            auth=AUTH, timeout=TIMEOUT,
            json={"score": round(score, 1), "status": status},
        )
    except Exception:
        pass


def get_thing() -> dict:
    try:
        r = httpx.get(
            f"{DITTO_URL}/api/2/things/org.example:{ASSET_ID}",
            auth=AUTH, timeout=TIMEOUT,
        )
        return r.json()
    except Exception as e:
        return {"error": str(e)}

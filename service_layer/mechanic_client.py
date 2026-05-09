"""
service_layer/mechanic_client.py
─────────────────────────────────
Mechanic Client — connects to a REMOTE mechanic's
Eclipse Ditto instance and pushes emergency/maintenance
packets to their digital twin.

The mechanic runs their OWN ACDT instance. You connect
to their Ditto endpoint using their credentials.
Configure via .env:
  MECHANIC_DITTO_URL      = https://mechanic.acdt.local:8080
  MECHANIC_DITTO_USER     = ditto
  MECHANIC_DITTO_PASSWORD = ditto
  MECHANIC_THING_ID       = org.example:MECHANIC_001
"""
import os
import httpx
from datetime import datetime, timezone
from dotenv import load_dotenv
load_dotenv()

# ── Remote mechanic Ditto config (from .env) ───────────────────────
MECHANIC_URL      = os.getenv("MECHANIC_DITTO_URL",      "http://localhost:8080")
MECHANIC_USER     = os.getenv("MECHANIC_DITTO_USER",     "ditto")
MECHANIC_PASSWORD = os.getenv("MECHANIC_DITTO_PASSWORD", "ditto")
MECHANIC_THING_ID = os.getenv("MECHANIC_THING_ID",       "org.example:MECHANIC_001")

AUTH    = (MECHANIC_USER, MECHANIC_PASSWORD)
TIMEOUT = httpx.Timeout(15.0)


def is_mechanic_connected() -> bool:
    """Check if the remote mechanic Ditto is reachable."""
    try:
        r = httpx.get(
            f"{MECHANIC_URL}/api/2/things/{MECHANIC_THING_ID}",
            auth=AUTH, timeout=httpx.Timeout(5.0),
        )
        return r.status_code in (200, 404)
    except Exception:
        return False


def push_to_mechanic(queue_type: str, packet: dict):
    """
    Push a packet to the remote mechanic twin.
    queue_type: 'emergency' or 'maintenance'
    """
    if not is_mechanic_connected():
        print(f"[MECHANIC CLIENT] Remote mechanic Ditto unreachable — storing locally")
        _store_locally(queue_type, packet)
        return

    feature   = "emergency_queue"   if queue_type == "emergency"    else "maintenance_queue"
    prop_key  = "active_emergencies" if queue_type == "emergency"   else "pending_services"

    try:
        # Get current queue from remote mechanic twin
        r = httpx.get(
            f"{MECHANIC_URL}/api/2/things/{MECHANIC_THING_ID}"
            f"/features/{feature}/properties",
            auth=AUTH, timeout=TIMEOUT,
        )
        props = r.json() if r.status_code == 200 else {}
        queue = props.get(prop_key, [])

        # Add new packet (keep last 20)
        queue.insert(0, packet)
        queue = queue[:20]

        # Push back to remote mechanic Ditto
        r = httpx.patch(
            f"{MECHANIC_URL}/api/2/things/{MECHANIC_THING_ID}"
            f"/features/{feature}/properties",
            auth=AUTH, timeout=TIMEOUT,
            json={
                prop_key:     queue,
                "last_updated": datetime.now(timezone.utc).isoformat(),
                "from_vehicle": os.getenv("ASSET_ID", "VIN_1234567890"),
            },
        )
        print(f"[MECHANIC CLIENT] Pushed {queue_type} to remote mechanic — status: {r.status_code}")

    except Exception as e:
        print(f"[MECHANIC CLIENT] Push failed: {e} — storing locally")
        _store_locally(queue_type, packet)


def _store_locally(queue_type: str, packet: dict):
    """Fallback — store packet in MongoDB if mechanic is unreachable."""
    try:
        from shared.mongo_io import log_event
        log_event(
            f"mechanic_offline_{queue_type}",
            packet,
            severity=packet.get("severity", "info"),
        )
        print(f"[MECHANIC CLIENT] Stored locally in MongoDB (mechanic offline)")
    except Exception as e:
        print(f"[MECHANIC CLIENT] Local fallback failed: {e}")


def get_mechanic_status() -> dict:
    """Get connection status and queue sizes from remote mechanic twin."""
    connected = is_mechanic_connected()
    if not connected:
        return {
            "connected":   False,
            "mechanic_url": MECHANIC_URL,
            "thing_id":    MECHANIC_THING_ID,
            "message":     "Remote mechanic Ditto unreachable",
        }
    try:
        r = httpx.get(
            f"{MECHANIC_URL}/api/2/things/{MECHANIC_THING_ID}",
            auth=AUTH, timeout=TIMEOUT,
        )
        thing = r.json()
        features = thing.get("features", {})
        eq = features.get("emergency_queue",  {}).get("properties", {})
        mq = features.get("maintenance_queue", {}).get("properties", {})
        return {
            "connected":          True,
            "mechanic_url":       MECHANIC_URL,
            "thing_id":           MECHANIC_THING_ID,
            "mechanic_name":      thing.get("attributes", {}).get("name", "Unknown"),
            "workshop":           thing.get("attributes", {}).get("workshop", "Unknown"),
            "emergency_count":    len(eq.get("active_emergencies", [])),
            "maintenance_count":  len(mq.get("pending_services", [])),
            "last_emergency":     eq.get("last_updated"),
            "last_maintenance":   mq.get("last_updated"),
        }
    except Exception as e:
        return {"connected": False, "error": str(e)}

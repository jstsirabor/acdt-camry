"""
autonomous/messenger.py
────────────────────────
Driver-facing proactive messages — pushed into the same Redis list
("driver:messages") that /api/chat writes driver messages into, and
that /api/messages reads back out for the dashboard polling loop
(fetchMessages() in dashboard.html).

Driver message shape (must match what agent_api.py already writes):
  {
    "role":      "acdt" | "driver",
    "content":   str,
    "timestamp": ISO8601 str,
    "type":      "critical" | "warning" | "info" | "journey" | "all_clear" | "driver",
    "source":    "autonomous" | "chat" | "obd_reader",
  }

dashboard.html only renders entries where source is "autonomous" or
"obd_reader" AND role != "driver" — so every push_* below uses
role="acdt", source="autonomous".

Mechanic-facing functions (push_to_mechanic, get_mechanic_status, etc.)
live in service_layer/mechanic_client.py. They're re-exported here so
existing `from autonomous.messenger import push_to_mechanic`-style
imports keep working without duplicating that logic.
"""
import json
from datetime import datetime, timezone

import redis

from shared.config import REDIS_HOST, REDIS_PORT

# Re-export mechanic-facing functions so any existing imports from
# autonomous.messenger continue to work.
from service_layer.mechanic_client import (  # noqa: F401
    is_mechanic_connected,
    push_to_mechanic,
    get_mechanic_status,
    get_emergency_queue,
    get_maintenance_queue,
)

# ── Driver message store (Redis) ────────────────────────────────────
DRIVER_MESSAGES_KEY = "driver:messages"
MAX_DRIVER_MESSAGES = 200  # trim the Redis list so it doesn't grow forever


def _redis():
    return redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)


def _push_driver_message(content: str, msg_type: str, source: str = "autonomous"):
    """Write a proactive message into the same Redis list the driver
    chat uses, trimmed to MAX_DRIVER_MESSAGES most-recent entries."""
    msg = {
        "role":      "acdt",
        "content":   content,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "type":      msg_type,
        "source":    source,
    }
    try:
        r = _redis()
        r.lpush(DRIVER_MESSAGES_KEY, json.dumps(msg))
        r.ltrim(DRIVER_MESSAGES_KEY, 0, MAX_DRIVER_MESSAGES - 1)
    except Exception as e:
        print(f"[MESSENGER] Failed to push driver message: {e}")


def push_critical(content: str):
    """Urgent, must-see-now alert (e.g. pull over immediately)."""
    _push_driver_message(content, "critical")


def push_warning(content: str):
    """Attention-needed but non-urgent alert."""
    _push_driver_message(content, "warning")


def push_info(content: str):
    """Informational, low-priority update."""
    _push_driver_message(content, "info")


def push_journey_prompt(fuel_msg: str = ""):
    """Sent when a journey start is detected (speed > 5 km/h)."""
    content = f"{fuel_msg}Heading out? Let me know your destination and I can check the route for you."
    _push_driver_message(content, "journey")


def push_all_clear():
    """Periodic reassurance message when nothing is wrong."""
    _push_driver_message("✅ All systems nominal — no issues to report.", "all_clear")


def get_recent_messages(limit: int = 50) -> list:
    """Return the most recent driver-facing messages, oldest→newest
    (dashboard.html reverses whatever /api/messages returns, so this
    matches how agent_api.py already expects the list ordered)."""
    try:
        r    = _redis()
        raw  = r.lrange(DRIVER_MESSAGES_KEY, 0, limit - 1)  # newest-first in Redis
        msgs = [json.loads(m) for m in raw]
        return list(reversed(msgs))  # oldest-first
    except Exception as e:
        print(f"[MESSENGER] Failed to fetch messages: {e}")
        return []


def clear_messages():
    """Clear the driver message list (used by /api/messages/clear)."""
    try:
        _redis().delete(DRIVER_MESSAGES_KEY)
    except Exception as e:
        print(f"[MESSENGER] Failed to clear messages: {e}")

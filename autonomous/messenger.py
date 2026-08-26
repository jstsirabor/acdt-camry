"""
autonomous/messenger.py
────────────────────────
Proactive messenger — decides when and what to send
to the driver chat without being asked.

Rules:
- CRITICAL finding    → message immediately, always
- WARNING finding     → message if last warning was >30 min ago
- INFO / routine      → message once per hour max
- Journey started     → ask destination, check weather + fuel
- All clear 2hrs      → brief check-in message
"""
import json
import time
from datetime import datetime, timezone, timedelta
from shared.config import REDIS_HOST, REDIS_PORT

_last_sent = {
    "critical":    None,
    "warning":     None,
    "info":        None,
    "journey":     None,
}
WARNING_COOLDOWN  = 30 * 60   # 30 minutes in seconds
INFO_COOLDOWN     = 60 * 60   # 1 hour


def _get_redis():
    import redis
    return redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)


def push_message(content: str, role: str = "acdt",
                 msg_type: str = "info", force: bool = False):
    """
    Push a proactive message to the driver chat.
    Respects cooldown periods unless force=True.
    """
    now = datetime.now(timezone.utc)

    if not force:
        last = _last_sent.get(msg_type)
        if last:
            elapsed = (now - last).total_seconds()
            if msg_type == "warning"  and elapsed < WARNING_COOLDOWN:
                return
            if msg_type == "info"     and elapsed < INFO_COOLDOWN:
                return

    _last_sent[msg_type] = now

    msg = {
        "role":      role,
        "content":   content,
        "timestamp": now.isoformat(),
        "type":      msg_type,
        "source":    "autonomous",
    }

    try:
        r = _get_redis()
        r.lpush("driver:messages", json.dumps(msg))
        r.ltrim("driver:messages", 0, 199)
        print(f"[MESSENGER] 💬 Sent {msg_type} message to driver")
    except Exception as e:
        print(f"[MESSENGER] Failed to push message: {e}")


def push_critical(content: str):
    """Always sends — no cooldown for critical alerts."""
    push_message(content, msg_type="critical", force=True)


def push_warning(content: str):
    """Sends with 30-minute cooldown."""
    push_message(content, msg_type="warning")


def push_info(content: str):
    """Sends with 1-hour cooldown."""
    push_message(content, msg_type="info")


def push_journey_prompt(fuel_status: str, weather: str = ""):
    """Ask driver for destination when journey is detected."""
    now = datetime.now(timezone.utc)
    last = _last_sent.get("journey")
    if last and (now - last).total_seconds() < 3600:
        return
    _last_sent["journey"] = now

    msg = (
        "It looks like you're about to head out. "
        f"{fuel_status} "
        "Where are you headed? I can check the weather on your route, "
        "estimate if you have enough fuel, and flag any concerns before you leave."
    )
    if weather:
        msg += f"\n\nCurrent conditions nearby: {weather}"

    push_message(msg, msg_type="journey", force=True)


def push_all_clear():
    """Send a brief all-clear update if nothing has been sent in 2 hours."""
    now  = datetime.now(timezone.utc)
    last = max(
        (t for t in _last_sent.values() if t is not None),
        default=None
    )
    if last and (now - last).total_seconds() < 7200:
        return
    push_info(
        "Everything looks good. All sensors are within normal limits "
        "and no maintenance issues require immediate attention. "
        "Have a safe drive."
    )


def get_recent_messages(limit: int = 50) -> list:
    """Get recent messages for the driver chat UI."""
    try:
        r    = _get_redis()
        msgs = r.lrange("driver:messages", 0, limit - 1)
        return [json.loads(m) for m in msgs]
    except Exception:
        return []


def clear_messages():
    """Clear the message queue."""
    try:
        _get_redis().delete("driver:messages")
    except Exception:
        pass

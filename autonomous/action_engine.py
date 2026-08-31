"""
autonomous/action_engine.py
────────────────────────────
Action Engine — executes autonomous actions when agents
detect critical or warning conditions. Pushes to a
remote mechanic digital twin over HTTP.
"""
import re
from datetime import datetime, timezone
from shared.mongo_io import log_event
from shared.redis_io import cache_agent_alert

CRITICAL_KEYWORDS = ["EMERGENCY", "CRITICAL", "Pull over", "stop the vehicle", "stop driving"]
WARNING_KEYWORDS  = ["WARNING", "OVERDUE", "DUE SOON", "borderline", "at risk"]

RATING_RE = re.compile(
    r"overall\s+safety\s+rating\**\s*:?\**\s*(critical|warning|safe)",
    re.IGNORECASE,
)

COOLDOWN_SECONDS = 1800
_last_push_state = {
    "safety":      {"severity": None, "at": None},
    "maintenance": {"severity": None, "at": None},
}


def _should_push(channel: str, severity: str) -> bool:
    state = _last_push_state[channel]
    now = datetime.now(timezone.utc)

    if state["severity"] != severity:
        state["severity"] = severity
        state["at"] = now
        return True

    if state["at"] is None or (now - state["at"]).total_seconds() >= COOLDOWN_SECONDS:
        state["at"] = now
        return True

    return False


def classify_severity(text: str) -> str:
    if text.strip().startswith("Safety Agent error"):
        return "warning"

    match = RATING_RE.search(text)
    if match:
        rating = match.group(1).lower()
        return "info" if rating == "safe" else rating

    upper = text.upper()
    if any(k.upper() in upper for k in CRITICAL_KEYWORDS):
        return "critical"
    if any(k.upper() in upper for k in WARNING_KEYWORDS):
        return "warning"
    return "info"


def act_on_safety(report: str):
    severity = classify_severity(report)
    if severity == "critical":
        if not _should_push("safety", "critical"):
            print("[ACTION ENGINE] 🚨 CRITICAL — duplicate within cooldown, skipping mechanic push")
            return
        print("[ACTION ENGINE] 🚨 CRITICAL — escalating to remote mechanic twin")
        packet = {
            "type":         "safety_emergency",
            "severity":     "critical",
            "timestamp":    datetime.now(timezone.utc).isoformat(),
            "report":       report,
            "action_taken": "emergency_alert_sent_to_mechanic",
            "recommended":  "Immediate vehicle inspection required",
        }
        log_event("autonomous_safety_action", packet, severity="critical")
        cache_agent_alert("safety", f"🚨 CRITICAL: {report[:200]}")
        _push_to_mechanic("emergency", packet)
    elif severity == "warning":
        if not _should_push("safety", "warning"):
            print("[ACTION ENGINE] ⚠ WARNING — duplicate within cooldown, skipping mechanic push")
            return
        print("[ACTION ENGINE] ⚠ WARNING — notifying remote mechanic twin")
        packet = {
            "type":         "safety_warning",
            "severity":     "warning",
            "timestamp":    datetime.now(timezone.utc).isoformat(),
            "report":       report,
            "action_taken": "warning_logged_and_mechanic_notified",
        }
        log_event("autonomous_safety_warning", packet, severity="warning")
        cache_agent_alert("safety", f"⚠ WARNING: {report[:200]}")
        _push_to_mechanic("emergency", packet)
    else:
        _last_push_state["safety"] = {"severity": None, "at": None}
        print("[ACTION ENGINE] ✅ Safety check passed — no action needed")
        log_event("autonomous_safety_check", {
            "severity":  "info",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "report":    report[:200],
        }, severity="info")
        cache_agent_alert("safety", "✅ SAFE: All sensors within normal limits.")


def act_on_maintenance(report: str):
    severity = classify_severity(report)
    if severity in ("critical", "warning"):
        if not _should_push("maintenance", severity):
            print(f"[ACTION ENGINE] 🔧 Maintenance {severity} — duplicate within cooldown, skipping mechanic push")
            return
        print(f"[ACTION ENGINE] 🔧 Maintenance {severity} — notifying remote mechanic twin")
        packet = {
            "type":         "maintenance_alert",
            "severity":     severity,
            "timestamp":    datetime.now(timezone.utc).isoformat(),
            "report":       report,
            "action_taken": "maintenance_schedule_sent_to_mechanic",
        }
        log_event("autonomous_maintenance_action", packet, severity=severity)
        cache_agent_alert("preventive", f"🔧 {severity.upper()}: {report[:200]}")
        _push_to_mechanic("maintenance", packet)
    else:
        _last_push_state["maintenance"] = {"severity": None, "at": None}
        print("[ACTION ENGINE] ✅ Maintenance check passed")
        cache_agent_alert("preventive", "✅ OK: No maintenance issues detected.")


def _push_to_mechanic(queue_type: str, packet: dict):
    """Push packet to the remote mechanic twin via mechanic_client."""
    try:
        from service_layer.mechanic_client import push_to_mechanic
        push_to_mechanic(queue_type, packet)
    except Exception as e:
        print(f"[ACTION ENGINE] Mechanic push failed: {e}")

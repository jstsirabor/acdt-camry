"""
autonomous/action_engine.py
────────────────────────────
Action Engine — executes autonomous actions when agents
detect critical or warning conditions. Pushes to a
remote mechanic digital twin over HTTP.
"""
from datetime import datetime, timezone
from shared.mongo_io import log_event
from shared.redis_io import cache_agent_alert

CRITICAL_KEYWORDS = ["EMERGENCY", "CRITICAL", "Pull over", "stop the vehicle", "stop driving"]
WARNING_KEYWORDS  = ["WARNING", "OVERDUE", "DUE SOON", "borderline", "at risk"]


def classify_severity(text: str) -> str:
    upper = text.upper()
    if any(k.upper() in upper for k in CRITICAL_KEYWORDS):
        return "critical"
    if any(k.upper() in upper for k in WARNING_KEYWORDS):
        return "warning"
    return "info"

def act_on_safety(report: str):
    severity = classify_severity(report)

    if severity == "critical":
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
        # SAFE — just log locally, do NOT push to mechanic
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
        print("[ACTION ENGINE] ✅ Maintenance check passed")
        cache_agent_alert("preventive", "✅ OK: No maintenance issues detected.")


def _push_to_mechanic(queue_type: str, packet: dict):
    """Push packet to the remote mechanic twin via mechanic_client."""
    try:
        from service_layer.mechanic_client import push_to_mechanic
        push_to_mechanic(queue_type, packet)
    except Exception as e:
        print(f"[ACTION ENGINE] Mechanic push failed: {e}")

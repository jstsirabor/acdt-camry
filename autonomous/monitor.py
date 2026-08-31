"""
autonomous/monitor.py
──────────────────────
Autonomous Monitor v3 — runs agents continuously and
pushes proactive messages to the driver chat.

Safety:      every 300 seconds
Maintenance: every 900 seconds
Journey:     detects speed > 5 km/h and prompts for destination
All-clear:   every 2 hours if nothing to report
"""
import time
import threading
from intelligent.safety_agent import run_safety_check
from intelligent.preventive_agent import run_preventive_check
from autonomous.action_engine import act_on_safety, act_on_maintenance
from autonomous.messenger import (
    push_critical, push_warning, push_info,
    push_journey_prompt, push_all_clear,
)

SAFETY_INTERVAL      = 300
MAINTENANCE_INTERVAL = 900
STARTUP_DELAY        = 15
_journey_active      = False


def _classify(text: str) -> str:
    upper = text.upper()
    if any(k in upper for k in ["EMERGENCY", "CRITICAL", "PULL OVER", "STOP THE VEHICLE"]):
        return "critical"
    if any(k in upper for k in ["WARNING", "OVERDUE", "DUE SOON", "BORDERLINE", "AT RISK"]):
        return "warning"
    return "info"


def _safety_loop():
    global _journey_active
    time.sleep(STARTUP_DELAY)
    print("[MONITOR] 🛡 Safety monitoring active")
    while True:
        try:
            report   = run_safety_check(
                "Autonomous check: assess all sensors. Be concise. "
                "Flag any critical or warning conditions plainly."
            )
            severity = _classify(report)
            act_on_safety(report)

            # Push to driver chat
            if severity == "critical":
                push_critical(
                    f"🚨 Urgent vehicle alert:\n\n{_clean(report)}\n\n"
                    "Please pull over safely as soon as possible."
                )
            elif severity == "warning":
                push_warning(
                    f"⚠️ Vehicle attention needed:\n\n{_clean(report)}"
                )
            else:
                push_all_clear()

            # Journey detection
            from shared.influx_io import get_latest
            speed = get_latest("vehicle_speed")
            fuel  = get_latest("fuel_level")
            if speed and speed > 5 and not _journey_active:
                _journey_active = True
                fuel_msg = ""
                if fuel is not None:
                    if fuel < 15:
                        fuel_msg = f"⚠️ Your fuel is low at {fuel:.0f}%. "
                    else:
                        fuel_msg = f"Your fuel level is {fuel:.0f}%. "
                push_journey_prompt(fuel_msg)
            elif speed and speed < 2:
                _journey_active = False

        except Exception as e:
            print(f"[MONITOR] Safety error: {e}")
        time.sleep(SAFETY_INTERVAL)


def _maintenance_loop():
    time.sleep(STARTUP_DELAY + 10)
    print("[MONITOR] 🔧 Maintenance monitoring active")
    while True:
        try:
            report   = run_preventive_check(
                "Autonomous check: assess maintenance schedule and wear. "
                "Be concise. Only flag overdue or at-risk items."
            )
            severity = _classify(report)
            act_on_maintenance(report)

            if severity in ("critical", "warning"):
                push_warning(
                    f"🔧 Maintenance update:\n\n{_clean(report)}\n\n"
                    "Your mechanic has been notified."
                )

        except Exception as e:
            print(f"[MONITOR] Maintenance error: {e}")
        time.sleep(MAINTENANCE_INTERVAL)


def _clean(text: str) -> str:
    """Strip excessive markdown for chat display."""
    import re
    text = re.sub(r'\*{1,3}', '', text)
    text = re.sub(r'#{1,6}\s?', '', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def start_autonomous_monitor():
    t1 = threading.Thread(target=_safety_loop,     daemon=True, name="SafetyMonitor")
    t2 = threading.Thread(target=_maintenance_loop, daemon=True, name="MaintenanceMonitor")
    t1.start()
    t2.start()
    print("[MONITOR] Autonomous monitor started")
    return t1, t2

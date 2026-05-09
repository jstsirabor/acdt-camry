"""
autonomous/monitor.py
──────────────────────
Autonomous Monitor — runs Safety and Preventive agents
continuously in background threads without user input.

Safety check:     every 30 seconds
Maintenance check: every 120 seconds
"""
import time
import threading
from intelligent.safety_agent import run_safety_check
from intelligent.preventive_agent import run_preventive_check
from autonomous.action_engine import act_on_safety, act_on_maintenance

SAFETY_INTERVAL      = 30
MAINTENANCE_INTERVAL = 120
STARTUP_DELAY        = 15


def _safety_loop():
    time.sleep(STARTUP_DELAY)
    print("[MONITOR] 🛡 Safety monitoring active — checking every 30s")
    while True:
        try:
            print("[MONITOR] Running autonomous safety check...")
            report = run_safety_check(
                "Autonomous check: assess all sensors for safety violations. "
                "Be concise. Flag any critical or warning conditions."
            )
            act_on_safety(report)
        except Exception as e:
            print(f"[MONITOR] Safety error: {e}")
        time.sleep(SAFETY_INTERVAL)


def _maintenance_loop():
    time.sleep(STARTUP_DELAY + 10)
    print("[MONITOR] 🔧 Maintenance monitoring active — checking every 120s")
    while True:
        try:
            print("[MONITOR] Running autonomous maintenance check...")
            report = run_preventive_check(
                "Autonomous check: assess maintenance schedule and sensor wear. "
                "Be concise. Flag anything overdue or at risk."
            )
            act_on_maintenance(report)
        except Exception as e:
            print(f"[MONITOR] Maintenance error: {e}")
        time.sleep(MAINTENANCE_INTERVAL)


def start_autonomous_monitor():
    t1 = threading.Thread(target=_safety_loop,      daemon=True, name="SafetyMonitor")
    t2 = threading.Thread(target=_maintenance_loop,  daemon=True, name="MaintenanceMonitor")
    t1.start()
    t2.start()
    print("[MONITOR] Autonomous monitor started")
    return t1, t2

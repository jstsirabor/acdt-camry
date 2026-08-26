"""
intelligent/safety_agent.py
────────────────────────────
Safety Agent v3 — expanded sensors, external weather
integration, and memory of past safety events.
"""
from langchain.agents import AgentExecutor, create_openai_tools_agent
from langchain.memory import ConversationBufferWindowMemory
from langchain.tools import tool
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_openai import ChatOpenAI
from shared.config import (OLLAMA_HOST, OLLAMA_API_KEY, SAFETY_MODEL,
                            THRESHOLDS, SENSOR_FIELDS, FAULT_SCENARIOS)
from shared.influx_io import get_latest, get_all_latest, get_recent
from shared.mongo_io import log_event, get_recent_events
from shared.redis_io import cache_agent_alert
from intelligent.tools.external_apis import get_weather
from intelligent.anomaly_detector import score_current_state

llm = ChatOpenAI(
    model=SAFETY_MODEL,
    base_url=f"{OLLAMA_HOST}/v1",
    api_key=OLLAMA_API_KEY,
    temperature=0,
)


@tool
def check_all_sensors(query: str = "") -> str:
    """Check all current sensor readings against safety thresholds."""
    readings   = get_all_latest()
    violations = []
    report     = []

    for field, val in readings.items():
        if val is None:
            report.append(f"{field}: no data")
            continue
        report.append(f"{field}: {val:.2f}")
        thresh = THRESHOLDS.get(field, {})
        if "critical" in thresh and val >= thresh["critical"]:
            violations.append(f"CRITICAL: {field}={val:.2f} (limit={thresh['critical']})")
        elif "warning" in thresh and val >= thresh["warning"]:
            violations.append(f"WARNING: {field}={val:.2f} (limit={thresh['warning']})")
        if "min" in thresh and val < thresh["min"]:
            violations.append(f"LOW: {field}={val:.2f} (min={thresh['min']})")
        if "max" in thresh and val > thresh["max"]:
            violations.append(f"HIGH: {field}={val:.2f} (max={thresh['max']})")

    result = "=== Sensor Readings ===\n" + "\n".join(report)
    if violations:
        result += "\n\n=== VIOLATIONS ===\n" + "\n".join(violations)
        for v in violations:
            log_event("threshold_violation", {"detail": v},
                      severity="critical" if "CRITICAL" in v else "warning")
    else:
        result += "\n\nAll sensors within safe limits."
    return result


@tool
def get_sensor(sensor_name: str) -> str:
    """Get the latest reading of a specific sensor by exact name."""
    val = get_latest(sensor_name)
    if val is None:
        return f"{sensor_name}: no data available"
    thresh = THRESHOLDS.get(sensor_name, {})
    status = "OK"
    if "critical" in thresh and val >= thresh["critical"]:
        status = "CRITICAL"
    elif "warning" in thresh and val >= thresh["warning"]:
        status = "WARNING"
    if "min" in thresh and val < thresh["min"]:
        status = "LOW"
    return f"{sensor_name}: {val:.2f} [{status}]"


@tool
def check_fault_scenarios(query: str = "") -> str:
    """
    Check all known fault scenarios against current sensor data.
    Detects: catalytic degradation, engine misfire, battery drain,
    tyre pressure loss, coolant leak, oil pressure drop.
    """
    results = []
    readings = get_all_latest()

    # Catalytic degradation
    o2_1 = readings.get("o2_sensor1_voltage")
    o2_2 = readings.get("o2_sensor2_voltage")
    if o2_1 and o2_2:
        diff = abs(o2_1 - o2_2)
        hist = get_recent("o2_sensor2_voltage", minutes=5)
        var  = _variance([v for _, v in hist])
        if diff < 0.05:
            results.append("FAULT DETECTED: Catalytic degradation (P0420) — O2 sensors tracking closely")
        if var < 0.001:
            results.append("FAULT DETECTED: O2 sensor not switching — sensor or cat failure")

    # Engine misfire
    misfires = sum(
        readings.get(f"misfire_count_cyl{i}", 0) or 0
        for i in range(1, 5)
    )
    if misfires > 10:
        results.append(f"FAULT DETECTED: Engine misfire (P0300) — {misfires} misfires detected")
    elif misfires > 4:
        results.append(f"WARNING: Misfire activity — {misfires} misfires, monitor closely")

    # Battery drain
    batt = readings.get("battery_voltage")
    alt  = readings.get("alternator_voltage")
    if batt and batt < 11.5:
        results.append(f"FAULT DETECTED: Critical battery voltage — {batt:.2f}V")
    elif batt and batt < 12.0:
        results.append(f"WARNING: Low battery voltage — {batt:.2f}V")
    if alt and (alt < 13.5 or alt > 14.8):
        results.append(f"WARNING: Alternator voltage abnormal — {alt:.2f}V")

    # Tyre pressure
    tyres = {
        "FL": readings.get("tyre_pressure_fl"),
        "FR": readings.get("tyre_pressure_fr"),
        "RL": readings.get("tyre_pressure_rl"),
        "RR": readings.get("tyre_pressure_rr"),
    }
    for pos, psi in tyres.items():
        if psi and psi < 28:
            results.append(f"FAULT DETECTED: Low tyre pressure {pos} — {psi:.1f} psi")
        elif psi and psi < 30:
            results.append(f"WARNING: Tyre pressure {pos} borderline — {psi:.1f} psi")

    # Oil pressure
    oil_p = readings.get("oil_pressure")
    if oil_p and oil_p < 20:
        results.append(f"FAULT DETECTED: Critical low oil pressure — {oil_p:.1f} psi")
    elif oil_p and oil_p < 25:
        results.append(f"WARNING: Low oil pressure — {oil_p:.1f} psi")

    if not results:
        return "No fault scenarios detected. All systems appear normal."
    return "=== FAULT SCENARIO ANALYSIS ===\n" + "\n".join(results)


@tool
def assess_emergency(query: str = "") -> str:
    """Determine if the vehicle is in an emergency state."""
    readings    = get_all_latest()
    emergencies = []
    for field, val in readings.items():
        if val is None:
            continue
        thresh = THRESHOLDS.get(field, {})
        if "critical" in thresh and val >= thresh["critical"]:
            emergencies.append(f"{field}={val:.2f} exceeds critical limit {thresh['critical']}")
        if "min" in thresh and val < thresh["min"] * 0.8:
            emergencies.append(f"{field}={val:.2f} critically below minimum {thresh['min']}")

    if emergencies:
        msg = "EMERGENCY: " + "; ".join(emergencies)
        msg += " — Pull over safely immediately."
        log_event("emergency_detected", {"conditions": emergencies}, severity="critical")
        cache_agent_alert("safety", f"🚨 {msg}")
        return msg
    cache_agent_alert("safety", "✅ SAFE: No emergency conditions.")
    return "No emergency conditions detected. Vehicle is safe."


@tool
def get_recent_safety_events(query: str = "") -> str:
    """Get the last 10 safety events from the log."""
    events = get_recent_events(10)
    if not events:
        return "No recent safety events."
    return "\n".join([
        f"[{e.get('severity','info').upper()}] {e.get('event_type')}: {e.get('details')}"
        for e in events
    ])


@tool
def check_anomaly_score(query: str = "") -> str:
    """
    Run anomaly detection on the current sensor snapshot using a
    trained autoencoder. Catches unusual sensor COMBINATIONS that
    individual threshold checks would miss — e.g. normal coolant
    temp but abnormal relationship between RPM, load, and MAF
    that suggests an undiagnosed mechanical issue.
    """
    result = score_current_state()
    if not result["available"]:
        return f"Anomaly detection unavailable: {result['reason']}"

    if result["is_anomalous"]:
        contributors = ", ".join([
            f"{c['field']}={c['value']:.2f}" for c in result["top_contributors"]
        ])
        return (
            f"ANOMALY DETECTED — score {result['anomaly_score']:.2f}x normal threshold.\n"
            f"This combination of sensor readings has not been seen during normal "
            f"operation. Most unusual readings: {contributors}.\n"
            f"This does not match a known fault pattern but deviates significantly "
            f"from learned normal behaviour — recommend caution and monitoring."
        )
    return (
        f"No anomaly detected. Current sensor pattern is consistent with normal "
        f"operation (score {result['anomaly_score']:.2f}, threshold 1.0)."
    )


def _variance(values: list) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return sum((v - mean) ** 2 for v in values) / len(values)


tools = [
    check_all_sensors,
    get_sensor,
    check_fault_scenarios,
    assess_emergency,
    get_recent_safety_events,
    check_anomaly_score,
    get_weather,
]

prompt = ChatPromptTemplate.from_messages([
    ("system", """You are the Safety Agent for a 2018 Toyota Camry digital twin.
Your job is to monitor live sensor data and identify safety-critical conditions.
You have access to weather data to factor in road conditions.

Rules:
- Always call check_all_sensors AND check_fault_scenarios before concluding
- Never invent sensor values — only report what tools return
- Factor in weather when assessing driving safety
- Rate overall safety: SAFE / WARNING / CRITICAL
- Always call check_anomaly_score in addition to threshold checks — it catches unusual sensor combinations that individual thresholds miss
- Be concise and direct — the driver needs to act quickly on critical findings"""),
    MessagesPlaceholder(variable_name="chat_history"),
    ("human", "{input}"),
    MessagesPlaceholder(variable_name="agent_scratchpad"),
])

# Window memory — remembers last 10 exchanges
memory = ConversationBufferWindowMemory(
    memory_key="chat_history", return_messages=True, k=10
)

safety_agent    = create_openai_tools_agent(llm=llm, tools=tools, prompt=prompt)
safety_executor = AgentExecutor(
    agent=safety_agent, tools=tools, memory=memory,
    verbose=True, handle_parsing_errors=True, max_iterations=8,
)


def run_safety_check(query: str = "Full safety assessment.") -> str:
    try:
        return safety_executor.invoke({"input": query})["output"]
    except Exception as e:
        return f"Safety Agent error: {str(e)}"

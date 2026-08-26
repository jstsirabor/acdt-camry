"""
intelligent/preventive_agent.py
────────────────────────────────
Preventive Maintenance Agent v3 — expanded fault scenarios,
fuel range integration, and window memory.
"""
from langchain.agents import AgentExecutor, create_openai_tools_agent
from langchain.memory import ConversationBufferWindowMemory
from langchain.tools import tool
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_openai import ChatOpenAI
from shared.config import (OLLAMA_HOST, OLLAMA_API_KEY, PREVENTIVE_MODEL,
                            MAINTENANCE_INTERVALS, FAULT_SCENARIOS)
from shared.influx_io import get_latest, get_recent
from shared.mongo_io import get_maintenance_history
from shared.redis_io import cache_agent_alert
from intelligent.tools.external_apis import estimate_fuel_range

llm = ChatOpenAI(
    model=PREVENTIVE_MODEL,
    base_url=f"{OLLAMA_HOST}/v1",
    api_key=OLLAMA_API_KEY,
    temperature=0,
)

_CURRENT_KM = 72000


@tool
def get_maintenance_schedule(query: str = "") -> str:
    """Get the full maintenance schedule — overdue, due soon, and OK items."""
    lines = []
    for service, info in MAINTENANCE_INTERVALS.items():
        remaining = (info["last_km"] + info["interval_km"]) - _CURRENT_KM
        if remaining <= 0:
            status = f"OVERDUE by {abs(remaining):,} km"
        elif remaining <= 1000:
            status = f"DUE SOON — {remaining:,} km"
        else:
            status = f"OK — {remaining:,} km remaining"
        lines.append(f"{service:25s} | {status}")
    return f"Maintenance Schedule at {_CURRENT_KM:,} km:\n" + "\n".join(lines)


@tool
def get_o2_sensor_health(query: str = "") -> str:
    """Analyse O2 sensors to assess catalytic converter health."""
    o2_1 = get_latest("o2_sensor1_voltage")
    o2_2 = get_latest("o2_sensor2_voltage")
    if o2_1 is None or o2_2 is None:
        return "O2 sensor data not available."
    diff  = abs(o2_1 - o2_2)
    hist2 = get_recent("o2_sensor2_voltage", minutes=5)
    var   = _variance([v for _, v in hist2])
    lines = [
        f"Upstream O2:      {o2_1:.3f}V",
        f"Downstream O2:    {o2_2:.3f}V",
        f"Differential:     {diff:.3f}V",
        f"Downstream var:   {var:.4f}",
    ]
    if diff < 0.05:
        lines.append("CRITICAL: O2 sensors tracking — catalytic converter likely degraded (P0420)")
    elif diff < 0.10:
        lines.append("WARNING: Low O2 differential — monitor catalytic converter closely")
    else:
        lines.append("OK: Healthy O2 differential")
    if var < 0.001:
        lines.append("WARNING: Downstream O2 not switching — sensor may be failing")
    return "\n".join(lines)


@tool
def get_battery_health(query: str = "") -> str:
    """Assess battery and alternator health from live readings."""
    batt = get_latest("battery_voltage")
    alt  = get_latest("alternator_voltage")
    if batt is None:
        return "Battery data not available."
    lines = [
        f"Battery voltage:    {batt:.2f}V",
        f"Alternator voltage: {alt:.2f}V" if alt else "Alternator: no data",
    ]
    if batt < 11.5:
        lines.append("CRITICAL: Battery failing — replace immediately")
    elif batt < 12.2:
        lines.append("WARNING: Battery weak — test and likely replace")
    else:
        lines.append("OK: Battery voltage normal")
    if alt:
        if alt < 13.5:
            lines.append("WARNING: Alternator undercharging — inspect charging system")
        elif alt > 14.8:
            lines.append("WARNING: Alternator overcharging — inspect voltage regulator")
        else:
            lines.append("OK: Alternator charging normally")
    return "\n".join(lines)


@tool
def get_tyre_health(query: str = "") -> str:
    """Check all four tyre pressures and assess condition."""
    tyres = {
        "Front Left":  get_latest("tyre_pressure_fl"),
        "Front Right": get_latest("tyre_pressure_fr"),
        "Rear Left":   get_latest("tyre_pressure_rl"),
        "Rear Right":  get_latest("tyre_pressure_rr"),
    }
    lines = []
    for pos, psi in tyres.items():
        if psi is None:
            lines.append(f"{pos}: no data")
            continue
        if psi < 26:
            status = "CRITICAL — dangerously low"
        elif psi < 28:
            status = "WARNING — below safe minimum (28 psi)"
        elif psi > 36:
            status = "WARNING — overinflated"
        else:
            status = "OK"
        lines.append(f"{pos}: {psi:.1f} psi — {status}")

    # Detect slow leak pattern
    hist_rl = get_recent("tyre_pressure_rl", minutes=10)
    if len(hist_rl) > 5:
        trend = hist_rl[-1][1] - hist_rl[0][1]
        if trend < -0.5:
            lines.append("WARNING: Rear left tyre pressure dropping — possible slow leak")
    return "Tyre Pressure Check:\n" + "\n".join(lines)


@tool
def get_misfire_analysis(query: str = "") -> str:
    """Analyse engine misfire counts across all cylinders."""
    counts = {
        f"Cylinder {i}": get_latest(f"misfire_count_cyl{i}")
        for i in range(1, 5)
    }
    lines  = []
    total  = 0
    for cyl, count in counts.items():
        if count is None:
            lines.append(f"{cyl}: no data")
            continue
        total += count
        if count > 20:
            status = "CRITICAL — severe misfire"
        elif count > 5:
            status = "WARNING — inspect spark plugs and coils"
        else:
            status = "OK"
        lines.append(f"{cyl}: {count} misfires — {status}")
    lines.append(f"Total misfires: {total}")
    if total > 20:
        lines.append("RECOMMENDATION: Immediate spark plug and ignition coil inspection")
    return "Misfire Analysis:\n" + "\n".join(lines)


@tool
def predict_next_failure(query: str = "") -> str:
    """Predict which component is most likely to fail next."""
    overdue  = []
    due_soon = []
    for service, info in MAINTENANCE_INTERVALS.items():
        remaining = (info["last_km"] + info["interval_km"]) - _CURRENT_KM
        if remaining <= 0:
            overdue.append((service, abs(remaining)))
        elif remaining <= 2000:
            due_soon.append((service, remaining))

    result = []
    if overdue:
        result.append("OVERDUE (highest risk):")
        for s, km in sorted(overdue, key=lambda x: x[1], reverse=True):
            result.append(f"  - {s}: overdue by {km:,} km")
    if due_soon:
        result.append("DUE SOON:")
        for s, km in sorted(due_soon, key=lambda x: x[1]):
            result.append(f"  - {s}: due in {km:,} km")
    if not result:
        result.append("No services overdue or due soon.")

    # Add sensor-based predictions
    o2 = get_o2_sensor_health("")
    if "WARNING" in o2 or "CRITICAL" in o2:
        result.append("\nO2/CATALYTIC RISK:")
        result.append(o2)

    batt = get_battery_health("")
    if "WARNING" in batt or "CRITICAL" in batt:
        result.append("\nBATTERY RISK:")
        result.append(batt)

    output = "\n".join(result)
    cache_agent_alert("preventive", output)
    return output


@tool
def get_service_history(service_name: str = "") -> str:
    """Get maintenance history for a specific service or all services."""
    history = get_maintenance_history(service_name or None)
    if not history:
        return f"No history found{' for ' + service_name if service_name else ''}."
    return "\n".join([
        f"{h['service']:25s} | {h['km']:,} km | {h['timestamp']}"
        for h in history
    ])


def _variance(values: list) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return sum((v - mean) ** 2 for v in values) / len(values)


tools = [
    get_maintenance_schedule,
    get_o2_sensor_health,
    get_battery_health,
    get_tyre_health,
    get_misfire_analysis,
    predict_next_failure,
    get_service_history,
    estimate_fuel_range,
]

prompt = ChatPromptTemplate.from_messages([
    ("system", """You are the Preventive Maintenance Agent for a 2018 Toyota Camry.
Your job is to track maintenance schedules, assess component wear, and predict
upcoming failures before they happen.

Rules:
- Always check the maintenance schedule AND sensor-based health indicators
- Check battery, tyres, and misfires in addition to O2 and fuel
- Prioritise by urgency: overdue > due soon > monitor
- Give specific km estimates and practical recommendations
- Be thorough but clear — avoid excessive technical jargon"""),
    MessagesPlaceholder(variable_name="chat_history"),
    ("human", "{input}"),
    MessagesPlaceholder(variable_name="agent_scratchpad"),
])

memory = ConversationBufferWindowMemory(
    memory_key="chat_history", return_messages=True, k=10
)

preventive_agent    = create_openai_tools_agent(llm=llm, tools=tools, prompt=prompt)
preventive_executor = AgentExecutor(
    agent=preventive_agent, tools=tools, memory=memory,
    verbose=True, handle_parsing_errors=True, max_iterations=10,
)


def run_preventive_check(query: str = "Full maintenance assessment.") -> str:
    try:
        return preventive_executor.invoke({"input": query})["output"]
    except Exception as e:
        return f"Preventive Agent error: {str(e)}"

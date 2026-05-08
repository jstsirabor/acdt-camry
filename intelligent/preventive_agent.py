"""
intelligent/preventive_agent.py
────────────────────────────────
Preventive Maintenance Agent — tracks service schedules,
part wear, and upcoming maintenance needs.
Model: ministral-3:8b via Ollama Cloud
"""
from langchain.agents import AgentExecutor, create_react_agent
from langchain.memory import ConversationBufferMemory
from langchain.tools import tool
from langchain_core.prompts import PromptTemplate
from langchain_openai import ChatOpenAI
from shared.config import (OLLAMA_HOST, OLLAMA_API_KEY, PREVENTIVE_MODEL,
                            MAINTENANCE_INTERVALS, ASSET_ID)
from shared.influx_io import get_latest, get_recent
from shared.mongo_io import log_maintenance, get_maintenance_history
from shared.redis_io import cache_agent_alert

# ── LLM ───────────────────────────────────────────────────────────
llm = ChatOpenAI(
    model=PREVENTIVE_MODEL,
    base_url=f"{OLLAMA_HOST}/v1",
    api_key=OLLAMA_API_KEY,
    temperature=0,
)

# ── Simulated odometer (in real system read from OBD-II) ──────────
_CURRENT_KM = 72000

# ── Tools ─────────────────────────────────────────────────────────
@tool
def get_maintenance_schedule(query: str = "") -> str:
    """Get the full maintenance schedule showing what is due, overdue, or upcoming."""
    lines = []
    for service, info in MAINTENANCE_INTERVALS.items():
        last_km   = info["last_km"]
        interval  = info["interval_km"]
        next_due  = last_km + interval
        remaining = next_due - _CURRENT_KM
        if remaining <= 0:
            status = f"OVERDUE by {abs(remaining):,} km"
        elif remaining <= 1000:
            status = f"DUE SOON in {remaining:,} km"
        else:
            status = f"OK — due in {remaining:,} km"
        lines.append(f"{service:30s} | last: {last_km:,} km | next: {next_due:,} km | {status}")
    return "=== Maintenance Schedule (current: {:,} km) ===\n".format(_CURRENT_KM) + "\n".join(lines)

@tool
def get_o2_sensor_health(query: str = "") -> str:
    """Analyse O2 sensor readings to assess catalytic converter and sensor health."""
    o2_1 = get_latest("o2_sensor1_voltage")
    o2_2 = get_latest("o2_sensor2_voltage")
    if o2_1 is None or o2_2 is None:
        return "O2 sensor data not available."
    correlation = abs(o2_1 - o2_2)
    history_1 = get_recent("o2_sensor1_voltage", minutes=5)
    history_2 = get_recent("o2_sensor2_voltage", minutes=5)
    variance_1 = _variance([v for _, v in history_1])
    variance_2 = _variance([v for _, v in history_2])
    assessment = []
    assessment.append(f"Upstream O2 (sensor1): {o2_1:.3f}V  variance={variance_1:.4f}")
    assessment.append(f"Downstream O2 (sensor2): {o2_2:.3f}V  variance={variance_2:.4f}")
    assessment.append(f"Voltage differential: {correlation:.3f}V")
    if variance_2 < 0.001:
        assessment.append("WARNING: Downstream O2 not switching — possible sensor failure or cat degradation")
    if correlation < 0.05:
        assessment.append("WARNING: O2 sensors tracking closely — catalytic converter may be degraded (P0420 risk)")
    elif correlation > 0.3:
        assessment.append("OK: Healthy O2 differential indicates good catalytic converter efficiency")
    else:
        assessment.append("MONITOR: O2 differential is borderline — monitor trend")
    return "\n".join(assessment)

@tool
def get_fuel_system_health(query: str = "") -> str:
    """Assess fuel system health from trim values and MAF readings."""
    ft_short = get_latest("fuel_trim_short")
    ft_long  = get_latest("fuel_trim_long")
    maf      = get_latest("mass_air_flow")
    if ft_short is None:
        return "Fuel trim data not available."
    lines = [
        f"Short-term fuel trim: {ft_short:+.1f}%",
        f"Long-term fuel trim:  {ft_long:+.1f}%",
        f"Mass air flow:        {maf:.2f} g/s" if maf else "MAF: no data",
    ]
    total = (ft_short or 0) + (ft_long or 0)
    if abs(total) > 25:
        lines.append(f"CRITICAL: Combined trim {total:+.1f}% — serious fueling issue")
    elif abs(total) > 15:
        lines.append(f"WARNING: Combined trim {total:+.1f}% — inspect fuel system")
    else:
        lines.append(f"OK: Combined trim {total:+.1f}% — fuel system healthy")
    return "\n".join(lines)

@tool
def get_service_history(service_name: str = "") -> str:
    """Get the maintenance history for a specific service or all services."""
    history = get_maintenance_history(service_name if service_name else None)
    if not history:
        return f"No maintenance history found{' for ' + service_name if service_name else ''}."
    lines = [f"{h['service']:30s} | {h['km']:,} km | {h['timestamp']} | {h.get('notes','')}"
             for h in history]
    return "\n".join(lines)

@tool
def predict_next_failure(query: str = "") -> str:
    """Predict which component is most likely to fail next based on current data and schedule."""
    overdue = []
    due_soon = []
    for service, info in MAINTENANCE_INTERVALS.items():
        remaining = (info["last_km"] + info["interval_km"]) - _CURRENT_KM
        if remaining <= 0:
            overdue.append((service, abs(remaining)))
        elif remaining <= 2000:
            due_soon.append((service, remaining))
    result = []
    if overdue:
        result.append("OVERDUE SERVICES (highest risk):")
        for s, km in sorted(overdue, key=lambda x: x[1], reverse=True):
            result.append(f"  - {s}: overdue by {km:,} km")
    if due_soon:
        result.append("DUE SOON:")
        for s, km in sorted(due_soon, key=lambda x: x[1]):
            result.append(f"  - {s}: due in {km:,} km")
    if not result:
        result.append("No services overdue or due soon.")
    o2_health = get_o2_sensor_health("")
    if "WARNING" in o2_health or "CRITICAL" in o2_health:
        result.append("\nO2/CATALYTIC RISK DETECTED:")
        result.append(o2_health)
    output = "\n".join(result)
    cache_agent_alert("preventive", output)
    return output

def _variance(values: list) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return sum((v - mean) ** 2 for v in values) / len(values)

tools = [get_maintenance_schedule, get_o2_sensor_health,
         get_fuel_system_health, get_service_history, predict_next_failure]

# ── Prompt ────────────────────────────────────────────────────────
_system = """You are the Preventive Maintenance Agent for a 2018 Toyota Camry digital twin.
Your job is to track maintenance schedules, assess component wear, and predict upcoming failures
before they happen. You focus on scheduled maintenance and gradual degradation patterns.

You have access to the following tools:
{tools}

Use this format:
Question: the input question you must answer
Thought: think about what maintenance data to check
Action: the action to take, must be one of [{tool_names}]
Action Input: the input to the action
Observation: the result of the action
... (repeat as needed)
Thought: I now know the final answer
Final Answer: your maintenance assessment with specific recommendations and timelines

Rules:
- Always check both the maintenance schedule AND sensor-based health indicators
- Prioritise by urgency: overdue > due soon > monitor
- Give specific km estimates for when action is needed
- Flag any sensor patterns suggesting premature wear"""

prompt = PromptTemplate.from_template(
    _system
    + "\n\nChat History:\n{chat_history}"
    + "\n\nQuestion: {input}"
    + "\n\nThought:{agent_scratchpad}"
)

memory = ConversationBufferMemory(memory_key="chat_history", return_messages=False)

preventive_agent = create_react_agent(llm=llm, tools=tools, prompt=prompt)
preventive_executor = AgentExecutor(
    agent=preventive_agent, tools=tools, memory=memory,
    verbose=True, handle_parsing_errors=True, max_iterations=8,
)

def run_preventive_check(query: str = "Assess the vehicle's maintenance status and predict upcoming needs.") -> str:
    """Called by the Predictive Agent as a tool."""
    try:
        result = preventive_executor.invoke({"input": query})
        return result["output"]
    except Exception as e:
        return f"Preventive Agent error: {str(e)}"

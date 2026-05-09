"""
intelligent/safety_agent.py
────────────────────────────
Safety Agent — monitors live sensor data for threshold violations
and emergency conditions. Reports to the Predictive Agent.
Model: gemma3:12b via Ollama Cloud
"""
from shared.config import (OLLAMA_HOST, OLLAMA_API_KEY, SAFETY_MODEL,
                            PREDICTIVE_MODEL, THRESHOLDS, SENSOR_FIELDS)
from langchain.agents import AgentExecutor, create_openai_tools_agent
from langchain.memory import ConversationBufferMemory
from langchain.tools import tool
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_openai import ChatOpenAI
from shared.influx_io import get_latest, get_all_latest
from shared.mongo_io import log_event, get_recent_events
from shared.redis_io import cache_agent_alert

# ── LLM ───────────────────────────────────────────────────────────
llm = ChatOpenAI(
    model=PREDICTIVE_MODEL,    # gpt-oss:120b — follows tool use reliably
    base_url=f"{OLLAMA_HOST}/v1",
    api_key=OLLAMA_API_KEY,
    temperature=0,
)

# ── Tools ─────────────────────────────────────────────────────────
@tool
def check_all_sensors(query: str = "") -> str:
    """Get all current sensor readings and flag any threshold violations."""
    readings = get_all_latest()
    violations = []
    report = []
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
            violations.append(f"WARNING: {field}={val:.2f} below min={thresh['min']}")
        if "max" in thresh and val > thresh["max"]:
            violations.append(f"WARNING: {field}={val:.2f} above max={thresh['max']}")
    result = "=== Sensor Readings ===\n" + "\n".join(report)
    if violations:
        result += "\n\n=== VIOLATIONS DETECTED ===\n" + "\n".join(violations)
        for v in violations:
            log_event("threshold_violation", {"detail": v},
                      severity="critical" if "CRITICAL" in v else "warning")
    else:
        result += "\n\nAll sensors within safe limits."
    return result

@tool
def get_sensor(sensor_name: str) -> str:
    """Get the latest reading of a specific sensor by name."""
    val = get_latest(sensor_name)
    if val is None:
        return f"{sensor_name}: no data available"
    thresh = THRESHOLDS.get(sensor_name, {})
    status = "OK"
    if "critical" in thresh and val >= thresh["critical"]:
        status = "CRITICAL"
    elif "warning" in thresh and val >= thresh["warning"]:
        status = "WARNING"
    return f"{sensor_name}: {val:.2f} [{status}]"

@tool
def get_recent_safety_events(query: str = "") -> str:
    """Get the last 10 safety-related events from the event log."""
    events = get_recent_events(10)
    if not events:
        return "No recent safety events."
    lines = []
    for e in events:
        lines.append(f"[{e.get('severity','info').upper()}] "
                     f"{e.get('event_type','unknown')}: "
                     f"{e.get('details',{})}")
    return "\n".join(lines)

@tool
def assess_emergency(query: str = "") -> str:
    """Determine if the vehicle is in an emergency state requiring immediate action."""
    readings = get_all_latest()
    emergencies = []
    for field, val in readings.items():
        if val is None:
            continue
        thresh = THRESHOLDS.get(field, {})
        if "critical" in thresh and val >= thresh["critical"]:
            emergencies.append(
                f"{field}={val:.2f} EXCEEDS critical threshold {thresh['critical']}"
            )
    if emergencies:
        msg = "EMERGENCY CONDITIONS DETECTED:\n" + "\n".join(emergencies)
        msg += "\n\nRECOMMENDED ACTION: Pull over safely and stop the vehicle immediately."
        log_event("emergency_detected", {"conditions": emergencies}, severity="critical")
        cache_agent_alert("safety", msg)
        return msg
    cache_agent_alert("safety", "All sensors within safe limits. No emergency action required.")
    return "Vehicle is operating within safe parameters. No emergency action required."

tools = [check_all_sensors, get_sensor, get_recent_safety_events, assess_emergency]

# ── Prompt ────────────────────────────────────────────────────────
prompt = ChatPromptTemplate.from_messages([
    ("system", """You are the Safety Agent for a 2018 Toyota Camry digital twin.
Your ONLY job is to monitor LIVE sensor readings from the vehicle's OBD-II system.

CRITICAL RULES:
- You MUST call check_all_sensors tool FIRST before saying anything
- NEVER invent, estimate, or assume sensor values
- ONLY report values returned by your tools
- If a tool returns no data, say "no data available" — do not fabricate readings
- Rate overall safety: SAFE / WARNING / CRITICAL based ONLY on tool results"""),
    MessagesPlaceholder(variable_name="chat_history"),
    ("human", "{input}"),
    MessagesPlaceholder(variable_name="agent_scratchpad"),
])

memory = ConversationBufferMemory(memory_key="chat_history", return_messages=True)

safety_agent    = create_openai_tools_agent(llm=llm, tools=tools, prompt=prompt)
safety_executor = AgentExecutor(
    agent=safety_agent, tools=tools, memory=memory,
    verbose=True, handle_parsing_errors=True, max_iterations=6,
)

def run_safety_check(query: str = "Perform a full safety assessment of the vehicle.") -> str:
    """Called by the Predictive Agent as a tool."""
    try:
        result = safety_executor.invoke({"input": query})
        return result["output"]
    except Exception as e:
        return f"Safety Agent error: {str(e)}"

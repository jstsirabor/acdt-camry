"""
intelligent/safety_agent.py
────────────────────────────
Safety Agent — monitors live sensor data for threshold violations
and emergency conditions. Reports to the Predictive Agent.
Model: gemma3:12b via Ollama Cloud
"""
import os
from ollama import Client
from langchain.agents import AgentExecutor, create_react_agent
from langchain.tools import tool
from langchain_core.prompts import PromptTemplate
from langchain_community.chat_models import ChatOllama
from shared.config import (OLLAMA_HOST, OLLAMA_API_KEY, SAFETY_MODEL,
                            THRESHOLDS, SENSOR_FIELDS)
from shared.influx_io import get_latest, get_all_latest
from shared.mongo_io import log_event, get_recent_events
from shared.redis_io import cache_agent_alert

# ── LLM ───────────────────────────────────────────────────────────
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    model=SAFETY_MODEL,
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
            emergencies.append(f"{field}={val:.2f} EXCEEDS critical threshold {thresh['critical']}")
    if emergencies:
        msg = "EMERGENCY CONDITIONS DETECTED:\n" + "\n".join(emergencies)
        msg += "\n\nRECOMMENDED ACTION: Pull over safely and stop the vehicle immediately."
        log_event("emergency_detected", {"conditions": emergencies}, severity="critical")
        cache_agent_alert("safety", msg)
        return msg
    cache_agent_alert("safety", "No emergency conditions detected.")
    return "Vehicle is operating within safe parameters. No emergency action required."

tools = [check_all_sensors, get_sensor, get_recent_safety_events, assess_emergency]

# ── Prompt ────────────────────────────────────────────────────────
_system = """You are the Safety Agent for a 2018 Toyota Camry digital twin.
Your ONLY job is to monitor sensor readings and identify safety-critical conditions.
You must be fast, precise, and err on the side of caution.

You have access to the following tools:
{tools}

Use this format:
Question: the input question you must answer
Thought: think about what safety checks are needed
Action: the action to take, must be one of [{tool_names}]
Action Input: the input to the action
Observation: the result of the action
... (repeat as needed)
Thought: I now know the final answer
Final Answer: your safety assessment — be direct and clear about any risks

Rules:
- Always check ALL sensors before concluding it is safe
- If ANY critical threshold is exceeded, recommend stopping the vehicle
- Include specific sensor values in your response
- Rate overall safety: SAFE / WARNING / CRITICAL"""

prompt = PromptTemplate.from_template(
    _system
    + "\n\nChat History:\n{chat_history}"
    + "\n\nQuestion: {input}"
    + "\n\nThought:{agent_scratchpad}"
)

from langchain.memory import ConversationBufferMemory
memory = ConversationBufferMemory(memory_key="chat_history", return_messages=False)

safety_agent = create_react_agent(llm=llm, tools=tools, prompt=prompt)
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

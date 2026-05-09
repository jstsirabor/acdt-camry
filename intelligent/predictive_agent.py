"""
intelligent/predictive_agent.py
────────────────────────────────
Predictive Agent — the orchestrator. Talks to the user,
consults Safety and Preventive sub-agents, and reasons
about the vehicle's future state.
Model: gpt-oss:120b via Ollama Cloud
"""
from langchain.agents import AgentExecutor, create_openai_tools_agent
from langchain.memory import ConversationBufferMemory
from langchain.tools import tool
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_openai import ChatOpenAI
from shared.config import OLLAMA_HOST, OLLAMA_API_KEY, PREDICTIVE_MODEL
from shared.influx_io import get_all_latest
from shared.mongo_io import get_recent_events
from intelligent.neo4j_kg import diagnose, get_components, get_failure_modes
from intelligent.safety_agent import run_safety_check
from intelligent.preventive_agent import run_preventive_check

# ── LLM ───────────────────────────────────────────────────────────
llm = ChatOpenAI(
    model=PREDICTIVE_MODEL,
    base_url=f"{OLLAMA_HOST}/v1",
    api_key=OLLAMA_API_KEY,
    temperature=0,
)

# ── Tools ─────────────────────────────────────────────────────────
@tool
def consult_safety_agent(query: str) -> str:
    """
    Consult the Safety Agent for real-time safety assessment.
    Use when the user asks about vehicle safety, emergency conditions,
    or when sensor readings suggest immediate danger.
    """
    return run_safety_check(query)

@tool
def consult_preventive_agent(query: str) -> str:
    """
    Consult the Preventive Maintenance Agent for maintenance status
    and wear predictions. Use when the user asks about upcoming
    maintenance, service schedules, or component degradation trends.
    """
    return run_preventive_check(query)

@tool
def get_live_sensors(query: str = "") -> str:
    """Get all current OBD-II sensor readings in a single snapshot."""
    readings = get_all_latest()
    if not any(v is not None for v in readings.values()):
        return "No sensor data available. Ensure the simulator is running."
    lines = [
        f"{k}: {v:.2f}" if v is not None else f"{k}: no data"
        for k, v in readings.items()
    ]
    return "=== Live OBD-II Readings ===\n" + "\n".join(lines)

@tool
def diagnose_vehicle(symptoms: str) -> str:
    """
    Diagnose possible failures from a comma-separated list of symptoms.
    Known symptoms: high_o2_correlation, low_o2_voltage_differential,
    high_coolant_temp, high_engine_rpm, high_engine_load,
    positive_fuel_trim, negative_fuel_trim, rough_idle,
    o2_sensor_no_switching
    """
    symp_list = [s.strip() for s in symptoms.split(",")]
    results   = diagnose(symp_list)
    if not results:
        return "No matching failure modes found for those symptoms."
    lines = [
        f"[{r['severity'].upper()}] {r['failure']} "
        f"(DTC: {r['dtc']}) → {r['action']}"
        for r in results
    ]
    return "=== Diagnosis Results ===\n" + "\n".join(lines)

@tool
def get_event_log(query: str = "") -> str:
    """Get the last 10 events from the vehicle event log."""
    events = get_recent_events(10)
    if not events:
        return "No events recorded yet."
    return "\n".join([
        f"[{e.get('severity','info').upper()}] "
        f"{e.get('event_type')} — {e.get('details')}"
        for e in events
    ])

@tool
def get_vehicle_components(query: str = "") -> str:
    """List all tracked vehicle components and their known failure modes."""
    comps    = get_components()
    failures = get_failure_modes()
    comp_str = "Components: " + ", ".join(comps)
    fail_str = "\n".join([
        f"  {f['component']} → {f['failure']} "
        f"({f['severity']}) DTC:{f['dtc']}"
        for f in failures
    ])
    return comp_str + "\n\nKnown Failure Modes:\n" + fail_str

tools = [
    consult_safety_agent,
    consult_preventive_agent,
    get_live_sensors,
    diagnose_vehicle,
    get_event_log,
    get_vehicle_components,
]

# ── Prompt ────────────────────────────────────────────────────────
prompt = ChatPromptTemplate.from_messages([
    ("system", """You are the Predictive Agent — the main AI orchestrator for a 2018 Toyota Camry
Agentic Car Digital Twin (ACDT). You are the user's primary interface for understanding
their vehicle's health, predicting failures, and planning maintenance.

You coordinate two specialist sub-agents:
- Safety Agent: handles emergency and real-time safety monitoring
- Preventive Agent: handles maintenance schedules and wear prediction

Guidelines:
- For safety questions → always consult the Safety Agent first
- For maintenance questions → always consult the Preventive Agent
- For general health questions → consult BOTH agents then synthesise
- Always include specific sensor values and actionable recommendations
- If failure probability is high, state urgency clearly
- Be the driver's trusted advisor — clear, honest, and specific"""),
    MessagesPlaceholder(variable_name="chat_history"),
    ("human", "{input}"),
    MessagesPlaceholder(variable_name="agent_scratchpad"),
])

memory = ConversationBufferMemory(memory_key="chat_history", return_messages=True)

predictive_agent = create_openai_tools_agent(llm=llm, tools=tools, prompt=prompt)
executor         = AgentExecutor(
    agent=predictive_agent, tools=tools, memory=memory,
    verbose=True, handle_parsing_errors=True, max_iterations=12,
)

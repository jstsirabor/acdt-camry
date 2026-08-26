"""
intelligent/predictive_agent.py
────────────────────────────────
Predictive Agent v3 — orchestrator with journey planning,
external API access, and window memory.
"""
from langchain.agents import AgentExecutor, create_openai_tools_agent
from langchain.memory import ConversationBufferWindowMemory
from langchain.tools import tool
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_openai import ChatOpenAI
from shared.config import OLLAMA_HOST, OLLAMA_API_KEY, PREDICTIVE_MODEL
from shared.influx_io import get_all_latest
from shared.mongo_io import get_recent_events
from intelligent.neo4j_kg import diagnose, get_components, get_failure_modes
from intelligent.safety_agent import run_safety_check
from intelligent.preventive_agent import run_preventive_check
from intelligent.tools.external_apis import (
    get_weather, get_weather_forecast, get_route_info,
    find_nearest_workshop, estimate_fuel_range,
    analyse_journey_safety, geocode_location,
)

llm = ChatOpenAI(
    model=PREDICTIVE_MODEL,
    base_url=f"{OLLAMA_HOST}/v1",
    api_key=OLLAMA_API_KEY,
    temperature=0,
)


@tool
def consult_safety_agent(query: str) -> str:
    """
    Consult the Safety Agent for real-time safety assessment.
    Use for: emergency conditions, sensor threshold violations,
    driving safety in current weather, immediate danger assessment.
    """
    return run_safety_check(query)


@tool
def consult_preventive_agent(query: str) -> str:
    """
    Consult the Preventive Maintenance Agent.
    Use for: maintenance schedules, component wear, upcoming failures,
    battery health, tyre condition, misfire analysis.
    """
    return run_preventive_check(query)


@tool
def get_live_sensors(query: str = "") -> str:
    """Get a snapshot of all current OBD-II sensor readings."""
    readings = get_all_latest()
    if not any(v is not None for v in readings.values()):
        return "No sensor data. Ensure the simulator or OBD adapter is running."
    return "Live OBD-II Readings:\n" + "\n".join([
        f"  {k}: {v:.2f}" if v is not None else f"  {k}: no data"
        for k, v in readings.items()
    ])


@tool
def diagnose_vehicle(symptoms: str) -> str:
    """
    Diagnose failures from comma-separated symptoms.
    Known symptoms: high_o2_correlation, low_o2_voltage_differential,
    high_coolant_temp, high_engine_rpm, high_engine_load,
    positive_fuel_trim, negative_fuel_trim, rough_idle,
    o2_sensor_no_switching, low_battery_voltage, low_tyre_pressure,
    engine_misfire, low_oil_pressure
    """
    symp_list = [s.strip() for s in symptoms.split(",")]
    results   = diagnose(symp_list)
    if not results:
        return "No matching failure modes found."
    return "Diagnosis:\n" + "\n".join([
        f"  [{r['severity'].upper()}] {r['failure']} (DTC:{r['dtc']}) → {r['action']}"
        for r in results
    ])


@tool
def get_event_log(query: str = "") -> str:
    """Get the last 10 events from the vehicle event log."""
    events = get_recent_events(10)
    if not events:
        return "No events recorded yet."
    return "\n".join([
        f"[{e.get('severity','info').upper()}] {e.get('event_type')} — {e.get('details')}"
        for e in events
    ])


@tool
def get_vehicle_components(query: str = "") -> str:
    """List all tracked components and known failure modes."""
    comps    = get_components()
    failures = get_failure_modes()
    return (
        "Components: " + ", ".join(comps)
        + "\n\nFailure Modes:\n"
        + "\n".join([
            f"  {f['component']} → {f['failure']} ({f['severity']}) DTC:{f['dtc']}"
            for f in failures
        ])
    )


@tool
def get_data_source_status(query: str = "") -> str:
    """Check whether vehicle data is coming from a real OBD adapter or the simulator."""
    from physical.obd_reader import get_data_source
    source = get_data_source()
    if source == "adapter":
        return "Data source: LIVE OBD-II adapter connected. Reading real vehicle data."
    return (
        "Data source: SIMULATOR. No OBD-II adapter detected. "
        "Data is simulated. Plug in an ELM327 adapter to read live vehicle data."
    )


tools = [
    consult_safety_agent,
    consult_preventive_agent,
    get_live_sensors,
    diagnose_vehicle,
    get_event_log,
    get_vehicle_components,
    get_data_source_status,
    get_weather,
    get_weather_forecast,
    get_route_info,
    find_nearest_workshop,
    estimate_fuel_range,
    analyse_journey_safety,
    geocode_location,
]

prompt = ChatPromptTemplate.from_messages([
    ("system", """You are ACDT — the intelligent co-pilot for a 2018 Toyota Camry.
You speak directly to the driver in plain, friendly English.
You coordinate two specialist agents (Safety and Preventive) and have access
to live vehicle sensors, weather data, maps, and route planning.

Personality:
- Calm, clear, and reassuring — never alarmist unless truly urgent
- Proactive — you flag issues before being asked when relevant
- Practical — always give the driver something actionable
- Concise — drivers are often on the move, keep responses readable

Guidelines:
- Safety question or emergency → consult Safety Agent first
- Maintenance question → consult Preventive Agent
- Journey planning → check weather, fuel range, and route
- General health → consult both agents and synthesise
- Always use real data from tools — never guess
- When something is urgent, say so clearly and simply
- When everything is fine, say so briefly and warmly"""),
    MessagesPlaceholder(variable_name="chat_history"),
    ("human", "{input}"),
    MessagesPlaceholder(variable_name="agent_scratchpad"),
])

# Remembers last 20 exchanges — driver can reference earlier conversation
memory = ConversationBufferWindowMemory(
    memory_key="chat_history", return_messages=True, k=20
)

predictive_agent = create_openai_tools_agent(llm=llm, tools=tools, prompt=prompt)
executor         = AgentExecutor(
    agent=predictive_agent, tools=tools, memory=memory,
    verbose=True, handle_parsing_errors=True, max_iterations=15,
)

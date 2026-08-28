"""
intelligent/diagnostic_agent.py
────────────────────────────────
ATDT Diagnostic Agent — maps a technician's freeform symptom
description onto the known Symptom vocabulary in the Neo4j
knowledge graph and returns ranked candidate failure modes.

Follows the same langchain + Ollama tool-agent pattern as
preventive_agent.py / predictive_agent.py.
"""
from langchain.agents import AgentExecutor, create_openai_tools_agent
from langchain.memory import ConversationBufferWindowMemory
from langchain.tools import tool
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_openai import ChatOpenAI
from shared.config import OLLAMA_HOST, OLLAMA_API_KEY, DIAGNOSTIC_MODEL
from intelligent.neo4j_kg import diagnose, get_repair_procedure

llm = ChatOpenAI(
    model=DIAGNOSTIC_MODEL,
    base_url=f"{OLLAMA_HOST}/v1",
    api_key=OLLAMA_API_KEY,
    temperature=0,
)

# Keep in sync with the Symptom nodes in neo4j_kg.build_knowledge_graph()
KNOWN_SYMPTOMS = [
    "high_o2_correlation",
    "low_o2_voltage_differential",
    "high_coolant_temp",
    "high_engine_rpm",
    "high_engine_load",
    "positive_fuel_trim",
    "negative_fuel_trim",
    "rough_idle",
    "o2_sensor_no_switching",
    "rpm_flare_without_acceleration",
]


@tool
def diagnose_from_symptoms(symptoms: str) -> str:
    """
    Diagnose failure modes from comma-separated symptom keys.
    Known symptoms: high_o2_correlation, low_o2_voltage_differential,
    high_coolant_temp, high_engine_rpm, high_engine_load,
    positive_fuel_trim, negative_fuel_trim, rough_idle,
    o2_sensor_no_switching, rpm_flare_without_acceleration.
    Use this once you've mapped the technician's description onto
    these exact keys.
    """
    symp_list = [s.strip() for s in symptoms.split(",") if s.strip() in KNOWN_SYMPTOMS]
    if not symp_list:
        return "No recognised symptom keys in that list. Re-check against the known vocabulary."
    results = diagnose(symp_list)
    if not results:
        return "No matching failure modes found for those symptoms."
    return "Diagnosis (ranked by severity):\n" + "\n".join([
        f"  [{r['severity'].upper()}] {r['failure']} (DTC:{r['dtc']}) → {r['action']}"
        for r in results
    ])


@tool
def list_known_symptoms(query: str = "") -> str:
    """List the exact symptom keys this system understands, so freeform
    technician descriptions can be mapped onto them correctly."""
    return "Known symptom keys:\n" + "\n".join(f"  - {s}" for s in KNOWN_SYMPTOMS)


@tool
def check_repair_procedure_exists(failure_name: str) -> str:
    """
    Check whether a confirmed failure mode has a documented repair
    procedure available (does not return the procedure itself — that's
    the Guidance Agent's job, use this only to confirm one exists before
    handing off).
    """
    procedure = get_repair_procedure(failure_name.strip())
    if not procedure:
        return f"No repair procedure on file for '{failure_name}'."
    return (
        f"Repair procedure available: {procedure['procedure_name']} "
        f"(~{procedure['estimated_time_hours']}h). Hand off to the Guidance Agent for full steps."
    )


tools = [
    diagnose_from_symptoms,
    list_known_symptoms,
    check_repair_procedure_exists,
]

prompt = ChatPromptTemplate.from_messages([
    ("system", """You are the ATDT Diagnostic Agent for a 2018 Toyota Camry.
A technician will describe, in their own words, what the vehicle is doing.
Your job is to:
1. Call list_known_symptoms if you need a reminder of the exact vocabulary.
2. Map the technician's description onto zero or more of those exact symptom keys.
   Only use keys from that list — never invent new ones. If nothing plausibly
   matches, say so rather than guessing.
3. Call diagnose_from_symptoms with the matched keys (comma-separated).
4. Present the ranked candidates clearly, most severe first.
5. If the technician wants to know whether a fix is documented, use
   check_repair_procedure_exists — but do not attempt to give repair steps
   yourself, that's the Guidance Agent's job.

Be direct and technical — you're talking to a technician, not a driver.
If the description is too vague to map to any symptom, ask a clarifying
question instead of guessing."""),
    MessagesPlaceholder(variable_name="chat_history"),
    ("human", "{input}"),
    MessagesPlaceholder(variable_name="agent_scratchpad"),
])

memory = ConversationBufferWindowMemory(
    memory_key="chat_history", return_messages=True, k=10
)

diagnostic_agent    = create_openai_tools_agent(llm=llm, tools=tools, prompt=prompt)
diagnostic_executor = AgentExecutor(
    agent=diagnostic_agent, tools=tools, memory=memory,
    verbose=True, handle_parsing_errors=True, max_iterations=10,
)


def run_diagnostic_check(query: str) -> str:
    try:
        return diagnostic_executor.invoke({"input": query})["output"]
    except Exception as e:
        return f"Diagnostic Agent error: {str(e)}"

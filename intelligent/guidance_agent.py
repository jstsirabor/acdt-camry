"""
intelligent/guidance_agent.py
────────────────────────────────
ATDT Guidance Agent — takes a confirmed failure mode, pulls its
repair procedure from the knowledge graph, and walks the technician
through it conversationally, one step at a time.

Session-aware: each technician/job gets its own step-tracking state,
keyed by session_id, so multiple concurrent repairs don't collide.

Follows the same langchain + Ollama tool-agent pattern as
diagnostic_agent.py / preventive_agent.py.
"""
from langchain.agents import AgentExecutor, create_openai_tools_agent
from langchain.memory import ConversationBufferWindowMemory
from langchain.tools import tool
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_openai import ChatOpenAI
from shared.config import OLLAMA_HOST, OLLAMA_API_KEY, GUIDANCE_MODEL
from intelligent.neo4j_kg import get_repair_procedure

llm = ChatOpenAI(
    model=GUIDANCE_MODEL,
    base_url=f"{OLLAMA_HOST}/v1",
    api_key=OLLAMA_API_KEY,
    temperature=0,
)

# Per-session repair state: { session_id: {failure_name, procedure_name,
# steps, tools_required, estimated_time_hours, step_index} }
# Resets on process restart — fine since a repair job is a single sitting.
_session_state: dict[str, dict] = {}

# Executors are also per-session so each technician's chat memory stays
# separate. Built lazily on first use.
_session_executors: dict[str, AgentExecutor] = {}


@tool
def start_repair_procedure(failure_name: str, session_id: str) -> str:
    """
    Load the repair procedure for a confirmed failure mode and start
    walking through it from step 1. Use this once the technician has
    confirmed which failure mode they're fixing (usually handed off
    from the Diagnostic Agent). failure_name must match a FailureMode
    name exactly, e.g. 'cat_efficiency_below_threshold'. session_id
    identifies this technician/job so state doesn't mix with others.
    """
    procedure = get_repair_procedure(failure_name.strip())
    if not procedure:
        return f"No repair procedure on file for '{failure_name}'. Cannot start guidance."

    _session_state[session_id] = {
        "failure_name": failure_name.strip(),
        "procedure_name": procedure["procedure_name"],
        "steps": procedure["steps"],
        "tools_required": procedure["tools_required"],
        "estimated_time_hours": procedure["estimated_time_hours"],
        "step_index": 0,
    }

    tools_list = ", ".join(procedure["tools_required"])
    return (
        f"Starting procedure: {procedure['procedure_name']}\n"
        f"Estimated time: {procedure['estimated_time_hours']}h\n"
        f"Tools required: {tools_list}\n"
        f"Total steps: {len(procedure['steps'])}\n\n"
        f"Step 1 of {len(procedure['steps'])}: {procedure['steps'][0]}"
    )


@tool
def next_step(session_id: str) -> str:
    """
    Advance to the next step in this session's currently active repair
    procedure. Use this when the technician says they've finished the
    current step or asks what's next. Fails if no procedure has been
    started yet for this session_id.
    """
    state = _session_state.get(session_id)
    if not state:
        return "No repair procedure is currently active for this session. Use start_repair_procedure first."

    state["step_index"] += 1
    idx, steps = state["step_index"], state["steps"]

    if idx >= len(steps):
        return (
            f"That was the last step. Procedure '{state['procedure_name']}' complete.\n"
            f"Recommend confirming the fix (clear DTC, test drive if applicable) before closing the job."
        )
    return f"Step {idx + 1} of {len(steps)}: {steps[idx]}"


@tool
def repeat_current_step(session_id: str) -> str:
    """Repeat this session's current step instructions without advancing."""
    state = _session_state.get(session_id)
    if not state:
        return "No repair procedure is currently active for this session. Use start_repair_procedure first."
    idx, steps = state["step_index"], state["steps"]
    return f"Step {idx + 1} of {len(steps)}: {steps[idx]}"


@tool
def go_back_step(session_id: str) -> str:
    """Go back to the previous step in this session, in case the technician needs to redo or recheck something."""
    state = _session_state.get(session_id)
    if not state:
        return "No repair procedure is currently active for this session. Use start_repair_procedure first."
    if state["step_index"] == 0:
        return "Already at step 1 — nothing before this."
    state["step_index"] -= 1
    idx, steps = state["step_index"], state["steps"]
    return f"Step {idx + 1} of {len(steps)}: {steps[idx]}"


@tool
def get_procedure_overview(session_id: str) -> str:
    """Give a full overview of this session's active procedure (all steps, tools, time estimate) — use only if the technician explicitly asks to see everything at once rather than step by step."""
    state = _session_state.get(session_id)
    if not state:
        return "No repair procedure is currently active for this session. Use start_repair_procedure first."
    steps_list = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(state["steps"]))
    tools_list = ", ".join(state["tools_required"])
    return (
        f"{state['procedure_name']} — full overview\n"
        f"Estimated time: {state['estimated_time_hours']}h\n"
        f"Tools required: {tools_list}\n\n"
        f"{steps_list}\n\n"
        f"(Currently on step {state['step_index'] + 1})"
    )


tools = [
    start_repair_procedure,
    next_step,
    repeat_current_step,
    go_back_step,
    get_procedure_overview,
]

prompt = ChatPromptTemplate.from_messages([
    ("system", """You are the ATDT Guidance Agent for a 2018 Toyota Camry.
You walk a technician through a confirmed repair procedure, one step at a
time, like an experienced colleague standing next to them.

The current session_id for every tool call in this conversation is: {session_id}
Always pass this exact session_id value to every tool call you make —
never omit it or invent a different one.

Rules:
- Never dump the whole procedure unprompted — one step at a time, unless
  the technician explicitly asks for the full overview.
- When the technician indicates they've finished a step (e.g. "done",
  "ok next", "finished that") call next_step.
- If they ask to repeat or didn't catch it, call repeat_current_step.
- If they need to go back, call go_back_step.
- If a failure mode hasn't been confirmed yet and no procedure is active,
  ask which failure mode they're working on before calling
  start_repair_procedure.
- Be direct and practical — you're talking to a technician with their
  hands in the engine bay, not writing documentation. Short, clear
  instructions, no fluff.
- If a step seems unclear or the technician reports something unexpected
  (e.g. a part looks different than described), flag it plainly rather
  than guessing — technicians should trust what you tell them."""),
    MessagesPlaceholder(variable_name="chat_history"),
    ("human", "{input}"),
    MessagesPlaceholder(variable_name="agent_scratchpad"),
])


def _get_executor(session_id: str) -> AgentExecutor:
    """Lazily build (and cache) a per-session agent executor so each
    technician/job keeps separate chat memory."""
    if session_id not in _session_executors:
        memory = ConversationBufferWindowMemory(
            memory_key="chat_history", return_messages=True, k=10
        )
        bound_prompt = prompt.partial(session_id=session_id)
        agent = create_openai_tools_agent(llm=llm, tools=tools, prompt=bound_prompt)
        _session_executors[session_id] = AgentExecutor(
            agent=agent, tools=tools, memory=memory,
            verbose=True, handle_parsing_errors=True, max_iterations=10,
        )
    return _session_executors[session_id]


def run_guidance_check(query: str, session_id: str = "default") -> str:
    try:
        executor = _get_executor(session_id)
        return executor.invoke({"input": query})["output"]
    except Exception as e:
        return f"Guidance Agent error: {str(e)}"

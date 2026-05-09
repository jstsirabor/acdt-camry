"""
service_layer/agent_api.py
───────────────────────────
FastAPI backend — streams agent execution as Server-Sent Events
and serves the dashboard UI.
"""
import json
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

app = FastAPI(title="ACDT — Agentic Car Digital Twin")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

_UI = Path(__file__).parent.parent / "ui" / "dashboard.html"


@app.get("/", response_class=HTMLResponse)
async def index():
    return _UI.read_text(encoding="utf-8")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/api/sensors")
async def sensors():
    from shared.influx_io import get_all_latest
    return JSONResponse(get_all_latest())


@app.get("/api/health-score")
async def health_score():
    from shared.redis_io import get_health_score
    from service_layer.ditto_client import get_thing
    score = get_health_score()
    thing = get_thing()
    status = thing.get("features", {}).get("health", {}).get("properties", {}).get("status", "unknown")
    return JSONResponse({"score": score, "status": status})


@app.get("/api/events")
async def events():
    from shared.mongo_io import get_recent_events
    import json
    from datetime import datetime

    def serialize(obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        raise TypeError(f"Object of type {type(obj)} is not JSON serializable")

    events = get_recent_events(20)
    return JSONResponse(json.loads(json.dumps(events, default=serialize)))


@app.get("/api/alerts")
async def alerts():
    from shared.redis_io import get_agent_alert
    return JSONResponse({
        "safety":     get_agent_alert("safety"),
        "preventive": get_agent_alert("preventive"),
    })


class ChatRequest(BaseModel):
    message: str


@app.post("/api/chat")
async def chat_stream(req: ChatRequest):
    async def generate():
        from intelligent.predictive_agent import executor
        try:
            async for event in executor.astream_events(
                {"input": req.message}, version="v2"
            ):
                etype = event["event"]
                name  = event.get("name", "")
                data  = event.get("data", {})

                if etype == "on_chat_model_stream":
                    chunk = data.get("chunk")
                    content = ""
                    if chunk and hasattr(chunk, "content"):
                        content = chunk.content or ""
                    elif isinstance(chunk, str):
                        content = chunk
                    if content:
                        yield _sse({"type": "thinking", "content": content})

                elif etype == "on_tool_start":
                    inp = data.get("input", {})
                    yield _sse({
                        "type":  "tool_start",
                        "tool":  name,
                        "input": json.dumps(inp, default=str)[:500],
                    })

                elif etype == "on_tool_end":
                    out = data.get("output", "")
                    if not isinstance(out, str):
                        out = json.dumps(out, default=str)
                    yield _sse({
                        "type":   "tool_end",
                        "tool":   name,
                        "output": out[:3000],
                    })

                elif etype == "on_chain_end" and name == "AgentExecutor":
                    out = data.get("output", {})
                    final = out.get("output", "") if isinstance(out, dict) else str(out)
                    yield _sse({"type": "final", "content": final})
                    yield "data: [DONE]\n\n"
                    return

        except Exception:
            import traceback
            yield _sse({"type": "error", "content": traceback.format_exc()})
            yield "data: [DONE]\n\n"
            return
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":     "no-cache",
            "Connection":        "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

@app.get("/api/mechanic")
async def mechanic_twin():
    from service_layer.mechanic_twin import get_mechanic_twin
    return JSONResponse(get_mechanic_twin())

@app.get("/api/mechanic/emergency")
async def mechanic_emergencies():
    from service_layer.mechanic_twin import get_emergency_queue
    return JSONResponse(get_emergency_queue())

@app.get("/api/mechanic/maintenance")
async def mechanic_maintenance():
    from service_layer.mechanic_twin import get_maintenance_queue
    return JSONResponse(get_maintenance_queue())

@app.get("/api/mechanic/status")
async def mechanic_status():
    from service_layer.mechanic_client import get_mechanic_status
    return JSONResponse(get_mechanic_status())

@app.get("/api/mechanic/push-test")
async def mechanic_push_test():
    """Manually trigger a test push to the mechanic twin."""
    from service_layer.mechanic_client import push_to_mechanic
    from datetime import datetime, timezone
    push_to_mechanic("emergency", {
        "type":      "test_ping",
        "severity":  "info",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "report":    "Test connection from ACDT vehicle twin.",
    })
    return JSONResponse({"status": "pushed"})

def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

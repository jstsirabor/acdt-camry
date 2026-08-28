"""
service_layer/agent_api.py
───────────────────────────
FastAPI backend v3 — supports proactive messages,
driver chat, all sensor/health endpoints, and
dedicated mechanic API endpoints.
"""
import json
import sys
from pathlib import Path
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

app = FastAPI(title="ACDT — Agentic Car Digital Twin v3")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

_UI         = Path(__file__).parent.parent / "ui" / "dashboard.html"
_MECH_UI    = Path(__file__).parent.parent / "ui" / "mechanic.html"


@app.get("/", response_class=HTMLResponse)
async def index():
    return _UI.read_text(encoding="utf-8")


@app.get("/mechanic", response_class=HTMLResponse)
async def mechanic_index():
    return _MECH_UI.read_text(encoding="utf-8")


@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/api/sensors")
async def sensors():
    from shared.influx_io import get_all_latest
    return JSONResponse(get_all_latest())


@app.get("/api/health-score")
async def health_score():
    from shared.redis_io import get_health_score
    from service_layer.ditto_client import get_thing
    score  = get_health_score()
    thing  = get_thing()
    status = (thing.get("features", {})
                   .get("health", {})
                   .get("properties", {})
                   .get("status", "unknown"))
    return JSONResponse({"score": score, "status": status})


@app.get("/api/events")
async def events():
    from shared.mongo_io import get_recent_events
    def serialize(obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        raise TypeError(f"Not serializable: {type(obj)}")
    return JSONResponse(
        json.loads(json.dumps(get_recent_events(20), default=serialize))
    )


@app.get("/api/alerts")
async def alerts():
    from shared.redis_io import get_agent_alert
    return JSONResponse({
        "safety":     get_agent_alert("safety"),
        "preventive": get_agent_alert("preventive"),
    })


@app.get("/api/data-source")
async def data_source():
    from physical.obd_reader import get_data_source
    return JSONResponse({"source": get_data_source()})


# ── Proactive messages ─────────────────────────────────────────────
@app.get("/api/messages")
async def get_messages():
    from autonomous.messenger import get_recent_messages
    return JSONResponse(get_recent_messages(50))


@app.post("/api/messages/clear")
async def clear_messages():
    from autonomous.messenger import clear_messages
    clear_messages()
    return JSONResponse({"status": "cleared"})


# ── Driver chat ────────────────────────────────────────────────────
class ChatRequest(BaseModel):
    message: str


@app.post("/api/chat")
async def chat_stream(req: ChatRequest):
    async def generate():
        from intelligent.predictive_agent import executor
        import json, redis
        from shared.config import REDIS_HOST, REDIS_PORT

        try:
            r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
            driver_msg = {
                "role":      "driver",
                "content":   req.message,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "type":      "driver",
                "source":    "chat",
            }
            r.lpush("driver:messages", json.dumps(driver_msg))
        except Exception:
            pass

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
                    if content:
                        yield _sse({"type": "thinking", "content": content})

                elif etype == "on_tool_start":
                    yield _sse({
                        "type":  "tool_start",
                        "tool":  name,
                        "input": json.dumps(data.get("input", {}), default=str)[:300],
                    })

                elif etype == "on_tool_end":
                    out = data.get("output", "")
                    if not isinstance(out, str):
                        out = json.dumps(out, default=str)
                    yield _sse({"type": "tool_end", "tool": name, "output": out[:2000]})

                elif etype == "on_chain_end" and name == "AgentExecutor":
                    out   = data.get("output", {})
                    final = out.get("output", "") if isinstance(out, dict) else str(out)
                    try:
                        r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
                        acdt_msg = {
                            "role":      "acdt",
                            "content":   final,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "type":      "response",
                            "source":    "chat",
                        }
                        r.lpush("driver:messages", json.dumps(acdt_msg))
                        r.ltrim("driver:messages", 0, 199)
                    except Exception:
                        pass
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

# ── ATDT — Diagnostic & Guidance ───────────────────────────────────
class DiagnoseRequest(BaseModel):
    query: str


@app.post("/api/atdt/diagnose")
async def atdt_diagnose(req: DiagnoseRequest):
    from intelligent.diagnostic_agent import run_diagnostic_check
    result = run_diagnostic_check(req.query)
    return JSONResponse({"response": result})


class GuidanceRequest(BaseModel):
    query: str
    session_id: str = "default"


@app.post("/api/atdt/guidance")
async def atdt_guidance(req: GuidanceRequest):
    from intelligent.guidance_agent import run_guidance_check
    result = run_guidance_check(req.query, req.session_id)
    return JSONResponse({"response": result})

# ── Mechanic API endpoints ─────────────────────────────────────────
@app.get("/api/mechanic/status")
async def mechanic_status():
    from service_layer.mechanic_client import get_mechanic_status
    return JSONResponse(get_mechanic_status())


@app.get("/api/mechanic/emergency")
async def mechanic_emergency():
    from service_layer.mechanic_client import get_emergency_queue
    def serialize(obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        raise TypeError(f"Not serializable: {type(obj)}")
    return JSONResponse(
        json.loads(json.dumps(get_emergency_queue(), default=serialize))
    )


@app.get("/api/mechanic/maintenance")
async def mechanic_maintenance():
    from service_layer.mechanic_client import get_maintenance_queue
    def serialize(obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        raise TypeError(f"Not serializable: {type(obj)}")
    return JSONResponse(
        json.loads(json.dumps(get_maintenance_queue(), default=serialize))
    )


@app.get("/api/mechanic/vehicle")
async def mechanic_vehicle():
    """Full vehicle twin state for mechanic view."""
    from service_layer.ditto_client import get_thing
    from shared.influx_io import get_all_latest
    from shared.redis_io import get_health_score, get_agent_alert
    def serialize(obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        raise TypeError(f"Not serializable: {type(obj)}")
    data = {
        "twin":         get_thing(),
        "sensors":      get_all_latest(),
        "health_score": get_health_score(),
        "safety_alert": get_agent_alert("safety"),
        "maintenance_alert": get_agent_alert("preventive"),
    }
    return JSONResponse(
        json.loads(json.dumps(data, default=serialize))
    )


@app.post("/api/mechanic/acknowledge")
async def mechanic_acknowledge(body: dict):
    """Mechanic acknowledges an alert — logs it to MongoDB."""
    from shared.mongo_io import log_event
    log_event(
        "mechanic_acknowledged",
        {
            "alert_type": body.get("type", "unknown"),
            "note":       body.get("note", ""),
            "mechanic":   body.get("mechanic", "Mechanic"),
        },
        severity="info"
    )
    return JSONResponse({"status": "acknowledged"})

@app.post("/api/typing")
async def set_typing():
    """Called while the driver has text in the input box. Sets a short-
    lived Redis flag so the messenger holds off on non-critical proactive
    messages. The flag auto-expires after 5 seconds if no new signal
    arrives, so nothing needs to explicitly clear it."""
    import redis
    from shared.config import REDIS_HOST, REDIS_PORT
    try:
        r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
        r.set("driver:typing", "1", ex=5)
    except Exception:
        pass
    return JSONResponse({"status": "ok"})


@app.get("/api/mechanic/obd-mode")
async def get_obd_mode():
    """Report the currently active data source and whether a live
    override is set (vs. following OBD_MODE / auto-detection)."""
    import redis
    from shared.config import REDIS_HOST, REDIS_PORT
    from physical.obd_reader import get_data_source
    override = None
    try:
        r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
        val = r.get("obd:mode_override")
        if val and val.strip():
            override = val.strip().lower()
    except Exception:
        pass
    return JSONResponse({"active_source": get_data_source(), "override": override})


@app.post("/api/mechanic/obd-mode")
async def set_obd_mode(body: dict):
    """Set or clear the live OBD data-source override. Pass
    {"mode": "simulator"|"adapter"|"mqtt"|"auto"} to force a source, or
    {"mode": ""} (or omit "mode") to clear the override and go back to
    whatever OBD_MODE/.env + auto-detection would normally choose.
    Takes effect on the next read_sensors() call — no restart needed."""
    import redis
    from shared.config import REDIS_HOST, REDIS_PORT
    mode = (body.get("mode") or "").strip().lower()
    valid = {"simulator", "adapter", "mqtt", "auto"}
    if mode and mode not in valid:
        return JSONResponse({"status": "error", "message": f"Invalid mode '{mode}'"}, status_code=400)
    try:
        r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
        if mode:
            r.set("obd:mode_override", mode)
        else:
            r.delete("obd:mode_override")
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)
    return JSONResponse({"status": "ok", "override": mode or None})


@app.get("/api/mechanic/push-test")
async def mechanic_push_test():
    from service_layer.mechanic_client import push_to_mechanic
    push_to_mechanic("emergency", {
        "type":      "test_ping",
        "severity":  "info",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "report":    "Test connection from ACDT v3.",
    })
    return JSONResponse({"status": "pushed"})


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

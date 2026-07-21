"""
Harness server — generic, knows nothing about specific workflows.
Serves both the REST API and the management UI.
"""
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from step_types import REGISTRY as STEP_REGISTRY
from registry import Registry

app = FastAPI(title="Engineering Agent Harness")
reg = Registry()


def _cfg(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


def _gate_context(wf_id: str, values: dict, node: str) -> dict:
    meta = reg.gate_meta(wf_id).get(node, {})
    return {
        "gate":    node,
        "options": meta.get("options", []),
        "field":   meta.get("output_field"),
        "state":   {k: v for k, v in values.items()
                    if k in ("evidence", "assessment", "fix", "validation", "pr")},
    }


def _response(wf_id: str, thread_id: str, graph_state) -> dict:
    if graph_state.next:
        node = graph_state.next[0]
        return {
            "status":    "waiting_for_human",
            "workflow":  wf_id,
            "thread_id": thread_id,
            "gate":      _gate_context(wf_id, graph_state.values, node),
        }
    return {
        "status":    "complete",
        "workflow":  wf_id,
        "thread_id": thread_id,
        "outcome":   graph_state.values.get("outcome"),
    }


# ── Workflow runs ────────────────────────────────────────────────────────────

@app.post("/api/run/{workflow_id}/{thread_id}")
def run(workflow_id: str, thread_id: str, ticket: dict):
    graph = reg.graph(workflow_id)
    graph.invoke(ticket, _cfg(thread_id))
    return _response(workflow_id, thread_id, graph.get_state(_cfg(thread_id)))


@app.get("/api/state/{workflow_id}/{thread_id}")
def get_state(workflow_id: str, thread_id: str):
    graph = reg.graph(workflow_id)
    state = graph.get_state(_cfg(thread_id))
    if not state.values:
        raise HTTPException(status_code=404, detail="Thread not found")
    return _response(workflow_id, thread_id, state)


@app.post("/api/decision/{workflow_id}/{thread_id}")
def submit_decision(workflow_id: str, thread_id: str, body: dict):
    graph = reg.graph(workflow_id)
    state = graph.get_state(_cfg(thread_id))
    if not state.next:
        raise HTTPException(status_code=400, detail="No pending gate")

    node    = state.next[0]
    meta    = reg.gate_meta(workflow_id).get(node, {})
    options = meta.get("options", [])
    field   = meta.get("output_field")
    decision = body.get("decision")

    if decision not in options:
        raise HTTPException(status_code=400,
                            detail=f"Invalid decision. Options: {options}")

    value = (decision == "authorized") if field == "scope_authorized" else decision
    graph.update_state(_cfg(thread_id), {field: value})
    graph.invoke(None, _cfg(thread_id))
    return _response(workflow_id, thread_id, graph.get_state(_cfg(thread_id)))


# ── Registry APIs ────────────────────────────────────────────────────────────

@app.get("/api/workflows")
def list_workflows():
    return {
        wf_id: {
            "id":      cfg["id"],
            "trigger": cfg.get("trigger", {}),
            "steps":   [{"id": s["id"], "type": ("gate" if "gate" in s else "agent")}
                        for s in cfg.get("steps", [])],
        }
        for wf_id, cfg in reg.workflows.items()
    }


@app.get("/api/agents")
def list_agents():
    return {
        ag_id: {
            "id":    cfg["id"],
            "steps": [{"type": s["type"], "output": s.get("output_field")}
                      for s in cfg.get("steps", [])],
        }
        for ag_id, cfg in reg.agents.items()
    }


@app.get("/api/step-types")
def list_step_types():
    return list(STEP_REGISTRY.keys())


@app.post("/api/workflows")
def create_workflow(workflow: dict):
    if "id" not in workflow:
        raise HTTPException(status_code=400, detail="Missing 'id'")
    reg.save_workflow(workflow)
    return {"created": workflow["id"]}


@app.post("/api/agents")
def create_agent(agent: dict):
    if "id" not in agent:
        raise HTTPException(status_code=400, detail="Missing 'id'")
    reg.save_agent(agent)
    return {"created": agent["id"]}


@app.post("/api/reload")
def reload():
    reg.reload()
    return {"reloaded": True, "workflows": list(reg.workflows), "agents": list(reg.agents)}


# ── UI ───────────────────────────────────────────────────────────────────────

UI_DIR = Path(__file__).parent / "ui"
app.mount("/ui", StaticFiles(directory=str(UI_DIR)), name="ui")


@app.get("/")
def root():
    return FileResponse(str(UI_DIR / "index.html"))

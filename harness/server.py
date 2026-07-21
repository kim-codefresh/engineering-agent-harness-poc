"""
Harness server — generic, knows nothing about specific workflows.
Serves both the REST API and the management UI.
"""
import base64
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from registry import Registry
from step_types import REGISTRY as STEP_REGISTRY

CONFIG_DIR = Path(__file__).parent / "config"
GITHUB_REPO        = os.getenv("GITHUB_REPO", "kim-codefresh/engineering-agent-harness-poc")
GITHUB_BASE_BRANCH = os.getenv("GITHUB_BASE_BRANCH", "feat/full-cve-flow-human-gates-lift-and-shift")


def _gh(method: str, path: str, body: dict = None):
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        raise HTTPException(status_code=503, detail="GITHUB_TOKEN not configured")
    req = urllib.request.Request(
        f"https://api.github.com/repos/{GITHUB_REPO}{path}",
        method=method,
        headers={
            "Authorization": f"token {token}",
            "Accept":        "application/vnd.github.v3+json",
            "Content-Type":  "application/json",
            "User-Agent":    "agent-harness",
        },
        data=json.dumps(body).encode() if body else None,
    )
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise HTTPException(status_code=e.code, detail=e.read().decode())

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


# ── Config YAML ─────────────────────────────────────────────────────────────

def _read_yaml(subdir: str, entity_id: str) -> str:
    path = CONFIG_DIR / subdir / f"{entity_id}.yaml"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"{entity_id} not found")
    return path.read_text()


@app.get("/api/workflows/{workflow_id}/yaml")
def workflow_yaml(workflow_id: str):
    content = _read_yaml("workflows", workflow_id)
    return {"id": workflow_id, "path": f"harness/config/workflows/{workflow_id}.yaml", "content": content}


@app.get("/api/agents/{agent_id}/yaml")
def agent_yaml(agent_id: str):
    content = _read_yaml("agents", agent_id)
    return {"id": agent_id, "path": f"harness/config/agents/{agent_id}.yaml", "content": content}


# ── Propose as PR ────────────────────────────────────────────────────────────

@app.post("/api/propose")
def propose(body: dict):
    entity_type = body.get("type")   # "workflow" or "agent"
    entity_id   = body.get("id")
    content     = body.get("content")
    description = body.get("description") or f"Update {entity_type} {entity_id}"

    if not all([entity_type, entity_id, content]):
        raise HTTPException(status_code=400, detail="type, id, and content are required")

    subdir    = "workflows" if entity_type == "workflow" else "agents"
    file_path = f"harness/config/{subdir}/{entity_id}.yaml"
    branch    = f"harness/{entity_type}/{entity_id}-{int(time.time())}"

    # 1. Get base branch SHA
    ref = _gh("GET", f"/git/ref/heads/{GITHUB_BASE_BRANCH}")
    if not ref:
        raise HTTPException(status_code=404, detail=f"Base branch '{GITHUB_BASE_BRANCH}' not found")
    base_sha = ref["object"]["sha"]

    # 2. Create branch
    _gh("POST", "/git/refs", {"ref": f"refs/heads/{branch}", "sha": base_sha})

    # 3. Get existing file SHA (required by GitHub API to update an existing file)
    existing = _gh("GET", f"/contents/{file_path}?ref={GITHUB_BASE_BRANCH}")
    file_sha = existing["sha"] if existing else None

    # 4. Commit the YAML file
    commit_body = {
        "message": f"harness: {description}",
        "content": base64.b64encode(content.encode()).decode(),
        "branch":  branch,
    }
    if file_sha:
        commit_body["sha"] = file_sha
    _gh("PUT", f"/contents/{file_path}", commit_body)

    # 5. Open the PR
    pr = _gh("POST", "/pulls", {
        "title": f"harness: {description}",
        "body":  (
            f"Created from the Agent Harness UI\n\n"
            f"**Entity:** `{entity_type}/{entity_id}`\n"
            f"**File:** `{file_path}`\n\n"
            f"---\n{description}"
        ),
        "head": branch,
        "base": GITHUB_BASE_BRANCH,
    })

    return {"pr_url": pr["html_url"], "branch": branch}


# ── UI ───────────────────────────────────────────────────────────────────────

UI_DIR = Path(__file__).parent / "ui"
app.mount("/ui", StaticFiles(directory=str(UI_DIR)), name="ui")


@app.get("/")
def root():
    return FileResponse(str(UI_DIR / "index.html"))

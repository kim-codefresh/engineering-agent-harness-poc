"""
Harness server — generic, knows nothing about specific workflows.
Serves both the REST API and the management UI.

Orchestration: Temporal (replaces LangGraph).
- Workflow runs are started as Temporal workflows
- Human gate decisions are sent as Temporal signals
- State is queried from Temporal (durable across crashes/redeploys)
"""
import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from registry import Registry
from step_types import REGISTRY as STEP_REGISTRY

# ── Temporal client (async, initialised on startup) ──────────────────────────

TEMPORAL_HOST  = os.getenv("TEMPORAL_HOST", "temporal-frontend.temporal.svc.cluster.local:7233")
TEMPORAL_QUEUE = os.getenv("TEMPORAL_TASK_QUEUE", "harness-queue")
_temporal_client = None


async def _get_temporal():
    global _temporal_client
    if _temporal_client is None:
        try:
            from temporalio.client import Client
            from temporal.workflow import HarnessWorkflow  # noqa: F401 — registers workflow
            _temporal_client = await Client.connect(TEMPORAL_HOST)
            print(f"[temporal] Connected to {TEMPORAL_HOST}")
        except Exception as e:
            print(f"[temporal] Could not connect: {e} — falling back to LangGraph")
            _temporal_client = False
    return _temporal_client if _temporal_client else None

CONFIG_DIR = Path(__file__).parent / "config"
GITHUB_REPO        = os.getenv("GITHUB_REPO", "kim-codefresh/engineering-agent-harness-poc")
GITHUB_BASE_BRANCH = os.getenv("GITHUB_BASE_BRANCH", "feat/full-cve-flow-human-gates-lift-and-shift")


def _gh(method: str, path: str, body: dict = None):
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        raise HTTPException(status_code=503, detail="GITHUB_TOKEN not configured")
    # Safety gate — write operations only permitted on kim-codefresh repos
    if method in ("POST", "PUT", "PATCH", "DELETE"):
        repo = os.getenv("GITHUB_REPO", "")
        if not repo.startswith("kim-codefresh/"):
            raise HTTPException(
                status_code=403,
                detail=f"SAFETY BLOCK: repo '{repo}' is not under kim-codefresh. "
                       "This harness may only write to kim-codefresh repositories."
            )
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

# In-memory run tracker (Temporal is authoritative; this is for the UI)
_active_runs: dict = {}


# ── Temporal workflow execution ───────────────────────────────────────────────

@app.post("/api/run/{workflow_id}/{thread_id}")
async def run(workflow_id: str, thread_id: str, ticket: dict):
    from temporal.workflow import HarnessWorkflow, WorkflowInput

    client = await _get_temporal()
    if client:
        # Start Temporal workflow
        handle = await client.start_workflow(
            HarnessWorkflow.run,
            WorkflowInput(
                workflow_id=workflow_id,
                initial_state=ticket,
                thread_id=thread_id,
            ),
            id=thread_id,
            task_queue=TEMPORAL_QUEUE,
        )
        _active_runs[thread_id] = {
            "status":    "running",
            "workflow":  workflow_id,
            "thread_id": thread_id,
            "handle_id": handle.id,
        }
        return {"status": "running", "workflow": workflow_id, "thread_id": thread_id,
                "engine": "temporal"}
    else:
        # Fallback to LangGraph
        graph = reg.graph(workflow_id)
        cfg   = {"configurable": {"thread_id": thread_id}}
        graph.invoke(ticket, cfg)
        gs = graph.get_state(cfg)
        return _lg_response(workflow_id, thread_id, gs)


@app.get("/api/state/{workflow_id}/{thread_id}")
async def get_state(workflow_id: str, thread_id: str):
    client = await _get_temporal()
    if client:
        try:
            handle = client.get_workflow_handle(thread_id)
            desc   = await handle.describe()
            run_info = _active_runs.get(thread_id, {})
            return {
                "status":    desc.status.name.lower(),
                "workflow":  workflow_id,
                "thread_id": thread_id,
                "engine":    "temporal",
                "pending_gates": run_info.get("pending_gates", []),
            }
        except Exception as e:
            raise HTTPException(status_code=404, detail=str(e))
    else:
        graph = reg.graph(workflow_id)
        cfg   = {"configurable": {"thread_id": thread_id}}
        gs    = graph.get_state(cfg)
        if not gs.values:
            raise HTTPException(status_code=404, detail="Thread not found")
        return _lg_response(workflow_id, thread_id, gs)


@app.post("/api/decision/{workflow_id}/{thread_id}")
async def submit_decision(workflow_id: str, thread_id: str, body: dict):
    field    = body.get("field")
    decision = body.get("decision")
    if not field or not decision:
        raise HTTPException(status_code=400, detail="field and decision are required")

    client = await _get_temporal()
    if client:
        handle = client.get_workflow_handle(thread_id)
        # Send Temporal signal — durable, survives crashes
        await handle.signal(
            "submit_gate_decision",
            field,
            decision,
        )
        return {"status": "signal_sent", "field": field, "decision": decision,
                "engine": "temporal"}
    else:
        # LangGraph fallback
        wf_config = reg.workflows.get(workflow_id, {})
        steps = wf_config.get("steps", [])
        gate_step = next((s for s in steps if "gate" in s
                          and s["gate"].get("output_field") == field), None)
        options = gate_step["gate"]["options"] if gate_step else []
        if decision not in options:
            raise HTTPException(status_code=400, detail=f"Invalid decision. Options: {options}")
        graph = reg.graph(workflow_id)
        cfg   = {"configurable": {"thread_id": thread_id}}
        graph.update_state(cfg, {field: decision})
        graph.invoke(None, cfg)
        return _lg_response(workflow_id, thread_id, graph.get_state(cfg))


def _lg_response(wf_id: str, thread_id: str, graph_state) -> dict:
    """LangGraph fallback response format."""
    if graph_state.next:
        node = graph_state.next[0]
        meta = reg.gate_meta(wf_id).get(node, {})
        return {
            "status":    "waiting_for_human",
            "workflow":  wf_id,
            "thread_id": thread_id,
            "engine":    "langgraph",
            "gate": {
                "gate":    node,
                "options": meta.get("options", []),
                "field":   meta.get("output_field"),
                "state":   {k: v for k, v in graph_state.values.items()
                            if k in ("evidence", "assessment", "fix", "validation", "pr")},
            },
        }
    return {
        "status":    "complete",
        "workflow":  wf_id,
        "thread_id": thread_id,
        "engine":    "langgraph",
        "outcome":   graph_state.values.get("outcome"),
    }


# ── Registry APIs ────────────────────────────────────────────────────────────

@app.get("/api/workflows")
def list_workflows():
    return reg.workflows


@app.get("/api/agents")
def list_agents():
    return reg.agents


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

@app.get("/api/branches")
def list_branches():
    data = _gh("GET", "/branches?per_page=50")
    if not data:
        return []
    return [b["name"] for b in data]


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
    base      = body.get("base_branch") or GITHUB_BASE_BRANCH

    # 1. Get base branch SHA
    ref = _gh("GET", f"/git/ref/heads/{base}")
    if not ref:
        raise HTTPException(status_code=404, detail=f"Base branch '{base}' not found")
    base_sha = ref["object"]["sha"]

    # 2. Create branch
    _gh("POST", "/git/refs", {"ref": f"refs/heads/{branch}", "sha": base_sha})

    # 3. Get existing file SHA (required by GitHub API to update an existing file)
    existing = _gh("GET", f"/contents/{file_path}?ref={base}")
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
        "base": base,
    })

    return {"pr_url": pr["html_url"], "branch": branch}


# ── Linear webhook ───────────────────────────────────────────────────────────

@app.post("/webhook/linear")
async def linear_webhook(request: Request, background_tasks: BackgroundTasks):
    body = await request.body()

    secret = os.getenv("LINEAR_WEBHOOK_SECRET", "")
    if secret:
        sig      = request.headers.get("linear-signature", "")
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            raise HTTPException(status_code=401, detail="Invalid signature")

    payload = json.loads(body)

    if payload.get("type") != "Issue":
        return {"status": "ignored", "reason": "not an Issue event"}

    issue        = payload.get("data", {})
    labels       = issue.get("labels", [])
    label_names  = [l.get("name", "") for l in labels]

    if "kim-test-harness" not in label_names:
        return {"status": "ignored", "reason": "label 'kim-test-harness' not present"}

    prev_label_ids = set((payload.get("updatedFrom") or {}).get("labelIds", []))
    trigger_label  = next((l for l in labels if l.get("name") == "kim-test-harness"), {})
    if trigger_label.get("id") in prev_label_ids:
        return {"status": "ignored", "reason": "label was already applied"}

    title       = issue.get("title", "")
    description = issue.get("description", "") or ""
    issue_id    = issue.get("id", "unknown")

    cve_match = re.search(r'CVE-\d{4}-\d+', title + " " + description, re.IGNORECASE)
    cve_id    = cve_match.group(0).upper() if cve_match else f"LINEAR-{issue_id[:8].upper()}"

    pkg_match = re.search(r'[Pp]ackage[:\s]+([a-zA-Z0-9_\-]+)', description)
    ver_match = re.search(r'[Vv]ersion[:\s]+([\d.]+)', description)
    package   = pkg_match.group(1) if pkg_match else "unknown-package"
    version   = ver_match.group(1) if ver_match else "0.0.0"

    # Extract Linear identifier (e.g. CFS-7827) for branch naming
    identifier = issue.get("identifier", issue_id[:8].upper())
    branch_name = f"{identifier.lower()}-fix"
    thread_id   = f"cve_remediation-{identifier.lower()}-{int(time.time())}"

    # Pass the full raw ticket so parse_cve_ticket skill can do proper extraction
    ticket = {
        "ticket_id":          identifier,
        "ticket_title":       title,
        "ticket_description": description,
        "linear_issue":       {"id": issue_id, "url": issue.get("url", ""), "identifier": identifier},
        "branch_name":        branch_name,
        "target_repo":        os.getenv("TARGET_REPO", "kim-codefresh/cf-api-test"),
    }

    background_tasks.add_task(_start_workflow_async, "cve_remediation", thread_id, ticket)
    return {"status": "accepted", "thread_id": thread_id, "ticket_id": identifier, "branch": branch_name}


def _start_workflow_async(workflow_id: str, thread_id: str, ticket: dict):
    """Start workflow — tries Temporal first, falls back to LangGraph."""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_start_temporal(workflow_id, thread_id, ticket))
    except Exception as e:
        print(f"[webhook] Temporal failed, falling back to LangGraph: {e}")
        try:
            graph = reg.graph(workflow_id)
            graph.invoke(ticket, {"configurable": {"thread_id": thread_id}})
        except Exception as e2:
            print(f"[webhook] LangGraph fallback also failed: {e2}")


async def _start_temporal(workflow_id: str, thread_id: str, ticket: dict):
    from temporal.workflow import HarnessWorkflow, WorkflowInput
    client = await _get_temporal()
    if not client:
        raise RuntimeError("Temporal not available")
    await client.start_workflow(
        HarnessWorkflow.run,
        WorkflowInput(workflow_id=workflow_id, initial_state=ticket, thread_id=thread_id),
        id=thread_id,
        task_queue=TEMPORAL_QUEUE,
    )
    _active_runs[thread_id] = {"status": "running", "workflow": workflow_id, "thread_id": thread_id}
    print(f"[temporal] Started workflow {thread_id}")


@app.get("/api/runs")
async def list_runs():
    """List active runs from Temporal + in-memory tracker."""
    runs = dict(_active_runs)
    client = await _get_temporal()
    if client:
        try:
            async for wf in client.list_workflows(f"TaskQueue='{TEMPORAL_QUEUE}'"):
                tid = wf.id
                if tid not in runs:
                    runs[tid] = {"thread_id": tid, "status": wf.status.name.lower()}
        except Exception:
            pass
    return runs


# ── UI ───────────────────────────────────────────────────────────────────────

UI_DIR = Path(__file__).parent / "ui"
app.mount("/ui", StaticFiles(directory=str(UI_DIR)), name="ui")


@app.get("/")
def root():
    return FileResponse(str(UI_DIR / "index.html"))

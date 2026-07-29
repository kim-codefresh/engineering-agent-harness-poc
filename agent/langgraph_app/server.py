"""
CVE Harness HTTP server.

The graph pauses at each human gate. Decisions arrive via HTTP, state
persists in memory for the lifetime of the pod.

Endpoints:
  POST /run/{thread_id}          body: CVE ticket JSON — starts the workflow
  GET  /state/{thread_id}        current gate + context
  POST /decision/{thread_id}     body: {"decision": "<option>"} — resume

Example (with kubectl port-forward svc/cve-harness 8000:8000):
  curl -X POST localhost:8000/run/CVE-2026-99999 \
       -H "Content-Type: application/json" -d @sample_cve.json
  curl localhost:8000/state/CVE-2026-99999
  curl -X POST localhost:8000/decision/CVE-2026-99999 \
       -H "Content-Type: application/json" -d '{"decision": "fix_authorized"}'
"""
from fastapi import FastAPI, HTTPException
from langgraph.checkpoint.memory import MemorySaver

from graph import build_graph

app = FastAPI(title="CVE Harness")

GATE_NODES = ["security_owner_review", "scope_authorization", "code_owner_review"]

GATE_OPTIONS = {
    "security_owner_review": ["no_fix_needed", "fix_authorized", "escalate"],
    "scope_authorization":   ["authorized", "rejected"],
    "code_owner_review":     ["approved", "changes_requested"],
}

GATE_FIELDS = {
    "security_owner_review": "disposition",
    "scope_authorization":   "scope_authorized",
    "code_owner_review":     "pr_decision",
}

checkpointer = MemorySaver()
graph_app = build_graph().compile(
    checkpointer=checkpointer,
    interrupt_before=GATE_NODES,
)


def _cfg(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


def _gate_context(values: dict, node: str) -> dict:
    ctx = {"gate": node, "options": GATE_OPTIONS.get(node, [])}
    if node == "security_owner_review":
        ctx["evidence"]   = values.get("evidence", {})
        ctx["assessment"] = values.get("assessment", {})
    elif node == "scope_authorization":
        ctx["evidence"]   = values.get("evidence", {})
        ctx["fix_complexity"] = values.get("assessment", {}).get("fix_complexity")
    elif node == "code_owner_review":
        ctx["pr"]         = values.get("pr", {})
        ctx["validation"] = values.get("validation", {})
    return ctx


def _response(thread_id: str, state) -> dict:
    if state.next:
        return {
            "status":    "waiting_for_human",
            "thread_id": thread_id,
            "gate":      _gate_context(state.values, state.next[0]),
        }
    return {
        "status":    "complete",
        "thread_id": thread_id,
        "outcome":   state.values.get("outcome"),
    }


@app.post("/run/{thread_id}")
def run(thread_id: str, ticket: dict):
    graph_app.invoke(
        {
            "cve_id":       ticket["cve_id"],
            "advisory":     ticket["advisory"],
            "dependencies": ticket["dependencies"],
        },
        _cfg(thread_id),
    )
    return _response(thread_id, graph_app.get_state(_cfg(thread_id)))


@app.get("/state/{thread_id}")
def get_state(thread_id: str):
    state = graph_app.get_state(_cfg(thread_id))
    if not state.values:
        raise HTTPException(status_code=404, detail="Thread not found")
    return _response(thread_id, state)


@app.post("/decision/{thread_id}")
def submit_decision(thread_id: str, body: dict):
    state = graph_app.get_state(_cfg(thread_id))
    if not state.next:
        raise HTTPException(status_code=400, detail="No pending gate for this thread")

    node     = state.next[0]
    decision = body.get("decision")
    options  = GATE_OPTIONS.get(node, [])

    if decision not in options:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid decision '{decision}' for gate '{node}'. Options: {options}",
        )

    field = GATE_FIELDS[node]
    value = (decision == "authorized") if node == "scope_authorization" else decision

    graph_app.update_state(_cfg(thread_id), {field: value})
    graph_app.invoke(None, _cfg(thread_id))

    return _response(thread_id, graph_app.get_state(_cfg(thread_id)))

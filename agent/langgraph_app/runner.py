"""
Local interactive runner — simulates the full CVE flow with mock Linear gates.

Each human gate prints what Linear/GitHub would show and reads the decision
from stdin. The runner uses LangGraph's interrupt_before + update_state pattern:
  1. graph runs until it hits a gate node
  2. runner shows context, collects decision, injects it via update_state()
  3. graph resumes from the gate node with the decision already in state

Usage:
    cd agent/langgraph_app
    ANTHROPIC_API_KEY=sk-ant-... python runner.py                        # Anthropic (default)
    LITELLM_MODEL=gemini/gemini-1.5-flash GEMINI_API_KEY=... python runner.py  # Gemini
    LITELLM_MODEL=groq/llama-3.1-70b-versatile GROQ_API_KEY=... python runner.py  # Groq
"""
import json
import sys
import time

from langgraph.checkpoint.memory import MemorySaver

from graph import build_graph

SAMPLE_CVE = {
    "cve_id": "CVE-2026-99999",
    "advisory": {
        "id": "CVE-2026-99999",
        "summary": (
            "Remote code execution via crafted input in demo-package "
            "versions 1.2.0 and 1.2.1. No authentication required."
        ),
        "package": "demo-package",
        "affected_versions": ["1.2.0", "1.2.1"],
    },
    "dependencies": {
        "demo-package": "1.2.0",
        "requests": "2.31.0",
        "pydantic": "2.5.0",
    },
}

GATE_NODES = ["security_owner_review", "scope_authorization", "code_owner_review"]


# ── Gate handlers ────────────────────────────────────────────

def _prompt(options: list[str]) -> str:
    print(f"\n  Options: {' | '.join(options)}")
    while True:
        raw = input("  Your decision: ").strip().lower()
        if raw in options:
            return raw
        print(f"  Invalid. Choose from: {', '.join(options)}")


def _gate_security_owner(app, config, state: dict):
    evidence = state["evidence"]
    assessment = state["assessment"]

    print("\n" + "═" * 60)
    print("📋  MOCK LINEAR — Security Review Required")
    print("═" * 60)
    print(f"  CVE:        {evidence['advisory_id']}")
    print(f"  Package:    {evidence['affected_package']}@{evidence['our_locked_version']}")
    print(f"  Affected:   {'YES ⚠️' if assessment['affected'] else 'NO ✅'}")
    print(f"  Risk:       {assessment['risk_level'].upper()}")
    print(f"  Reasoning:  {assessment['reasoning']}")
    print(f"  Agent rec:  {assessment['recommended_disposition']}")
    print("─" * 60)
    print("  [→ In production: posted as Linear comment, awaiting reply]")
    print("═" * 60)

    decision = _prompt(["no_fix_needed", "fix_authorized", "escalate"])
    app.update_state(config, {"disposition": decision})


def _gate_scope_authorization(app, config, state: dict):
    evidence = state["evidence"]
    assessment = state["assessment"]

    print("\n" + "═" * 60)
    print("📋  MOCK LINEAR — Scope Authorization Required")
    print("═" * 60)
    print(f"  CVE:        {evidence['advisory_id']}")
    print(f"  Package:    {evidence['affected_package']}@{evidence['our_locked_version']}")
    print(f"  Proposed:   bump to latest safe version")
    print(f"  Complexity: {assessment.get('fix_complexity', 'simple')}")
    print(f"  Scope:      1 ticket · 1 repository · 1 branch")
    print("─" * 60)
    print("  [→ In production: repository owner approves via Linear]")
    print("═" * 60)

    decision = _prompt(["authorized", "rejected"])
    app.update_state(config, {"scope_authorized": decision == "authorized"})


def _gate_code_owner(app, config, state: dict):
    pr = state["pr"]
    validation = state["validation"]

    print("\n" + "═" * 60)
    print("📋  MOCK LINEAR — PR Review Required")
    print("═" * 60)
    print(f"  PR:       {pr['url']}")
    print(f"  Title:    {pr['title']}")
    print(f"  Changes:")
    for line in pr["diff_summary"].split("\n"):
        print(f"    {line}")
    print(f"  Tests:    ✅ {validation['tests_run']} passed, 0 failed")
    print(f"  Rescan:   ✅ CVE no longer detected")
    print("─" * 60)
    print("  [→ In production: GitHub PR — merge blocked until approved]")
    print("═" * 60)

    decision = _prompt(["approved", "changes_requested"])
    app.update_state(config, {"pr_decision": decision})


GATE_HANDLERS = {
    "security_owner_review": _gate_security_owner,
    "scope_authorization": _gate_scope_authorization,
    "code_owner_review": _gate_code_owner,
}


# ── Runner ───────────────────────────────────────────────────

def run(ticket: dict) -> dict:
    thread_id = f"cve-{ticket['cve_id']}-{int(time.time())}"
    config = {"configurable": {"thread_id": thread_id}}

    app = build_graph().compile(
        checkpointer=MemorySaver(),
        interrupt_before=GATE_NODES,
    )

    print(f"\n{'━' * 60}")
    print(f"  CVE Workflow — {ticket['cve_id']}")
    print(f"  Thread: {thread_id}")
    print(f"{'━' * 60}\n")

    current_input = {
        "cve_id": ticket["cve_id"],
        "advisory": ticket["advisory"],
        "dependencies": ticket["dependencies"],
    }

    while True:
        app.invoke(current_input, config)
        current_input = None  # subsequent resumes pass None

        graph_state = app.get_state(config)
        if not graph_state.next:
            break

        next_node = graph_state.next[0]
        handler = GATE_HANDLERS.get(next_node)
        if handler is None:
            break

        handler(app, config, graph_state.values)

    final = app.get_state(config).values
    outcome = final.get("outcome", {})

    print(f"\n{'━' * 60}")
    print(f"  WORKFLOW COMPLETE")
    print(f"  Status:  {outcome.get('status', 'unknown').upper()}")
    print(f"  Summary: {outcome.get('summary', '')}")
    print(f"{'━' * 60}\n")

    return final


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else None
    ticket = SAMPLE_CVE
    if path:
        with open(path) as f:
            ticket = json.load(f)
    run(ticket)

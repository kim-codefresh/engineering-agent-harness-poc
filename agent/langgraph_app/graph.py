from typing import Optional
from typing_extensions import TypedDict

from langgraph.graph import END, StateGraph

from nodes import (
    assess_finding,
    code_owner_review,
    gather_evidence,
    merge_pr,
    open_pr,
    prepare_fix,
    record_outcome,
    run_validation,
    scope_authorization,
    security_owner_review,
)


class CVEState(TypedDict, total=False):
    # Input
    cve_id: str
    advisory: dict
    dependencies: dict
    # Stage 2: evidence
    evidence: dict
    # Stage 3: assessment
    assessment: dict
    # Stage 4: security owner decision
    disposition: str          # no_fix_needed | fix_authorized | escalate
    # Stage 5: scope authorization
    scope_authorized: bool
    # Stage 6: fix
    fix: dict
    # Stage 7: validation
    validation: dict
    # Stage 8: PR
    pr: dict
    pr_decision: str          # approved | changes_requested
    # Stage 9: outcome
    outcome: dict


# ── Routing functions ────────────────────────────────────────

def route_after_security_review(state: CVEState) -> str:
    return state["disposition"]


def route_after_scope(state: CVEState) -> str:
    return "prepare_fix" if state.get("scope_authorized") else "record_outcome"


def route_after_validation(state: CVEState) -> str:
    return "open_pr" if state["validation"]["passed"] else "record_outcome"


def route_after_pr_review(state: CVEState) -> str:
    return state["pr_decision"]


# ── Graph builder ────────────────────────────────────────────

def build_graph_auto() -> StateGraph:
    """Graph variant for k8s: uses LLM recommendation directly, no human gates."""
    from nodes.assess_finding import assess_finding as _assess

    def assess_and_decide(state: CVEState) -> dict:
        result = _assess(state)
        # promote LLM recommendation straight to disposition
        result["disposition"] = result["assessment"]["recommended_disposition"]
        result["scope_authorized"] = True
        result["pr_decision"] = "approved"
        return result

    g = StateGraph(CVEState)
    g.add_node("gather_evidence", gather_evidence)
    g.add_node("assess_finding", assess_and_decide)
    g.add_node("prepare_fix", prepare_fix)
    g.add_node("run_validation", run_validation)
    g.add_node("open_pr", open_pr)
    g.add_node("merge_pr", merge_pr)
    g.add_node("record_outcome", record_outcome)

    g.set_entry_point("gather_evidence")
    g.add_edge("gather_evidence", "assess_finding")
    g.add_conditional_edges(
        "assess_finding",
        route_after_security_review,
        {
            "no_fix_needed": "record_outcome",
            "escalate": "record_outcome",
            "fix_authorized": "prepare_fix",
        },
    )
    g.add_edge("prepare_fix", "run_validation")
    g.add_conditional_edges(
        "run_validation",
        route_after_validation,
        {"open_pr": "open_pr", "record_outcome": "record_outcome"},
    )
    g.add_edge("open_pr", "merge_pr")
    g.add_edge("merge_pr", "record_outcome")
    g.add_edge("record_outcome", END)
    return g


def build_graph() -> StateGraph:
    g = StateGraph(CVEState)

    # Nodes
    g.add_node("gather_evidence", gather_evidence)
    g.add_node("assess_finding", assess_finding)
    g.add_node("security_owner_review", security_owner_review)
    g.add_node("scope_authorization", scope_authorization)
    g.add_node("prepare_fix", prepare_fix)
    g.add_node("run_validation", run_validation)
    g.add_node("open_pr", open_pr)
    g.add_node("code_owner_review", code_owner_review)
    g.add_node("merge_pr", merge_pr)
    g.add_node("record_outcome", record_outcome)

    # Linear edges
    g.set_entry_point("gather_evidence")
    g.add_edge("gather_evidence", "assess_finding")
    g.add_edge("assess_finding", "security_owner_review")

    # Gate 1: security owner sets disposition
    g.add_conditional_edges(
        "security_owner_review",
        route_after_security_review,
        {
            "no_fix_needed": "record_outcome",
            "escalate": "record_outcome",
            "fix_authorized": "scope_authorization",
        },
    )

    # Gate 2: repo owner authorizes scope
    g.add_conditional_edges(
        "scope_authorization",
        route_after_scope,
        {
            "prepare_fix": "prepare_fix",
            "record_outcome": "record_outcome",
        },
    )

    g.add_edge("prepare_fix", "run_validation")

    # Deterministic validation gate
    g.add_conditional_edges(
        "run_validation",
        route_after_validation,
        {
            "open_pr": "open_pr",
            "record_outcome": "record_outcome",
        },
    )

    g.add_edge("open_pr", "code_owner_review")

    # Gate 3: code owner reviews PR
    g.add_conditional_edges(
        "code_owner_review",
        route_after_pr_review,
        {
            "approved": "merge_pr",
            "changes_requested": "record_outcome",
        },
    )

    g.add_edge("merge_pr", "record_outcome")
    g.add_edge("record_outcome", END)

    return g


if __name__ == "__main__":
    import json
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else None
    ticket = {
        "cve_id": "CVE-2026-99999",
        "advisory": {
            "id": "CVE-2026-99999",
            "summary": "Remote code execution in demo-package 1.2.0 and 1.2.1.",
            "package": "demo-package",
            "affected_versions": ["1.2.0", "1.2.1"],
        },
        "dependencies": {"demo-package": "1.2.0"},
    }
    if path:
        with open(path) as f:
            ticket = json.load(f)

    app = build_graph_auto().compile()
    result = app.invoke({
        "cve_id": ticket["cve_id"],
        "advisory": ticket["advisory"],
        "dependencies": ticket["dependencies"],
    })
    print(json.dumps(result, indent=2))

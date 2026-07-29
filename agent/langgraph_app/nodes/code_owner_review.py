def code_owner_review(state):
    """
    Gate 3 — code owner reviews and approves the draft PR.

    Runner injects pr_decision into state before this node runs.
    """
    print(f"[code_owner_review] PR decision: {state.get('pr_decision')}")
    return {"pr_decision": state["pr_decision"]}

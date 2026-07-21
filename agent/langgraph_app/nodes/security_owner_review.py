def security_owner_review(state):
    """
    Gate 1 — security owner disposition.

    The runner pauses BEFORE this node (interrupt_before), shows the assessment
    to the human, collects their decision, and injects it into state via
    update_state(). By the time this node runs, state["disposition"] is already
    set. This node just logs the confirmed decision.
    """
    print(f"[security_owner_review] Disposition confirmed: {state['disposition']}")
    return {"disposition": state["disposition"]}

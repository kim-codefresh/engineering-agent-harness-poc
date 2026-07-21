def record_outcome(state):
    disposition = state.get("disposition", "unknown")
    pr = state.get("pr", {})
    validation = state.get("validation", {})
    scope_authorized = state.get("scope_authorized", True)
    pr_decision = state.get("pr_decision")

    if pr.get("status") == "merged":
        status, summary = "fixed", f"CVE resolved — PR #{pr['number']} merged."
    elif disposition == "no_fix_needed":
        status, summary = "no_fix_needed", "Not affected. No fix required."
    elif disposition == "escalate":
        status, summary = "escalated", "Escalated for manual triage."
    elif not scope_authorized:
        status, summary = "scope_rejected", "Repository owner rejected scope. No fix prepared."
    elif validation and not validation.get("passed"):
        status, summary = "validation_failed", "Tests or rescan failed. Fix not submitted."
    elif pr_decision == "changes_requested":
        status, summary = "changes_requested", "Code owner requested changes. New authorization required."
    else:
        status, summary = "ended", "Workflow ended."

    outcome = {"status": status, "summary": summary, "disposition": disposition}

    print(f"\n[record_outcome] {'✅' if status == 'fixed' else '⚠️'} {status.upper()} — {summary}")
    return {"outcome": outcome}

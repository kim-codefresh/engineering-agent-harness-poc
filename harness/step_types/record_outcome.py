def execute(state: dict, config: dict) -> dict:
    disposition = state.get("disposition", "unknown")
    pr          = state.get("pr", {})
    status      = "fixed" if pr.get("status") == "merged" else disposition
    summary     = (f"CVE resolved — PR #{pr['number']} merged." if status == "fixed"
                   else f"Outcome: {disposition}.")
    return {"outcome": {"status": status, "summary": summary, "disposition": disposition}}

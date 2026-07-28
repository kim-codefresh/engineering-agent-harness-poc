"""
auto_route — deterministic routing skill.

Reads state and sets the routing field so the workflow can
take the correct conditional edge — no human signal needed.
"""


def route_research(state: dict, config: dict) -> dict:
    """Map research_cve output → research_exit routing field."""
    if state.get("patches") and state.get("all_patches_found"):
        return {"research_exit": "ready"}
    if state.get("mitigation_details"):
        return {"research_exit": "mitigation"}
    if state.get("needs_human_guidance"):
        return {"research_exit": "needs_human_guidance"}
    if state.get("retry_exhausted"):
        return {"research_exit": "retry_exhausted"}
    # Default: needs human if nothing clear
    return {"research_exit": "needs_human_guidance"}


def route_checks(state: dict, config: dict) -> dict:
    """Map run_local_checks output → local_checks_routing."""
    if state.get("checks_passed"):
        return {"local_checks_routing": "passed"}
    return {"local_checks_routing": "failed"}


def route_pr_checks(state: dict, config: dict) -> dict:
    """Map poll_pr_checks output → pr_checks_routing."""
    checks = state.get("pr_checks", {})
    if isinstance(checks, dict):
        fc = checks.get("failure_class")
        if fc in ("infra_flaky",):
            return {"pr_checks_routing": "passed"}   # retry pr_validator
        if checks.get("checks_status") == "passed":
            return {"pr_checks_routing": "passed"}
    return {"pr_checks_routing": "failed"}


def route_e2e(state: dict, config: dict) -> dict:
    """Map trigger_e2e output → e2e_routing."""
    e2e = state.get("e2e_result", {})
    status = e2e.get("status", "passed") if isinstance(e2e, dict) else "passed"
    if "fail" in status:
        return {"e2e_routing": "failed"}
    return {"e2e_routing": "passed"}


def execute(state: dict, config: dict) -> dict:
    """Dispatcher — reads route_type from config."""
    route_type = config.get("route_type", "research")
    fn = {
        "research":  route_research,
        "checks":    route_checks,
        "pr_checks": route_pr_checks,
        "e2e":       route_e2e,
    }.get(route_type, route_research)
    result = fn(state, config)
    print(f"[auto_route:{route_type}] → {result}")
    return result

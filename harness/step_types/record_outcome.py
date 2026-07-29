import json
import os
import urllib.request


def _linear_request(api_key: str, query: str, variables: dict = None) -> dict:
    body = json.dumps({"query": query, **({"variables": variables} if variables else {})})
    req = urllib.request.Request("https://api.linear.app/graphql",
        data=body.encode(),
        headers={"Authorization": api_key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read())
    except Exception:
        return {}


def execute(state: dict, config: dict) -> dict:
    pr          = state.get("pr", {})
    pr_decision = state.get("pr_decision", "")
    ticket_id   = state.get("ticket_id", "")

    # Determine outcome
    if pr.get("status") == "merged" or pr_decision == "approved":
        status  = "fixed"
        summary = f"CVE resolved — PR #{pr.get('number','?')} merged. ✅"
    elif state.get("mitigation_decision") == "done":
        status  = "mitigated"
        summary = "Mitigation accepted."
    elif state.get("escalation_decision") == "close":
        status  = "closed"
        summary = "Escalated and closed without fix."
    else:
        status  = "ended"
        summary = "Workflow ended."

    outcome = {"status": status, "summary": summary}
    print(f"\n[record_outcome] {'✅' if status == 'fixed' else '⚠️'} {status.upper()} — {summary}")

    linear_api_key = os.getenv("LINEAR_API_KEY", "")
    if not linear_api_key or not ticket_id:
        return {"outcome": outcome}

    try:
        # Find issue ID
        data = _linear_request(linear_api_key, "{ issues(first:20) { nodes { id identifier } } }")
        issue_id = None
        for issue in data.get("data", {}).get("issues", {}).get("nodes", []):
            if issue.get("identifier") == ticket_id:
                issue_id = issue["id"]
                break

        if not issue_id:
            return {"outcome": outcome}

        # Post completion comment
        pr_url = pr.get("url", "")
        body = (
            f"{'✅' if status == 'fixed' else '⚠️'} **Agent Harness — {status.upper()}**\n\n"
            f"{summary}"
            + (f"\n\nPR: {pr_url}" if pr_url else "")
            + "\n\n*Workflow complete.*"
        )
        _linear_request(linear_api_key,
            "mutation($issueId: String!, $body: String!) { commentCreate(input: {issueId: $issueId, body: $body}) { success } }",
            {"issueId": issue_id, "body": body})
        print(f"[record_outcome] Posted completion comment to {ticket_id}")

        # Move to Done if fixed
        if status == "fixed":
            states = _linear_request(linear_api_key, "{ workflowStates(first:50) { nodes { id name type } } }")
            done_state = next(
                (s for s in states.get("data", {}).get("workflowStates", {}).get("nodes", [])
                 if s.get("type") == "completed"),
                None
            )
            if done_state:
                result = _linear_request(linear_api_key,
                    "mutation($id: String!, $stateId: String!) { issueUpdate(id: $id, input: {stateId: $stateId}) { success } }",
                    {"id": issue_id, "stateId": done_state["id"]})
                success = result.get("data", {}).get("issueUpdate", {}).get("success", False)
                print(f"[record_outcome] Moved {ticket_id} to Done: {success}")

    except Exception as e:
        print(f"[record_outcome] Linear update failed: {e}")

    return {"outcome": outcome}

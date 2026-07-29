"""
Temporal activities — the actual execution units.

Each activity runs in the Temporal worker process.
Temporal handles retries, timeouts, and heartbeating.
"""
import json
import os
import sys
import time
import urllib.request
import urllib.error
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from temporalio import activity

# Make harness root importable
sys.path.insert(0, str(Path(__file__).parent.parent))

from step_types import REGISTRY as STEP_REGISTRY
from graph_builder import _build_llm_agent_node


CONFIG_DIR = Path(__file__).parent.parent / "config"


@dataclass
class NotifyStuckInput:
    thread_id: str
    step_id: str
    error: str
    attempts: int
    ticket_id: str
    workflow_id: str
    run_id: str


@dataclass
class NotifyFailureInput:
    thread_id: str
    workflow_id: str
    error: str
    ticket_id: str


@dataclass
class NotifyGateInput:
    thread_id: str
    gate: str
    field: str
    options: list
    state: dict


@dataclass
class RunAgentInput:
    agent_id: str
    state: dict
    thread_id: str


@dataclass
class RunCodeStepInput:
    step_type: str
    state: dict
    config: dict
    thread_id: str


@activity.defn(name="notify_stuck")
def notify_stuck(input: NotifyStuckInput) -> dict:
    """Post Linear comment when a step is stuck after N failures, with recovery links."""
    linear_api_key = os.getenv("LINEAR_API_KEY", "")
    harness_url    = os.getenv("HARNESS_URL", "http://agent-harness.agent-harness.svc.cluster.local:8000")
    temporal_ui    = os.getenv("TEMPORAL_UI_URL", "http://localhost:8088")

    # Recovery links — hit the harness API which sends Temporal signals
    base = f"{harness_url}/api/runs/{input.thread_id}/recover"
    temporal_link = f"{temporal_ui}/namespaces/default/workflows/{input.workflow_id}/{input.run_id}/history"

    body = (
        f"⚠️ **Agent Harness — Stuck at step `{input.step_id}`**\n\n"
        f"Failed **{input.attempts}** times and cannot recover automatically.\n\n"
        f"**Error:** `{input.error}`\n\n"
        f"**What would you like to do?**\n\n"
        f"• **Retry** (a fix was deployed, try again):\n"
        f"  `POST {base}` `{{\"action\":\"retry\"}}`\n\n"
        f"• **Skip to PR review** (PR already open, go straight to human gate):\n"
        f"  `POST {base}` `{{\"action\":\"skip_to\",\"step\":\"pr_gate\"}}`\n\n"
        f"• **Go back to research** (agent tries a different approach):\n"
        f"  `POST {base}` `{{\"action\":\"skip_to\",\"step\":\"research\"}}`\n\n"
        f"• **Cancel this run**:\n"
        f"  `POST {base}` `{{\"action\":\"cancel\"}}`\n\n"
        f"👉 [View step history in Temporal]({temporal_link})\n\n"
        f"*Workflow is paused and waiting for your decision.*"
    )

    if not linear_api_key:
        activity.logger.warning("No LINEAR_API_KEY — cannot post stuck comment")
        return {"ok": False}

    try:
        search = json.dumps({"query": "{ issues(first:20) { nodes { id identifier } } }"})
        req = urllib.request.Request("https://api.linear.app/graphql",
            data=search.encode(),
            headers={"Authorization": linear_api_key, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read())
        issue_id = None
        for issue in data.get("data", {}).get("issues", {}).get("nodes", []):
            if issue.get("identifier") == input.ticket_id:
                issue_id = issue["id"]
                break
        if not issue_id:
            return {"ok": False}

        mutation = json.dumps({
            "query": "mutation($issueId: String!, $body: String!) { commentCreate(input: {issueId: $issueId, body: $body}) { success } }",
            "variables": {"issueId": issue_id, "body": body}
        })
        req2 = urllib.request.Request("https://api.linear.app/graphql",
            data=mutation.encode(),
            headers={"Authorization": linear_api_key, "Content-Type": "application/json"})
        with urllib.request.urlopen(req2, timeout=5) as r:
            result = json.loads(r.read())
        success = result.get("data", {}).get("commentCreate", {}).get("success", False)
        activity.logger.info(f"Posted stuck comment: {success}")
        return {"ok": success}
    except Exception as e:
        activity.logger.warning(f"Failed to post stuck comment: {e}")
        return {"ok": False}


@activity.defn(name="notify_harness_failure")
def notify_harness_failure(input: NotifyFailureInput) -> dict:
    """Post a Linear comment when the harness workflow fails."""
    linear_api_key = os.getenv("LINEAR_API_KEY", "")
    temporal_ui    = os.getenv("TEMPORAL_UI_URL", "http://localhost:8088")
    harness_url    = os.getenv("HARNESS_URL", "http://agent-harness.agent-harness.svc.cluster.local:8000")

    temporal_link = (
        f"{temporal_ui}/namespaces/default/workflows"
        f"/{input.workflow_id}"
    )

    body = (
        f"🚨 **Agent Harness — Workflow Failed**\n\n"
        f"**Thread:** `{input.thread_id}`\n"
        f"**Error:** {input.error}\n\n"
        f"👉 [View in Temporal UI]({temporal_link}) — see the full step history and failure reason\n"
        f"👉 [Harness UI]({harness_url})\n\n"
        f"*This is an automated notification from the agent harness.*"
    )

    if not linear_api_key:
        activity.logger.warning(f"No LINEAR_API_KEY — cannot post failure comment for {input.ticket_id}")
        return {"ok": False}

    try:
        # Find the issue
        search = json.dumps({"query": "{ issues(first:20) { nodes { id identifier } } }"})
        req = urllib.request.Request("https://api.linear.app/graphql",
            data=search.encode(),
            headers={"Authorization": linear_api_key, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read())
        issue_id = None
        for issue in data.get("data", {}).get("issues", {}).get("nodes", []):
            if issue.get("identifier") == input.ticket_id:
                issue_id = issue["id"]
                break

        if not issue_id:
            activity.logger.warning(f"Issue {input.ticket_id} not found in Linear")
            return {"ok": False}

        mutation = json.dumps({
            "query": "mutation($issueId: String!, $body: String!) { commentCreate(input: {issueId: $issueId, body: $body}) { success } }",
            "variables": {"issueId": issue_id, "body": body}
        })
        req2 = urllib.request.Request("https://api.linear.app/graphql",
            data=mutation.encode(),
            headers={"Authorization": linear_api_key, "Content-Type": "application/json"})
        with urllib.request.urlopen(req2, timeout=5) as r:
            result = json.loads(r.read())
        success = result.get("data", {}).get("commentCreate", {}).get("success", False)
        activity.logger.info(f"Posted failure comment to {input.ticket_id}: {success}")
        return {"ok": success}
    except Exception as e:
        activity.logger.warning(f"Failed to post Linear failure comment: {e}")
        return {"ok": False, "error": str(e)}


@activity.defn(name="notify_gate_waiting")
def notify_gate_waiting(input: NotifyGateInput) -> dict:
    """Notify harness server AND post Linear comment when a gate is reached."""
    harness_url = os.getenv("HARNESS_URL", "http://agent-harness.agent-harness.svc.cluster.local:8000")
    linear_api_key = os.getenv("LINEAR_API_KEY", "")
    linear_workspace = os.getenv("LINEAR_WORKSPACE", "")

    # 1. Notify harness server (for UI)
    body = json.dumps({
        "thread_id": input.thread_id,
        "gate":      input.gate,
        "field":     input.field,
        "options":   input.options,
        "state":     input.state,
    }).encode()
    req = urllib.request.Request(
        f"{harness_url}/api/internal/gate-waiting",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            pass
    except Exception as e:
        activity.logger.warning(f"Could not notify harness of gate: {e}")

    # 2. Post Linear comment
    if linear_api_key and input.state:
        ticket_id = input.thread_id.split("-")[2].upper() + "-" + input.thread_id.split("-")[3] if "-" in input.thread_id else ""
        assessment = input.state.get("assessment", {})
        vulns = input.state.get("vulnerabilities", [])
        vuln_line = ""
        if vulns:
            v = vulns[0]
            vuln_line = f"\n- **Package:** `{v.get('packages')}@{v.get('packageVersion')}` → fix: `{v.get('fix_version')}`"

        harness_ui_url = f"{harness_url.replace('svc.cluster.local','localhost').replace(':8000',':8000')}/index.html"

        comment = f"""🤖 **Agent Harness — Gate: {input.gate}**

The agent has completed its analysis and is waiting for your decision.
{vuln_line}

**Assessment:**
- Affected: {'Yes ⚠️' if assessment.get('affected') else 'No ✅'}
- Risk: {assessment.get('risk_level', '—').upper()}
- Reasoning: {assessment.get('reasoning', '—')}
- Agent recommends: `{assessment.get('recommended_disposition', '—')}`

**Options:** {' | '.join(f'`{o}`' for o in input.options)}

👉 [Make decision in Harness UI]({harness_ui_url}) → Runs tab → select your decision"""

        # Find issue ID using ticket_id from state (set by webhook, workspace-agnostic)
        ticket_id = input.state.get("ticket_id", "")
        try:
            query = json.dumps({
                "query": "query FindIssue($id: String!) { issue(id: $id) { id identifier } }",
                "variables": {"id": ticket_id}
            }) if not ticket_id.startswith("har5") else json.dumps({
                # Fallback: search by label kim-test-harness
                "query": "{ issues(filter: {labels: {name: {eq: \"kim-test-harness\"}}}, first: 1) { nodes { id identifier } } }"
            })
            issues_req = urllib.request.Request(
                "https://api.linear.app/graphql",
                data=query.encode(),
                headers={"Authorization": linear_api_key, "Content-Type": "application/json"},
            )
            with urllib.request.urlopen(issues_req, timeout=5) as r:
                issues_data = json.loads(r.read())
            issue_id = None
            # Try direct issue lookup first
            direct = issues_data.get("data", {}).get("issue")
            if direct and direct.get("id"):
                issue_id = direct["id"]
            else:
                # Fallback: label search result
                nodes = issues_data.get("data", {}).get("issues", {}).get("nodes", [])
                if nodes:
                    issue_id = nodes[0]["id"]

            if issue_id:
                # Use GraphQL variables to avoid string escaping issues
                mutation = json.dumps({
                    "query": "mutation CreateComment($issueId: String!, $body: String!) { commentCreate(input: {issueId: $issueId, body: $body}) { success } }",
                    "variables": {"issueId": issue_id, "body": comment}
                })
                comment_req = urllib.request.Request(
                    "https://api.linear.app/graphql",
                    data=mutation.encode(),
                    headers={"Authorization": linear_api_key, "Content-Type": "application/json"},
                )
                with urllib.request.urlopen(comment_req, timeout=5) as r:
                    result = json.loads(r.read())
                activity.logger.info(f"Posted Linear comment: {result}")
        except Exception as e:
            activity.logger.warning(f"Could not post Linear comment: {e}")

    return {"ok": True}


@activity.defn(name="load_workflow_config")
def load_workflow_config(workflow_id: str) -> dict:
    """Load workflow config from file. Durable — Temporal retries if it fails."""
    path = CONFIG_DIR / "workflows" / f"{workflow_id}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Workflow config not found: {path}")
    with open(path) as f:
        return yaml.safe_load(f)


def _load_agent_config(agent_id: str) -> dict:
    """Load agent .md frontmatter config."""
    for ext in (".md", ".yaml"):
        path = CONFIG_DIR / "agents" / f"{agent_id}{ext}"
        if path.exists():
            text = path.read_text()
            if ext == ".md" and text.startswith("---"):
                parts = text.split("---", 2)
                if len(parts) >= 3:
                    return yaml.safe_load(parts[1]) or {}
            else:
                return yaml.safe_load(text) or {}
    raise FileNotFoundError(f"Agent config not found: {agent_id}")


@activity.defn(name="run_agent")
def run_agent(input: RunAgentInput) -> dict:
    """
    Run an LLM agent activity.

    Temporal handles:
    - Retries if this activity fails
    - Heartbeating to detect stuck activities
    - Durable execution — if worker dies mid-activity, Temporal re-dispatches

    We handle:
    - LiteLLM with fallbacks for model escalation
    - Tool execution before LLM reasoning
    - Budget enforcement
    - Langfuse tracing
    - Message trimming
    - Pydantic output validation
    """
    agent_id = input.agent_id
    activity.logger.info(f"Running agent: {agent_id} for thread {input.thread_id}")

    # Heartbeat so Temporal knows we're alive during long operations
    activity.heartbeat(f"Loading agent config: {agent_id}")

    agent_cfg = _load_agent_config(agent_id)

    # Build and execute the LLM agent node from graph_builder
    # This gives us: LiteLLM fallbacks, budget enforcement, Langfuse, trimming, Pydantic
    node_fn = _build_llm_agent_node(agent_cfg)

    activity.heartbeat(f"Starting agent execution: {agent_id}")
    result = node_fn(input.state)
    activity.heartbeat(f"Agent complete: {agent_id}")

    # Auto-set routing signal fields so workflow gates can route correctly
    if agent_id == "research_cve" and "research_exit" not in result:
        if result.get("patches") is not None and result.get("all_patches_found"):
            result["research_exit"] = "ready"
        elif result.get("mitigation_details"):
            result["research_exit"] = "mitigation"
        elif result.get("needs_human_guidance"):
            result["research_exit"] = "needs_human_guidance"
        elif result.get("retry_exhausted"):
            result["research_exit"] = "retry_exhausted"
        else:
            # Default: needs human guidance if nothing clear
            result["research_exit"] = "needs_human_guidance"
            if "needs_human_guidance" not in result:
                result["needs_human_guidance"] = {
                    "last_failure": str(result.get("find_and_patch_dependency_error", "unknown")),
                    "suggestion": "Check agent output and retry"
                }
        activity.logger.info(f"Auto-set research_exit={result['research_exit']}")

    if agent_id == "risk_assessor" and "risk_assessment" not in result:
        result["risk_assessment"] = result.get("agent_response", result)

    return result


@activity.defn(name="run_code_step")
def run_code_step(input: RunCodeStepInput) -> dict:
    """
    Run a deterministic code step activity.

    Temporal retries this automatically on failure.
    All code steps must be idempotent (check before creating).
    """
    step_type = input.step_type
    activity.logger.info(f"Running code step: {step_type} for thread {input.thread_id}")

    module = STEP_REGISTRY.get(step_type)
    if module is None:
        raise ValueError(f"Step type '{step_type}' not found in registry")

    activity.heartbeat(f"Executing: {step_type}")
    result = module.execute(input.state, input.config)
    activity.logger.info(f"Code step '{step_type}' produced: {list(result.keys())}")
    return result

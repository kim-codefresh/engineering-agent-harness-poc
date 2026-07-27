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

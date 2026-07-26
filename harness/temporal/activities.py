"""
Temporal activities — the actual execution units.

Each activity runs in the Temporal worker process.
Temporal handles retries, timeouts, and heartbeating.
"""
import json
import os
import sys
import time
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


@activity.defn
async def load_workflow_config(workflow_id: str) -> dict:
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


@activity.defn
async def run_agent(input: RunAgentInput) -> dict:
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


@activity.defn
async def run_code_step(input: RunCodeStepInput) -> dict:
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

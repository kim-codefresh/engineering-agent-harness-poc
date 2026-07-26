"""
Harness graph builder — translates workflow YAML into a compiled LangGraph graph.

Step kinds (must be declared explicitly in the workflow):
  agent:          non-deterministic — uses an LLM or named agent composition
  deterministic:  pure code — no LLM, predictable, safe to repeat
  gate:           human decision point — pauses until a person decides

This is the only file in the harness that imports LangGraph directly.
"""
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from step_types import REGISTRY as STEP_REGISTRY


# ── Executors ─────────────────────────────────────────────────────────────────

def _run_step_type(step_cfg: dict) -> callable:
    """Execute a single step type (shared by both agent and deterministic nodes)."""
    step_type = step_cfg["type"]

    def node(state: dict) -> dict:
        module = STEP_REGISTRY[step_type]
        return module.execute(state, step_cfg)

    node.__name__ = step_cfg.get("id", step_type)
    return node


def _run_agent_composition(agent_cfg: dict) -> callable:
    """Execute a named agent — a sequence of step types from an .md definition."""
    steps = agent_cfg["steps"]

    def node(state: dict) -> dict:
        acc = {}
        for s in steps:
            result = STEP_REGISTRY[s["type"]].execute({**state, **acc}, s)
            acc.update(result)
        return acc

    node.__name__ = agent_cfg["id"]
    return node


def _build_agent_node(step: dict, agent_registry: dict) -> callable:
    """
    Build a node for a step declared as `agent:`.
    Supports three forms:
      agent:
        uses: named_agent     # references an .md agent definition
      agent:
        type: call_llm        # inline LLM step
        prompt: "..."
    Named agent with no steps → runs as a single call_llm using the agent's
    model and tools declared in its .md frontmatter.
    """
    cfg = step["agent"]
    if "uses" in cfg:
        named = agent_registry.get(cfg["uses"])
        if not named:
            raise KeyError(f"Agent '{cfg['uses']}' not found in registry")
        # If the agent has explicit step types, compose them
        if named.get("steps"):
            return _run_agent_composition(named)
        # Otherwise it's an LLM agent — run it via call_llm with its declared model
        agent_id = named.get("id", cfg["uses"])
        model    = named.get("model", None)
        tools    = named.get("tools", [])

        def llm_agent_node(state: dict) -> dict:
            import os
            import litellm
            import json
            run_model = model or os.getenv("LITELLM_MODEL", "anthropic/claude-haiku-4-5-20251001")
            system = (
                f"You are the {agent_id} agent. "
                f"Use the tools available to complete your task and return a structured JSON result. "
                f"Available tools: {', '.join(tools) if tools else 'none declared'}. "
                f"State keys available: {list(state.keys())}."
            )
            user = f"Current state:\n{json.dumps({k: v for k, v in state.items() if not isinstance(v, (bytes,))}, indent=2, default=str)}"
            print(f"[graph_builder] Running LLM agent: {agent_id} with model {run_model}")
            resp = litellm.completion(
                model=run_model,
                max_tokens=named.get("budget_tokens", 4096),
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
            content = resp.choices[0].message.content
            try:
                result = json.loads(content)
                if not isinstance(result, dict):
                    result = {"agent_response": result}
            except Exception:
                result = {"agent_response": content}
            return result

        llm_agent_node.__name__ = agent_id
        return llm_agent_node

    return _run_step_type({**cfg, "id": step["id"]})


def _build_deterministic_node(step: dict) -> callable:
    """Build a node for a step declared as `deterministic:`."""
    return _run_step_type({**step["deterministic"], "id": step["id"]})


def _build_gate_node(gate_cfg: dict, step_id: str) -> callable:
    field = gate_cfg["output_field"]

    def gate_node(state: dict) -> dict:
        return {field: state[field]}

    gate_node.__name__ = step_id
    return gate_node


# ── Graph builder ─────────────────────────────────────────────────────────────

def build_graph(workflow_config: dict, agent_registry: dict) -> tuple:
    graph    = StateGraph(dict)
    gate_ids = []

    for step in workflow_config["steps"]:
        step_id = step["id"]
        if "agent" in step:
            graph.add_node(step_id, _build_agent_node(step, agent_registry))
        elif "deterministic" in step:
            graph.add_node(step_id, _build_deterministic_node(step))
        elif "gate" in step:
            gate_ids.append(step_id)
            graph.add_node(step_id, _build_gate_node(step["gate"], step_id))

    graph.set_entry_point(workflow_config["steps"][0]["id"])

    for step in workflow_config["steps"]:
        step_id = step["id"]
        if "routes" in step:
            field = step["gate"]["output_field"]
            graph.add_conditional_edges(
                step_id,
                lambda state, f=field: state.get(f),
                step["routes"],
            )
        elif "next" in step:
            graph.add_edge(step_id, step["next"])
        elif step_id == workflow_config.get("terminal_step"):
            graph.add_edge(step_id, END)

    return graph, gate_ids


def compile_workflow(workflow_config: dict, agent_registry: dict,
                     checkpointer=None) -> tuple:
    graph, gate_ids = build_graph(workflow_config, agent_registry)
    gate_meta = {
        s["id"]: {
            "options":      s["gate"]["options"],
            "output_field": s["gate"]["output_field"],
        }
        for s in workflow_config["steps"] if "gate" in s
    }
    compiled = graph.compile(
        checkpointer=checkpointer or MemorySaver(),
        interrupt_before=gate_ids,
    )
    return compiled, gate_ids, gate_meta

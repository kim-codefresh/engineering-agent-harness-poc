"""
Harness graph builder — translates workflow + agent configs into a compiled
LangGraph graph. This is the only file in the harness that imports LangGraph.

Step model:
  - type: <step_type>   direct step type (deterministic or LLM) — primary model
  - agent: <agent_id>   reusable named composition of step types (optional)
  - gate:               human decision point
"""
from langgraph.graph import END, StateGraph
from langgraph.checkpoint.memory import MemorySaver
from step_types import REGISTRY as STEP_REGISTRY


# ── Direct step type node ─────────────────────────────────────────────────────

def build_step_node(step_config: dict) -> callable:
    """A single step executed directly — deterministic code or LLM call."""
    step_type = step_config["type"]

    def step_node(state: dict) -> dict:
        module = STEP_REGISTRY[step_type]
        return module.execute(state, step_config)

    step_node.__name__ = step_config.get("id", step_type)
    return step_node


# ── Agent node (reusable composition) ────────────────────────────────────────

def build_agent_node(agent_config: dict) -> callable:
    """A named group of step types — only used when reuse across workflows matters."""
    steps = agent_config["steps"]

    def agent_node(state: dict) -> dict:
        accumulated = {}
        for step_cfg in steps:
            module = STEP_REGISTRY[step_cfg["type"]]
            result = module.execute({**state, **accumulated}, step_cfg)
            accumulated.update(result)
        return accumulated

    agent_node.__name__ = agent_config["id"]
    return agent_node


# ── Gate node ─────────────────────────────────────────────────────────────────

def build_gate_node(gate_config: dict, step_id: str) -> callable:
    output_field = gate_config["output_field"]

    def gate_node(state: dict) -> dict:
        return {output_field: state[output_field]}

    gate_node.__name__ = step_id
    return gate_node


# ── Graph builder ─────────────────────────────────────────────────────────────

def build_graph(workflow_config: dict, agent_registry: dict) -> tuple:
    graph    = StateGraph(dict)
    gate_ids = []

    for step in workflow_config["steps"]:
        step_id = step["id"]
        if "type" in step:
            # Primary model: step type declared directly on the workflow step
            graph.add_node(step_id, build_step_node(step))
        elif "agent" in step:
            # Reuse model: reference a named agent composition
            agent_cfg = agent_registry[step["agent"]]
            graph.add_node(step_id, build_agent_node(agent_cfg))
        elif "gate" in step:
            gate_ids.append(step_id)
            graph.add_node(step_id, build_gate_node(step["gate"], step_id))

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
        for s in workflow_config["steps"]
        if "gate" in s
    }

    compiled = graph.compile(
        checkpointer=checkpointer or MemorySaver(),
        interrupt_before=gate_ids,
    )
    return compiled, gate_ids, gate_meta

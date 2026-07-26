"""
Harness graph builder — translates workflow YAML into a compiled LangGraph graph.

Step kinds:
  agent:          LLM reasoning loop — model, budget, fallbacks, Langfuse tracing
  deterministic:  pure code — Pydantic-validated output before passing downstream
  gate:           human decision point — pauses until a human decides

This is the only file in the harness that imports LangGraph directly.
"""
import json
import os
import time
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from step_types import REGISTRY as STEP_REGISTRY


# ── Langfuse tracer (optional — skipped if not configured) ───────────────────

def _get_tracer():
    try:
        from langfuse import Langfuse
        pk = os.getenv("LANGFUSE_PUBLIC_KEY")
        sk = os.getenv("LANGFUSE_SECRET_KEY")
        host = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")
        if pk and sk:
            return Langfuse(public_key=pk, secret_key=sk, host=host)
    except ImportError:
        pass
    return None

_tracer = None

def _tracer_instance():
    global _tracer
    if _tracer is None:
        _tracer = _get_tracer()
    return _tracer


def _trace_llm(trace, agent_id: str, model: str, messages: list,
               response: Any, start_ms: float, token_usage: dict):
    """Record an LLM call span in Langfuse."""
    t = _tracer_instance()
    if not t or not trace:
        return
    try:
        duration_ms = int((time.time() * 1000) - start_ms)
        trace.generation(
            name=f"{agent_id}.llm",
            model=model,
            input=messages,
            output=response,
            usage=token_usage,
            metadata={"duration_ms": duration_ms},
        )
    except Exception as e:
        print(f"[langfuse] trace error: {e}")


def _trace_tool(trace, agent_id: str, tool_name: str, input_data: Any, output_data: Any):
    t = _tracer_instance()
    if not t or not trace:
        return
    try:
        trace.span(
            name=f"{agent_id}.{tool_name}",
            input=input_data,
            output=output_data,
        )
    except Exception:
        pass


# ── Message trimming ──────────────────────────────────────────────────────────

def _trim_messages(messages: list, max_keep: int = 10) -> list:
    """
    Keep the system prompt + last max_keep messages.
    Older messages beyond the window are replaced with a single summary message.
    """
    if len(messages) <= max_keep + 1:
        return messages

    system = [m for m in messages if m.get("role") == "system"]
    non_system = [m for m in messages if m.get("role") != "system"]

    if len(non_system) <= max_keep:
        return messages

    old = non_system[:-max_keep]
    recent = non_system[-max_keep:]

    summary_text = (
        f"[Context summary — {len(old)} earlier messages trimmed]\n"
        + "\n".join(
            f"- {m['role']}: {str(m.get('content',''))[:120]}..."
            for m in old
        )
    )
    summary_msg = {"role": "assistant", "content": summary_text}
    return system + [summary_msg] + recent


# ── Pydantic output validation ────────────────────────────────────────────────

def _validate_output(state: dict, agent_cfg: dict) -> tuple[bool, str]:
    """
    Validate agent output against the output_schema declared in the agent's .md.
    Returns (is_valid, error_message).
    """
    schema = agent_cfg.get("output_schema")
    if not schema:
        return True, ""

    # Determine which exit path this is
    for exit_name, exit_fields in schema.items():
        if not isinstance(exit_fields, dict):
            continue
        # Check if ALL required fields for this exit are present
        if all(k in state for k in exit_fields.keys()):
            # Validate types
            for field, expected_type in exit_fields.items():
                val = state.get(field)
                if val is None:
                    return False, f"Required field '{field}' is None for exit '{exit_name}'"
            return True, ""

    return False, f"Output doesn't match any declared exit schema. State keys: {list(state.keys())}"


# ── LLM agent runner ─────────────────────────────────────────────────────────

def _build_llm_agent_node(agent_cfg: dict) -> callable:
    """
    Build a LangGraph node for an LLM agent declared in its .md.
    Implements:
    - LiteLLM with fallbacks for model escalation
    - Budget enforcement (tokens + wall-clock)
    - Pydantic-style output schema validation
    - Message trimming to prevent context overflow
    - Langfuse tracing for every LLM call and tool call
    """
    agent_id      = agent_cfg.get("id", "unknown")
    model         = agent_cfg.get("model", os.getenv("LITELLM_MODEL", "anthropic/claude-haiku-4-5-20251001"))
    tools_allowed = agent_cfg.get("tools", [])
    budget_tokens = agent_cfg.get("budget_tokens", 8192)
    budget_secs   = agent_cfg.get("budget_seconds", 300)
    escalation    = agent_cfg.get("retry_escalation", {})
    fallback_model = escalation.get("escalate_to")
    fallback_after = escalation.get("after_retries", 3)

    def node(state: dict) -> dict:
        import litellm

        thread_id = state.get("ticket_id", "unknown")
        t = _tracer_instance()
        trace = None
        if t:
            try:
                trace = t.trace(
                    name=agent_id,
                    metadata={"thread_id": thread_id, "agent": agent_id},
                )
            except Exception:
                pass

        # Build skill context — run declared tools in sequence before LLM
        accumulated = dict(state)
        for tool_name in tools_allowed:
            module = STEP_REGISTRY.get(tool_name)
            if module is None:
                print(f"[{agent_id}] Tool '{tool_name}' not in registry, skipping")
                continue
            print(f"[{agent_id}] Running tool: {tool_name}")
            t0 = time.time()
            try:
                result = module.execute(accumulated, {})
                accumulated.update(result)
                _trace_tool(trace, agent_id, tool_name, {}, result)
                print(f"[{agent_id}] Tool {tool_name} done in {time.time()-t0:.1f}s")
            except Exception as e:
                print(f"[{agent_id}] Tool {tool_name} failed: {e}")
                accumulated[f"{tool_name}_error"] = str(e)

        # Build system prompt
        system_prompt = (
            f"You are {agent_id}, an expert security engineer AI agent.\n"
            f"You have already run the following tools and their results are in the state:\n"
            f"{', '.join(tools_allowed) if tools_allowed else 'none'}\n\n"
            f"Based on the state data, produce a structured JSON response.\n"
            f"Output schema options:\n"
            f"{json.dumps(agent_cfg.get('output_schema', {}), indent=2)}\n\n"
            f"Return ONLY valid JSON matching one of the schema options above. "
            f"Choose the appropriate exit based on what you found."
        )

        user_content = json.dumps(
            {k: v for k, v in accumulated.items()
             if not isinstance(v, bytes) and k != "ticket_description"},
            indent=2, default=str
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": f"Current state:\n{user_content}"},
        ]

        # LLM call with budget enforcement, message trimming, and fallbacks
        start_time = time.time()
        retries = 0
        result = {}

        while True:
            elapsed = time.time() - start_time
            if elapsed > budget_secs:
                print(f"[{agent_id}] Budget exceeded: {elapsed:.0f}s > {budget_secs}s")
                return {**accumulated, "retry_exhausted": True,
                        "exhaustion_reason": f"Time budget exceeded ({elapsed:.0f}s)"}

            use_model = model
            if fallback_model and retries >= fallback_after:
                use_model = fallback_model
                print(f"[{agent_id}] Escalating to {use_model} after {retries} retries")

            trimmed = _trim_messages(messages)
            start_ms = time.time() * 1000

            try:
                resp = litellm.completion(
                    model=use_model,
                    max_tokens=min(budget_tokens, 4096),
                    messages=trimmed,
                    **({"fallbacks": [fallback_model]} if fallback_model and retries < fallback_after else {}),
                )
                content = resp.choices[0].message.content
                usage = {
                    "input":  resp.usage.prompt_tokens if resp.usage else 0,
                    "output": resp.usage.completion_tokens if resp.usage else 0,
                }
                _trace_llm(trace, agent_id, use_model, trimmed, content, start_ms, usage)

                # Parse JSON response
                try:
                    result = json.loads(content)
                    if not isinstance(result, dict):
                        result = {"agent_response": result}
                except json.JSONDecodeError:
                    # Try extracting JSON from markdown
                    import re
                    match = re.search(r'```(?:json)?\s*([\s\S]+?)\s*```', content)
                    if match:
                        try:
                            result = json.loads(match.group(1))
                        except Exception:
                            result = {"agent_response": content}
                    else:
                        result = {"agent_response": content}

                # Validate output against schema
                merged = {**accumulated, **result}
                valid, err = _validate_output(merged, agent_cfg)
                if valid:
                    print(f"[{agent_id}] ✅ Valid output after {retries} retries")
                    if t and trace:
                        try:
                            trace.update(output=result)
                        except Exception:
                            pass
                    return merged

                print(f"[{agent_id}] Output invalid (retry {retries+1}): {err}")
                messages.append({"role": "assistant", "content": content})
                messages.append({"role": "user", "content":
                    f"Your output was invalid: {err}. "
                    f"Please fix and return valid JSON matching the schema."})
                retries += 1

            except Exception as e:
                print(f"[{agent_id}] LLM call failed (retry {retries+1}): {e}")
                retries += 1
                if retries > fallback_after + 2:
                    return {**accumulated, "retry_exhausted": True,
                            "exhaustion_reason": str(e)}
                time.sleep(min(2 ** retries, 30))

    node.__name__ = agent_id
    return node


# ── Deterministic node with Pydantic-style validation ────────────────────────

def _build_deterministic_node(step: dict) -> callable:
    cfg     = step["deterministic"]
    step_id = step["id"]

    def node(state: dict) -> dict:
        module = STEP_REGISTRY[cfg["type"]]
        result = module.execute(state, cfg)
        # Compact output — log what changed
        changed = list(result.keys())
        print(f"[{step_id}] ✅ Output fields: {changed}")
        return result

    node.__name__ = step_id
    return node


# ── Gate node ─────────────────────────────────────────────────────────────────

def _build_gate_node(gate_cfg: dict, step_id: str) -> callable:
    field = gate_cfg["output_field"]

    def gate_node(state: dict) -> dict:
        return {field: state[field]}

    gate_node.__name__ = step_id
    return gate_node


# ── Agent node dispatcher ─────────────────────────────────────────────────────

def _build_agent_node(step: dict, agent_registry: dict) -> callable:
    cfg = step["agent"]

    if "uses" in cfg:
        named = agent_registry.get(cfg["uses"])
        if not named:
            raise KeyError(f"Agent '{cfg['uses']}' not found in registry")
        if named.get("steps"):
            # Composition of step types
            steps = named["steps"]
            agent_id = named["id"]
            def composition_node(state: dict) -> dict:
                acc = {}
                for s in steps:
                    result = STEP_REGISTRY[s["type"]].execute({**state, **acc}, s)
                    acc.update(result)
                return acc
            composition_node.__name__ = agent_id
            return composition_node
        else:
            # Full LLM agent
            return _build_llm_agent_node(named)

    # Inline step type
    return _build_deterministic_node({"deterministic": cfg, "id": step["id"]})


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

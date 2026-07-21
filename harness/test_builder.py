"""
Sanity check: load configs and prove the graph builder compiles without errors.
Does NOT invoke the graph (no LLM call, no cost).
"""
import yaml
from pathlib import Path
from graph_builder import compile_workflow

BASE = Path(__file__).parent / "config"

def load(path): return yaml.safe_load(path.read_text())

# Load agent registry
agent_registry = {
    p.stem: load(p)
    for p in (BASE / "agents").glob("*.yaml")
}

# Load workflow
workflow = load(BASE / "workflows" / "cve_remediation.yaml")

# Add stub agents for any not yet implemented
for name in ["pr_opener", "outcome_recorder"]:
    agent_registry.setdefault(name, {
        "id": name,
        "steps": [{"type": "record_outcome", "output_field": "outcome",
                   "summary_template": f"{name} ran"}]
    })

compiled, gate_ids, gate_meta = compile_workflow(workflow, agent_registry)

print("✅ Graph compiled successfully")
print(f"   Nodes:  {list(compiled.nodes.keys())}")
print(f"   Gates:  {gate_ids}")
print(f"   Meta:   {list(gate_meta.keys())}")

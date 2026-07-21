"""
Registry — loads YAML configs from disk, compiles LangGraph graphs.
Call reload() to hot-reload after new configs are written.
"""
import yaml
from pathlib import Path
from langgraph.checkpoint.memory import MemorySaver
from graph_builder import compile_workflow

CONFIG_DIR = Path(__file__).parent / "config"


def _load_yaml_dir(subdir: str) -> dict:
    return {
        p.stem: yaml.safe_load(p.read_text())
        for p in (CONFIG_DIR / subdir).glob("*.yaml")
    }


class Registry:
    def __init__(self):
        self.checkpointer = MemorySaver()
        self.reload()

    def reload(self):
        self.agents    = _load_yaml_dir("agents")
        self.workflows = _load_yaml_dir("workflows")
        self._graphs   = {}
        self._gate_meta = {}

        for wf_id, wf_cfg in self.workflows.items():
            try:
                compiled, gate_ids, gate_meta = compile_workflow(
                    wf_cfg, self.agents, self.checkpointer
                )
                self._graphs[wf_id]    = compiled
                self._gate_meta[wf_id] = gate_meta
            except Exception as e:
                print(f"[registry] Failed to compile workflow '{wf_id}': {e}")

    def graph(self, workflow_id: str):
        g = self._graphs.get(workflow_id)
        if not g:
            raise KeyError(f"Workflow '{workflow_id}' not found or failed to compile")
        return g

    def gate_meta(self, workflow_id: str) -> dict:
        return self._gate_meta.get(workflow_id, {})

    def save_agent(self, agent_config: dict):
        path = CONFIG_DIR / "agents" / f"{agent_config['id']}.yaml"
        path.write_text(yaml.dump(agent_config, default_flow_style=False))
        self.reload()

    def save_workflow(self, workflow_config: dict):
        path = CONFIG_DIR / "workflows" / f"{workflow_config['id']}.yaml"
        path.write_text(yaml.dump(workflow_config, default_flow_style=False))
        self.reload()

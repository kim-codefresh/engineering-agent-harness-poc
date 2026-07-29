"""
Registry — loads configs from disk, compiles LangGraph graphs.

Agents are .md files with YAML frontmatter — machine-readable config +
human documentation in one file. Workflows are .yaml files.
"""
import yaml
from pathlib import Path
from langgraph.checkpoint.memory import MemorySaver
from graph_builder import compile_workflow

CONFIG_DIR = Path(__file__).parent / "config"


def _parse_md_frontmatter(text: str) -> dict:
    """Extract YAML frontmatter from a markdown file (between --- delimiters)."""
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) >= 3:
        return yaml.safe_load(parts[1]) or {}
    return {}


def _load_agents(path: Path) -> dict:
    """Load agents from .md files (preferred) or .yaml files (fallback)."""
    agents = {}
    # .md files are the primary format
    for p in path.glob("*.md"):
        cfg = _parse_md_frontmatter(p.read_text())
        if cfg and "id" in cfg:
            agents[cfg["id"]] = cfg
    # .yaml files still supported for backward compat
    for p in path.glob("*.yaml"):
        if p.stem not in agents:  # .md takes priority
            cfg = yaml.safe_load(p.read_text())
            if cfg and "id" in cfg:
                agents[cfg["id"]] = cfg
    return agents


def _load_yaml_dir(subdir: str) -> dict:
    return {
        p.stem: yaml.safe_load(p.read_text())
        for p in (CONFIG_DIR / subdir).glob("*.yaml")
    }


def _agent_to_md(agent_config: dict) -> str:
    """Serialize an agent config to a .md file with YAML frontmatter."""
    frontmatter = yaml.dump(agent_config, default_flow_style=False, allow_unicode=True)
    steps_desc = "\n".join(
        f"- `{s.get('type', '?')}` → `{s.get('output_field', '?')}`"
        for s in agent_config.get("steps", [])
    )
    return (
        f"---\n{frontmatter}---\n\n"
        f"# {agent_config['id']}\n\n"
        f"## Steps\n{steps_desc}\n"
    )


class Registry:
    def __init__(self):
        self.checkpointer = MemorySaver()
        self.reload()

    def reload(self):
        self.agents    = _load_agents(CONFIG_DIR / "agents")
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
        path = CONFIG_DIR / "agents" / f"{agent_config['id']}.md"
        path.write_text(_agent_to_md(agent_config))
        self.reload()

    def save_workflow(self, workflow_config: dict):
        path = CONFIG_DIR / "workflows" / f"{workflow_config['id']}.yaml"
        path.write_text(yaml.dump(workflow_config, default_flow_style=False, allow_unicode=True))
        self.reload()

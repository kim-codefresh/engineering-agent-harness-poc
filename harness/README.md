# Engineering Agent Harness

A CVE remediation flow built to explore four problems: **orchestration**, **agent running**, **context/memory sharing**, and **sandboxing**.

---

## Target architecture

The harness is designed so that **adding a new workflow requires no Python** — only YAML and agent `.md` files. The execution engine (Temporal) and the generic workflow runner are written once by the harness team and never touched again.

```
┌──────────────────────────────────────────────────────────────┐
│  HARNESS TEAM — written once, stable                         │
│                                                              │
│  Temporal Worker                                             │
│    └─ HarnessWorkflow.py   generic YAML interpreter          │
│    └─ activities/          run_agent · run_code_step · gate  │
│                                                              │
│  step_types/               skill library                     │
│    call_llm · open_pr · wait_for_checks · trigger_e2e · ...  │
└──────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────┐
│  WORKFLOW DEVELOPER — YAML + .md only, no Python             │
│                                                              │
│  workflows/handle_incident/                                  │
│    config.yaml             step sequence, gates, routing     │
│    agents/                                                   │
│      triage.md             prompt · tools · model · budget   │
│      fix.md                                                  │
└──────────────────────────────────────────────────────────────┘
```

**The only time a developer touches Python** is when they need a brand new step type — a capability that doesn't exist yet (e.g. a PagerDuty integration). Once written, that step type is available to all future workflows forever.

---

## The 2-agent CVE flow

```
[TRIGGER]
CVE arrives in Linear
        │
        ▼
┌─────────────────────────────────────────────────────┐
│  AGENT 1 ○  research_cve                            │
│  Investigates CVE, reads code, prepares fix,        │
│  opens draft PR. Loops internally until done.       │
│                                                     │
│  Tools: GitHub · Codefresh Classic · Linear API     │
│         curl · Cypress                              │
│                                                     │
│  Exits:                                             │
│    ready           → PR opened, fix prepared        │
│    mitigation      → workaround only, no full fix   │
│    retry exhausted → budget or retries used up      │
└───────────┬──────────────┬──────────────────────────┘
            │              │                │
         ready          mitigation    retry exhausted
            │              │                │
            ▼              ▼                ▼
    ┌──────────────┐  ┌──────────────┐  ┌─────────────────┐
    │  HUMAN ⬡     │  │  CODE △      │  │  CODE △         │
    │ review draft │  │ comment on   │  │ mark_linear     │
    │ PR           │  │ linear       │  │ _label          │
    └──────┬───────┘  └──────┬───────┘  └────────┬────────┘
    reject │                 ▼                    │
     loops │          ┌──────────────┐      ┌─────┴──────────┐
    agent 1│          │  HUMAN ⬡     │      │  HUMAN ⬡       │
           │          │ in the loop  │      │  escalation    │
           │          └──────┬───────┘      └──────┬─────────┘
           │          reject │ loops         retry │  close
           │          agent 1│               agent │     │
           │                 │                   [a1]  done ⬡
           ▼                 ▼
┌─────────────────────────────────────────────────────┐
│  AGENT 2 ○  pr_validator                            │
│  Waits for CI checks, triggers Cypress e2e,         │
│  reports results.                                   │
│                                                     │
│  Tools: GitHub API · Cypress                        │
│                                                     │
│  Exits:                                             │
│    passed  → all checks green, e2e green            │
│    failed  → loops back to agent 1 to fix           │
└───────────┬─────────────────────────────────────────┘
            │ passed
            ▼
    ┌────────────────────────┐
    │  CODE △                │
    │  mark_ready_to_review  │
    │  (Linear status)       │
    └──────────┬─────────────┘
               ▼
    ┌────────────────────────┐
    │  HUMAN ⬡               │
    │  final PR review       │
    └──────┬──────┬──────────┘
    approve│      │reject → loops agent 1
           ▼
    ┌──────────────┐
    │  CODE △      │
    │  merge       │
    │  (GitHub API)│
    └──────┬───────┘
           ▼
        done ⬡
     (human marks closed in Linear)
```

**Symbols:** ○ Agent (LLM loop) · △ Code (deterministic) · ⬡ Human (person acts) · ◇ Gate (automated wait)

---

## How to add a new workflow

No Python required. Create a directory under `workflows/`:

```
workflows/handle_incident/
  config.yaml           ← step sequence, gates, routing (see cve_remediation for reference)
  agents/
    triage.md           ← agent: prompt, tools, model, budget
    fix.md
```

**Config YAML shape:**

```yaml
id: handle_incident
trigger:
  source: linear
  event: incident.created

steps:
  - id: triage
    agent:
      uses: triage        # references agents/triage.md
    next: review_gate

  - id: review_gate
    gate:
      output_field: decision
      options: [resolve, escalate]
    routes:
      resolve: fix
      escalate: record

  - id: fix
    agent:
      uses: fix
    next: record

  - id: record
    deterministic:
      type: update_linear_status
      output_field: outcome
```

**If you need a step type that doesn't exist yet** (e.g. PagerDuty notification), add one Python file:

```
step_types/notify_pagerduty.py   ← implement execute(state, config) → dict
```

Register it in `step_types/__init__.py`. It's then available to all workflows by name.

---

## The 4 pillars

### 1. Orchestration — Temporal

Temporal is the execution engine. It stores all workflow state durably, handles retries, enforces timeouts, and provides built-in signals for human gates. The harness runs a generic `HarnessWorkflow` class that reads the workflow `config.yaml` and executes each step — agent, code, or gate. Temporal replaces `graph_builder.py`, `MemorySaver`, and our custom gate server with battle-tested primitives.

Key properties:
- Workflow state survives pod crashes and harness redeploys
- Active runs are not broken by a new harness version (Temporal workflow versioning)
- Human gates are Temporal signals — no custom HTTP endpoint needed
- Full audit trail of every step, input, and output in Temporal's UI

### 2. Agent Runner — custom + OpenHands for execution

The runner gives each agent exactly the tools declared in its `.md`, enforces token and time budgets, validates typed output before passing downstream, and escalates the model (e.g. Sonnet → Fable) after N retries. For the steps that actually execute code in a repo (fix preparation, test running), OpenHands provides a battle-tested sandboxed execution environment rather than us rebuilding it.

### 3. Context / Memory — typed state + prompt caching + LangGraph Store

Within a run: LangGraph's typed state dict is the contract between agents. Agent 2 receives only the fields agent 1 declared as output — never the full conversation history. Anthropic prompt caching reduces token cost on repeated system prompts and advisory text. Across runs: LangGraph Store (Postgres-backed) lets agents recall past CVE patterns once enough runs exist to learn from.

### 4. Sandboxing — k8s Jobs + Vault + gVisor

Each agent invocation runs as its own ephemeral k8s Job with its own service account — separate credentials per agent, not shared pod env vars. HashiCorp Vault issues dynamic short-lived tokens scoped to exactly what each agent needs (`research_cve` gets PR-write, `pr_validator` gets checks-read only). NetworkPolicy restricts egress to approved endpoints. gVisor adds kernel-level isolation for the code execution steps.

---

## Repo map

```
harness/
  HarnessWorkflow.py     Temporal workflow — reads config.yaml, executes steps
  activities/            Temporal activities: run_agent · run_code_step · gate
  step_types/            skill library — one file per capability
    call_llm.py            LiteLLM (model-agnostic, supports escalation)
    gather_evidence.py
    open_pr.py             GitHub API (kim-codefresh org only)
    merge_pr.py            GitHub API squash merge
    wait_for_checks.py     polls GitHub PR checks
    trigger_e2e.py         adds comment/label to trigger Cypress
    github_api.py          shared helper + safety gate
  config/
    workflows/             workflow definitions — YAML only
      cve_remediation.yaml
    agents/                agent definitions — .md with YAML frontmatter
      research_cve.md
      pr_validator.md      (to be built)
  ui/
    index.html             pipeline view · gate decisions · YAML editor · propose-as-PR
  README.md                this file
```

---

## Running it

```bash
# port-forward the harness
kubectl -n agent-harness port-forward svc/agent-harness 8000:8000

# open the UI
open http://localhost:8000

# or trigger via API
curl -X POST localhost:8000/api/run/cve_remediation/my-run-id \
  -H "Content-Type: application/json" \
  -d @sample_cve.json
```

See the root README for cluster setup.

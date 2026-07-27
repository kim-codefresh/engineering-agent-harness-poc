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
│    └─ HarnessWorkflow   generic YAML interpreter             │
│    └─ activities/       run_agent · run_code_step · gate     │
│                                                              │
│  step_types/ + skills/  capability library                   │
└──────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────┐
│  WORKFLOW DEVELOPER — YAML + .md only, no Python             │
│                                                              │
│  config/workflows/cve_remediation.yaml  step sequence        │
│  config/agents/research_cve.md          agent definition     │
└──────────────────────────────────────────────────────────────┘
```

---

## The CVE flow — 2 agents

```
[TRIGGER]
CVE arrives in Linear (label: kim-test-harness)
        │
        ▼
┌─────────────────────────────────────────────────────┐
│  AGENT 1 ○  research_cve  — LOCAL ONLY              │
│  Researches CVE, prepares fix locally.              │
│  Zero GitHub writes during research.                │
│                                                     │
│  Tools: gather_evidence · fetch_advisory            │
│         find_and_patch_dependency (local clone)     │
│         recall_past_cve                             │
│                                                     │
│  Exits:                                             │
│    ready              → local patch prepared        │
│    mitigation         → workaround only             │
│    needs_human_guide  → struggling after N retries  │
│    retry_exhausted    → budget used up              │
└──┬───────────┬──────────────┬───────────────────────┘
   │           │              │
 ready     mitigation   needs_human_guide / retry_exhausted
   │           │              │
   ▼           ▼              ▼
[CODE △]   [CODE △]     [HUMAN ⬡]
run_local  comment_on   mid-retry check
_checks    linear       (continue | escalate)
   │           │              │ continue     │ escalate
   │ passed    │              └──→ agent1    │
   ▼           ▼                            ▼
[CODE △]   [HUMAN ⬡]                  [CODE △]
open_prs   human in                   mark_linear
(1+ PRs)   the loop                   _label
   │       approve│reject→a1               │
   ▼           ▼                      [HUMAN ⬡]
[CODE △]   done ⬡                     escalation
wait_for                               gate
_checks                            retry│close
   │ passed                         agent│  │
   ▼                                  [a1] done ⬡
[CODE △]
trigger_e2e
   │ passed
   ▼
┌─────────────────────────────────────────────────────┐
│  AGENT 2 ○  risk_assessor  — INDEPENDENT            │
│  Risk check on the fix. Separate from Agent 1        │
│  to avoid bias. Not a code reviewer — a risk         │
│  assessor.                                           │
│                                                      │
│  Checks: Is new version safe? New CVEs introduced?  │
│  Breaking changes? Scan actually clean?             │
└───────────────────────┬─────────────────────────────┘
                        │
                        ▼
                [HUMAN ⬡]
                review PR + risk
                (PR links · CI · scan · risk assessment
                 all in Linear comment)
                approve│reject→loops agent1
                        │ approve
                        ▼
                [CODE △] merge
                        │
                        ▼
                      done ⬡
```

**Symbols:** ○ Agent (LLM loop) · △ Code (deterministic) · ⬡ Human (person acts)

---

## What each step does

| Step | Kind | What |
|---|---|---|
| `research_cve` | Agent 1 ○ | Research CVE locally — no GitHub writes |
| `run_local_checks` | Code △ | Lint · tests · build · local scan on patch |
| `open_prs` | Code △ | Create branches, commit, open PRs (1 per affected repo) |
| `wait_for_checks` | Code △ | Poll CI + Prisma Cloud scan on all PRs |
| `trigger_e2e` | Code △ | Comment `/e2e` on PRs, wait for Cypress results |
| `risk_assessor` | Agent 2 ○ | Independent risk check — no bias from Agent 1 |
| `review PR + risk` | Human ⬡ | Full context: PR links · CI · scan · risk |
| `merge` | Code △ | Squash merge all PRs · update Linear |
| `mid-retry check` | Human ⬡ | After N failures: continue \| escalate |
| `comment_on_linear` | Code △ | Post mitigation details to Linear ticket |
| `human in the loop` | Human ⬡ | Review mitigation: done \| retry agent |
| `mark_linear_label` | Code △ | Add escalation label when budget exhausted |
| `human escalation` | Human ⬡ | Final gate: retry \| close |

---

## How to add a new workflow

No Python required. Create a YAML + agent `.md` files:

```
config/workflows/my_workflow.yaml    ← step sequence + routing
config/agents/my_agent.md           ← agent: prompt, tools, model, budget
```

The only time Python is needed: adding a brand new step type. Once added to `step_types/` it's available to all workflows forever.

---

## The 4 pillars

### 1. Orchestration — Temporal
Durable execution, workflow versioning (safe redeploys during active runs), built-in signals for human gates. `HarnessWorkflow` reads any `config.yaml` — workflow authors never touch Python.

### 2. Agent Runner — custom + LiteLLM
Custom runner owns: per-agent tool allowlist, Pydantic output validation, budget enforcement (tokens + time), model escalation (Sonnet → Opus after N retries via LiteLLM fallbacks). Langfuse traces every LLM call.

### 3. Context/Memory — typed state + Postgres
Typed state dict is the contract between agents. Agent 2 only receives Agent 1's declared output fields — never the full conversation. Postgres stores cross-run memory (past CVE fixes per package).

### 4. Sandboxing — k8s Jobs + Vault + gVisor
Each agent invocation runs as its own ephemeral k8s Job with its own service account and Vault-issued scoped credentials. NetworkPolicy restricts egress. gVisor for code execution steps (on real nodes).

---

## Running it

```bash
# Port-forwards (keep all three open)
kubectl -n agent-harness port-forward svc/agent-harness 8000:8000
kubectl -n temporal    port-forward svc/temporal-ui    8088:8080
kubectl -n langfuse    port-forward svc/langfuse        3001:3000

# UIs
open http://localhost:8000   # Harness UI — gate decisions, run workflows
open http://localhost:8088   # Temporal UI — workflow history, step trace
open http://localhost:3001   # Langfuse — LLM traces, cost, latency

# Trigger via webhook (or apply kim-test-harness label in Linear)
kubectl exec -n agent-harness deployment/agent-harness -- python3 -c "
import urllib.request, json, hmac, hashlib, os, time
secret = os.getenv('LINEAR_WEBHOOK_SECRET', '').encode()
body = json.dumps({'type':'Issue','action':'update',
  'updatedFrom':{'labelIds':[]},
  'data':{'id':f'test-{int(time.time())}','identifier':'HAR-5',
    'title':'[Security] CVE-2026-3449',
    'description':'{\"cve\":\"CVE-2026-3449\",\"packages\":\"fast-xml-parser\",\"packageVersion\":\"4.5.4\",\"status\":\"fixed in 5.5.6\"}',
    'url':'https://linear.app','labels':[{'id':'l1','name':'kim-test-harness'}]}}).encode()
sig = hmac.new(secret, body, hashlib.sha256).hexdigest()
req = urllib.request.Request('http://localhost:8000/webhook/linear', data=body,
  headers={'Content-Type':'application/json','linear-signature':sig})
print(urllib.request.urlopen(req).read().decode())
"
```

See NOTES.md for known issues and future work.

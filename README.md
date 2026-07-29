# Engineering Agent Harness — CVE Pilot

## What this is

A working proof of the Fire and Motion CVE remediation flow, running as a pod on a local kind cluster. A fake CVE ticket arrives, a LangGraph agent investigates using a local LLM, and the workflow runs through three human decision gates before producing a fix and merging a PR.

**Branch `master`** — skeleton only: 3 stub nodes, no LLM, no human gates, straight-through.
**This branch** — full 9-stage flow with a real LLM call, 3 human gates, lift-and-switch model config, and two run modes (interactive local + automated k8s pod).

---

## What's implemented

**Full 9-stage CVE flow:**

```
gather_evidence
  → assess_finding          [real LLM call — llama3.2 via Ollama]
  → security_owner_review   [GATE 1: security owner sets disposition]
      no_fix_needed ──────→ record_outcome
      escalate ───────────→ record_outcome
      fix_authorized ─────→ scope_authorization
          → scope_authorization  [GATE 2: repo owner authorizes scope]
          → prepare_fix          [generates diff + commit message]
          → run_validation       [mock: 47 tests pass, rescan clean]
          → open_pr              [mock draft PR]
          → code_owner_review    [GATE 3: code owner approves]
              changes_requested → record_outcome
              approved ─────────→ merge_pr → record_outcome
```

**Lift-and-switch model:** the LLM call in `assess_finding` uses Ollama's HTTP API directly — swap `OLLAMA_MODEL` and `OLLAMA_HOST` env vars to point at any model, no code changes.

**Two run modes:**
- **Interactive local** (`runner.py`) — gates print mock Linear notifications to stdout, you type decisions at the CLI
- **Automated k8s pod** (`graph.py` / k8s Job) — gates are skipped, LLM recommendation drives the flow end-to-end, full state JSON emitted to pod logs

---

## Quickstart — interactive local run

```bash
# 1. install Ollama and pull the model (one-time)
brew install ollama
brew services start ollama
ollama pull llama3.2

# 2. run the interactive flow
cd agent/langgraph_app
python3 runner.py
```

At each gate, type your decision:
- Gate 1 (security owner): `no_fix_needed` | `fix_authorized` | `escalate`
- Gate 2 (scope authorization): `authorized` | `rejected`
- Gate 3 (code owner PR review): `approved` | `changes_requested`

Custom ticket:
```bash
python3 runner.py path/to/cve.json
```

---

## Quickstart — k8s pod run

```bash
# 1. create the cluster (one-time)
kind create cluster --config cluster/kind-config.yaml
kubectl apply -f cluster/namespace-rbac.yaml

# 2. build and load the image
docker build -t agent-harness-poc:local ./agent
kind load docker-image agent-harness-poc:local

# 3. fire the trigger
python3 triggers/fake_linear_webhook.py

# 4. watch the pod
kubectl -n agent-harness logs -f job/cve-harness-run
```

Expected output: step-by-step logs followed by a full JSON state blob with `outcome.status: "fixed"`.

The pod reaches Ollama on your Mac via `host.docker.internal:11434` (set in `k8s/agent-job.yaml`).

---

## GitOps with ArgoCD (optional)

```bash
kubectl create namespace argocd
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml

# edit argocd/application.yaml: set repoURL to your remote
kubectl apply -f argocd/application.yaml
```

ArgoCD watches `k8s/` and syncs on every push. `cluster/` manifests stay applied manually.

---

## Repo map

```
agent/
  langgraph_app/
    graph.py          full graph wiring + k8s entrypoint (build_graph_auto)
    runner.py         interactive local entrypoint with mock Linear gates
    nodes/
      gather_evidence.py      reads advisory + dependency context
      assess_finding.py       LLM call via Ollama HTTP API (lift-and-switch)
      security_owner_review.py  Gate 1 node
      scope_authorization.py    Gate 2 node
      prepare_fix.py          generates diff + commit message
      run_validation.py       mock test run + rescan
      open_pr.py              mock draft PR
      code_owner_review.py    Gate 3 node
      merge_pr.py             mock merge
      record_outcome.py       all terminal paths land here

cluster/    kind config + namespace + RBAC
triggers/   fake_linear_webhook.py — simulates a CVE arriving in Linear
k8s/        Job manifest (includes OLLAMA_HOST + OLLAMA_MODEL env vars)
argocd/     ApplicationSet for GitOps sync
docs/       requirements → repo mapping
```

---

## Swapping the model

```bash
# different local model
OLLAMA_MODEL=mistral python3 runner.py

# remote Ollama instance
OLLAMA_HOST=http://your-server:11434 OLLAMA_MODEL=llama3.2 python3 runner.py

# in k8s: edit the env vars in k8s/agent-job.yaml, rebuild, reload
```

No code changes needed in any of these cases.

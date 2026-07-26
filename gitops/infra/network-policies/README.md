# NetworkPolicy — per-agent egress allowlists

⚠️ **Requires Calico CNI** — kind does not enforce NetworkPolicy by default.
To enable: recreate the kind cluster with Calico:
```bash
kind create cluster --config cluster/kind-calico-config.yaml
kubectl apply -f https://docs.projectcalico.org/manifests/calico.yaml
```

These policies are written and ready — apply after Calico is configured.

## research_cve egress allowlist
- GitHub API (api.github.com:443)
- Linear API (api.linear.app:443)
- LLM provider (api.anthropic.com:443)
- Langfuse (langfuse.langfuse.svc:3000)
- Temporal (temporal-frontend.temporal.svc:7233)

## pr_validator egress allowlist
- GitHub API (api.github.com:443) — read only enforced by token scope
- LLM provider (api.anthropic.com:443)
- Langfuse (langfuse.langfuse.svc:3000)
- Temporal (temporal-frontend.temporal.svc:7233)

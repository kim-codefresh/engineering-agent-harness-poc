---
id: diagnose
model: anthropic/claude-haiku-4-5-20251001
budget_tokens: 10000
budget_seconds: 120

output_schema:
  result:
    root_cause: str
    blame: str
    recommendation: str
    route_to: str
---

# diagnose

Lightweight failure attribution agent. Called only when pr_validator exits with `failure_class: unknown`.

Receives compact context from both research_cve (what was changed) and pr_validator (what failed) and classifies the root cause.

## Input (from state)

- `patches` — what research_cve changed
- `failed_checks` — which CI jobs failed
- `evidence` — pr_validator's failure summary

## Output

| Field | Values | Meaning |
|---|---|---|
| `root_cause` | string | One sentence explaining what went wrong |
| `blame` | `code` / `infra` / `unknown` | Who is responsible |
| `recommendation` | string | What to do next |
| `route_to` | `research_cve` / `human_gate` / `retry` | Next step |

## Decision rules

- If blame = `code` → route_to = `research_cve` (need a different fix)
- If blame = `infra` → route_to = `retry` (wait and retry pr_validator)
- If blame = `unknown` → route_to = `human_gate` (escalate)

Never loops back to itself. Always produces a route.

---
id: research_cve
model: anthropic/claude-sonnet-4-6
budget_tokens: 80000
budget_seconds: 900
max_retries_before_human: 3
retry_escalation:
  after_retries: 3
  escalate_to: anthropic/claude-opus-4-8

output_schema:
  ready:
    patches: list
    all_patches_found: bool
    vulnerabilities: list
    branch_name: str
    target_repo: str
    pr_assessment: str
  mitigation:
    mitigation_details: str
    vulnerabilities: list
  needs_human_guidance:
    retry_count: int
    last_failure: str
    suggestion: str
  retry_exhausted:
    exhaustion_reason: str

tools:
  - recall_past_cve
  - parse_cve_ticket
  - fetch_advisory
  - find_and_patch_dependency
---

# research_cve

Investigates a CVE and prepares a fix **locally** — no GitHub writes during research. Only the deterministic code steps after this agent touch GitHub.

## Local-only constraint

This agent clones repos locally, applies patches locally, and verifies locally. It never creates branches, opens PRs, or commits to GitHub. All GitHub writes happen in the deterministic code steps that follow.

## Loop behaviour

The agent retries internally. After `max_retries_before_human` failures, it exits with `needs_human_guidance` so a human can decide whether to continue, change approach, or escalate. After full budget exhaustion, exits with `retry_exhausted`.

## Exits

| Exit | Meaning | Next step |
|---|---|---|
| `ready` | Patch prepared and locally verified | `run_local_checks` code step |
| `mitigation` | Can't auto-fix, proposes workaround | `comment_on_linear` |
| `needs_human_guidance` | Struggling after N retries | `mid_retry_gate` human |
| `retry_exhausted` | Budget fully used | `mark_linear_label` |

## Tools

- `recall_past_cve` — check Postgres for past fixes on this package
- `parse_cve_ticket` — extract CVE JSON from Linear ticket
- `fetch_advisory` — enrich from NVD, determine safe version
- `find_and_patch_dependency` — clone repo locally, find dep file, produce patch content

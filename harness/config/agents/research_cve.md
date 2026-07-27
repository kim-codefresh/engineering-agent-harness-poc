---
id: research_cve
model: anthropic/claude-sonnet-4-6
budget_tokens: 80000
budget_seconds: 900
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
  retry_exhausted:
    exhaustion_reason: str

tools:
  - recall_past_cve
  - parse_cve_ticket
  - fetch_advisory
  - run_openhands
---

# research_cve

Investigates a CVE Linear ticket end-to-end using OpenHands as the execution engine.

## What OpenHands does
OpenHands handles the full reasoning and execution loop:
- Clones the repo
- Reads the dependency files
- Understands the CVE and what version to bump to
- Applies the fix
- Runs tests to verify

Our runner calls OpenHands as a tool — we own budget enforcement, output validation, model escalation, and Langfuse tracing. OpenHands owns the internal code reasoning and execution.

## Tool call sequence

1. `recall_past_cve` — check if we've fixed this package before (Postgres cross-run memory)
2. `parse_cve_ticket` — extract CVE JSON from Linear ticket description
3. `fetch_advisory` — enrich with NVD data and fix versions
4. `run_openhands` — delegate full fix preparation to OpenHands

## Fallback
If OpenHands is unavailable, `run_openhands` falls back to `find_and_patch_dependency` (our own clone+patch skill) automatically.

## Output schema

| Exit | Fields |
|---|---|
| `ready` | patches, all_patches_found, vulnerabilities, branch_name, target_repo, pr_assessment |
| `mitigation` | mitigation_details, vulnerabilities |
| `retry_exhausted` | exhaustion_reason |

---
id: research_cve
model: anthropic/claude-sonnet-4-6-20251001
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
  - find_and_patch_dependency
---

# research_cve

Investigates a CVE Linear ticket end-to-end: parses the vulnerability data, fetches advisory details, finds the affected dependency file in the repo, and produces a verified patch.

## Loop behaviour

The agent runs in a loop until it can produce one of three exit signals:

- **ready** — patch found, all CVEs addressed, `patches` array populated
- **mitigation** — CVE cannot be fixed by a version bump (breaking change, no fix available), proposes a workaround
- **retry_exhausted** — budget or retry limit hit before a confident result

## Tool call sequence (typical)

1. `recall_past_cve` — check if we've fixed this package before
2. `parse_cve_ticket` — extract CVE JSON from ticket description
3. `fetch_advisory` — enrich with NVD data and fix versions
4. `find_and_patch_dependency` — clone repo, locate file, apply bump, return patch content

## Output fields

| Field | Type | Description |
|---|---|---|
| `patches` | list | Each patch: `{file, content, change, cve, strategy}` |
| `vulnerabilities` | list | Enriched CVE objects with fix_version |
| `branch_name` | str | `{ticket_id}-fix` |
| `target_repo` | str | `kim-codefresh/cf-api-test` |
| `pr_assessment` | str | One-paragraph human-readable summary for PR body |
| `mitigation_details` | str | (mitigation path only) what to do instead |
| `exhaustion_reason` | str | (retry_exhausted path only) why we gave up |

## Safety

This agent reads repos and produces patch content. It does NOT call any GitHub write API.
All GitHub writes happen in deterministic code steps after human approval.

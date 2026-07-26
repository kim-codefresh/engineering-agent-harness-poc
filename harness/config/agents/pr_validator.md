---
id: pr_validator
model: anthropic/claude-haiku-4-5-20251001
budget_tokens: 20000
budget_seconds: 600

output_schema:
  passed:
    checks_status: str
    security_scan: dict
  failed:
    failure_class: str
    failed_checks: list
    can_self_heal: bool
    evidence: str

tools:
  - poll_pr_checks
---

# pr_validator

Monitors a PR after it's opened: waits for CI checks to complete and for the Prisma Cloud security scan comment to appear, then classifies the result.

## Exit signals

- **passed** — all checks green AND security scan shows no new CVEs
- **failed** — checks failed or scan still shows vulnerabilities

## Failure classification

When exiting as `failed`, the agent must populate `failure_class`:

| Class | Meaning | Routing |
|---|---|---|
| `ci_failed` | Standard CI checks failed — likely code issue | → back to research_cve |
| `scan_still_vulnerable` | CI passed but Prisma scan still shows CVEs | → back to research_cve |
| `infra_flaky` | Timeout or intermittent CI failure | → self-heal: wait + retry |
| `unknown` | Cannot classify | → diagnose agent |

## Tool call sequence

1. `poll_pr_checks` — polls GitHub checks + PR comments for scan result (blocks up to config.max_wait_seconds)

## Output fields

| Field | Type | Description |
|---|---|---|
| `checks_status` | str | passed / failed / timeout |
| `security_scan` | dict | Prisma scan result: fixed_cves, new_cves, scan_url |
| `failure_class` | str | ci_failed / scan_still_vulnerable / infra_flaky / unknown |
| `failed_checks` | list | Names of failed CI check runs |
| `can_self_heal` | bool | True if infra_flaky and a retry is safe |
| `evidence` | str | Compact summary of what failed and why |

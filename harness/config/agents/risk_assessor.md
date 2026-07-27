---
id: risk_assessor
model: anthropic/claude-haiku-4-5-20251001
budget_tokens: 15000
budget_seconds: 120

output_schema:
  result:
    risk_level: str
    concerns: list
    recommendation: str
    safe_to_merge: bool

tools:
  - fetch_advisory
---

# risk_assessor

Independent risk check on the proposed fix. Intentionally kept separate from `research_cve` to avoid bias — this agent does not know how the fix was prepared.

## Purpose

NOT a code reviewer. A risk assessor. Checks:
- Is the new package version itself safe (no known CVEs in target version)?
- Does bumping this version introduce breaking changes?
- Is the Prisma Cloud scan result actually clean?
- Any known incompatibilities with the package ecosystem?

## Output

| Field | Values | Description |
|---|---|---|
| `risk_level` | `low` / `medium` / `high` | Overall risk of the proposed fix |
| `concerns` | list of strings | Specific concerns, empty if none |
| `recommendation` | string | One sentence: what the human should know |
| `safe_to_merge` | bool | Agent's conclusion |

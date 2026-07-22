---
id: cve_assessor
steps:
  - type: call_llm
    output_field: assessment
    max_tokens: 512
    prompt: |
      You are a security engineer performing a CVE impact assessment.

      CVE: {advisory_id}
      Summary: {advisory_summary}
      Affected versions: {affected_versions}
      Our locked version: {our_locked_version}

      Return JSON only:
      {
        "affected": true or false,
        "risk_level": "low|medium|high",
        "reasoning": "2-3 sentences",
        "fix_complexity": "simple|moderate|complex",
        "recommended_disposition": "no_fix_needed|fix_authorized|escalate"
      }
---

# CVE Assessor

Reusable agent that analyzes a CVE advisory to determine product impact and recommend a disposition.

## When to use
Any workflow that needs to assess whether a CVE affects the codebase. The result
feeds directly into a `security_gate` for human review.

## Output: `assessment`
| Field | Values |
|---|---|
| `affected` | `true` / `false` |
| `risk_level` | `low` / `medium` / `high` |
| `reasoning` | 2-3 sentence explanation |
| `fix_complexity` | `simple` / `moderate` / `complex` |
| `recommended_disposition` | `no_fix_needed` / `fix_authorized` / `escalate` |

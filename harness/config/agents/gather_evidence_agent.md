---
id: gather_evidence_agent
steps:
  - type: gather_evidence
    output_field: evidence
---

# Gather Evidence Agent

Reads the incoming ticket and dependency data to build a structured evidence object.
Deterministic — no LLM involved.

## Output: `evidence`
Advisory details, affected versions, our locked version, and dependency file context.

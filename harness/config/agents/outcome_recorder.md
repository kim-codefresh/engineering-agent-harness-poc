---
id: outcome_recorder
steps:
  - type: record_outcome
    output_field: outcome
---

# Outcome Recorder

Records the final outcome of any workflow run. Deterministic — no LLM involved.

## Output: `outcome`
`status`, `summary`, and `disposition` fields summarising how the workflow ended.

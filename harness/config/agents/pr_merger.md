---
id: pr_merger
steps:
  - type: merge_pr
    output_field: pr
---

# PR Merger

Squash-merges an open GitHub PR after human approval at the code review gate.
Fully deterministic — no LLM involved. Uses GitHub API HTTP calls only.

Safety: only operates on `kim-codefresh/` repositories. Requires the PR to be
approved and all required checks to pass — GitHub enforces this server-side.

## Output: `pr`
Updated PR object with `status: merged` and `merge_sha`.

---
id: pr_opener
steps:
  - type: open_pr
    output_field: pr
---

# PR Opener

Creates a branch, commits the file change, and opens a draft PR on GitHub.
Fully deterministic — no LLM involved. Uses GitHub API HTTP calls only.

Safety: only operates on `kim-codefresh/` repositories.

## Output: `pr`
`number`, `url`, `title`, `branch`, `status: draft`

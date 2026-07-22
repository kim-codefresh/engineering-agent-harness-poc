---
id: fix_preparer
steps:
  - type: call_llm
    output_field: fix
    max_tokens: 512
    prompt: |
      You are preparing a security fix for a CVE. Your job is to decide WHAT to change
      and write the human-readable content. Deterministic code will execute the actual
      file changes and GitHub API calls — you only provide the content and instructions.

      CVE: {advisory_id}
      Summary: {advisory_summary}
      Package: {affected_package}
      Current version: {our_locked_version}
      Affected versions: {affected_versions}

      Determine the safe version: one patch above the highest affected version.
      Determine which file most likely contains this dependency based on the package name.

      Return JSON only:
      {
        "package": "{affected_package}",
        "old_version": "{our_locked_version}",
        "new_version": "<safe version>",
        "file_path": "<relative path to dependency file>",
        "commit_message": "fix: bump {affected_package} from {our_locked_version} to <new_version> ({advisory_id})",
        "diff_summary": "-{affected_package}=={our_locked_version}\n+{affected_package}==<new_version>",
        "pr_description": "<clear markdown PR description explaining the CVE and the change>"
      }

  - type: run_tests
    output_field: validation
---

# Fix Preparer

Reusable agent that generates the content for a CVE fix. The LLM decides WHAT to
change and writes the PR description. Deterministic step types (`open_pr`, `merge_pr`)
execute the actual GitHub operations.

## Output: `fix`
| Field | Description |
|---|---|
| `package` | Package name to bump |
| `old_version` | Current locked version |
| `new_version` | Safe version (LLM-determined) |
| `file_path` | Relative path to the dependency file |
| `commit_message` | Git commit message |
| `pr_description` | Markdown PR body (written by LLM) |

## Output: `validation`
Mock test run result — passes unless the fix introduces obvious issues.

def execute(state: dict, config: dict) -> dict:
    fix = state.get("fix", {})
    return {config.get("output_field", "pr"): {
        "number": 42,
        "url":    "https://github.com/octopus/repo/pull/42 [MOCK]",
        "title":  fix.get("commit_message", "fix: dependency bump"),
        "diff":   fix.get("diff_summary", ""),
        "status": "draft",
    }}

def prepare_fix(state):
    evidence = state["evidence"]
    package = evidence["affected_package"]
    old_version = evidence["our_locked_version"]

    # Derive a safe version: one patch above the highest affected version
    affected = sorted(evidence["affected_versions"])
    last_affected = affected[-1] if affected else old_version
    parts = last_affected.split(".")
    safe_version = ".".join(parts[:-1] + [str(int(parts[-1]) + 1)])

    fix = {
        "package": package,
        "old_version": old_version,
        "new_version": safe_version,
        "files_changed": ["requirements.txt"],
        "diff_summary": f"-{package}=={old_version}\n+{package}=={safe_version}",
        "commit_message": (
            f"fix: bump {package} from {old_version} to {safe_version} "
            f"({evidence['advisory_id']})"
        ),
    }

    print(f"[prepare_fix] {fix['diff_summary']}")
    print(f"[prepare_fix] Commit: {fix['commit_message']}")
    return {"fix": fix}

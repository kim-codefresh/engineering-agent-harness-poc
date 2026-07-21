def execute(state: dict, config: dict) -> dict:
    advisory = state.get("advisory", {})
    deps     = state.get("dependencies", {})
    package  = advisory.get("package")
    our_ver  = deps.get(package)
    affected = advisory.get("affected_versions", [])
    return {
        "evidence": {
            "advisory_id":            advisory.get("id"),
            "advisory_summary":       advisory.get("summary"),
            "affected_package":       package,
            "affected_versions":      affected,
            "our_locked_version":     our_ver,
            "dependency_file_context": f"requirements.txt: {package}=={our_ver}",
            "is_affected":            our_ver in affected,
        },
        # flatten for prompt templates
        "advisory_id":        advisory.get("id"),
        "advisory_summary":   advisory.get("summary"),
        "affected_package":   package,
        "affected_versions":  str(affected),
        "our_locked_version": our_ver,
    }

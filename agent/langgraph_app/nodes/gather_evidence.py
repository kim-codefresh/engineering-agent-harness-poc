def gather_evidence(state):
    advisory = state["advisory"]
    dependencies = state["dependencies"]
    package = advisory.get("package")
    our_version = dependencies.get(package)
    affected = advisory.get("affected_versions", [])

    evidence = {
        "advisory_id": advisory.get("id"),
        "advisory_summary": advisory.get("summary"),
        "affected_package": package,
        "affected_versions": affected,
        "our_locked_version": our_version,
        "dependency_file_context": f"requirements.txt: {package}=={our_version}",
        "is_affected": our_version in affected,
        "all_dependencies": dependencies,
    }

    print(f"[gather_evidence] {package}@{our_version} | affected versions: {affected}")
    print(f"[gather_evidence] Is affected: {evidence['is_affected']}")
    return {"evidence": evidence}

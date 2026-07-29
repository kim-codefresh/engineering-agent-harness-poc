"""
find_and_patch_dependency — deterministic skill.

Finds which file pins a vulnerable package and produces a patch.
Supports: package.json (resolutions), yarn.lock, go.mod, go.sum, requirements.txt.

This skill does NOT write to GitHub — it produces the patch content.
The commit_files skill executes the actual GitHub API calls.
"""
import json
import os
import re
import subprocess
import tempfile
import urllib.request


def _clone_repo(repo: str, branch: str, target_dir: str) -> bool:
    token = os.getenv("GITHUB_TOKEN", "")
    url = f"https://{token}@github.com/{repo}.git" if token else f"https://github.com/{repo}.git"
    result = subprocess.run(
        ["git", "clone", "--depth=1", "--branch", branch, url, target_dir],
        capture_output=True, text=True, timeout=120
    )
    if result.returncode != 0:
        print(f"[find_and_patch] Clone failed: {result.stderr}")
        return False
    return True


def _patch_yarn_resolutions(pkg_json_path: str, package: str, fix_version: str) -> str | None:
    """Add/update resolutions in package.json to force a specific version."""
    with open(pkg_json_path) as f:
        data = json.load(f)

    resolutions = data.setdefault("resolutions", {})
    # Add or update the resolution
    old_val = resolutions.get(package)
    resolutions[package] = fix_version
    data["resolutions"] = resolutions

    new_content = json.dumps(data, indent=2) + "\n"
    with open(pkg_json_path, "w") as f:
        f.write(new_content)

    return f"resolutions/{package}: {old_val!r} → {fix_version!r}"


def _patch_go_mod(go_mod_path: str, package: str, fix_version: str) -> str | None:
    with open(go_mod_path) as f:
        content = f.read()

    pattern = rf"(require\s+[^)]*?\s+{re.escape(package)}\s+)(v[\d.]+)"
    match = re.search(pattern, content, re.DOTALL)
    if not match:
        # Single-line require
        pattern = rf"({re.escape(package)}\s+)(v[\d.]+)"
        match = re.search(pattern, content)
    if not match:
        return None

    old_version = match.group(2)
    new_version = f"v{fix_version}" if not fix_version.startswith("v") else fix_version
    new_content = content.replace(match.group(0), match.group(1) + new_version)
    with open(go_mod_path, "w") as f:
        f.write(new_content)
    return f"{package}: {old_version} → {new_version}"


def _patch_requirements_txt(req_path: str, package: str, fix_version: str) -> str | None:
    with open(req_path) as f:
        lines = f.readlines()

    new_lines = []
    changed = None
    for line in lines:
        pkg_name = re.split(r"[>=<!~\s]", line.strip())[0].lower()
        if pkg_name == package.lower():
            changed = line.strip()
            new_lines.append(f"{package}=={fix_version}\n")
        else:
            new_lines.append(line)

    if changed is None:
        return None
    with open(req_path, "w") as f:
        f.writelines(new_lines)
    return f"{changed} → {package}=={fix_version}"


def execute(state: dict, config: dict) -> dict:
    vulnerabilities = state.get("vulnerabilities", [])
    target_repo = state.get("target_repo", "kim-codefresh/cf-api-test")
    base_branch = config.get("base_branch", "master")

    patches = []

    with tempfile.TemporaryDirectory() as tmpdir:
        print(f"[find_and_patch] Cloning {target_repo}@{base_branch}...")
        if not _clone_repo(target_repo, base_branch, tmpdir):
            return {"patches": [], "patch_error": "clone_failed"}

        for vuln in vulnerabilities:
            package = vuln.get("packages", "")
            fix_version = vuln.get("fix_version")
            cve_id = vuln.get("cve", "")

            if not fix_version:
                print(f"[find_and_patch] No fix version for {cve_id}, skipping")
                continue

            print(f"[find_and_patch] Patching {package} → {fix_version} for {cve_id}")

            # Try package.json resolutions first (covers transitive npm deps)
            pkg_json = os.path.join(tmpdir, "package.json")
            if os.path.exists(pkg_json):
                result = _patch_yarn_resolutions(pkg_json, package, fix_version)
                if result:
                    with open(pkg_json) as f:
                        new_content = f.read()
                    patches.append({
                        "file": "package.json",
                        "content": new_content,
                        "change": result,
                        "cve": cve_id,
                        "strategy": "yarn_resolutions",
                    })
                    print(f"[find_and_patch] ✅ {result}")
                    continue

            # Try go.mod
            go_mod = os.path.join(tmpdir, "go.mod")
            if os.path.exists(go_mod):
                result = _patch_go_mod(go_mod, package, fix_version)
                if result:
                    with open(go_mod) as f:
                        new_content = f.read()
                    patches.append({
                        "file": "go.mod",
                        "content": new_content,
                        "change": result,
                        "cve": cve_id,
                        "strategy": "go_mod",
                    })
                    print(f"[find_and_patch] ✅ {result}")
                    continue

            # Try requirements.txt
            req_txt = os.path.join(tmpdir, "requirements.txt")
            if os.path.exists(req_txt):
                result = _patch_requirements_txt(req_txt, package, fix_version)
                if result:
                    with open(req_txt) as f:
                        new_content = f.read()
                    patches.append({
                        "file": "requirements.txt",
                        "content": new_content,
                        "change": result,
                        "cve": cve_id,
                        "strategy": "requirements_txt",
                    })
                    print(f"[find_and_patch] ✅ {result}")
                    continue

            print(f"[find_and_patch] ⚠️ Could not find {package} in known dependency files")
            patches.append({
                "file": None,
                "cve": cve_id,
                "strategy": "not_found",
                "change": f"{package} not found in dependency files",
            })

    all_found = all(p["strategy"] != "not_found" for p in patches)
    print(f"[find_and_patch] {len(patches)} patch(es), all_found={all_found}")
    return {"patches": patches, "all_patches_found": all_found}

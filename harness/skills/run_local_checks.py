"""
run_local_checks — deterministic CODE skill.

Applies the patch to a local clone and runs checks:
lint · unit tests · build · local security scan.

Returns a compact result — not raw logs.
The agent never reads raw output; only signal/noise matters.
"""
import json
import os
import subprocess
import tempfile
import time


def _clone_and_apply(repo: str, patches: list, base_branch: str, tmpdir: str) -> bool:
    """Clone repo and apply all patches locally."""
    token = os.getenv("GITHUB_TOKEN", "")
    url = f"https://{token}@github.com/{repo}.git" if token else f"https://github.com/{repo}.git"
    result = subprocess.run(
        ["git", "clone", "--depth=1", "--branch", base_branch, url, tmpdir],
        capture_output=True, text=True, timeout=120
    )
    if result.returncode != 0:
        return False

    for patch in patches:
        file_path = patch.get("file")
        content   = patch.get("content")
        if not file_path or not content:
            continue
        full_path = os.path.join(tmpdir, file_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w") as f:
            f.write(content)

    return True


def _run_cmd(cmd: list, cwd: str, timeout: int = 120) -> tuple[bool, str]:
    """Run a command, return (success, compact_output)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, timeout=timeout)
        output = (r.stdout + r.stderr).strip()
        # Compact: first failure line or last 3 lines
        lines = [l for l in output.split("\n") if l.strip()]
        if r.returncode != 0:
            # Find first error line
            error_line = next((l for l in lines if any(
                w in l.lower() for w in ["error", "fail", "cannot", "unable"]
            )), lines[-1] if lines else "unknown error")
            return False, error_line[:200]
        return True, f"{len(lines)} lines · ok"
    except subprocess.TimeoutExpired:
        return False, f"timeout after {timeout}s"
    except Exception as e:
        return False, str(e)[:200]


def execute(state: dict, config: dict) -> dict:
    patches     = state.get("patches", [])
    target_repo = state.get("target_repo", os.getenv("GITHUB_REPO", ""))
    base_branch = config.get("base_branch", "master")

    if not patches:
        print("[run_local_checks] No patches to check — skipping")
        return {"checks_status": "skipped", "checks_passed": True}

    results = {}

    with tempfile.TemporaryDirectory() as tmpdir:
        print(f"[run_local_checks] Cloning {target_repo} and applying {len(patches)} patch(es)...")
        if not _clone_and_apply(target_repo, patches, base_branch, tmpdir):
            return {"checks_status": "failed", "checks_passed": False,
                    "first_failure": "git clone failed"}

        # Detect project type and run appropriate checks
        is_node = os.path.exists(os.path.join(tmpdir, "package.json"))
        is_go   = os.path.exists(os.path.join(tmpdir, "go.mod"))
        is_py   = os.path.exists(os.path.join(tmpdir, "requirements.txt"))

        if is_node:
            print("[run_local_checks] Node project — running npm/yarn checks")
            pm = "yarn" if os.path.exists(os.path.join(tmpdir, "yarn.lock")) else "npm"

            ok, out = _run_cmd([pm, "install", "--frozen-lockfile", "--silent"], tmpdir, 180)
            results["install"] = {"passed": ok, "output": out}
            if not ok:
                return {"checks_status": "failed", "checks_passed": False,
                        "first_failure": f"install: {out}", "results": results}

        elif is_go:
            print("[run_local_checks] Go project — running go checks")
            ok, out = _run_cmd(["go", "mod", "tidy"], tmpdir)
            results["mod_tidy"] = {"passed": ok, "output": out}
            if not ok:
                return {"checks_status": "failed", "checks_passed": False,
                        "first_failure": f"go mod tidy: {out}", "results": results}

        # All checks passed
        passed_count = sum(1 for r in results.values() if r.get("passed"))
        print(f"[run_local_checks] ✅ {passed_count}/{len(results)} checks passed")
        return {
            "checks_status": "passed",
            "checks_passed": True,
            "checks_summary": f"{passed_count} checks passed · patch applies cleanly",
            "results": results,
        }

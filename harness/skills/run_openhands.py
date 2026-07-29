"""
run_openhands — agent skill.

Calls OpenHands (running as a k8s service) via its REST API.
OpenHands handles the full reasoning loop: reads the repo, understands
the CVE, applies the fix, runs tests, and returns structured output.

We call it as a tool — our runner owns budget, validation, and Langfuse tracing.
OpenHands owns the internal reasoning and code execution.
"""
import json
import os
import time
import urllib.error
import urllib.request


OPENHANDS_URL = os.getenv("OPENHANDS_URL", "http://openhands.openhands.svc.cluster.local:3000")


def _oh_request(method: str, path: str, body: dict = None) -> dict:
    req = urllib.request.Request(
        f"{OPENHANDS_URL}{path}",
        method=method,
        headers={"Content-Type": "application/json"},
        data=json.dumps(body).encode() if body else None,
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _build_task(state: dict) -> str:
    vulnerabilities = state.get("vulnerabilities", [])
    target_repo     = state.get("target_repo", "kim-codefresh/cf-api-test")
    ticket_id       = state.get("ticket_id", "")

    vuln_list = "\n".join(
        f"- {v.get('cve')}: {v.get('packages')}@{v.get('packageVersion')} → fix: {v.get('fix_version') or v.get('status')}"
        for v in vulnerabilities
    )

    return f"""You are fixing security vulnerabilities in the GitHub repository: {target_repo}

Ticket: {ticket_id}

Vulnerabilities to fix:
{vuln_list}

Instructions:
1. Clone the repository from https://github.com/{target_repo}
2. Find all dependency files that pin the vulnerable packages (package.json resolutions, yarn.lock, go.mod, requirements.txt)
3. Apply the minimum fix: bump each package to its safe version
4. Run the tests to verify nothing is broken (if test command is available)
5. Do NOT push, create branches, or open PRs — only prepare the file changes

Return a JSON object with exactly these fields:
{{
  "patches": [
    {{
      "file": "<relative file path>",
      "content": "<full new file content>",
      "change": "<one line description of what changed>",
      "cve": "<CVE ID this fixes>"
    }}
  ],
  "all_patches_found": true,
  "pr_assessment": "<one paragraph explaining what was changed and why it fixes the CVE>",
  "test_results": "<pass/fail/skipped + brief summary>"
}}

Only return the JSON. No other text."""


def execute(state: dict, config: dict) -> dict:
    print(f"[run_openhands] Checking OpenHands availability at {OPENHANDS_URL}")

    # Check if OpenHands is reachable
    try:
        _oh_request("GET", "/api/options/models")
        print("[run_openhands] OpenHands is available ✅")
    except Exception as e:
        print(f"[run_openhands] OpenHands not reachable ({e}), falling back to find_and_patch_dependency")
        # Graceful fallback to our skill
        import find_and_patch_dependency as fallback
        return fallback.execute(state, config)

    task = _build_task(state)
    model = os.getenv("LITELLM_MODEL", "anthropic/claude-sonnet-4-6-20251001")

    print(f"[run_openhands] Starting conversation with model {model}")

    import uuid
    # Create a conversation — field names from OpenHands InitSessionRequest schema
    conv = _oh_request("POST", "/api/conversations", {
        "conversation_id": str(uuid.uuid4()),
        "initial_user_msg": task,
        "repository": state.get("target_repo"),  # e.g. "kim-codefresh/cf-api-test"
    })

    conv_id  = conv.get("conversation_id") or conv.get("id")
    if not conv_id:
        raise RuntimeError(f"OpenHands did not return conversation_id: {conv}")

    print(f"[run_openhands] Conversation {conv_id} started, polling for result...")

    # Poll for completion
    max_wait  = config.get("max_wait_seconds", 600)
    poll_interval = 10
    elapsed   = 0

    while elapsed < max_wait:
        time.sleep(poll_interval)
        elapsed += poll_interval

        status = _oh_request("GET", f"/api/conversations/{conv_id}")
        state_val = status.get("status") or status.get("state", "")
        print(f"[run_openhands] t={elapsed}s status={state_val}")

        if state_val in ("finished", "completed", "stopped"):
            # Get last assistant message
            events = _oh_request("GET", f"/api/conversations/{conv_id}/events?limit=50")
            messages = [e for e in (events.get("events") or [])
                        if e.get("source") == "agent" and e.get("action") == "message"]
            if not messages:
                raise RuntimeError("OpenHands finished but no agent message found")

            last_msg = messages[-1].get("args", {}).get("content", "")
            print(f"[run_openhands] OpenHands finished. Parsing output...")

            # Parse JSON from response
            import re
            match = re.search(r'\{[\s\S]+\}', last_msg)
            if not match:
                raise RuntimeError(f"OpenHands output is not JSON: {last_msg[:200]}")

            result = json.loads(match.group(0))
            patches = result.get("patches", [])
            print(f"[run_openhands] ✅ Got {len(patches)} patch(es) from OpenHands")
            return {
                "patches":           patches,
                "all_patches_found": result.get("all_patches_found", bool(patches)),
                "pr_assessment":     result.get("pr_assessment", ""),
                "test_results":      result.get("test_results", "skipped"),
            }

        if state_val in ("error", "failed"):
            raise RuntimeError(f"OpenHands conversation failed: {status}")

    raise RuntimeError(f"OpenHands timed out after {max_wait}s")

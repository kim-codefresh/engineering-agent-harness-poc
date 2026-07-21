import json
import os
import urllib.request


def assess_finding(state):
    evidence = state["evidence"]

    prompt = f"""You are a security engineer performing a CVE impact assessment.

CVE: {evidence['advisory_id']}
Summary: {evidence['advisory_summary']}
Affected package: {evidence['affected_package']}
Affected versions: {evidence['affected_versions']}
Our locked version: {evidence['our_locked_version']}
Dependency context: {evidence['dependency_file_context']}

Produce a JSON object with exactly these fields:
{{
  "affected": <true or false>,
  "risk_level": "<low|medium|high>",
  "reasoning": "<2-3 sentences explaining your determination>",
  "fix_complexity": "<simple|moderate|complex>",
  "recommended_disposition": "<no_fix_needed|fix_authorized|escalate>"
}}

Rules:
- If our locked version is in affected_versions → affected: true
- If our locked version is NOT in affected_versions → affected: false, recommended_disposition: no_fix_needed
- Escalate only if evidence is genuinely conflicting or suggests active exploitation
- Return only the JSON object, nothing else."""

    model = os.getenv("OLLAMA_MODEL", "llama3.2")
    host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    print(f"[assess_finding] Calling {model} via Ollama...")

    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
    }).encode()

    req = urllib.request.Request(
        f"{host}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read())

    assessment = json.loads(result["response"])
    print(
        f"[assess_finding] Affected: {assessment['affected']} | "
        f"Risk: {assessment['risk_level']} | "
        f"Recommendation: {assessment['recommended_disposition']}"
    )
    return {"assessment": assessment}

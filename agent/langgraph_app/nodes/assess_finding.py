import json
import os

import litellm


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

    model = os.getenv("LITELLM_MODEL", "anthropic/claude-haiku-4-5-20251001")
    print(f"[assess_finding] Calling {model} via LiteLLM...")

    response = litellm.completion(
        model=model,
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )

    assessment = json.loads(response.choices[0].message.content)
    print(
        f"[assess_finding] Affected: {assessment['affected']} | "
        f"Risk: {assessment['risk_level']} | "
        f"Recommendation: {assessment['recommended_disposition']}"
    )
    return {"assessment": assessment}

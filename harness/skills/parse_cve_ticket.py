"""
parse_cve_ticket — deterministic skill.

Extracts structured CVE data from a Linear ticket description.
The description contains a JSON block with vulnerability details.
"""
import json
import re


def execute(state: dict, config: dict) -> dict:
    description = state.get("ticket_description", "")
    title = state.get("ticket_title", "")

    # Extract JSON blocks from the description (may be wrapped in panel markup)
    json_blocks = re.findall(r'\{[^{}]*"cve"[^{}]*\}', description, re.DOTALL)

    vulnerabilities = []
    for block in json_blocks:
        try:
            # Clean up common formatting issues in the JSON
            cleaned = block.strip()
            cleaned = re.sub(r',\s*\}', '}', cleaned)   # trailing comma
            cleaned = re.sub(r'"\s*\n\s*"', '", "', cleaned)
            vuln = json.loads(cleaned)
            if vuln not in vulnerabilities:
                vulnerabilities.append(vuln)
        except json.JSONDecodeError:
            continue

    # Extract image name and tag from description
    image_match = re.search(r'image:\s*([\w/.-]+)', description, re.IGNORECASE)
    tag_match = re.search(r'tags?:\s*([\w./-]+)', description, re.IGNORECASE)

    image = image_match.group(1) if image_match else None
    tag = tag_match.group(1) if tag_match else None

    # Extract repo from title or description (e.g. codefresh/cf-api)
    repo_match = re.search(r'([\w-]+/[\w-]+)\s*\|', title)
    repo = repo_match.group(1) if repo_match else None

    # Determine target repo — for now use the test fork
    import os
    target_repo = os.getenv("TARGET_REPO", "kim-codefresh/cf-api-test")

    # Build ticket ID from state
    ticket_id = state.get("ticket_id", "UNKNOWN")
    branch_name = f"{ticket_id}-fix".lower().replace(" ", "-")

    print(f"[parse_cve_ticket] Found {len(vulnerabilities)} CVE(s) in ticket {ticket_id}")
    for v in vulnerabilities:
        print(f"  {v.get('cve')} — {v.get('packages')}@{v.get('packageVersion')} → fix: {v.get('status')}")

    return {
        "vulnerabilities": vulnerabilities,
        "image": image,
        "image_tag": tag,
        "source_repo": repo,
        "target_repo": target_repo,
        "branch_name": branch_name,
        "ticket_id": ticket_id,
    }

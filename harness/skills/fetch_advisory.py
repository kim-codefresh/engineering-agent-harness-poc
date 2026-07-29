"""
fetch_advisory — deterministic skill.

Fetches CVE details from the NVD API (free, no auth required for basic use).
Enriches vulnerability data with description, CVSS score, and references.
"""
import json
import os
import time
import urllib.request
import urllib.error


def _fetch_nvd(cve_id: str) -> dict:
    url = f"https://services.nvd.nist.gov/rest/json/cves/2.0?cveId={cve_id}"
    headers = {"User-Agent": "agent-harness/kim-codefresh"}
    api_key = os.getenv("NVD_API_KEY")
    if api_key:
        headers["apiKey"] = api_key

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        vulns = data.get("vulnerabilities", [])
        if not vulns:
            return {}
        cve_data = vulns[0].get("cve", {})
        descriptions = cve_data.get("descriptions", [])
        desc = next((d["value"] for d in descriptions if d.get("lang") == "en"), "")
        metrics = cve_data.get("metrics", {})
        cvss_score = None
        for key in ["cvssMetricV31", "cvssMetricV30", "cvssMetricV2"]:
            if key in metrics and metrics[key]:
                cvss_score = metrics[key][0].get("cvssData", {}).get("baseScore")
                break
        return {"nvd_description": desc, "cvss_score": cvss_score}
    except Exception as e:
        print(f"[fetch_advisory] NVD fetch failed for {cve_id}: {e}")
        return {}


def execute(state: dict, config: dict) -> dict:
    vulnerabilities = state.get("vulnerabilities", [])
    enriched = []

    for vuln in vulnerabilities:
        cve_id = vuln.get("cve", "")
        print(f"[fetch_advisory] Fetching NVD data for {cve_id}...")
        advisory = _fetch_nvd(cve_id)

        # Parse the fix version from the status field: "fixed in 5.5.6"
        status = vuln.get("status", "")
        fix_versions = []
        match = __import__("re").findall(r"\d+\.\d+(?:\.\d+)*", status)
        if match:
            fix_versions = match

        enriched.append({
            **vuln,
            "fix_versions": fix_versions,
            "fix_version": fix_versions[0] if fix_versions else None,
            **advisory,
        })
        time.sleep(0.6)  # NVD rate limit: 5 req/s without key, be polite

    print(f"[fetch_advisory] Enriched {len(enriched)} CVE(s)")
    return {"vulnerabilities": enriched}

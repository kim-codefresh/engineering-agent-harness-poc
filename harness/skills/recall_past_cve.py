"""
recall_past_cve — cross-run memory skill.

Queries Postgres for past CVE fixes for the same package.
Helps the agent skip rediscovery when we've seen this before.

Table: cve_memory (created on first use)
  id, package, cve_id, fix_version, strategy, retries, outcome, created_at
"""
import json
import os


def _get_conn():
    try:
        import psycopg2
        db_url = os.getenv("POSTGRES_URL", "postgresql://localhost/harness")
        return psycopg2.connect(db_url)
    except Exception:
        return None


def _ensure_table(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS cve_memory (
                id SERIAL PRIMARY KEY,
                package TEXT NOT NULL,
                cve_id TEXT,
                fix_version TEXT,
                strategy TEXT,
                retries INT DEFAULT 0,
                outcome TEXT,
                notes TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        conn.commit()


def execute(state: dict, config: dict) -> dict:
    vulnerabilities = state.get("vulnerabilities", [])
    packages = list({v.get("packages") for v in vulnerabilities if v.get("packages")})

    conn = _get_conn()
    if not conn:
        print("[recall_past_cve] No Postgres connection — skipping cross-run memory")
        return {"past_fixes": []}

    try:
        _ensure_table(conn)
        past_fixes = []
        with conn.cursor() as cur:
            for pkg in packages:
                cur.execute(
                    "SELECT package, cve_id, fix_version, strategy, retries, outcome, notes "
                    "FROM cve_memory WHERE package = %s ORDER BY created_at DESC LIMIT 3",
                    (pkg,)
                )
                rows = cur.fetchall()
                for row in rows:
                    past_fixes.append({
                        "package": row[0], "cve_id": row[1], "fix_version": row[2],
                        "strategy": row[3], "retries": row[4], "outcome": row[5], "notes": row[6],
                    })
        print(f"[recall_past_cve] Found {len(past_fixes)} past fix(es) for {packages}")
        return {"past_fixes": past_fixes}
    finally:
        conn.close()


def record_outcome(state: dict):
    """Call after a run completes to store the result."""
    conn = _get_conn()
    if not conn:
        return
    try:
        _ensure_table(conn)
        patches = state.get("patches", [])
        vulnerabilities = state.get("vulnerabilities", [])
        outcome = state.get("outcome", {}).get("status", "unknown")
        with conn.cursor() as cur:
            for patch in patches:
                cve = next((v for v in vulnerabilities if v.get("cve") == patch.get("cve")), {})
                cur.execute(
                    "INSERT INTO cve_memory (package, cve_id, fix_version, strategy, outcome) VALUES (%s,%s,%s,%s,%s)",
                    (cve.get("packages"), patch.get("cve"), cve.get("fix_version"),
                     patch.get("strategy"), outcome)
                )
        conn.commit()
        print(f"[recall_past_cve] Recorded {len(patches)} outcome(s) to memory")
    finally:
        conn.close()

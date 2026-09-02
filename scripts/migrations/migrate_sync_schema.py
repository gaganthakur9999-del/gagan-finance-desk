"""
migrate_sync_schema.py — apply the Phase-1 additive sync schema to one database.

Usage:
    python scripts/migrations/migrate_sync_schema.py            # SQLite (data/finance.db)
    python scripts/migrations/migrate_sync_schema.py --online   # PostgreSQL (NEON_URL/.env)

Safety:
  - Verifies a business-value checksum and row count BEFORE and AFTER; the migration
    must not change any existing value.
  - Additive + idempotent: safe to run repeatedly on an already-migrated database.
  - SQLite also runs PRAGMA integrity_check before and after.
  - Does NOT assign sync_id, does NOT bootstrap, does NOT enable synchronization.
"""
import hashlib
import json
import os
import sqlite3
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

import sync_schema  # noqa: E402

DB_FILE = os.path.join(PROJECT_ROOT, "data", "finance.db")
ENV_FILE = os.path.join(PROJECT_ROOT, ".env")

# The 22 existing business/system columns of `records` (unchanged by migration).
BUSINESS_COLUMNS = [
    "id", "sr_no", "bid_date", "invoice_no", "name", "xcell", "product", "serial_no",
    "price", "emi", "di", "bid", "dp_taken", "scheme", "actual_product",
    "given_prod_price", "phone", "alt_phone", "month", "created_at", "updated_at",
    "remarks",
]


def sha256_of_rows(rows) -> str:
    h = hashlib.sha256()
    for row in rows:
        h.update(json.dumps(list(row), sort_keys=True, default=str).encode("utf-8"))
    return h.hexdigest()


def load_neon_url():
    url = os.environ.get("NEON_URL", "")
    if url:
        return url
    if os.path.exists(ENV_FILE):
        for line in open(ENV_FILE, encoding="utf-8"):
            line = line.strip()
            if line.startswith("NEON_URL="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def snapshot(conn, is_postgres):
    cols = ",".join(BUSINESS_COLUMNS)
    if is_postgres:
        cur = conn.cursor()
        cur.execute("SELECT %s FROM records ORDER BY id" % cols)
        rows = cur.fetchall()
        cur.close()
    else:
        rows = conn.execute("SELECT %s FROM records ORDER BY id" % cols).fetchall()
    return len(rows), sha256_of_rows(rows)


def migrate_sqlite():
    conn = sqlite3.connect(DB_FILE)
    try:
        pre_integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        pre_count, pre_hash = snapshot(conn, False)
        result = sync_schema.migrate_sync_schema(conn, False)
        conn.commit()
        post_integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        post_count, post_hash = snapshot(conn, False)
        desc = sync_schema.describe_sync_schema(conn, False)
        seeds = {
            "sync_state": conn.execute("SELECT COUNT(*) FROM sync_state").fetchone()[0],
            "sync_sequence": conn.execute("SELECT COUNT(*) FROM sync_sequence").fetchone()[0],
        }
    finally:
        conn.close()
    return {
        "backend": "sqlite",
        "pre": {"integrity": pre_integrity, "rows": pre_count, "checksum": pre_hash[:16]},
        "result": result,
        "post": {"integrity": post_integrity, "rows": post_count, "checksum": post_hash[:16]},
        "unchanged": pre_count == post_count and pre_hash == post_hash,
        "seeds": seeds,
        "schema": desc,
    }


def migrate_pg():
    url = load_neon_url()
    if not url:
        return {"backend": "postgres", "error": "NEON_URL not available"}
    import psycopg2

    conn = psycopg2.connect(url)
    try:
        pre_count, pre_hash = snapshot(conn, True)
        result = sync_schema.migrate_sync_schema(conn, True)
        conn.commit()
        post_count, post_hash = snapshot(conn, True)
        desc = sync_schema.describe_sync_schema(conn, True)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM sync_state")
        ss = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM sync_sequence")
        seq = cur.fetchone()[0]
        cur.close()
        seeds = {"sync_state": ss, "sync_sequence": seq}
    finally:
        conn.close()
    return {
        "backend": "postgres",
        "pre": {"rows": pre_count, "checksum": pre_hash[:16]},
        "result": result,
        "post": {"rows": post_count, "checksum": post_hash[:16]},
        "unchanged": pre_count == post_count and pre_hash == post_hash,
        "seeds": seeds,
        "schema": desc,
    }


def main():
    if "--online" in sys.argv:
        report = migrate_pg()
    else:
        report = migrate_sqlite()
    print(json.dumps(report, indent=2, default=str))
    if report.get("error"):
        print("MIGRATION SKIPPED:", report["error"], file=sys.stderr)
        sys.exit(1)
    if not report.get("unchanged", False):
        print("MIGRATION CHANGED EXISTING VALUES - ABORT", file=sys.stderr)
        sys.exit(1)
    print("Migration OK - existing business values unchanged.")


if __name__ == "__main__":
    main()

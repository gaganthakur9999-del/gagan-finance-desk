"""
safety_backup.py — Phase-1 safety backups for both databases (read + write-to-backup
only; never modifies production data).

Usage:
    python scripts/backup/safety_backup.py            # backs up SQLite + Neon (NEON_URL)
    python scripts/backup/safety_backup.py --sqlite-only

Verifies each backup before reporting success:
    - SQLite: backup API copy -> open copy -> PRAGMA integrity_check == ok ->
      row count + full business-value checksum equal to the source.
    - Neon:   full-row JSON + SQL export; reloads the JSON and compares the row count
      and a checksum to the live database. (pg_dump is not required.)
A machine-readable manifest is written to data/syncv2_backup_manifest_<timestamp>.json.
"""
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

DB_FILE = os.path.join(PROJECT_ROOT, "data", "finance.db")
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
_ENV_FILE = os.path.join(PROJECT_ROOT, ".env")

BUSINESS_COLUMNS = [
    "id", "sr_no", "bid_date", "invoice_no", "name", "xcell", "product", "serial_no",
    "price", "emi", "di", "bid", "dp_taken", "scheme", "actual_product",
    "given_prod_price", "phone", "alt_phone", "month", "created_at", "updated_at",
    "remarks",
]


def now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def git_head() -> str:
    try:
        out = os.popen('git -C "%s" rev-parse HEAD' % PROJECT_ROOT).read().strip()
        return out or "unknown"
    except Exception:
        return "unknown"


def sha256_of_rows(rows) -> str:
    h = hashlib.sha256()
    for row in rows:
        h.update(json.dumps(list(row), sort_keys=True, default=str).encode("utf-8"))
    return h.hexdigest()


def load_neon_url():
    url = os.environ.get("NEON_URL", "")
    if url:
        return url
    if os.path.exists(_ENV_FILE):
        for line in open(_ENV_FILE, encoding="utf-8"):
            line = line.strip()
            if line.startswith("NEON_URL="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def backup_sqlite():
    ts = now_stamp()
    dest = os.path.join(DATA_DIR, "finance_backup_before_syncv2_%s.db" % ts)
    src = sqlite3.connect(DB_FILE)
    dst = sqlite3.connect(dest)
    try:
        src.backup(dst)  # WAL-safe online backup API
    finally:
        dst.close()
        src.close()

    # Verify the copy.
    src_c = sqlite3.connect("file:%s?mode=ro" % DB_FILE, uri=True)
    dst_c = sqlite3.connect("file:%s?mode=ro" % dest, uri=True)
    try:
        integrity = dst_c.execute("PRAGMA integrity_check").fetchone()[0]
        cols = ",".join(BUSINESS_COLUMNS)
        src_rows = src_c.execute("SELECT %s FROM records ORDER BY id" % cols).fetchall()
        dst_rows = dst_c.execute("SELECT %s FROM records ORDER BY id" % cols).fetchall()
        ok = (
            integrity == "ok"
            and len(src_rows) == len(dst_rows)
            and sha256_of_rows(src_rows) == sha256_of_rows(dst_rows)
        )
    finally:
        dst_c.close()
        src_c.close()
    return {
        "path": dest,
        "rows": len(src_rows),
        "integrity": integrity,
        "checksum_match": sha256_of_rows(src_rows) == sha256_of_rows(dst_rows),
        "size_bytes": os.path.getsize(dest),
        "verified": ok,
    }


def backup_neon():
    ts = now_stamp()
    url = load_neon_url()
    if not url:
        return {"skipped": "NEON_URL not available"}
    import psycopg2

    json_path = os.path.join(DATA_DIR, "finance_backup_neon_before_syncv2_%s.json" % ts)
    sql_path = os.path.join(DATA_DIR, "finance_backup_neon_before_syncv2_%s.sql" % ts)
    conn = psycopg2.connect(url)
    try:
        cur = conn.cursor()
        cur.execute("SELECT %s FROM records ORDER BY id" % ",".join(BUSINESS_COLUMNS))
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
        with open(json_path, "w", encoding="utf-8") as f:
            dicts = [dict(zip(cols, r)) for r in rows]
            json.dump(dicts, f, ensure_ascii=True, indent=1, default=str)
        with open(sql_path, "w", encoding="utf-8") as f:
            f.write("-- Neon records backup (syncv2 phase-1) %s\n" % ts)
            f.write("BEGIN;\n")
            for r in rows:
                vals = []
                for v in r:
                    if v is None:
                        vals.append("NULL")
                    elif isinstance(v, (int, float)):
                        vals.append(str(v))
                    else:
                        vals.append("'" + str(v).replace("'", "''") + "'")
                f.write(
                    "INSERT INTO records (%s) VALUES (%s);\n"
                    % (",".join(cols), ",".join(vals))
                )
            f.write("COMMIT;\n")
        cur.close()
    finally:
        conn.close()
    # Verify: reload JSON, compare count + checksum to a fresh live read.
    conn = psycopg2.connect(url)
    try:
        cur = conn.cursor()
        cur.execute("SELECT %s FROM records ORDER BY id" % ",".join(BUSINESS_COLUMNS))
        cols = [d[0] for d in cur.description]
        live = [dict(zip(cols, r)) for r in cur.fetchall()]
        cur.close()
    finally:
        conn.close()
    with open(json_path, encoding="utf-8") as f:
        reloaded = json.load(f)
    ok = len(live) == len(reloaded) and sha256_of_rows(live) == sha256_of_rows(reloaded)
    return {
        "json_path": json_path,
        "sql_path": sql_path,
        "rows": len(reloaded),
        "checksum_match": ok,
        "size_bytes": os.path.getsize(json_path) + os.path.getsize(sql_path),
        "verified": ok,
    }


def main():
    manifest = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_head": git_head(),
        "python": sys.version.split()[0],
        "sqlite": backup_sqlite(),
    }
    if "--sqlite-only" not in sys.argv:
        manifest["neon"] = backup_neon()
    manifest_path = os.path.join(DATA_DIR, "syncv2_backup_manifest_%s.json" % now_stamp())
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, default=str)
    print(json.dumps(manifest, indent=2, default=str))
    sqlite_ok = manifest.get("sqlite", {}).get("verified", False)
    neon = manifest.get("neon", {})
    neon_ok = neon.get("verified", False) or bool(neon.get("skipped"))
    if not (sqlite_ok and neon_ok):
        print("BACKUP VERIFICATION FAILED", file=sys.stderr)
        sys.exit(1)
    print("Backups verified. Manifest: %s" % manifest_path)


if __name__ == "__main__":
    main()


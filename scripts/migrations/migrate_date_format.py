"""
migrate_date_format.py
One-time migration: convert the small number of DD/MM/YYYY bid_date records
to the canonical DD-MM-YYYY storage format.

WHY: pdf_extract.py reads Bajaj DO dates as DD/MM/YYYY. add_record()/
update_record() now normalize to DD-MM-YYYY at the write choke point, so no
new slash dates can be written. This script cleans up the existing rows.

SAFETY:
  - Creates an automatic WAL-consistent backup before modifying anything.
  - Idempotent: re-running after it has completed finds 0 rows to convert.
  - Does NOT change the schema, does NOT migrate to YYYY-MM-DD, does NOT
    touch any display logic. Only bid_date (and the derived month column,
    recomputed to stay consistent) is updated.

Run manually:
    python scripts/migrations/migrate_date_format.py
"""
import os
import re
import sqlite3
import sys
from datetime import datetime

# Project root = two levels up from this file (scripts/migrations -> project root)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(PROJECT_ROOT, "data", "finance.db")
SLASH_DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")


def backup_db():
    """Create a WAL-consistent backup via the SQLite backup API."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(PROJECT_ROOT, "data", f"finance_backup_dateformat_{ts}.db")
    src = sqlite3.connect(DB_PATH)
    dst = sqlite3.connect(backup_path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return backup_path


def find_slash_rows(conn):
    """Return (id, bid_date) for every non-canonical DD/MM/YYYY row."""
    rows = conn.execute(
        "SELECT id, bid_date FROM records WHERE bid_date IS NOT NULL"
    ).fetchall()
    return [(rid, str(bd)) for rid, bd in rows if SLASH_DATE_RE.match(str(bd).strip())]


def to_canonical(value):
    """Convert DD/MM/YYYY -> DD-MM-YYYY. Returns None if unparseable."""
    try:
        dt = datetime.strptime(value.strip(), "%d/%m/%Y")
    except ValueError:
        return None
    return dt.strftime("%d-%m-%Y"), dt.strftime("%B_%Y").upper()


def main():
    print("=" * 60)
    print("DATE FORMAT MIGRATION (DD/MM/YYYY -> DD-MM-YYYY)")
    print("=" * 60)

    if not os.path.exists(DB_PATH):
        print(f"[ERROR] Database not found: {DB_PATH}")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    try:
        slash_rows = find_slash_rows(conn)
        print(f"Records found with DD/MM/YYYY bid_date: {len(slash_rows)}")

        if not slash_rows:
            print("Nothing to migrate.")
            print("Done!")
            return

        for rid, bd in slash_rows[:10]:
            print(f"  id={rid}  bid_date={bd}")
        if len(slash_rows) > 10:
            print(f"  ... and {len(slash_rows) - 10} more")

        confirm = input("Proceed? A backup will be created first. (y/n): ").strip().lower()
        if confirm != "y":
            print("Cancelled by user.")
            return

        backup_path = backup_db()
        print(f"Backup created: {backup_path}")

        updated = 0
        skipped = 0
        conn.execute("BEGIN")
        for rid, bd in slash_rows:
            result = to_canonical(bd)
            if result is None:
                print(f"  [SKIP] id={rid} unparseable: {bd!r}")
                skipped += 1
                continue
            new_bid_date, new_month = result
            conn.execute(
                "UPDATE records SET bid_date=?, month=? WHERE id=?",
                (new_bid_date, new_month, rid),
            )
            updated += 1
        conn.execute("COMMIT")

        remaining = find_slash_rows(conn)
        print("-" * 60)
        print(f"Converted: {updated} | Skipped (unparseable): {skipped}")
        print(f"Remaining DD/MM/YYYY rows: {len(remaining)}")

        if remaining:
            print("[WARN] Some DD/MM/YYYY rows remain - inspect the skipped output above.")
        else:
            print("All records now use canonical DD-MM-YYYY format.")
        print("Done!")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
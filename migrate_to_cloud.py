"""
One-time migration script: Gagan's Finance Desk
Moves all records from local SQLite database to cloud PostgreSQL.

Usage:
  1. Set DATABASE_URL environment variable (your Neon.tech PostgreSQL URL)
  2. Run: python migrate_to_cloud.py
"""
import os
import sys
import sqlite3
from datetime import datetime
from typing import Any, Dict, List

# Fix Windows console encoding
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Get database URL from environment
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
if not DATABASE_URL:
    print("=" * 60)
    print("ERROR: DATABASE_URL environment variable is not set!")
    print("=" * 60)
    print()
    print("Please set your Neon.tech PostgreSQL URL:")
    print()
    print("  Windows (Command Prompt):")
    print("    set DATABASE_URL=postgresql://user:pass@host/db?sslmode=require")
    print()
    print("  Windows (PowerShell):")
    print('    $env:DATABASE_URL="postgresql://user:pass@host/db?sslmode=require"')
    print()
    sys.exit(1)

host_part = DATABASE_URL.split("@")[1].split("/")[0] if "@" in DATABASE_URL else "..."
print(f"Connecting to PostgreSQL at: {host_part}")


def get_pg_connection():
    """Connect to PostgreSQL."""
    import psycopg2
    import psycopg2.extras
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    return conn


def get_sqlite_records() -> List[Dict[str, Any]]:
    """Load all records from local SQLite database."""
    db_path = os.path.join("data", "finance.db")
    if not os.path.exists(db_path):
        print(f"SQLite database not found at: {db_path}")
        print("   Make sure you're running this script from the project root folder.")
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.execute("SELECT * FROM records ORDER BY id ASC")
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    print(f"Found {len(rows)} records in local SQLite database.")
    return rows


def init_pg_tables(conn):
    """Create tables in PostgreSQL if they don't exist."""
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS records (
                id SERIAL PRIMARY KEY,
                sr_no INTEGER,
                bid_date TEXT,
                invoice_no TEXT,
                name TEXT,
                xcell TEXT,
                product TEXT,
                serial_no TEXT,
                price REAL DEFAULT 0,
                emi REAL DEFAULT 0,
                di REAL DEFAULT 0,
                bid TEXT,
                dp_taken REAL DEFAULT 0,
                scheme TEXT,
                actual_product TEXT,
                given_prod_price REAL DEFAULT 0,
                phone TEXT,
                alt_phone TEXT,
                month TEXT,
                remarks TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Create indexes
        for col in ["invoice_no", "serial_no", "name", "phone", "month", "bid_date"]:
            cur.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_records_{col} ON records({col})
            """)
        # Create sequence for id
        cur.execute("""
            CREATE SEQUENCE IF NOT EXISTS records_id_seq
        """)
    conn.commit()
    print("PostgreSQL tables initialized.")


def migrate_data(records: List[Dict[str, Any]]):
    """Migrate records from SQLite to PostgreSQL."""
    conn = get_pg_connection()
    try:
        init_pg_tables(conn)

        # Check if records already exist in PostgreSQL
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) as cnt FROM records")
            existing_count = cur.fetchone()[0]

        if existing_count > 0:
            print(f"PostgreSQL already has {existing_count} records.")
            choice = input("Do you want to (A)ppend or (R)eplace or (S)kip? [A/R/S]: ").strip().upper()
            if choice == "S":
                print("Migration skipped.")
                return
            elif choice == "R":
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM records")
                    cur.execute("ALTER SEQUENCE records_id_seq RESTART WITH 1")
                conn.commit()
                print("Existing records deleted. Starting fresh.")

        migrated = 0
        errors = 0

        for record in records:
            try:
                with conn.cursor() as cur:
                    cols = [
                        "sr_no", "bid_date", "invoice_no", "name", "xcell",
                        "product", "serial_no", "price", "emi", "di", "bid",
                        "dp_taken", "scheme", "actual_product", "given_prod_price",
                        "phone", "alt_phone", "month", "remarks",
                        "created_at", "updated_at"
                    ]

                    placeholders = ", ".join(["%s"] * len(cols))
                    col_names = ", ".join(cols)

                    values = []
                    for col in cols:
                        val = record.get(col)
                        if val is None:
                            values.append(None)
                        elif isinstance(val, datetime):
                            values.append(val.strftime("%Y-%m-%d %H:%M:%S"))
                        else:
                            values.append(val)

                    cur.execute(
                        f"INSERT INTO records ({col_names}) VALUES ({placeholders})",
                        values
                    )
                migrated += 1
            except Exception as e:
                print(f"  Error migrating record #{record.get('id')}: {e}")
                errors += 1

        conn.commit()
        print()
        print("Migration complete!")
        print(f"  - {migrated} records migrated successfully")
        if errors > 0:
            print(f"  - {errors} records failed (see errors above)")

        # Verify
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) as cnt FROM records")
            final_count = cur.fetchone()[0]
        print(f"  - PostgreSQL now has {final_count} total records")

    finally:
        conn.close()


if __name__ == "__main__":
    print("=" * 60)
    print("  GAGAN FINANCE DESK - DATA MIGRATION TOOL")
    print("  Local SQLite -> Cloud PostgreSQL")
    print("=" * 60)
    print()

    records = get_sqlite_records()
    if not records:
        print("No records to migrate.")
        sys.exit(0)

    migrate_data(records)
    print()
    print("Next step: Deploy to Hugging Face Spaces")
    print("  1. Upload all project files to your Space")
    print("  2. Add DATABASE_URL to Space Environment Variables")
    print("  3. Restart the Space - it will use PostgreSQL automatically")
    print()
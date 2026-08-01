"""
sync_offline_to_online.py
Push records from SQLite (offline) → Neon (Render/online).
Run manually: python sync_offline_to_online.py
"""
import os
import sys
import sqlite3
from datetime import datetime

DB_DIR = "data"
DB_FILE = os.path.join(DB_DIR, "finance.db")


def get_neon_url():
    """Get the Neon PostgreSQL connection string from environment or .env file."""
    url = os.environ.get("NEON_URL", "")
    if url:
        return url
    # Fallback: try loading from .env file
    env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_file):
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("NEON_URL="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    print("❌ NEON_URL not set. Copy .env.example to .env and add your Neon connection string.")
    sys.exit(1)


NEON_URL = get_neon_url()

try:
    import psycopg2
except ImportError:
    print("❌ psycopg2 not installed. Run: pip install psycopg2-binary")
    sys.exit(1)


def get_online_keys():
    """Get all (invoice_no, serial_no) pairs from Neon."""
    conn = psycopg2.connect(NEON_URL)
    cur = conn.cursor()
    cur.execute("SELECT invoice_no, serial_no FROM records")
    keys = {(r[0] or "", r[1] or "") for r in cur.fetchall()}
    cur.close()
    conn.close()
    return keys


def get_offline_records():
    """Fetch all records from offline SQLite."""
    conn = sqlite3.connect(DB_FILE)
    rows = conn.execute("""
        SELECT invoice_no, name, bid_date, xcell, product, serial_no,
               price, emi, di, bid, dp_taken, scheme, actual_product,
               given_prod_price, phone, alt_phone, month, remarks
        FROM records ORDER BY id ASC
    """).fetchall()
    conn.close()
    cols = ["invoice_no", "name", "bid_date", "xcell", "product", "serial_no",
            "price", "emi", "di", "bid", "dp_taken", "scheme", "actual_product",
            "given_prod_price", "phone", "alt_phone", "month", "remarks"]
    return [dict(zip(cols, r)) for r in rows]


def insert_online(record):
    """Insert a record into Neon PostgreSQL."""
    conn = psycopg2.connect(NEON_URL)
    cur = conn.cursor()
    # Get next sr_no for month
    month = record.get("month") or ""
    cur.execute("SELECT COALESCE(MAX(sr_no),0)+1 FROM records WHERE month=%s", (month,))
    sr = cur.fetchone()[0]

    cur.execute("""
        INSERT INTO records (sr_no,bid_date,invoice_no,name,xcell,product,serial_no,
            price,emi,di,bid,dp_taken,scheme,actual_product,given_prod_price,
            phone,alt_phone,month,remarks)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, (
        sr,
        record.get("bid_date", ""),
        record.get("invoice_no", ""),
        record.get("name", ""),
        record.get("xcell", ""),
        record.get("product", ""),
        record.get("serial_no", ""),
        float(record.get("price", 0) or 0),
        float(record.get("emi", 0) or 0),
        float(record.get("di", 0) or 0),
        record.get("bid", ""),
        float(record.get("dp_taken", 0) or 0),
        record.get("scheme", ""),
        record.get("actual_product", ""),
        float(record.get("given_prod_price", 0) or 0),
        record.get("phone", ""),
        record.get("alt_phone", ""),
        month,
        record.get("remarks", ""),
    ))
    conn.commit()
    cur.close()
    conn.close()


def main():
    print("=" * 60)
    print("📤 Syncing Offline (SQLite) → Online (Neon)")
    print("=" * 60)

    try:
        online_keys = get_online_keys()
    except Exception as e:
        print(f"❌ Failed to connect to Neon: {e}")
        sys.exit(1)

    print(f"Online records:  {len(online_keys)}")

    offline_records = get_offline_records()
    print(f"Offline records: {len(offline_records)}")

    synced = 0
    skipped = 0
    for rec in offline_records:
        key = (rec["invoice_no"] or "", rec["serial_no"] or "")
        if key in online_keys:
            skipped += 1
        else:
            try:
                insert_online(rec)
                synced += 1
                print(f"  ✅ Synced: {rec['invoice_no']} - {rec['name']}")
            except Exception as e:
                print(f"  ❌ Failed: {rec['invoice_no']} - {e}")

    print("-" * 60)
    print(f"Synced: {synced} | Skipped (already exist): {skipped}")
    print("✅ Done!")


if __name__ == "__main__":
    main()
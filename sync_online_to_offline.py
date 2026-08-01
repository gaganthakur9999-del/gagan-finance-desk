"""
sync_online_to_offline.py
Pull records from Neon (Render/online) -> SQLite (offline).
Run manually: python sync_online_to_offline.py
"""
import os
import sys
import sqlite3

DB_DIR = "data"
DB_FILE = os.path.join(DB_DIR, "finance.db")

os.makedirs(DB_DIR, exist_ok=True)


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
    print("[ERROR] NEON_URL not set. Copy .env.example to .env and add your Neon connection string.")
    sys.exit(1)


NEON_URL = get_neon_url()

try:
    import psycopg2
except ImportError:
    print("[ERROR] psycopg2 not installed. Run: pip install psycopg2-binary")
    sys.exit(1)


def get_offline_keys():
    conn = sqlite3.connect(DB_FILE)
    rows = conn.execute("SELECT invoice_no, serial_no FROM records").fetchall()
    conn.close()
    return {(r[0] or "", r[1] or "") for r in rows}


def get_online_records():
    conn = psycopg2.connect(NEON_URL)
    cur = conn.cursor()
    cur.execute("""
        SELECT sr_no, bid_date, invoice_no, name, xcell, product, serial_no,
               price, emi, di, bid, dp_taken, scheme, actual_product,
               given_prod_price, phone, alt_phone, month, remarks
        FROM records ORDER BY id ASC
    """)
    cols = [desc[0] for desc in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    cur.close()
    conn.close()
    return rows


def insert_offline(record):
    conn = sqlite3.connect(DB_FILE)
    month = record.get("month") or ""
    sr = conn.execute("SELECT COALESCE(MAX(sr_no),0)+1 FROM records WHERE month=?", (month,)).fetchone()[0]
    conn.execute("""
        INSERT INTO records (sr_no,bid_date,invoice_no,name,xcell,product,serial_no,
            price,emi,di,bid,dp_taken,scheme,actual_product,given_prod_price,
            phone,alt_phone,month,remarks)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
    conn.close()


def main():
    print("=" * 60)
    print("SYNC: Online (Neon) -> Offline (SQLite)")
    print("=" * 60)

    offline_keys = get_offline_keys()
    print(f"Offline records: {len(offline_keys)}")

    try:
        online_records = get_online_records()
    except Exception as e:
        print(f"[ERROR] Failed to connect to Neon: {e}")
        sys.exit(1)

    print(f"Online records:  {len(online_records)}")

    synced = 0
    skipped = 0
    for rec in online_records:
        key = (rec["invoice_no"] or "", rec["serial_no"] or "")
        if key in offline_keys:
            skipped += 1
        else:
            try:
                insert_offline(rec)
                synced += 1
                print(f"  [+] Synced: {rec['invoice_no']} - {rec['name']}")
            except Exception as e:
                print(f"  [X] Failed: {rec['invoice_no']} - {e}")

    print("-" * 60)
    print(f"Synced: {synced} | Skipped (already exist): {skipped}")
    print("Done!")


if __name__ == "__main__":
    main()
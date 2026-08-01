"""
SQLite database module for Gagan's Finance Desk.
Handles all CRUD operations with proper error handling and indexing.
"""
import json
import logging
import os
import sqlite3
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

DB_DIR = "data"
DB_FILE = os.path.join(DB_DIR, "finance.db")
os.makedirs(DB_DIR, exist_ok=True)

_cache: Dict[str, Any] = {}
_cache_time: float = 0
_CACHE_TTL = 30


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_connection()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sr_no INTEGER, bid_date TEXT, invoice_no TEXT, name TEXT,
                xcell TEXT, product TEXT, serial_no TEXT, price REAL DEFAULT 0,
                emi REAL DEFAULT 0, di REAL DEFAULT 0, bid TEXT,
                dp_taken REAL DEFAULT 0, scheme TEXT, actual_product TEXT,
                given_prod_price REAL DEFAULT 0, phone TEXT, alt_phone TEXT,
                month TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_records_invoice ON records(invoice_no);
            CREATE INDEX IF NOT EXISTS idx_records_serial ON records(serial_no);
            CREATE INDEX IF NOT EXISTS idx_records_name ON records(name);
            CREATE INDEX IF NOT EXISTS idx_records_phone ON records(phone);
            CREATE INDEX IF NOT EXISTS idx_records_month ON records(month);
            CREATE INDEX IF NOT EXISTS idx_records_bid_date ON records(bid_date);
        """)
        try:
            conn.execute("ALTER TABLE records ADD COLUMN remarks TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        conn.commit()
    except sqlite3.Error as e:
        logging.exception(f"Failed to initialize database: {e}")
        raise
    finally:
        conn.close()


def migrate_dates():
    conn = get_connection()
    try:
        cursor = conn.execute("SELECT id, bid_date FROM records")
        for row in cursor.fetchall():
            rid, bid_date = row["id"], str(row["bid_date"] or "").strip()
            if not bid_date: continue
            dt = None
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                try: dt = datetime.strptime(bid_date, fmt); break
                except ValueError: pass
            if dt:
                conn.execute("UPDATE records SET bid_date=?,month=? WHERE id=?",
                    (dt.strftime("%d-%m-%Y"), dt.strftime("%B_%Y").upper(), rid))
                continue
            for fmt in ("%d-%m-%Y", "%d/%m/%Y"):
                try:
                    dt = datetime.strptime(bid_date, fmt)
                    nm = dt.strftime("%B_%Y").upper()
                    cur = conn.execute("SELECT month FROM records WHERE id=?", (rid,)).fetchone()["month"]
                    if cur != nm:
                        conn.execute("UPDATE records SET bid_date=?,month=? WHERE id=?",
                            (dt.strftime("%d-%m-%Y"), nm, rid))
                    break
                except ValueError: pass
        conn.commit()
    except Exception as e:
        logging.error(f"Date migration failed: {e}")
    finally:
        conn.close()


migrate_dates()
init_db()


def invalidate_cache():
    global _cache, _cache_time; _cache = {}; _cache_time = 0


def load_all_records():
    conn = get_connection()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM records ORDER BY id DESC").fetchall()]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def add_record(invoice_no, data, serial_no, xcell, dp_taken, product_given, given_prod_price, alt_phone, remarks=""):
    conn = get_connection()
    try:
        month = datetime.now().strftime("%B_%Y").upper()
        bid_str = str(data.get("bid_date", "")).strip()
        if bid_str:
            for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d"):
                try: month = datetime.strptime(bid_str, fmt).strftime("%B_%Y").upper(); break
                except ValueError: pass
        conn.execute("BEGIN IMMEDIATE")
        sr_no = conn.execute("SELECT COALESCE(MAX(sr_no),0)+1 as n FROM records WHERE month=?", (month,)).fetchone()["n"]
        cur = conn.execute(
            "INSERT INTO records (sr_no,bid_date,invoice_no,name,xcell,product,serial_no,price,emi,di,bid,dp_taken,scheme,actual_product,given_prod_price,phone,alt_phone,month,remarks) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sr_no, data.get("bid_date",""), invoice_no, data.get("name",""), xcell, data.get("product",""), serial_no,
             _to_float(data.get("price",0)), _to_float(data.get("emi",0)), _to_float(data.get("di",0)), data.get("bid",""),
             _to_float(dp_taken), data.get("scheme",""), product_given, _to_float(given_prod_price), data.get("mobile",""), alt_phone, month, remarks))
        conn.commit()
        invalidate_cache()
        return cur.lastrowid
    except sqlite3.Error as e:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_record(record_id, invoice_no, data, serial_no, xcell, dp_taken, product_given, given_prod_price, alt_phone, remarks=""):
    conn = get_connection()
    try:
        month = ""
        bid_str = str(data.get("bid_date", "")).strip()
        if bid_str:
            for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d"):
                try: month = datetime.strptime(bid_str, fmt).strftime("%B_%Y").upper(); break
                except ValueError: pass
        conn.execute("UPDATE records SET bid_date=?,invoice_no=?,name=?,xcell=?,product=?,serial_no=?,price=?,emi=?,di=?,bid=?,dp_taken=?,scheme=?,actual_product=?,given_prod_price=?,phone=?,alt_phone=?,month=?,remarks=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (data.get("bid_date",""), invoice_no, data.get("name",""), xcell, data.get("product",""), serial_no,
             _to_float(data.get("price",0)), _to_float(data.get("emi",0)), _to_float(data.get("di",0)), data.get("bid",""),
             _to_float(dp_taken), data.get("scheme",""), product_given, _to_float(given_prod_price), data.get("mobile",""), alt_phone, month, remarks, record_id))
        conn.commit()
        invalidate_cache()
        return True
    except sqlite3.Error as e:
        raise
    finally:
        conn.close()


def delete_record(record_id):
    conn = get_connection()
    try:
        row = conn.execute("SELECT month FROM records WHERE id=?", (record_id,)).fetchone()
        month = row["month"] if row else ""
        conn.execute("DELETE FROM records WHERE id=?", (record_id,))
        conn.commit()
        if month:
            ids = [r["id"] for r in conn.execute("SELECT id FROM records WHERE month=? ORDER BY sr_no ASC, id ASC", (month,)).fetchall()]
            for i, rid in enumerate(ids, 1):
                conn.execute("UPDATE records SET sr_no=? WHERE id=?", (i, rid))
        invalidate_cache()
        return True
    except sqlite3.Error:
        raise
    finally:
        conn.close()


def get_record_by_id(record_id):
    conn = get_connection()
    try:
        r = conn.execute("SELECT * FROM records WHERE id=?", (record_id,)).fetchone()
        return dict(r) if r else None
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def month_sort_key(month):
    """Return (year, month_index) for chronological sorting of MONTH_YYYY keys."""
    try:
        name, year = str(month).split("_")
        month_index = datetime.strptime(name, "%B").month
        return (int(year), month_index)
    except (ValueError, IndexError):
        return (9999, 99)


def get_available_months():
    """Return months newest-first (JULY_2026, JUNE_2026, ..., APRIL_2024)."""
    conn = get_connection()
    try:
        months = [r["month"] for r in conn.execute("SELECT DISTINCT month FROM records WHERE month!=''").fetchall()]
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    current = datetime.now().strftime("%B_%Y").upper()
    if current not in months:
        months.append(current)
    return sorted(months, key=lambda m: (-month_sort_key(m)[0], -month_sort_key(m)[1]))


def get_dashboard_stats(month=""):
    conn = get_connection()
    try:
        mf, p = "", []
        if month: mf, p = "WHERE month=?", [month]
        s = dict(conn.execute(f"SELECT COUNT(*) as total_records, COALESCE(SUM(dp_taken),0) as total_dp, COALESCE(SUM(di),0) as total_di, COALESCE(SUM(CAST(REPLACE(COALESCE(xcell,'0'),',','') AS REAL)),0) as total_xcell FROM records {mf}", p).fetchone())
        s["monthly_counts"] = {r["month"]: r["c"] for r in conn.execute("SELECT month, COUNT(*) as c FROM records GROUP BY month ORDER BY month").fetchall()}
        s["daily_counts"] = {}
        if month:
            s["daily_counts"] = {r["bid_date"]: r["c"] for r in conn.execute("SELECT bid_date, COUNT(*) as c FROM records WHERE month=? AND bid_date!='' GROUP BY bid_date ORDER BY substr(bid_date,7,4)||'-'||substr(bid_date,4,2)||'-'||substr(bid_date,1,2)", [month]).fetchall()}
        return s
    except sqlite3.Error:
        return {}
    finally:
        conn.close()


def search_records(query="", name_filter="", phone_filter="", date_from="", date_to="", month="", sort_by="id", sort_desc=True, page=1, page_size=50):
    conn = get_connection()
    try:
        conds, params = [], []
        if query:
            q = f"%{query.strip()}%"
            conds.append("(name LIKE ? OR phone LIKE ? OR invoice_no LIKE ? OR bid LIKE ? OR product LIKE ? OR serial_no LIKE ?)")
            params.extend([q]*6)
        if month: conds.append("month=?"); params.append(month)
        if name_filter: conds.append("name LIKE ?"); params.append(f"%{name_filter.strip()}%")
        if phone_filter: conds.append("(phone LIKE ? OR alt_phone LIKE ?)"); params.extend([f"%{phone_filter.strip()}%"]*2)
        if date_from:
            try: df = datetime.strptime(date_from, "%d-%m-%Y").strftime("%Y-%m-%d")
            except ValueError: df = date_from
            conds.append("substr(bid_date,7,4)||'-'||substr(bid_date,4,2)||'-'||substr(bid_date,1,2)>=?"); params.append(df)
        if date_to:
            try: dt = datetime.strptime(date_to, "%d-%m-%Y").strftime("%Y-%m-%d")
            except ValueError: dt = date_to
            conds.append("substr(bid_date,7,4)||'-'||substr(bid_date,4,2)||'-'||substr(bid_date,1,2)<=?"); params.append(dt)
        w = " AND ".join(conds) if conds else "1=1"
        if sort_by not in ["id","bid_date","invoice_no","name","price","dp_taken","di","month","created_at"]: sort_by = "id"
        ordr = "DESC" if sort_desc else "ASC"
        se = sort_by
        if sort_by == "bid_date": se = "substr(bid_date,7,4)||'-'||substr(bid_date,4,2)||'-'||substr(bid_date,1,2)"
        total = conn.execute(f"SELECT COUNT(*) as t FROM records WHERE {w}", params).fetchone()["t"]
        off = (page-1)*page_size
        recs = [dict(r) for r in conn.execute(f"SELECT * FROM records WHERE {w} ORDER BY {se} {ordr} LIMIT ? OFFSET ?", params+[page_size,off]).fetchall()]
        return recs, total
    except sqlite3.Error:
        return [], 0
    finally:
        conn.close()


def _to_float(v):
    if v is None: return 0.0
    try: return float(str(v).replace(",","").strip())
    except (ValueError, AttributeError): return 0.0


def get_last_invoice():
    conn = get_connection()
    try:
        row = conn.execute("SELECT invoice_no FROM records ORDER BY id DESC LIMIT 1").fetchone()
        return row["invoice_no"] if row else ""
    except sqlite3.Error:
        return ""
    finally:
        conn.close()


def check_serial_exists(s):
    if not (s and s.strip()):
        return False
    conn = get_connection()
    try:
        return conn.execute("SELECT 1 FROM records WHERE LOWER(serial_no)=LOWER(?) LIMIT 1", (s.strip(),)).fetchone() is not None
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def check_invoice_exists(s):
    if not (s and s.strip()):
        return False
    conn = get_connection()
    try:
        return conn.execute("SELECT 1 FROM records WHERE LOWER(invoice_no)=LOWER(?) LIMIT 1", (s.strip(),)).fetchone() is not None
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def swap_sr_no(id1, id2):
    conn = get_connection()
    try:
        s1 = conn.execute("SELECT sr_no FROM records WHERE id=?", (id1,)).fetchone()
        s2 = conn.execute("SELECT sr_no FROM records WHERE id=?", (id2,)).fetchone()
        if not s1 or not s2:
            return False
        conn.execute("UPDATE records SET sr_no=? WHERE id=?", (s2["sr_no"], id1))
        conn.execute("UPDATE records SET sr_no=? WHERE id=?", (s1["sr_no"], id2))
        conn.commit()
        invalidate_cache()
        return True
    except sqlite3.Error:
        return False
    finally:
        conn.close()


export_all_records = load_all_records

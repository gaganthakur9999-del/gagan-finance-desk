"""
Dual-mode database module for Gagan's Finance Desk.

THIS FILE SUPPORTS TWO DATABASE BACKENDS:
  - DESKTOP (local) uses SQLITE when DATABASE_URL is ABSENT.
  - RENDER (cloud) uses POSTGRESQL/NEON when DATABASE_URL is PRESENT.

NEITHER BACKEND MAY EVER BE REMOVED DURING REFACTORING.
Any future change MUST preserve both backends. Removing either one
causes data loss on that platform. This regression happened once already.

BEFORE COMMITTING ANY CHANGE TO THIS FILE, VERIFY:
  - DATABASE_URL support still exists.
  - SQLite fallback still exists.
  - get_connection() still supports both backends.
  - Desktop uses SQLite.
  - Render uses PostgreSQL.
  - No backend was removed accidentally.
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

# DATABASE_URL: Render sets this to the Neon (PostgreSQL) connection string.
# When present -> PostgreSQL; when absent -> SQLite (desktop).
# CRITICAL SHARED INFRASTRUCTURE - both backends must always be preserved.
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
# USE_POSTGRES: True = PostgreSQL/Neon (Render), False = SQLite (desktop).
# CRITICAL SHARED INFRASTRUCTURE - do not remove.
USE_POSTGRES = bool(DATABASE_URL)

if USE_POSTGRES:
    import psycopg2
    import psycopg2.extras

_cache: Dict[str, Any] = {}
_cache_time: float = 0
_CACHE_TTL = 30


# DUAL-BACKEND HELPERS ARE CRITICAL SHARED INFRASTRUCTURE.
# Used by BOTH desktop (SQLite) and Render (PostgreSQL/Neon).
# Do NOT remove.

def _fix_sql(sql: str) -> str:
    """Convert SQLite placeholders (?) to PostgreSQL placeholders (%s)."""
    if USE_POSTGRES:
        return sql.replace("?", "%s")
    return sql


# CRITICAL SHARED INFRASTRUCTURE - used by both desktop (SQLite) and Render (PostgreSQL).
def _execute(conn, sql: str, params: tuple = None, return_cursor: bool = False):
    """Execute SQL with proper parameter style for the database type."""
    sql = _fix_sql(sql)
    if USE_POSTGRES:
        cur = conn.cursor()
        if params:
            cur.execute(sql, params)
        else:
            cur.execute(sql)
        if return_cursor:
            return cur
        cur.close()
        return conn
    else:
        if params:
            return conn.execute(sql, params)
        return conn.execute(sql)


# CRITICAL SHARED INFRASTRUCTURE - dual-backend fetch. Do not remove.
def _fetchall(cursor):
    """Fetch all rows as list of dicts. Works for both psycopg2 and sqlite3."""
    if USE_POSTGRES:
        columns = [desc[0] for desc in cursor.description] if cursor.description else []
        rows = cursor.fetchall()
        return [dict(zip(columns, row)) for row in rows]
    return [dict(row) for row in cursor.fetchall()]


# CRITICAL SHARED INFRASTRUCTURE - dual-backend fetch. Do not remove.
def _fetchone(cursor):
    """Fetch one row as dict or None. Works for both psycopg2 and sqlite3."""
    if USE_POSTGRES:
        columns = [desc[0] for desc in cursor.description] if cursor.description else []
        row = cursor.fetchone()
        if row:
            return dict(zip(columns, row))
        return None
    row = cursor.fetchone()
    return dict(row) if row else None


# CRITICAL SHARED INFRASTRUCTURE - dual-backend commit. Do not remove.
def _commit(conn):
    conn.commit()


# CRITICAL SHARED INFRASTRUCTURE - dual-backend rollback. Do not remove.
def _rollback(conn):
    conn.rollback()


# CRITICAL SHARED INFRASTRUCTURE - dual-backend last row id. Do not remove.
def _lastrowid(cursor) -> int:
    """Get last inserted row ID."""
    if USE_POSTGRES:
        return cursor.fetchone()[0]
    return cursor.lastrowid


# CRITICAL SHARED INFRASTRUCTURE - dual-backend script execution. Do not remove.
def _executescript(conn, script: str):
    """Execute a SQL script. Handles differences between SQLite and PostgreSQL."""
    if USE_POSTGRES:
        statements = [s.strip() for s in script.split(";") if s.strip()]
        cur = conn.cursor()
        for stmt in statements:
            if stmt.upper().startswith("PRAGMA"):
                continue
            stmt = stmt.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
            stmt = stmt.replace("AUTOINCREMENT", "")
            cur.execute(stmt)
        cur.close()
    else:
        conn.executescript(script)


# CRITICAL SHARED INFRASTRUCTURE - decides the backend.
# Returns PostgreSQL when DATABASE_URL is present (Render), else SQLite (desktop).
# Both backends MUST stay. Do not remove either branch.
def get_connection():
    """If DATABASE_URL is set returns PostgreSQL, else SQLite (WAL)."""
    if USE_POSTGRES:
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = False
        return conn
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Create tables and indexes if they don't exist."""
    conn = get_connection()
    try:
        _executescript(conn, """
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
            _execute(conn, "ALTER TABLE records ADD COLUMN remarks TEXT DEFAULT ''")
        except Exception:
            pass
        _commit(conn)
    except Exception as e:
        logging.exception(f"Failed to initialize database: {e}")
        raise
    finally:
        conn.close()


def migrate_dates():
    """Migrate all bid_date values to DD-MM-YYYY format and fix month column."""
    conn = get_connection()
    try:
        cursor = _execute(conn, "SELECT id, bid_date FROM records", return_cursor=True)
        for row in _fetchall(cursor):
            rid = row["id"]
            bid_date = str(row["bid_date"] or "").strip()
            if not bid_date:
                continue
            dt = None
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                try:
                    dt = datetime.strptime(bid_date, fmt)
                    break
                except ValueError:
                    pass
            if dt:
                _execute(conn, "UPDATE records SET bid_date=?,month=? WHERE id=?",
                         (dt.strftime("%d-%m-%Y"), dt.strftime("%B_%Y").upper(), rid))
                continue
            for fmt in ("%d-%m-%Y", "%d/%m/%Y"):
                try:
                    dt = datetime.strptime(bid_date, fmt)
                    nm = dt.strftime("%B_%Y").upper()
                    cur2 = _execute(conn, "SELECT month FROM records WHERE id=?", (rid,), return_cursor=True)
                    row2 = _fetchone(cur2)
                    if row2 and row2["month"] != nm:
                        _execute(conn, "UPDATE records SET bid_date=?,month=? WHERE id=?",
                                 (dt.strftime("%d-%m-%Y"), nm, rid))
                    break
                except ValueError:
                    pass
        _commit(conn)
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
        return _fetchall(_execute(conn, "SELECT * FROM records ORDER BY id DESC", return_cursor=True))
    except Exception:
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
                try:
                    month = datetime.strptime(bid_str, fmt).strftime("%B_%Y").upper()
                    break
                except ValueError:
                    pass
        _execute(conn, "BEGIN")
        cur = _execute(conn,
            "SELECT COALESCE(MAX(sr_no),0)+1 as n FROM records WHERE month=?",
            (month,), return_cursor=True)
        sr_no = _fetchone(cur)["n"]
        insert_sql = ("INSERT INTO records (sr_no,bid_date,invoice_no,name,xcell,product,serial_no,price,emi,di,bid,dp_taken,scheme,actual_product,given_prod_price,phone,alt_phone,month,remarks) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)")
        if USE_POSTGRES:
            insert_sql = insert_sql.rstrip() + " RETURNING id"
        cur = _execute(conn, insert_sql,
            (sr_no, data.get("bid_date",""), invoice_no, data.get("name",""), xcell, data.get("product",""), serial_no,
             _to_float(data.get("price",0)), _to_float(data.get("emi",0)), _to_float(data.get("di",0)), data.get("bid",""),
             _to_float(dp_taken), data.get("scheme",""), product_given, _to_float(given_prod_price), data.get("mobile",""), alt_phone, month, remarks),
            return_cursor=True)
        new_id = _lastrowid(cur)
        _commit(conn)
        invalidate_cache()
        return new_id
    except Exception as e:
        _rollback(conn)
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
                try:
                    month = datetime.strptime(bid_str, fmt).strftime("%B_%Y").upper()
                    break
                except ValueError:
                    pass
        _execute(conn,
            "UPDATE records SET bid_date=?,invoice_no=?,name=?,xcell=?,product=?,serial_no=?,price=?,emi=?,di=?,bid=?,dp_taken=?,scheme=?,actual_product=?,given_prod_price=?,phone=?,alt_phone=?,month=?,remarks=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (data.get("bid_date",""), invoice_no, data.get("name",""), xcell, data.get("product",""), serial_no,
             _to_float(data.get("price",0)), _to_float(data.get("emi",0)), _to_float(data.get("di",0)), data.get("bid",""),
             _to_float(dp_taken), data.get("scheme",""), product_given, _to_float(given_prod_price), data.get("mobile",""), alt_phone, month, remarks, record_id))
        _commit(conn)
        invalidate_cache()
        return True
    except Exception as e:
        raise
    finally:
        conn.close()


def delete_record(record_id):
    conn = get_connection()
    try:
        cur = _execute(conn, "SELECT month FROM records WHERE id=?", (record_id,), return_cursor=True)
        row = _fetchone(cur)
        month = row["month"] if row else ""
        _execute(conn, "DELETE FROM records WHERE id=?", (record_id,))
        _commit(conn)
        if month:
            cur = _execute(conn, "SELECT id FROM records WHERE month=? ORDER BY sr_no ASC, id ASC", (month,), return_cursor=True)
            ids = [r["id"] for r in _fetchall(cur)]
            for i, rid in enumerate(ids, 1):
                _execute(conn, "UPDATE records SET sr_no=? WHERE id=?", (i, rid))
        _commit(conn)
        invalidate_cache()
        return True
    except Exception:
        raise
    finally:
        conn.close()


def get_record_by_id(record_id):
    conn = get_connection()
    try:
        cur = _execute(conn, "SELECT * FROM records WHERE id=?", (record_id,), return_cursor=True)
        return _fetchone(cur)
    except Exception:
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
    """Return months newest-first - always includes the current month so a new
    month's sheet appears automatically even with zero records."""
    conn = get_connection()
    try:
        cur = _execute(conn, "SELECT DISTINCT month FROM records WHERE month!=''", return_cursor=True)
        months = [r["month"] for r in _fetchall(cur)]
    except Exception:
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
        if month:
            mf, p = "WHERE month=?", [month]
        if USE_POSTGRES:
            xcell_sql = "COALESCE(CAST(NULLIF(REPLACE(COALESCE(xcell, '0'), ',', ''), '') AS REAL), 0)"
        else:
            xcell_sql = "COALESCE(CAST(REPLACE(COALESCE(xcell, '0'), ',', '') AS REAL), 0)"
        cur = _execute(conn,
            f"SELECT COUNT(*) as total_records, COALESCE(SUM(dp_taken),0) as total_dp, COALESCE(SUM(di),0) as total_di, SUM({xcell_sql}) as total_xcell FROM records {mf}",
            tuple(p) if p else None, return_cursor=True)
        s = _fetchone(cur) or {}
        cur = _execute(conn, "SELECT month, COUNT(*) as c FROM records GROUP BY month", return_cursor=True)
        s["monthly_counts"] = {r["month"]: r["c"] for r in _fetchall(cur)}
        s["daily_counts"] = {}
        if month:
            cur = _execute(conn,
                "SELECT bid_date, COUNT(*) as c FROM records WHERE month=? AND bid_date!='' GROUP BY bid_date",
                (month,), return_cursor=True)
            s["daily_counts"] = {r["bid_date"]: r["c"] for r in _fetchall(cur)}
        return s
    except Exception:
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
        if month:
            conds.append("month=?")
            params.append(month)
        if name_filter:
            conds.append("name LIKE ?")
            params.append(f"%{name_filter.strip()}%")
        if phone_filter:
            conds.append("(phone LIKE ? OR alt_phone LIKE ?)")
            params.extend([f"%{phone_filter.strip()}%"]*2)
        if date_from:
            try:
                df = datetime.strptime(date_from, "%d-%m-%Y").strftime("%Y-%m-%d")
            except ValueError:
                df = date_from
            conds.append("substr(bid_date,7,4)||'-'||substr(bid_date,4,2)||'-'||substr(bid_date,1,2)>=?")
            params.append(df)
        if date_to:
            try:
                dt2 = datetime.strptime(date_to, "%d-%m-%Y").strftime("%Y-%m-%d")
            except ValueError:
                dt2 = date_to
            conds.append("substr(bid_date,7,4)||'-'||substr(bid_date,4,2)||'-'||substr(bid_date,1,2)<=?")
            params.append(dt2)
        w = " AND ".join(conds) if conds else "1=1"
        if sort_by not in ["id","bid_date","invoice_no","name","price","dp_taken","di","month","created_at"]:
            sort_by = "id"
        ordr = "DESC" if sort_desc else "ASC"
        se = sort_by
        if sort_by == "bid_date":
            se = "substr(bid_date,7,4)||'-'||substr(bid_date,4,2)||'-'||substr(bid_date,1,2)"
        cur = _execute(conn, f"SELECT COUNT(*) as t FROM records WHERE {w}", tuple(params), return_cursor=True)
        total = _fetchone(cur)["t"]
        off = (page-1)*page_size
        cur = _execute(conn,
            f"SELECT * FROM records WHERE {w} ORDER BY {se} {ordr} LIMIT ? OFFSET ?",
            tuple(params) + (page_size, off), return_cursor=True)
        recs = _fetchall(cur)
        return recs, total
    except Exception:
        return [], 0
    finally:
        conn.close()


def _to_float(v):
    if v is None: return 0.0
    try: return float(str(v).replace(",", "").strip())
    except (ValueError, AttributeError): return 0.0


def get_last_invoice():
    conn = get_connection()
    try:
        cur = _execute(conn, "SELECT invoice_no FROM records ORDER BY id DESC LIMIT 1", return_cursor=True)
        row = _fetchone(cur)
        return row["invoice_no"] if row else ""
    except Exception:
        return ""
    finally:
        conn.close()


def check_serial_exists(s):
    if not (s and s.strip()):
        return False
    conn = get_connection()
    try:
        cur = _execute(conn, "SELECT 1 FROM records WHERE LOWER(serial_no)=LOWER(?) LIMIT 1", (s.strip(),), return_cursor=True)
        return _fetchone(cur) is not None
    except Exception:
        return False
    finally:
        conn.close()


def check_invoice_exists(s):
    if not (s and s.strip()):
        return False
    conn = get_connection()
    try:
        cur = _execute(conn, "SELECT 1 FROM records WHERE LOWER(invoice_no)=LOWER(?) LIMIT 1", (s.strip(),), return_cursor=True)
        return _fetchone(cur) is not None
    except Exception:
        return False
    finally:
        conn.close()


def swap_sr_no(id1, id2):
    conn = get_connection()
    try:
        cur = _execute(conn, "SELECT sr_no FROM records WHERE id=?", (id1,), return_cursor=True)
        s1 = _fetchone(cur)
        cur = _execute(conn, "SELECT sr_no FROM records WHERE id=?", (id2,), return_cursor=True)
        s2 = _fetchone(cur)
        if not s1 or not s2:
            return False
        _execute(conn, "UPDATE records SET sr_no=? WHERE id=?", (s2["sr_no"], id1))
        _execute(conn, "UPDATE records SET sr_no=? WHERE id=?", (s1["sr_no"], id2))
        _commit(conn)
        invalidate_cache()
        return True
    except Exception:
        return False
    finally:
        conn.close()


export_all_records = load_all_records

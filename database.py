"""
Dual-mode database module for Gagan's Finance Desk.
Works with SQLite (local dev) and PostgreSQL (Hugging Face cloud).
When DATABASE_URL environment variable is set, uses PostgreSQL.
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

# Ensure data directory exists
os.makedirs(DB_DIR, exist_ok=True)

# Detect if we should use PostgreSQL
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
USE_POSTGRES = bool(DATABASE_URL)

if USE_POSTGRES:
    import psycopg2
    import psycopg2.extras

# Cache for records (invalidated every 30 seconds or on manual refresh)
_cache: Dict[str, Any] = {}
_cache_time: float = 0
_CACHE_TTL = 30  # seconds


def _fix_sql(sql: str) -> str:
    """Convert SQLite placeholders (?) to PostgreSQL placeholders (%s)."""
    if USE_POSTGRES:
        return sql.replace('?', '%s')
    return sql


def _execute(conn, sql: str, params: tuple = None, return_cursor: bool = False):
    """
    Execute SQL with proper parameter style for the database type.
    Returns cursor for PostgreSQL, sqlite3.Cursor for SQLite.
    """
    sql = _fix_sql(sql)
    if USE_POSTGRES:
        # Don't use 'with' context - caller manages cursor lifecycle
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


def _fetchall(cursor):
    """Fetch all rows as list of dicts. Works for both psycopg2 and sqlite3."""
    if USE_POSTGRES:
        columns = [desc[0] for desc in cursor.description] if cursor.description else []
        rows = cursor.fetchall()
        return [dict(zip(columns, row)) for row in rows]
    return [dict(row) for row in cursor.fetchall()]


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


def _commit(conn):
    """Commit transaction."""
    if USE_POSTGRES:
        conn.commit()
    else:
        conn.commit()


def _rollback(conn):
    """Rollback transaction."""
    if USE_POSTGRES:
        conn.rollback()
    else:
        conn.rollback()


def _lastrowid(cursor) -> int:
    """Get last inserted row ID."""
    if USE_POSTGRES:
        return cursor.fetchone()[0]
    return cursor.lastrowid


def _executescript(conn, script: str):
    """Execute a SQL script. Handles differences between SQLite and PostgreSQL."""
    if USE_POSTGRES:
        # Split by semicolons and execute individually
        statements = [s.strip() for s in script.split(';') if s.strip()]
        with conn.cursor() as cur:
            for stmt in statements:
                if stmt.upper().startswith('PRAGMA'):
                    continue  # Skip PRAGMA for PostgreSQL
                # Fix CREATE TABLE for PostgreSQL
                stmt = stmt.replace('INTEGER PRIMARY KEY AUTOINCREMENT', 'SERIAL PRIMARY KEY')
                stmt = stmt.replace('AUTOINCREMENT', '')
                cur.execute(stmt)
    else:
        conn.executescript(script)


def get_connection():
    """Get a database connection.
    
    If DATABASE_URL environment variable is set, returns a PostgreSQL connection.
    Otherwise, returns a SQLite connection with WAL mode.
    """
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
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_records_invoice ON records(invoice_no);
            CREATE INDEX IF NOT EXISTS idx_records_serial ON records(serial_no);
            CREATE INDEX IF NOT EXISTS idx_records_name ON records(name);
            CREATE INDEX IF NOT EXISTS idx_records_phone ON records(phone);
            CREATE INDEX IF NOT EXISTS idx_records_month ON records(month);
            CREATE INDEX IF NOT EXISTS idx_records_bid_date ON records(bid_date);
        """)
        # Add remarks column if missing
        try:
            _execute(conn, "ALTER TABLE records ADD COLUMN remarks TEXT DEFAULT ''")
            logging.info("Added missing column: remarks")
        except Exception:
            pass  # Column already exists
        _commit(conn)
    except Exception as e:
        logging.exception(f"Failed to initialize database: {e}")
        raise
    finally:
        conn.close()


def migrate_dates():
    """
    Migrate all bid_date values to DD-MM-YYYY format.
    Also fix month column based on bid_date.
    Run once at startup.
    """
    conn = get_connection()
    try:
        cursor = _execute(conn, "SELECT id, bid_date FROM records", return_cursor=True)
        rows = _fetchall(cursor)
        updated_count = 0
        for row in rows:
            rid = row["id"]
            bid_date = str(row["bid_date"] or "").strip()
            if not bid_date:
                continue

            dt = None

            # Try YYYY-MM-DD HH:MM:SS format (e.g., 2026-06-01 00:00:00)
            if dt is None:
                try:
                    dt = datetime.strptime(bid_date, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    pass

            # Try YYYY-MM-DD format (e.g., 2026-06-30)
            if dt is None:
                try:
                    dt = datetime.strptime(bid_date, "%Y-%m-%d")
                except ValueError:
                    pass

            if dt is not None:
                new_date = dt.strftime("%d-%m-%Y")
                new_month = dt.strftime("%B_%Y").upper()
                _execute(conn, "UPDATE records SET bid_date = ?, month = ? WHERE id = ?",
                         (new_date, new_month, rid))
                updated_count += 1
                continue

            # Already DD-MM-YYYY or DD/MM/YYYY, just fix month if needed
            for fmt in ("%d-%m-%Y", "%d/%m/%Y"):
                try:
                    dt = datetime.strptime(bid_date, fmt)
                    new_month = dt.strftime("%B_%Y").upper()
                    cur_cursor = _execute(conn, "SELECT month FROM records WHERE id = ?",
                                          (rid,), return_cursor=True)
                    cur_month = _fetchone(cur_cursor)["month"]
                    if cur_month != new_month:
                        _execute(conn, "UPDATE records SET bid_date = ?, month = ? WHERE id = ?",
                                 (dt.strftime("%d-%m-%Y"), new_month, rid))
                        updated_count += 1
                    break
                except ValueError:
                    continue

        _commit(conn)
        if updated_count > 0:
            logging.info(f"Date migration: updated {updated_count} records")
    except Exception as e:
        logging.error(f"Date migration failed: {e}")
        _rollback(conn)
    finally:
        conn.close()


def invalidate_cache():
    """Force refresh of cached records."""
    global _cache, _cache_time
    _cache = {}
    _cache_time = 0


def get_cached_records() -> List[Dict[str, Any]]:
    """Get records with caching. Cache is invalidated every 30 seconds or on manual refresh."""
    global _cache, _cache_time
    current_time = time.time()

    if _cache and current_time - _cache_time < _CACHE_TTL:
        return _cache.get("records", [])

    records = load_all_records()
    _cache = {"records": records}
    _cache_time = current_time
    return records


def load_all_records() -> List[Dict[str, Any]]:
    """Load all records from the database."""
    conn = get_connection()
    try:
        cursor = _execute(conn, "SELECT * FROM records ORDER BY id DESC", return_cursor=True)
        return _fetchall(cursor)
    except Exception as e:
        logging.error(f"Error loading records: {e}")
        return []
    finally:
        conn.close()


def get_record_by_id(record_id: int) -> Optional[Dict[str, Any]]:
    """Get a single record by its ID."""
    conn = get_connection()
    try:
        cursor = _execute(conn, "SELECT * FROM records WHERE id = ?", (record_id,), return_cursor=True)
        return _fetchone(cursor)
    except Exception as e:
        logging.error(f"Error fetching record {record_id}: {e}")
        return None
    finally:
        conn.close()


def add_record(
    invoice_no: str,
    data: Dict[str, Any],
    serial_no: str,
    xcell: str,
    dp_taken: str,
    product_given: str,
    given_prod_price: str,
    alt_phone: str,
    remarks: str = "",
) -> int:
    """Insert a new record into the database. Returns the new record ID."""
    conn = get_connection()
    try:
        # Determine month from bid_date if possible, else use current month
        bid_date_str = str(data.get("bid_date", "")).strip()
        if bid_date_str:
            for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d"):
                try:
                    dt = datetime.strptime(bid_date_str, fmt)
                    month = dt.strftime("%B_%Y").upper()
                    break
                except ValueError:
                    continue
            else:
                month = datetime.now().strftime("%B_%Y").upper()
        else:
            month = datetime.now().strftime("%B_%Y").upper()

        if USE_POSTGRES:
            # PostgreSQL: use BEGIN + RETURNING id
            _execute(conn, "BEGIN")
            cur = _execute(conn, "SELECT COALESCE(MAX(sr_no), 0) + 1 as next_sr FROM records WHERE month = ?",
                          (month,), return_cursor=True)
            sr_no = _fetchone(cur)["next_sr"]

            sql = """INSERT INTO records 
                (sr_no, bid_date, invoice_no, name, xcell, product, serial_no, 
                 price, emi, di, bid, dp_taken, scheme, actual_product, 
                 given_prod_price, phone, alt_phone, month, remarks)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                RETURNING id"""
            params = (
                sr_no, data.get("bid_date", ""), invoice_no, data.get("name", ""),
                xcell, data.get("product", ""), serial_no,
                _to_float(data.get("price", 0)), _to_float(data.get("emi", 0)),
                _to_float(data.get("di", 0)), data.get("bid", ""),
                _to_float(dp_taken), data.get("scheme", ""), product_given,
                _to_float(given_prod_price), data.get("mobile", ""), alt_phone,
                month, remarks,
            )
            cur = _execute(conn, sql, params, return_cursor=True)
            new_id = _lastrowid(cur)
            _commit(conn)
        else:
            # SQLite: use BEGIN IMMEDIATE + lastrowid
            _execute(conn, "BEGIN IMMEDIATE")
            cur = _execute(conn, "SELECT COALESCE(MAX(sr_no), 0) + 1 as next_sr FROM records WHERE month = ?",
                          (month,), return_cursor=True)
            sr_no = _fetchone(cur)["next_sr"]

            sql = """INSERT INTO records 
                (sr_no, bid_date, invoice_no, name, xcell, product, serial_no, 
                 price, emi, di, bid, dp_taken, scheme, actual_product, 
                 given_prod_price, phone, alt_phone, month, remarks)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
            params = (
                sr_no, data.get("bid_date", ""), invoice_no, data.get("name", ""),
                xcell, data.get("product", ""), serial_no,
                _to_float(data.get("price", 0)), _to_float(data.get("emi", 0)),
                _to_float(data.get("di", 0)), data.get("bid", ""),
                _to_float(dp_taken), data.get("scheme", ""), product_given,
                _to_float(given_prod_price), data.get("mobile", ""), alt_phone,
                month, remarks,
            )
            cur = _execute(conn, sql, params)
            new_id = _lastrowid(cur)
            _commit(conn)

        invalidate_cache()
        return new_id
    except Exception as e:
        _rollback(conn)
        logging.exception(f"Error adding record: {e}")
        if "already exists" in str(e).lower() or "duplicate" in str(e).lower():
            raise ValueError(f"Record with this data already exists: {e}")
        raise
    finally:
        conn.close()


def update_record(
    record_id: int,
    invoice_no: str,
    data: Dict[str, Any],
    serial_no: str,
    xcell: str,
    dp_taken: str,
    product_given: str,
    given_prod_price: str,
    alt_phone: str,
    remarks: str = "",
) -> bool:
    """Update an existing record."""
    conn = get_connection()
    try:
        # Recalculate month from bid_date
        bid_date_str = str(data.get("bid_date", "")).strip()
        month = ""
        if bid_date_str:
            for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d"):
                try:
                    dt = datetime.strptime(bid_date_str, fmt)
                    month = dt.strftime("%B_%Y").upper()
                    break
                except ValueError:
                    continue

        sql = """UPDATE records SET
            bid_date = ?, invoice_no = ?, name = ?, xcell = ?, product = ?,
            serial_no = ?, price = ?, emi = ?, di = ?, bid = ?,
            dp_taken = ?, scheme = ?, actual_product = ?,
            given_prod_price = ?, phone = ?, alt_phone = ?, month = ?,
            remarks = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?"""
        params = (
            data.get("bid_date", ""), invoice_no, data.get("name", ""),
            xcell, data.get("product", ""), serial_no,
            _to_float(data.get("price", 0)), _to_float(data.get("emi", 0)),
            _to_float(data.get("di", 0)), data.get("bid", ""),
            _to_float(dp_taken), data.get("scheme", ""), product_given,
            _to_float(given_prod_price), data.get("mobile", ""), alt_phone,
            month, remarks, record_id,
        )
        _execute(conn, sql, params)
        _commit(conn)
        invalidate_cache()
        return True
    except Exception as e:
        _rollback(conn)
        logging.exception(f"Error updating record {record_id}: {e}")
        raise
    finally:
        conn.close()


def delete_record(record_id: int) -> bool:
    """Delete a record by its ID and reindex SR NOs for that month."""
    conn = get_connection()
    try:
        # Get the month of the record being deleted
        cursor = _execute(conn, "SELECT month FROM records WHERE id = ?", (record_id,), return_cursor=True)
        row = _fetchone(cursor)
        month = row["month"] if row else ""

        _execute(conn, "DELETE FROM records WHERE id = ?", (record_id,))
        _commit(conn)

        # Reindex SR NOs for the affected month
        if month:
            reindex_sr_no(conn, month)

        invalidate_cache()
        return True
    except Exception as e:
        _rollback(conn)
        logging.exception(f"Error deleting record {record_id}: {e}")
        raise
    finally:
        conn.close()


def reindex_sr_no(conn, month: str):
    """Reindex SR NOs sequentially for a given month."""
    cursor = _execute(conn,
        "SELECT id FROM records WHERE month = ? ORDER BY sr_no ASC, id ASC",
        (month,), return_cursor=True
    )
    rows = _fetchall(cursor)
    for idx, row in enumerate(rows, start=1):
        _execute(conn, "UPDATE records SET sr_no = ? WHERE id = ?", (idx, row["id"]))


def swap_sr_no(id1: int, id2: int) -> bool:
    """Swap SR NOs between two records."""
    conn = get_connection()
    try:
        cursor = _execute(conn, "SELECT sr_no FROM records WHERE id = ?", (id1,), return_cursor=True)
        sr1 = _fetchone(cursor)
        cursor = _execute(conn, "SELECT sr_no FROM records WHERE id = ?", (id2,), return_cursor=True)
        sr2 = _fetchone(cursor)
        if sr1 and sr2:
            _execute(conn, "UPDATE records SET sr_no = ? WHERE id = ?", (sr2["sr_no"], id1))
            _execute(conn, "UPDATE records SET sr_no = ? WHERE id = ?", (sr1["sr_no"], id2))
            _commit(conn)
            invalidate_cache()
            return True
        return False
    except Exception as e:
        _rollback(conn)
        logging.exception(f"Error swapping SR NO: {e}")
        raise
    finally:
        conn.close()


def check_serial_exists(serial_no: str) -> bool:
    """Check if a serial number already exists in the database."""
    if not serial_no or not serial_no.strip():
        return False
    conn = get_connection()
    try:
        cursor = _execute(conn,
            "SELECT 1 FROM records WHERE LOWER(serial_no) = LOWER(?) LIMIT 1",
            (serial_no.strip(),), return_cursor=True
        )
        return _fetchone(cursor) is not None
    except Exception as e:
        logging.error(f"Error checking serial: {e}")
        return False
    finally:
        conn.close()


def check_invoice_exists(invoice_no: str) -> bool:
    """Check if an invoice number already exists."""
    if not invoice_no or not invoice_no.strip():
        return False
    conn = get_connection()
    try:
        cursor = _execute(conn,
            "SELECT 1 FROM records WHERE LOWER(invoice_no) = LOWER(?) LIMIT 1",
            (invoice_no.strip(),), return_cursor=True
        )
        return _fetchone(cursor) is not None
    except Exception as e:
        logging.error(f"Error checking invoice: {e}")
        return False
    finally:
        conn.close()


def get_last_invoice() -> str:
    """Get the last invoice number."""
    conn = get_connection()
    try:
        cursor = _execute(conn, "SELECT invoice_no FROM records ORDER BY id DESC LIMIT 1", return_cursor=True)
        row = _fetchone(cursor)
        return row["invoice_no"] if row else "No invoice yet"
    except Exception as e:
        logging.error(f"Error getting last invoice: {e}")
        return "No invoice yet"
    finally:
        conn.close()


def get_dashboard_stats(month: str = "") -> Dict[str, Any]:
    """Get aggregated statistics for the dashboard.
    If month is provided, filter by that month."""
    conn = get_connection()
    try:
        month_filter = ""
        params: list = []
        if month:
            month_filter = "WHERE month = ?"
            params = [month]

        if USE_POSTGRES:
            xcell_sql = "COALESCE(CAST(NULLIF(REPLACE(COALESCE(xcell, '0'), ',', ''), '') AS REAL), 0)"
        else:
            xcell_sql = "COALESCE(CAST(REPLACE(COALESCE(xcell, '0'), ',', '') AS REAL), 0)"
        cursor = _execute(conn, f"""
            SELECT 
                COUNT(*) as total_records,
                COALESCE(SUM(dp_taken), 0) as total_dp,
                COALESCE(SUM(di), 0) as total_di,
                SUM({xcell_sql}) as total_xcell
            FROM records {month_filter}
        """, tuple(params) if params else None, return_cursor=True)
        stats = _fetchone(cursor) or {}

        # Monthly counts (always all months for the overview)
        cursor = _execute(conn, """
            SELECT month, COUNT(*) as count 
            FROM records 
            GROUP BY month 
            ORDER BY month
        """, return_cursor=True)
        monthly_rows = _fetchall(cursor)
        stats["monthly_counts"] = {r["month"]: r["count"] for r in monthly_rows}

        # Daily counts - if month specified, get daily breakdown
        stats["daily_counts"] = {}
        if month:
            cursor = _execute(conn, """
                SELECT bid_date, COUNT(*) as count 
                FROM records 
                WHERE month = ? AND bid_date != ''
                GROUP BY bid_date 
                ORDER BY substr(bid_date, 7, 4) || '-' || substr(bid_date, 4, 2) || '-' || substr(bid_date, 1, 2)
            """, (month,), return_cursor=True)
            daily_rows = _fetchall(cursor)
            stats["daily_counts"] = {r["bid_date"]: r["count"] for r in daily_rows}

        return stats
    except Exception as e:
        logging.error(f"Error getting dashboard stats: {e}")
        return {"total_records": 0, "total_dp": 0, "total_di": 0, "monthly_counts": {}, "daily_counts": {}}
    finally:
        conn.close()


def get_available_months() -> List[str]:
    """Get list of all months that have records."""
    conn = get_connection()
    try:
        cursor = _execute(conn, """
            SELECT DISTINCT month FROM records 
            WHERE month != '' 
            ORDER BY month DESC
        """, return_cursor=True)
        return [r["month"] for r in _fetchall(cursor)]
    except Exception as e:
        logging.error(f"Error getting months: {e}")
        return []
    finally:
        conn.close()


def search_records(
    query: str = "",
    name_filter: str = "",
    phone_filter: str = "",
    date_from: str = "",
    date_to: str = "",
    sort_by: str = "id",
    sort_desc: bool = True,
    page: int = 1,
    page_size: int = 50,
) -> Tuple[List[Dict[str, Any]], int]:
    """
    Search records with filters, sorting, and pagination.
    Returns (records, total_count).
    """
    conn = get_connection()
    try:
        conditions = []
        params: list = []

        # Global search
        if query:
            q = f"%{query.strip()}%"
            conditions.append("""
                (name LIKE ? OR phone LIKE ? OR invoice_no LIKE ? 
                 OR bid LIKE ? OR product LIKE ? OR serial_no LIKE ?)
            """)
            params.extend([q, q, q, q, q, q])

        # Field-specific filters
        if name_filter:
            conditions.append("name LIKE ?")
            params.append(f"%{name_filter.strip()}%")

        if phone_filter:
            conditions.append("(phone LIKE ? OR alt_phone LIKE ?)")
            params.append(f"%{phone_filter.strip()}%")
            params.append(f"%{phone_filter.strip()}%")

        if date_from:
            try:
                dt_from = datetime.strptime(date_from, "%d-%m-%Y")
                date_from_iso = dt_from.strftime("%Y-%m-%d")
            except ValueError:
                date_from_iso = date_from
            conditions.append("substr(bid_date, 7, 4) || '-' || substr(bid_date, 4, 2) || '-' || substr(bid_date, 1, 2) >= ?")
            params.append(date_from_iso)

        if date_to:
            try:
                dt_to = datetime.strptime(date_to, "%d-%m-%Y")
                date_to_iso = dt_to.strftime("%Y-%m-%d")
            except ValueError:
                date_to_iso = date_to
            conditions.append("substr(bid_date, 7, 4) || '-' || substr(bid_date, 4, 2) || '-' || substr(bid_date, 1, 2) <= ?")
            params.append(date_to_iso)

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        # Validate sort column to prevent SQL injection
        allowed_sorts = ["id", "bid_date", "invoice_no", "name", "price", "dp_taken", "di", "month", "created_at"]
        if sort_by not in allowed_sorts:
            sort_by = "id"
        order = "DESC" if sort_desc else "ASC"

        # When sorting by bid_date, use SQL expression to convert DD-MM-YYYY to YYYY-MM-DD
        sort_expression = sort_by
        if sort_by == "bid_date":
            sort_expression = "substr(bid_date, 7, 4) || '-' || substr(bid_date, 4, 2) || '-' || substr(bid_date, 1, 2)"

        # Get total count
        count_cursor = _execute(conn,
            f"SELECT COUNT(*) as total FROM records WHERE {where_clause}",
            tuple(params), return_cursor=True
        )
        total_count = _fetchone(count_cursor)["total"]

        # Get paginated results
        offset = (page - 1) * page_size
        cursor = _execute(conn,
            f"SELECT * FROM records WHERE {where_clause} ORDER BY {sort_expression} {order} LIMIT ? OFFSET ?",
            tuple(params) + (page_size, offset), return_cursor=True
        )
        records = _fetchall(cursor)

        return records, total_count
    except Exception as e:
        logging.error(f"Error searching records: {e}")
        return [], 0
    finally:
        conn.close()


def export_all_records() -> List[Dict[str, Any]]:
    """Get all records for Excel export."""
    return load_all_records()


def _get_next_sr_no(conn, month: str) -> int:
    """Get the next serial number for a given month."""
    cursor = _execute(conn,
        "SELECT COALESCE(MAX(sr_no), 0) + 1 as next_sr FROM records WHERE month = ?",
        (month,), return_cursor=True
    )
    return _fetchone(cursor)["next_sr"]


def _to_float(value) -> float:
    """Safely convert a value to float."""
    if value is None:
        return 0.0
    try:
        return float(str(value).replace(",", "").strip())
    except (ValueError, AttributeError):
        return 0.0


# Run date migration at startup
migrate_dates()

# Initialize the database when this module is imported
init_db()
# -*- coding: utf-8 -*-
# Phase-1 migration tests — isolated temporary databases only. Never touches real data.
# Run standalone:  python tests/test_sync_schema_migration.py
# Run with pytest: pytest tests/test_sync_schema_migration.py
import os
import sqlite3
import sys
import tempfile

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT not in sys.path:
    sys.path.insert(0, PROJECT)

import sync_schema


def _make_old_schema_db(path):
    """Create a temp DB replicating the CURRENT production `records` schema exactly
    (remarks added last, matching how the live table was built), plus 2 sample rows."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sr_no INTEGER, bid_date TEXT, invoice_no TEXT, name TEXT,
            xcell TEXT, product TEXT, serial_no TEXT, price REAL DEFAULT 0,
            emi REAL DEFAULT 0, di REAL DEFAULT 0, bid TEXT,
            dp_taken REAL DEFAULT 0, scheme TEXT, actual_product TEXT,
            given_prod_price REAL DEFAULT 0, phone TEXT, alt_phone TEXT,
            month TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        ALTER TABLE records ADD COLUMN remarks TEXT DEFAULT '';
        CREATE INDEX idx_records_invoice ON records(invoice_no);
        CREATE INDEX idx_records_serial ON records(serial_no);
        CREATE INDEX idx_records_name ON records(name);
        CREATE INDEX idx_records_phone ON records(phone);
        CREATE INDEX idx_records_month ON records(month);
        CREATE INDEX idx_records_bid_date ON records(bid_date);
        """
    )
    conn.executemany(
        "INSERT INTO records (sr_no,bid_date,invoice_no,name,product,serial_no,price,month,remarks)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        [
            (1, "15-08-2026", "2608001", "Alpha", "P1", "SER001", 1000.5, "AUGUST_2026", "note1"),
            (2, "16-08-2026", "2608002", "Beta", "P2", "SER002", 2000.0, "AUGUST_2026", "note2"),
        ],
    )
    conn.commit()
    conn.close()


def _business_rows(conn):
    cols = ["id", "sr_no", "bid_date", "invoice_no", "name", "product", "serial_no",
            "price", "month", "remarks"]
    return conn.execute("SELECT %s FROM records ORDER BY id" % ",".join(cols)).fetchall()


def test_old_schema_to_new():
    tmp = tempfile.mktemp(suffix=".db")
    _make_old_schema_db(tmp)
    conn = sqlite3.connect(tmp)
    before = _business_rows(conn)
    result = sync_schema.migrate_sync_schema(conn, False)
    conn.commit()
    assert set(result["columns_added"]) == {
        "sync_id", "server_rev", "row_rev", "base_json", "deleted_at"
    }
    assert set(result["tables_created"]) == {
        "outbox", "applied_ops", "conflicts", "sync_state", "sync_sequence"
    }
    # New columns exist with correct defaults.
    assert conn.execute("PRAGMA table_info(records)").fetchall()
    row = conn.execute(
        "SELECT sync_id, server_rev, row_rev, base_json, deleted_at FROM records ORDER BY id"
    ).fetchone()
    assert row[0] is None and row[3] is None and row[4] is None
    assert row[1] == 0 and row[2] == 0
    # Seeds present.
    assert conn.execute("SELECT COUNT(*) FROM sync_state").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM sync_sequence").fetchone()[0] == 1
    # Business values unchanged.
    assert _business_rows(conn) == before
    # Unique index on sync_id exists.
    uniq = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name='idx_records_sync_id'"
    ).fetchone()[0]
    assert uniq == 1
    conn.close()
    os.remove(tmp)


def test_migration_is_idempotent():
    tmp = tempfile.mktemp(suffix=".db")
    _make_old_schema_db(tmp)
    conn = sqlite3.connect(tmp)
    sync_schema.migrate_sync_schema(conn, False)
    conn.commit()
    d1 = sync_schema.describe_sync_schema(conn, False)
    before = _business_rows(conn)
    # Second run must be a clean no-op.
    r2 = sync_schema.migrate_sync_schema(conn, False)
    conn.commit()
    d2 = sync_schema.describe_sync_schema(conn, False)
    assert r2["columns_added"] == [] and r2["tables_created"] == []
    assert d1 == d2
    assert _business_rows(conn) == before
    assert conn.execute("SELECT COUNT(*) FROM sync_state").fetchone()[0] == 1
    conn.close()
    os.remove(tmp)


def test_pg_ddl_translation():
    sql = sync_schema.translate_ddl("CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, x TEXT)", True)
    assert "SERIAL PRIMARY KEY" in sql and "AUTOINCREMENT" not in sql
    sql2 = sync_schema.translate_ddl("CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, x TEXT)", False)
    assert "INTEGER PRIMARY KEY AUTOINCREMENT" in sql2
    # Every table DDL translates cleanly (no leftover AUTOINCREMENT for PG).
    for ddl in sync_schema.TABLE_DDL.values():
        t = sync_schema.translate_ddl(ddl, True)
        assert "AUTOINCREMENT" not in t and "SERIAL PRIMARY KEY" in t


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print("PASS", fn.__name__)
        except Exception as e:
            failed += 1
            import traceback
            traceback.print_exc()
    print("\n%s" % ("ALL SYNC SCHEMA MIGRATION TESTS PASSED" if failed == 0 else "%d FAILED" % failed))
    sys.exit(1 if failed else 0)

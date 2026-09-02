"""
tests/syncv2_helpers.py - isolated SQLite twin-DB fixtures for syncv2 tests.

Never touches production databases. A "server" here is a second SQLite database
running the exact same coordinator code paths that PostgreSQL uses (is_pg=False),
consistent with the project's existing translation/compat test approach.
"""
import json
import os
import sqlite3
import sys
import tempfile

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT not in sys.path:
    sys.path.insert(0, PROJECT)
sys.path.insert(0, os.path.join(PROJECT, "scripts", "sync"))
sys.path.insert(0, os.path.join(PROJECT, "syncv2"))

import sync_schema  # noqa: E402
from syncv2 import protocol as P  # noqa: E402
from syncv2 import merge as M  # noqa: E402
from syncv2 import store as S  # noqa: E402

RECORDS_DDL = """
CREATE TABLE records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sr_no INTEGER, bid_date TEXT, invoice_no TEXT, name TEXT,
    xcell TEXT, product TEXT, serial_no TEXT, price REAL DEFAULT 0,
    emi REAL DEFAULT 0, di REAL DEFAULT 0, bid TEXT,
    dp_taken REAL DEFAULT 0, scheme TEXT, actual_product TEXT,
    given_prod_price REAL DEFAULT 0, phone TEXT, alt_phone TEXT,
    month TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, remarks TEXT DEFAULT ''
)"""


def make_db(path):
    """Create a fresh sync-schema SQLite database at path."""
    conn = sqlite3.connect(path)
    conn.execute(RECORDS_DDL)
    sync_schema.migrate_sync_schema(conn, False)
    conn.commit()
    return conn


def base_business(**kw):
    row = {f: None for f in P.BUSINESS_FIELDS}
    row.update({
        "sr_no": 1, "bid_date": "01-08-2026", "invoice_no": "", "name": "AA",
        "product": "", "serial_no": "", "price": 1000, "emi": 0, "di": 0,
        "bid": "B1", "dp_taken": 0, "scheme": "", "actual_product": "",
        "given_prod_price": 0, "phone": "", "alt_phone": "", "month": "AUGUST_2026",
        "remarks": "",
    })
    row.update(kw)
    row["month"] = M.month_from_bid_date(row.get("bid_date")) or row.get("month")
    return row


def insert_row(conn, sync_id, business, deleted_at=None, server_rev=0, row_rev=0,
               base_json=None):
    q = S.ph(False)
    b = {f: business.get(f) for f in P.BUSINESS_FIELDS}
    bj = base_json or json.dumps(b, sort_keys=True, default=str,
                                 ensure_ascii=True, separators=(",", ":"))
    cols = sorted(b) + ["sync_id", "deleted_at", "base_json", "server_rev",
                        "row_rev", "created_at", "updated_at"]
    marks = ",".join([q] * len(cols))
    params = [b[f] for f in sorted(b)]
    params += [sync_id, deleted_at, bj, server_rev, row_rev, S.now_utc(), S.now_utc()]
    conn.execute("INSERT INTO records (%s) VALUES (%s)" % (",".join(cols), marks),
                 params)
    conn.commit()


def make_baselined_pair(off_conn, srv_conn, sync_id, business):
    """Insert an identical, mutually-baselined record on both DBs (Phase-3 style)."""
    bj = json.dumps({f: business.get(f) for f in P.BUSINESS_FIELDS}, sort_keys=True,
                    default=str, ensure_ascii=True, separators=(",", ":"))
    insert_row(off_conn, sync_id, business, base_json=bj)
    insert_row(srv_conn, sync_id, business, base_json=bj)


class ServerAdapter:
    """Small adapter making coordinator functions look like a remote API.

    `faults` lets tests inject failures at defined call numbers.
    """

    def __init__(self, conn, faults=None):
        self.conn = conn
        self.is_pg = False
        self._faults = faults or {}
        self._calls = {"apply_ops": 0, "pull": 0, "resolve_conflict": 0}

    def _maybe_fault(self, key, exc_cls=OSError):
        self._calls[key] += 1
        fault = self._faults.get(key)
        if fault is None:
            return
        when, message = fault if isinstance(fault, tuple) else (fault, str(fault))
        if isinstance(when, int) and self._calls[key] == when:
            raise exc_cls(message or "injected network failure")

    def pull(self, since_rev):
        self._maybe_fault("pull")
        return SVC.pull_changes(self.conn, self.is_pg, since_rev)

    def apply_ops(self, ops):
        self._maybe_fault("apply_ops")
        return SVC.apply_ops(self.conn, self.is_pg, ops)

    def resolve_conflict(self, conflict_id, choice, resolution_payload=None):
        self._maybe_fault("resolve_conflict")
        return SVC.resolve_conflict(self.conn, self.is_pg, conflict_id, choice,
                                    resolution_payload)

    def open_conflict_count(self):
        return S.fetch_all(self.conn, False,
                           "SELECT COUNT(*) FROM conflicts WHERE status='open'")[0][0]

    def row(self, sync_id):
        return S.read_row_full(self.conn, False, sync_id)

    def open_blocking_conflict(self, sync_id):
        return SVC.has_open_conflict(self.conn, False, sync_id)

    def list_conflicts(self):
        return SVC.list_open_conflicts(self.conn, False)


import importlib  # noqa: E402
import syncv2.server as SVC  # noqa: E402  (imported after helper funcs for clarity)

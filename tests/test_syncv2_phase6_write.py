# -*- coding: utf-8 -*-
"""Phase 6 - Finance Desk write-path integration tests (Sync V2 transactional
outbox). Isolated temporary SQLite databases ONLY - never touches production,
never connects to Neon, never runs a network call.

Run standalone:  python tests/test_syncv2_phase6_write.py
Run with pytest: pytest tests/test_syncv2_phase6_write.py
"""
import json
import os
import sqlite3
import sys
import tempfile
import uuid

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT not in sys.path:
    sys.path.insert(0, PROJECT)
sys.path.insert(0, os.path.join(PROJECT, "tests"))

os.environ.pop("DATABASE_URL", None)
os.environ.pop("NEON_URL", None)

_TMP = tempfile.mkdtemp(prefix="gfd_p6_write_")
os.environ["FINANCE_DB_PATH"] = os.path.join(_TMP, "finance.db")

import config  # noqa: E402
config.EXCEL_FILE = os.path.join(_TMP, "ALL_RECORDS.xlsx")

import database as db  # noqa: E402
import sync_schema  # noqa: E402
import sync_write  # noqa: E402
from syncv2 import protocol as P  # noqa: E402
from syncv2 import store as S  # noqa: E402
from syncv2 import merge as M  # noqa: E402
from syncv2.engine import SyncEngine  # noqa: E402
import syncv2_helpers as H  # noqa: E402

# Global scratch counter for unique temp DB paths per test.
_counter = {"n": 0}


def _biz(**kw):
    row = {f: None for f in P.BUSINESS_FIELDS}
    row.update({
        "sr_no": 1, "bid_date": "05-08-2026", "invoice_no": "INV-1",
        "name": "ORIG", "xcell": "", "product": "PROD-A", "serial_no": "SER-1",
        "price": 1000.0, "emi": 0.0, "di": 0.0, "bid": "B1", "dp_taken": 0.0,
        "scheme": "", "actual_product": "", "given_prod_price": 0.0,
        "phone": "111", "alt_phone": "", "month": "AUGUST_2026", "remarks": "",
    })
    row.update(kw)
    return row


def _seed_baselined(conn, sync_id, business, server_rev=0, row_rev=0):
    """Raw Phase-3-style insert: business + base_json = same snapshot (no ops)."""
    b = {f: business.get(f) for f in P.BUSINESS_FIELDS}
    bj = json.dumps(b, sort_keys=True, default=str, ensure_ascii=True,
                    separators=(",", ":"))
    cols = sorted(b) + ["sync_id", "deleted_at", "base_json", "server_rev",
                        "row_rev", "created_at", "updated_at"]
    marks = ",".join("?" * len(cols))
    params = [b[f] for f in sorted(b)]
    params += [sync_id, None, bj, server_rev, row_rev, S.now_utc(), S.now_utc()]
    conn.execute("INSERT INTO records (%s) VALUES (%s)" % (",".join(cols), marks),
                 params)
    conn.commit()


def _seed_plain(conn, business, sync_id=None):
    """Insert a plain baselined row (no outbox ops). Returns the local row id."""
    b = {f: business.get(f) for f in P.BUSINESS_FIELDS}
    bj = json.dumps(b, sort_keys=True, default=str, ensure_ascii=True,
                    separators=(",", ":"))
    cols = sorted(b) + ["sync_id", "deleted_at", "base_json", "server_rev",
                        "row_rev", "created_at", "updated_at"]
    marks = ",".join("?" * len(cols))
    sid = sync_id or str(uuid.uuid4())
    params = [b[f] for f in sorted(b)]
    params += [sid, None, bj, 0, 0, S.now_utc(), S.now_utc()]
    conn.execute("INSERT INTO records (%s) VALUES (%s)" % (",".join(cols), marks),
                 params)
    conn.commit()
    cur = conn.execute("SELECT id FROM records WHERE sync_id=?", (sid,))
    rid = cur.fetchone()[0]
    return rid


def _q(sql, params=()):
    conn = sqlite3.connect(db.DB_FILE)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return rows


def _q1(sql, params=()):
    rows = _q(sql, params)
    return rows[0] if rows else None


def _make_app_db():
    """Fresh temp SQLite app DB with the Phase-1 sync schema. Returns its path
    and points database.DB_FILE at it so db.* functions target this file."""
    _counter["n"] += 1
    path = os.path.join(_TMP, "app_%d.db" % _counter["n"])
    if os.path.exists(path):
        os.remove(path)
    db.DB_FILE = path
    db._db_initialized = True      # skip lazy auto-init (we init explicitly)
    db.init_db()
    conn = sqlite3.connect(path)
    sync_schema.migrate_sync_schema(conn, False)
    conn.commit()
    conn.close()
    return path


def _outbox_rows(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT id, op_id, sync_id, op_type, payload_json, base_rev, status "
        "FROM outbox ORDER BY id").fetchall()]
    conn.close()
    for r in rows:
        r["payload"] = json.loads(r.pop("payload_json"))
    return rows

# ---------------------------------------------------------------------------
# CREATE
# ---------------------------------------------------------------------------
def test_add_record_assigns_sync_id_and_atomic_outbox_op():
    path = _make_app_db()
    rid = db.add_record(
        invoice_no="2608001", serial_no="SER001", xcell="X1", dp_taken="500",
        product_given="PROD-A", given_prod_price="950", alt_phone="222",
        remarks="note",
        data={"name": "AAA", "product": "PROD-A", "price": "1000",
              "mobile": "111", "bid": "BID-1", "bid_date": "05-08-2026",
              "emi": "100", "di": "700", "scheme": "12/2"},
    )
    row = _q1("SELECT * FROM records WHERE id=?", (rid,))
    # stable identity + business fields
    assert row["sync_id"] and str(uuid.UUID(row["sync_id"])) == row["sync_id"]
    assert row["sr_no"] == 1
    assert row["month"] == "AUGUST_2026"
    assert row["name"] == "AAA" and row["price"] == 1000.0
    assert row["row_rev"] == 1 and row["server_rev"] == 0
    assert row["deleted_at"] is None and row["base_json"] is None
    # exactly one durable outbox op = the create
    ops = _outbox_rows(path)
    assert len(ops) == 1
    op = ops[0]
    assert op["sync_id"] == row["sync_id"]
    assert op["op_type"] == P.OP_UPSERT and op["status"] == P.OUTBOX_PENDING
    assert op["payload"]["base_rev"] == 0
    assert op["payload"]["local_row_rev"] == 1
    assert op["payload"]["payload"]["name"] == "AAA"
    assert op["payload"]["payload"]["price"] == 1000.0
    assert op["payload"]["payload"]["sr_no"] == 1
    # BASE empty (nothing known yet) - never a fake later snapshot
    assert all(v is None for v in op["payload"]["base"].values())


def test_add_record_second_row_sr_contiguous_and_no_duplicate_identity():
    path = _make_app_db()
    rid1 = db.add_record(invoice_no="2608001", serial_no="SER001", xcell="",
                         dp_taken="0", product_given="", given_prod_price="0",
                         alt_phone="", remarks="",
                         data={"name": "AAA", "product": "P", "price": "100",
                               "bid_date": "05-08-2026"})
    rid2 = db.add_record(invoice_no="2608002", serial_no="SER002", xcell="",
                         dp_taken="0", product_given="", given_prod_price="0",
                         alt_phone="", remarks="",
                         data={"name": "BBB", "product": "P", "price": "200",
                               "bid_date": "05-08-2026"})
    r1 = _q1("SELECT * FROM records WHERE id=?", (rid1,))
    r2 = _q1("SELECT * FROM records WHERE id=?", (rid2,))
    assert r1["sr_no"] == 1 and r2["sr_no"] == 2
    assert r1["sync_id"] != r2["sync_id"]
    ops = _outbox_rows(path)
    assert len(ops) == 2
    assert {o["sync_id"] for o in ops} == {r1["sync_id"], r2["sync_id"]}


def test_add_record_create_failure_rolls_back_business_row():
    path = _make_app_db()
    orig = S.execute

    def boom(conn, is_pg, sql, params=()):
        if "INSERT INTO outbox" in sql:
            raise RuntimeError("injected: outbox insert failed")
        return orig(conn, is_pg, sql, params)

    S.execute = boom
    try:
        try:
            db.add_record(invoice_no="2608003", serial_no="SER003", xcell="",
                          dp_taken="0", product_given="", given_prod_price="0",
                          alt_phone="", remarks="",
                          data={"name": "CCC", "product": "P", "price": "300",
                                "bid_date": "05-08-2026"})
            raise AssertionError("add_record should have raised")
        except RuntimeError as e:
            assert "injected" in str(e)
    finally:
        S.execute = orig
    assert _q("SELECT * FROM records WHERE invoice_no='2608003'") == []
    assert _outbox_rows(path) == []
    assert _q1("SELECT COUNT(*) AS c FROM records")["c"] == 0


# ---------------------------------------------------------------------------
# UPDATE (base preservation, row_rev, coalescing)
# ---------------------------------------------------------------------------
def test_update_record_keeps_base_and_writes_payload():
    path = _make_app_db()
    conn = sqlite3.connect(path)
    base = _biz(name="ORIG", price=1000.0, phone="111", bid_date="05-08-2026")
    _seed_baselined(conn, "s1", base, server_rev=5)
    conn.close()
    rid = _q1("SELECT id FROM records WHERE sync_id='s1'")["id"]

    db.update_record(
        record_id=rid, invoice_no="INV-NEW", serial_no="SER-9", xcell="X9",
        dp_taken="600", product_given="PROD-NEW", given_prod_price="700",
        alt_phone="999", remarks="hello",
        data={"name": "CHANGED", "product": "PROD-NEW", "price": "1500",
              "mobile": "888", "bid": "BID-NEW", "bid_date": "05-08-2026",
              "emi": "150", "di": "1200", "scheme": "18/6"},
    )
    row = _q1("SELECT * FROM records WHERE sync_id='s1'")
    assert row["name"] == "CHANGED" and row["price"] == 1500.0
    # base_json (the BASE) is untouched; server_rev untouched; only row_rev+1
    assert row["row_rev"] == 1 and row["server_rev"] == 5
    base_stored = json.loads(row["base_json"])
    assert base_stored["name"] == "ORIG" and base_stored["price"] == 1000.0

    ops = _outbox_rows(path)
    assert len(ops) == 1 and ops[0]["op_type"] == P.OP_UPSERT
    op = ops[0]["payload"]
    assert op["base_rev"] == 5
    assert op["base"]["name"] == "ORIG" and op["base"]["price"] == 1000.0
    # payload = the FULL resulting business snapshot
    assert op["payload"]["name"] == "CHANGED"
    assert op["payload"]["price"] == 1500.0
    assert op["payload"]["serial_no"] == "SER-9"
    assert op["payload"]["remarks"] == "hello"
    assert op["local_row_rev"] == 1


def test_multiple_updates_coalesce_into_one_op_preserving_oldest_base():
    path = _make_app_db()
    conn = sqlite3.connect(path)
    base = _biz(name="ORIG", price=1000.0, bid_date="05-08-2026")
    _seed_baselined(conn, "s1", base, server_rev=7)
    conn.close()
    rid = _q1("SELECT id FROM records WHERE sync_id='s1'")["id"]

    db.update_record(record_id=rid, invoice_no="INV-1", serial_no="SER-1",
                     xcell="", dp_taken="0", product_given="",
                     given_prod_price="0", alt_phone="", remarks="edit A",
                     data={"name": "EDIT-A", "product": "P", "price": "1100",
                           "mobile": "1", "bid_date": "05-08-2026"})
    db.update_record(record_id=rid, invoice_no="INV-1", serial_no="SER-1",
                     xcell="", dp_taken="0", product_given="",
                     given_prod_price="0", alt_phone="", remarks="edit C",
                     data={"name": "EDIT-C", "product": "P", "price": "1300",
                           "mobile": "1", "bid_date": "05-08-2026"})

    row = _q1("SELECT * FROM records WHERE sync_id='s1'")
    assert row["name"] == "EDIT-C" and row["price"] == 1300.0
    assert row["row_rev"] == 2 and row["server_rev"] == 7
    assert json.loads(row["base_json"])["name"] == "ORIG"

    ops = _outbox_rows(path)
    # coalesced: ONE pending upsert; earlier op superseded
    assert len(ops) == 2
    assert sorted(o["status"] for o in ops) == sorted(
        [P.OUTBOX_SUPERSEDED, P.OUTBOX_PENDING])
    survivor = [o for o in ops if o["status"] == P.OUTBOX_PENDING][0]["payload"]
    assert survivor["payload"]["name"] == "EDIT-C"
    assert survivor["payload"]["price"] == 1300.0
    assert survivor["payload"]["remarks"] == "edit C"
    # survivor keeps the OLDEST required base ancestor, never the new snapshot
    assert survivor["base"]["name"] == "ORIG"
    assert survivor["base"]["price"] == 1000.0
    assert survivor["base_rev"] == 7


# ---------------------------------------------------------------------------
# DELETE / TOMBSTONE
# ---------------------------------------------------------------------------
def test_delete_tombstones_record_hides_it_and_renumbers_atomically():
    path = _make_app_db()
    conn = sqlite3.connect(path)
    rid1 = _seed_plain(conn, _biz(sr_no=1, invoice_no="INV-1", name="ONE",
                                  serial_no="S1", bid_date="05-08-2026"),
                       sync_id="sA")
    rid2 = _seed_plain(conn, _biz(sr_no=2, invoice_no="INV-2", name="TWO",
                                  serial_no="S2", bid_date="05-08-2026"),
                       sync_id="sB")
    rid3 = _seed_plain(conn, _biz(sr_no=3, invoice_no="INV-3", name="THREE",
                                  serial_no="S3", bid_date="05-08-2026"),
                       sync_id="sC")
    conn.close()

    db.delete_record(rid2)

    tomb = _q1("SELECT * FROM records WHERE id=?", (rid2,))
    assert tomb["deleted_at"] is not None          # tombstone, no physical purge
    assert tomb["sync_id"] == "sB"                 # identity preserved
    assert tomb["name"] == "TWO" and tomb["sr_no"] == 2
    assert tomb["row_rev"] == 1                    # 0 -> 1 (delete bump)

    # normal business views hide it
    assert _q1("SELECT COUNT(*) AS c FROM records WHERE deleted_at IS NULL")["c"] == 2
    assert len(_q("SELECT * FROM records")) == 3   # row physically retained
    recs, total = db.search_records(month="AUGUST_2026")
    assert total == 2 and all(r["id"] != rid2 for r in recs)
    assert db.count_records() == 2
    assert all(r["id"] != rid2 for r in db.load_all_records())
    # old-sync/UI duplicate checks must behave as if the record was physically
    # deleted (invoice/serial reusable, invoice suggestion ignores the tombstone)
    assert db.check_invoice_exists("INV-2") is False
    assert db.check_serial_exists("S2") is False
    assert "AUGUST_2026" in db.get_available_months()

    # remaining live rows are contiguous (renumber in the SAME transaction)
    r1 = _q1("SELECT * FROM records WHERE id=?", (rid1,))
    r3 = _q1("SELECT * FROM records WHERE id=?", (rid3,))
    assert r1["sr_no"] == 1 and r3["sr_no"] == 2

    ops = _outbox_rows(path)
    # one delete op for the tombstone + one upsert op for the renumbered row
    dels = [o for o in ops if o["op_type"] == P.OP_DELETE]
    ups = [o for o in ops if o["op_type"] == P.OP_UPSERT]
    assert len(dels) == 1 and dels[0]["sync_id"] == "sB"
    assert dels[0]["payload"]["payload"]["deleted_at"] is not None
    assert dels[0]["payload"]["base_rev"] == 0
    assert any(o["sync_id"] == "sC" and o["payload"]["payload"]["sr_no"] == 2
               for o in ups)
    # row sA (unchanged) has no pending op
    assert not any(o["sync_id"] == "sA" and o["status"] == P.OUTBOX_PENDING
                   for o in ups)


def test_delete_renumber_failure_rolls_back_tombstone_and_reorder():
    path = _make_app_db()
    conn = sqlite3.connect(path)
    rid1 = _seed_plain(conn, _biz(sr_no=1, invoice_no="INV-1", name="ONE",
                                  serial_no="S1"), sync_id="sA")
    rid2 = _seed_plain(conn, _biz(sr_no=2, invoice_no="INV-2", name="TWO",
                                  serial_no="S2"), sync_id="sB")
    rid3 = _seed_plain(conn, _biz(sr_no=3, invoice_no="INV-3", name="THREE",
                                  serial_no="S3"), sync_id="sC")
    conn.close()

    orig = S.execute
    calls = {"n": 0}

    def boom(conn_, is_pg, sql, params=()):
        if "INSERT INTO outbox" in sql:
            calls["n"] += 1
            if calls["n"] == 2:      # delete op ok; first renumber op fails
                raise RuntimeError("injected: renumber outbox failed")
        return orig(conn_, is_pg, sql, params)

    S.execute = boom
    try:
        try:
            db.delete_record(rid2)
            raise AssertionError("delete_record should have raised")
        except RuntimeError as e:
            assert "injected" in str(e)
    finally:
        S.execute = orig

    # everything rolled back: no tombstone, no reorder, no outbox ops
    for rid in (rid1, rid2, rid3):
        row = _q1("SELECT * FROM records WHERE id=?", (rid,))
        assert row["deleted_at"] is None
    assert _q1("SELECT sr_no FROM records WHERE id=?", (rid2,))["sr_no"] == 2
    assert _q1("SELECT sr_no FROM records WHERE id=?", (rid3,))["sr_no"] == 3
    assert _outbox_rows(path) == []


# ---------------------------------------------------------------------------
# SR SWAP / REORDER
# ---------------------------------------------------------------------------
def test_swap_sr_no_atomic_with_outbox_and_no_identity_change():
    path = _make_app_db()
    conn = sqlite3.connect(path)
    rid1 = _seed_plain(conn, _biz(sr_no=1, name="ONE", serial_no="S1"),
                       sync_id="sA")
    rid2 = _seed_plain(conn, _biz(sr_no=2, name="TWO", serial_no="S2"),
                       sync_id="sB")
    conn.close()

    assert db.swap_sr_no(rid1, rid2) is True

    r1 = _q1("SELECT * FROM records WHERE id=?", (rid1,))
    r2 = _q1("SELECT * FROM records WHERE id=?", (rid2,))
    assert r1["sr_no"] == 2 and r2["sr_no"] == 1       # business order swapped
    assert r1["sync_id"] == "sA" and r2["sync_id"] == "sB"   # identity stable

    ops = _outbox_rows(path)
    assert len(ops) == 2
    assert all(o["op_type"] == P.OP_UPSERT for o in ops)
    p1 = [o for o in ops if o["sync_id"] == "sA"][0]["payload"]
    p2 = [o for o in ops if o["sync_id"] == "sB"][0]["payload"]
    assert p1["payload"]["sr_no"] == 2
    assert p2["payload"]["sr_no"] == 1
    assert p1["base"]["sr_no"] == 1 and p2["base"]["sr_no"] == 2


def test_swap_sr_no_failure_rolls_back_both_rows():
    path = _make_app_db()
    conn = sqlite3.connect(path)
    rid1 = _seed_plain(conn, _biz(sr_no=1, name="ONE", serial_no="S1"),
                       sync_id="sA")
    rid2 = _seed_plain(conn, _biz(sr_no=2, name="TWO", serial_no="S2"),
                       sync_id="sB")
    conn.close()

    orig = S.execute
    calls = {"n": 0}

    def boom(conn_, is_pg, sql, params=()):
        if "INSERT INTO outbox" in sql:
            calls["n"] += 1
            if calls["n"] == 2:      # second row's outbox fails -> rollback all
                raise RuntimeError("injected: swap outbox failed")
        return orig(conn_, is_pg, sql, params)

    S.execute = boom
    try:
        # Legacy parity: swap_sr_no returns False on failure (never raises).
        assert db.swap_sr_no(rid1, rid2) is False
    finally:
        S.execute = orig

    r1 = _q1("SELECT sr_no FROM records WHERE id=?", (rid1,))
    r2 = _q1("SELECT sr_no FROM records WHERE id=?", (rid2,))
    assert r1["sr_no"] == 1 and r2["sr_no"] == 2       # unchanged
    assert _outbox_rows(path) == []


# ---------------------------------------------------------------------------
# FAILURE ATOMICITY on update
# ---------------------------------------------------------------------------
def test_update_failure_during_metadata_rolls_back_business_change():
    path = _make_app_db()
    conn = sqlite3.connect(path)
    base = _biz(name="ORIG", price=1000.0)
    _seed_baselined(conn, "s1", base, server_rev=3)
    conn.close()
    rid = _q1("SELECT id FROM records WHERE sync_id='s1'")["id"]

    orig_now = sync_write._now_iso

    def boom():
        raise RuntimeError("injected: metadata timestamp failed")

    sync_write._now_iso = boom
    try:
        try:
            db.update_record(record_id=rid, invoice_no="INV-1", serial_no="S1",
                             xcell="", dp_taken="0", product_given="",
                             given_prod_price="0", alt_phone="", remarks="",
                             data={"name": "WONT-STICK", "product": "P",
                                   "price": "9000", "mobile": "1",
                                   "bid_date": "05-08-2026"})
            raise AssertionError("update_record should have raised")
        except RuntimeError as e:
            assert "injected" in str(e)
    finally:
        sync_write._now_iso = orig_now

    row = _q1("SELECT * FROM records WHERE sync_id='s1'")
    assert row["name"] == "ORIG" and row["price"] == 1000.0
    assert row["row_rev"] == 0
    assert _outbox_rows(path) == []


def test_update_failure_before_outbox_rolls_back_business_change():
    path = _make_app_db()
    conn = sqlite3.connect(path)
    base = _biz(name="ORIG", price=1000.0)
    _seed_baselined(conn, "s1", base, server_rev=3)
    conn.close()
    rid = _q1("SELECT id FROM records WHERE sync_id='s1'")["id"]

    orig = S.execute

    def boom(conn_, is_pg, sql, params=()):
        if "INSERT INTO outbox" in sql:
            raise RuntimeError("injected: outbox insert failed")
        return orig(conn_, is_pg, sql, params)

    S.execute = boom
    try:
        try:
            db.update_record(record_id=rid, invoice_no="INV-1", serial_no="S1",
                             xcell="", dp_taken="0", product_given="",
                             given_prod_price="0", alt_phone="", remarks="",
                             data={"name": "WONT-STICK", "product": "P",
                                   "price": "9000", "mobile": "1",
                                   "bid_date": "05-08-2026"})
            raise AssertionError("update_record should have raised")
        except RuntimeError as e:
            assert "injected" in str(e)
    finally:
        S.execute = orig

    row = _q1("SELECT * FROM records WHERE sync_id='s1'")
    assert row["name"] == "ORIG" and row["price"] == 1000.0
    assert row["row_rev"] == 0
    assert _outbox_rows(path) == []


def test_update_failure_after_outbox_before_commit_rolls_back_everything():
    path = _make_app_db()
    conn = sqlite3.connect(path)
    base = _biz(name="ORIG", price=1000.0)
    _seed_baselined(conn, "s1", base, server_rev=3)
    conn.close()
    rid = _q1("SELECT id FROM records WHERE sync_id='s1'")["id"]

    orig_commit = db._commit

    def boom(conn_):
        raise RuntimeError("injected: commit failed")

    db._commit = boom
    try:
        try:
            db.update_record(record_id=rid, invoice_no="INV-1", serial_no="S1",
                             xcell="", dp_taken="0", product_given="",
                             given_prod_price="0", alt_phone="", remarks="",
                             data={"name": "WONT-STICK", "product": "P",
                                   "price": "9000", "mobile": "1",
                                   "bid_date": "05-08-2026"})
            raise AssertionError("update_record should have raised")
        except RuntimeError as e:
            assert "injected" in str(e)
    finally:
        db._commit = orig_commit

    row = _q1("SELECT * FROM records WHERE sync_id='s1'")
    assert row["name"] == "ORIG" and row["price"] == 1000.0
    assert row["row_rev"] == 0
    assert _outbox_rows(path) == []


# ---------------------------------------------------------------------------
# LEGACY SCHEMA (no sync columns) -> exact pre-Phase-6 behaviour
# ---------------------------------------------------------------------------
def test_legacy_schema_db_keeps_old_crud_behaviour():
    _make_app_db()   # ensure the active DB is out of the way first
    legacy = os.path.join(_TMP, "legacy.db")
    if os.path.exists(legacy):
        os.remove(legacy)
    db.DB_FILE = legacy
    db._db_initialized = True
    db.init_db()      # plain schema: NO sync columns/outbox
    try:
        rid = db.add_record(invoice_no="LEG-1", serial_no="SER-L1", xcell="",
                            dp_taken="0", product_given="", given_prod_price="0",
                            alt_phone="", remarks="",
                            data={"name": "LEGACY", "product": "P",
                                  "price": "500", "bid_date": "05-08-2026"})
        row = _q1("SELECT * FROM records WHERE id=?", (rid,))
        assert row["name"] == "LEGACY"
        assert "sync_id" not in row        # untouched legacy schema
        db.delete_record(rid)
        assert _q1("SELECT COUNT(*) AS c FROM records")["c"] == 0
    finally:
        # leave the module DB_FILE pointing back at a fresh sync DB
        _make_app_db()


# ---------------------------------------------------------------------------
# ENGINE COMPATIBILITY: outbox produced by database.py writes pushes cleanly
# ---------------------------------------------------------------------------
def test_database_write_outbox_pushes_through_phase4_engine():
    path = _make_app_db()
    conn = sqlite3.connect(path)
    base = _biz(name="ORIG", price=1000.0, phone="111", bid_date="05-08-2026")
    _seed_baselined(conn, "s1", base, server_rev=0)
    rid = _q1("SELECT id FROM records WHERE sync_id='s1'")["id"]

    d = os.path.join(_TMP, "engine_%d" % _counter["n"])
    os.makedirs(d, exist_ok=True)
    srv = H.make_db(os.path.join(d, "srv.db"))
    H.insert_row(srv, "s1", base)
    srv.commit()

    db.update_record(
        record_id=rid, invoice_no="INV-1", serial_no="SER-1", xcell="",
        dp_taken="0", product_given="", given_prod_price="0", alt_phone="",
        remarks="engine ready",
        data={"name": "CHANGED", "product": "P", "price": "1500", "mobile": "2",
              "bid_date": "05-08-2026"})
    conn.close()

    eng_conn = sqlite3.connect(path)
    eng = SyncEngine(eng_conn, False, H.ServerAdapter(srv),
                     lock_path=os.path.join(d, "sync.lock"))
    res = eng.run_once()
    assert res.status == P.SESSION_SUCCESS and res.pushed == 1, res.as_dict()
    # server converged to the local write
    srv_row = S.read_row_full(srv, False, "s1")
    assert srv_row["name"] == "CHANGED" and srv_row["price"] == 1500.0
    assert srv_row["server_rev"] > 0
    # local row converged: base advanced to the new agreed snapshot
    off_row = S.read_row_full(eng_conn, False, "s1")
    assert off_row["name"] == "CHANGED" and off_row["row_rev"] == 0
    assert M.values_equal("name",
                          json.loads(off_row["base_json"]).get("name"), "CHANGED")
    eng_conn.close()
    srv.close()


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print("PASS", fn.__name__)
        except Exception:
            failed += 1
            import traceback
            traceback.print_exc()
    print("\n%s" % ("ALL SYNCV2 PHASE6 WRITE TESTS PASSED"
                    if failed == 0 else "%d FAILED" % failed))
    sys.exit(1 if failed else 0)


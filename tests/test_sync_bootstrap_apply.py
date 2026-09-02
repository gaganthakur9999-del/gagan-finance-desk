"""
Synthetic tests for the Phase-3 bootstrap-apply tool (bootstrap_apply.py).

Every database created here is a TEMPORARY SQLite twin (never production).
The same code paths used against production are exercised directly:
    build_pairs coverage / duplicate-sync guard
    preflight stop conditions (extra records, unexpected field diffs, sr-set change)
    apply_offline + apply_online (single transactions)
    verify_post (business equality, base_json equality, sync_id integrity)
    re-running the one-time bootstrap is refused
    'NA' serial + invoice values are preserved
"""
import os
import sqlite3
import sys
import tempfile
import uuid

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT not in sys.path:
    sys.path.insert(0, PROJECT)
sys.path.insert(0, os.path.join(PROJECT, "scripts", "sync"))

import sync_schema  # noqa: E402
import bootstrap_apply as B  # noqa: E402

AUG = "AUGUST"


def R(rid, **kw):
    row = {
        "id": rid, "sr_no": 1, "bid_date": "01-08-2026", "invoice_no": "",
        "name": "", "xcell": "", "product": "", "serial_no": "", "price": 0,
        "emi": 0, "di": 0, "bid": "", "dp_taken": "", "scheme": "",
        "actual_product": "", "given_prod_price": 0, "phone": "", "alt_phone": "",
        "month": AUG, "created_at": "2026-08-01 10:00:00",
        "updated_at": "2026-08-01 10:00:00", "remarks": "",
    }
    row.update(kw)
    return row


def build_db(path, rows):
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE records (
        id INTEGER PRIMARY KEY AUTOINCREMENT, sr_no INTEGER, bid_date TEXT,
        invoice_no TEXT, name TEXT, xcell TEXT, product TEXT, serial_no TEXT,
        price REAL DEFAULT 0, emi REAL DEFAULT 0, di REAL DEFAULT 0, bid TEXT,
        dp_taken REAL DEFAULT 0, scheme TEXT, actual_product TEXT,
        given_prod_price REAL DEFAULT 0, phone TEXT, alt_phone TEXT, month TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, remarks TEXT DEFAULT '')""")
    sync_schema.migrate_sync_schema(conn, False)
    cols = ",".join(B.CHECKSUM_COLUMNS)
    marks = ",".join("?" for _ in B.CHECKSUM_COLUMNS)
    for r in rows:
        conn.execute("INSERT INTO records (%s) VALUES (%s)" % (cols, marks),
                     tuple(r[c] for c in B.CHECKSUM_COLUMNS))
    conn.commit()
    return conn


def make_twin_dbs():
    d = tempfile.mkdtemp()
    off = R(1, bid="B1", invoice_no="I1", serial_no="S1", name="AA", price=1000)
    on = R(101, bid="B1", invoice_no="I1", serial_no="S1", name="AA", price=1000)
    off2 = R(2, bid="B2", invoice_no="I2", serial_no="S2", name="BB", price=2000, sr_no=5)
    on2 = R(102, bid="B2", invoice_no="I2", serial_no="S2", name="BB", price=2000, sr_no=7)
    off3 = R(3, bid="", invoice_no="", serial_no="", name="NEEL CHAND",
             bid_date="28-08-2025", month="AUGUST_2025", sr_no=35, price=None,
             phone="9816369426")
    on3 = R(103, bid="", invoice_no="", serial_no="", name="NEEL CHAND",
            bid_date="28-08-2025", month="AUGUST_2025", sr_no=35, price=None,
            phone="9816369426")
    u1, u2, um = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    pairs = [
        {"offline_id": 1, "online_id": 101, "sync_id": u1, "manual": False, "sr_diff": False},
        {"offline_id": 2, "online_id": 102, "sync_id": u2, "manual": False, "sr_diff": True},
        {"offline_id": 3, "online_id": 103, "sync_id": um, "manual": True, "sr_diff": False},
    ]
    c_off = build_db(os.path.join(d, "off.db"), [off, off2, off3])
    c_on = build_db(os.path.join(d, "on.db"), [on, on2, on3])
    return d, c_off, c_on, pairs, um


def test_preflight_detects_sr_fix_set():
    d, c_off, c_on, pairs, um = make_twin_dbs()
    off_rows = B.read_rows(c_off, False)
    on_rows = B.read_rows(c_on, False)
    res = B.preflight({}, pairs, off_rows, on_rows,
                      report_sr_diff_pairs={p["online_id"] for p in pairs if p["sr_diff"]})
    assert res == {"sr_fix_online_ids": [102]}
    c_off.close()
    c_on.close()


def test_preflight_stops_on_extra_online_record():
    d, c_off, c_on, pairs, um = make_twin_dbs()
    c_on.execute("INSERT INTO records (id, name) VALUES (104, 'UNEXPECTED')")
    c_on.commit()
    try:
        B.preflight({}, pairs, B.read_rows(c_off, False), B.read_rows(c_on, False),
                    report_sr_diff_pairs={p["online_id"] for p in pairs if p["sr_diff"]})
        raise AssertionError("expected StopError")
    except B.StopError:
        pass
    c_off.close()
    c_on.close()


def test_preflight_stops_on_unapproved_field_diff():
    d, c_off, c_on, pairs, um = make_twin_dbs()
    c_on.execute("UPDATE records SET price=9999 WHERE id=101")
    c_on.commit()
    try:
        B.preflight({}, pairs, B.read_rows(c_off, False), B.read_rows(c_on, False),
                    report_sr_diff_pairs={p["online_id"] for p in pairs if p["sr_diff"]})
        raise AssertionError("expected StopError")
    except B.StopError:
        pass
    c_off.close()
    c_on.close()


def test_duplicate_sync_id_mapping_guard():
    try:
        B._validate_coverage([{"offline_id": 1, "online_id": 101, "sync_id": "X"},
                              {"offline_id": 2, "online_id": 102, "sync_id": "X"}])
        raise AssertionError("expected StopError")
    except B.StopError:
        pass


def test_full_bootstrap_on_twin_dbs():
    d, c_off, c_on, pairs, um = make_twin_dbs()
    off_rows = B.read_rows(c_off, False)
    on_rows = B.read_rows(c_on, False)
    sr_fix = set(B.preflight({}, pairs, off_rows, on_rows,
                             report_sr_diff_pairs={p["online_id"] for p in pairs if p["sr_diff"]})
                 ["sr_fix_online_ids"])
    now = B.now_iso()

    # Offline sr unchanged after its own apply; online sr fixed to Offline value.
    B.apply_offline(c_off, pairs, off_rows, now)
    B.apply_online(c_on, False, pairs, off_rows, on_rows, sr_fix, now)

    # Online sr now equals Offline sr (offline authoritative).
    on_row2 = B.read_rows(c_on, False)[102]
    assert int(on_row2["sr_no"]) == 5

    verif = B.verify_post(c_off, False, c_on, False, pairs, um)
    assert verif["pair_business_diffs_remaining"] == 0
    assert verif["base_json_mismatches"] == 0
    assert verif["sync_ids_offline"] == 3
    assert verif["sync_ids_online"] == 3

    # Same UUID on both sides per identity; base_json identical string.
    s_off = {r[0]: r[1] for r in c_off.execute("SELECT id, sync_id FROM records").fetchall()}
    s_on = {r[0]: r[1] for r in c_on.execute("SELECT id, sync_id FROM records").fetchall()}
    b_off = {r[0]: r[1] for r in c_off.execute("SELECT id, base_json FROM records").fetchall()}
    b_on = {r[0]: r[1] for r in c_on.execute("SELECT id, base_json FROM records").fetchall()}
    for p in pairs:
        assert s_off[p["offline_id"]] == s_on[p["online_id"]] == p["sync_id"]
        assert b_off[p["offline_id"]] == b_on[p["online_id"]]
    assert s_off[3] == um  # NEEL unified under one shared sync_id
    assert s_off[3] == s_on[103]

    # server_rev/row_rev initialised to 0; sync_state marked; outbox empty.
    assert c_off.execute("SELECT server_rev, row_rev FROM records WHERE id=1").fetchone() == (0, 0)
    assert c_on.execute("SELECT server_rev, row_rev FROM records WHERE id=101").fetchone() == (0, 0)
    assert c_off.execute("SELECT last_success_at FROM sync_state WHERE id=1").fetchone()[0] is not None
    assert c_on.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 0
    c_off.close()
    c_on.close()


def test_na_and_invoice_values_preserved():
    d, c_off, c_on, pairs, um = make_twin_dbs()
    # Two rows with serial 'NA' and a real invoice value.
    c_off.execute("UPDATE records SET serial_no='NA' WHERE id IN (1,2)")
    c_on.execute("UPDATE records SET serial_no='NA' WHERE id IN (101,102)")
    c_off.commit()
    c_on.commit()
    off_rows = B.read_rows(c_off, False)
    on_rows = B.read_rows(c_on, False)
    sr_fix = set(B.preflight({}, pairs, off_rows, on_rows,
                             report_sr_diff_pairs={p["online_id"] for p in pairs if p["sr_diff"]})
                 ["sr_fix_online_ids"])
    now = B.now_iso()
    B.apply_offline(c_off, pairs, off_rows, now)
    B.apply_online(c_on, False, pairs, off_rows, on_rows, sr_fix, now)
    assert [r[0] for r in c_off.execute("SELECT serial_no FROM records WHERE id IN (1,2)")] == ["NA", "NA"]
    assert [r[0] for r in c_on.execute("SELECT serial_no FROM records WHERE id IN (101,102)")] == ["NA", "NA"]
    assert c_off.execute("SELECT invoice_no FROM records WHERE id=1").fetchone()[0] == "I1"
    assert c_on.execute("SELECT invoice_no FROM records WHERE id=101").fetchone()[0] == "I1"
    c_off.close()
    c_on.close()


def test_rerun_is_refused_after_baseline():
    d, c_off, c_on, pairs, um = make_twin_dbs()
    off_rows = B.read_rows(c_off, False)
    on_rows = B.read_rows(c_on, False)
    sr_fix = {p["online_id"] for p in pairs if p["sr_diff"]}
    now = B.now_iso()
    B.apply_offline(c_off, pairs, off_rows, now)
    B.apply_online(c_on, False, pairs, off_rows, on_rows, sr_fix, now)
    try:
        B.apply_offline(c_off, pairs, B.read_rows(c_off, False), now)
        raise AssertionError("expected StopError on re-run")
    except B.StopError:
        pass
    # Offline not corrupted by the refused re-run.
    assert c_off.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 3
    assert c_off.execute("SELECT COUNT(DISTINCT sync_id) FROM records").fetchone()[0] == 3
    c_off.close()
    c_on.close()


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print("PASS", fn.__name__)
        except Exception:
            failed += 1
            import traceback
            traceback.print_exc()
    print("\n%s" % ("ALL SYNC BOOTSTRAP APPLY TESTS PASSED" if failed == 0
                    else "%d FAILED" % failed))
    sys.exit(1 if failed else 0)


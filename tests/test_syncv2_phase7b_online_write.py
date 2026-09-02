# -*- coding: utf-8 -*-
"""Phase 7B - Online write-capture seam + E2E propagation tests.

The Online seam (online_write.py) is backend-agnostic (is_pg). No isolated
PostgreSQL exists on this machine, so the seam is executed with is_pg=False
against SQLite twin databases - the project's established translation/compat
method. The engine/server code paths exercised are byte-identical to the PG
branches except for psycopg2-specific mechanics (which stay NOT TESTED).

Run standalone:  python tests/test_syncv2_phase7b_online_write.py
"""
import json
import os
import sys
import tempfile

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT not in sys.path:
    sys.path.insert(0, PROJECT)
sys.path.insert(0, os.path.join(PROJECT, "tests"))

import online_write as OW  # noqa: E402
import syncv2_helpers as H  # noqa: E402
from syncv2 import protocol as P  # noqa: E402
from syncv2 import store as S  # noqa: E402
from syncv2 import merge as M  # noqa: E402
from syncv2 import server as SVC  # noqa: E402
from syncv2.engine import SyncEngine  # noqa: E402

_TMP = tempfile.mkdtemp(prefix="gfd_p7b_")
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


def _env():
    _counter["n"] += 1
    d = os.path.join(_TMP, "env_%d" % _counter["n"])
    os.makedirs(d, exist_ok=True)
    off = H.make_db(os.path.join(d, "off.db"))
    onl = H.make_db(os.path.join(d, "onl.db"))
    eng = SyncEngine(off, False, H.ServerAdapter(onl),
                     lock_path=os.path.join(d, "sync.lock"))
    return off, onl, eng, d


def _commit(conn):
    conn.commit()


def _on_id(conn, sid):
    rows = S.fetch_all(conn, False, "SELECT id FROM records WHERE sync_id=?",
                       (sid,))
    assert rows, "missing online row for sync_id %s" % sid
    return rows[0][0]


def _seed_pair(off, onl, sid, business, server_rev=0):
    bj = json.dumps({f: business.get(f) for f in P.BUSINESS_FIELDS},
                    sort_keys=True, default=str, ensure_ascii=True,
                    separators=(",", ":"))
    H.insert_row(off, sid, business, base_json=bj, server_rev=server_rev)
    H.insert_row(onl, sid, business, base_json=bj, server_rev=server_rev)


def _assert_no_dup_sync_ids(conn, label):
    rows = S.fetch_all(conn, False,
                       "SELECT sync_id FROM records WHERE sync_id IS NOT NULL "
                       "AND sync_id <> ''")
    vals = [r[0] for r in rows]
    assert len(vals) == len(set(vals)), "duplicate sync_id on %s" % label

# ---------------------------------------------------------------------------
# ONLINE CREATE / EDIT / DELETE / SR — seam + pull propagation
# ---------------------------------------------------------------------------
def test_online_create_assigns_sync_id_rev_and_pulls_to_offline():
    off, onl, eng, _ = _env()
    biz = _biz(name="NEW-ONLINE", invoice_no="ON-1", serial_no="OSER-1")
    OW.create_row(onl, False, biz)
    _commit(onl)
    rows = S.fetch_all(onl, False, "SELECT sync_id, server_rev, row_rev, "
                                    "base_json FROM records WHERE name=?",
                       ("NEW-ONLINE",))
    assert len(rows) == 1
    sid, srv, rrv, base = rows[0]
    assert sid and srv >= 1 and rrv == 0 and base
    # no outbox entries (server-side writes are row-revision records, not ops)
    assert S.fetch_all(onl, False, "SELECT COUNT(*) FROM outbox")[0][0] == 0
    res = eng.run_once()
    assert res.status == P.SESSION_SUCCESS and res.pulled >= 1
    off_row = S.read_row_full(off, False, sid)
    assert off_row is not None and off_row["name"] == "NEW-ONLINE"
    assert int(off_row["server_rev"]) == int(srv)
    _assert_no_dup_sync_ids(off, "offline")
    _assert_no_dup_sync_ids(onl, "online")
    off.close()
    onl.close()


def test_online_edit_preserves_sync_id_and_is_pullable():
    off, onl, eng, _ = _env()
    biz = _biz(name="ORIG")
    _seed_pair(off, onl, "s1", biz)
    rid = _on_id(onl, "s1")
    before = S.read_row_full(onl, False, "s1")
    OW.edit_row(onl, False, rid, _biz(name="ONLINE-EDIT", price=1500.0))
    _commit(onl)
    after = S.read_row_full(onl, False, "s1")
    assert after["sync_id"] == "s1"
    assert after["name"] == "ONLINE-EDIT" and after["price"] == 1500.0
    assert int(after["server_rev"]) > int(before["server_rev"] or 0)
    assert int(after["row_rev"] or 0) == 0
    assert json.loads(after["base_json"])["name"] == "ONLINE-EDIT"
    assert S.fetch_all(onl, False, "SELECT COUNT(*) FROM outbox")[0][0] == 0
    res = eng.run_once()
    assert res.status == P.SESSION_SUCCESS and res.pulled >= 1
    off_row = S.read_row_full(off, False, "s1")
    assert off_row["name"] == "ONLINE-EDIT" and off_row["price"] == 1500.0
    assert int(off_row["server_rev"]) == int(after["server_rev"])
    off.close()
    onl.close()


def test_online_delete_is_tombstone_and_pullable_no_physical_purge():
    off, onl, eng, _ = _env()
    biz = _biz(name="TO-DELETE")
    _seed_pair(off, onl, "s1", biz)
    rid = _on_id(onl, "s1")
    res = OW.delete_row(onl, False, rid)
    _commit(onl)
    assert res["result"] == "applied" and res["deleted_at"]
    row = S.read_row_full(onl, False, "s1")
    assert row["deleted_at"] is not None
    assert row["sync_id"] == "s1" and row["name"] == "TO-DELETE"
    # physical row retained
    assert S.fetch_all(onl, False,
                       "SELECT COUNT(*) FROM records WHERE sync_id='s1'"
                       )[0][0] == 1
    r = eng.run_once()
    assert r.status == P.SESSION_SUCCESS
    off_row = S.read_row_full(off, False, "s1")
    assert off_row["deleted_at"] is not None
    # second delete is a no-op (idempotent)
    res2 = OW.delete_row(onl, False, rid)
    assert res2["result"] == "noop"
    off.close()
    onl.close()


def test_online_swap_sr_moves_order_and_pulls():
    off, onl, eng, _ = _env()
    biz_a = _biz(sr_no=1, name="AAA", serial_no="S1", invoice_no="I1")
    biz_b = _biz(sr_no=2, name="BBB", serial_no="S2", invoice_no="I2")
    _seed_pair(off, onl, "sA", biz_a)
    _seed_pair(off, onl, "sB", biz_b)
    ra = _on_id(onl, "sA")
    rb = _on_id(onl, "sB")
    assert OW.swap_sr(onl, False, ra, rb) is True
    _commit(onl)
    assert S.read_row_full(onl, False, "sA")["sr_no"] == 2
    assert S.read_row_full(onl, False, "sB")["sr_no"] == 1
    res = eng.run_once()
    assert res.status == P.SESSION_SUCCESS and res.pulled >= 2
    assert S.read_row_full(off, False, "sA")["sr_no"] == 2
    assert S.read_row_full(off, False, "sB")["sr_no"] == 1
    _assert_no_dup_sync_ids(onl, "online")
    off.close()
    onl.close()


# ---------------------------------------------------------------------------
# THREE-WAY CONFLICT BEHAVIOUR WITH ONLINE-ORIGINATED CHANGES
# ---------------------------------------------------------------------------
def test_online_edit_then_offline_edit_different_fields_unions():
    off, onl, eng, _ = _env()
    _seed_pair(off, onl, "s1", _biz(name="ORIG", price=1000.0, phone="111"))
    # Online edits NAME through the seam.
    rid = _on_id(onl, "s1")
    OW.edit_row(onl, False, rid, _biz(name="ON-NAME", price=1000.0, phone="111"))
    _commit(onl)
    # Offline edits PRICE before any sync.
    eng.begin_local_change("s1", _biz(name="ORIG", price=9000.0, phone="111"))
    res = eng.run_once()
    assert res.status == P.SESSION_SUCCESS and res.pushed == 1
    on_row = S.read_row_full(onl, False, "s1")
    assert on_row["name"] == "ON-NAME" and on_row["price"] == 9000.0
    off_row = S.read_row_full(off, False, "s1")
    assert off_row["name"] == "ON-NAME" and off_row["price"] == 9000.0
    assert _rows_equal(off, onl, "s1")
    off.close()
    onl.close()


def _rows_equal(off, onl, sid):
    a = S.read_row_full(off, False, sid)
    b = S.read_row_full(onl, False, sid)
    return (all(M.values_equal(f, a.get(f), b.get(f))
                for f in P.BUSINESS_FIELDS)
            and (a.get("deleted_at") or None) == (b.get("deleted_at") or None))


def test_online_edit_then_offline_edit_same_field_conflicts_and_resolves():
    off, onl, eng, _ = _env()
    base = _biz(name="ORIG", price=1000.0)
    _seed_pair(off, onl, "s1", base)
    rid = _on_id(onl, "s1")
    OW.edit_row(onl, False, rid, _biz(name="ON-WIN", price=1000.0))
    _commit(onl)
    eng.begin_local_change("s1", _biz(name="OFF-LOSE", price=1000.0))
    res = eng.run_once()
    assert res.status == P.SESSION_CONFLICT
    openc = SVC.open_conflicts_for(onl, False, "s1")
    assert any(c["kind"] == P.CONFLICT_FIELD for c in openc)
    cid = [c for c in openc if c["kind"] == P.CONFLICT_FIELD][0]["id"]
    out = eng.resolve_conflict(cid, "KEEP_ONLINE")
    assert out.get("converged") is True
    assert S.read_row_full(onl, False, "s1")["name"] == "ON-WIN"
    assert S.read_row_full(off, False, "s1")["name"] == "ON-WIN"
    assert _rows_equal(off, onl, "s1")
    off.close()
    onl.close()


def test_offline_delete_vs_online_edit_conflicts_no_resurrection():
    off, onl, eng, _ = _env()
    base = _biz(name="ORIG", price=1000.0)
    _seed_pair(off, onl, "s1", base)
    rid = _on_id(onl, "s1")
    OW.edit_row(onl, False, rid, _biz(name="ON-EDIT", price=1000.0))
    _commit(onl)
    eng.delete_local("s1")           # offline tombstone op
    res = eng.run_once()
    assert res.status == P.SESSION_CONFLICT
    openc = SVC.open_conflicts_for(onl, False, "s1")
    assert any(c["kind"] == P.CONFLICT_DELETE_EDIT for c in openc)
    cid = [c for c in openc if c["kind"] == P.CONFLICT_DELETE_EDIT][0]["id"]
    eng.resolve_conflict(cid, "KEEP_ONLINE")
    assert S.read_row_full(onl, False, "s1")["deleted_at"] is None
    assert S.read_row_full(off, False, "s1")["deleted_at"] is None
    assert S.read_row_full(off, False, "s1")["name"] == "ON-EDIT"
    off.close()
    onl.close()


def test_online_delete_vs_offline_edit_conflicts_no_resurrection():
    off, onl, eng, _ = _env()
    base = _biz(name="ORIG", price=1000.0)
    _seed_pair(off, onl, "s1", base)
    rid = _on_id(onl, "s1")
    OW.delete_row(onl, False, rid)
    _commit(onl)
    eng.begin_local_change("s1", _biz(name="STALE-EDIT", price=1000.0))
    res = eng.run_once()
    assert res.status == P.SESSION_CONFLICT
    on_row = S.read_row_full(onl, False, "s1")
    assert on_row["deleted_at"] is not None and on_row["name"] == "ORIG"
    cid = [c for c in SVC.open_conflicts_for(onl, False, "s1")
           if c["kind"] == P.CONFLICT_DELETE_EDIT][0]["id"]
    eng.resolve_conflict(cid, "KEEP_ONLINE")
    assert S.read_row_full(onl, False, "s1")["deleted_at"] is not None
    assert S.read_row_full(off, False, "s1")["deleted_at"] is not None
    off.close()
    onl.close()


# ---------------------------------------------------------------------------
# SR ORDERING / GROUPED CONFLICT WITH ONLINE-ORIGINATED REORDER
# ---------------------------------------------------------------------------
def test_both_sides_reorder_same_month_opens_grouped_sr_conflict():
    off, onl, eng, _ = _env()
    sids = ["sA", "sB", "sC"]
    for i, sid in enumerate(sids, start=1):
        _seed_pair(off, onl, sid, _biz(sr_no=i, bid_date="05-08-2026",
                                       name=sid, serial_no="S%s" % sid))
    # Online reorders sB->1, sC->2, sA->3 via the seam (one rev per row).
    ids = {sid: _on_id(onl, sid) for sid in sids}
    assert OW.swap_sr(onl, False, ids["sA"], ids["sB"]) is True
    _commit(onl)
    # Offline reorders independently: sC->1, sA->2, sB->3.
    offline_order = {"sC": 1, "sA": 2, "sB": 3}
    for sid in sids:
        eng.begin_local_change(sid, _biz(
            sr_no=offline_order[sid], bid_date="05-08-2026", name=sid,
            serial_no="S%s" % sid))
    res = eng.run_once()
    assert res.status == P.SESSION_CONFLICT
    openc = SVC.open_conflicts_for(onl, False, sids[0])
    assert any(c["kind"] == P.CONFLICT_SR_ORDER for c in openc)
    cid = [c for c in openc if c["kind"] == P.CONFLICT_SR_ORDER][0]["id"]
    out = eng.resolve_conflict(cid, "KEEP_OFFLINE")
    assert out.get("converged") is True
    for sid in sids:
        on = S.read_row_full(onl, False, sid)
        offr = S.read_row_full(off, False, sid)
        assert int(on["sr_no"]) == offline_order[sid]
        assert int(offr["sr_no"]) == offline_order[sid]
    _assert_no_dup_sync_ids(onl, "online")
    off.close()
    onl.close()


# ---------------------------------------------------------------------------
# IDENTITY / INVOICE INDEPENDENCE
# ---------------------------------------------------------------------------
def test_invoice_change_to_other_identity_keeps_records_independent():
    off, onl, eng, _ = _env()
    _seed_pair(off, onl, "sA", _biz(invoice_no="INV-A", serial_no="SA",
                                   name="A"))
    _seed_pair(off, onl, "sB", _biz(invoice_no="INV-B", serial_no="SB",
                                   name="B"))
    # Offline changes A's invoice to B's invoice number.
    eng.begin_local_change("sA", _biz(invoice_no="INV-B", serial_no="SA",
                                      name="A"))
    res = eng.run_once()
    assert res.status == P.SESSION_SUCCESS, res.as_dict()
    assert S.read_row_full(onl, False, "sA")["invoice_no"] == "INV-B"
    assert S.read_row_full(onl, False, "sB")["invoice_no"] == "INV-B"
    # Records remain distinct identities with distinct sync_ids.
    assert S.read_row_full(onl, False, "sA")["sync_id"] == "sA"
    assert S.read_row_full(onl, False, "sB")["sync_id"] == "sB"
    # Non-blocking invoice-collision advisory is recorded.
    openc = SVC.list_open_conflicts(onl, False)
    assert any(c["kind"] == P.CONFLICT_INVOICE for c in openc)
    off.close()
    onl.close()


# ---------------------------------------------------------------------------
# ATOMICITY / ROLLBACK / NULL-SYNC REFUSAL / REVISION MONOTONICITY
# ---------------------------------------------------------------------------
def test_seam_rollback_reverts_business_and_sequence_together():
    onl = H.make_db(os.path.join(_TMP, "rollback_%d.db" % _counter["n"]))
    _counter["n"] += 1
    biz = _biz(name="ONE")
    OW.create_row(onl, False, biz)
    _commit(onl)
    rows = S.fetch_all(onl, False, "SELECT id, sync_id FROM records")
    rid, sid = rows[0][0], rows[0][1]
    seq_before = S.fetch_all(onl, False,
                             "SELECT value FROM sync_sequence WHERE id=1")[0][0]
    try:
        onl.execute("BEGIN")
        OW.edit_row(onl, False, rid, _biz(name="EDIT1"))
        OW.edit_row(onl, False, rid, _biz(name="EDIT2"))
        raise RuntimeError("boom")
    except RuntimeError:
        onl.rollback()
    row = S.read_row_full(onl, False, sid)
    assert row["name"] == "ONE"           # both edits rolled back
    seq_after = S.fetch_all(onl, False,
                            "SELECT value FROM sync_sequence WHERE id=1")[0][0]
    assert seq_after == seq_before        # revision allocations rolled back too
    onl.close()


def test_seam_refuses_legacy_null_sync_row():
    onl = H.make_db(os.path.join(_TMP, "nullsync_%d.db" % _counter["n"]))
    _counter["n"] += 1
    # Simulate a legacy row created by old sync: business fields only, NULL sync.
    onl.execute(
        "INSERT INTO records (sr_no,bid_date,invoice_no,name,serial_no,price,"
        "phone,month) VALUES (1,'05-08-2026','LEG-1','LEGACY','LSER-1',100,"
        "'1','AUGUST_2026')")
    onl.commit()
    rid = onl.execute("SELECT id FROM records WHERE invoice_no='LEG-1'"
                      ).fetchone()[0]
    seq_before = S.fetch_all(onl, False,
                             "SELECT value FROM sync_sequence WHERE id=1")[0][0]
    try:
        onl.execute("BEGIN")
        OW.edit_row(onl, False, rid, _biz(name="X"))
        raise AssertionError("edit_row should have refused a NULL-sync row")
    except RuntimeError as exc:
        assert "no sync_id" in str(exc)
    finally:
        onl.rollback()
    seq_after = S.fetch_all(onl, False,
                            "SELECT value FROM sync_sequence WHERE id=1")[0][0]
    assert seq_after == seq_before
    row = S.fetch_all(onl, False, "SELECT name FROM records WHERE id=?",
                      (rid,))
    assert row[0][0] == "LEGACY"
    onl.close()


def test_revisions_monotonic_under_sequential_online_writes():
    onl = H.make_db(os.path.join(_TMP, "mono_%d.db" % _counter["n"]))
    _counter["n"] += 1
    revs = []
    biz = _biz(name="R1")
    OW.create_row(onl, False, biz)
    _commit(onl)
    rid = onl.execute("SELECT id FROM records").fetchone()[0]
    for i in range(1, 6):
        res = OW.edit_row(onl, False, rid, _biz(name="R%d" % (i + 1)))
        _commit(onl)
        revs.append(res["server_rev"])
    assert revs == sorted(revs) and len(set(revs)) == len(revs)
    row = S.fetch_all(onl, False,
                      "SELECT server_rev FROM records WHERE id=?", (rid,))
    assert row[0][0] == revs[-1]
    onl.close()


def test_no_duplicate_sync_ids_after_mixed_workload():
    off, onl, eng, _ = _env()
    biz = _biz(name="BASE")
    _seed_pair(off, onl, "s1", biz)
    # Online create + offline create, then one round trip.
    OW.create_row(onl, False, _biz(name="ON-NEW", invoice_no="ON-2",
                                   serial_no="OSER-2"))
    _commit(onl)
    eng.begin_local_change("sX", _biz(name="OFF-NEW", invoice_no="OFF-1",
                                      serial_no="OFSER-1"))
    res = eng.run_once()
    assert res.status == P.SESSION_SUCCESS
    _assert_no_dup_sync_ids(onl, "online")
    _assert_no_dup_sync_ids(off, "offline")
    off.close()
    onl.close()


def test_online_delete_renumbers_month_rows_and_converges():
    off, onl, eng, _ = _env()
    sids = ["sA", "sB", "sC"]
    for i, sid in enumerate(sids, start=1):
        _seed_pair(off, onl, sid, _biz(sr_no=i, invoice_no="INV-%s" % sid,
                                       serial_no="S%s" % sid, name=sid,
                                       bid_date="05-08-2026"))
    # Online deletes the middle row; the seam tombstones it and renumbers sC
    # 3 -> 2 with its own server revision.
    mid = _on_id(onl, "sB")
    res = OW.delete_row(onl, False, mid)
    _commit(onl)
    assert res["result"] == "applied" and res["renumbered"] == 1
    assert S.read_row_full(onl, False, "sB")["deleted_at"] is not None
    assert S.read_row_full(onl, False, "sC")["sr_no"] == 2
    assert S.read_row_full(onl, False, "sA")["sr_no"] == 1
    r = eng.run_once()
    assert r.status == P.SESSION_SUCCESS
    assert S.read_row_full(off, False, "sB")["deleted_at"] is not None
    assert S.read_row_full(off, False, "sC")["sr_no"] == 2
    assert S.read_row_full(off, False, "sA")["sr_no"] == 1
    assert _rows_equal(off, onl, "sA")
    assert _rows_equal(off, onl, "sC")
    off.close()
    onl.close()


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
    print("\n%s" % ("ALL SYNCV2 PHASE7B ONLINE WRITE TESTS PASSED"
                    if failed == 0 else "%d FAILED" % failed))
    sys.exit(1 if failed else 0)


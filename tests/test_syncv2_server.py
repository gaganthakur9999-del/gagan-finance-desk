"""Coordinator tests: idempotency ledger, revisions, conflicts, resolution."""
import os
import sqlite3
import sys
import tempfile
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests"))

from syncv2 import protocol as P
from syncv2 import server as SVC
from syncv2 import store as S
import syncv2_helpers as H


def _base_payload():
    return {f: None for f in P.BUSINESS_FIELDS}


def _make_env():
    d = tempfile.mkdtemp(prefix="syncv2_server_")
    return d, H.make_db(os.path.join(d, "srv.db"))


def _op(sync_id, business, base=None, op_id=None, op_type=P.OP_UPSERT, base_rev=0):
    return {"op_id": op_id or str(uuid.uuid4()), "sync_id": sync_id,
            "op_type": op_type, "payload": dict(business),
            "base": base or _base_payload(), "base_rev": base_rev}


def test_apply_new_record_create():
    d, conn = _make_env()
    biz = H.base_business(name="NEW CUSTOMER", invoice_no="INV-9001")
    res = SVC.apply_ops(conn, False, [_op("sync-new", biz)])
    assert res["results"][0]["result"] == "applied"
    assert res["revision"] == 1
    row = S.read_row_full(conn, False, "sync-new")
    assert row["name"] == "NEW CUSTOMER"
    assert row["month"] == "AUGUST_2026"
    assert conn.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 1
    conn.close()


def test_idempotent_duplicate_op_does_not_reapply():
    d, conn = _make_env()
    op = _op("sync-1", H.base_business(name="NAME1"))
    r1 = SVC.apply_ops(conn, False, [op])
    r2 = SVC.apply_ops(conn, False, [dict(op, payload=H.base_business(name="HACKED"))])
    assert r1["results"][0]["result"] == "applied"
    assert r2["results"][0]["replayed"] is True
    assert r2["results"][0]["result"] == "applied"
    row = S.read_row_full(conn, False, "sync-1")
    assert row["name"] == "NAME1"
    assert conn.execute("SELECT COUNT(*) FROM applied_ops").fetchone()[0] == 1
    assert conn.execute("SELECT value FROM sync_sequence WHERE id=1").fetchone()[0] == 1
    conn.close()


def test_one_sided_offline_edit_applies():
    d, conn = _make_env()
    biz = H.base_business()
    H.insert_row(conn, "sync-1", biz)
    res = SVC.apply_ops(conn, False,
                        [_op("sync-1", H.base_business(name="EDITED", phone="9110000000"),
                             base=biz, base_rev=0)])
    assert res["results"][0]["result"] == "applied"
    row2 = S.read_row_full(conn, False, "sync-1")
    assert row2["name"] == "EDITED" and row2["phone"] == "9110000000"
    conn.close()


def test_stale_client_reconciled_via_three_way():
    d, conn = _make_env()
    base = H.base_business()
    H.insert_row(conn, "sync-1", base)
    biz2 = dict(base)
    biz2["phone"] = "9-SERVER"
    SVC.apply_ops(conn, False, [_op("sync-1", biz2, base=base, base_rev=0)])
    stale = dict(base)
    stale["name"] = "STALE-CLIENT-NAME"
    res = SVC.apply_ops(conn, False, [_op("sync-1", stale, base=base, base_rev=0)])
    assert res["results"][0]["result"] == "applied"
    row = S.read_row_full(conn, False, "sync-1")
    assert row["name"] == "STALE-CLIENT-NAME" and row["phone"] == "9-SERVER"
    conn.close()


def test_same_financial_field_divergence_conflicts():
    d, conn = _make_env()
    base = H.base_business()
    H.insert_row(conn, "sync-1", base)
    off = dict(base); off["price"] = 1111
    SVC.apply_ops(conn, False, [_op("sync-1", off, base=base, base_rev=0)])  # applied rev1
    stale = dict(base); stale["price"] = 3333
    res = SVC.apply_ops(conn, False, [_op("sync-1", stale, base=base, base_rev=0)])
    assert res["results"][0]["result"] == "conflict"
    row = S.read_row_full(conn, False, "sync-1")
    assert row["price"] == 1111  # conflicting change NOT applied; baseline not advanced
    openc = SVC.open_conflicts_for(conn, False, "sync-1")
    assert len(openc) == 1 and openc[0]["kind"] == "financial"
    assert float(openc[0]["offline_value"]) == 3333
    assert float(openc[0]["online_value"]) == 1111
    assert float(openc[0]["base_value"]) == 1000
    res2 = SVC.apply_ops(conn, False, [_op("sync-1", stale, base=base, base_rev=0)])
    assert res2["results"][0]["result"] == "conflict"  # freeze: never blind-applies
    conn.close()


def test_both_sides_same_field_same_value_converges():
    d, conn = _make_env()
    base = H.base_business()
    H.insert_row(conn, "sync-1", base)
    c1 = dict(base); c1["name"] = "SAME"
    SVC.apply_ops(conn, False, [_op("sync-1", c1, base=base, base_rev=0)])
    c2 = dict(base); c2["name"] = "SAME"
    res = SVC.apply_ops(conn, False, [_op("sync-1", c2, base=base, base_rev=0)])
    assert res["results"][0]["result"] == "applied"   # safe convergence
    assert S.read_row_full(conn, False, "sync-1")["name"] == "SAME"
    conn.close()


def test_offline_delete_propagates_and_noop_on_retry():
    d, conn = _make_env()
    base = H.base_business()
    H.insert_row(conn, "sync-1", base)
    del_payload = dict(base)
    del_payload["deleted_at"] = S.now_utc()
    op = _op("sync-1", del_payload, base=base, op_type=P.OP_DELETE)
    res = SVC.apply_ops(conn, False, [op])
    assert res["results"][0]["result"] == "applied"
    assert S.read_row_full(conn, False, "sync-1")["deleted_at"] is not None
    r2 = SVC.apply_ops(conn, False, [dict(op)])
    assert r2["results"][0]["replayed"] is True       # same op_id replays stored result
    fresh = _op("sync-1", del_payload, base=base, op_type=P.OP_DELETE)
    r3 = SVC.apply_ops(conn, False, [fresh])
    assert r3["results"][0]["result"] == "noop"        # fresh duplicate delete = no-op
    conn.close()


def test_delete_vs_edit_conflict():
    d, conn = _make_env()
    base = H.base_business()
    H.insert_row(conn, "sync-1", base)
    online_edit = dict(base)
    online_edit["name"] = "ONLINE-EDIT"
    SVC.apply_ops(conn, False, [_op("sync-1", online_edit, base=base, base_rev=0)])
    del_payload = dict(base)
    del_payload["deleted_at"] = S.now_utc()
    res = SVC.apply_ops(conn, False,
                        [_op("sync-1", del_payload, base=base, op_type=P.OP_DELETE,
                             base_rev=0)])
    assert res["results"][0]["result"] == "conflict"
    row = S.read_row_full(conn, False, "sync-1")
    assert row["name"] == "ONLINE-EDIT" and row["deleted_at"] is None
    conn.close()


def test_invoice_collision_advisory_recorded_but_applied():
    d, conn = _make_env()
    H.insert_row(conn, "sync-A", H.base_business(invoice_no="INV-SAME"))
    base_b = H.base_business(invoice_no="INV-B")
    H.insert_row(conn, "sync-B", base_b)
    changed = dict(base_b)
    changed["invoice_no"] = "INV-SAME"
    res = SVC.apply_ops(conn, False, [_op("sync-B", changed, base=base_b, base_rev=0)])
    assert res["results"][0]["result"] == "applied"
    advisory = SVC.open_conflicts_for(conn, False, "sync-B")
    assert any(c["kind"] == "invoice_collision" for c in advisory)
    assert S.read_row_full(conn, False, "sync-A")["invoice_no"] == "INV-SAME"
    assert S.read_row_full(conn, False, "sync-B")["invoice_no"] == "INV-SAME"
    conn.close()


def test_same_record_invoice_change_ok():
    d, conn = _make_env()
    base = H.base_business(invoice_no="OLD-INV")
    H.insert_row(conn, "sync-1", base)
    changed = dict(base)
    changed["invoice_no"] = "NEW-INV"
    res = SVC.apply_ops(conn, False, [_op("sync-1", changed, base=base, base_rev=0)])
    assert res["results"][0]["result"] == "applied"
    conn.close()


def test_resolution_keep_offline_and_idempotent():
    d, conn = _make_env()
    base = H.base_business()
    H.insert_row(conn, "sync-1", base)
    a = dict(base); a["price"] = 1111
    SVC.apply_ops(conn, False, [_op("sync-1", a, base=base, base_rev=0)])
    b = dict(base); b["price"] = 2222
    SVC.apply_ops(conn, False, [_op("sync-1", b, base=base, base_rev=0)])  # conflict
    cid = SVC.open_conflicts_for(conn, False, "sync-1")[0]["id"]
    res = SVC.resolve_conflict(conn, False, cid, "KEEP_OFFLINE")
    assert res.get("applied_revision")
    assert S.read_row_full(conn, False, "sync-1")["price"] == 2222  # offline_value wins
    res2 = SVC.resolve_conflict(conn, False, cid, "KEEP_ONLINE")
    assert res2.get("replayed")
    assert S.read_row_full(conn, False, "sync-1")["price"] == 2222
    conn.close()


def test_resolution_keep_online_and_merge():
    d, conn = _make_env()
    base = H.base_business()
    H.insert_row(conn, "sync-1", base)
    a = dict(base); a["price"] = 1111
    SVC.apply_ops(conn, False, [_op("sync-1", a, base=base, base_rev=0)])
    b = dict(base); b["price"] = 2222
    SVC.apply_ops(conn, False, [_op("sync-1", b, base=base, base_rev=0)])
    cid = SVC.open_conflicts_for(conn, False, "sync-1")[0]["id"]
    res = SVC.resolve_conflict(conn, False, cid, "KEEP_ONLINE")
    assert S.read_row_full(conn, False, "sync-1")["price"] == 1111  # online_value wins
    conn.close()


def test_resolution_after_server_moved_reopens():
    d, conn = _make_env()
    base = H.base_business()
    H.insert_row(conn, "sync-1", base)
    a = dict(base); a["price"] = 1111
    SVC.apply_ops(conn, False, [_op("sync-1", a, base=base, base_rev=0)])
    b = dict(base); b["price"] = 2222
    SVC.apply_ops(conn, False, [_op("sync-1", b, base=base, base_rev=0)])
    cid = SVC.open_conflicts_for(conn, False, "sync-1")[0]["id"]
    # Out-of-band server change AFTER the conflict was recorded.
    conn.execute("UPDATE records SET price=7777 WHERE sync_id='sync-1'")
    conn.commit()
    res = SVC.resolve_conflict(conn, False, cid, "KEEP_OFFLINE")
    assert res.get("reopened") is True   # never blind-applies an old resolution
    assert S.read_row_full(conn, False, "sync-1")["price"] == 7777
    conn.close()


def test_concurrent_revisions_are_monotonic():
    import threading
    d, path = tempfile.mkdtemp(prefix="syncv2_conc_"), os.path.join(
        tempfile.mkdtemp(prefix="syncv2_conc_"), "srv.db")
    d = tempfile.mkdtemp(prefix="syncv2_conc_")
    path = os.path.join(d, "srv.db")
    H.make_db(path).close()
    results = []
    lock = threading.Lock()

    def worker(i):
        conn = sqlite3.connect(path)
        try:
            r = SVC.apply_ops(conn, False,
                              [_op("sync-%d" % i, H.base_business(name="W%d" % i))])
            with lock:
                results.append(r["results"][0]["server_rev_after"])
        finally:
            conn.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    revs = [r for r in results if isinstance(r, int)]
    assert len(revs) == 6
    assert len(set(revs)) == 6               # no duplicate revision assignment
    assert revs == sorted(revs)
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT value FROM sync_sequence WHERE id=1").fetchone()[0] == 6
    conn.close()


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
    print("\n%s" % ("ALL SYNCV2 SERVER TESTS PASSED" if failed == 0 else "%d FAILED" % failed))
    sys.exit(1 if failed else 0)



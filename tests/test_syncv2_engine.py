"""SyncEngine end-to-end tests on isolated twin SQLite databases."""
import os
import sqlite3
import sys
import tempfile
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests"))

from syncv2 import protocol as P
from syncv2 import merge as M
from syncv2 import server as SVC
from syncv2 import store as S
from syncv2.engine import SyncEngine
import syncv2_helpers as H


def _env():
    d = tempfile.mkdtemp(prefix="syncv2_engine_")
    off = H.make_db(os.path.join(d, "off.db"))
    srv = H.make_db(os.path.join(d, "srv.db"))
    adapter = H.ServerAdapter(srv)
    eng = SyncEngine(off, False, adapter, lock_path=os.path.join(d, "sync.lock"))
    return d, off, srv, adapter, eng


def _equal(off, srv, sync_id):
    import json as _json
    a = S.read_row_full(off, False, sync_id)
    b = S.read_row_full(srv, False, sync_id)
    if a is None or b is None:
        return a is None and b is None
    for f in P.BUSINESS_FIELDS:
        if not M.values_equal(f, a.get(f), b.get(f)):
            return False
    ja, jb = _json.loads(a.get("base_json") or "{}"), _json.loads(b.get("base_json") or "{}")
    if set(ja) != set(jb):
        return False
    for f in P.BUSINESS_FIELDS:
        if f in ja and not M.values_equal(f, ja.get(f), jb.get(f)):
            return False
    return ((a.get("deleted_at") or None) == (b.get("deleted_at") or None)
            and int(a.get("row_rev") or 0) == int(b.get("row_rev") or 0))


def test_outbox_transaction_atomicity():
    d, off, srv, adapter, eng = _env()
    biz = H.base_business(name="NEW")
    op = eng.begin_local_change("sync-new", biz)
    assert op
    assert S.read_row_full(off, False, "sync-new")["name"] == "NEW"
    assert len(S.outbox_rows(off, False)) == 1
    bad = dict(biz)
    bad["not_a_column"] = 1
    try:
        eng.begin_local_change("sync-bad", bad)
        raise AssertionError("expected failure")
    except Exception:
        pass
    assert S.read_row_full(off, False, "sync-bad") is None
    assert len(S.outbox_rows(off, False)) == 1
    off.close()
    srv.close()


def test_coalescing_three_edits_sends_one_op():
    d, off, srv, adapter, eng = _env()
    biz = H.base_business()
    H.make_baselined_pair(off, srv, "s1", biz)
    eng.begin_local_change("s1", H.base_business(name="A"))
    eng.begin_local_change("s1", H.base_business(name="B"))
    eng.begin_local_change("s1", H.base_business(name="C"))
    assert len(S.outbox_rows(off, False)) == 3
    res = eng.run_once()
    assert res.status == P.SESSION_SUCCESS
    statuses = {r[0]: r[1] for r in off.execute(
        "SELECT status, COUNT(*) FROM outbox GROUP BY status").fetchall()}
    assert statuses.get("applied") == 1 and statuses.get("superseded") == 2
    assert S.read_row_full(srv, False, "s1")["name"] == "C"
    assert _equal(off, srv, "s1")
    off.close()
    srv.close()


def test_offline_edit_syncs_to_server():
    d, off, srv, adapter, eng = _env()
    biz = H.base_business()
    H.make_baselined_pair(off, srv, "s1", biz)
    eng.begin_local_change("s1", H.base_business(name="OFF-EDITED", phone="91"))
    res = eng.run_once()
    assert res.status == P.SESSION_SUCCESS
    assert res.pushed == 1
    assert S.read_row_full(srv, False, "s1")["name"] == "OFF-EDITED"
    assert _equal(off, srv, "s1")
    assert S.read_row_full(off, False, "s1")["row_rev"] == 0
    assert eng.get_status()["outbox"].get("applied") == 1
    off.close()
    srv.close()


def test_server_change_pulled_to_offline():
    d, off, srv, adapter, eng = _env()
    biz = H.base_business()
    H.make_baselined_pair(off, srv, "s1", biz)
    edited = dict(biz)
    edited["name"] = "SERVER-EDITED"
    SVC.apply_ops(srv, False,
                  [{"op_id": str(uuid.uuid4()), "sync_id": "s1", "op_type": "upsert",
                    "payload": edited, "base": biz, "base_rev": 0}])
    res = eng.run_once()
    assert res.status == P.SESSION_SUCCESS
    assert res.pulled >= 1
    assert S.read_row_full(off, False, "s1")["name"] == "SERVER-EDITED"
    assert _equal(off, srv, "s1")
    off.close()
    srv.close()


def test_incremental_pull_no_duplicate_application():
    d, off, srv, adapter, eng = _env()
    biz = H.base_business()
    H.make_baselined_pair(off, srv, "s1", biz)
    edited = dict(biz)
    edited["name"] = "V1"
    SVC.apply_ops(srv, False,
                  [{"op_id": str(uuid.uuid4()), "sync_id": "s1", "op_type": "upsert",
                    "payload": edited, "base": biz, "base_rev": 0}])
    r1 = eng.run_once()
    assert r1.pulled >= 1
    row1 = S.read_row_full(off, False, "s1")
    assert row1["name"] == "V1"
    r2 = eng.run_once()
    assert r2.pulled == 0 and r2.pushed == 0
    assert S.read_row_full(off, False, "s1")["server_rev"] == row1["server_rev"]
    off.close()
    srv.close()


def test_offline_delete_syncs_tombstone():
    d, off, srv, adapter, eng = _env()
    biz = H.base_business()
    H.make_baselined_pair(off, srv, "s1", biz)
    eng.delete_local("s1")
    res = eng.run_once()
    assert res.status == P.SESSION_SUCCESS
    assert S.read_row_full(srv, False, "s1")["deleted_at"] is not None
    assert S.read_row_full(off, False, "s1")["deleted_at"] is not None
    assert _equal(off, srv, "s1")
    res2 = eng.run_once()
    assert res2.status == P.SESSION_SUCCESS and res2.pushed == 0
    assert S.read_row_full(off, False, "s1")["deleted_at"] is not None
    off.close()
    srv.close()


def test_network_failure_offline_safe_then_recovers():
    d, off, srv, adapter, eng = _env()
    biz = H.base_business()
    H.make_baselined_pair(off, srv, "s1", biz)
    eng.begin_local_change("s1", H.base_business(name="OFFLINE-EDIT"))
    adapter._faults["apply_ops"] = (1, "connection reset by peer")
    res = eng.run_once()
    assert res.status == P.SESSION_OFFLINE
    assert S.read_row_full(off, False, "s1")["name"] == "OFFLINE-EDIT"  # local intact
    assert S.read_row_full(srv, False, "s1")["name"] == "AA"            # server untouched
    assert eng.get_status()["outbox"].get("pending") == 1
    adapter._faults.clear()
    res2 = eng.run_once()
    assert res2.status == P.SESSION_SUCCESS
    assert S.read_row_full(srv, False, "s1")["name"] == "OFFLINE-EDIT"
    assert _equal(off, srv, "s1")
    off.close()
    srv.close()


def test_crash_after_server_commit_is_safe():
    d, off, srv, adapter, eng = _env()
    biz = H.base_business()
    H.make_baselined_pair(off, srv, "s1", biz)
    edited = H.base_business(name="CRASH-TEST")
    op_id = eng.begin_local_change("s1", edited)
    payload = S.outbox_rows(off, False)[0]["payload"]
    direct = {"op_id": op_id, "sync_id": "s1", "op_type": "upsert",
              "payload": payload["payload"], "base": payload["base"],
              "base_rev": payload["base_rev"]}
    SVC.apply_ops(srv, False, [direct])
    assert S.read_row_full(srv, False, "s1")["name"] == "CRASH-TEST"
    res = eng.run_once()          # client retries the same op_id
    assert res.status == P.SESSION_SUCCESS
    assert S.read_row_full(srv, False, "s1")["name"] == "CRASH-TEST"
    assert S.fetch_all(srv, False, "SELECT COUNT(*) FROM applied_ops")[0][0] == 1
    assert _equal(off, srv, "s1")
    off.close()
    srv.close()


def test_partial_batch_recovers_without_resending_applied():
    d, off, srv, adapter, eng = _env()
    biz = H.base_business()
    for s in ("s1", "s2", "s3"):
        H.make_baselined_pair(off, srv, s, biz)
    eng.delete_local("s1")
    eng.delete_local("s2")
    eng.delete_local("s3")
    # Make op #2 fail on the server: its target row does not exist there.
    srv.execute("DELETE FROM records WHERE sync_id='s2'")
    srv.commit()
    res = eng.run_once()
    assert res.status == P.SESSION_NEEDS_ATTENTION
    assert S.read_row_full(srv, False, "s1")["deleted_at"] is not None  # op1 succeeded
    assert S.read_row_full(srv, False, "s3")["deleted_at"] is None      # op3 not processed
    st = eng.get_status()
    assert st["outbox"].get("failed") == 1                              # op2 permanent
    assert st["outbox"].get("pending") == 1                             # op3 recoverable
    # Repair: give op2 a valid target again, then let the next run finish.
    H.insert_row(srv, "s2", biz)
    off.execute("UPDATE outbox SET status='pending' WHERE sync_id='s2'")
    off.commit()
    res2 = eng.run_once()
    assert res2.status == P.SESSION_SUCCESS
    assert S.read_row_full(srv, False, "s2")["deleted_at"] is not None
    assert S.read_row_full(srv, False, "s3")["deleted_at"] is not None
    n1 = S.fetch_all(srv, False,
                     "SELECT COUNT(*) FROM applied_ops WHERE sync_id='s1'")[0][0]
    assert n1 == 1          # op1 was NOT resent
    assert _equal(off, srv, "s1") and _equal(off, srv, "s2") and _equal(off, srv, "s3")
    off.close()
    srv.close()


def test_single_flight_busy():
    d, off, srv, adapter, eng = _env()
    assert eng._flight.acquire(blocking=False) is True
    res = eng.run_once()
    assert res.status == P.SESSION_BUSY
    eng._flight.release()
    off.close()
    srv.close()


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
    print("\n%s" % ("ALL SYNCV2 ENGINE TESTS PASSED" if failed == 0 else "%d FAILED" % failed))
    sys.exit(1 if failed else 0)



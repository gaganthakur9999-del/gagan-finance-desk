"""Focused regression tests for the Phase-4 correction pass (audit defects).

All databases are isolated temp SQLite twins. Never touches production.
"""
import json
import os
import sqlite3
import sys
import tempfile
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests"))

from syncv2 import protocol as P
from syncv2 import merge as M
from syncv2 import retry as R
from syncv2 import server as SVC
from syncv2 import store as S
from syncv2.engine import SyncEngine
import syncv2_helpers as H


def _env():
    d = tempfile.mkdtemp(prefix="syncv2_corr_")
    off = H.make_db(os.path.join(d, "off.db"))
    srv = H.make_db(os.path.join(d, "srv.db"))
    adapter = H.ServerAdapter(srv)
    eng = SyncEngine(off, False, adapter, lock_path=os.path.join(d, "sync.lock"))
    return d, off, srv, adapter, eng


def _op(sync_id, business, base=None, op_id=None, op_type=P.OP_UPSERT, base_rev=0):
    return {"op_id": op_id or str(uuid.uuid4()), "sync_id": sync_id,
            "op_type": op_type, "payload": dict(business),
            "base": base or {f: None for f in P.BUSINESS_FIELDS}, "base_rev": base_rev}


def _bases_equal(off, srv, sid):
    ja = json.loads(S.read_row_full(off, False, sid)["base_json"] or "{}")
    jb = json.loads(S.read_row_full(srv, False, sid)["base_json"] or "{}")
    if set(ja) != set(jb):
        return False
    return all(M.values_equal(f, ja.get(f), jb.get(f)) for f in P.BUSINESS_FIELDS)


def _rows_equal(off, srv, sid):
    a = S.read_row_full(off, False, sid)
    b = S.read_row_full(srv, False, sid)
    if a is None or b is None:
        return a is None and b is None
    return (all(M.values_equal(f, a.get(f), b.get(f)) for f in P.BUSINESS_FIELDS)
            and (a.get("deleted_at") or None) == (b.get("deleted_at") or None)
            and int(a.get("row_rev") or 0) == int(b.get("row_rev") or 0))


def _server_change(srv, sync_id, business, base, base_rev=0):
    SVC.apply_ops(srv, False,
                  [_op(sync_id, business, base=base, base_rev=base_rev)])


def _make_server_env():
    d = tempfile.mkdtemp(prefix="syncv2_corr_")
    return d, H.make_db(os.path.join(d, "srv.db"))


def test_resolution_writes_resolved_base_and_second_resolution_works():
    d, conn = _make_server_env()
    base = H.base_business()
    H.insert_row(conn, "s1", base)
    SVC.apply_ops(conn, False, [_op("s1", H.base_business(price=1111), base=base,
                                    base_rev=0)])
    SVC.apply_ops(conn, False, [_op("s1", H.base_business(price=2222), base=base,
                                    base_rev=0)])
    cid = SVC.open_conflicts_for(conn, False, "s1")[0]["id"]
    res = SVC.resolve_conflict(conn, False, cid, "KEEP_OFFLINE")
    assert res.get("applied_revision")
    row = S.read_row_full(conn, False, "s1")
    assert float(row["price"]) == 2222
    jb = json.loads(row["base_json"])
    assert float(jb["price"]) == 2222   # base = RESOLVED snapshot
    # Second legitimate conflict on the same record must resolve (not reopen-loop).
    SVC.apply_ops(conn, False, [_op("s1", H.base_business(price=3333), base=base,
                                    base_rev=0)])
    cid2 = SVC.open_conflicts_for(conn, False, "s1")[0]["id"]
    res2 = SVC.resolve_conflict(conn, False, cid2, "KEEP_ONLINE")
    assert res2.get("applied_revision") is not None
    assert float(S.read_row_full(conn, False, "s1")["price"]) == 2222
    conn.close()


def test_keep_offline_engine_convergence():
    d, off, srv, adapter, eng = _env()
    base = H.base_business()
    H.make_baselined_pair(off, srv, "s1", base)
    _server_change(srv, "s1", H.base_business(name="ON-EDIT"), base)
    eng.begin_local_change("s1", H.base_business(name="OFF-EDIT", price=999))
    res = eng.run_once()
    assert res.status == P.SESSION_CONFLICT
    cid = SVC.open_conflicts_for(srv, False, "s1")[0]["id"]
    out = eng.resolve_conflict(cid, "KEEP_OFFLINE")
    assert out.get("converged") is True
    assert S.read_row_full(off, False, "s1")["name"] == "OFF-EDIT"
    assert _rows_equal(off, srv, "s1") and _bases_equal(off, srv, "s1")
    assert S.read_row_full(off, False, "s1")["row_rev"] == 0
    r2 = eng.run_once()
    assert r2.status == P.SESSION_SUCCESS and r2.pushed == 0
    off.close()
    srv.close()


def test_keep_online_engine_convergence():
    d, off, srv, adapter, eng = _env()
    base = H.base_business()
    H.make_baselined_pair(off, srv, "s1", base)
    _server_change(srv, "s1", H.base_business(name="ON-EDIT"), base)
    eng.begin_local_change("s1", H.base_business(name="OFF-EDIT"))
    res = eng.run_once()
    assert res.status == P.SESSION_CONFLICT
    cid = SVC.open_conflicts_for(srv, False, "s1")[0]["id"]
    out = eng.resolve_conflict(cid, "KEEP_ONLINE")
    assert out.get("converged") is True
    assert S.read_row_full(off, False, "s1")["name"] == "ON-EDIT"  # loser adopts
    assert _rows_equal(off, srv, "s1") and _bases_equal(off, srv, "s1")
    assert S.read_row_full(off, False, "s1")["row_rev"] == 0
    r2 = eng.run_once()
    assert r2.status == P.SESSION_SUCCESS and r2.pushed == 0
    off.close()
    srv.close()


def test_merge_engine_convergence():
    d, off, srv, adapter, eng = _env()
    base = H.base_business()
    H.make_baselined_pair(off, srv, "s1", base)
    _server_change(srv, "s1", H.base_business(name="ON-EDIT"), base)
    eng.begin_local_change("s1", H.base_business(name="OFF-EDIT"))
    eng.run_once()
    cid = SVC.open_conflicts_for(srv, False, "s1")[0]["id"]
    out = eng.resolve_conflict(cid, "MERGE", {"value": "MERGED-NAME"})
    assert out.get("converged") is True
    assert S.read_row_full(off, False, "s1")["name"] == "MERGED-NAME"
    assert S.read_row_full(srv, False, "s1")["name"] == "MERGED-NAME"
    assert _rows_equal(off, srv, "s1") and _bases_equal(off, srv, "s1")
    off.close()
    srv.close()


def test_multi_field_conflicts_preserved_and_converged():
    d, off, srv, adapter, eng = _env()
    base = H.base_business()
    H.make_baselined_pair(off, srv, "s1", base)
    _server_change(srv, "s1", H.base_business(name="ONN", price=2222), base)
    eng.begin_local_change("s1", H.base_business(name="OFFN", price=3333,
                                                 phone="OFFP"))
    res = eng.run_once()
    assert res.status == P.SESSION_CONFLICT
    openc = SVC.open_conflicts_for(srv, False, "s1")
    kinds = {c["kind"] for c in openc}
    assert {"field", "financial"} <= kinds          # BOTH conflicts preserved
    fields = {c["field_name"] for c in openc}
    assert {"name", "price"} <= fields
    # Resolving ONE field must not converge/discard the other yet.
    name_cid = [c for c in openc if c["field_name"] == "name"][0]["id"]
    out1 = eng.resolve_conflict(name_cid, "KEEP_ONLINE")
    assert out1.get("converged") is False and out1.get("pending_syncs") == ["s1"]
    assert S.read_row_full(off, False, "s1")["name"] == "OFFN"  # local untouched
    price_cid = [c for c in SVC.open_conflicts_for(srv, False, "s1")
                 if c["field_name"] == "price"][0]["id"]
    out2 = eng.resolve_conflict(price_cid, "KEEP_ONLINE")
    assert out2.get("converged") is True
    assert S.read_row_full(off, False, "s1")["name"] == "ONN"
    assert float(S.read_row_full(off, False, "s1")["price"]) == 2222
    assert S.read_row_full(off, False, "s1")["phone"] == "OFFP"  # offline-only survives
    assert _rows_equal(off, srv, "s1") and _bases_equal(off, srv, "s1")
    assert SVC.open_conflicts_for(srv, False, "s1") == []
    r2 = eng.run_once()
    assert r2.status == P.SESSION_SUCCESS and r2.pushed == 0
    off.close()
    srv.close()


def test_grouped_sr_conflict_single_and_resolution_converges():
    d, off, srv, adapter, eng = _env()
    month = "AUGUST_2026"
    sids = ["sA", "sB", "sC"]
    for sr, sid in enumerate(sids, start=1):
        biz = H.base_business(sr_no=sr, bid_date="05-08-2026", name=sid)
        H.make_baselined_pair(off, srv, sid, biz)
    offline_order = {"sB": 1, "sC": 2, "sA": 3}
    for sid in sids:
        eng.begin_local_change(sid, H.base_business(
            sr_no=offline_order[sid], bid_date="05-08-2026", name=sid))
    online_order = {"sC": 1, "sA": 2, "sB": 3}
    for sid in sids:
        _server_change(srv, sid, H.base_business(
            sr_no=online_order[sid], bid_date="05-08-2026", name=sid),
            H.base_business(sr_no=int(sids.index(sid)) + 1, bid_date="05-08-2026",
                            name=sid))
    res = eng.run_once()
    # All three ops are parked under ONE grouped month conflict (not N conflicts).
    sr_open = [c for c in SVC.open_conflicts_for(srv, False, "sA")
               if c["kind"] == P.CONFLICT_SR_ORDER]
    assert len(sr_open) == 1 and sr_open[0]["month"] == month
    assert len(SVC.open_conflicts_for(srv, False, "sA")) == 1
    assert S.read_row_full(srv, False, "sA")["sr_no"] == 2  # online order unchanged
    gcid = sr_open[0]["id"]
    out = eng.resolve_conflict(gcid, "KEEP_OFFLINE")
    assert out.get("sr_resolution") is True and out.get("converged") is True
    # Server now reflects the chosen (offline) ordering.
    srv_order = [S.read_row_full(srv, False, sid)["sr_no"] for sid in sids]
    assert srv_order == [3, 1, 2]     # sA=3, sB=1, sC=2 (KEEP_OFFLINE ordering)
    for sid in sids:
        assert _rows_equal(off, srv, sid) and _bases_equal(off, srv, sid)
    assert SVC.open_conflicts_for(srv, False, "sA") == []
    r2 = eng.run_once()
    assert r2.status == P.SESSION_SUCCESS and r2.pushed == 0
    off.close()
    srv.close()



def test_conflict_id_is_db_generated_integer():
    """The conflicts.id column is integer (SERIAL on PG); conflict insertion must
    never depend on UUID-as-id. Exercised on the SQLite compatibility path."""
    d, conn = _make_server_env()
    base = H.base_business()
    H.insert_row(conn, "s1", base)
    SVC.apply_ops(conn, False, [_op("s1", H.base_business(price=1111), base=base)])
    SVC.apply_ops(conn, False, [_op("s1", H.base_business(price=2222), base=base)])
    cid = SVC.open_conflicts_for(conn, False, "s1")[0]["id"]
    assert isinstance(cid, int)              # not a text UUID
    coltype = [r for r in conn.execute("PRAGMA table_info(conflicts)")
               if r[1] == "id"][0][2]
    assert "INTEGER" in coltype
    res = SVC.resolve_conflict(conn, False, cid, "KEEP_ONLINE")
    assert res.get("applied_revision")       # lookup/resolution works with int id
    conn.close()


def test_file_lock_single_flight_returns_busy_and_releases():
    import time as _time
    d, off, srv, adapter, eng = _env()
    lock = os.path.join(d, "manual.lock")
    eng2 = SyncEngine(off, False, H.ServerAdapter(srv), lock_path=lock)
    with open(lock, "w") as f:
        f.write("pid=999999 ts=%f" % _time.time())
    res = eng2.run_once()
    assert res.status == P.SESSION_BUSY
    os.unlink(lock)
    res2 = eng2.run_once()
    assert res2.status == P.SESSION_SUCCESS
    assert not os.path.exists(lock)          # released after success
    off.close()
    srv.close()


def test_retry_classification_tokenised():
    for msg in ("connection timed out", "Connection refused", "network is down",
                "temporary database unavailable", "HTTP/1.1 503 Service Unavailable",
                "connection reset by peer"):
        assert R.is_permanent(OSError(msg)) is False, msg
    for exc in (ValueError("unknown sync_id s-5"),
                RuntimeError("server row 5 changed concurrently"),
                ValueError("invalid payload for sync-55"),
                ValueError("impossible state at revision 5")):
        assert R.is_permanent(exc) is True, exc
    assert R.classify_error(ValueError("malformed operation")) == R.ERR_PERMANENT


def test_stale_upsert_against_tombstone_conflicts_no_resurrection():
    d, off, srv, adapter, eng = _env()
    base = H.base_business(name="ORIGINAL")
    H.make_baselined_pair(off, srv, "s1", base)
    del_payload = dict(base)
    del_payload["deleted_at"] = S.now_utc()
    SVC.apply_ops(srv, False, [_op("s1", del_payload, base=base,
                                   op_type=P.OP_DELETE)])
    eng.begin_local_change("s1", H.base_business(name="STALE-EDIT"))
    res = eng.run_once()
    assert res.status == P.SESSION_CONFLICT
    row = S.read_row_full(srv, False, "s1")
    assert row["deleted_at"] is not None and row["name"] == "ORIGINAL"  # no mutation
    openc = SVC.open_conflicts_for(srv, False, "s1")
    assert any(c["kind"] == P.CONFLICT_DELETE_EDIT for c in openc)
    cid = [c for c in openc if c["kind"] == P.CONFLICT_DELETE_EDIT][0]["id"]
    out = eng.resolve_conflict(cid, "KEEP_ONLINE")
    assert out.get("converged") is True
    assert S.read_row_full(srv, False, "s1")["deleted_at"] is not None
    assert S.read_row_full(off, False, "s1")["deleted_at"] is not None
    assert _rows_equal(off, srv, "s1")
    r2 = eng.run_once()
    assert r2.status == P.SESSION_SUCCESS and r2.pushed == 0
    off.close()
    srv.close()


def test_blocked_op_adopts_after_remote_resolution():
    d, off, srv, adapter, eng = _env()
    base = H.base_business()
    H.make_baselined_pair(off, srv, "s1", base)
    _server_change(srv, "s1", H.base_business(name="ON-EDIT"), base)
    eng.begin_local_change("s1", H.base_business(name="OFF-EDIT"))
    res = eng.run_once()
    assert res.status == P.SESSION_CONFLICT
    assert S.outbox_rows(off, False, statuses=(P.OUTBOX_BLOCKED,))
    cid = SVC.open_conflicts_for(srv, False, "s1")[0]["id"]
    SVC.resolve_conflict(srv, False, cid, "KEEP_ONLINE")
    r2 = eng.run_once()      # blocked op retired; loser adopts resolved state
    assert r2.status == P.SESSION_SUCCESS
    assert S.outbox_rows(off, False, statuses=(P.OUTBOX_BLOCKED,)) == []
    assert S.read_row_full(off, False, "s1")["name"] == "ON-EDIT"
    assert _rows_equal(off, srv, "s1")
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
    print("\n%s" % ("ALL SYNCV2 CORRECTION TESTS PASSED" if failed == 0 else "%d FAILED" % failed))
    sys.exit(1 if failed else 0)


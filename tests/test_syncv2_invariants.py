"""Invariant/property tests + retry helper tests for syncv2."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests"))

from syncv2 import protocol as P
from syncv2 import retry as R
from syncv2 import server as SVC
from syncv2 import store as S
from syncv2.engine import SyncEngine
import syncv2_helpers as H


def _converged(off, srv, sync_ids):
    for sid in sync_ids:
        a = S.read_row_full(off, False, sid)
        b = S.read_row_full(srv, False, sid)
        if a is None or b is None:
            return False
        for f in P.BUSINESS_FIELDS:
            if not M.values_equal(f, a.get(f), b.get(f)):
                return False
    return True


import syncv2.merge as M  # noqa: E402


def test_retry_backoff_increases_and_classification():
    delays = [R.next_retry_delay(a, base_seconds=1, cap_seconds=8, jitter=0,
                                 rng=__import__("random").Random(0))
              for a in range(4)]
    assert all(delays[i] < delays[i + 1] for i in range(3))   # 1,2,4,8 capped
    assert R.is_permanent(ValueError("malformed payload")) is True
    assert R.is_permanent(OSError("connection reset")) is False
    assert R.is_permanent(OSError("timed out")) is False
    assert R.is_permanent(ValueError("impossible state")) is True


def test_invariant_after_successful_sync_business_and_base_agree():
    import json as _json
    d = tempfile.mkdtemp(prefix="inv_")
    off = H.make_db(os.path.join(d, "off.db"))
    srv = H.make_db(os.path.join(d, "srv.db"))
    eng = SyncEngine(off, False, H.ServerAdapter(srv))
    for i in range(5):
        H.make_baselined_pair(off, srv, "s%d" % i, H.base_business(name="R%d" % i))
    # Offline edits to 3 rows + a server-side change to another.
    eng.begin_local_change("s0", H.base_business(name="OFF0", price=500))
    eng.begin_local_change("s1", H.base_business(name="OFF1"))
    eng.begin_local_change("s2", H.base_business(name="OFF2"))
    edited = dict(H.base_business(name="SRV3"))
    edited["phone"] = "9-SRV"
    SVC.apply_ops(srv, False, [{"op_id": "fixed-op-3", "sync_id": "s3",
                                "op_type": "upsert", "payload": edited,
                                "base": H.base_business(name="R3"),
                                "base_rev": 0}])
    res = eng.run_once()
    assert res.status == P.SESSION_SUCCESS
    assert _converged(off, srv, ["s0", "s1", "s2", "s3", "s4"])
    # Invariant: same sync_id -> same resolved business state -> same base_json.
    for sid in ("s0", "s3"):
        ja = _json.loads(S.read_row_full(off, False, sid)["base_json"])
        jb = _json.loads(S.read_row_full(srv, False, sid)["base_json"])
        for f in P.BUSINESS_FIELDS:
            assert M.values_equal(f, ja[f], jb[f])
    assert S.read_row_full(srv, False, "s3")["phone"] == "9-SRV"  # server edit kept
    off.close()
    srv.close()


def test_duplicate_op_same_result_single_application():
    d = tempfile.mkdtemp(prefix="inv2_")
    srv = H.make_db(os.path.join(d, "srv.db"))
    biz = H.base_business()
    H.insert_row(srv, "s1", biz)
    op = {"op_id": "dup-op-1", "sync_id": "s1", "op_type": "upsert",
          "payload": dict(H.base_business(name="ONCE")), "base": dict(biz),
          "base_rev": 0}
    r1 = SVC.apply_ops(srv, False, [dict(op)])
    r2 = SVC.apply_ops(srv, False, [dict(op)])
    assert r1["results"][0]["server_rev_after"] == r2["results"][0]["server_rev_after"]
    assert S.read_row_full(srv, False, "s1")["name"] == "ONCE"
    assert S.fetch_all(srv, False,
                       "SELECT COUNT(*) FROM applied_ops WHERE op_id='dup-op-1'")[0][0] == 1
    srv.close()


def test_network_unavailable_local_op_stays_usable():
    d = tempfile.mkdtemp(prefix="inv3_")
    off = H.make_db(os.path.join(d, "off.db"))
    srv = H.make_db(os.path.join(d, "srv.db"))
    adapter = H.ServerAdapter(srv)
    adapter._faults["apply_ops"] = (1, "network is down")
    eng = SyncEngine(off, False, adapter)
    H.make_baselined_pair(off, srv, "s1", H.base_business())
    eng.begin_local_change("s1", H.base_business(name="LOCAL-STILL-WORKS"))
    # The offline application layer must never be blocked by the network.
    assert S.read_row_full(off, False, "s1")["name"] == "LOCAL-STILL-WORKS"
    res = eng.run_once()
    assert res.status == P.SESSION_OFFLINE
    assert S.read_row_full(off, False, "s1")["name"] == "LOCAL-STILL-WORKS"
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
    print("\n%s" % ("ALL SYNCV2 INVARIANT TESTS PASSED" if failed == 0 else "%d FAILED" % failed))
    sys.exit(1 if failed else 0)

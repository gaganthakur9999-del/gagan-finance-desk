# -*- coding: utf-8 -*-
"""Phase 7C - controlled NULL/blank sync_id ADOPTION design tests.

Synthetic twin SQLite databases only. The adoption procedure is backend-agnostic;
when a real isolated PostgreSQL becomes available the same functions run with
is_pg=True. NEVER points at production.

Run standalone:  python tests/test_syncv2_phase7c_adoption.py
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

import syncv2_helpers as H  # noqa: E402
from syncv2 import protocol as P  # noqa: E402
from syncv2 import store as S  # noqa: E402

sys.path.insert(0, os.path.join(PROJECT, "scripts", "sync"))
import adoption_design as AD  # noqa: E402

_TMP = tempfile.mkdtemp(prefix="gfd_p7c_")
_counter = {"n": 0}


def _biz(**kw):
    row = {f: None for f in P.BUSINESS_FIELDS}
    row.update({
        "sr_no": 5, "bid_date": "02-09-2026", "invoice_no": "260906",
        "name": "Tarun Kumar", "xcell": "", "product": "PROD", "serial_no":
        "605PRBA130421", "price": 26000.0, "emi": 0.0, "di": 0.0,
        "bid": "B436420370", "dp_taken": 0.0, "scheme": "",
        "actual_product": "", "given_prod_price": 0.0, "phone": "7018273919",
        "alt_phone": "", "month": "SEPTEMBER_2026", "remarks": "",
    })
    row.update(kw)
    return row


def _env():
    """Two synthetic legacy replicas: schema present, rows WITHOUT sync_id."""
    _counter["n"] += 1
    d = os.path.join(_TMP, "env_%d" % _counter["n"])
    os.makedirs(d, exist_ok=True)
    off = H.make_db(os.path.join(d, "off.db"))
    onl = H.make_db(os.path.join(d, "onl.db"))
    return off, onl, d


def _insert_legacy_null(conn, business):
    b = {f: business.get(f) for f in P.BUSINESS_FIELDS}
    cols = sorted(b) + ["created_at", "updated_at"]
    marks = ",".join("?" * len(cols))
    params = [b[f] for f in sorted(b)] + [S.now_utc(), S.now_utc()]
    conn.execute("INSERT INTO records (%s) VALUES (%s)" % (",".join(cols), marks),
                 params)
    conn.commit()
    cur = conn.execute("SELECT id FROM records WHERE invoice_no=? "
                       "ORDER BY id DESC LIMIT 1", (b["invoice_no"],))
    return cur.fetchone()[0]


def _read(conn, rid):
    conn.row_factory = sqlite3.Row
    return dict(conn.execute("SELECT * FROM records WHERE id=?", (rid,)).fetchone())


# ---------------------------------------------------------------------------
# ADOPTION PROCEDURE
# ---------------------------------------------------------------------------
def test_adopt_establishes_shared_identity_with_no_outbox_or_conflict():
    off, onl, _ = _env()
    biz = _biz()
    off_id = _insert_legacy_null(off, biz)
    on_id = _insert_legacy_null(onl, biz)
    sid = str(uuid.uuid4())
    res = AD.adopt_pair(off, onl, off_id, on_id, sid)
    assert res["result"] == "adopted"
    off_row = _read(off, off_id)
    on_row = _read(onl, on_id)
    assert off_row["sync_id"] == sid and on_row["sync_id"] == sid
    # base_json identical, Offline-authoritative business snapshot
    assert off_row["base_json"] == on_row["base_json"]
    base = json.loads(off_row["base_json"])
    assert base["serial_no"] == "605PRBA130421"
    assert base["bid"] == "B436420370"
    # bootstrap semantics: revs 0, tombstone false
    assert off_row["server_rev"] == 0 and off_row["row_rev"] == 0
    assert on_row["server_rev"] == 0 and on_row["row_rev"] == 0
    assert off_row["deleted_at"] is None and on_row["deleted_at"] is None
    # no outbox row / conflict / applied_ops created
    assert S.fetch_all(off, False, "SELECT COUNT(*) FROM outbox")[0][0] == 0
    assert S.fetch_all(onl, False, "SELECT COUNT(*) FROM outbox")[0][0] == 0
    assert S.fetch_all(onl, False,
                       "SELECT COUNT(*) FROM conflicts")[0][0] == 0
    assert S.fetch_all(onl, False,
                       "SELECT COUNT(*) FROM applied_ops")[0][0] == 0
    # exactly one shared identity per replica, no duplicate row created
    for conn in (off, onl):
        n = S.fetch_all(conn, False,
                        "SELECT COUNT(*) FROM records WHERE sync_id=?",
                        (sid,))[0][0]
        assert n == 1
    off.close()
    onl.close()


def test_adopt_is_idempotent_and_rerun_safe():
    off, onl, _ = _env()
    biz = _biz()
    off_id = _insert_legacy_null(off, biz)
    on_id = _insert_legacy_null(onl, biz)
    sid = str(uuid.uuid4())
    AD.adopt_pair(off, onl, off_id, on_id, sid)
    res = AD.adopt_pair(off, onl, off_id, on_id, sid)
    assert res["result"] == "noop"
    assert _read(off, off_id)["sync_id"] == sid
    assert _read(onl, on_id)["sync_id"] == sid
    off.close()
    onl.close()


def test_adopt_half_state_recovers_on_rerun_with_same_uuid():
    off, onl, _ = _env()
    biz = _biz()
    off_id = _insert_legacy_null(off, biz)
    on_id = _insert_legacy_null(onl, biz)
    sid = str(uuid.uuid4())
    orig = AD._apply_side
    calls = {"n": 0}

    def boom(conn, is_pg, record_id, sync_id, base_json):
        calls["n"] += 1
        if calls["n"] == 2:      # online side fails after offline committed
            raise RuntimeError("injected online write failure")
        return orig(conn, is_pg, record_id, sync_id, base_json)

    AD._apply_side = boom
    try:
        try:
            AD.adopt_pair(off, onl, off_id, on_id, sid)
            raise AssertionError("expected injected failure")
        except RuntimeError as exc:
            assert "injected" in str(exc)
    finally:
        AD._apply_side = orig
    # offline half applied, online still NULL -> recoverable half state
    assert _read(off, off_id)["sync_id"] == sid
    assert not _read(onl, on_id)["sync_id"]
    res = AD.adopt_pair(off, onl, off_id, on_id, sid)
    assert res["result"] == "adopted"
    assert _read(onl, on_id)["sync_id"] == sid
    assert _read(off, off_id)["base_json"] == _read(onl, on_id)["base_json"]
    off.close()
    onl.close()


def test_adopt_refuses_different_record():
    off, onl, _ = _env()
    off_id = _insert_legacy_null(off, _biz(name="Tarun Kumar"))
    on_id = _insert_legacy_null(onl, _biz(name="SOMEONE ELSE", phone="999"))
    try:
        AD.adopt_pair(off, onl, off_id, on_id, str(uuid.uuid4()))
        raise AssertionError("adopt_pair should refuse a different record")
    except ValueError as exc:
        assert "refuses non-identical pair" in str(exc)
    off.close()
    onl.close()


def test_adopt_refuses_conflicting_existing_sync_id():
    off, onl, _ = _env()
    biz = _biz()
    off_id = _insert_legacy_null(off, biz)
    on_id = _insert_legacy_null(onl, biz)
    sid = str(uuid.uuid4())
    AD.adopt_pair(off, onl, off_id, on_id, sid)
    try:
        AD.adopt_pair(off, onl, off_id, on_id, str(uuid.uuid4()))
        raise AssertionError("adopt_pair should refuse a conflicting uuid")
    except ValueError as exc:
        assert "conflicting sync_id" in str(exc)
    off.close()
    onl.close()


def test_adopt_offline_failure_rolls_back_cleanly():
    off, onl, _ = _env()
    biz = _biz()
    off_id = _insert_legacy_null(off, biz)
    on_id = _insert_legacy_null(onl, biz)
    orig = S.execute

    def bad(conn, is_pg, sql, params=()):
        if "UPDATE records SET sync_id" in sql:
            orig(conn, is_pg, sql, params)   # update executes inside the txn...
            raise RuntimeError("injected crash before commit")
        return orig(conn, is_pg, sql, params)

    S.execute = bad
    try:
        try:
            AD.adopt_pair(off, onl, off_id, on_id, str(uuid.uuid4()))
            raise AssertionError("adopt_pair should have raised")
        except RuntimeError as exc:
            assert "injected" in str(exc)
    finally:
        S.execute = orig
    # rollback: offline row unchanged
    assert not _read(off, off_id)["sync_id"]
    assert not _read(onl, on_id)["sync_id"]
    off.close()
    onl.close()


# ---------------------------------------------------------------------------
# CLASSIFIER (Part J) - category decisions for future NULL-sync rows
# ---------------------------------------------------------------------------
def _rows(conn):
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute("SELECT * FROM records").fetchall()]


def test_classifier_exact_counterpart():
    off, onl, _ = _env()
    _insert_legacy_null(off, _biz())
    on_id = _insert_legacy_null(onl, _biz())
    out = AD.classify_null_sync_rows(_rows(off), _rows(onl))
    assert out[0]["category"] == AD.EXACT_COUNTERPART
    assert out[0]["exact_candidates"] == [on_id]
    off.close()
    onl.close()


def test_classifier_independent_new_record():
    off, onl, _ = _env()
    _insert_legacy_null(off, _biz(serial_no="BRAND-NEW-SERIAL",
                                  invoice_no="260999", name="New Guy"))
    _insert_legacy_null(onl, _biz())   # different identity online
    out = AD.classify_null_sync_rows(_rows(off), _rows(onl))
    assert out[0]["category"] == AD.INDEPENDENT_NEW
    off.close()
    onl.close()


def test_classifier_serial_na_is_not_identity_key():
    off, onl, _ = _env()
    _insert_legacy_null(off, _biz(name="A", serial_no="NA", invoice_no="INV-A"))
    _insert_legacy_null(off, _biz(name="B", serial_no="NA", invoice_no="INV-B"))
    on_id_a = _insert_legacy_null(onl, _biz(name="A", serial_no="NA",
                                            invoice_no="INV-A"))
    out = AD.classify_null_sync_rows(_rows(off), _rows(onl))
    by_name = {x["name"]: x for x in out}
    # serial 'NA' is never an EXACT identity key: field-identical + invoice
    # match is only a review candidate, never auto-adoptable.
    assert by_name["A"]["category"] == AD.LIKELY_REVIEW
    assert by_name["A"]["exact_candidates"] == []
    assert by_name["A"]["likely_candidates"] == [on_id_a]
    assert by_name["B"]["category"] == AD.INDEPENDENT_NEW
    off.close()
    onl.close()


def test_classifier_same_side_duplicate():
    off, onl, _ = _env()
    _insert_legacy_null(off, _biz(serial_no="SER-X", invoice_no="INV-1"))
    _insert_legacy_null(off, _biz(serial_no="SER-X", invoice_no="INV-2",
                                  name="Other"))
    _insert_legacy_null(onl, _biz(serial_no="SER-X", invoice_no="INV-1"))
    out = AD.classify_null_sync_rows(_rows(off), _rows(onl))
    dup = [x for x in out if x["serial_no"] == "SER-X"
           and x["invoice_no"] == "INV-1"]
    assert dup and dup[0]["category"] == AD.DUPLICATE
    off.close()
    onl.close()


def test_classifier_invoice_alone_never_identity():
    off, onl, _ = _env()
    _insert_legacy_null(off, _biz(serial_no="S1", invoice_no="INV-SAME",
                                  name="OfflineGuy", bid="BID-OFF"))
    _insert_legacy_null(onl, _biz(serial_no="S2", invoice_no="INV-SAME",
                                  name="OnlineGuy", bid="BID-ON"))
    out = AD.classify_null_sync_rows(_rows(off), _rows(onl))
    # Same invoice but different serial/bid/name/phone -> NOT an exact or even
    # a strong-key counterpart: the row is classified as independent.
    assert out[0]["category"] == AD.INDEPENDENT_NEW
    assert out[0]["exact_candidates"] == []
    assert out[0]["likely_candidates"] == []
    off.close()
    onl.close()


def test_classifier_ambiguous_multiple_candidates():
    off, onl, _ = _env()
    _insert_legacy_null(off, _biz(serial_no="S1", invoice_no="INV-1",
                                  name="X", phone="1"))
    _insert_legacy_null(onl, _biz(serial_no="S1", invoice_no="INV-1",
                                  name="Y", phone="2"))
    _insert_legacy_null(onl, _biz(serial_no="S1", invoice_no="INV-1",
                                  name="Z", phone="3"))
    out = AD.classify_null_sync_rows(_rows(off), _rows(onl))
    assert out[0]["category"] == AD.AMBIGUOUS
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
    print("\n%s" % ("ALL SYNCV2 PHASE7C ADOPTION TESTS PASSED"
                    if failed == 0 else "%d FAILED" % failed))
    sys.exit(1 if failed else 0)


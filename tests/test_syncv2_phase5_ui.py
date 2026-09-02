"""Phase-5 UI presentation tests (pure view models + AppTest smoke, isolated data)."""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests"))

import sync_v2_state as state
import syncv2_helpers as H
from syncv2 import protocol as P

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _make_off_db():
    d = tempfile.mkdtemp(prefix="sv2_ui_")
    conn = H.make_db(os.path.join(d, "off.db"))
    return d, conn


def test_status_classification_and_labels():
    ready = {"present": True, "sync_schema": True, "state": {}, "outbox": {},
             "local_open_conflicts": 0}
    assert state.classify_status(ready) == state.STATUS_READY
    synced = dict(ready)
    synced["state"] = {"last_success_at": "2026-08-02T10:00:00+00:00"}
    assert state.classify_status(synced) == state.STATUS_SYNCED
    conflicted = dict(ready)
    conflicted["outbox"] = {"blocked": 2}
    assert state.classify_status(conflicted) == state.STATUS_CONFLICT
    local_conf = dict(ready)
    local_conf["local_open_conflicts"] = 1
    assert state.classify_status(local_conf) == state.STATUS_CONFLICT
    pending_err = dict(ready)
    pending_err["outbox"] = {"pending": 1}
    pending_err["state"] = {"last_error": "network down"}
    assert state.classify_status(pending_err) == state.STATUS_OFFLINE
    assert state.status_label(state.STATUS_NEEDS_ATTENTION) == "Needs attention"
    assert state.status_label(state.STATUS_READY) == "Not active"


def test_last_sync_formatting_never_invents_timestamp():
    assert state.format_last_sync(None) is None
    assert state.format_last_sync("") is None
    assert state.format_last_sync("not-a-date") is None
    assert "2026" in state.format_last_sync("2026-08-02T10:00:00+00:00")


def test_build_single_field_conflict_view():
    rows = [{"id": 1, "sync_id": "s1", "kind": "financial", "field_name": "price",
             "base_value": "1000", "offline_value": "2222", "online_value": "1111",
             "month": "", "status": "open", "created_at": "2026-08-02T10:00:00+00:00"}]
    rec = {"name": "ABC Traders", "invoice_no": "INV-9"}
    views = state.build_conflict_views(rows, lambda sid: rec)
    assert len(views) == 1
    v = views[0]
    assert v["label"] == "ABC Traders" and v["detail"] == "INV-9"
    assert v["conflict_count"] == 1
    assert v["field_conflicts"][0]["field_label"] == "Amount"
    assert v["field_conflicts"][0]["base"] == "1000"


def test_build_multi_field_conflict_views_keep_all_fields():
    rows = [
        {"id": 1, "sync_id": "s1", "kind": "field", "field_name": "name",
         "base_value": "Old", "offline_value": "Off", "online_value": "On",
         "status": "open", "created_at": "t"},
        {"id": 2, "sync_id": "s1", "kind": "financial", "field_name": "price",
         "base_value": "1000", "offline_value": "2222", "online_value": "1111",
         "status": "open", "created_at": "t"},
    ]
    views = state.build_conflict_views(rows, lambda sid: {"name": "ABC"})
    assert len(views) == 1
    assert views[0]["conflict_count"] == 2
    assert {f["field"] for f in views[0]["field_conflicts"]} == {"name", "price"}


def test_build_grouped_sr_conflict_view():
    rows = [{"id": 7, "sync_id": "sA", "kind": P.CONFLICT_SR_ORDER,
             "field_name": "sr_no", "month": "AUGUST_2026",
             "base_value": json.dumps(["sA", "sB", "sC"]),
             "offline_value": json.dumps(["sB", "sC", "sA"]),
             "online_value": json.dumps(["sC", "sA", "sB"]),
             "status": "open", "created_at": "t"}]
    recs = {"sA": {"name": "AAA"}, "sB": {"name": "BBB"}, "sC": {"name": "CCC"}}
    views = state.build_conflict_views(rows, lambda sid: recs.get(sid, {}))
    assert len(views) == 1
    sr = views[0]["sr_conflicts"][0]
    assert sr["month"] == "AUGUST_2026"
    assert [e["label"] for e in sr["offline_seq"]] == ["BBB", "CCC", "AAA"]
    assert [e["label"] for e in sr["online_seq"]] == ["CCC", "AAA", "BBB"]
    assert views[0]["field_conflicts"] == []


def test_delete_and_invoice_conflicts_are_grouped_separately():
    rows = [
        {"id": 3, "sync_id": "s1", "kind": P.CONFLICT_DELETE_EDIT,
         "field_name": "deleted_at", "base_value": None,
         "offline_value": "2026-08-02T10:00:00+00:00", "online_value": None,
         "status": "open", "created_at": "t"},
        {"id": 4, "sync_id": "s2", "kind": P.CONFLICT_INVOICE,
         "field_name": "invoice_no", "base_value": "INV-1",
         "offline_value": "INV-1", "online_value": "INV-1",
         "status": "open", "created_at": "t"},
    ]
    views = state.build_conflict_views(rows, lambda sid: {"name": "X"})
    by_sid = {v["sync_id"]: v for v in views}
    assert len(by_sid["s1"]["delete_conflicts"]) == 1
    assert len(by_sid["s2"]["invoice_collisions"]) == 1
    assert by_sid["s1"]["conflict_types"] == ["Deleted vs changed"]


def test_read_local_status_on_temp_db():
    d, conn = _make_off_db()
    conn.execute("INSERT INTO outbox (op_id, sync_id, op_type, status, attempts, "
                 "created_at, updated_at) VALUES ('o1','s1','upsert','pending',0,"
                 "'2026-08-02','2026-08-02')")
    conn.commit()
    conn.close()
    local = state.read_local_sync_status(os.path.join(d, "off.db"))
    assert local["present"] is True and local["sync_schema"] is True
    assert local["outbox"].get("pending") == 1
    assert state.classify_status(local) == state.STATUS_NEEDS_ATTENTION


def test_read_local_record_lookup():
    d, conn = _make_off_db()
    H.insert_row(conn, "s1", H.base_business(name="ABC", invoice_no="INV-1"))
    conn.close()
    rec = state.read_local_record(os.path.join(d, "off.db"), "s1")
    assert rec.get("name") == "ABC" and rec.get("invoice_no") == "INV-1"


def _render_smoke(source):
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_string(source, default_timeout=30)
    at.run()
    assert not at.exception
    return at


def test_settings_render_does_not_start_sync_and_ready_without_engine():
    d, conn = _make_off_db()
    conn.close()
    dbp = os.path.join(d, "off.db").replace("\\", "/")
    source = (
        "import sys\n"
        "sys.path.insert(0, %r)\n"
        "import sync_v2_ui as ui\n"
        "ui.render_settings_section(%r, engine=None)\n" % (_REPO, dbp)
    )
    at = _render_smoke(source)
    text = " ".join(str(x.value) for x in at.get("markdown") if getattr(x, "value", None))
    assert "Sync V2" in text


def test_settings_render_with_engine_never_calls_run_once():
    d, off, srv, adapter, eng = _mk_engine_env()
    off.close()
    dbp = os.path.join(d, "off.db").replace("\\", "/")
    source = (
        "import sys\n"
        "sys.path.insert(0, %r)\n"
        "import sync_v2_ui as ui\n"
        "import syncv2_helpers as H\n"
        "from syncv2.engine import SyncEngine\n"
        "import sqlite3\n"
        "off=sqlite3.connect(%r)\n"
        "srv=sqlite3.connect(%r)\n"
        "class Guard:\n"
        "    def __init__(self, eng):\n"
        "        self._e = eng\n"
        "        self.run_calls = 0\n"
        "    def __getattr__(self, name):\n"
        "        return getattr(self._e, name)\n"
        "    def run_once(self):\n"
        "        self.run_calls += 1\n"
        "        raise AssertionError('UI must never call run_once')\n"
        "eng = Guard(SyncEngine(off, False, H.ServerAdapter(srv)))\n"
        "ui.render_settings_section(%r, engine=eng)\n" % (_REPO, dbp,
                                                          os.path.join(d, "srv.db"),
                                                          dbp)
    )
    at = _render_smoke(source)
    text = " ".join(str(x.value) for x in at.get("markdown") if getattr(x, "value", None))
    assert "Sync V2" in text


def test_offline_warning_retry_and_continue():
    source = (
        "import sys\n"
        "sys.path.insert(0, %r)\n"
        "import sync_v2_ui as ui\n"
        "ui.render_offline_warning(last_sync_text='2 Aug 2026, 10:00 AM', "
        "dismiss_key='sv2_off_test')\n" % _REPO
    )
    at = _render_smoke(source)
    labels = [b.label for b in at.button]
    assert "Retry" in labels and "Continue Offline" in labels


def _mk_engine_env():
    import sqlite3
    d = tempfile.mkdtemp(prefix="sv2_ui_eng_")
    off = H.make_db(os.path.join(d, "off.db"))
    srv = H.make_db(os.path.join(d, "srv.db"))
    adapter = H.ServerAdapter(srv)
    from syncv2.engine import SyncEngine
    eng = SyncEngine(off, False, adapter)
    return d, off, srv, adapter, eng


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
    print("\n%s" % ("ALL SYNCV2 PHASE5 UI TESTS PASSED" if failed == 0 else "%d FAILED" % failed))
    sys.exit(1 if failed else 0)


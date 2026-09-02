"""
sync_v2_state.py - READ-ONLY Sync V2 presentation helpers (pure, no Streamlit).

Builds user-facing status/conflict view models from local database state and
optionally the server conflict feed. Nothing here starts a sync session.
"""
import json
import os
import sqlite3
from datetime import datetime, timezone

from syncv2 import protocol as P

STATUS_SYNCED = "SYNCED"
STATUS_SYNCING = "SYNCING"
STATUS_OFFLINE = "OFFLINE"
STATUS_NEEDS_ATTENTION = "NEEDS_ATTENTION"
STATUS_CONFLICT = "CONFLICT"
STATUS_ERROR = "ERROR"
STATUS_BUSY = "BUSY"
STATUS_READY = "READY"  # transitional: schema present, no Sync V2 run yet

_CONFLICT_TYPE_LABELS = {
    "field": "Changed differently",
    "financial": "Amount / finance changed",
    "serial": "Serial changed differently",
    "delete_edit": "Deleted vs changed",
    "sr_ordering": "Ordering changed on both sides",
    "invoice_collision": "Same invoice on multiple records",
}


def open_local_db(db_path):
    if not db_path or not os.path.exists(db_path):
        return None
    return sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)


def read_local_sync_status(db_path):
    """Read sync_state/outbox from the local (Offline) database. Read-only."""
    conn = open_local_db(db_path)
    if conn is None:
        return {"present": False}
    try:
        has_sync_cols = False
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(records)")}
            has_sync_cols = "sync_id" in cols
        except sqlite3.Error:
            pass
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        state = {}
        if has_sync_cols and "sync_state" in tables:
            rows = conn.execute(
                "SELECT last_success_at, last_attempt_at, last_error, "
                "last_pulled_sync_rev, conflict_count FROM sync_state WHERE id=1").fetchall()
            if rows:
                state = dict(zip(["last_success_at", "last_attempt_at", "last_error",
                                  "last_pulled_sync_rev", "conflict_count"], rows[0]))
        outbox = {}
        if "outbox" in tables:
            for status, count in conn.execute(
                    "SELECT status, COUNT(*) FROM outbox GROUP BY status").fetchall():
                outbox[status] = count
        local_conflicts = 0
        if "conflicts" in tables:
            local_conflicts = conn.execute(
                "SELECT COUNT(*) FROM conflicts WHERE status='open'").fetchone()[0]
        return {
            "present": True,
            "sync_schema": has_sync_cols,
            "state": state,
            "outbox": outbox,
            "local_open_conflicts": local_conflicts,
        }
    except sqlite3.Error as exc:
        return {"present": False, "error": str(exc)}
    finally:
        conn.close()


def classify_status(local, server_ok=None, engine_busy=False, sync_running=False):
    if not local.get("present") or not local.get("sync_schema"):
        return STATUS_READY
    if sync_running:
        return STATUS_SYNCING
    if engine_busy:
        return STATUS_BUSY
    outbox = local.get("outbox") or {}
    pending = (outbox.get("pending", 0) or 0) + (outbox.get("in_flight", 0) or 0)
    blocked = outbox.get("blocked", 0) or 0
    if blocked or local.get("local_open_conflicts"):
        return STATUS_CONFLICT
    last_error = (local.get("state") or {}).get("last_error")
    if last_error:
        return STATUS_OFFLINE if pending else STATUS_ERROR
    if (local.get("state") or {}).get("last_success_at"):
        return STATUS_SYNCED
    if pending:
        return STATUS_NEEDS_ATTENTION
    return STATUS_READY


def status_label(status):
    return {
        STATUS_SYNCED: "Synced",
        STATUS_SYNCING: "Syncing…",
        STATUS_OFFLINE: "Offline",
        STATUS_NEEDS_ATTENTION: "Needs attention",
        STATUS_CONFLICT: "Conflicts need review",
        STATUS_ERROR: "Sync error",
        STATUS_BUSY: "Sync in progress",
        STATUS_READY: "Not active",
    }.get(status, status)


def parse_iso(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def format_last_sync(value):
    if not value:
        return None
    dt = parse_iso(value)
    if dt is None:
        return None
    return dt.astimezone().strftime("%d %b %Y, %I:%M %p")


def human_ago(value):
    if not value:
        return None
    dt = parse_iso(value)
    if dt is None:
        return None
    seconds = max(0, int((datetime.now(timezone.utc) - dt).total_seconds()))
    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return "%d minute%s ago" % (minutes, "" if minutes == 1 else "s")
    hours = minutes // 60
    if hours < 24:
        return "%d hour%s ago" % (hours, "" if hours == 1 else "s")
    days = hours // 24
    return "%d day%s ago" % (days, "" if days == 1 else "s")


def field_label(field):
    labels = {
        "name": "Customer Name", "phone": "Phone", "alt_phone": "Alternate Phone",
        "product": "Product", "actual_product": "Actual Product", "xcell": "Xcell",
        "remarks": "Remarks", "bid_date": "BID Date", "price": "Amount",
        "emi": "EMI", "di": "DI", "dp_taken": "DP Taken",
        "given_prod_price": "Given Product Price", "serial_no": "Serial Number",
        "bid": "BID / DO ID", "invoice_no": "Invoice Number", "sr_no": "SR / Order",
        "scheme": "Scheme", "deleted_at": "Record",
    }
    return labels.get(field or "", (field or "").replace("_", " ").title())


def conflict_type_label(kind):
    return _CONFLICT_TYPE_LABELS.get(kind or "", (kind or "").replace("_", " ").title())


def record_summary(row):
    if not row:
        return {"label": "Record", "detail": ""}
    name = row.get("name") or ""
    invoice = row.get("invoice_no") or ""
    detail = invoice if invoice else (row.get("phone") or "")
    return {"label": name or "Unnamed record", "detail": detail}


def read_local_record(db_path, sync_id):
    """Read the local record business fields for a sync_id (read-only)."""
    conn = open_local_db(db_path)
    if conn is None:
        return {}
    try:
        cols = ",".join(P.BUSINESS_FIELDS)
        rows = conn.execute(
            "SELECT %s FROM records WHERE sync_id=?" % cols, (sync_id,)).fetchall()
        if not rows:
            return {}
        return dict(zip(P.BUSINESS_FIELDS, rows[0]))
    except sqlite3.Error:
        return {}
    finally:
        conn.close()


def _display_value(field, value, kind):
    if kind in (P.CONFLICT_SR_ORDER, P.CONFLICT_INVOICE):
        return value
    if value is None:
        return ""
    return str(value)


def build_conflict_views(conflict_rows, record_lookup):
    """Turn raw server conflict rows into grouped user-facing views.

    record_lookup: callable(sync_id) -> local records row (business dict).
    """
    groups = {}
    for row in conflict_rows or []:
        sid = row.get("sync_id")
        if sid is None:
            continue
        groups.setdefault(sid, {"sync_id": sid, "conflicts": []})["conflicts"].append(row)
    views = []
    for sid, group in groups.items():
        rec = record_lookup(sid) or {}
        summary = record_summary(rec)
        conflicts = group["conflicts"]
        sr_items = [c for c in conflicts if c.get("kind") == P.CONFLICT_SR_ORDER]
        invoice_items = [c for c in conflicts if c.get("kind") == P.CONFLICT_INVOICE]
        delete_items = [c for c in conflicts if c.get("kind") == P.CONFLICT_DELETE_EDIT]
        field_items = [c for c in conflicts
                       if c.get("kind") in ("field", "financial", "serial")]
        sr_views = [_sr_view(c, record_lookup) for c in sr_items]
        field_views = [{
            "id": c.get("id"), "field": c.get("field_name"),
            "field_label": field_label(c.get("field_name")),
            "kind": c.get("kind"), "kind_label": conflict_type_label(c.get("kind")),
            "base": _display_value(c.get("field_name"), c.get("base_value"), c.get("kind")),
            "offline": _display_value(c.get("field_name"), c.get("offline_value"),
                                      c.get("kind")),
            "online": _display_value(c.get("field_name"), c.get("online_value"),
                                     c.get("kind")),
        } for c in field_items]
        views.append({
            "sync_id": sid,
            "label": summary["label"],
            "detail": summary["detail"],
            "record": rec,
            "conflict_count": len(conflicts),
            "conflict_types": sorted({conflict_type_label(c.get("kind"))
                                      for c in conflicts}),
            "field_conflicts": field_views,
            "sr_conflicts": sr_views,
            "invoice_collisions": invoice_items,
            "delete_conflicts": delete_items,
            "created_at": conflicts[0].get("created_at"),
        })
    views.sort(key=lambda v: (v["label"] or "").lower())
    return views


def _sr_view(conflict, record_lookup):
    def decode(value):
        if not value:
            return []
        if isinstance(value, list):
            return value
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return []

    def seq_entries(seq):
        out = []
        for i, sid in enumerate(seq or [], start=1):
            rec = record_lookup(sid) or {}
            summary = record_summary(rec)
            out.append({"position": i, "sync_id": sid, "label": summary["label"],
                        "detail": summary["detail"]})
        return out

    return {
        "id": conflict.get("id"), "kind": P.CONFLICT_SR_ORDER,
        "month": conflict.get("month") or "",
        "base_seq": seq_entries(decode(conflict.get("base_value"))),
        "offline_seq": seq_entries(decode(conflict.get("offline_value"))),
        "online_seq": seq_entries(decode(conflict.get("online_value"))),
    }



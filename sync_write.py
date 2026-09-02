"""sync_write.py - Sync V2-aware transactional LOCAL write service (Phase 6).

Central seam between database.py (business/persistence) and the pure-Python
syncv2 package (sync metadata + outbox). Every supported Finance Desk record
write becomes ONE atomic SQLite transaction:

    BEGIN
        business row change           (owned by database.py CRUD)
        capture before-row metadata   (base_json / server_rev / row_rev / sync_id)
        advance row_rev + updated_at
        insert the durable outbox operation
        coalesce pending upserts      (Phase-4 logic: newest payload, OLDEST base)
    COMMIT            -- any failure above ROLLS BACK the whole logical write

Design rules honoured:
  * The outbox payload is the transaction's ACTUAL resulting business snapshot.
    It is NEVER built by re-reading the row after commit.
  * base_json and server_rev are NEVER overwritten by a local edit; they remain
    the ancestor / "which side wins" authority. Only row_rev + updated_at move.
  * A delete is a tombstone (deleted_at preserved, sync_id preserved); the row
    is never physically removed by the Sync V2 path.
  * No network access and no SyncEngine.run_once() anywhere in this module.
  * PostgreSQL/online mode is untouched (database.py only calls these helpers on
    a SQLite connection that actually carries the Phase-1 schema).
"""

import uuid
from datetime import datetime, timezone

from syncv2 import protocol as P
from syncv2 import store as S
import database as db


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def has_sync_schema(conn):
    """True when a SQLite `records` table carries the full Phase-1 Sync V2 schema
    (sync columns + the outbox table). Always False for PostgreSQL/online mode,
    so the online application keeps its exact legacy behaviour."""
    if db.USE_POSTGRES:
        return False
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(records)")}
        if not {"sync_id", "server_rev", "row_rev", "base_json",
                "deleted_at"} <= cols:
            return False
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        return "outbox" in tables
    except Exception:
        return False


def row_to_dict(conn, record_id):
    """Read one local row (business + sync metadata) as a dict, or None."""
    cur = conn.execute("SELECT * FROM records WHERE id=?", (record_id,))
    try:
        rows = [dict(r) for r in cur.fetchall()]
    finally:
        cur.close()
    return rows[0] if rows else None


def business_dict(row):
    """Business snapshot of a records row (exactly the 19 synced fields)."""
    return {f: row.get(f) for f in P.BUSINESS_FIELDS}


def _base_of(row):
    return S.decode_base(row.get("base_json"))


def _require_sync_id(row):
    sync_id = row.get("sync_id")
    if not sync_id:
        raise RuntimeError(
            "Sync V2 write refused: record id %r has no sync_id (bootstrap "
            "missing?). Refusing to create a silent non-syncing change."
            % (row.get("id")))
    return sync_id

def _enqueue(conn, op_type, before_row, payload, new_row_rev):
    """Insert one durable outbox operation inside the caller's open transaction.

    BASE = the row's stored ancestor snapshot (base_json) - never the new local
    state. base_rev = server_rev as last agreed with the server.
    """
    sync_id = _require_sync_id(before_row)
    op_id = str(uuid.uuid4())
    base_rev = int(before_row.get("server_rev") or 0)
    S.create_outbox_op(conn, False, op_id, sync_id, op_type, payload,
                       _base_of(before_row), base_rev, new_row_rev)
    return op_id


def enqueue_create(conn, record_id):
    """Outbox 'upsert' for a brand-new record (row already inserted inside the
    caller's transaction with row_rev=1 and a fresh sync_id). BASE is empty:
    nothing was known about this identity before."""
    row = row_to_dict(conn, record_id)
    if not row:
        raise RuntimeError("cannot enqueue create for missing record %r"
                           % (record_id,))
    _require_sync_id(row)
    new_row_rev = int(row.get("row_rev") or 0) or 1
    payload = business_dict(row)
    return _enqueue(conn, P.OP_UPSERT, row, payload, new_row_rev)


def finalize_edit(conn, before_row, business_after, coalesce=True):
    """Complete an already-applied business UPDATE inside the same transaction.

    before_row: the row dict read BEFORE the business change (its base_json,
    server_rev, row_rev and sync_id are the transaction's true before-state).
    business_after: the resulting business snapshot (used verbatim as payload).
    Advances row_rev + updated_at, enqueues an upsert op carrying the after-state
    over the OLDEST required BASE, and optionally runs the Phase-4 coalescer so
    consecutive unsynced edits fold into ONE op while preserving the ancestor.
    """
    _require_sync_id(before_row)
    record_id = before_row["id"]
    new_row_rev = int(before_row.get("row_rev") or 0) + 1
    conn.execute("UPDATE records SET row_rev=?, updated_at=? WHERE id=?",
                 (new_row_rev, _now_iso(), record_id))
    payload = {f: business_after.get(f) for f in P.BUSINESS_FIELDS}
    op_id = _enqueue(conn, P.OP_UPSERT, before_row, payload, new_row_rev)
    if coalesce:
        S.coalesce_upserts(conn, False)
    return op_id


def tombstone(conn, before_row, deleted_at=None):
    """Soft-delete a record + durable delete outbox op (same transaction).

    Business fields are untouched, sync_id is preserved, row_rev advances and
    deleted_at is stamped. No physical purge, no automatic purge ever.
    """
    _require_sync_id(before_row)
    deleted_at = deleted_at or _now_iso()
    record_id = before_row["id"]
    new_row_rev = int(before_row.get("row_rev") or 0) + 1
    conn.execute("UPDATE records SET deleted_at=?, row_rev=?, updated_at=? "
                 "WHERE id=?", (deleted_at, new_row_rev, _now_iso(), record_id))
    payload = business_dict(before_row)
    payload["deleted_at"] = deleted_at
    return _enqueue(conn, P.OP_DELETE, before_row, payload, new_row_rev)


def coalesce(conn):
    """Run the existing Phase-4 outbox coalescer (keeps latest payload + oldest
    base ancestor for pending upserts sharing a sync_id)."""
    S.coalesce_upserts(conn, False)


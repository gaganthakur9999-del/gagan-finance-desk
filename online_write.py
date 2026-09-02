"""online_write.py - Sync V2-aware ONLINE (server-side replica) write seam.

Phase 7B. The Online (Render/PostgreSQL) application shares database.py with the
Offline app. When USE_POSTGRES is true and the Phase-1 schema is present, every
normal-user records write must go through THIS seam so that Online-originated
changes become discoverable by the Sync V2 incremental pull:

    Online business change  (database.py CRUD owns the SQL)
        -> read before-row (sync_id / business)
        -> allocate ONE new global server revision (sync_sequence row lock)
        -> apply business change to `records`
        -> advance server_rev on the row
        -> maintain base_json as the server-current snapshot (Phase-4
           write_server_row semantics; the Offline replica's own base_json only
           advances when it pulls, so 'BASE' is never force-agreed by an Online
           write)
        -> row_rev stays 0 (server-side convention, identical to write_server_row)

Rules honoured:
  * create  -> fresh uuid4 sync_id assigned EXACTLY once, rev>0, base_json set
  * edit    -> sync_id preserved, new server_rev, base_json = resulting snapshot
  * delete  -> TOMBSTONE (deleted_at), never a physical delete; sync_id preserved
  * SR swap -> one revision per affected row, same transaction, identity stable
  * no outbox entry and no applied_ops entry are needed for Online-originated
    writes: the row revision IS the server-side change record; client ops remain
    the only outbox/applied_ops users (idempotent replay unchanged)
  * a legacy NULL/blank sync_id row is REFUSED (no silent split identity, no
    silent non-syncing mutation) - adoption is a deliberate controlled step
  * never commits; caller owns the transaction (BEGIN/COMMIT/ROLLBACK)

Backend-agnostic (is_pg like the rest of syncv2). Tests run it with is_pg=False
against SQLite twin databases; the real-PG path uses the identical code branches.
"""
import json

from syncv2 import protocol as P
from syncv2 import store as S
from syncv2 import server as SVC
from syncv2 import merge as M

# Full records column list (business + system), stable across both backends.
_COLS = ["id", "sr_no", "bid_date", "invoice_no", "name", "xcell", "product",
         "serial_no", "price", "emi", "di", "bid", "dp_taken", "scheme",
         "actual_product", "given_prod_price", "phone", "alt_phone", "month",
         "remarks", "sync_id", "server_rev", "row_rev", "base_json",
         "deleted_at", "created_at", "updated_at"]

_SYNC_COLUMNS = {"sync_id", "server_rev", "row_rev", "base_json", "deleted_at"}


def _now_iso():
    return S.now_utc()


def schema_ready(conn, is_pg):
    """True when records carries the Phase-1 Sync V2 schema + outbox table."""
    if is_pg:
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='records'")
            cols = {r[0] for r in cur.fetchall()}
            cur.execute("SELECT to_regclass('public.outbox')")
            out = cur.fetchone()
            cur.close()
            return bool(out and out[0]) and _SYNC_COLUMNS <= cols
        except Exception:
            return False
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(records)")}
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        return _SYNC_COLUMNS <= cols and "outbox" in tables
    except Exception:
        return False


def _ph(is_pg):
    return "%s" if is_pg else "?"


def row_by_id(conn, is_pg, record_id):
    rows = S.fetch_all(conn, is_pg,
                       "SELECT %s FROM records WHERE id=%s"
                       % (",".join(_COLS), _ph(is_pg)), (record_id,))
    if not rows:
        return None
    return dict(zip(_COLS, rows[0]))


def _row_dict_business(row):
    return {f: row.get(f) for f in P.BUSINESS_FIELDS}


def _base_json(business):
    return json.dumps(business, sort_keys=True, default=str,
                      ensure_ascii=True, separators=(",", ":"))


def _require_sync_id(row):
    sid = row.get("sync_id")
    if not sid:
        raise RuntimeError(
            "Online Sync V2 write refused: record id %r has no sync_id. "
            "Legacy NULL-sync rows must be adopted first; refusing a silent "
            "split identity / non-syncing mutation." % (row.get("id")))
    return sid


def _id_by_sync_id(conn, is_pg, sync_id):
    rows = S.fetch_all(conn, is_pg, "SELECT id FROM records WHERE sync_id=%s"
                       % _ph(is_pg), (sync_id,))
    return rows[0][0] if rows else None


def create_row(conn, is_pg, business):
    """Online-originated create: stable sync_id + rev>0 + server-current base.
    Returns the local/online row id."""
    import uuid
    sync_id = str(uuid.uuid4())
    rev = SVC.next_revision(conn, is_pg)
    biz = {f: business.get(f) for f in P.BUSINESS_FIELDS}
    biz["month"] = M.month_from_bid_date(biz.get("bid_date")) or biz.get("month")
    SVC.write_server_row(conn, is_pg, sync_id, biz, None,
                         _base_json(biz), rev)
    rid = _id_by_sync_id(conn, is_pg, sync_id)
    if rid is None:
        raise RuntimeError("online create failed to persist sync_id %s" % sync_id)
    return rid


def edit_row(conn, is_pg, record_id, business_after):
    """Online-originated edit. sync_id preserved, server_rev advanced, base_json
    becomes the server-current snapshot (never forced on the Offline replica)."""
    row = row_by_id(conn, is_pg, record_id)
    if row is None or row.get("deleted_at"):
        return {"result": "noop", "record_id": record_id}
    sync_id = _require_sync_id(row)
    rev = SVC.next_revision(conn, is_pg)
    biz = {f: business_after.get(f) for f in P.BUSINESS_FIELDS}
    biz["month"] = M.month_from_bid_date(biz.get("bid_date")) or biz.get("month")
    SVC.write_server_row(conn, is_pg, sync_id, biz, None, _base_json(biz), rev)
    return {"result": "applied", "record_id": record_id, "sync_id": sync_id,
            "server_rev": rev}



def delete_row(conn, is_pg, record_id, deleted_at=None):
    """Online-originated delete: TOMBSTONE + month renumbering in one caller
    transaction. Never a physical delete. sync_id and business fields of the
    tombstone are preserved; every shifted live row gets its own new server
    revision so the ordering is discoverable by pull."""
    row = row_by_id(conn, is_pg, record_id)
    if row is None or row.get("deleted_at"):
        return {"result": "noop", "record_id": record_id}
    sync_id = _require_sync_id(row)
    deleted_at = deleted_at or _now_iso()
    rev = SVC.next_revision(conn, is_pg)
    S.execute(conn, is_pg,
              "UPDATE records SET deleted_at=%s, server_rev=%s, updated_at=%s "
              "WHERE sync_id=%s" % (_ph(is_pg), _ph(is_pg), _ph(is_pg),
                                    _ph(is_pg)),
              (deleted_at, rev, _now_iso(), sync_id))
    month = row.get("month") or ""
    renumbered = 0
    if month:
        ph = _ph(is_pg)
        live = S.fetch_all(conn, is_pg,
                           "SELECT %s FROM records WHERE month=%s AND "
                           "deleted_at IS NULL ORDER BY sr_no ASC, id ASC"
                           % (",".join(_COLS), ph), (month,))
        for idx, raw in enumerate(live, start=1):
            live_row = dict(zip(_COLS, raw))
            if int(live_row.get("sr_no") or 0) == idx:
                continue
            biz = _row_dict_business(live_row)
            biz["sr_no"] = idx
            rrev = SVC.next_revision(conn, is_pg)
            SVC.write_server_row(conn, is_pg, live_row["sync_id"], biz, None,
                                 _base_json(biz), rrev)
            renumbered += 1
    return {"result": "applied", "record_id": record_id, "sync_id": sync_id,
            "server_rev": rev, "deleted_at": deleted_at,
            "renumbered": renumbered}


def swap_sr(conn, is_pg, id1, id2):
    """Online-originated SR move: one revision per affected row in ONE caller
    transaction. sr_no is order data, never identity; sync_ids are stable."""
    row1 = row_by_id(conn, is_pg, id1)
    row2 = row_by_id(conn, is_pg, id2)
    if (not row1 or not row2 or row1.get("deleted_at") or row2.get("deleted_at")):
        return False
    s1 = _require_sync_id(row1)
    s2 = _require_sync_id(row2)
    old1, old2 = row1.get("sr_no"), row2.get("sr_no")
    b1 = _row_dict_business(row1)
    b1["sr_no"] = old2
    b2 = _row_dict_business(row2)
    b2["sr_no"] = old1
    rev1 = SVC.next_revision(conn, is_pg)
    SVC.write_server_row(conn, is_pg, s1, b1, None, _base_json(b1), rev1)
    rev2 = SVC.next_revision(conn, is_pg)
    SVC.write_server_row(conn, is_pg, s2, b2, None, _base_json(b2), rev2)
    return True


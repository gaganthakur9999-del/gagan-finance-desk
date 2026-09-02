"""
syncv2/store.py - low-level dual-backend persistence primitives.

Backend-agnostic via `is_pg` + a placeholder helper: every read/write used by
the engine works against SQLite (tests + Offline client) and PostgreSQL (server).
No application schema changes are made here; the Phase-1 sync_schema tables and
the Phase-3 records columns are used as-is.
"""
import json
from datetime import datetime, timezone

from . import protocol as P

_BIZ = ",".join(P.BUSINESS_FIELDS)


def now_utc():
    return datetime.now(timezone.utc).isoformat()


def ph(is_pg):
    return "%s" if is_pg else "?"


def fetch_all(conn, is_pg, sql, params=()):
    if is_pg:
        cur = conn.cursor()
        cur.execute(sql, list(params))
        rows = cur.fetchall()
        cur.close()
        return rows
    return conn.execute(sql, list(params)).fetchall()


def execute(conn, is_pg, sql, params=()):
    if is_pg:
        cur = conn.cursor()
        cur.execute(sql, list(params))
        n = cur.rowcount
        cur.close()
        return n
    return conn.execute(sql, list(params)).rowcount


def commit(conn, is_pg):
    conn.commit()


def rollback(conn, is_pg):
    conn.rollback()


# ---------------------------------------------------------------- row snapshots
def read_row_full(conn, is_pg, sync_id):
    """records row for a sync_id: business fields + sync metadata."""
    q = ph(is_pg)
    rows = fetch_all(conn, is_pg,
                     "SELECT %s, sync_id, server_rev, row_rev, base_json, deleted_at "
                     "FROM records WHERE sync_id=%s" % (_BIZ, q), (sync_id,))
    if not rows:
        return None
    names = P.BUSINESS_FIELDS + ["sync_id", "server_rev", "row_rev",
                                 "base_json", "deleted_at"]
    return dict(zip(names, rows[0]))


def decode_base(base_json):
    if not base_json:
        return {f: None for f in P.BUSINESS_FIELDS}
    return json.loads(base_json)


# ---------------------------------------------------------------- outbox client
def create_outbox_op(conn, is_pg, op_id, sync_id, op_type, payload, base_snapshot,
                     base_rev, local_row_rev):
    q = ph(is_pg)
    payload_json = json.dumps({
        "sync_id": sync_id, "op_type": op_type, "payload": payload,
        "base": base_snapshot, "base_rev": base_rev,
        "local_row_rev": local_row_rev, "created_at": now_utc(),
    }, default=str, separators=(",", ":"))
    execute(conn, is_pg,
            "INSERT INTO outbox (op_id, sync_id, op_type, payload_json, base_rev, "
            "status, attempts, next_retry_at, last_error, created_at, updated_at) "
            "VALUES (%s,%s,%s,%s,%s,'pending',0,NULL,NULL,%s,%s)"
            % (q, q, q, q, q, q, q),
            (op_id, sync_id, op_type, payload_json, base_rev, now_utc(), now_utc()))


def outbox_rows(conn, is_pg, statuses=(P.OUTBOX_PENDING,)):
    q = ph(is_pg)
    marks = ",".join([q] * len(statuses))
    rows = fetch_all(conn, is_pg,
                     "SELECT op_id, sync_id, op_type, payload_json, base_rev, status, "
                     "attempts, next_retry_at, last_error FROM outbox "
                     "WHERE status IN (%s) ORDER BY id" % marks, statuses)
    out = []
    for r in rows:
        d = dict(zip(["op_id", "sync_id", "op_type", "payload_json", "base_rev",
                      "status", "attempts", "next_retry_at", "last_error"], r))
        d["payload"] = json.loads(d.pop("payload_json"))
        out.append(d)
    return out


def set_outbox_status(conn, is_pg, op_id, status, last_error=None, attempts=None):
    q = ph(is_pg)
    sql = "UPDATE outbox SET status=%s, updated_at=%s" % (q, q)
    params = [status, now_utc()]
    if last_error is not None:
        sql += ", last_error=%s" % q
        params.append(last_error)
    if attempts is not None:
        sql += ", attempts=%s" % q
        params.append(attempts)
    sql += " WHERE op_id=%s" % q
    params.append(op_id)
    execute(conn, is_pg, sql, tuple(params))


def fail_outbox_retry(conn, is_pg, op_id, attempts, last_error, next_retry_at):
    q = ph(is_pg)
    execute(conn, is_pg,
            "UPDATE outbox SET status='pending', attempts=%s, last_error=%s, "
            "next_retry_at=%s, updated_at=%s WHERE op_id=%s"
            % (q, q, q, q, q),
            (attempts, last_error, next_retry_at, now_utc(), op_id))


def mark_in_flight_back_to_pending(conn, is_pg):
    q = ph(is_pg)
    execute(conn, is_pg,
            "UPDATE outbox SET status='pending', updated_at=%s "
            "WHERE status='in_flight'" % q, (now_utc(),))


def coalesce_upserts(conn, is_pg):
    """Keep the LATEST payload but the OLDEST base ancestor/base_rev for pending
    upserts sharing a sync_id; older ops become superseded. Never coalesces delete
    ops and never drops the conflict-detection ancestor."""
    q = ph(is_pg)
    rows = fetch_all(conn, is_pg,
                     "SELECT id, op_id, sync_id, payload_json, base_rev, created_at "
                     "FROM outbox WHERE status IN ('pending','in_flight') "
                     "AND op_type='upsert' ORDER BY id")
    by_sid = {}
    for r in rows:
        d = dict(zip(["id", "op_id", "sync_id", "payload_json", "base_rev",
                      "created_at"], r))
        by_sid.setdefault(d["sync_id"], []).append(d)
    superseded = []
    for sid, ops in by_sid.items():
        if len(ops) < 2:
            continue
        first, last = ops[0], ops[-1]
        for old in ops[:-1]:
            superseded.append(old["op_id"])
        # The survivor keeps its latest payload but must retain the OLDEST base
        # ancestor (state before the first of these edits) for conflict detection.
        first_payload = json.loads(first["payload_json"])
        last_payload = json.loads(last["payload_json"])
        last_payload["base"] = first_payload.get("base")
        last_payload["base_rev"] = first_payload.get("base_rev", 0)
        execute(conn, is_pg,
                "UPDATE outbox SET payload_json=%s, base_rev=%s, updated_at=%s "
                "WHERE id=%s" % (q, q, q, q),
                (json.dumps(last_payload, default=str, separators=(",", ":")),
                 int(first["base_rev"]), now_utc(), last["id"]))
    for op_id in superseded:
        set_outbox_status(conn, is_pg, op_id, P.OUTBOX_SUPERSEDED)
    return superseded


def update_local_row(conn, is_pg, sync_id, business, deleted_at, base_json,
                     server_rev, row_rev, expect_server_rev=None):
    """Apply a fully resolved state to a client row (guarded by optional
    expect_server_rev so we never clobber a newer mutually-agreed state)."""
    q = ph(is_pg)
    sorted_fields = sorted(business)
    sets = ", ".join("%s=%s" % (f, q) for f in sorted_fields)
    params = [business[f] for f in sorted_fields]
    sql = ("UPDATE records SET %s, deleted_at=%s, base_json=%s, server_rev=%s, "
           "row_rev=%s" % (sets, q, q, q, q))
    params += [deleted_at, base_json, server_rev, row_rev]
    if expect_server_rev is not None:
        sql += " WHERE sync_id=%s AND server_rev=%s" % (q, q)
        params += [sync_id, expect_server_rev]
    else:
        sql += " WHERE sync_id=%s" % q
        params.append(sync_id)
    return execute(conn, is_pg, sql, tuple(params))


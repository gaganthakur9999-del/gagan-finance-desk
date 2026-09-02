"""
syncv2/server.py - server-side synchronization coordinator.

Owns:
  - the global monotonic server revision (sync_sequence)
  - the idempotency ledger (applied_ops)
  - optimistic concurrency (row server_rev vs client base_rev)
  - applying client ops after three-way merge
  - the incremental pull feed
  - conflict storage/retrieval/resolution

The server is the COORDINATOR, not the business-data master. Backend-agnostic:
is_pg=False runs the exact same logic against SQLite (tests), is_pg=True against
PostgreSQL/Neon.
"""
import json
import threading
import uuid

from . import protocol as P
from . import merge as M
from . import store as S

_SQLITE_REV_LOCK = threading.Lock()


class ConcurrentRowChange(RuntimeError):
    """Raised when an optimistic server-row write loses a same-record race.

    apply_one rolls the attempt back and retries against the refreshed row so
    the divergence is re-detected and surfaced as a conflict (never a silent
    last-writer-wins overwrite).
    """



class ConflictRecord:
    def __init__(self, sync_id, kind, field, base, offline, online, month="",
                 extra=None):
        self.sync_id = sync_id
        self.kind = kind
        self.field = field
        self.base = base
        self.offline = offline
        self.online = online
        self.month = month or ""
        self.extra = extra or {}

    def value(self, v):
        if isinstance(v, (dict, list, tuple)):
            return json.dumps(v, sort_keys=True, default=str, separators=(",", ":"))
        return v if v is not None else None


def open_conflict(conn, is_pg, rec):
    q = S.ph(is_pg)
    params = (rec.sync_id, rec.kind, rec.field,
              rec.value(rec.offline), rec.value(rec.online), rec.value(rec.base),
              rec.month, S.now_utc())
    if is_pg:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO conflicts (sync_id, kind, field_name, offline_value, "
            "online_value, base_value, month, status, resolution, "
            "resolution_op_id, created_at, resolved_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,'open',NULL,NULL,%s,NULL) "
            "RETURNING id" % (q, q, q, q, q, q, q, q), list(params))
        cid = cur.fetchone()[0]
        cur.close()
        return cid
    cur = conn.execute(
        "INSERT INTO conflicts (sync_id, kind, field_name, offline_value, "
        "online_value, base_value, month, status, resolution, "
        "resolution_op_id, created_at, resolved_at) "
        "VALUES (?,?,?,?,?,?,?,'open',NULL,NULL,?,NULL) RETURNING id",
        list(params))
    cid = cur.fetchone()[0]
    return cid


def open_conflicts_for(conn, is_pg, sync_id, status=P.CONFLICT_STATUS_OPEN):
    q = S.ph(is_pg)
    rows = S.fetch_all(conn, is_pg,
                       "SELECT id, sync_id, kind, field_name, offline_value, "
                       "online_value, base_value, month, status, resolution, "
                       "resolution_op_id, created_at FROM conflicts "
                       "WHERE sync_id=%s AND status=%s ORDER BY id" % (q, q),
                       (sync_id, status))
    out = []
    for r in rows:
        d = dict(zip(["id", "sync_id", "kind", "field_name", "offline_value",
                      "online_value", "base_value", "month", "status", "resolution",
                      "resolution_op_id", "created_at"], r))
        out.append(d)
    return out


def has_open_conflict(conn, is_pg, sync_id, kinds=None):
    q = S.ph(is_pg)
    if kinds is None:
        kinds = list(P.BLOCKING_CONFLICT_KINDS)
    marks = ",".join([q] * len(kinds))
    rows = S.fetch_all(conn, is_pg,
                       "SELECT 1 FROM conflicts WHERE sync_id=%s AND status='open' "
                       "AND kind IN (%s) LIMIT 1" % (q, marks),
                       [sync_id] + kinds)
    return bool(rows)


def read_sequence(conn, is_pg):
    q = S.ph(is_pg)
    rows = S.fetch_all(conn, is_pg, "SELECT value FROM sync_sequence WHERE id=1")
    return rows[0][0] if rows else 0


def next_revision(conn, is_pg):
    """Allocate the next global server revision transactionally.

    PostgreSQL: row lock via UPDATE on sync_sequence serializes concurrent
    requests. SQLite: an in-process lock serializes allocation (documented as
    single-process limitation; cross-process server use is PostgreSQL).
    """
    if is_pg:
        q = S.ph(is_pg)
        cur = conn.cursor()
        cur.execute("UPDATE sync_sequence SET value=value+1 WHERE id=1")
        cur.execute("SELECT value FROM sync_sequence WHERE id=1")
        v = cur.fetchone()[0]
        cur.close()
        return v
    with _SQLITE_REV_LOCK:
        conn.execute("UPDATE sync_sequence SET value=value+1 WHERE id=1")
        return conn.execute("SELECT value FROM sync_sequence WHERE id=1").fetchone()[0]


def _recorded_result(conn, is_pg, op_id):
    q = S.ph(is_pg)
    rows = S.fetch_all(conn, is_pg,
                       "SELECT result, server_rev_after FROM applied_ops "
                       "WHERE op_id=%s" % q, (op_id,))
    if not rows:
        return None
    return {"op_id": op_id, "replayed": True,
            "result": rows[0][0], "server_rev_after": rows[0][1]}


def record_applied(conn, is_pg, op_id, sync_id, op_type, result, rev_after,
                   conflict_json=None):
    q = S.ph(is_pg)
    S.execute(conn, is_pg,
              "INSERT INTO applied_ops (op_id, sync_id, op_type, result, "
              "server_rev_after, conflict_json, applied_at) "
              "VALUES (%s,%s,%s,%s,%s,%s,%s)" % (q, q, q, q, q, q, q),
              (op_id, sync_id, op_type, result, rev_after,
               json.dumps(conflict_json, sort_keys=True, default=str) if conflict_json else None,
               S.now_utc()))


def _business_of(row):
    return {f: row.get(f) for f in P.BUSINESS_FIELDS}


def _base_dict(row):
    if row is None:
        return {f: None for f in P.BUSINESS_FIELDS}
    return S.decode_base(row.get("base_json"))


def _changed_fields(base, cur):
    return [f for f in P.BUSINESS_FIELDS if not M.values_equal(f, base.get(f), cur.get(f))]


def _invoice_owners(conn, is_pg):
    rows = S.fetch_all(conn, is_pg,
                       "SELECT sync_id, invoice_no FROM records "
                       "WHERE invoice_no IS NOT NULL AND invoice_no <> ''")
    owners = {}
    for sync_id, inv in rows:
        k = M.normalized_invoice(inv)
        if k:
            owners.setdefault(k, set()).add(sync_id)
    return owners


def _conflict_result(conn, is_pg, op_id, sync_id, kind, field, base_v, off_v, on_v,
                     month="", merge=None):
    cid = open_conflict(conn, is_pg, ConflictRecord(
        sync_id, kind, field, base_v, off_v, on_v, month=month,
        extra={"op_id": op_id, "merge": (merge or {})}))
    return {"op_id": op_id, "result": "conflict", "server_rev_after": None,
            "conflict_ids": [cid], "conflicts": 1}


def write_server_row(conn, is_pg, sync_id, business, deleted_at, base_json, rev,
                     expected_server_rev=None):
    """Upsert a fully-resolved server row under a new revision.

    Returns 'inserted' or 'updated'. On update the WHERE guard aborts when a
    concurrent writer already changed the row (rowcount != 1 -> caller rollback).
    `expected_server_rev` (when given) makes the update an optimistic write: the
    row is only replaced if its server_rev still equals the revision this op
    merged against, closing the read-merge-write race between two simultaneous
    writers of the SAME record (no silent last-writer-wins loss).
    """
    q = S.ph(is_pg)
    existing = S.fetch_all(conn, is_pg, "SELECT 1 FROM records WHERE sync_id=%s" % q,
                           (sync_id,))
    if not existing:
        cols = sorted(business) + ["sync_id", "deleted_at", "base_json", "server_rev",
                                   "row_rev", "created_at", "updated_at"]
        marks = ",".join([q] * len(cols))
        params = [business[f] for f in sorted(business)]
        params += [sync_id, deleted_at, base_json, rev, 0, S.now_utc(), S.now_utc()]
        S.execute(conn, is_pg, "INSERT INTO records (%s) VALUES (%s)"
                  % (",".join(cols), marks), params)
        return "inserted"
    sets = ", ".join("%s=%s" % (f, q) for f in sorted(business))
    params = [business[f] for f in sorted(business)]
    if expected_server_rev is not None:
        sql = ("UPDATE records SET %s, deleted_at=%s, base_json=%s, server_rev=%s, "
               "row_rev=0, updated_at=%s WHERE sync_id=%s AND server_rev=%s"
               % (sets, q, q, q, q, q, q))
        params += [deleted_at, base_json, rev, S.now_utc(), sync_id,
                   int(expected_server_rev)]
    else:
        sql = ("UPDATE records SET %s, deleted_at=%s, base_json=%s, server_rev=%s, "
               "row_rev=0, updated_at=%s WHERE sync_id=%s" % (sets, q, q, q, q, q))
        params += [deleted_at, base_json, rev, S.now_utc(), sync_id]
    n = S.execute(conn, is_pg, sql, tuple(params))
    if n != 1:
        raise ConcurrentRowChange("server row %s changed concurrently (rowcount %s)"
                                  % (sync_id, n))
    return "updated"



def apply_one(conn, is_pg, op):
    """Apply one client op with a bounded retry when a concurrent writer of the
    SAME record commits between this op's merge read and its row write.

    Optimistic-concurrency fix (real-PG Part F finding): the merge used to be
    computed against a snapshot and written WITHOUT a row-level guard, so two
    simultaneous same-record writers could both report 'applied' while the
    second silently overwrote the first. Now every update carries the
    server_rev the op merged against; on ConcurrentRowChange the attempt is
    rolled back (no revision consumed) and re-run against the refreshed row,
    which re-detects the divergence and opens a genuine conflict.

    result: 'noop' | 'applied' | 'conflict'. On conflict nothing is applied, the
    baseline is not advanced, the op is NOT recorded as applied, and a conflict
    row preserves base/offline/online for later resolution.
    """
    for _attempt in (1, 2, 3):
        try:
            return _apply_one_try(conn, is_pg, op)
        except ConcurrentRowChange:
            S.rollback(conn, is_pg)
    raise ConcurrentRowChange("server row for op %s kept moving across retries"
                              % (op.get("op_id"),))


def _apply_one_try(conn, is_pg, op):
    op_id, sync_id = op["op_id"], op["sync_id"]
    replay = _recorded_result(conn, is_pg, op_id)
    if replay:
        return replay  # stored result; do NOT re-apply, do NOT bump the revision.
    if not isinstance(op_id, str) or not op_id or not sync_id:
        raise ValueError("malformed op (op_id/sync_id required)")
    if has_open_conflict(conn, is_pg, sync_id):
        cids = [c["id"] for c in open_conflicts_for(conn, is_pg, sync_id)]
        return {"op_id": op_id, "result": "conflict", "server_rev_after": None,
                "conflict_ids": cids, "replayed": False}

    op_type = op.get("op_type", P.OP_UPSERT)
    payload = op["payload"]
    base_snap = op.get("base") or {f: None for f in P.BUSINESS_FIELDS}
    server_row = S.read_row_full(conn, is_pg, sync_id)

    if op_type == P.OP_DELETE:
        if server_row is None:
            raise ValueError("delete op for unknown sync_id %s" % sync_id)
        if server_row.get("deleted_at"):
            return {"op_id": op_id, "result": "noop", "server_rev_after": None,
                    "reason": "already deleted"}
        base_snap_d = op.get("base") or _base_dict(server_row)
        # Delete vs edit: if the server changed business fields since the client's
        # ancestor, the delete must NOT silently destroy that edit.
        if _changed_fields(base_snap_d, _business_of(server_row)):
            return _conflict_result(conn, is_pg, op_id, sync_id,
                                    P.CONFLICT_DELETE_EDIT, "deleted_at",
                                    base_snap_d.get("deleted_at"),
                                    payload.get("deleted_at") or S.now_utc(),
                                    server_row.get("deleted_at"))
        rev = next_revision(conn, is_pg)
        deleted_at = payload.get("deleted_at") or S.now_utc()
        base_json = json.dumps(_business_of(server_row), sort_keys=True, default=str,
                               separators=(",", ":"))
        write_server_row(conn, is_pg, sync_id, _business_of(server_row),
                         deleted_at, base_json, rev,
                         expected_server_rev=int(server_row.get("server_rev") or 0))
        record_applied(conn, is_pg, op_id, sync_id, op_type, "applied", rev)
        return {"op_id": op_id, "result": "applied", "server_rev_after": rev}

    if server_row is None:
        if op.get("base_rev", 0) != 0 and any(v is not None for v in base_snap.values()):
            raise ValueError("create op carries a non-empty base - impossible state")
        rev = next_revision(conn, is_pg)
        resolved = {f: payload.get(f) for f in P.BUSINESS_FIELDS}
        resolved["month"] = M.month_from_bid_date(resolved.get("bid_date")) \
            or resolved.get("month")
        write_server_row(conn, is_pg, sync_id, resolved, None,
                         json.dumps(resolved, sort_keys=True, default=str, separators=(",", ":")), rev)
        record_applied(conn, is_pg, op_id, sync_id, op_type, "applied", rev)
        return {"op_id": op_id, "result": "applied", "server_rev_after": rev}

    # ---- existing record: three-way client vs server, relative to op base ----
    online_cur = _business_of(server_row)
    online_deleted = server_row.get("deleted_at")
    client_deleted = payload.get("deleted_at")
    client_fields = {f: payload.get(f) for f in P.BUSINESS_FIELDS}

    merge = M.merge_business(base_snap, client_fields, online_cur)
    if merge["conflicts"]:
        # Persist ONE conflict record per genuinely conflicting field so every
        # divergent field stays individually resolvable (never collapses to the
        # first conflict only).
        ids = []
        for c in merge["conflicts"]:
            ids.append(open_conflict(conn, is_pg, ConflictRecord(
                sync_id, c["kind"], c["field"], c["base"], c["offline"], c["online"],
                extra={"op_id": op_id, "merge": merge})))
        return {"op_id": op_id, "result": "conflict", "server_rev_after": None,
                "conflict_ids": ids, "conflicts": len(ids)}

    # Stale edit against a server tombstone: an offline edit (record still alive
    # locally) racing a remote delete must surface as delete-vs-edit, never
    # silently mutate the tombstone's business payload. (No resurrection: if the
    # conflict is kept ONLINE the row stays deleted.)
    if online_deleted and not client_deleted and \
            _changed_fields(base_snap, client_fields):
        return _conflict_result(conn, is_pg, op_id, sync_id, P.CONFLICT_DELETE_EDIT,
                                "deleted_at", base_snap.get("deleted_at"),
                                client_deleted, online_deleted, merge=merge)

    base_alive = not bool(base_snap.get("deleted_at"))
    off_alive = not bool(client_deleted)
    on_alive = not bool(online_deleted)
    del_act, del_why = M.reconcile_tombstone(
        base_alive, off_alive, on_alive,
        off_edit=bool(_changed_fields(base_snap, client_fields)),
        on_edit=bool(_changed_fields(base_snap, online_cur)))
    if del_act == "conflict":
        return _conflict_result(conn, is_pg, op_id, sync_id, P.CONFLICT_DELETE_EDIT,
                                "deleted_at", base_snap.get("deleted_at"),
                                client_deleted, online_deleted, merge=merge)

    resolved = dict(merge["resolved"])

    # Invoice: client-side change applies only after a collision check.
    inv_action, _, _ = M.classify_field(P.INVOICE_FIELD, base_snap, client_fields,
                                        online_cur)
    if inv_action == M.FIELD_CONFLICT:
        return _conflict_result(conn, is_pg, op_id, sync_id, "field", "invoice_no",
                                base_snap.get("invoice_no"),
                                client_fields.get("invoice_no"),
                                online_cur.get("invoice_no"), merge=merge)
    inv_value = (client_fields if inv_action == M.FIELD_USE_OFFLINE
                 else online_cur).get("invoice_no")
    resolved["invoice_no"] = inv_value

    # SR ordering is applied row-level here; month-scope grouping is engine-side.
    sr_action, _, _ = M.classify_field(P.SR_FIELD, base_snap, client_fields, online_cur)
    if sr_action == M.FIELD_CONFLICT:
        return _conflict_result(conn, is_pg, op_id, sync_id, P.CONFLICT_SR_ORDER,
                                "sr_no", base_snap.get("sr_no"),
                                client_fields.get("sr_no"),
                                online_cur.get("sr_no"), month=payload.get("month") or "",
                                merge=merge)
    resolved["sr_no"] = (client_fields.get("sr_no") if sr_action == M.FIELD_USE_OFFLINE
                         else online_cur.get("sr_no"))
    resolved["month"] = M.month_from_bid_date(resolved.get("bid_date")) \
        or resolved.get("month")

    final_deleted = None
    if del_act == "apply":
        if not on_alive and off_alive:
            final_deleted = online_deleted
        elif not off_alive and on_alive:
            final_deleted = client_deleted
        elif not off_alive and not on_alive:
            final_deleted = client_deleted or online_deleted

    advisory = []
    if inv_action == M.FIELD_USE_OFFLINE and M.normalized_invoice(inv_value):
        col = M.detect_invoice_collision(inv_value, sync_id,
                                         _invoice_owners(conn, is_pg))
        if col:
            open_conflict(conn, is_pg, ConflictRecord(
                sync_id, P.CONFLICT_INVOICE, "invoice_no", base_snap.get("invoice_no"),
                inv_value, online_cur.get("invoice_no"), extra=col))
            advisory.append(col)

    rev = next_revision(conn, is_pg)
    base_json = json.dumps(resolved, sort_keys=True, default=str, separators=(",", ":"))
    write_server_row(conn, is_pg, sync_id, resolved, final_deleted, base_json, rev,
                     expected_server_rev=int(server_row.get("server_rev") or 0))
    record_applied(conn, is_pg, op_id, sync_id, op_type, "applied", rev,
                   conflict_json=advisory or None)
    return {"op_id": op_id, "result": "applied", "server_rev_after": rev,
            "conflict_ids": []}


def _open_field_conflicts(conn, is_pg, op):
    """Open one conflict per genuinely conflicting non-SR field for an op."""
    payload = op.get("payload") or {}
    base_snap = op.get("base") or {f: None for f in P.BUSINESS_FIELDS}
    sync_id = op["sync_id"]
    server_row = S.read_row_full(conn, is_pg, sync_id)
    client_fields = {f: payload.get(f) for f in P.BUSINESS_FIELDS}
    if server_row is None:
        return []
    online_cur = _business_of(server_row)
    merge = M.merge_business(base_snap, client_fields, online_cur)
    ids = []
    for c in merge.get("conflicts", []):
        ids.append(open_conflict(conn, is_pg, ConflictRecord(
            sync_id, c["kind"], c["field"], c["base"], c["offline"], c["online"],
            extra={"op_id": op.get("op_id"), "merge": merge})))
    return ids


def _sr_group_plan(conn, is_pg, ops):
    """Detect month-scoped grouped SR conflicts across a delivered batch.

    Returns {op_index: (month, conflict_id)}. Only pure reorders (same month on
    both sides, base month == payload month) are grouped; the group conflict is
    opened once per month with deterministic BASE/OFFLINE/ONLINE sequences.
    """
    import json as _json
    q = S.ph(is_pg)
    month_ops = {}
    for idx, op in enumerate(ops):
        if op.get("op_type", P.OP_UPSERT) != P.OP_UPSERT:
            continue
        payload, base = op.get("payload") or {}, op.get("base") or {}
        if M.values_equal("sr_no", payload.get("sr_no"), base.get("sr_no")):
            continue
        pm = M.month_from_bid_date(payload.get("bid_date")) or payload.get("month") or ""
        bm = M.month_from_bid_date(base.get("bid_date")) or base.get("month") or ""
        if not pm or pm != bm:
            continue  # month-membership moves are handled at row level
        month_ops.setdefault(pm, []).append((idx, op))

    plan = {}
    for month, entries in month_ops.items():
        rows = S.fetch_all(conn, is_pg,
                           "SELECT sync_id, sr_no, base_json FROM records "
                           "WHERE month=%s AND deleted_at IS NULL" % q, (month,))
        by_id = {r[0]: r for r in rows}
        base_sr, cur_sr = {}, {}
        op_base_sr = {}
        for idx, op in entries:
            ob = op.get("base") or {}
            if op["sync_id"] in by_id:
                try:
                    op_base_sr[op["sync_id"]] = int(ob.get("sr_no") or 0)
                except (TypeError, ValueError):
                    op_base_sr[op["sync_id"]] = 0
        for r in rows:
            try:
                base_sr[r[0]] = int((_json.loads(r[2] or "{}") or {}).get("sr_no") or 0)
            except (ValueError, TypeError):
                base_sr[r[0]] = 0
            cur_sr[r[0]] = int(r[1] or 0)
        # The true month ancestor is the OFFLINE op base (the last mutually
        # agreed ordering), NOT the server's already-advanced base_json.
        for sid, v in op_base_sr.items():
            base_sr[sid] = v
        base_seq = [r[0] for r in sorted(rows, key=lambda r: (base_sr.get(r[0], 0),
                                                              str(r[0])))]
        online_seq = [r[0] for r in sorted(rows, key=lambda r: (cur_sr.get(r[0], 0),
                                                                str(r[0])))]
        target = dict(base_sr)
        for idx, op in entries:
            if op["sync_id"] in by_id:
                target[op["sync_id"]] = int(op["payload"].get("sr_no") or 0)
        offline_seq = [sid for sid in sorted(by_id, key=lambda s: (target.get(s, 0), s))]
        decision = M.reconcile_sr(base_seq, offline_seq, online_seq)
        if decision["action"] != "conflict":
            continue
        # Reuse an already-open grouped SR conflict for this month so a month
        # whose reorder spans multiple batches never creates duplicate groups.
        existing = S.fetch_all(conn, is_pg,
                               "SELECT id FROM conflicts WHERE kind=%s AND status='open' "
                               "AND month=%s LIMIT 1" % (q, q),
                               (P.CONFLICT_SR_ORDER, month))
        if existing:
            cid = existing[0][0]
        else:
            cid = open_conflict(conn, is_pg, ConflictRecord(
                entries[0][1]["sync_id"], P.CONFLICT_SR_ORDER, "sr_no",
                base_seq, offline_seq, online_seq, month=month,
                extra={"batch_sync_ids": sorted(by_id)}))
        for idx, op in entries:
            plan[idx] = (month, cid)
    return plan


def apply_ops(conn, is_pg, ops):
    """Apply a batch of operations; each op commits in its OWN transaction.

    Month-scoped grouped SR conflicts are detected first and open ONE conflict
    record per affected month (their ops are parked, never partially applied).
    On the first op error the batch stops (later ops are not processed) and an
    'error' result is returned; earlier ops are already committed + recorded, so
    they will never be resent as new work. Retrying the same op_id replays the
    stored result and never re-applies.
    """
    results = []
    group_plan = _sr_group_plan(conn, is_pg, ops)
    opened_group = bool(group_plan)
    try:
        for idx, op in enumerate(ops):
            if idx in group_plan:
                month, gcid = group_plan[idx]
                ids = _open_field_conflicts(conn, is_pg, op)
                conflict_ids = [gcid] + ids
                results.append({"op_id": op.get("op_id"), "result": "conflict",
                                "server_rev_after": None,
                                "conflict_ids": conflict_ids,
                                "conflicts": len(conflict_ids),
                                "grouped_sr_month": month,
                                "grouped_sr_conflict_id": gcid})
                continue
            try:
                results.append(apply_one(conn, is_pg, op))
                S.commit(conn, is_pg)   # each op commits independently (partial batches)
            except Exception as exc:  # noqa: BLE001 - surface as structured result
                S.rollback(conn, is_pg)
                results.append({"op_id": op.get("op_id"), "result": "error",
                                "error": "%s: %s" % (type(exc).__name__, exc)})
                break
        if opened_group:
            # Group + any field conflict rows for parked ops are durable together.
            S.commit(conn, is_pg)
        return {"results": results, "revision": read_sequence(conn, is_pg),
                "stopped_at_error": any(r.get("result") == "error" for r in results)}
    except Exception:
        S.rollback(conn, is_pg)
        raise


def list_open_conflicts(conn, is_pg, status=P.CONFLICT_STATUS_OPEN):
    """Read-only list of open (or resolved) conflicts for presentation."""
    q = S.ph(is_pg)
    rows = S.fetch_all(conn, is_pg,
                       "SELECT id, sync_id, kind, field_name, offline_value, "
                       "online_value, base_value, month, status, created_at "
                       "FROM conflicts WHERE status=%s ORDER BY id" % q, (status,))
    return [dict(zip(["id", "sync_id", "kind", "field_name", "offline_value",
                      "online_value", "base_value", "month", "status",
                      "created_at"], r)) for r in rows]


def pull_changes(conn, is_pg, since_rev):
    """Incremental pull: rows whose records.server_rev > since_rev.

    Returns (current_max_revision, rows). Each row includes business fields,
    server_rev, deleted_at and base_json. Tombstones travel as rows with
    deleted_at set - never physical deletion.
    """
    q = S.ph(is_pg)
    rows = S.fetch_all(conn, is_pg,
                       "SELECT %s, sync_id, server_rev, row_rev, base_json, deleted_at "
                       "FROM records WHERE server_rev > %s ORDER BY server_rev, id"
                       % (_biz_cols(), q), (int(since_rev),))
    names = P.BUSINESS_FIELDS + ["sync_id", "server_rev", "row_rev",
                                 "base_json", "deleted_at"]
    out = [dict(zip(names, r)) for r in rows]
    return read_sequence(conn, is_pg), out


def _biz_cols():
    return ",".join(P.BUSINESS_FIELDS)


def _as_deleted(value):
    """Interpret a conflict column value as a tombstone timestamp or None."""
    if value is None:
        return None
    s = str(value)
    return s if s and s.strip().lower() not in ("", "null", "none") else None


def _reopen_conflict(conn, is_pg, conflict_id):
    q = S.ph(is_pg)
    S.execute(conn, is_pg,
              "UPDATE conflicts SET status='open', resolved_at=NULL WHERE id=%s" % q,
              (str(conflict_id),))
    conn.commit()
    return {"conflict_id": conflict_id, "reopened": True,
            "reason": "server moved again after the conflict was recorded"}


def resolve_conflict(conn, is_pg, conflict_id, choice, resolution_payload=None):
    """Backend conflict resolution (KEEP_OFFLINE / KEEP_ONLINE / MERGE).

    A successful resolution:
      - applies the chosen value deterministically,
      - sets the server base_json to the RESOLVED snapshot (never the stale
        pre-resolution snapshot), so later resolutions on the same record do
        NOT deterministically reopen forever,
      - allocates exactly one new revision for the actual resolution,
      - is idempotent (already-resolved -> replay without a new revision),
      - re-opens instead of blind-applying when THIS conflict's own state moved.
    """
    q = S.ph(is_pg)
    rows = S.fetch_all(conn, is_pg,
                       "SELECT id, sync_id, kind, field_name, offline_value, "
                       "online_value, base_value, month, status FROM conflicts "
                       "WHERE id=%s" % q, (str(conflict_id),))
    if not rows:
        raise ValueError("unknown conflict id %s" % conflict_id)
    c = dict(zip(["id", "sync_id", "kind", "field_name", "offline_value",
                  "online_value", "base_value", "month", "status"], rows[0]))
    if c["status"] == P.CONFLICT_STATUS_RESOLVED:
        return {"conflict_id": conflict_id, "replayed": True, "sync_id": c["sync_id"],
                "field_name": c["field_name"], "kind": c["kind"], "month": c["month"]}
    if choice not in ("KEEP_OFFLINE", "KEEP_ONLINE", "MERGE"):
        raise ValueError("unsupported resolution choice %s" % choice)
    if c["kind"] == P.CONFLICT_SR_ORDER:
        return _resolve_sr_conflict(conn, is_pg, c, choice, resolution_payload)

    sync_id = c["sync_id"]
    server_row = S.read_row_full(conn, is_pg, sync_id)
    if server_row is None:
        raise ValueError("server row missing for conflict %s" % conflict_id)
    try:
        field = c["field_name"]
        resolved = dict(_business_of(server_row))
        deleted = None
        # Reopen guard: re-open only when THIS conflict's field moved again.
        # Resolving a sibling conflict field of the same record is expected.
        if c["kind"] in (P.CONFLICT_FIELD, P.CONFLICT_FINANCIAL, P.CONFLICT_SERIAL):
            recorded_online = c.get("online_value")
            if not M.values_equal(field, recorded_online, server_row.get(field)):
                return _reopen_conflict(conn, is_pg, conflict_id)
            if choice == "KEEP_OFFLINE":
                resolved[field] = c.get("offline_value")
            elif choice == "KEEP_ONLINE":
                resolved[field] = recorded_online
            else:
                if not resolution_payload or "value" not in resolution_payload:
                    raise ValueError("MERGE requires resolution_payload['value']")
                resolved[field] = resolution_payload["value"]
        elif c["kind"] == P.CONFLICT_DELETE_EDIT:
            recorded_online_deleted = bool(_as_deleted(c.get("online_value")))
            if bool(server_row.get("deleted_at")) != recorded_online_deleted:
                return _reopen_conflict(conn, is_pg, conflict_id)
            target = c.get("online_value") if choice == "KEEP_ONLINE" \
                else c.get("offline_value")
            deleted = _as_deleted(target)
        else:
            raise ValueError("resolution unsupported for kind %s" % c["kind"])

        rev = next_revision(conn, is_pg)
        resolved["month"] = M.month_from_bid_date(resolved.get("bid_date")) \
            or resolved.get("month")
        # FIX: the new ancestor is the RESOLVED snapshot, never the stale
        # pre-resolution server state.
        base_json = json.dumps(resolved, sort_keys=True, default=str,
                               separators=(",", ":"))
        write_server_row(conn, is_pg, sync_id, resolved, deleted, base_json, rev)
        S.execute(conn, is_pg,
                  "UPDATE conflicts SET status='resolved', resolution=%s, "
                  "resolved_at=%s WHERE id=%s" % (q, q, q),
                  (choice, S.now_utc(), str(conflict_id)))
        conn.commit()
        return {"conflict_id": conflict_id, "applied_revision": rev, "replayed": False,
                "sync_id": sync_id, "field_name": field, "kind": c["kind"],
                "resolved": resolved, "deleted_at": deleted,
                "server_rev_after": rev}
    except Exception:
        S.rollback(conn, is_pg)
        raise


def _resolve_sr_conflict(conn, is_pg, c, choice, resolution_payload):
    """Resolve a month-scoped grouped SR ordering conflict.

    Applies ONE deterministic month ordering to every affected server row (one
    new revision per row) and advances each row's base to its resolved snapshot.
    Returns the affected sync_ids so the engine can converge the Offline replica.
    """
    import json as _json
    q = S.ph(is_pg)
    try:
        def seq(value):
            if value is None:
                return []
            return _json.loads(value) if isinstance(value, str) else list(value)

        base_seq = seq(c.get("base_value"))
        offline_seq = seq(c.get("offline_value"))
        online_seq = seq(c.get("online_value"))
        if choice == "KEEP_OFFLINE":
            chosen = list(offline_seq)
        elif choice == "KEEP_ONLINE":
            chosen = list(online_seq)
        else:
            if not resolution_payload or "seq" not in resolution_payload:
                raise ValueError("MERGE for sr_ordering requires resolution_payload['seq']")
            chosen = list(resolution_payload["seq"])

        month = c.get("month") or ""
        rows = S.fetch_all(conn, is_pg,
                           "SELECT sync_id, sr_no FROM records "
                           "WHERE month=%s AND deleted_at IS NULL" % q, (month,))
        cur_online = [r[0] for r in sorted(
            rows, key=lambda r: (int(r[1] or 0), str(r[0])))]
        if cur_online != online_seq:
            return _reopen_conflict(conn, is_pg, c["id"])
        if set(chosen) != set(cur_online) or len(chosen) != len(set(chosen)):
            raise ValueError("sr ordering choice must contain exactly the month's "
                             "records (%d rows), no duplicates" % len(cur_online))

        last_rev = None
        affected = []
        for position, sid in enumerate(chosen, start=1):
            row = S.read_row_full(conn, is_pg, sid)
            if row is None:
                raise ValueError("sr conflict references missing row %s" % sid)
            business = _business_of(row)
            business["sr_no"] = position
            business["month"] = month
            rev = next_revision(conn, is_pg)
            base_json = json.dumps(business, sort_keys=True, default=str,
                                   separators=(",", ":"))
            write_server_row(conn, is_pg, sid, business, row.get("deleted_at"),
                             base_json, rev)
            last_rev = rev
            affected.append(sid)
        S.execute(conn, is_pg,
                  "UPDATE conflicts SET status='resolved', resolution=%s, "
                  "resolved_at=%s WHERE id=%s" % (q, q, q),
                  (choice, S.now_utc(), str(c["id"])))
        conn.commit()
        return {"conflict_id": c["id"], "applied_revision": last_rev, "replayed": False,
                "sync_id": c["sync_id"], "kind": P.CONFLICT_SR_ORDER,
                "sr_resolution": True, "affected_sync_ids": affected,
                "month": month, "server_rev_after": last_rev}
    except Exception:
        S.rollback(conn, is_pg)
        raise





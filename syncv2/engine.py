"""
syncv2/engine.py - the client-side SyncEngine.

SyncEngine orchestrates a full sync session against the server coordinator:

    IDLE -> CONNECTING -> PULL -> MERGE -> PUSH -> FINALIZE -> IDLE

Failure states: OFFLINE / ERROR / CONFLICT / NEEDS_ATTENTION / BUSY.

Engine responsibilities:
  - single-flight (one sync per local database at a time; in-process + advisory
    lock file for other processes)
  - transactional "business change + outbox op" primitive (Phase-6 workflows use
    this; nothing in the current app calls it yet)
  - incremental pull, client three-way reconcile, outbox push, finalize
  - structured SyncResult, never UI strings, never streamlit.

Offline-first: the local SQLite database is the primary operational environment;
the server merely coordinates revisions/idempotency/conflicts.
"""
import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone

from . import protocol as P
from .protocol import SyncResult
from . import merge as M
from . import retry as R
from . import server as SVC
from . import store as S


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _make_base_json(business):
    return json.dumps({f: business.get(f) for f in P.BUSINESS_FIELDS},
                      sort_keys=True, default=str, ensure_ascii=True,
                      separators=(",", ":"))


def _read_state(conn, is_pg):
    q = S.ph(is_pg)
    rows = S.fetch_all(conn, is_pg,
                       "SELECT last_pulled_sync_rev, last_success_at, last_error, "
                       "conflict_count FROM sync_state WHERE id=1")
    if not rows:
        return {"last_pulled_sync_rev": 0, "last_success_at": None,
                "last_error": None, "conflict_count": 0}
    return dict(zip(["last_pulled_sync_rev", "last_success_at", "last_error",
                     "conflict_count"], rows[0]))


def _write_state(conn, is_pg, **kw):
    q = S.ph(is_pg)
    sets = ", ".join("%s=%s" % (k, q) for k in kw)
    S.execute(conn, is_pg, "UPDATE sync_state SET %s WHERE id=1" % sets,
              tuple(kw.values()))


class LockBusy(Exception):
    pass


class FileFlightLock:
    """Advisory cross-process single-flight lock (O_EXCL + stale timeout).

    Documented limitation: advisory only - a crashed process may leave a stale
    lock file until the stale age (default 600s) passes.
    """

    def __init__(self, path, stale_seconds=600):
        self.path = path
        self.stale = stale_seconds
        self.acquired = False

    def acquire(self, timeout=0.5):
        start = time.time()
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, ("pid=%d ts=%s" % (os.getpid(), time.time())).encode())
                os.close(fd)
                self.acquired = True
                return True
            except FileExistsError:
                try:
                    age = time.time() - os.path.getmtime(self.path)
                    if age > self.stale:
                        os.unlink(self.path)
                        continue
                except FileNotFoundError:
                    continue
                if time.time() - start > timeout:
                    raise LockBusy("another sync session holds the lock")
                time.sleep(0.05)

    def release(self):
        if self.acquired:
            try:
                os.unlink(self.path)
            except FileNotFoundError:
                pass
            self.acquired = False


class SyncEngine:
    """Client orchestrator. `client` = (conn, is_pg); `server` = SyncServerAdapter."""

    def __init__(self, client_conn, client_is_pg, server, lock_path=None):
        self._conn = client_conn
        self._pg = client_is_pg
        self._server = server            # adapter exposing pull()/apply_ops()/...
        self._flight = threading.Lock()
        self._file_lock = FileFlightLock(lock_path) if lock_path else None
        self.status = P.SESSION_IDLE

    # ------------------------------------------------------------ local primitives
    def begin_local_change(self, sync_id, business, op_type=P.OP_UPSERT,
                           deleted_at=None, reason="workflow"):
        """Transactional primitive: business row change + durable outbox op.

        If a record with sync_id does not exist locally it is created first, so
        Phase-6 workflows can call this atomically. On any error everything rolls
        back (no business row, no outbox entry).
        """
        conn, is_pg = self._conn, self._pg
        q = S.ph(is_pg)
        try:
            local = S.read_row_full(conn, is_pg, sync_id)
            op_id = str(uuid.uuid4())
            if local is None:
                cols = sorted(business) + ["sync_id", "row_rev", "created_at",
                                           "updated_at"]
                marks = ",".join([q] * len(cols))
                params = [business[f] for f in sorted(business)]
                params += [sync_id, 1, _now_iso(), _now_iso()]
                S.execute(conn, is_pg, "INSERT INTO records (%s) VALUES (%s)"
                          % (",".join(cols), marks), params)
                base_snap = {f: None for f in P.BUSINESS_FIELDS}
                base_rev = 0
            else:
                base_snap = S.decode_base(local.get("base_json"))
                base_rev = int(local.get("server_rev") or 0)
                new_row_rev = int(local.get("row_rev") or 0) + 1
                sets = ", ".join("%s=%s" % (f, q) for f in sorted(business))
                params = [business[f] for f in sorted(business)]
                sql = ("UPDATE records SET %s, row_rev=%s, updated_at=%s "
                       "WHERE sync_id=%s" % (sets, q, q, q))
                params += [new_row_rev, _now_iso(), sync_id]
                if S.execute(conn, is_pg, sql, tuple(params)) != 1:
                    raise RuntimeError("local row %s missing" % sync_id)
            payload = dict(business)
            if deleted_at:
                payload["deleted_at"] = deleted_at
            S.create_outbox_op(conn, is_pg, op_id, sync_id, op_type, payload,
                               base_snap, base_rev,
                               int(local["row_rev"]) + 1 if local else 1)
            S.commit(conn, is_pg)
            return op_id
        except Exception:
            S.rollback(conn, is_pg)
            raise

    # ---------------------------------------------------------------- pull+merge
    def _active_sync_ids(self):
        rows = S.outbox_rows(self._conn, self._pg,
                             statuses=(P.OUTBOX_PENDING, P.OUTBOX_IN_FLIGHT,
                                       P.OUTBOX_BLOCKED))
        return {r["sync_id"] for r in rows}

    def _insert_local(self, sync_id, business, deleted_at, base_json, server_rev):
        conn, is_pg = self._conn, self._pg
        q = S.ph(is_pg)
        cols = sorted(business) + ["sync_id", "deleted_at", "base_json", "server_rev",
                                   "row_rev", "created_at", "updated_at"]
        marks = ",".join([q] * len(cols))
        params = [business[f] for f in sorted(business)]
        params += [sync_id, deleted_at, base_json, int(server_rev or 0), 0,
                   _now_iso(), _now_iso()]
        S.execute(conn, is_pg, "INSERT INTO records (%s) VALUES (%s)"
                  % (",".join(cols), marks), params)

    def pull(self):
        """Pull + locally merge server changes after the stored watermark."""
        conn, is_pg = self._conn, self._pg
        state = _read_state(conn, is_pg)
        since = int(state["last_pulled_sync_rev"] or 0)
        maxrev, rows = self._server.pull(since)
        active = self._active_sync_ids()
        merged = 0
        needs_attention = []
        skipped_revs = []
        for row in rows:
            sid = row["sync_id"]
            if sid in active:
                skipped_revs.append(int(row.get("server_rev") or 0))
                continue  # local change pending; push reconciles, post-pull converges
            online_biz = {f: row.get(f) for f in P.BUSINESS_FIELDS}
            online_del = row.get("deleted_at")
            local = S.read_row_full(conn, is_pg, sid)
            if local is None:
                self._insert_local(sid, online_biz, online_del,
                                   row.get("base_json") or _make_base_json(online_biz),
                                   int(row.get("server_rev") or 0))
                merged += 1
                continue
            res = self._merge_client_state(sid, local, row)
            if res == "attention":
                needs_attention.append(sid)
                continue
            merged += 1
        if needs_attention:
            return {"status": P.SESSION_NEEDS_ATTENTION, "pulled": len(rows),
                    "merged": merged, "needs_attention": needs_attention,
                    "maxrev": maxrev}
        # Never advance the watermark past a skipped row: it must be re-fetched
        # after its pending op is pushed (otherwise its converged state is lost).
        if skipped_revs:
            new_watermark = max(since, min(skipped_revs) - 1)
        else:
            new_watermark = int(maxrev)
        _write_state(conn, is_pg, last_pulled_sync_rev=new_watermark)
        S.commit(conn, is_pg)
        return {"status": P.SESSION_SUCCESS, "pulled": len(rows), "merged": merged,
                "maxrev": int(maxrev), "needs_attention": []}

    def _merge_client_state(self, sid, local, online_row):
        """Three-way merge of a pulled server row into a local row with NO pending op."""
        conn, is_pg = self._conn, self._pg
        base_snap = S.decode_base(local.get("base_json"))
        offline_biz = {f: local.get(f) for f in P.BUSINESS_FIELDS}
        online_biz = {f: online_row.get(f) for f in P.BUSINESS_FIELDS}
        online_del = online_row.get("deleted_at")
        server_rev = int(online_row.get("server_rev") or 0)
        merge = M.merge_business(base_snap, offline_biz, online_biz)
        if merge["conflicts"]:
            return "attention"
        base_alive = not bool(base_snap.get("deleted_at"))
        off_alive = not bool(local.get("deleted_at"))
        on_alive = not bool(online_del)
        del_act, _ = M.reconcile_tombstone(base_alive, off_alive, on_alive,
                                           off_edit=False,
                                           on_edit=bool(_changed_any(base_snap, online_biz)))
        if del_act == "conflict":
            return "attention"
        resolved = dict(merge["resolved"])
        inv_action, _, _ = M.classify_field(P.INVOICE_FIELD, base_snap,
                                            offline_biz, online_biz)
        if inv_action == M.FIELD_CONFLICT:
            return "attention"
        resolved["invoice_no"] = (online_biz if inv_action == M.FIELD_USE_ONLINE
                                  else offline_biz).get("invoice_no")
        sr_action, _, _ = M.classify_field(P.SR_FIELD, base_snap, offline_biz, online_biz)
        if sr_action == M.FIELD_CONFLICT:
            return "attention"
        resolved["sr_no"] = (online_biz if sr_action == M.FIELD_USE_ONLINE
                             else offline_biz).get("sr_no")
        final_del = online_del if del_act == "apply" else local.get("deleted_at")
        n = S.update_local_row(conn, is_pg, sid, resolved, final_del,
                               _make_base_json(resolved), server_rev, 0)
        return "ok" if n == 1 else "attention"

    # ---------------------------------------------------------------- local writes
    def delete_local(self, sync_id, deleted_at=None):
        """Tombstone a local record + durable delete outbox op (transactional)."""
        conn, is_pg = self._conn, self._pg
        q = S.ph(is_pg)
        try:
            local = S.read_row_full(conn, is_pg, sync_id)
            if local is None:
                raise ValueError("cannot delete unknown local sync_id %s" % sync_id)
            op_id = str(uuid.uuid4())
            deleted_at = deleted_at or _now_iso()
            business = {f: local.get(f) for f in P.BUSINESS_FIELDS}
            S.execute(conn, is_pg,
                      "UPDATE records SET deleted_at=%s, row_rev=%s, updated_at=%s "
                      "WHERE sync_id=%s" % (q, q, q, q),
                      (deleted_at, int(local.get("row_rev") or 0) + 1, _now_iso(),
                       sync_id))
            payload = dict(business)
            payload["deleted_at"] = deleted_at
            S.create_outbox_op(conn, is_pg, op_id, sync_id, P.OP_DELETE, payload,
                               S.decode_base(local.get("base_json")),
                               int(local.get("server_rev") or 0),
                               int(local.get("row_rev") or 0) + 1)
            S.commit(conn, is_pg)
            return op_id
        except Exception:
            S.rollback(conn, is_pg)
            raise

    # ---------------------------------------------------------------- push
    def push(self, batch_size=50):
        """Push pending outbox ops; returns a structured dict."""
        conn, is_pg = self._conn, self._pg
        S.mark_in_flight_back_to_pending(conn, is_pg)
        S.coalesce_upserts(conn, is_pg)
        S.commit(conn, is_pg)
        pending = S.outbox_rows(conn, is_pg, statuses=(P.OUTBOX_PENDING,))
        if not pending:
            return {"status": P.SESSION_SUCCESS, "pushed": 0, "conflicts": 0,
                    "failed": 0, "message": "no pending ops"}
        pushed = conflicts = failed = 0
        blocked = []
        for start in range(0, len(pending), batch_size):
            chunk = pending[start:start + batch_size]
            for op in chunk:
                _set_outbox_status(conn, is_pg, op["op_id"], P.OUTBOX_IN_FLIGHT)
            send = [_build_send_op(op) for op in chunk]
            resp = self._server.apply_ops(send)
            processed = 0
            for op, result in zip(chunk, resp["results"]):
                kind = _classify_outcome(result)
                if kind in ("applied", "noop"):
                    _set_outbox_status(conn, is_pg, op["op_id"], P.OUTBOX_APPLIED)
                    pushed += 1
                    processed += 1
                elif kind == "conflict":
                    _set_outbox_status(conn, is_pg, op["op_id"], P.OUTBOX_BLOCKED,
                                       last_error="open conflict; resolve first")
                    blocked.append(op["op_id"])
                    conflicts += 1
                    processed += 1
                else:
                    err = result.get("error") or "unknown error"
                    # Ops after the failing one were not processed: requeue them.
                    for op_rest in chunk[processed + 1:]:
                        _set_outbox_status(conn, is_pg, op_rest["op_id"],
                                           P.OUTBOX_PENDING)
                    if _text_is_permanent(err):
                        _set_outbox_status(conn, is_pg, op["op_id"], P.OUTBOX_FAILED,
                                           last_error=err)
                        failed += 1
                        return {"status": P.SESSION_NEEDS_ATTENTION, "pushed": pushed,
                                "conflicts": conflicts, "failed": failed,
                                "message": err}
                    _retry_op(conn, is_pg, op, err)
                    failed += 1
                    return {"status": P.SESSION_OFFLINE, "pushed": pushed,
                            "conflicts": conflicts, "failed": failed,
                            "message": err}
            # Any ops the server did not process (stopped before end of chunk).
            for op_rest in chunk[processed:]:
                _set_outbox_status(conn, is_pg, op_rest["op_id"], P.OUTBOX_PENDING)
        return {"status": P.SESSION_SUCCESS, "pushed": pushed, "conflicts": conflicts,
                "failed": failed, "blocked": blocked}

    # ---------------------------------------------------------------- session
    def _acquire_file_lock(self):
        if self._file_lock is not None:
            self._file_lock.acquire(timeout=0.2)

    def _release_file_lock(self):
        if self._file_lock is not None:
            self._file_lock.release()

    def _retire_remote_resolved_blocked(self):
        """Single-primary Offline cleanup for conflicts resolved by another engine.

        When a blocked op's sync_id no longer has an open blocking conflict on
        the server AND the server row advanced beyond the local row, the parked
        op is superseded and the local row will adopt the resolved server state
        on the following pull. Documented multi-client limitation: offline-only
        edits that were never conflict fields are not re-applied in this remote
        path (resolutions are expected through the primary engine).
        """
        conn, is_pg = self._conn, self._pg
        changed = False
        for op in S.outbox_rows(conn, is_pg, statuses=(P.OUTBOX_BLOCKED,)):
            sid = op["sync_id"]
            local = S.read_row_full(conn, is_pg, sid)
            if local is None:
                continue
            try:
                srv = self._server_row(sid)
            except Exception:
                continue
            if srv is None:
                continue
            if self._server_open_blocking(sid):
                continue
            if int(srv.get("server_rev") or 0) <= int(local.get("server_rev") or 0):
                continue
            _set_outbox_status(conn, is_pg, op["op_id"], P.OUTBOX_SUPERSEDED)
            # Force-adopt the resolved server state so the loser converges even
            # though this engine did not run the resolution (documented multi-
            # client path; offline-only non-conflict edits are not re-applied).
            srv_biz = {f: srv.get(f) for f in P.BUSINESS_FIELDS}
            S.update_local_row(conn, is_pg, sid, srv_biz, srv.get("deleted_at"),
                               _make_base_json(srv_biz), int(srv.get("server_rev") or 0), 0)
            changed = True
        if changed:
            S.commit(conn, is_pg)

    def run_once(self):
        """Full sync session: PULL -> MERGE -> PUSH -> FINALIZE (single-flight)."""
        if not self._flight.acquire(blocking=False):
            return SyncResult(status=P.SESSION_BUSY,
                              message="another sync session is running")
        if self._file_lock is not None:
            try:
                self._file_lock.acquire(timeout=0.5)
            except LockBusy:
                self._flight.release()
                return SyncResult(status=P.SESSION_BUSY,
                                  message="another process is syncing this database")
        conn, is_pg = self._conn, self._pg
        try:
            self.status = P.SESSION_CONNECTING
            S.mark_in_flight_back_to_pending(conn, is_pg)
            S.commit(conn, is_pg)
            self._retire_remote_resolved_blocked()
            try:
                self.status = P.SESSION_PULL
                pull_res = self.pull()
            except Exception as exc:
                S.rollback(conn, is_pg)
                raise
            if pull_res.get("status") != P.SESSION_SUCCESS:
                self.status = pull_res.get("status", P.SESSION_ERROR)
                _write_state(conn, is_pg, last_error="needs attention on %s"
                            % pull_res.get("needs_attention"))
                S.commit(conn, is_pg)
                return SyncResult(status=self.status, pulled=pull_res["pulled"],
                                  merged=pull_res["merged"], message="merge needs attention")
            self.status = P.SESSION_MERGE
            self.status = P.SESSION_PUSH
            push_res = self.push()
            if push_res.get("status") == P.SESSION_OFFLINE:
                self.status = P.SESSION_OFFLINE
                return SyncResult(status=P.SESSION_OFFLINE,
                                  pushed=push_res["pushed"],
                                  conflicts=push_res["conflicts"],
                                  failed=push_res["failed"],
                                  message=push_res.get("message", ""))
            if push_res.get("status") == P.SESSION_NEEDS_ATTENTION:
                self.status = P.SESSION_NEEDS_ATTENTION
                return SyncResult(status=P.SESSION_NEEDS_ATTENTION,
                                  pushed=push_res["pushed"],
                                  conflicts=push_res["conflicts"],
                                  failed=push_res["failed"],
                                  message=push_res.get("message", ""))
            if push_res.get("conflicts", 0) > 0:
                self.status = P.SESSION_CONFLICT
                return SyncResult(status=P.SESSION_CONFLICT,
                                  pushed=push_res["pushed"],
                                  conflicts=push_res["conflicts"],
                                  failed=push_res["failed"],
                                  message="open conflicts require resolution")
            self.status = P.SESSION_FINALIZE
            final = self.pull()  # converge local rows to the resolved server state
            _write_state(conn, is_pg, last_success_at=_now_iso(), last_error=None)
            S.commit(conn, is_pg)
            self.status = P.SESSION_IDLE
            changed = (pull_res.get("pulled", 0) > 0 or push_res.get("pushed", 0) > 0)
            return SyncResult(status=P.SESSION_SUCCESS, pulled=pull_res["pulled"],
                              merged=pull_res["merged"], pushed=push_res["pushed"],
                              conflicts=push_res.get("conflicts", 0),
                              failed=push_res.get("failed", 0),
                              changed=changed, revision=final.get("maxrev"))
        except Exception as exc:  # noqa: BLE001 - network/session failures are results
            temp = R.classify_error(exc) == R.ERR_TEMPORARY
            if temp:
                try:
                    S.mark_in_flight_back_to_pending(conn, is_pg)
                except Exception:
                    pass
            self.status = P.SESSION_OFFLINE if temp else P.SESSION_ERROR
            try:
                _write_state(conn, is_pg, last_error=str(exc))
                S.commit(conn, is_pg)
            except Exception:
                pass
            return SyncResult(status=self.status, message="%s: %s"
                              % (type(exc).__name__, exc))
        finally:
            self._release_file_lock()
            self._flight.release()


    # ---------------------------------------------------------------- status/resolution
    def get_status(self):
        conn, is_pg = self._conn, self._pg
        statuses = {}
        for r in S.fetch_all(conn, is_pg,
                             "SELECT status, COUNT(*) FROM outbox GROUP BY status"):
            statuses[r[0]] = r[1]
        local_conf = S.fetch_all(conn, is_pg,
                                 "SELECT COUNT(*) FROM conflicts WHERE status='open'")[0][0]
        srv_conf = getattr(self._server, "open_conflict_count", lambda: 0)()
        return {"session": self.status, "outbox": statuses,
                "local_open_conflicts": local_conf,
                "server_open_conflicts": srv_conf,
                "sync_state": _read_state(conn, is_pg)}

    def get_open_conflicts(self):
        """Read-only list of server conflicts for presentation (never syncs)."""
        fn = getattr(self._server, "list_conflicts", None)
        if fn is None:
            return []
        return fn()

    # ---------------------------------------------------- resolution convergence
    def _server_row(self, sync_id):
        row = getattr(self._server, "row", None)
        if row is None:
            raise RuntimeError("server adapter must expose row(sync_id)")
        return row(sync_id)

    def _server_open_blocking(self, sync_id):
        fn = getattr(self._server, "open_blocking_conflict", None)
        if fn is None:
            return False
        return bool(fn(sync_id))

    def _local_outbox_ops(self, sync_id):
        return [op for op in S.outbox_rows(
            self._conn, self._pg,
            statuses=(P.OUTBOX_BLOCKED, P.OUTBOX_PENDING, P.OUTBOX_IN_FLIGHT))
            if op["sync_id"] == sync_id]

    def _supersede_local_ops(self, sync_id):
        for op in self._local_outbox_ops(sync_id):
            _set_outbox_status(self._conn, self._pg, op["op_id"],
                               P.OUTBOX_SUPERSEDED)

    def _adopt_local_row(self, sync_id, business, deleted_at, server_rev):
        n = S.update_local_row(self._conn, self._pg, sync_id, business, deleted_at,
                               _make_base_json(business), int(server_rev or 0), 0)
        return n == 1

    def _converge_after_resolution(self, sync_id):
        """Converge the local (Offline) replica to a resolved server state.

        Uses the existing push/apply path: offline-only fields from the parked
        op are carried forward; resolved conflict fields keep the server value.
        Idempotent; returns a summary dict.
        """
        conn, is_pg = self._conn, self._pg
        srv = self._server_row(sync_id)
        if srv is None:
            self._supersede_local_ops(sync_id)
            return {"sync_id": sync_id, "status": "no_server_row"}
        server_biz = {f: srv.get(f) for f in P.BUSINESS_FIELDS}
        server_del = srv.get("deleted_at")
        server_rev = int(srv.get("server_rev") or 0)
        ops = self._local_outbox_ops(sync_id)
        if not ops:
            self._adopt_local_row(sync_id, server_biz, server_del, server_rev)
            S.commit(conn, is_pg)
            return {"sync_id": sync_id, "status": "adopted"}
        if server_del:
            # Resolution kept the tombstone: converge the local replica to the
            # deleted server row (no business overlay, no resurrection).
            self._supersede_local_ops(sync_id)
            self._adopt_local_row(sync_id, server_biz, server_del, server_rev)
            S.commit(conn, is_pg)
            return {"sync_id": sync_id, "status": "adopted_deleted"}

        latest = ops[-1]
        payload = latest["payload"]
        op_payload = payload.get("payload") or {}
        op_base = payload.get("base") or {f: None for f in P.BUSINESS_FIELDS}
        final = dict(server_biz)
        final_deleted = None
        if latest["op_type"] != P.OP_DELETE:
            for f in P.BUSINESS_FIELDS:
                offline_changed = not M.values_equal(f, op_base.get(f), op_payload.get(f))
                server_still_at_base = M.values_equal(f, op_base.get(f), server_biz.get(f))
                if offline_changed and server_still_at_base:
                    final[f] = op_payload.get(f)  # offline-only change survives
        # Push the convergence state through the normal op path (base = current
        # server snapshot) so the server also receives the offline-only fields.
        conv = {"op_id": str(uuid.uuid4()), "sync_id": sync_id,
                "op_type": P.OP_UPSERT, "payload": final,
                "base": S.decode_base(srv.get("base_json")),
                "base_rev": server_rev}
        resp = self._server.apply_ops([conv])
        result = resp["results"][0] if resp.get("results") else {"result": "error"}
        if result.get("result") != "applied":
            return {"sync_id": sync_id, "status": "needs_attention",
                    "error": result.get("error") or result.get("result")}
        new_rev = int(result.get("server_rev_after") or server_rev)
        self._supersede_local_ops(sync_id)
        self._adopt_local_row(sync_id, final, final_deleted, new_rev)
        S.commit(conn, is_pg)
        return {"sync_id": sync_id, "status": "converged", "server_rev": new_rev}

    def resolve_conflict(self, conflict_id, choice, resolution_payload=None):
        """Backend resolution + Offline convergence (KEEP_OFFLINE/ONLINE/MERGE).

        After the server resolves a conflict the Offline replica is converged to
        the resolved state (business, base_json, row_rev, tombstone). If other
        open conflicts remain for the same record the local convergence waits
        until the final conflict is resolved. Idempotent.
        """
        res = self._server.resolve_conflict(conflict_id, choice, resolution_payload)
        if res.get("reopened"):
            return res
        if not (res.get("applied_revision") or res.get("replayed")
                or res.get("sr_resolution")):
            return res
        affected = list(res.get("affected_sync_ids") or [])
        if res.get("sync_id") and res["sync_id"] not in affected:
            affected.append(res["sync_id"])
        pending = [sid for sid in affected if self._server_open_blocking(sid)]
        if pending:
            return {k: v for k, v in res.items()} | {"converged": False,
                                                     "pending_syncs": pending}
        syncs = [self._converge_after_resolution(sid)
                 for sid in dict.fromkeys(affected)]
        return {k: v for k, v in res.items()} | {"converged": True, "syncs": syncs}

def _changed_any(base, cur):
    return any(not M.values_equal(f, base.get(f), cur.get(f)) for f in P.BUSINESS_FIELDS)


def _op_payload_bytes(op):
    return op["payload"]  # already parsed dict: {payload, base, base_rev, ...}


def _build_send_op(op):
    p = op["payload"]
    return {"op_id": op["op_id"], "sync_id": op["sync_id"],
            "op_type": op["op_type"], "payload": p.get("payload"),
            "base": p.get("base"), "base_rev": p.get("base_rev", 0)}


def _set_outbox_status(conn, is_pg, op_id, status, last_error=None):
    S.set_outbox_status(conn, is_pg, op_id, status, last_error=last_error)
    S.commit(conn, is_pg)


def _classify_outcome(result):
    """Return (kind) for an op result: applied/noop/conflict/error."""
    return result.get("result")


def _pull_success(pull_result):
    return pull_result.get("status") == P.SESSION_SUCCESS


def _text_is_permanent(error_text):
    t = (error_text or "").lower()
    temp_hints = ("timeout", "connect", "connection", "dns", "network",
                  "unavailable", "refused", "reset", "ssl", "broken pipe")
    if any(h in t for h in temp_hints):
        return False
    return True


def _retry_op(conn, is_pg, op, error_text):
    """Re-queue an op as pending with exponential backoff + attempts counter."""
    from datetime import timedelta
    attempt = int(op.get("attempts") or 0) + 1
    delay = R.next_retry_delay(attempt - 1)
    nxt = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
    S.fail_outbox_retry(conn, is_pg, op["op_id"], attempt, error_text, nxt)
    S.commit(conn, is_pg)






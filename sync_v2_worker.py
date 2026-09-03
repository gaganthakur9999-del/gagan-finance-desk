"""sync_v2_worker.py - background Sync V2 worker for the Offline app.

Design rules honoured:
  * Pure Python - NEVER imports or calls Streamlit st.* APIs.
  * Local-first: CRUD commits first; this worker only runs AFTER commit.
  * Single-flight: only one sync session runs at a time per process.
  * Non-blocking wake: notify() only sets an Event (never does network).
  * Durable errors: failures are written into the local sync_state so the UI can
    show OFFLINE/ERROR while every outbox op stays pending for later retry.
  * The local database always remains usable; run_sync_once never lets an
    exception escape to callers (CRUD path) and never runs network in CRUD.

Runs the existing SyncEngine unchanged (no redesign): pull -> merge -> push ->
finalize. Startup does an immediate background sync; afterwards it waits for
notifications after local writes plus a periodic retry/wake.
"""
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone

import sync_v2_client as client
from syncv2.engine import SyncEngine

_PERIODIC_SECONDS = 60.0

_start_lock = threading.Lock()
_busy_lock = threading.Lock()
_event = threading.Event()
_stop = threading.Event()
_thread = None
_db_path = None


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _has_sync_tables(db_path):
    """True when the local DB has the outbox + sync_state tables the engine needs."""
    try:
        conn = sqlite3.connect(db_path)
        try:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            return {"outbox", "sync_state", "sync_sequence"} <= tables
        finally:
            conn.close()
    except sqlite3.Error:
        return False


def _record_error(db_path, message):
    """Persist a worker error into sync_state (never raises)."""
    try:
        conn = sqlite3.connect(db_path)
        try:
            with conn:
                conn.execute(
                    "UPDATE sync_state SET last_error=?, last_attempt_at=? "
                    "WHERE id=1", (message, _now_iso()))
        finally:
            conn.close()
    except sqlite3.Error:
        pass


def run_sync_once(db_path, neon_url=None):
    """Run ONE SyncEngine session in the current thread.

    Returns a summary dict. Never raises. When no Neon URL is configured (or the
    connection fails) the error is recorded locally and pending outbox ops are
    left untouched for retry. `neon_url=None` uses the existing config lookup;
    pass neon_url='' to force offline in tests without touching .env.
    """
    if not _busy_lock.acquire(blocking=False):
        return {"status": "BUSY", "message": "another sync session is running"}
    try:
        if not db_path or not os.path.exists(db_path):
            return {"status": "READY", "message": "local database unavailable"}
        if not _has_sync_tables(db_path):
            return {"status": "READY", "message": "sync schema not present"}
        url = neon_url if neon_url is not None else client.get_neon_url()
        if not url:
            _record_error(db_path, "NEON_URL not configured; sync skipped")
            return {"status": "OFFLINE", "message": "NEON_URL not configured"}
        local = sqlite3.connect(db_path)
        pg = None
        try:
            pg = client.connect(url)
            eng = SyncEngine(local, False, client.NeonServerAdapter(pg))
            result = eng.run_once()
            return {
                "status": result.status,
                "pushed": result.pushed,
                "pulled": result.pulled,
                "conflicts": result.conflicts,
                "message": result.message,
            }
        finally:
            if pg is not None:
                try:
                    pg.close()
                except Exception:
                    pass
            local.close()
    except Exception as exc:  # noqa: BLE001 - worker failures are data, not crashes
        try:
            _record_error(db_path, "%s: %s" % (type(exc).__name__, exc))
        except Exception:
            pass
        return {"status": "ERROR", "message": "%s: %s" % (type(exc).__name__, exc)}
    finally:
        _busy_lock.release()


def _worker_loop(db_path):
    """Background thread: immediate first sync, then wait/notify/periodic."""
    run_sync_once(db_path)
    while not _stop.is_set():
        _event.wait(_PERIODIC_SECONDS)  # notification OR periodic retry wake
        _event.clear()
        if _stop.is_set():
            break
        run_sync_once(db_path)
    _event.clear()


def start_worker(db_path):
    """Start the single background worker for the Offline app. Idempotent:
    repeated calls (Streamlit reruns) return the existing thread."""
    global _thread, _db_path
    if not db_path:
        return False
    with _start_lock:
        if _thread is not None and _thread.is_alive():
            return True
        _db_path = db_path
        _stop.clear()
        _thread = threading.Thread(target=_worker_loop, args=(db_path,),
                                   name="sync_v2_worker", daemon=True)
        _thread.start()
        return True


def stop_worker(timeout=5.0):
    """Stop the background worker (used by tests/cleanup)."""
    global _thread
    with _start_lock:
        if _thread is None:
            return
        _stop.set()
        _event.set()
        _thread.join(timeout=timeout)
        _thread = None


def notify():
    """Non-blocking wake after a committed local write. Returns True when a
    worker exists and was notified; never performs network work."""
    if _thread is not None and _thread.is_alive():
        _event.set()
        return True
    return False


def is_started():
    return _thread is not None and _thread.is_alive()


def is_syncing():
    return _busy_lock.locked()

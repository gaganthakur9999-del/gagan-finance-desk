"""sync_v2_client.py - Streamlit-free Sync V2 server client for the Offline app.

Offline-first master application talks to the production Neon/PostgreSQL replica
by running the SAME Sync V2 coordinator functions (syncv2.server) that the
Online seam and the E2E environment use. This module only provides:

  - the Neon connection configuration (reusing the existing NEON_URL/.env setup
    used by the old sync scripts - never the local SQLite file), and
  - a small NeonServerAdapter that mirrors the adapter surface SyncEngine expects
    (pull/apply_ops/resolve_conflict/row/...).

Pure Python. NO streamlit import and NO sync protocol logic - everything is
delegated to the existing syncv2 package.
"""
import os

from syncv2 import server as SVC


def get_neon_url():
    """Neon connection string from env, falling back to .env (same rule as the
    existing old-sync scripts and read-only verification scripts)."""
    url = os.environ.get("NEON_URL", "").strip()
    if url:
        return url
    env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_file):
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("NEON_URL="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def connect(url=None):
    """Open one psycopg2 connection to Neon. Import is lazy so desktop machines
    without psycopg2 still import this module safely."""
    import psycopg2
    return psycopg2.connect(url or get_neon_url(), connect_timeout=20)


class NeonServerAdapter:
    """Adapter exposing SyncEngine's expected server surface over a real
    PostgreSQL (Neon) connection. Mirrors the adapter used by the Sync V2 tests
    and E2E harness; all calls delegate to syncv2.server (is_pg=True)."""

    def __init__(self, conn):
        self.conn = conn
        self.is_pg = True

    def pull(self, since_rev):
        return SVC.pull_changes(self.conn, self.is_pg, since_rev)

    def apply_ops(self, ops):
        return SVC.apply_ops(self.conn, self.is_pg, ops)

    def resolve_conflict(self, conflict_id, choice, resolution_payload=None):
        return SVC.resolve_conflict(self.conn, self.is_pg, conflict_id, choice,
                                    resolution_payload)

    def open_conflict_count(self):
        return SVC.list_open_conflicts(self.conn, self.is_pg).__len__()

    def row(self, sync_id):
        return SVC.read_row_full(self.conn, self.is_pg, sync_id)

    def open_blocking_conflict(self, sync_id):
        return SVC.has_open_conflict(self.conn, self.is_pg, sync_id)

    def list_conflicts(self):
        return SVC.list_open_conflicts(self.conn, self.is_pg)

"""
sync_schema.py — Phase-1 additive synchronization schema (offline-first redesign).

DUAL-BACKEND, IDEMPOTENT, ADDITIVE ONLY.
Supported backends: SQLite (desktop) and PostgreSQL/Neon (Render).

What this does (and only this):
  - Adds 5 columns to `records` when missing:
        sync_id TEXT            (permanent cross-DB identity; values assigned later)
        server_rev INTEGER 0    (server optimistic-concurrency version the client last saw)
        row_rev INTEGER 0       (local per-replica change counter)
        base_json TEXT          (ancestor snapshot at last completed two-way sync)
        deleted_at TEXT         (tombstone timestamp; NULL = live)
  - Creates the sync infrastructure tables when missing:
        outbox        (laptop durable change queue)
        applied_ops   (server idempotency / conflict-recovery ledger)
        conflicts     (durable, never-lost conflict records)
        sync_state    (single-row checkpoint/status)
        sync_sequence (server global monotonic revision counter)
  - Seeds sync_state(id=1) and sync_sequence(id=1, value=0) when absent.
  - Creates supporting indexes when missing.

What this does NOT do (Phase-1 boundary):
  - does NOT assign/unify production sync_id values (no bootstrap)
  - does NOT modify existing production rows' business values
  - does NOT copy records between databases
  - does NOT enable synchronization, tombstones, invoice changes, or SR changes

Safety: running the migration twice (or on an already-migrated DB) is a no-op.
Existing columns/tables/indexes are never altered or dropped.
"""
import logging
from typing import Dict, List

_LOG = logging.getLogger("sync_schema")

# Existing-column types added to `records` (name -> SQL type). Additive only.
RECORDS_ADD_COLUMNS: Dict[str, str] = {
    "sync_id": "TEXT",
    "server_rev": "INTEGER NOT NULL DEFAULT 0",
    "row_rev": "INTEGER NOT NULL DEFAULT 0",
    "base_json": "TEXT",
    "deleted_at": "TEXT",
}

# New tables (DDL written in SQLite-friendly syntax; PostgreSQL translation in
# `translate_ddl`). `id` becomes SERIAL on PostgreSQL via translation.
TABLE_DDL: Dict[str, str] = {
    "outbox": """
        CREATE TABLE IF NOT EXISTS outbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            op_id TEXT NOT NULL,
            sync_id TEXT NOT NULL,
            op_type TEXT NOT NULL,
            payload_json TEXT,
            base_rev INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            next_retry_at TEXT,
            last_error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CONSTRAINT uq_outbox_op_id UNIQUE (op_id)
        )
    """,
    "applied_ops": """
        CREATE TABLE IF NOT EXISTS applied_ops (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            op_id TEXT NOT NULL,
            sync_id TEXT,
            op_type TEXT,
            result TEXT,
            server_rev_after INTEGER,
            conflict_json TEXT,
            applied_at TEXT NOT NULL,
            CONSTRAINT uq_applied_ops_op_id UNIQUE (op_id)
        )
    """,
    "conflicts": """
        CREATE TABLE IF NOT EXISTS conflicts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sync_id TEXT,
            kind TEXT,
            field_name TEXT,
            offline_value TEXT,
            online_value TEXT,
            base_value TEXT,
            month TEXT,
            status TEXT NOT NULL DEFAULT 'open',
            resolution TEXT,
            resolution_op_id TEXT,
            created_at TEXT NOT NULL,
            resolved_at TEXT
        )
    """,
    "sync_state": """
        CREATE TABLE IF NOT EXISTS sync_state (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            last_success_at TEXT,
            last_attempt_at TEXT,
            last_error TEXT,
            last_pulled_sync_rev INTEGER NOT NULL DEFAULT 0,
            last_push_op_id TEXT,
            conflict_count INTEGER NOT NULL DEFAULT 0
        )
    """,
    "sync_sequence": """
        CREATE TABLE IF NOT EXISTS sync_sequence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            value INTEGER NOT NULL DEFAULT 0
        )
    """,
}

# New indexes (name -> SQL). Unique on sync_id (multiple NULLs allowed pre-baseline).
INDEX_DDL: Dict[str, str] = {
    "idx_records_sync_id": "CREATE UNIQUE INDEX IF NOT EXISTS idx_records_sync_id ON records(sync_id)",
    "idx_records_deleted_at": "CREATE INDEX IF NOT EXISTS idx_records_deleted_at ON records(deleted_at)",
    "idx_outbox_status": "CREATE INDEX IF NOT EXISTS idx_outbox_status ON outbox(status, next_retry_at)",
    "idx_conflicts_status": "CREATE INDEX IF NOT EXISTS idx_conflicts_status ON conflicts(status)",
    "idx_applied_ops_result": "CREATE INDEX IF NOT EXISTS idx_applied_ops_result ON applied_ops(result)",
}


def translate_ddl(sql: str, is_postgres: bool) -> str:
    """Translate SQLite-flavoured DDL to PostgreSQL where needed."""
    if is_postgres:
        sql = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
        sql = sql.replace("AUTOINCREMENT", "")
    return sql


def _existing_records_columns(conn, is_postgres: bool) -> set:
    if is_postgres:
        cur = conn.cursor()
        cur.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name='records'"
        )
        cols = {r[0] for r in cur.fetchall()}
        cur.close()
        return cols
    rows = conn.execute("PRAGMA table_info(records)").fetchall()
    return {r[1] for r in rows}


def _existing_tables(conn, is_postgres: bool) -> set:
    if is_postgres:
        cur = conn.cursor()
        cur.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")
        tabs = {r[0] for r in cur.fetchall()}
        cur.close()
        return tabs
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {r[0] for r in rows}


def _exec_ddl(conn, sql: str, is_postgres: bool):
    """Execute one (possibly multi-statement) DDL chunk on either backend."""
    sql = translate_ddl(sql, is_postgres)
    if is_postgres:
        cur = conn.cursor()
        for stmt in sql.split(";"):
            stmt = stmt.strip()
            if stmt:
                cur.execute(stmt)
        cur.close()
    else:
        conn.executescript(sql)


def migrate_sync_schema(conn, is_postgres: bool) -> Dict[str, list]:
    """Apply the additive sync schema. Idempotent. Returns what was added.

    conn: an open sqlite3 or psycopg2 connection (transaction managed by caller).
    """
    added_columns: List[str] = []
    existing_cols = _existing_records_columns(conn, is_postgres)
    for col, coltype in RECORDS_ADD_COLUMNS.items():
        if col in existing_cols:
            continue
        _exec_ddl(conn, "ALTER TABLE records ADD COLUMN %s %s" % (col, coltype), is_postgres)
        added_columns.append(col)

    existing_tables = _existing_tables(conn, is_postgres)
    created_tables: List[str] = []
    for table, ddl in TABLE_DDL.items():
        if table in existing_tables:
            continue
        _exec_ddl(conn, ddl, is_postgres)
        created_tables.append(table)

    added_indexes: List[str] = []
    for name, ddl in INDEX_DDL.items():
        _exec_ddl(conn, ddl, is_postgres)
        added_indexes.append(name)  # IF NOT EXISTS makes re-runs safe no-ops

    # Seed single rows (sync_state + sync_sequence) if the tables are empty.
    _seed_row(conn, is_postgres, "sync_state")
    _seed_row(conn, is_postgres, "sync_sequence")

    result = {
        "columns_added": added_columns,
        "tables_created": created_tables,
        "indexes_ensured": added_indexes,
    }
    _LOG.info("sync schema migrate: %s", result)
    return result


def _seed_row(conn, is_postgres: bool, table: str):
    if is_postgres:
        cur = conn.cursor()
        cur.execute("INSERT INTO %s (id) VALUES (1) ON CONFLICT DO NOTHING" % table)
        cur.close()
    else:
        conn.execute("INSERT OR IGNORE INTO %s (id) VALUES (1)" % table)


def describe_sync_schema(conn, is_postgres: bool) -> Dict[str, list]:
    """Return the resulting schema for verification/reporting (read-only)."""
    records_cols = sorted(_existing_records_columns(conn, is_postgres))
    tables = sorted(_existing_tables(conn, is_postgres))
    indexes = []
    if is_postgres:
        cur = conn.cursor()
        cur.execute("SELECT indexname FROM pg_indexes WHERE tablename='records' ORDER BY indexname")
        indexes = [r[0] for r in cur.fetchall()]
        for t in ("outbox", "applied_ops", "conflicts", "sync_state", "sync_sequence"):
            cur.execute(
                "SELECT indexname FROM pg_indexes WHERE tablename=%s ORDER BY indexname", (t,)
            )
            indexes += [r[0] for r in cur.fetchall()]
        cur.close()
    else:
        for t in ("records", "outbox", "applied_ops", "conflicts", "sync_state", "sync_sequence"):
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=?", (t,)
            ).fetchall()
            indexes += [r[0] for r in rows]
    return {"records_columns": records_cols, "tables": tables, "indexes": sorted(set(indexes))}


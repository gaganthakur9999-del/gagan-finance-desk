# Sync V2 Phase 7C-E2E — Isolated PostgreSQL Environment (Infrastructure Only)

_Objective: obtain a genuinely isolated PostgreSQL environment for Sync V2 E2E.
No application-logic changes were made in this phase._

## 1. Environment discovered

Pre-existing options were re-checked first and none existed:

- Docker / Podman: not installed
- PostgreSQL binaries (`psql`, `initdb`, `pg_ctl`, `pg_isready`, `postgres`): not on PATH
- PostgreSQL Windows service / install dirs (`C:\Program Files\PostgreSQL`, etc.): none
- Local listener on 5432/5433: none
- Test `DATABASE_URL` / CI PostgreSQL config / docker-compose fixtures: none in repo
- Neon branch/API configuration: none (only the read-only production `NEON_URL`)
- Chocolatey / Scoop: not installed
- `winget`: present, but a machine-scope PostgreSQL MSI install is intrusive and
  not disposable; not used

Network (PyPI, Maven, GitHub, EDB) and `pip` were available; Python 3.14 was the
only system interpreter.

## 2. Environment selected

**Disposable, user-scope real PostgreSQL 16.4** assembled from the official EDB
Windows x64 binaries zip (no installer, no service, no admin), staged entirely
under the repository's gitignored `temp/` directory:

| Item | Value |
|---|---|
| PostgreSQL version | **16.4** (x86_64, Visual C++ build 1940) |
| Binaries | `temp/p7ce2e_pg/pgsql/bin` |
| Data dir | `temp/p7ce2e_pg/data` |
| Host / port | `127.0.0.1` / `55432` (loopback only) |
| Auth | `trust` on loopback (ephemeral, disposable) |
| Database name | `finance_syncv2_test` (fresh each smoke run) |
| Server log | `temp/p7ce2e_pg/server2.log` |

Tooling kept for reproducibility: `uv` (user-scope Python installer) and a Python
3.12 venv under `temp/` were the validated bootstrap path for the pip-based PG
package; the smoke test itself uses system Python 3.14 + psycopg2.

## 3. Isolation guarantees

- The instance runs as the current user from disposable temp directories; it is
  NOT a Windows service and does not touch any system PostgreSQL install.
- It listens ONLY on `127.0.0.1:55432` (never exposed).
- It uses a distinctly named, freshly-created database (`finance_syncv2_test`)
  that contains only synthetic data.
- It shares no credentials, connection strings, or files with production Neon.
- Production Neon was not connected to and no production configuration was
  modified.

## 4. Setup (how this was built)

1. Verified no existing isolated PG; confirmed network reachability.
2. Downloaded the official EDB binaries zip (338,727,828 bytes,
   `postgresql-16.4-1-windows-x64-binaries.zip`) into `temp/p7ce2e_pg/` and
   extracted it.
3. `initdb -D temp/p7ce2e_pg/data -U postgres -A trust -E UTF8 --no-locale`
4. **Required fix:** the first start crashed with `0xC0000142` (DLL init
   failure); staging the MSVC runtime DLLs (`vcruntime140.dll`,
   `vcruntime140_1.dll`, `msvcp140.dll`, `concrt140.dll`) from
   `C:\Windows\System32` into `pgsql/bin` resolved it.
5. Started with
   `pg_ctl -D temp/p7ce2e_pg/data -o "-p 55432 -h 127.0.0.1" -l temp/p7ce2e_pg/server2.log start`
   -> "database system is ready to accept connections".

## 5. Cleanup

- Stop: `temp/p7ce2e_pg/pgsql/bin/pg_ctl -D temp/p7ce2e_pg/data stop`
- Remove: `temp/p7ce2e_pg` (binaries + data), `temp/p7ce2e_python`,
  `temp/p7ce2e_venv312`, `temp/p7ce2e_tools` (all disposable).
- The isolated database is dropped/recreated automatically at the start of the
  smoke run (`DROP DATABASE IF EXISTS finance_syncv2_test WITH (FORCE)`).

## 6. Smoke-test results (real PostgreSQL, is_pg=True)

Executed against `127.0.0.1:55432/finance_syncv2_test` with system psycopg2, the
real Sync V2 modules, and the Online seam (`online_write.py`):

- `SCHEMA columns_added 5, tables 5` - full Phase-1 schema migration applied on a
  fresh PG database (base `records` DDL created first with translated SERIAL DDL).
- PASS create: uuid4 sync_id assigned once, `server_rev=1`, `row_rev=0`.
- PASS update: sync_id stable, `server_rev` advanced, `base_json` refreshed,
  business name updated.
- PASS revision allocation: `sync_sequence` monotonic (2 -> 3) via the PG
  row-locked `next_revision`.
- PASS pull: `pull_changes(since=0)` returned the row with `maxrev >= 3`.
- PASS rollback: an aborted create reverted both the row and the revision.
- PASS conflict insert: DB-generated integer conflict id via `RETURNING`.
- PASS conflict resolution: KEEP_ONLINE applied (new revision allocated), conflict
  closed.
- PASS tombstone: `deleted_at` set, sync_id preserved, physical row retained.

PostgreSQL-specific findings during the smoke:
1. `0xC0000142` startup crash fixed by staging the VC++ runtime DLLs (documented
   above) - an environment issue, not a Sync V2 code issue.
2. On a brand-new database the legacy `records` table must exist before
   `sync_schema.migrate_sync_schema` (which only adds columns/tables). Expected:
   Phase-1 migrated an existing schema; the E2E harness must create the base
   `records` DDL first (the smoke does this via `translate_ddl`).
3. A manually-opened field conflict re-opened when its recorded online value did
   not match the row's current value (test-ordering mistake in an early smoke
   draft, corrected; the reopen guard behaved correctly).

No Sync V2 application-code change was required for the smoke test.

## 7. READY for the full Phase-7C E2E matrix?

**YES - environment is READY.** A genuine, isolated, real PostgreSQL 16.4
instance is running on `127.0.0.1:55432` with the Sync V2 schema applied, and the
create/update/tombstone/revision/rollback/pull/conflict-insert/conflict-resolution
smoke path all PASS with `is_pg=True`. The full 43-item E2E matrix and the
Part-F concurrency tests can now be run against this environment (or a recreated
equivalent using the documented setup) in the next step of Phase 7C-E2E.

---

_Date: 2026-09-02. Infrastructure-only phase. No application-logic changes, no
production writes, no commits/pushes._

- No production resource is affected by any of the above.


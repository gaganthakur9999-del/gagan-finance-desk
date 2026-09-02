# Sync V2 Phase 7B — Isolated Online Write-Capture Design + Implementation

_Status: COMPLETE (isolated). No production writes, no production sync, no
automatic sync. Phase 7 (overall production integration) is NOT complete.
Next authorized phase: **Phase 7C**._

Evidence tags:
- **PASS-EMULATED** — executed on isolated SQLite twin databases through the
  backend-agnostic (is_pg-parameterized) seam/engine code paths (the project's
  established translation/compat method).
- **CODE-REVIEW ONLY** — verified by source inspection only.
- **NOT TESTED** — requires a real PostgreSQL environment that does not exist yet.

---

## 1. Isolated PostgreSQL environment

**None available, none creatable on this machine.** Checked: no Docker, no
PostgreSQL binaries (`psql`/`initdb`/`pg_ctl`/`pg_isready`), no PostgreSQL
Windows service, port 5432 not listening, no Neon branch/API credentials, no test
`DATABASE_URL`, no CI/docker-compose fixtures in the repository.

Per the phase rules, production Neon was NOT used as a test database. The smallest
safe approach was therefore to implement the seam **backend-agnostic** (identical
code for `is_pg=False` and `is_pg=True`, like the rest of syncv2) and validate it
with isolated SQLite twins. Connection/cleanup method for a future real-PG run:
dedicated Neon branch or local PG database `finance_syncv2_test`; drop schema after
the run; never production.

## 2. Architecture decision

Online (Render/Neon) normal-user record writes all flow through `database.py`
CRUD (`add_record` / `update_record` / `delete_record` / `swap_sr_no`) — verified
by repository search: `pages/generate_invoice.py`, `pages/records.py`,
`ui_components.py` contain no direct SQL. **`database.py` is the central write
seam** for the Online application, exactly as it is for the Offline application.

Chosen design: **application-level Online write seam** (`online_write.py`, called
from `database.py` only when running against PostgreSQL with the Phase-1 schema
present). Rejected alternatives:
- DB triggers — hidden magic, hard to version/test, fires on coordinator writes too.
- Database outbox — the Online replica is the *server*; it has no client outbox
  semantics; per-row server revisions are already the server's change record.
- Server coordinator API — a deployment seam is still needed later, but it is not
  required to make Online *application* writes capture correct metadata.

Rationale: smallest change consistent with the existing model. A server-originated
change is a `records` row whose `server_rev` advanced (plus `base_json` refreshed
to the server-current snapshot); `pull_changes()` already streams exactly these
rows, so Online writes become incrementally discoverable with no new event system.
Client outbox/`applied_ops` semantics are untouched (client ops remain the only
users).

## 3. Online write-capture design

| Operation | server_rev | row_rev | base_json | sync_id | outbox | physical delete |
|---|---|---|---|---|---|---|
| create | +1 (from sync_sequence) | 0 | server-current snapshot | fresh uuid4, once | none | n/a |
| edit | +1 | 0 | server-current snapshot | preserved | none | n/a |
| delete | +1 (+1 per renumbered row) | 0 | unchanged (business untouched) | preserved | none | **never** — tombstone |
| SR move | +1 per affected row | 0 | refreshed | preserved | none | n/a |

BASE semantics: a server-originated change advances server state/revision but does
NOT force the Offline replica's base. Offline's `base_json` advances only when it
pulls (merge accept-online) or resolves a conflict. The server's `base_json`
equals the server-current snapshot (identical to `write_server_row` semantics used
for every applied client op), which is what a fresh/`pull()` client adopts as its
initial base. Validation: same-field online-vs-offline edits still open a conflict;
different-field edits still union (tests pass). Legacy NULL/blank-sync rows are
**refused** by the seam (no silent split identity / non-syncing mutation); adoption
remains a deliberate controlled step.

## 4. Implementation

- **`online_write.py` (new)** — backend-agnostic seam: `schema_ready`, `row_by_id`,
  `create_row`, `edit_row`, `delete_row` (tombstone + same-transaction month
  renumbering, one revision per shifted row), `swap_sr`. Uses `syncv2.server`
  primitives (`next_revision`, `write_server_row`, `store.execute/fetch_all`) so
  revision allocation and row writes are identical to the coordinator. Never
  commits (caller owns the transaction).
- **`database.py`** — `_online_sync_ready(conn)` gate; Online branches in
  `add_record`/`update_record`/`delete_record`/`swap_sr_no` invoke the seam when
  `USE_POSTGRES` and the Phase-1 schema exist; legacy SQL remains the fallback for
  pre-schema PostgreSQL and un-migrated SQLite. `_live_where()` now also filters
  `deleted_at IS NULL` on PostgreSQL when the column exists, so Online business
  views hide Online tombstones.
- **`tests/test_syncv2_phase7b_online_write.py` (new)** — 15 tests.

## 5. PostgreSQL compatibility

Schema/unique-index objects were already verified on real Neon in Phase 7A.
Phase 7B adds no SQLite-only syntax to any shared path: every new statement is
dual-backend (`%s`/`?` via `_ph`), the PG gate uses `information_schema` +
`to_regclass`, revision allocation reuses the row-locked `next_revision`, and
`write_server_row` is the existing dual-backend writer. **CODE-REVIEW ONLY** for
real-PG execution; NOT TESTED here (no isolated PG).

## 6. E2E test matrix

All executed through the backend-agnostic code paths on isolated SQLite twins
(`PASS-EMULATED`), unless stated otherwise.

| # | Scenario | Result |
|---|---|---|
| 1 | Offline create -> push -> Online | PASS-EMULATED (engine suite + phase7b mixed workload) |
| 2 | Offline edit -> push -> Online | PASS-EMULATED (phase7b same/diff-field tests, engine suite) |
| 3 | Offline delete -> push -> Online | PASS-EMULATED (engine suite + phase7b offline-delete-vs-edit) |
| 4 | Online create -> pull -> Offline | PASS-EMULATED (phase7b) |
| 5 | Online edit -> pull -> Offline | PASS-EMULATED (phase7b) |
| 6 | Online delete -> pull -> Offline | PASS-EMULATED (phase7b, incl. renumber convergence) |
| 7 | Online SR reorder -> pull -> Offline | PASS-EMULATED (phase7b swap) |
| 8 | Offline SR reorder -> push -> Online | PASS-EMULATED (engine suite + phase7b grouped conflict) |
| 9 | Offline+Online edit same field | PASS-EMULATED (phase7b) |
| 10 | Offline+Online edit different fields | PASS-EMULATED (phase7b union) |
| 11 | Multiple conflicting fields | PASS-EMULATED (corrections/server suites) |
| 12 | Offline edit + Online delete | PASS-EMULATED (phase7b, no resurrection) |
| 13 | Offline delete + Online edit | PASS-EMULATED (phase7b, no resurrection) |
| 14 | Stale BASE + multiple Online changes | PASS-EMULATED (engine/corrections stale-base tests) |
| 15 | Resolve Keep Offline | PASS-EMULATED (phase7b sr group; corrections) |
| 16 | Resolve Keep Online | PASS-EMULATED (phase7b field/delete) |
| 17 | Resolve Merge | PASS-EMULATED (corrections suite) |
| 18 | sync_id stable | PASS-EMULATED (phase7b) |
| 19 | Online create gets valid sync_id | PASS-EMULATED (phase7b) |
| 20 | No duplicate sync_id | PASS-EMULATED (phase7b) |
| 21 | Same-invoice records independent | PASS-EMULATED (phase7b) |
| 22 | Invoice collision advisory | PASS-EMULATED (phase7b + server suite) |
| 23 | BID collision does not merge | PASS-EMULATED (identity is sync_id; bootstrap/server suites) |
| 24 | Serial collision follows rules | PASS-EMULATED (server/corrections suites) |
| 25 | Offline reorder | PASS-EMULATED |
| 26 | Online reorder | PASS-EMULATED (phase7b swap) |
| 27 | Both replicas reorder same month | PASS-EMULATED (phase7b grouped conflict) |
| 28 | Grouped SR conflict | PASS-EMULATED (phase7b) |
| 29 | Resolution converges both replicas | PASS-EMULATED (phase7b) |
| 30 | Duplicate op replay | PASS-EMULATED (engine suite) |
| 31 | Partial push | PASS-EMULATED (engine suite) |
| 32 | Retry after temporary failure | PASS-EMULATED (engine suite) |
| 33 | Rollback after failed transaction | PASS-EMULATED (phase7b rollback; server suite) |
| 34 | No-op sync | PASS-EMULATED (engine suite) |
| 35 | Incremental pull | PASS-EMULATED (engine + phase7b) |

## 8. Invariants

- No duplicate sync_ids and sync_id stability: asserted after every phase-7B test.
- No silent data loss: business/base equality asserted on both replicas after
  convergence in every propagation/conflict test.
- server_rev monotonic: dedicated test (5 sequential online edits -> 5 unique
  ascending revisions).
- row_rev: server rows stay 0 (server-side convention) while Offline rows keep
  Phase-6 semantics.
- Outbox/applied_ops untouched by the Online seam (asserted); client op
  idempotency is unchanged.
- Conflicts: field / delete-edit / grouped-SR / invoice-advisory states asserted.
- Tombstones: preserved, no physical purge, no resurrection on stale edits,
  repeated delete idempotent.
- Independent records remain independent; invoices are never silently renumbered;
  SR ordering converges deterministically after grouped resolution.

## 9. Remaining blockers

1. **No isolated real PostgreSQL environment** - items 36/37 and all real-PG
   mechanics (RETURNING via psycopg2, row-locked sync_sequence under concurrent
   sessions) remain NOT TESTED. Phase 7C must obtain one (Neon branch / local PG).
2. **NULL/blank sync_id identity (Offline 2534 / Online 2698) is NOT adopted in
   production** and the seam refuses it; production adoption is a controlled,
   still-prohibited step.
3. **Old Sync Now / old CLI scripts can still create NULL-sync rows on Neon** for
   newly created Offline records pushed during the transition (Phase 7A finding;
   old sync is intentionally unchanged). These rows must be covered by the
   Phase-7C adoption pass before any real push.
4. **No production coordinator deployment seam** yet (in-process adapter only).

## 10. Production safety verification

Read-only re-verification after implementation (both replicas):

- Neon: records 1,524 (live 1,524), NULL-sync 1 (id 2698), server_rev>0 0,
  row_rev>0 0, outbox 0, applied_ops 0, conflicts 0, sync_sequence 0.
- SQLite: identical (NULL-sync id 2534, all-zero revisions/metadata).

Production was not written to at any point in Phase 7B; all implementation and
tests used isolated temp SQLite databases. No production SyncEngine run, no
adoption, no sync activation, old Sync Now untouched.

## 11. GO/NO-GO for Phase 7C

**GO for Phase 7C (isolated real-PostgreSQL E2E + controlled-adoption design).**
Phase 7C may: stand up an isolated real PG/Neon environment and run the full
matrix (A-Y and items 36-37) against the seam with is_pg=True; exercise the seam's
deployment branches; and design (not execute) the production adoption procedure
for the NULL-sync identity and future old-sync-created rows.

**NOT GO for any production-connected activity**: no adoption of Tarun Kumar /
NULL rows, no production Neon writes, no production SyncEngine, no automatic sync,
no deployment of the new Online branches to Render, until 7C demonstrates the
seam on real PostgreSQL and the controlled-adoption runbook is approved.

---

_Audit date: 2026-09-02. Isolated only. No production mutation. No commits/pushes._

| 36 | Concurrent revision allocation | NOT TESTED (real PG required) |
| 37 | Concurrent writes to same record | NOT TESTED (real PG required; code-review caveat documented Phase 7A) |
| 38 | Resolution after stale server revision | PASS-EMULATED (engine/corrections) |
| 39 | Deleted row remains tombstoned | PASS-EMULATED (phase7b) |
| 40 | Tombstone discoverable by pull | PASS-EMULATED (phase7b) |
| 41 | Stale edit cannot resurrect tombstone | PASS-EMULATED (phase7b + corrections) |
| 42 | Repeated delete idempotent | PASS-EMULATED (phase7b noop) |
| 43 | Old append-only sync not involved | PASS (isolation guarantee; no old-sync calls in tests) |

## 7. Test results

`python tests/test_syncv2_phase7b_online_write.py` → 15/15 PASS
(`ALL SYNCV2 PHASE7B ONLINE WRITE TESTS PASSED`). Full existing battery green —
merge, server, engine, invariants, corrections, phase5_ui, phase6_write,
phase7b_online_write, schema migration, bootstrap analysis/apply, dashboard
chart ordering — all exit 0. `py_compile` clean over all modified/new modules.


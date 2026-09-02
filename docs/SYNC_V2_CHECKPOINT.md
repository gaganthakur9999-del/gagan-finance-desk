# Sync V2 Development Checkpoint

_Checkpoint created at the intentional pause at the end of Phase 4. Documentation-only file._

## Current State

- Phase 4 is **COMPLETE** (corrected + regression-tested).
- **Phase 5 (Sync V2 status + conflict UI) is COMPLETE.**
- **Phase 6 (Offline Finance Desk write-path / outbox / tombstone integration) is
  COMPLETE and its read-only integration audit returned PASS.**
- **Phase 7A (real PostgreSQL/Neon compatibility + production baseline read-only
  audit) is COMPLETE.**
- **Phase 7B (isolated Online write-capture design + implementation) is
  COMPLETE.** A backend-agnostic Online write seam (`online_write.py`) wired into
  `database.py` captures Online-originated create/edit/delete/SR writes on the
  server replica (sync_id assigned once, tombstone deletes, one server_rev per
  change, no outbox). Validated PASS-EMULATED on SQLite twins; NOT TESTED on real
  PostgreSQL (no isolated PG exists).
- **Phase 7C-E2E is COMPLETE-PASS (real PostgreSQL).** A real, isolated,
  disposable PostgreSQL 16.4 (`127.0.0.1:55432`, `finance_syncv2_test`) was used
  to run the full 43-item E2E matrix plus the Online write seam, BASE semantics,
  invariant sweep and the concurrency tests (concurrent revision allocation,
  concurrent same-record edits, stale-revision resolution) with `is_pg=True` -
  **all PASS** (see `docs/SYNC_V2_PHASE7C_AUDIT.md`). One genuine concurrency
  bug was found and fixed: same-record edits racing could silently
  last-writer-win on real PG; `syncv2/server.py` now uses an optimistic
  `server_rev` write guard + bounded retry so the race re-detects and opens a
  real conflict. No production adoption was executed and production was only
  re-verified read-only (unchanged baseline).
- Phase 7C production roll-out items (adoption execution, Online-seam
  deployment, old-sync freeze) are **still not done**.
- Phase 8 remains **NOT authorized**.

- Sync V2 is integrated into the **Offline** Finance Desk write paths (local
  SQLite creates/edits/deletes/reorders produce durable transactional outbox
  operations). The **Online (Render/Neon) application write capture is NOT yet
  Sync V2 aware** (Phase 7A finding - see `docs/SYNC_V2_PHASE7A_AUDIT.md`).
- One post-bootstrap legacy identity exists on both replicas **without sync_id**
  (Offline id 2534 / Online id 2698); it must be adopted before any real push.
- The existing application continues to use the **OLD sync system**.
- Existing Finance Desk behavior remains unchanged (old "Sync Now" untouched).
- No automatic startup sync is enabled.
- No background sync worker is enabled.
- No post-write sync is enabled.
- No Online Sync button was added.
- **No Sync V2 production synchronization was performed during any phase so far;
  Phase 7A was strictly read-only.**


## Completed Phases

### Phase 1 — Schema + Safety Backups
Completed.
The additive Sync V2 schema (`sync_id`, `server_rev`, `row_rev`, `base_json`, `deleted_at` on `records` plus `outbox`, `applied_ops`, `conflicts`, `sync_state`, `sync_sequence`) was applied to both the Offline SQLite database and the Online Neon database, and the migration was verified with safety backups. No business data was changed.

### Phase 2 — Read-Only Bootstrap Reconciliation
Completed.
Both databases were analyzed strictly read-only, all records were reconciled into matched identities (the review logic and reports were tested before any real run), and the discovered cases were documented (identical matches, the 16 SR-order-only differences, and the single conservative NEEL CHAND unmatched pair).

### Phase 3 — Production Bootstrap / Baseline
Completed.
The shared stable `sync_id` baseline was established on both databases: every matched identity received the same permanent UUID v4 on both replicas, `base_json` was written identically per pair, Offline was authoritative for the approved bootstrap differences (including the 16 SR corrections), the NEEL CHAND record was unified as one identity, and post-bootstrap verification passed (no invoice collisions, no conflicts, no fake revision history, backups verified).

### Phase 4 — Sync Engine
Completed.
`syncv2/` contains the standalone pure-Python sync engine and includes:
- protocol/state machine (`syncv2/protocol.py`)
- three-way merge (`syncv2/merge.py`)
- stable `sync_id` identity
- revision model (`server_rev` / `row_rev` / `base_json` / global `sync_sequence`)
- durable outbox (`pending → in_flight → applied`, coalescing, retry)
- idempotent operation handling (`op_id` / `applied_ops` ledger)
- incremental pull (server-revision watermark)
- conflict detection / storage / resolution backend
- tombstones (delete propagation with resurrection prevention)
- invoice collision handling
- SR ordering handling
- retry/recovery logic (`syncv2/retry.py`)
- comprehensive isolated tests (66 syncv2 tests: 16 merge + 14 server + 10 engine + 4 invariants + 11 correction/regression + 11 Phase-5 UI/presentation)

Phase 4 deliberately did **NOT** integrate the engine with Streamlit application workflows.

A correction pass after the read-only Phase-4 audit fixed the flagged defects (resolution
`base_json` now equals the resolved snapshot; losing-replica convergence for
KEEP_OFFLINE/KEEP_ONLINE/MERGE; one conflict record per conflicting field; a real grouped
month-level SR conflict with deterministic resolution; DB-generated integer conflict ids
for PostgreSQL compatibility; file-lock single-flight wiring; tokenised retry
classification; delete-vs-stale-update conflict handling). Sync V2 remains standalone and
NOT integrated.

### Phase 5 — Sync V2 Status + Conflict UI
Completed.
Presentation layer added OUTSIDE the engine: `sync_v2_state.py` (pure view models /
status classification / conflict views) and `sync_v2_ui.py` (status badge + details,
polished offline warning with Retry / Continue Offline, record-level conflict review
with Keep Offline / Keep Online / Review & Merge, grouped SR ordering review including
custom order, invoice-collision advisory, and delete-vs-edit explanation). A clearly
labelled transitional "Sync V2 — Status" section was added to the Offline Settings page
beside the untouched old "Sync Now". The UI never starts a sync session from rendering
and makes no network call to render status. Conflict resolution calls the Phase-4
`engine.resolve_conflict()` backend only. Sync V2 is still NOT integrated into
automatic/startup/background sync, and the old sync remains operational.

### Phase 6 — Offline Write-Path / Outbox / Tombstone Integration
Completed (read-only integration audit: PASS).
A central Sync-V2-aware write service (`sync_write.py`) plus Sync-V2-aware CRUD in
`database.py` make every normal Offline Finance Desk write ONE atomic local SQLite
transaction: business change + captured BASE + `row_rev`/`updated_at` advance +
durable outbox op + Phase-4 coalescing. Create assigns a stable `sync_id`; delete
is a tombstone (`deleted_at`, sync_id preserved, single-transaction renumbering);
SR reorder writes one upsert per affected row in one transaction. Legacy behavior
is preserved when the Phase-1 schema is absent or when running against
PostgreSQL/Neon. Tombstones are hidden from normal business views and from old-sync
readers. Old Sync Now remains operational; no Sync V2 op is ever sent automatically.

### Phase 7A — Real PostgreSQL/Neon Compatibility + Production Read-Only Audit
Completed (strictly read-only; see `docs/SYNC_V2_PHASE7A_AUDIT.md`).
Live read-only audits confirmed: Neon (PostgreSQL 18.6) and Offline SQLite each
carry the full Phase-1 schema; the Phase-3 baseline is fully intact (1,523 shared
sync_ids, 0 business/base/rev/tombstone/SR-order diffs, empty outbox/applied_ops/
conflicts, sync_sequence 0). Exactly one post-bootstrap legacy identity exists on
both replicas without sync_id (Offline 2534 / Online 2698 - one identity pushed by
old Sync Now). No legacy-sync duplicate damage was found. Code-path audit result:
**ONLINE WRITE CAPTURE IS NOT YET SYNC V2 AWARE** (Online CRUD runs the legacy
branches; edits/deletes/new rows never advance server_rev/row_rev or create outbox
ops, so SyncEngine.pull() cannot discover Online changes). No isolated PostgreSQL
environment exists, so real-PG execution (Step-9 matrix) remains NOT TESTED and is
assigned to Phase 7B.

### Phase 7B — Isolated Online Write-Capture (server-side) Design + Implementation
Completed (isolated only; see `docs/SYNC_V2_PHASE7B_AUDIT.md`).
`online_write.py` is the new backend-agnostic Online write seam wired into
`database.py` (gated on PostgreSQL + Phase-1 schema): Online creates assign a
stable uuid4 sync_id and a server revision; edits preserve sync_id and advance
server_rev (base_json refreshed to server-current, never force-agreeing the
Offline base); deletes become tombstones with same-transaction month renumbering;
SR moves allocate one revision per affected row. NULL/blank-sync legacy rows are
refused by the seam. Online business views now hide tombstones on PostgreSQL when
the column exists. 15 new tests (PASS-EMULATED on SQLite twins) cover create/edit/
delete/SR propagation, different-field union, same-field and delete-vs-edit
conflicts, grouped SR conflict + resolution, invoice independence/advisory,
rollback atomicity, NULL-sync refusal, and revision monotonicity. Real PostgreSQL
execution remains NOT TESTED (no isolated PG environment).

### Phase 7C — Real-PostgreSQL E2E Validation + Production Adoption Runbook
PARTIAL / E2E-BLOCKED (see `docs/SYNC_V2_PHASE7C_AUDIT.md`).
The production NULL-sync adoption runbook + classifier were designed
(`scripts/sync/adoption_design.py`; category 1 exact-counterpart only, serial `NA`
never an identity key, invoice alone never identity) and proven with 12 synthetic
SQLite-twin tests (idempotent, transactional, rollback-safe, rerun-safe,
incapable of merging a different record, zero outbox/conflict/duplicates).
Production was re-verified read-only and is unchanged (1,524/1,524, Tarun
identity still NULL on both replicas, all zero revisions/metadata). The real
PostgreSQL E2E matrix and concurrency tests are NOT TESTED - no isolated PG
environment exists on this machine and production was never used as a test
database. Production adoption was NOT executed.

## Current Operational Behavior

The user can continue using Finance Desk normally.

The OLD sync system remains the operational sync mechanism until Sync V2 is fully integrated and explicitly activated in a later approved phase.

Sync V2 must NOT be described as currently replacing or improving the old sync behavior.

## Important Known Limitation

Until later phases are completed, the old sync system retains its existing limitations, including its append-only behavior and inability to reliably synchronize ordinary edits/deletes/reordering as true bidirectional updates.

Do not attempt to solve these limitations in this checkpoint task.

## Next Phase

### Phase 7C-E2E is COMPLETE (real-PostgreSQL E2E executed)

The Phase 7C-E2E objective (obtain an isolated PostgreSQL and execute the E2E
matrix) is **done**: the 43-item matrix, Online-seam checks, BASE semantics,
invariant sweep and concurrency tests all PASS on a real, isolated PostgreSQL
16.4 (`is_pg=True`). One genuine concurrency defect (silent last-writer-wins on
same-record races) was found during the concurrency tests and fixed with an
optimistic `server_rev` write guard + bounded retry in `syncv2/server.py`; all
existing SQLite twin suites stay green.

The next phase is still **NOT authorized** by the working rules. When it is
approved, a Phase-8 cutover plan would, in order: deploy the Sync V2-aware
Online seam, execute the controlled production NULL-sync adoption (Offline 2534
/ Online 2698), then freeze the old Sync Now at the planned point - while
keeping automatic/startup/background sync disabled until explicitly enabled.


Later planned phases are:
- Phase 8 — automatic startup/background synchronization and production rollout/hardening

These phases are **NOT authorized** by this checkpoint task.

## Critical Resume Instructions

When development resumes:
1. Read this checkpoint first.
2. Read the existing Sync V2 documentation (`docs/syncv2_engine.md`,
   `docs/SYNC_V2_PHASE7A_AUDIT.md`, `docs/SYNC_V2_PHASE7B_AUDIT.md`,
   `docs/SYNC_V2_PHASE7C_AUDIT.md`) and project history.
3. Treat Phases 4-6, 7A, 7B and the Phase-7C adoption-design/synthetic-test work
   as completed; do not rebuild them.
4. Resume from Phase 7C-E2E (obtain an isolated PostgreSQL and execute the E2E
   matrix).
5. Do not redo completed phases unless a later review discovers a specific defect.
6. Do not enable automatic synchronization prematurely.
7. Do not silently modify production data.
8. Preserve the existing Finance Desk workflow until the appropriate integration phase is explicitly authorized.

## Git State at Checkpoint

Current `git status --short` (inspected at checkpoint creation):

```
 M .gitignore
?? Archive/
?? docs/syncv2_engine.md
?? scripts/backup/
?? scripts/migrations/migrate_sync_schema.py
?? scripts/sync/bootstrap_apply.py
?? scripts/sync/bootstrap_reconcile.py
?? sync_bootstrap_analysis.py
?? sync_schema.py
?? sync_v2_state.py
?? sync_v2_ui.py
?? syncv2/
?? test.pdf
?? tests/syncv2_helpers.py
?? tests/test_sync_bootstrap_analysis.py
?? tests/test_sync_bootstrap_apply.py
?? tests/test_sync_schema_migration.py
?? tests/test_syncv2_corrections.py
?? tests/test_syncv2_engine.py
?? tests/test_syncv2_invariants.py
?? tests/test_syncv2_merge.py
?? tests/test_syncv2_phase5_ui.py
?? tests/test_syncv2_server.py
```

The Phase 1–4 work has **NOT been committed or pushed** — it remains untracked in the working tree (only `.gitignore` carries a tracked modification). No commit was made for this checkpoint.

## Final Rule

This task is documentation-only: no application code, sync system, database, or UI was modified, and no migration/bootstrap/sync/Neon operation was run. No commit or push was performed.

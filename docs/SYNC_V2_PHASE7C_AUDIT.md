# Sync V2 Phase 7C — Real-PostgreSQL E2E Validation Audit

_Status: **PHASE 7C-E2E COMPLETE — REAL-POSTGRESQL 43/43 MATRIX + CONCURRENCY +
SEAM + BASE SEMANTICS + INVARIANTS ALL PASS** (one genuine concurrency bug found
and fixed in `syncv2/server.py`). No production mutation. Production adoption NOT
executed. Phase 8 is NOT authorized._

---

## 1. Environment

An isolated, disposable **real PostgreSQL 16.4** (official EDB Windows x64
binaries, user scope, loopback only) was stood up at `127.0.0.1:55432` with a
dedicated database **`finance_syncv2_test`** (created/destroyed freely per
scenario). See `docs/SYNC_V2_PHASE7C_E2E_ENVIRONMENT.md`. Production Neon
(`neondb`, PostgreSQL 18.6) was NEVER used as a test database and was only
queried read-only at the end.

## 2. Isolation method

- The Online replica under test is the real local PostgreSQL (`is_pg=True`,
  psycopg2, multiple independent connections/threads).
- The Offline replica is the project's real SQLite twin pattern (`is_pg=False`),
  matching production Offline.
- Existing Sync V2 modules (`syncv2/*`, `online_write.py`, `sync_schema.py`,
  `sync_write.py`) ran unchanged (one product fix documented below).
- Harness: `temp/p7ce2e_full_matrix.py` (43-item matrix + Online-seam +
  BASE-semantics + invariant sections); smoke `temp/p7ce2e_smoke.py`.

## 3. Schema results (real PG)

`sync_schema.migrate_sync_schema` on a fresh PG database added the 5 Sync V2
columns and created all 5 sync tables; `translate_ddl` (SERIAL/RETURNING-safe)
produced the base `records` table. Unique index on `records(sync_id)` verified.

## 4. Store results (real PG)

Insert/update/soft-delete/SR-swap/outbox/applied_ops/conflict/revision/pull/
tombstone paths all exercised with `is_pg=True`; `sync_sequence` row-locked
`next_revision` verified under 6-thread concurrency (no duplicates/gaps).

## 5. Full E2E matrix — REAL POSTGRESQL RESULTS (43/43 PASS)

| # | Scenario | Result |
|---|---|---|
| 1-8 | Offline create/edit/delete/SR-reorder -> push -> Online; Online create/edit/delete/SR-reorder -> pull -> Offline | **PASS (real PG)** |
| 9 | same-field edit conflict | PASS (field conflict persisted, KEEP_ONLINE converged) |
| 10 | different-field edit merge | PASS (both changes retained, no conflict) |
| 11 | multiple-field conflict | PASS (one conflict per field; KEEP_ONLINE + MERGE mixed) |
| 12 | Offline edit vs Online delete | PASS (delete_edit; KEEP_ONLINE no resurrection) |
| 13 | Offline delete vs Online edit | PASS (delete_edit; KEEP_ONLINE edit survives) |
| 14 | stale BASE after multiple remote changes | PASS (merge applied, rev monotonic, base advanced) |
| 15-17 | Keep Offline / Keep Online / Review-Merge | PASS |
| 18 | sync_id stability (create/edit/delete lifecycle) | PASS |
| 19 | Online-created sync_id (uuid4, discoverable) | PASS |
| 20 | duplicate sync_id prevention (DB unique + apply path) | PASS |
| 21 | same invoice but independent records | PASS |
| 22 | invoice collision behavior (advisory, non-blocking) | PASS |
| 23 | BID collision behavior (never identity) | PASS |
| 24 | serial collision behavior (independent; divergence conflicts) | PASS |
| 25-26 | Offline / Online reorder deterministic | PASS |
| 27 | simultaneous same-month reorder | PASS (one grouped sr_ordering conflict, nothing half-applied) |
| 28 | grouped SR conflict (single group, sequences recorded) | PASS |
| 29 | grouped resolution convergence | PASS (KEEP_OFFLINE converged both replicas) |
| 30 | duplicate operation replay (no new revision, ledger once) | PASS |
| 31 | partial push (first op committed; failed op not lost; resume) | PASS |
| 32 | retry (network failure requeues pending; idempotent apply) | PASS |
| 33 | rollback (aborted server write reverts row + revision) | PASS |
| 34 | no-op sync | PASS |
| 35 | incremental pull (watermark honored) | PASS |
| 36 | concurrent server revision allocation | PASS (150/150 unique, contiguous, seq valid) |
| 37 | concurrent same-record writes | PASS (after fix: one applied + one conflict, no silent LWW) |
| 38 | conflict resolution under stale server revision | PASS (reopen guard; no blind apply; no data loss) |
| 39-43 | Tombstones: persist / pull / no resurrection / idempotent delete / hidden from reads | PASS |

## 6. Concurrency results (real PG, multiple connections)

A. **Concurrent revision allocation**: 6 threads x 25 allocations -> 150 unique,
contiguous revisions 1..150, no duplicates/gaps, `sync_sequence` value 150.

B. **Concurrent same-record edits**: two independent clients edited the same
field from the same BASE simultaneously. **A genuine product race was found and
fixed**: `apply_one` merged against a snapshot but wrote WITHOUT a row-level
guard, so both clients could report `applied` and the second silently overwrite
the first (lost update). Fix: `write_server_row(..., expected_server_rev)` adds
`AND server_rev = <rev the op merged against>`; `apply_one` catches the loss,
rolls the attempt back (no revision consumed) and retries against the refreshed
row, which then opens a REAL conflict. Verified: exactly one applied + one
conflict, loser value persisted in the conflict record, resolution converged
both replicas, no lost update. All SQLite twin suites remain green after the fix.

C. Stale-resolution guard: resolution whose recorded online value no longer
matches the current row is **reopened** (never blind-applied).

## 7. Online seam results (real PG) — PASS

Online CREATE assigns one uuid4 sync_id, server_rev>0, row_rev=0, server-current
base_json; Offline pull discovers it. EDIT preserves sync_id and advances
server_rev/base; DELETE is a tombstone (row retained, sync_id kept, revision
advanced, live reads hide it, pull propagates); SR MOVE via the seam keeps
sync_ids stable with valid revisions and discoverable ordering.

## 8. BASE semantics (real PG) — PASS

Scenario A (different fields): Offline A-edit + Online B-edit merge with BOTH
changes; BASE stays the previous mutually-agreed value until convergence, then
becomes the resolved snapshot on both replicas. Scenario B (same field both
sides): explicit blocking conflict with base/offline/online recorded.

## 9. Invariants after the suite (both replicas) — PASS

No duplicate sync_id; no NULL/blank sync_id on Sync-V2-created rows; server_rev
monotonic allocation; server rows row_rev=0; outbox drained; applied_ops op_ids
unique; no open blocking conflicts; physical = live + tombstones; replicas
converged (business, base semantic equality, tombstones); invoice numbers never
renumber; BID never identity; serial `"NA"` remains a normal value; SR ordering
deterministic.

## 10. Adoption runbook (design + synthetic tests only)

Classified runs were never executed against production. `adopt_pair` and the
classifier are proven by 12/12 synthetic SQLite-twin tests
(`tests/test_syncv2_phase7c_adoption.py`). The runbook rules are unchanged;
adoption of Offline 2534 / Online 2698 is NOT executed.

## 11. Failures & fixes

1. Environment: fresh PG `0xC0000142` startup crash fixed by staging MSVC
   runtime DLLs (environment, not product). Fresh DB needs the legacy `records`
   DDL before `migrate_sync_schema` (expected).
2. Harness assembly bugs in the new E2E harness (function-body displacement
   during staged file appends) were caught and repaired by a structural audit;
   tests re-run clean.
3. **Genuine product bug (fixed):** concurrent same-record edits could
   silently last-writer-win on real PG (Section 6B). Fixed with an optimistic
   `server_rev` write guard + bounded retry in `syncv2/server.py`.
4. Observation (not a fix): conflict resolution re-applies numeric values as
   text through the JSON snapshot (numeric columns normalize the live row);
   existing product comparisons already use semantic numeric equality.

## 12. Production read-only verification (Step 9) — unchanged

Offline (copy): records 1,524 (live 1,524); NULL-sync identities 1; server_rev>0
= 0; row_rev>0 = 0; outbox 0; applied_ops 0; conflicts 0; sync_sequence 0.
Neon (SELECT-only): identical values; Tarun Kumar Offline id 2534 / Online id
2698 both NULL-sync and untouched. No production writes occurred.

## 13. Remaining blockers

1. Online (Render) application is still running pre-deployment code paths; the
   Sync V2-aware Online seam must be deployed before real bidirectional use.
2. Production NULL-sync adoption (2534/2698) remains designed but NOT executed.
3. Old sync still operational and must be frozen only at the planned cutover.
4. Phase 8 (automatic/startup/background sync + rollout) is NOT authorized.

## 14. GO/NO-GO for Phase 8

Real-PostgreSQL E2E objective is **COMPLETE-PASS (43/43 + concurrency + seam +
BASE + invariants)**. Phase 8 remains **NOT authorized** per the working rules;
the natural next step is a controlled Phase-8 cutover plan (deploy Online seam,
execute adoption, freeze old sync) after explicit approval.

---

_Audit date: 2026-09-02. Real-PG E2E executed on an isolated local PostgreSQL
16.4; production read-only only. No production mutation, no adoption execution,
no automatic sync, no deployment. No commits/pushes._


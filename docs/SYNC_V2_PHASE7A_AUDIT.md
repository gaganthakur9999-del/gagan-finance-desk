# Sync V2 Phase 7A — Real PostgreSQL/Neon Compatibility & Production Read-Only Audit

_Status: COMPLETE (read-only). No production mutation. Phase 7 is NOT complete.
Next authorized phase: **Phase 7B** (see section 16)._

Evidence tags used throughout:

- **VERIFIED** — observed via live read-only queries against the real databases.
- **CODE-REVIEW ONLY** — established by source inspection; not executed on real PostgreSQL.
- **NOT TESTED** — no isolated PostgreSQL environment existed to execute against.
- **BLOCKED** — prevents a specific later action until resolved.

---

## 1. Scope

Prove Sync V2 compatibility with real PostgreSQL/Neon, audit the real production
baseline (Neon + Offline SQLite) read-only, investigate NULL/blank `sync_id` rows,
audit legacy-sync duplicates, verify the Phase-3 bootstrap baseline is intact,
audit whether Online-side writes are captured by Sync V2, and assess readiness for
controlled real Sync V2 E2E testing. Nothing was created or altered in production.

## 2. Safety statement

All Neon access used `psycopg2` with `set_session(readonly=True, autocommit=True)`
(a hard guard - any write statement would error). All SQLite access used
`sqlite3.connect("file:data/finance.db?mode=ro", uri=True)`. Only `SELECT`
statements were issued. No triggers, indexes, schema, sync metadata, business
fields, outbox/conflicts/applied_ops/sync_state/sync_sequence were touched.
No production SyncEngine run. No sync activation.

## 3. Production Neon audit — VERIFIED

| Metric | Value |
|---|---|
| Connected | True |
| database / user | `neondb` / `neondb_owner` |
| host / port | `ep-empty-hill-az58hboq.c-3.ap-southeast-1.aws.neon.tech` / 5432 |
| PostgreSQL version | 18.6 |
| records total / live / tombstones | 1,524 / 1,524 / 0 |
| `sync_id` NULL or blank | 1 (id 2698) |
| `sync_id` malformed | 0 |
| `sync_id` duplicate groups | 0 |
| `server_rev` min / max / count>0 | 0 / 0 / 0 |
| `row_rev` min / max / count>0 | 0 / 0 / 0 |
| `base_json` NULL/empty | 1 (the NULL-sync row) |
| `deleted_at` non-null | 0 |
| outbox by status | `{}` |
| applied_ops | 0 |
| conflicts by status | `{}` |
| sync_sequence | value 0 |
| sync_state (id 1) | last_success 2026-09-02T07:24:37Z, last_error NULL, last_pulled_sync_rev 0, conflict_count 0 |

Schema tables (public): `records`, `outbox`, `applied_ops`, `conflicts`,
`sync_state`, `sync_sequence`. `records` has all 27 columns including the 5 Sync
V2 columns. Indexes include unique `idx_records_sync_id`, `uq_outbox_op_id`,
`uq_applied_ops_op_id`, plus `idx_records_deleted_at`, `idx_outbox_status`,
`idx_conflicts_status`, `idx_applied_ops_result`. — **VERIFIED**.

## 4. Offline SQLite audit — VERIFIED

| Metric | Value |
|---|---|
| file | `data/finance.db` (read-only) |
| records total / live / tombstones | 1,524 / 1,524 / 0 |
| `sync_id` NULL or blank | 1 (id 2534) |
| `sync_id` malformed | 0 |
| `sync_id` duplicate groups | 0 |
| `server_rev` min / max / count>0 | 0 / 0 / 0 |
| `row_rev` min / max / count>0 | 0 / 0 / 0 |
| `base_json` NULL/empty | 1 (the NULL-sync row) |
| `deleted_at` non-null | 0 |
| outbox by status | `{}` |
| applied_ops | 0 |
| conflicts by status | `{}` |
| sync_sequence | value 0 |
| sync_state (id 1) | last_success 2026-09-02T07:22:33Z, last_error NULL, last_pulled_sync_rev 0, conflict_count 0 |

Schema identical on the replica (6 sync tables; `records` 27 columns; unique
`idx_records_sync_id`; unique constraints on outbox and applied_ops `op_id`). —
**VERIFIED**.

## 5. Cross-replica comparison — VERIFIED

- shared `sync_id` identities: **1,523** (the full Phase-3 baseline)
- `sync_id` present only Offline: 0 · only Online: 0
- business-field differences among shared ids: **0**
- `base_json` string mismatches among shared ids: **0**
- `server_rev` differences: **0** · `row_rev` differences: **0**
- `deleted_at` differences: **0** (no tombstones anywhere)
- live SR ordering per month differences: **0 months**

Conclusion: the two replicas carry the exact same 1,524 business identities
(1,523 synced + 1 unsynced legacy identity), field-identical. — **VERIFIED**.


## 6. NULL / blank sync_id investigation — VERIFIED

Exactly one identity on EACH replica has no `sync_id` (both are the SAME business
record duplicated across replicas by the old sync - they are counterparts of each
other, not duplicates of any baselined identity):

| Side | id | sr_no | bid_date | name | phone | BID | invoice | serial | price | month | updated_at (UTC) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Offline | 2534 | 5 | 02-09-2026 | Tarun Kumar | 7018273919 | B436420370 | 260906 | 605PRBA130421 | 26000.0 | SEPTEMBER_2026 | 2026-09-02 08:49:12 |
| Online | 2698 | 5 | 02-09-2026 | Tarun Kumar | 7018273919 | B436420370 | 260906 | 605PRBA130421 | 26000.0 | SEPTEMBER_2026 | 2026-09-02 08:50:35 |

Deterministic evidence:
- identical invoice + serial + BID + name + phone + price + date + month on both
  replicas;
- Offline `updated_at` (08:49:12 UTC) precedes Online `updated_at` (08:50:35 UTC)
  by ~83 s - consistent with local creation followed by old "Sync Now" push;
- the local row has no `sync_id`, which is only possible when it was created by a
  **pre-Phase-6** `add_record` (Phase-6 code assigns `sync_id` whenever the
  Phase-1 schema exists, and the schema exists here);
- invoice 260906 / serial 605PRBA130421 are unique on both replicas, so this is
  NOT a duplicate of a baselined record.

Classification:
- Offline row 2534 — **post-bootstrap new local record** created by a pre-Phase-6
  app process, with a deterministic online counterpart (class 2 + 5).
- Online row 2698 — **post-bootstrap new online record** created by old Sync Now
  as a copy of the local row, with no sync metadata (class 3 + 5).
- Neither is a duplicate of an existing synced record; the pair is ONE unsynced
  identity spanning both replicas.

## 7. Legacy duplicate investigation — VERIFIED

- Within-replica duplicate serial groups: Offline 1 / Online 1 - both are the
  identical placeholder value `"NA"` on two clearly independent baselined
  identities: CHARAN DASS (offline 998 / online 988, invoice 260712, sr 10) and
  MOHAR SINGH (offline 999 / online 989, invoice 260713, sr 11). **Clearly
  independent records**, not duplicates.
- Within-replica duplicate invoice groups: 0 / 0.
- Cross-replica serial-ownership mismatch candidates: 0.
- No same-BID / same-name+phone+month / same-name+product+date duplicate groups
  beyond the single NULL-sync identity in section 6.

Conclusion: no legacy-sync duplicate damage inside either replica, and no baseline
identity is duplicated. — **VERIFIED**.


## 8. Bootstrap baseline verification — VERIFIED

Cross-referenced against `data/syncv2/bootstrap_apply_20260902_072444.json`
(pairs 1,523 = 1,522 auto + 1 manual NEEL CHAND; 16 approved online sr fixes;
initial revs 0/0/0; post-apply verification: 0 business diffs, 0 base_json
mismatches, sync_ids 1,523 each side, outbox/applied_ops/conflicts 0):

- shared sync_id coverage today: **1,523 / 1,523** (unchanged);
- 1:1 mapping intact (0 duplicate sync_ids, 0 only-one-side sync_ids);
- base_json equality per pair: **intact** (0 mismatches);
- server_rev / row_rev baseline: still all 0 (no engine ever ran);
- SR ordering: identical per month on both replicas (0 order diffs);
- business-field equality per pair: **intact** (0 diffs);
- tombstone state: 0 tombstones both sides;
- sync_state checkpoint: `last_success` timestamps from the bootstrap apply run,
  `last_pulled_sync_rev=0`, `conflict_count=0`; sync_sequence still 0.

Baseline drift: **none**. The only post-bootstrap change is the single legacy
identity in section 6 (added 08:49-08:50 UTC, after the 07:22-07:24 apply) - a
normal consequence of the old sync remaining operational, not baseline corruption.
— **VERIFIED**.

## 9. Online-side write capture audit — CODE-REVIEW ONLY (no test writes)

`database._sync_schema_present(conn)` returns False whenever `USE_POSTGRES` is
true, so every CRUD path of the Online (Render/Neon) application executes the
legacy branches exactly as before Phase 6.

| Online operation | server_rev | row_rev | base_json | outbox | sync_id |
|---|---|---|---|---|---|
| create (add_record) | untouched (0) | untouched (0) | NULL | none | not assigned (NULL) |
| edit (update_record) | untouched | untouched | untouched | none | preserved |
| delete (delete_record) | n/a | n/a | n/a | none | row physically deleted |
| SR move (swap_sr_no) | untouched | untouched | untouched | none | preserved |

Answers:
1. Does the Online write modify server_rev? **No.**
2. Does it modify row_rev? **No.**
3. Does it modify base_json? **No.**
4. Does it create an outbox entry? **No.**
5. Does it preserve sync_id? Edit/SR-move: yes (untouched). Create: leaves NULL.
   Delete: physically removes the row and its sync_id.
6. Can SyncEngine.pull() discover the Online change? **No.** Edits never advance
   server_rev; new Online rows have server_rev 0 and sync_id NULL; Online hard
   deletes leave no tombstone row to pull.
7. Can the server coordinator distinguish an Online change from an unchanged row?
   **Not via the revision watermark.** If an Offline op later arrives for the same
   identity, divergence is detected *value-based* (three-way merge of the op BASE
   against the server row's current business values) - but there is no incremental
   change signal.
8. Can a conflict be detected if Offline has a stale BASE? **Only value-based and
   only when Offline pushes an op for that identity.** An Online-only change that
   is never pushed against stays silent.

**"ONLINE WRITE CAPTURE IS NOT YET SYNC V2 AWARE."**

Missing architectural seam: the Online application's CRUD writes never bump
server_rev/row_rev, never maintain base_json, and never emit an outbox/op on Neon.
There is no DB trigger, no Online outbox producer, and no server-side write path
that records Sync V2 revisions for Online business changes. (Phase 6 deliberately
integrated only the Offline/SQLite side.)

Additional consequence (code review + observed production state): pushing a

## 10. PostgreSQL compatibility findings

**Isolated PostgreSQL environment: `REAL POSTGRESQL E2E ENVIRONMENT NOT AVAILABLE`.**
No local PostgreSQL server (no psql, no service, port 5432 not listening), no
dedicated test database, no Neon branch/API access in this project. Nothing was
created anywhere; production was never a candidate. The Step-9 checklist items
(A-Y) are therefore **NOT TESTED** on real PostgreSQL; the analysis below is
**CODE-REVIEW ONLY**.

Code-level compatibility review:
- Placeholder style: dual (`database._fix_sql` for app CRUD; `syncv2.store.ph()`
  for the engine); no unparameterized record writes. — OK by review.
- Generated IDs: sync-table `id SERIAL` via `translate_ddl`; conflict ids are
  DB-generated with `RETURNING id` on both backends (Phase-4 correction). SQLite
  RETURNING (>=3.35) is exercised by the existing suites. — OK by review.
- sync_sequence revision allocation: PostgreSQL path uses a row-locked
  `UPDATE sync_sequence SET value=value+1` (atomic); SQLite uses an in-process
  lock. — OK by review (execution on real PG NOT TESTED).
- JSON/TEXT: payload_json/base_json are TEXT; engine writes canonical
  json.dumps(..., separators=(",", ":")) and reads with json.loads; no jsonb
  dependency. — OK by review.
- Timestamps: ISO-8601 strings (with tz offset) in TEXT columns; never compared
  in SQL; never a sync authority. — OK by review.
- Uniqueness/idempotency objects verified present on Neon: uq_outbox_op_id,
  uq_applied_ops_op_id, unique idx_records_sync_id. — VERIFIED.
- No SQLite-only keywords reach the PG path: INSERT OR IGNORE/REPLACE,
  last_insert_rowid, sqlite_sequence and PRAGMA are confined to SQLite-only
  branches; `_executescript` drops PRAGMA statements for PG; AUTOINCREMENT is
  translated to SERIAL. — OK by review.
- Optimistic guard `update_local_row(..., expect_server_rev=...)` is a
  parameterized `UPDATE ... WHERE sync_id=? AND server_rev=?` with rowcount
  checking. — OK by review.

Remaining real-PG-only risks UNVERIFIED (Phase 7B isolation targets): concurrent
apply_one transaction isolation; next_revision under concurrent sessions;
sequence behavior after rollback; RETURNING through the exact psycopg2 cursor
usage; and the full Step-9 matrix (A-Y).

Phase-6-created Offline record whose identity already exists Online as a NULL-sync
row (section 6) would create a second Online row, because the server looks rows up
by sync_id and the legacy Online row has none. NULL-sync adoption must therefore
precede any real push.


## 11. Concurrency / revision findings — CODE-REVIEW ONLY

- `next_revision()`: PostgreSQL serializes concurrent allocations with a row lock
  on sync_sequence (single UPDATE); two concurrent pushes cannot receive the same
  revision on PG. SQLite path serializes in-process. — OK by review.
- Revision gaps after a rolled-back operation: acceptable by design (revisions are
  a monotonic watermark; gaps are harmless).
- Concurrent ops on the SAME record: `apply_one()` runs per-op transactions with
  READ COMMITTED semantics; `write_server_row()` guards updates only by `sync_id`
  (no server_rev WHERE guard on the coordinator write). Two concurrent pushes for
  the same record could each merge against a pre-commit snapshot and the last
  commit wins. This is mitigated today by single-engine/single-Offline-replica
  design plus the applied_ops replay ledger, but it is NOT proven on real PG.
  — CODE-REVIEW ONLY / NOT TESTED.
- Idempotent retries: replay checks applied_ops under a UNIQUE constraint; retries
  of the same op_id reuse the stored result. — OK by review.
- Partial failure metadata: each op commits independently and is recorded before
  ack; interrupted batches are re-drivable. — OK by review.
- Conflict resolution overwrite risk: resolution re-reads the server row and
  re-opens (does not blind-apply) if this conflict's own field/state moved. — OK
  by review.
- Single-flight is client-side only; there is no production network coordinator
  yet (repo's ServerAdapter is in-process). A deployment seam is Phase-7B scope.

## 12. Performance sanity — NOT TESTED

No production load test was run. No isolated PostgreSQL existed to measure the
suggested workloads (100/500 queued edits, incremental pull, no-op sync, conflict
creation/resolution). The SQLite twin suites give functional equivalence only, not
performance. Phase 7B should capture the suggested numbers on an isolated PG
environment and check only for pathological behavior (e.g., per-op N+1 scans in
coalescing, index usage on pull where server_rev>N).

## 13. Blocking issues

No Phase-7A deliverable is blocked. For Phase 7B's goal of *controlled real Sync V2
E2E testing*, the following are blockers until resolved:
1. **Online write capture is not Sync V2 aware** (section 9). Any real production
   E2E that tolerates Online-side edits would diverge silently.
2. **NULL-sync legacy identity must be adopted before any real push** (sections 6
   and 9): one identity exists on both replicas without sync_id (Offline 2534 /
   Online 2698); pushing its Phase-6 counterpart later would create an Online
   duplicate.
3. **No isolated PostgreSQL environment exists.** Real-PG execution (schema
   migration, rollback/commit, RETURNING ids, revision allocation under
   concurrency, full A-Y matrix) has not been run anywhere.
4. **No production coordinator deployment seam** exists (in-process adapter only).

## 14. Non-blocking issues

- Serial `"NA"` placeholder appears on two independent baselined identities
  (CHARAN DASS / MOHAR SINGH) - pre-existing data artifact, not a sync defect.
- `_fix_sql` performs a naive `?` to `%s` replacement; harmless today (no literal
  `?` in SQL text) but worth a comment guard during Phase 7B.
- The single legacy identity in section 6 will, after adoption, still have no
  base_json/sync history; adoption must set base_json from the Offline copy and
  treat both copies as one identity (no fake revisions).
- DB-file restore and date/bootstrap migrations remain ADMINISTRATIVE (unchanged);
  a restore that replaces the local DB can stale outbox/sync_state (Phase-8
  concern, documented previously).


## 15. Exact recommended Phase 7B work

1. Stand up an **isolated PostgreSQL/Neon environment** (Neon branch, separate
   project/schema, or local PG) that is never production; run the full Sync V2
   test suite against it with is_pg=True.
2. Execute the Step-9 matrix (A-Y) on the isolated PG: schema migration, rollback/
   commit, RETURNING conflict ids, UUID sync_id storage, JSON/TEXT and timestamp
   round-trips, concurrent revision allocation, applied_ops/outbox uniqueness,
   optimistic concurrency, stale-base handling, three-way merge, multi-conflict,
   grouped SR conflicts, tombstones, delete-vs-stale-edit, idempotent replay,
   retryable failures, partial push, incremental pull, conflict resolution,
   losing-replica convergence, no-op sync, single-flight.
3. Design and implement **Online write capture** (server-side revision/outbox
   recording on Neon when the Online app writes) - in the isolated environment
   first; keep Offline Phase-6 behavior unchanged.
4. Design an **adoption procedure for NULL/blank sync_id rows** (both replicas,
   deterministic matching only). The section-6 identity is the sole current
   candidate; it must be adopted as ONE identity with base_json = Offline snapshot
   and revs 0/0 before any real push.
5. Build the **coordinator deployment seam** (network adapter or server process
   exposing pull/apply_ops/resolution against Neon) and an isolated engine-client
   harness.
6. Capture the performance sanity numbers from section 12 on the isolated PG.
7. Keep the old Sync Now operational; keep automatic/background/startup sync
   disabled; re-run the full existing suite after every change.

## 16. Verdict

**GO for Phase 7B** - as an *isolated-environment* phase (items 1-7 above), with
production still read-only and old sync still operational.

**NOT GO for any production-connected Sync V2 E2E** until the Phase 7B work
completes: Online write capture (blocker 1), NULL-sync adoption (blocker 2), an
isolated PG proof of the engine (blocker 3), and a real coordinator seam (blocker
4) must all be demonstrated first. No production push, conflict resolution, sync_id
adoption, or schema change is authorized by this audit.

---

## Appendix - evidence status summary

| Topic | Status |
|---|---|
| Neon metrics / schema | VERIFIED |
| SQLite metrics / schema | VERIFIED |
| Cross-replica comparison | VERIFIED |
| NULL sync_id investigation | VERIFIED |
| Legacy duplicate investigation | VERIFIED |
| Bootstrap baseline verification | VERIFIED |
| Online write capture | CODE-REVIEW ONLY (code paths; no test writes) |
| PostgreSQL SQL compatibility | CODE-REVIEW ONLY |
| Real-PG execution (Step-9 A-Y) | NOT TESTED (no isolated environment) |
| Concurrency on real PG | CODE-REVIEW ONLY / NOT TESTED |
| Performance sanity | NOT TESTED |

_Audit date: 2026-09-02. Read-only. No production mutation. No commits/pushes._


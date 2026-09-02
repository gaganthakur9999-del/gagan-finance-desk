# SyncV2 Engine (Phase 4)

Pure-Python synchronization engine for the GFD offline/online pair. It does not
import or call Streamlit, and it is not connected to any application workflow yet.

Modules (`syncv2/`):

| Module      | Responsibility |
|-------------|----------------|
| `protocol`  | vocabulary: field classification, op types, outbox/conflict statuses, session states, structured `SyncResult` |
| `merge`     | pure three-way merge, month derivation, invoice collision, SR ordering, tombstone rules |
| `store`     | dual-backend SQL primitives (`is_pg=False` SQLite, `is_pg=True` PostgreSQL) |
| `server`    | coordinator: monotonic revisions, idempotency ledger, optimistic concurrency, apply/pull/resolve |
| `engine`    | `SyncEngine` client: single-flight, transactional outbox primitive, pull/merge/push/finalize, status |
| `retry`     | exponential backoff + jitter, temporary-vs-permanent classification |

## Sync protocol

Session: `IDLE -> CONNECTING -> PULL -> MERGE -> PUSH -> FINALIZE -> IDLE`, plus
`OFFLINE / ERROR / CONFLICT / NEEDS_ATTENTION / BUSY`.

1. **PULL** - ask the server for rows whose `records.server_rev > last_pulled_sync_rev`
   (incremental; never a full re-download once baselined).
2. **MERGE** - for every pulled row without a pending local op, three-way merge the
   client row against the server row relative to the shared `base_json`. If a row has
   a pending op it is skipped and re-fetched after push.
3. **PUSH** - coalesce upserts, then send pending outbox ops in batches. Every op
   commits independently server-side; on the first op error the batch stops and the
   remaining ops are re-queued locally.
4. **FINALIZE** - second incremental pull converges local rows to the exact resolved
   server state; `base_json` advances only here (mutual convergence), `row_rev` resets,
   `sync_state.last_pulled_sync_rev` advances.

## Revision model

- `server_rev` (row): the global server revision this row's state was based on.
- `row_rev` (row): this replica's count of unsynced local edits.
- `base_json` (row): ancestor snapshot of the last mutually-agreed business state.
- `sync_sequence` (server global): monotonic, transactional, incremented **only when a
  change actually commits**; no-op retries, failed transactions, and conflict-only
  events never allocate revisions. PostgreSQL serializes via the row lock on the
  sequence row; SQLite uses an in-process lock (documented limitation).

Baseline advancement rule: `base_json` moves only after successful mutual
convergence, never on local edit, outbox creation, send, conflict, or partial failure.

## Outbox lifecycle

`pending -> in_flight -> applied`, with `in_flight -> pending` on restart/failure and
`in_flight -> failed` for permanent errors, plus `superseded` (coalescing) and
`blocked` (open conflict on the same sync_id; never resent while open).

Payloads carry `op_id`, `sync_id`, `op_type`, the current payload snapshot, the
**ancestor base snapshot**, `base_rev`, and the local row revision - never just "read
the row now", because the row may have changed again.

Coalescing keeps the latest payload but the **oldest** base ancestor so conflict
detection information is never lost. A failed network operation never corrupts the
local DB: local commits complete, the op stays durable, the engine returns
`OFFLINE / RETRY_SCHEDULED`.

## Conflict model

Conflicts preserve `base/offline/online` (+sync_id, kind, field, month, timestamps,
resolution) in the existing `conflicts` table. On conflict the engine does NOT apply
the change, does NOT advance baseline, does NOT mark the op applied, and deletes
nothing. One conflict record is persisted per genuinely conflicting field (a
multi-field divergence keeps every field resolvable; no field is silently left
without a record). Resolving one field never converges/discards a sibling field -
the Offline replica is only converged after the record's last open conflict is
resolved. Unrelated records continue syncing.

A successful resolution applies the chosen value, sets the server `base_json` to
the **resolved snapshot** (never the stale pre-resolution state), advances exactly
one revision, and is idempotent. The reopen guard is per-conflict: it re-opens only
when THAT conflict's own field/state moved again, so a later legitimate conflict on
the same record resolves normally. After resolution the engine converges the losing
Offline replica deterministically: its business state becomes the resolved state,
`base_json` becomes the resolved snapshot, `row_rev` resets, and parked outbox ops
are superseded; offline-only non-conflict edits from the parked op are carried
forward through the normal push path. Conflicts resolved by a different engine
instance are detected on the next run and the Offline row force-adopts the resolved
server state (documented multi-client limitation - the primary engine performs
resolutions). Conflict ids are database-generated integers (never UUIDs-in-integer-
columns) so the SQLite and PostgreSQL/SERIAL paths behave identically; real Neon
verification remains Phase 7.

## Deletion model

Deletes become `deleted_at` tombstones (never physical). Offline delete, Online
delete, and both-delete converge safely; delete-vs-edit is a `delete_edit` conflict
in both directions - including a stale Offline upsert against a server tombstone
(kept ONLINE the row stays deleted and its business payload is never silently
mutated); a stale replica can never resurrect a tombstoned record (pull applies the
tombstone because base already reflects it). Repeated deletes are no-ops.

## Invoice / SR models

Invoice is editable business data of one identity (`sync_id` is identity, never
invoice/BID/serial/DB-id). Two distinct sync_ids owning the same normalized nonblank
invoice create an advisory `invoice_collision` review object - both are preserved,
never merged or renumbered. SR is month-scoped ordering: one-sided reorders
propagate through the normal per-row path; a both-sides reorder of the same month is
detected at push time and opens **ONE grouped `sr_ordering` conflict** per month
(BASE/OFFLINE/ONLINE sequences stored as JSON), parking the affected ops instead of
creating N per-row conflicts. Resolving the grouped conflict applies one
deterministic month ordering to every affected server row and the engine then
converges each affected Offline row. `month` is derived from `bid_date`.

## Failure recovery

Idempotent ops (`op_id` in `applied_ops` -> stored result replayed, never re-applied,
revision not re-bumped) make lost responses and retries safe. Crashes at any point
(before request, after server commit, before ack, after ack, before local finalize)
leave a recoverable state: in-flight ops reset to pending, applied ops replay,
watermarks never skip rows that still need convergence. Single-flight is enforced
in-process and, when a lock path is configured, by an O_EXCL file lock that is
released on success and on failure (BUSY is returned to competing processes; a stale
lock is broken after its age threshold). Blocked ops whose conflict was resolved by
another engine are retired and the Offline row force-adopts the resolved state on the
next run.

## Retry

Temporary failures (explicit tokenised markers: timeouts, connection
refused/reset, DNS/network-down/unreachable, unavailable, HTTP 5xx) re-queue with
exponential backoff + jitter (`attempts`, `next_retry_at`, `last_error`). Permanent
failures (schema, malformed payload, impossible state, business constraint,
unrecoverable conflict) go to `failed` / needs attention - never endless retries.
Arbitrary digits or the word "server" in an error message are never treated as
temporary by themselves.

## Production safety (Phase 4)

No production synchronization was performed. Engine tests use isolated temp SQLite
databases (server side emulated via the same `is_pg=False` code path, consistent with
the project's PostgreSQL translation/compat strategy). No application workflow, UI,
background sync, or automatic sync is enabled.

## Phase 5 — Status & conflict presentation (UI layer)

Presentation-only Streamlit components live OUTSIDE `syncv2/`:
`sync_v2_state.py` (pure view models: local status classification, conflict views)
and `sync_v2_ui.py` (status badge, status details, offline warning with Retry /
Continue Offline, record-level conflict review with Keep Offline / Keep Online /
Review & Merge, grouped SR ordering review incl. custom order, invoice-collision
advisory, delete-vs-edit explanation). The Settings page shows a clearly-labelled
"Sync V2 - Status" transitional section that renders "Not active" until a Sync V2
service/engine is connected; the classic old "Sync Now" remains untouched. The UI
never starts a sync session (no `run_once` from rendering), makes no network calls to
render the indicator, and resolves conflicts only through the Phase-4
`engine.resolve_conflict()` backend.

## Phase 6 — Local write-path integration (transactional outbox)

Every normal Finance Desk record write now funnels through Sync-V2-aware CRUD in
`database.py` backed by the central write service `sync_write.py`. On a local
(SQLite) database that carries the Phase-1 schema each write is ONE local
transaction:

    BEGIN
      business row change                      (CRUD owns the SQL)
      before-state captured                    (sync_id / base_json / server_rev / row_rev)
      advance row_rev + updated_at             (base_json + server_rev NEVER overwritten)
      durable outbox operation                 (payload = actual resulting snapshot)
      Phase-4 coalesce                         (newest payload, OLDEST base ancestor)
    COMMIT   -- any failure ROLLS BACK the whole logical write

Paths integrated: create (`add_record` assigns a fresh stable `sync_id` at insert),
ordinary edit (`update_record`: customer/phone/alt-phone/product/actual-product/
xcell/remarks/BID/bid_date/price/EMI/DI/DP taken/given-prod-price/serial/invoice/
any field), delete (`delete_record` becomes a tombstone: `deleted_at` stamped,
`sync_id` preserved, row never physically purged, month renumbering in the SAME
transaction with one upsert per renumbered live row), and SR move-up/move-down
(`swap_sr_no`: both rows swapped + two upserts, one transaction). sr_no remains
business/order data - never identity, no fake sync_id. PDF/manual/Excel-import paths
already call `db.add_record`/`db.update_record`, so they inherit the same guarantee
without per-page sync code.

Tombstones are invisible to normal business views (`deleted_at IS NULL` is applied
only when the column exists), so the user experience is identical to the physical
deletes they replace, and the OLD sync readers exclude them the same way. When the
schema is absent - or when running against PostgreSQL/Neon (the online application) -
all CRUD keeps its exact pre-Phase-6 behaviour.

Design invariants:
- Never "change DB row first, then read it back to build the payload" - the outbox
  payload is the transaction's real before/after state.
- No write path calls `run_once()`, and no write path performs any network I/O.
- Local-first: create/edit/delete/reorder commit immediately with no network wait.
- `updated_at` is never a sync/version/conflict authority (sync_id + base_json +
  revisions remain the model).
- Old Sync Now and the old sync scripts remain fully operational; Sync V2 is still
  NOT integrated into automatic/startup/background sync and no Sync V2 push happens
  automatically from a write.


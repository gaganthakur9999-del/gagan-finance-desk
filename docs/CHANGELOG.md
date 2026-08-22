# Changelog

All notable changes to **Gagan Finance Desk** are documented in this file.

This changelog tracks changes **from the current production baseline onward**. It does **not** attempt to reconstruct older history — the complete development history is preserved separately in [`PROJECT_HISTORY.md`](./PROJECT_HISTORY.md).

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project consciously does **not** use fabricated semantic versions. Change entries reference the production baseline described below.

---

## Changelog Policy

- **Scope:** this document records changes **from the current production baseline onward** — it is a forward-looking log, not a historical record.
- **Historical information** belongs in [`PROJECT_HISTORY.md`](./PROJECT_HISTORY.md); the project's feature inventory lives in [`FEATURE_HISTORY.md`](./FEATURE_HISTORY.md), and engineering deep-dives live in [`TECHNICAL_HISTORY.md`](./TECHNICAL_HISTORY.md).
- **No invented versions or dates:** releases are referenced by production state (commit / deployment status), never by fabricated semantic versions.
- **Future entries** should use the section headings:
  - `Added`
  - `Changed`
  - `Fixed`
  - `Performance`
  - `Documentation`
  - `Refactoring`

---

## Current Production Baseline

Describes the verified state that marks the start of change tracking. No version number is assigned — this baseline is identified by its production commit and synchronized deployment.

### Added
- Performance-optimized database layer: targeted SQL for quick-stats and record counts (Phase 1).
- Scalar invoice-number suggestion queries, LIMIT-based recent invoices, point-lookup move handler, cached Excel download, SQL pre-filtered EMI candidates, and a COUNT-only search caption (Phase 2).
- Deduplicated dashboard statistics (skips global monthly GROUP BY when a specific month is shown).
- `bid_date` normalization on **every write path** (`add_record`, `update_record`, both sync scripts, Settings → Sync Now) via `helpers._normalize_date()` — canonical storage format `DD-MM-YYYY` is enforced.
- One-time migration script (`scripts/migrations/migrate_date_format.py`) converting the 11 pre-existing `DD/MM/YYYY` records.
- Project history documentation: `PROJECT_HISTORY.md`, `FEATURE_HISTORY.md`, `TECHNICAL_HISTORY.md`.

### Changed
- Excel engine (Option E): alternating-row styling now reuses hoisted `PatternFill` / `Alignment` objects instead of constructing ~1.8 million per-cell objects at scale — exported workbook content is identical (verified cell-for-cell; only save-time metadata timestamps differ).
- Project structure reorganized into `docs/` and `scripts/` (`import/`, `sync/`, `migrations/`) subtrees.
- Sync launchers (`Sync Offline to Online.bat`, `Sync Online to Offline.bat`) made portable (use `%~dp0` instead of a hardcoded `E:\` path) and pointed at the relocated scripts.
- `.gitignore` hardened: `data/*.db`, `data/*.sqlite*`, `*.log`, and related runtime artifacts are now ignored.

### Fixed
- Date-format inconsistency: PDF extraction could previously store `DD/MM/YYYY`; all 1,392 records are now normalized to `DD-MM-YYYY` and cannot regress via application write paths.
- Premature full-table reads on hot pages removed while preserving identical outputs (verified by old-vs-new equivalence tests).

### Performance
- Generate-Invoice quick stats: 37.14 ms → 5.53 ms (6.7×).
- Settings record count: 19.99 ms → 5.27 ms (3.8×).
- `suggest_next_invoice()`: 4.5× @1.4K records, **14× @100K** (two scalar SQL queries).
- Recent invoices: 8.8×; move-target lookup: 8.5×.
- Excel export build: 2.1× after Option E (alternating-row styling 2.7×).
- Verified module-level code (incl. `migrate_dates()`) runs **once per server process**, not per rerun.

### Documentation
- Added `README.md` (project homepage).
- Added `docs/PROJECT_HISTORY.md`, `docs/FEATURE_HISTORY.md`, `docs/TECHNICAL_HISTORY.md`, `docs/CHANGELOG.md` (this file).

### Refactoring
- Monolithic-era structure replaced by the modular `pages/` package (pre-existing) and the new `docs/` + `scripts/` layout.

### Deployment readiness
- Git history preserved via `git mv` renames for relocated scripts.
- Production commit pushed to `origin/main` (local HEAD == origin/main).
- Render deployment uses `DATABASE_URL` → Neon (PostgreSQL) automatically; SQLite remains the offline desktop backend.
- Neon synchronized: no missing, extra, or modified records versus SQLite; every `bid_date` is `DD-MM-YYYY`.
- No temporary/audit scripts, backup databases, or runtime folders are tracked.

---

## Future Releases

**Performance Phase 3 - Render/Neon connection pooling & startup optimization (2026-08-20).**

### Added
- **PostgreSQL connection pool** - lazy `ThreadedConnectionPool(1, 10)` created on the first database touch (never at import, never per rerun), registered with `st.cache_resource` inside the Streamlit runtime. A `_PooledConnection` adapter preserves the existing `conn.close()` pattern: `close()` returns the connection to the pool instead of destroying it, rolls back any open/aborted transaction, discards broken/stale (Neon idle-timeout) connections, and falls back to a direct connection if the pool is momentarily exhausted.
- **Read-result caching** - `st.cache_data(ttl=30)` for read-only queries (`get_available_months`, `get_today_stats`, `get_dashboard_stats`, `get_recent_invoices`, `load_emi_candidates`, `get_monthly_card_stats`), resolved lazily at first call so CLI scripts/tests never import streamlit. Centralized `invalidate_cache()` clears the cache after every write.
- **`get_monthly_card_stats()`** - one `GROUP BY month` query replaces up to 4 separate per-month queries on the Generate Invoice cards (values verified identical).
- **PostgreSQL DB fingerprint** - `(COUNT(*), MAX(id), MAX(updated_at))` enables the Excel download cache on Neon (previously rebuilt on every rerun).
- **`PERF_DEBUG=1`** - optional, low-noise query-duration / pool-acquisition logging (off by default).
- **`FINANCE_DB_PATH`** env override for the SQLite database path (isolated tests / portable deployments; default unchanged).

### Changed
- `init_db()` is now **lazy** - runs once on the first `get_connection()` instead of at module import (recursion-safe, retries on failure). No import-time schema work.
- `migrate_dates()` is now **opt-in only** - never runs automatically, so normal startup never scans the records table.
- Records page reuses the `total` already returned by `search_records()` (duplicate `count_search_records()` call removed).
- Settings -> Sync Now is **batched**: one Neon connection + one transaction + `executemany` instead of a connect/commit per record (same dedup and `sr_no` logic).
- Manual-entry form and Generate Invoice compute `suggest_next_invoice()` only when the value is not already in session state.
- Lazy page/module imports: Dashboard and other pages no longer initialize PDF/DOCX/Excel machinery they do not need.

### Fixed
- Eager evaluation of `st.session_state.get("generated_invoice_no", suggest_next_invoice())` - the default argument ran `suggest_next_invoice()` (2 SQL queries) on every rerun even when the value already existed.
- PostgreSQL Records page rebuilt the entire Excel workbook on every rerun (now cached by fingerprint).
- `suggest_next_invoice()` ran on every rerun while the collapsed manual-entry form was open.
- Search matching was case-sensitive on PostgreSQL (`LIKE`) but case-insensitive on SQLite; `search_records()` / `count_search_records()` now use `LOWER(column) LIKE LOWER(?)` so both backends match case-insensitively.

### Performance
- `database` module import (no init/migrate at import): ~95-231 ms -> ~53 ms locally; **zero database connections at import**.
- `ui_components` heavy import chain (openpyxl + docxtpl + num2words + PDF stack): ~2,032 ms -> ~7 ms core chain; Dashboard page imports in ~1 ms with no heavy modules.
- Generate Invoice page second render (cached): ~50 ms in AppTest.
- Neon round-trips reduced architecturally: per-operation connection pool, one grouped month query, fingerprint-keyed Excel cache, batched sync, no duplicate COUNT. (Exact Neon latency not measurable locally; all queries verified against SQLite.)
- Records page calls `get_recent_invoices(10)` only when the Edit/Regenerate UI is open, not on every normal rerun - one fewer query / Neon round trip per Records rerun (22 Aug 2026).

### Documentation
- Updated `docs/CHANGELOG.md`, `docs/FEATURE_HISTORY.md`, `docs/PROJECT_HISTORY.md`, `docs/TECHNICAL_HISTORY.md`.

### Refactoring
- No UI, invoice, PDF, or Excel format changes. Business logic and both database backends (SQLite + PostgreSQL/Neon) preserved.
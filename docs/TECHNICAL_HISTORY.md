# Gagan Finance Desk — Technical History

**Reconstructed:** 2026-08-05. Records the **technical milestones**: database changes, performance optimizations, architecture improvements, sync implementation, Excel engine improvements, date normalization, and query optimizations. No invented version numbers.

**Confidence legend:** ✅ **Verified** (git commit / file timestamp / log) · 🟡 **Estimated** · ❓ **Unknown**

---

## Document Metadata

- **Document purpose:** Engineering reference for Gagan Finance Desk — preserves the verified technical evolution (architecture, database, performance, sync, Excel engine) and provides a current snapshot, measured project statistics, engineering milestones, a performance snapshot, and evidence-based future improvements. This is a development history, **not** a changelog or version history.
- **Last updated:** 2026-08-05
- **Reconstruction date:** 2026-08-05
- **Evidence sources used:**
  - Git history (`git log --all`, 9 commits, 2026-07-18 → 2026-08-01)
  - File-system creation/modification timestamps (recursive scan of the project tree)
  - `logs/errors.log` (2026-06-13 → 2026-08-05), `logs/activity.log`
  - `Backups/*.json` invoice backups (2026-06-22 → present)
  - `data/finance_backup_*.db` / `.sql` / `.json` snapshots
  - `Archive/Recovery_2026-08-03/*` scripts + `dry_run_report_20260803_115906.txt`
  - Live SQLite database inspection (`PRAGMA`, schema, row counts)
  - Measured benchmark/validation runs performed on 2026-08-05 (Phase 1, Phase 2, Excel Option E, date normalization, rerun-frequency instrumentation)
- **Confidence methodology:** ✅ **Verified** = direct git commit, file timestamp, log entry, or measured run. 🟡 **Estimated** = strong inference from surrounding evidence. ❓ **Unknown** = cannot be proven from available sources. Measured facts are stated as measurements; estimates are explicitly labeled.

---

## Project Summary

**Gagan Finance Desk** is a desktop-first finance management application for an electronics/mobile financing business (Bajaj-linked DO/EMI operations). It generates professional invoices from a linked template, extracts customer data automatically from Bajaj Delivery-Order PDFs, persists records in a local SQLite database, maintains a month-sheet Excel ledger, tracks EMI end-dates for follow-up, and synchronizes to a Neon/PostgreSQL cloud copy.

- **Primary purpose:** Convert a pre-existing month-by-month Excel ledger (SR NO per month, `YYMMNNN` invoice IDs, DP/DI/Xcell/BID/scheme tracking) into a streamlined desktop workflow: PDF → auto-form → invoice → save → backup → Excel sync → EMI follow-up.
- **Major capabilities:**
  - Bajaj DO PDF auto-extraction with editable confirmation form
  - GST-aware DOCX invoice generation (`template.docx`), with Windows PDF/PNG preview and cross-platform download
  - Per-invoice JSON backups on every save
  - Month-sheet Excel export/update (`Excel/ALL_RECORDS.xlsx`) with styled headers, alternating rows, auto column widths
  - Records search, pagination, month filter, sort, bulk delete, edit, regenerate, move-up/down
  - Dashboard (records/DP/DI totals, monthly & daily charts)
  - EMI Notification tab (last-EMI month tables with prev/current/next navigation)
  - Settings: GST rates, template/poppler paths, theme, DB backup/restore, cloud sync, system status
  - Offline→Online / Online→Offline synchronization to Neptune/PostgreSQL (dedup by invoice+serial)
- **Technology stack:** Python 3.14 · Streamlit ≥1.28 (UI framework) · SQLite (local) / psycopg2 + Neon/PostgreSQL (cloud) · openpyxl (Excel) · docxtpl + num2words (invoice template rendering) · pypdf (PDF text extraction) · docx2pdf/pdf2image (Windows-only PDF/PNG preview) · fpdf2 (cross-platform fallback listed) · num2words, openpyxl. Windows shell via `.bat` launchers.
- **Offline & online architecture:** desktop runs fully offline on SQLite (WAL); Render/cloud runs with `DATABASE_URL` set → psycopg2 to Neon/PostgreSQL. `database.py` is the single dual-backend layer and must always preserve both paths (a past regression is permanently guarded by comments).
- **Current maturity:** production-grade for day-to-day desktop use (1,392 records spanning Apr 2024–Aug 2026). Multiple structured migrations/recovery operations have been executed and verified. The 5 hot page paths are optimized to avoid full-table reads; the largest remaining per-save cost is the full Excel rebuild (verified at every scale).
- **Scope of this document:** technical evolution record + current engineering snapshot. Feature-level details live in `FEATURE_HISTORY.md`; the narrative history lives in `PROJECT_HISTORY.md`.

---

## Database

### Schema evolution (`database.py`, `data/finance.db`)

| Change | Date | Evidence / Notes |
|--------|------|------------------|
| `records` table created (SQLite) — 20 cols incl. `created_at`/`updated_at` timestamps | 23 Jun 2026 ✅ | `data/finance.db` first modified 06-23; schema preserved through recovery backups |
| `remarks TEXT DEFAULT ''` column added | between 11 Jul ↔ 16 Jul 2026 🟡 | `logs/errors.log` 07-11: `sqlite3.OperationalError: table records has no column named remarks` in `add_record`; by 07-16 refactor the `ALTER TABLE ... ADD COLUMN remarks` migration exists in `init_db()` |
| 6 indexes (`idx_records_invoice`, `serial`, `name`, `phone`, `month`, `bid_date`) | present by 16 Jul 2026 🟡 | `init_db()` `CREATE INDEX IF NOT EXISTS` |
| PostgreSQL/Neon path via `DATABASE_URL` | 18 Jul 2026 ✅ | commit `e42b579` |
| Dual-backend restored & permanently protected | 1 Aug 2026 ✅ | commits `0b40a3c`, `9a9ef57` ("regression happened once already") |
| Recovery import (1405 rows, 29 sheets) + April 2024 cleanup | 3 Aug 2026 ✅ | backup `.db`s + `delete_verified_apr2024.py` (24 rows; total → 1381) |
| Neon records replaced from canonical SQLite | 3 Aug 2026 ✅ | `neon_records_replace.py` + `finance_backup_neon_records_20260803_154335.sql/.json` |
| Date canonicalization (all bid_date = DD-MM-YYYY) | 5 Aug 2026 ✅ | `migrate_date_format.py`, backup `finance_backup_dateformat_20260805_150034.db` |

### Date handling

| Change | Date | Evidence |
|--------|------|----------|
| `migrate_dates()` canonicalizes ISO → DD-MM-YYYY + fixes `month` | present by 16 Jul 2026 🟡 | runs at module import |
| `add_record`/`update_record` derive `month` from 3 accepted formats | present by 23 Jun / 16 Jul 🟡 | seen in June-era code paths |
| **Write-path normalization** (`_normalize_date()` inside `add_record`/`update_record`, sync scripts, Settings sync) | 5 Aug 2026 ✅ | verified via smoke tests |
| One-time migration script `migrate_date_format.py` | 5 Aug 2026 ✅ | 11 rows converted; idempotent |

---

## Performance Optimizations

### Audit baseline (2026-08-05, verified)
- Real DB: 1,392 records / 404 KB / 28 months; all page queries <5 ms at that scale.
- Scale simulation (synthetic, same schema/indexes): `load_all_records()` 14.4 s @ 1M; `LOWER()`-defeated checks ~190 ms @ 1M; `MAX(sr_no)` 159 ms @ 1M; month sorts ~178 ms @ 1M; dashboard agg 284 ms @ 1M; `migrate_dates()` as a cold-start cost only (verified module-level code runs once per process, not per rerun).

### Phase 1 (5 Aug 2026 ✅)
| Change | SQL | Measured |
|--------|-----|----------|
| Generate-Invoice quick stats | `SELECT COUNT, SUM(dp), SUM(di) WHERE bid_date IN (3 formats)` | 37.14 ms → 5.53 ms (6.7×) |
| Settings record count | `SELECT COUNT(*)` | 19.99 ms → 5.27 ms (3.8×) |

### Phase 2 (5 Aug 2026 ✅)
| Change | SQL | Measured |
|--------|-----|----------|
| `suggest_next_invoice()` | 2 scalar queries: `MAX(SUBSTR(invoice_no,1,4))` + `MAX(CAST(SUBSTR(...)) ` | 4.5× @1.4K, **14× @100K**; 9 edge-case scenarios identical |
| Recent invoices | `SELECT invoice_no ... ORDER BY id DESC LIMIT 10` | 8.8×; identical lists |
| Move target | `SELECT id WHERE month=? AND sr_no=? LIMIT 1` | 8.5×; same id incl. boundary |
| Excel download | serve cached `Excel/ALL_RECORDS.xlsx` when DB fingerprint current (SQLite only) | ~0 rebuilds per rerun |
| EMI candidates | 8 columns; `WHERE scheme LIKE '%/%' AND bid_date != ''` | 1.3×; rows 1392→1353, cols 20→8; EMI set identical |
| Dashboard monthly GROUP BY | skipped when specific month selected; month cards skip per-card scans | 4→0 scans; view unchanged |
| Records count caption | `COUNT(*)` via `count_search_records` | replaces `SELECT * LIMIT 1` pattern |

### Excel engine (5 Aug 2026 ✅)
| Change | Evidence |
|--------|----------|
| Phase 3A profile | `_apply_alternating_rows` = 84–92% of export; **1.8M `Alignment` objects @100K** |
| Option E implemented (style-object reuse) | Alignment 25,562 → 506; export 2.1×; save 1.3×; Alignment-object memory −25,056 |
| Workbook equivalence | 28 sheets/names/order identical; all cell values & styles identical; widths, row heights, hidden rows/cols, freeze, merged, filters, print/margins identical; only `created`/`modified` metadata timestamps differ (save-time, inherent) |

---

## Architecture

| Change | Date | Evidence |
|--------|------|----------|
| Monolithic `app.py` (~930 lines incl. `page_generate_invoice` at line 929) on `E:\GAGAN FINANCE DESK V1\` | through 11 Jul 2026 ✅ | `logs/errors.log` 07-11 traceback |
| Modular `pages/` package + shared modules (config, styles, ui_components, invoice, excel_utils, pdf_extract, database, helpers) | 16 Jul 2026 ✅ | file timestamps |
| Routing model: `render_menu()` top-nav buttons; page functions dispatched from `app.py` | 16 Jul 2026 ✅ | `ui_components.py`, `app.py` |
| CSS theming engine (`styles.py`) with dark/light + mobile breakpoints; Streamlit chrome hidden via CSS | 16 Jul 2026 ✅ | `styles.py`, `.streamlit/config.toml` |
| Session-state-based navigation (no multipage plugin); theme toggle triggers rerun | 16 Jul 2026 ✅ | `ui_components.py` |

---

## Synchronization

| Change | Date | Evidence |
|--------|------|----------|
| `database.get_connection()` dual-backend: `DATABASE_URL` present → PostgreSQL, absent → SQLite (WAL) | 18 Jul 2026 ✅ | commit `e42b579` |
| Dual-backend helper layer (`_fix_sql`, `_fetchall`, `_fetchone`, `_execute`, `_executescript`, `_lastrowid`) | 18 Jul 2026 ✅ | present in current `database.py`; the "CRITICAL SHARED INFRASTRUCTURE" guards |
| Offline→Online push (`sync_offline_to_online.py`) dedup by (invoice_no, serial_no) | 18–31 Jul 2026 ✅ | file timestamps |
| Online→Offline pull (`sync_online_to_offline.py`) | 18–31 Jul 2026 ✅ | file timestamps |
| Settings `_sync_now()` per-record INSERT into Neon | 18–31 Jul 2026 ✅ | `pages/settings.py` |
| Neon dataset replaced from SQLite (canonical source of truth) | 3 Aug 2026 ✅ | `neon_records_replace.py` |
| Sync scripts + Settings sync normalize `bid_date` | 5 Aug 2026 ✅ | tested via mocked psycopg2 (3 formats × 3 paths all → DD-MM-YYYY) |

---

## Query Optimization Timeline (SQL)

1. **Jun 2026 (prototype):** all reads via `SELECT *` from SQLite (`load_all_records` everywhere); months derived in Python.
2. **16 Jul 2026:** indexes added (`month`, `bid_date`, etc.); `search_records` pagination + `substr(bid_date,7,4)` sort hack for DD-MM-YYYY.
3. **18 Jul 2026:** PostgreSQL-safe parameter style added (placeholder conversion `?`→`%s`).
4. **1 Aug 2026:** month ordering fixes (`month_sort_key`), invoice-YYMM fixes, EMI query base.
5. **5 Aug 2026 (today):** replacement of full-table reads with:
   - `WHERE bid_date IN (3 formats)` aggregate (quick stats)
   - `COUNT(*)` (settings + search caption)
   - 2 scalar MAX queries (suggest_next_invoice)
   - `LIMIT 10` (recent invoices)
   - `WHERE month=? AND sr_no=? LIMIT 1` (move)
   - 8-col SQL pre-filter (EMI candidates)
   - `get_db_fingerprint()` (Excel cache invalidation)
   - `include_monthly_counts=False` (dashboard month views)

---

## Migrations & Data Ops

| Migration | Date | Evidence |
|-----------|------|----------|
| `import_excel.py` (59-record target, Excel→DB) | 23 Jun 2026 ✅ | docstring + file timestamp |
| `migrate_dates()` (ISO→DD-MM-YYYY) | by 16 Jul 2026 🟡 | module import hook |
| Recovery import script (1405 rows, 29 sheets, header mapping) | 3 Aug 2026 ✅ | `recovery_import.py`, dry-run report |
| April 2024 deletion (24 rows, transaction-guarded, integrity check) | 3 Aug 2026 ✅ | `delete_verified_apr2024.py` |
| Neon replace from SQLite (backup .sql/.json first) | 3 Aug 2026 ✅ | `neon_records_replace.py` |
| `migrate_date_format.py` (11 DD/MM/YYYY → DD-MM-YYYY, auto-backup, idempotent) | 5 Aug 2026 ✅ | script + backup artifact |

---

## Current Architecture Snapshot

### Frontend
- **Framework:** Streamlit (desktop app run via `python -m streamlit run app.py`; `.bat` launcher supplied).
- **UI architecture:** Top-button navigation (`ui_components.render_menu`) — no sidebar; Streamlit default chrome hidden via CSS; dark/light themes via `styles.py`; mobile-responsive breakpoints (768px / 480px); state via `st.session_state` (no multipage plugin).

### Backend
Structure (measured; 19 active app modules, 3,328 LOC):
```
app.py                        (75 LOC)  — entry: config → CSS → logging → page dispatch
├── config.py                 (55)      — paths, headers, settings.json read/write
├── styles.py                 (201)     — CSS theme engine (dark/light + mobile)
├── helpers.py                (175)     — dates/amounts, validation, activity log
├── database.py               (708)     — dual-backend data layer (24 SQL fns / 35 defs)
├── invoice.py                (181)     — invoice generation, numbering, backups
├── pdf_extract.py            (94)      — Bajaj DO extraction
├── excel_utils.py            (98)      — multi-sheet Excel export/update
├── ui_components.py          (181)     — nav menu, form, month cards, manual entry
├── migrate_date_format.py    (125)     — one-time date canonicalization
├── import_excel.py           (151)     — CLI Excel → DB importer
├── sync_offline_to_online.py (146)     — SQLite → Neon push
├── sync_online_to_offline.py (137)     — Neon → SQLite pull
└── pages/
    ├── generate_invoice.py   (148)     — PDF upload → auto-fill → generate+save
    ├── records.py            (476)     — search/filter/sort/paginate/bulk/edit/regenerate/move
    ├── dashboard.py          (76)      — metrics + charts
    ├── emi_notification.py   (127)     — EMI end-month tables + navigation
    └── settings.py           (174)     — config, tax, backup/restore, sync, status
```
- **Page structure:** 5 page functions (`page_*`) dispatched by `app.py` via `render_menu()` state key.
- **Business logic:** invoice numbering (YYMM+counter with future-code preservation), EMI scheduling (scheme `12/4`, first-EMI month rule, last-EMI month), GST split (taxable/CGST/SGST), SR-number-per-month with swap/renumber.

### Database
- **SQLite (desktop):** `data/finance.db`, WAL mode, foreign keys ON; tables `records` (22 cols) + `sqlite_sequence`; 6 indexes on `records`; `migrate_dates()` + `init_db()` run at import.
- **PostgreSQL/Neon (cloud):** selected automatically when `DATABASE_URL` is set; same SQL via `_fix_sql` placeholder conversion; both backends are CRITICAL SHARED INFRASTRUCTURE and must never be removed.
- **Synchronization:** offline→online and online→offline scripts dedup by `(invoice_no, serial_no)`; Settings "Sync Now" pushes local records to Neon; `bid_date` normalized on all sync write paths.

### Invoice Engine
- `invoice.py` — renders `template.docx` via `docxtpl`, computes taxable/CGST/SGST, amount-in-words (`num2words`); Windows: DOCX→PDF (`docx2pdf`)→PNG (`pdf2image`) preview; Linux/cloud: DOCX download only. Per-invoice JSON backup to `Backups/`.

### PDF Processing
- `pdf_extract.py` — regex extraction of name, product, price, mobile, address, DO ID, date, EMI, DI, scheme from Bajaj DO PDFs; missing-field warnings flag low-confidence results.

### Excel Engine
- `excel_utils.py` — one sheet per month (`MONTH_YYYY`, newest first), 18 business columns, styled header, alternating rows with hoisted reusable style objects (Option E), auto column widths (12–45), regenerated on every save (`update_excel_file`) and served as cached download when current.

### Dashboard
- `pages/dashboard.py` — totals (records/DP/DI), monthly GROUP BY chart (ALL_MONTHS), daily chart per selected month (dates normalized for display); redundant monthly scan skipped when a month is chosen.
- Bar charts pass `sort=False` so the already chronologically ordered data (`month_sort_key` / sorted day list) is not re-sorted lexicographically by Vega-Lite's default ascending sort (fix 1 Sep 2026).

### Search
- `pages/records.py` + `database.search_records` — 6-column OR-LIKE search (name/phone/invoice/BID/product/serial), month filter, date-range (via `substr(bid_date,7,4)` ISO-compose), 6 sort keys, pagination (20–200), search-history chips, COUNT-only caption.

### Reporting
- No dedicated report generator; reporting is served by Excel export (full workbook download) and Dashboard (aggregate metrics/charts).

### Configuration
- `config/settings.json` persisted; `config.py` supplies constants + defaults (template path, poppler path, GST/CGST/SGST, invoice prefix, theme).

### Logging
- `logs/errors.log` (RotatingFileHandler, 5 MB × 3) for exceptions; `logs/activity.log` (append) for invoice/manual-entry/settings/sync activity.

### Backups
- Per-invoice JSON in `Backups/`; DB backups via Settings download/restore; timestamped WAL-consistent DB backups before migrations/recoveries (`data/finance_backup_*.db`); recovery archive scripts retained.

---

## Project Statistics (measured 2026-08-05)

| Metric | Value |
|--------|-------|
| Active Python files (app code, excl. `Archive/`, `__pycache__`, `.git`) | **19** (13 root modules + 6 `pages/` modules) |
| Lines of active application code | **≈3,328** (root 2,327 + pages 1,001) |
| Archived recovery/verification scripts (`Archive/Recovery_2026-08-03/`) | **12 files, ≈2,598 LOC** |
| Pages (routable page functions) | **5** (Generate Invoice, Records, Dashboard, EMI Notification, Settings) |
| SQL/data-access functions in `database.py` | **24** (of 35 `def` functions) |
| Database tables | **2** (`records`, `sqlite_sequence`) |
| Columns on `records` | **22** |
| Database indexes on `records` | **6** (`invoice`, `serial`, `name`, `phone`, `month`, `bid_date`) |
| In-repo migration scripts | **2** (`import_excel.py`, `migrate_date_format.py`) + in-code `migrate_dates()` |
| Synchronization scripts | **2** (`sync_offline_to_online.py`, `sync_online_to_offline.py`) + Settings `_sync_now()` UI path |
| Records in live DB | **1,392** (spanning Apr 2024 – Aug 2026, 28 months) |
| Invoice backups (`Backups/`) | ~130+ (earliest 2026-06-22) |

**Major dependencies (from `requirements.txt`, verified):**
`streamlit>=1.28.0`, `psycopg2-binary>=2.9.0`, `pypdf>=3.0.0`, `docxtpl>=0.11.0`, `pdf2image>=1.16.0`, `pywin32>=306` (win32), `docx2pdf>=0.1.8` (win32), `fpdf2>=2.8.0`, `num2words>=0.5.10`, `openpyxl>=3.1.0`. Stdlib in use: `sqlite3`, `json`, `re`, `logging`, `os`, `datetime`.

---

## Major Engineering Milestones

| Milestone | Problem solved | Technical solution | Impact | Confidence |
|-----------|----------------|--------------------|--------|------------|
| Monolith → modular pages/ | Single ~930-line `app.py` (E:\ era, line 929 in `page_generate_invoice` as of 11 Jul 2026) was unmaintainable | Split into `pages/` package + shared modules (`config`, `styles`, `ui_components`, `invoice`, `excel_utils`, `pdf_extract`, `database`, `helpers`) with `render_menu()` dispatch | Each page/feature evolved independently; themes & mobile CSS added the same day | ✅ Verified (timestamps 16 Jul 2026) |
| Dual-backend (SQLite + PostgreSQL) | Desktop offline vs Render/cloud needed one codebase | `DATABASE_URL`-driven `get_connection()`; `_fix_sql` placeholder conversion; dual-backend fetch/commit helpers | Same code runs locally (SQLite) and on Neon (PostgreSQL) | ✅ Verified (commit `e42b579`, 18 Jul) |
| Dual-backend restoration | A cloud-era refactor regressed the PostgreSQL path ("This regression happened once already") | Restored both branches; added permanent "CRITICAL SHARED INFRASTRUCTURE" guard comments | The invariant can never silently regress again | ✅ Verified (commits `0b40a3c`, `9a9ef57`, 1 Aug) |
| Performance Phase 1 | Full-table loads on Generate-Invoice quick-stats & Settings count | Targeted SQL aggregates: `COUNT/SUM WHERE bid_date IN (3 formats)`, `SELECT COUNT(*)` | 6.7× / 3.8× faster; identical outputs | ✅ Verified (5 Aug, measured) |
| Performance Phase 2 | 1–3 `SELECT *` loads per rerun on hot pages | Scalar invoice queries, LIMIT-10 recent, move point-query, cached Excel artifact, 8-col EMI pre-filter, COUNT-only captions, dashboard group-by dedup | suggest 4.5× @1.4K / 14× @100K; recent 8.8×; move 8.5×; all full-table reads removed from hot render paths | ✅ Verified (5 Aug, measured + equivalence-tested) |
| Date normalization | Mixed `bid_date` formats (PDF wrote `DD/MM/YYYY`; 11 rows contaminated) | `_normalize_date()` at every write choke point (`add_record`, `update_record`, 2 sync scripts, Settings sync) + `migrate_date_format.py` one-time conversion | All 1,392 rows canonical `DD-MM-YYYY`; none can be reintroduced | ✅ Verified (5 Aug, migration + smoke tested) |
| Excel engine (Option E) | `_apply_alternating_rows` created ~1.8M per-cell style objects @100K (84–92% of export time) | Hoisted reusable `PatternFill`/`Alignment` objects | Export 2.1×; Alignment objects 25,562 → 506; workbook content byte-identical (only save-time metadata differs) | ✅ Verified (5 Aug, cell-for-cell comparison + benchmark) |

---

## Current Performance Snapshot

**Startup behavior (verified, not re-run):**
- Module-level code (imports, `migrate_dates()`, `init_db()`) runs **once per server process**, not per rerun — confirmed by instrumented AppTest across 8 reruns (module block fired 1×; page functions 11×).
- Cold-start cost of `migrate_dates()` at 1,392 rows ≈ 5 ms; grows to seconds only at 100K+ rows (one-time per launch).

**Hot page performance (measured 5 Aug 2026, 1,392-row DB):**
| Path | Time |
|------|------|
| Generate-Invoice quick stats | 5.53 ms |
| Invoice number suggestion | 7.54 ms |
| Settings record count | 5.27 ms |
| Recent invoices | 3.19 ms |
| Move target lookup | 3.56 ms |
| EMI page candidate fetch | ~56 ms |
| All indexed page queries | < 5 ms |

**Database query optimizations:** full-table `SELECT *` eliminated from all 5 hot page paths; all remaining reads are indexed (month, bid_date, PK) or scalar with `LIMIT`.

**Excel export performance (measured 5 Aug 2026):**
- Export build after Option E: **697 ms** @1,392 rows (was 1,491 ms).
- `_apply_alternating_rows` after Option E: **260 ms** (was 708 ms).
- Save: ~1.0–1.5 s @1,392 rows (zip serialize — inherent to full rebuild).
- At 100K rows the full rebuild is dominated by save (~25 s) and is the scaling ceiling for `update_excel_file()`.

**Remaining bottlenecks (all verified in earlier investigations):**
1. `update_excel_file()` full workbook rebuild on every save — O(N) save (~25.5 s @100K).
2. Settings `_sync_now()` opens one Neon connection + transaction per record.
3. `check_invoice_exists` / `check_serial_exists` — `LOWER()` defeats the index (full covering-index scan; ~190 ms @1M).
4. 6-column OR-LIKE search — fast when matches exist (LIMIT short-circuit), worst case (no matches) full scan.
5. EMI computation — still O(candidates) in Python.
6. Dashboard ALL-MONTHS monthly GROUP BY — ~90 ms @1M (only when the month chart is shown).
7. `migrate_dates()` — cold-start only, but ~1,392-row scan each process launch.

---

## Known Future Improvements

Identified by prior investigations only (no invented features):

1. **Excel incremental update engine (Phase 3B candidate)** — update only the affected month sheet (or append changed rows) instead of full rebuild; save remains the ceiling, so also consider deferring full rebuilds. (From Phase 3A investigation; Option A/B/C/D evaluated, not implemented.)
2. **Search optimization (FTS5)** — SQLite FTS5 verified available; replace 6-column OR-LIKE with tokenized search (sub-ms @1M). (From Phase 2 report; not implemented.)
3. **`LOWER()` index optimization** — NOCASE/expression index for `invoice_no`/`serial_no` checks (or case-consistent writes). (From Phase 2 report; not implemented.)
4. **Batch Neon synchronization** - ✅ Implemented in Phase 3 (20 Aug 2026): `pages/settings.py` `_sync_now()` uses one connection + `executemany` in one transaction.
5. **Precomputed monthly summary table** — maintain counts/sums per month/day on write to keep dashboard/statistics O(1) at 500K+. (From Phase 2 roadmap; not implemented.)
6. **Streamlit read caching** - ✅ Implemented in Phase 3 (20 Aug 2026): `st.cache_data(ttl=30)` on read-only queries with centralized `invalidate_cache()` after every write (dead `_cache` globals removed).
7. **Cap `save_backup()` growth** — keep N most-recent backups per invoice (or JSONL archive). (From Phase 2 roadmap; not implemented.)
8. **Canonical ISO `YYYY-MM-DD` storage for `bid_date`** — recommended in the date investigation (natural sorting/indexing, removes `substr()` hacks); requires the documented `migrate_dates()` extension. (From date-format investigation; not implemented — current canonical storage is `DD-MM-YYYY`.)
9. **Connection reuse (PostgreSQL)** - ✅ Implemented in Phase 3 (20 Aug 2026): lazy `ThreadedConnectionPool` for Neon instead of per-operation `psycopg2.connect()`. SQLite intentionally keeps normal connections (open is ~0.4 ms locally).

---

## Key Technical Invariants (current)

- **Dual-backend never removed** (SQLite desktop + PostgreSQL/Neon) — explicitly protected by comments after a real regression.
- **`bid_date` storage = DD-MM-YYYY** (write-path normalized; display unchanged).
- **Hot page paths avoid full-table loads** (Phase 1/2 verified identical outputs).
- **Workbook appearance stable** across export refactors (validated cell-for-cell).

---

## Performance Phase 3 - connection pooling & startup optimization (2026-08-20)

**Architecture (PostgreSQL/Neon):**
- Lazy `psycopg2.pool.ThreadedConnectionPool(minconn=1, maxconn=10)` created on the first database touch (never at import, never per rerun); registered with `st.cache_resource` inside the Streamlit runtime, process-wide singleton for CLI scripts.
- `_PooledConnection` adapter preserves the codebase-wide `conn = get_connection(); ... conn.close()` pattern - `close()` hands the connection back to the pool; psycopg2's `putconn()` rolls back open/aborted transactions and discards connections whose server side is gone (Neon idle timeouts); pool exhaustion falls back to a direct connection.
- SQLite backend unchanged (normal `sqlite3` connections + WAL; open ~0.4 ms locally).

**Startup (both backends):**
- `init_db()` now runs lazily once on first `get_connection()` (recursion-safe; retries on failure); `migrate_dates()` is opt-in only. Zero schema work / zero records scans at import.
- `FINANCE_DB_PATH` env override for isolated tests.

**Caching:**
- `st.cache_data(ttl=30)` on `get_available_months`, `get_today_stats`, `get_dashboard_stats`, `get_recent_invoices`, `load_emi_candidates`, `get_monthly_card_stats` - resolved lazily at first call (CLI scripts/tests never import streamlit).
- `invalidate_cache()` (centralized) clears `st.cache_data` after every insert/update/delete/swap/restore/sync - the next read is always fresh.
- Records page Excel download cached via `st.cache_data(ttl=3600)` keyed by `get_db_fingerprint()` (SQLite: file mtimes; PostgreSQL: `(COUNT(*), MAX(id), MAX(updated_at))`).

**Round-trip reductions (Neon):**
- One grouped `get_monthly_card_stats()` replaces up to 4 per-month queries on the Generate Invoice cards.
- Records page reuses the `total` returned by `search_records()` (duplicate `count_search_records()` removed).
- Settings Sync Now batched to one connection + `executemany`.
- `suggest_next_invoice()` no longer runs on every rerun (eager-default fix + session-state guard in the manual-entry form).
- Records page `get_recent_invoices(10)` deferred into the Edit/Regenerate branches - no query/Neon round trip on plain Records reruns (22 Aug 2026).

**Measured locally (SQLite, 1,425 rows):**
- `database` import: ~95-231 ms -> ~53 ms (zero connections at import).
- `ui_components` import chain: ~2,032 ms -> ~7 ms; `pages.dashboard` import ~1 ms with no heavy modules.
- AppTest: all 5 pages pass; Generate Invoice second render ~50 ms.
- Neon latency itself was not measurable from this machine; the pool/grouped-query design removes per-operation network connects.

**Case-insensitive search (20 Aug 2026):** `search_records()` / `count_search_records()` use `LOWER(column) LIKE LOWER(?)` so the text search matches regardless of case on both SQLite and PostgreSQL (PG `LIKE` was previously case-sensitive; SQLite `LIKE` is ASCII-case-insensitive by default). The leading-wildcard `%q%` pattern already forces a full scan, so the added `LOWER()` has negligible cost and no index can help it anyway.

**Not implemented (per project decision):**
- `idx_records_month_srno ON records(month, sr_no)` is **documented only** - NOT added to `init_db()` and NOT run against production. If desired, run manually on both backends:
  ```sql
  CREATE INDEX IF NOT EXISTS idx_records_month_srno ON records(month, sr_no);
  ```
  (Valid SQLite + PostgreSQL; helpful for month-scoped `MAX(sr_no)`, renumbering, and move lookups.)

---

## Unknown / Estimated Items
- ❓ Exact SQL used by the very first prototype (June) — no source retained.
- 🟡 `migrate_dates()` exact introduction date (present by 16 Jul).
- 🟡 Whether `idx_*` indexes existed before the 16 Jul refactor.
- ❓ Full content of the `E:\GAGAN FINANCE DESK V1\app.py` monolith.

---

## Engineering Principles

Long-term principles observed in this project's documented history:

- **SQLite remains the offline database.** Desktop use runs on `data/finance.db` (WAL mode); nothing about the local path changes based on deployment.
- **PostgreSQL/Neon remains the cloud backend.** When `DATABASE_URL` is set (Render/cloud), the app uses PostgreSQL via `psycopg2`.
- **Both backends must always coexist.** A past refactor regressed the PostgreSQL path once; permanent "CRITICAL SHARED INFRASTRUCTURE" guards in `database.py` enforce that neither backend is ever removed — this is the project's #1 architectural rule.
- **Performance optimizations must preserve identical output.** Every Phase 1/2/Excel-engine change was validated old-vs-new (identical values, identical workbook content, identical behavior), not just faster.
- **Data integrity has priority over speed.** Examples: `bid_date` canonicalization on every write path, transaction-guarded deletes (April 2024 cleanup), WAL-consistent backups before migrations, and idempotent, backup-first migration scripts.
- **Backward compatibility is preserved whenever practical.** Existing storage format (`DD-MM-YYYY`), existing templates, existing data files, and existing working pages remain supported; optimizations replaced internals, not interfaces.

*End of TECHNICAL_HISTORY.md*

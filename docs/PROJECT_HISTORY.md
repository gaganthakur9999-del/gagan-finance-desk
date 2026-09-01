# Gagan Finance Desk — Project Development History

**Reconstructed:** 2026-08-05. This is a **development history**, not a changelog or version history. No version numbers are invented.

**Evidence sources used:** Git history (9 commits, `git log`), file-system creation/modification timestamps, `logs/errors.log` (June 13 – today), `logs/activity.log`, `Backups/*.json` invoice backups (June 22 – today), `data/finance_backup_*.db` snapshots, `Archive/Recovery_2026-08-03/*` recovery scripts & dry-run report, SQLite database contents (1,392 records spanning April 2024 – Aug 2026), and today's verified performance/date-normalization work.

**Confidence legend:** ✅ **Verified** (git commit, file timestamp, or log entry) · 🟡 **Estimated** (strong inference from surrounding evidence) · ❓ **Unknown** (cannot be proven from available sources).

---

## Chronological Timeline

```
Pre-history (business data only) — Excel workbook "All_Records.xlsx"
        ↓
Mid-June 2026 — First prototype (desktop, Streamlit, monolithic app.py on E:\ drive)
        ↓
Late June 2026 — Invoice generation from template.docx + JSON backup on every save
        ↓
Early July 2026 — PDF extraction, Excel export, monthly sheets; monolith at ~930 lines
        ↓
16 July 2026 — Modular refactor into pages/ + shared modules
        ↓
18 July 2026 — Git initialized; first commit "Initial cloud version with PostgreSQL support"
        ↓
Late July 2026 — Cross-platform PDF (fpdf2/mammoth+weasyprint), sync scripts, Render prep
        ↓
1 Aug 2026 — Cloud/deploy attempt regressed dual-backend; restored; EMI Notification tab added
        ↓
3 Aug 2026 — Large Excel recovery import (1405 rows, 29 sheets), April 2024 cleanup, Neon replace
        ↓
5 Aug 2026 — Performance Phase 1 → Date normalization → Performance Phase 2 → Excel engine investigation
```

---

## Milestones

### 1. Pre-history: Business records in Excel only
- **Approx. date:** Before June 2026 (data starts April 2024) 🟡
- **Confidence:** 🟡 High (month-sheet distribution + dry-run report)
- **Evidence:** `Archive/Recovery_2026-08-03/dry_run_report_20260803_115906.txt` — workbook `C:\Users\ASUS\Desktop\All_Records.xlsx` with **29 month-sheets April 2024 → August 2026, 1,405 data rows**, 18 columns. The app's month-sheet Excel export mirrors this exact structure — the software was built around the user's existing monthly ledger.
- **Files involved:** (external) `All_Records.xlsx`; later `Excel/ALL_RECORDS.xlsx`
- **Why important:** Explains the entire product shape: one sheet per month, SR-number-per-month, `YYMMNNN` invoice IDs, DP/DI/Xcell/BID/scheme tracking.

### 2. First desktop prototype (mid-June 2026)
- **Approx. date:** 12–13 June 2026 ✅ (log file creation + first entries)
- **Confidence:** ✅
- **Evidence:** `logs/errors.log` created **2026-06-12 18:06**; first entries **2026-06-13 10:31** are Streamlit asyncio `ConnectionResetError` — the app was already a running **Streamlit** desktop app.
- **Files involved:** `logs/errors.log`
- **Description:** A Streamlit-based desktop finance tool on Windows. No git yet; the codebase was not version-controlled.
- **Why important:** Earliest confirmed existence of the project.

### 3. Invoice generation + template + JSON backups (late June 2026)
- **Approx. date:** 22–23 June 2026 ✅
- **Confidence:** ✅
- **Evidence:** `Backups/260677.json` — **2026-06-22** (invoice 260677 = June 2026, counter 77). `template.docx` modified **2026-06-18** / copied into project 06-23. `data/finance.db` created **06-23**, `Excel/ALL_RECORDS.xlsx` created **06-23**, `GAGAN FINANCE DESK.bat` created **06-23**. `logs/errors.log` line **2026-06-17 20:07 "Excel file open while saving"** — Excel export already active.
- **Files involved:** `Backups/*.json`, `template.docx`, `data/finance.db`, `Excel/ALL_RECORDS.xlsx`, `GAGAN FINANCE DESK.bat`
- **Description:** Invoice generation from `template.docx`, automatic `Backups/{invoice_no}.json` on every save, SQLite persistence, and full month-sheet Excel export on every save. The `.bat` launcher was added for double-click startup.
- **Why important:** The core workflow (upload/enter → invoice → backup → Excel syncing) was in place within the first two weeks.

### 4. Excel import utility (23 June 2026)
- **Approx. date:** 23 June 2026 ✅
- **Confidence:** ✅ (file timestamp)
- **Evidence:** `import_excel.py` created 06-23 20:05; its docstring says "Import your **59 records** from ALL_RECORDS.xlsx" — an early ad-hoc migration target.
- **Files involved:** `import_excel.py`
- **Description:** One-off importer mapping Excel columns → DB fields, dedup by invoice/serial.
- **Why important:** Shows the data existed in Excel before/alongside the DB; the app supported bulk migration early.

### 5. PDF extraction from Bajaj DO (mid-July 2026)
- **Approx. date:** 16 July 2026 ✅
- **Confidence:** ✅ (file timestamp)
- **Evidence:** `pdf_extract.py` created **2026-07-16 17:06**; also `app.py` created 07-16 14:09, `config.py`, `ui_components.py`, `excel_utils.py`, `invoice.py`, `pages/*` all 07-16.
- **Files involved:** `pdf_extract.py`
- **Description:** Regex-based extraction of name, product, price, mobile, address, DO ID, date, EMI, DI, scheme from Bajaj DO PDFs.
- **Why important:** Automated the manual data-entry workflow — the app's primary interaction.

### 6. Monolithic app grows (~930 lines) — evidence on E:\ drive
- **Approx. date:** through 11 July 2026 ✅
- **Confidence:** ✅ (log evidence)
- **Evidence:** `logs/errors.log` **2026-07-11** — traceback at `E:\GAGAN FINANCE DESK V1\app.py`, line **929**, in `page_generate_invoice`; DB insert failed "table records has no column named remarks" (before the `remarks` column migration). This proves the pre-refactor `app.py` was a single large file located on the **E:** drive, and the `remarks` column is a later schema addition.
- **Files involved:** (old) `E:\GAGAN FINANCE DESK V1\app.py`
- **Description:** Everything in one `app.py` (page function at line 929); `remarks` column not yet present.
- **Why important:** Documents the architecture before the modular refactor and dates the `remarks` feature.

### 7. Modular refactor into pages/ + shared modules (16 July 2026)
- **Approx. date:** 16 July 2026 ✅
- **Confidence:** ✅ (file timestamps)
- **Evidence:** On 07-16: `pages/__init__.py` (17:03), `config.py` (17:04), `.streamlit/config.toml` (17:21), `styles.py` (17:04), `invoice.py` (17:06), `excel_utils.py` (17:06), `pdf_extract.py` (17:06), `pages/settings.py` (17:08), `pages/dashboard.py` (17:07), `ui_components.py` (17:06), `app.py` (14:09→18:32 update).
- **Files involved:** all current `pages/*.py`, `ui_components.py`, `excel_utils.py`, `styles.py`, `.streamlit/config.toml`
- **Description:** Split the monolith into a routing `app.py`, a `pages/` package (Generate Invoice, Records, Dashboard, EMI Notification, Settings), and shared modules (config, styles, ui_components, invoice, excel_utils, pdf_extract). CSS themes (dark/light) and mobile-responsive styling introduced (`styles.py`).
- **Why important:** Made the codebase maintainable and enabled independent page evolution.

### 8. Git initialized; first commit — "Initial cloud version with PostgreSQL support" (18 July 2026)
- **Approx. date:** 18 July 2026 ✅ (commit `e42b579`)
- **Confidence:** ✅
- **Evidence:** `git log` — `e42b579 | 2026-07-18 14:00 | Initial cloud version with PostgreSQL support`. Also `helpers.py` (07-18 15:58), `.gitignore` (07-18 12:54), sync `.bat` files (07-18 19:13).
- **Files involved:** repo init; `helpers.py`, `.gitignore`, `database.py` PostgreSQL branch
- **Description:** First version tracking; opened the door to Render/cloud with a PostgreSQL/Neon path while keeping the SQLite fallback. Added helpers (amount/date parsing, validation), plus `sync_offline_to_online.py` / `sync_online_to_offline.py` started (files 07-18 19:09/19:10).
- **Why important:** Start of reproducible version history and the offline↔cloud architecture.

### 9. Cross-platform PDF/HTML invoice work (18 July 2026)
- **Approx. date:** 18 July 2026 ✅ (commits `297485e`, `bd798a6`, `957bb58`, `8b8d00c`, `6e67c8d`)
- **Confidence:** ✅
- **Evidence:** Commits: "remove Windows-only dependencies for Linux/Render deployment", "skip poppler check on Linux (Render cloud)", "Add HTML invoice preview – works on all devices (PC, phone, tablet)", "Add PDF generation using fpdf2 – works on all platforms (phone, PC, cloud)", "Fix: proper DOCX-to-PDF conversion using mammoth+weasyprint from template". `test.pdf` created 07-18 15:57.
- **Files involved:** `invoice.py`, `requirements.txt`, `helpers.py`, `test.pdf`
- **Description:** Made invoice PDF/HTML generation work on Windows, Linux/Render, and phones — reduced Windows-only `docx2pdf`/poppler reliance.
- **Why important:** Enabled mobile/cloud usage; the "works on all devices" theme.

### 10. Cloud/prep for Render + sync scripts finalized (18–31 July 2026)
- **Approx. date:** 18–31 July 2026 ✅
- **Confidence:** ✅
- **Evidence:** Sync `.bat` files 07-18; `database.py` dual-backend `DATABASE_URL` logic; `requirements.txt` (created 06-20, updated 07-31 — psycopg2-binary), `.env.example`/`.env` (07-31 15:53); `data/finance_backup_before_import.db` (07-31 17:30); continued Backups through July.
- **Files involved:** `database.py`, `sync_offline_to_online.py`, `sync_online_to_offline.py`, `Sync *.bat`, `.env`, `requirements.txt`
- **Description:** Two-way sync scripts (SQLite↔Neon) and Settings-page Sync Now button; environment-based DB selection.
- **Why important:** Established the desktop-primary/cloud-dr backup architecture.

### 11. Cloud deploy regression & dual-backend restore (1 Aug 2026)
- **Approx. date:** 1 Aug 2026 ✅
- **Confidence:** ✅ (commits)
- **Evidence:** Commit `0b40a3c | 2026-08-01 13:25 | Restore dual-backend support (PostgreSQL DATABASE_URL + SQLite fallback)` and `9a9ef57 | 2026-08-01 15:15 | Add permanent dual-backend protection comments`. `database.py` now carries the warning "This regression happened once already" and "NEITHER BACKEND MAY EVER BE REMOVED DURING REFACTORING".
- **Description:** During cloud work the PostgreSQL path was temporarily lost; restored and locked with permanent comments so it can never regress again.
- **Why important:** A documented past failure — the dual-backend invariant is the project's #1 architectural rule.

### 12. EMI Notification tab + improvements (1 Aug 2026)
- **Approx. date:** 1 Aug 2026 ✅ (commit `839a817`)
- **Confidence:** ✅
- **Evidence:** Commit `839a817 | 2026-08-01 12:32 | Add EMI Notification tab, fix month ordering & invoice numbering, improve PDF extraction & cloud sync`. `pages/emi_notification.py` timestamps 07-31 18:25 (created) / 08-01 (commit).
- **Files involved:** `pages/emi_notification.py`, `invoice.py`, `pdf_extract.py`, `database.py`
- **Description:** Added EMI end-date tracking (scheme `12/4`, first-EMI month rules, last-EMI month map with prev/next navigation); fixed month (YYYYMM) ordering and invoice numbering (YYMM+counter) and DO-extraction robustness.
- **Why important:** Business-critical "which customers' EMIs are ending" view; second big feature after invoices.

### 13. Data recovery day (3 Aug 2026) — import, April 2024 cleanup, Neon replace
- **Approx. date:** 3 Aug 2026 ✅
- **Confidence:** ✅ (backup names + scripts + dry-run report)
- **Evidence:** Many artifacts on 08-03: `data/finance_backup_before_recovery_import_20260803_124422.db`, `dry_run_report_20260803_115906.txt` (1,405 rows, 29 sheets), `delete_verified_apr2024.py` (24 verified April-2024 rows deleted, total → 1381), `data/finance_backup_before_apr2024_delete_20260803_125840.db`, `neon_records_replace.py` + `finance_backup_neon_records_20260803_154335.sql/.json` (replaced Neon records from SQLite), `cleanup_report.txt`, `verify_neon_vs_sqlite.py`.
- **Files involved:** `Archive/Recovery_2026-08-03/*`, `data/finance_backup_*.db`
- **Description:** A deliberate data-reconciliation day: imported a fresh workbook into the SQLite DB, then removed 24 stray April-2024 rows (which had been misfiled as AUGUST_2026 with NULL bid_date by the import fallback rule), verified integrity, and replaced the entire Neon dataset with the canonical SQLite snapshot.
- **Why important:** The project's disciplined use of backups (online backup API), verification scripts, and transaction-guarded deletes; resulted in the current 1,392-record canonical dataset (1,405 − 24 + recoveries).

### 14. Post-recovery operation (4 Aug 2026)
- **Approx. date:** 4 Aug 2026 ✅
- **Confidence:** ✅
- **Evidence:** `config/settings.json` updated 08-04 10:21 (theme/gst/poppler persisted settings restored); Backups `260818–260822` throughout the day; `temp/Invoice_*.docx/pdf/png`.
- **Files involved:** `config/settings.json`, `Backups/`, `temp/`
- **Description:** Normal daily invoice generation resumed with the reconciled data.
- **Why important:** Confirms settings persistence (GST rates, template path, theme) existed by this point.

### 15. Performance Phase 1 (5 Aug 2026)
- **Date:** 5 Aug 2026 ✅ (performed today in this session)
- **Evidence:** Source changes in `database.py`, `pages/generate_invoice.py`, `pages/settings.py`; phase report.
- **Description:** Replaced full-table loads on the Generate-Invoice quick-stats and Settings record count with targeted SQL (`get_today_stats()`, `count_records()`); verified identical values.
- **Why important:** First performance milestone; confirmed the rerun model's function-level costs.

### 16. Date normalization (5 Aug 2026)
- **Date:** 5 Aug 2026 ✅
- **Evidence:** `add_record()`/`update_record()` normalize `bid_date` via `helpers._normalize_date()`; same applied in sync scripts and Settings sync; `migrate_date_format.py` one-time conversion of 11 `DD/MM/YYYY` rows; backup `data/finance_backup_dateformat_20260805_150034.db`; all 1,392 rows now `DD-MM-YYYY`.
- **Description:** Closed the accidental mixed-format hole (PDF extraction produced `DD/MM/YYYY`).
- **Why important:** Canonical date storage prevents sorting/filtering/indexing bugs.

### 17. Performance Phase 2 (5 Aug 2026)
- **Date:** 5 Aug 2026 ✅
- **Evidence:** `invoice.suggest_next_invoice()` → 2 scalar SQL queries; Records recent-invoices LIMIT 10; move handler targeted query; Excel download serves cached artifact; EMI 8-column SQL pre-filter; dashboard group-by dedup; `count_search_records`. Verified: suggest 4.5×@1.4K / 14×@100K, recent 8.8×, move 8.5×; all outputs identical.
- **Why important:** Removed virtually all unnecessary full-table reads from the 5 hot page paths.

### 18. Excel engine optimization (5 Aug 2026)
- **Date:** 5 Aug 2026 ✅
- **Evidence:** Phase 3A investigation profiled `update_excel_file()` — alternating-row styling was 84–92% of export time (1.8M per-cell `Alignment` objects at 100K rows); Option E implemented in `excel_utils.py` (hoisted reusable style objects). Verified: Alignment objects 25,562 → 506; export 2.1× faster; workbook identical except `created`/`modified` metadata timestamps (save-time, inherent to any re-save).
- **Why important:** The last big per-save latency; makes Excel export O(rows-copy) instead of O(rows × style-objects).

### 19. Performance Phase 3 - Render/Neon connection pooling & startup optimization (20 Aug 2026)
- **Date:** 20 Aug 2026 ✅
- **Evidence:** `database.py` lazy `ThreadedConnectionPool` + `_PooledConnection`; lazy `init_db()`; opt-in `migrate_dates()`; `st.cache_data` read caching + centralized `invalidate_cache()`; `get_monthly_card_stats()` grouped query; PostgreSQL fingerprint for the Excel download cache; Records page reuses `search_records()` total; batched Settings sync; lazy page/module imports; eager-eval fixes for `generated_invoice_no` and the manual-entry suggestion. Verified: module import ~53 ms (was ~95-231 ms incl. import-time init/migrate); `ui_components` chain ~2,032 ms to ~7 ms; Dashboard ~1 ms with zero heavy imports; AppTest all 5 pages pass; SQLite functional suite passes; pool wrapper tests pass; PostgreSQL import verified (no connection at import).
- **Description:** Replaced per-operation `psycopg2.connect()` with a safe lazy pool, removed import-time DB work, cached read-only results with immediate write invalidation, collapsed the monthly cards into one grouped query, and stopped per-rerun Excel rebuilds on Neon.
- **Why important:** Removes the per-query network connection overhead on Render/Neon (the dominant cloud latency) and cuts desktop import/startup costs without changing UI, invoice, PDF, or Excel behavior.

### 20. Case-insensitive search (20 Aug 2026)
- **Date:** 20 Aug 2026 ✅
- **Evidence:** `search_records()` / `count_search_records()` in `database.py` now use `LOWER(column) LIKE LOWER(?)` for the six searchable fields; verified mixed-case matches (`jaswant` matching `JASWANT` / `Jaswant` / `jAsWaNt`) on SQLite and the same SQL/placeholder path on PostgreSQL.
- **Description:** PostgreSQL `LIKE` is case-sensitive while SQLite `LIKE` is ASCII-case-insensitive, so the same query behaved differently on the two backends. Lowering both sides of the comparison makes matching consistent everywhere without schema/index/UI changes.
- **Why important:** One search behaves identically on desktop and Neon; no performance or architecture impact.

### 21. Defer recent-invoice hint (22 Aug 2026)
- **Date:** 22 Aug 2026 ✅
- **Evidence:** `pages/records.py` moved `db.get_recent_invoices(10)` out of the unconditional Records-page rerun path into the two branches that display it (the Edit form and the Regenerate UI); verified by a mocked-call AppTest that plain Records renders (open/search/month/sort/page/page-size) make zero calls and Edit/Regenerate make exactly one each.
- **Description:** The recent-invoice hint is only rendered by the Edit and Regenerate panels, but was queried on every Records rerun - an unnecessary query (and, on Render, a Neon round trip) whenever the user merely views, searches, or paginates Records. The call now executes only when those panels are open.
- **Why important:** Removes one query/round trip from the most-visited page without changing any displayed data or behavior.

### 22. Dashboard ALL-MONTHS chart ordering fix (1 Sep 2026)
- **Date:** 1 Sep 2026 ✅
- **Evidence:** `pages/dashboard.py` passes `sort=False` to its three `st.bar_chart()` calls; regression tests in `tests/test_dashboard_chart_ordering.py` assert `month_sort_key` ordering, chronological `chart_data`, and that `sort=False` produces a `"sort": null` Vega-Lite encoding (data order) while the default produces no sort key (Vega-Lite's default ascending/lexicographic order).
- **Description:** The Records Overview chart built chronologically ordered `chart_data`, but `st.bar_chart()` defaults to `sort=True`, which lets Vega-Lite re-sort the categorical X-axis lexicographically - so month-name labels (APRIL_2025, AUGUST_2024, ...) appeared alphabetically instead of chronologically when ALL MONTHS was selected. Single-month labels (zero-padded DD-MM-YYYY) sort lexicographically in chronological order, which is why only ALL MONTHS was affected. Passing `sort=False` preserves the already-correct Python data order.
- **Why important:** Correct chronological X-axis ordering for the monthly overview chart; the fix is chart-layer/backend-agnostic (no SQL/schema change), so offline (SQLite) and online (PostgreSQL/Neon) behave identically.

---

## Categorized History

**Initial Development**
- Pre-history Excel ledger (Apr 2024–) 🟡
- First Streamlit prototype (12–13 Jun 2026) ✅
- Excel import utility (23 Jun 2026) ✅

**Invoice System**
- Template invoice + JSON backups (22–23 Jun 2026) ✅
- Cross-platform PDF/HTML invoice (18 Jul 2026) ✅
- Invoice numbering YYMM+counter fixes (1 Aug 2026) ✅
- `suggest_next_invoice` SQL optimization (5 Aug 2026) ✅

**PDF Processing**
- Bajaj DO extraction (16 Jul 2026) ✅
- Extraction improvements (1 Aug 2026) ✅

**Database**
- SQLite finance.db inception (23 Jun 2026) ✅
- `remarks` column added (after 11 Jul failure, before 16 Jul refactor) 🟡
- Cloud/PostgreSQL support (18 Jul 2026) ✅
- Dual-backend regression & restore (1 Aug 2026) ✅
- Recovery import & Neon replace (3 Aug 2026) ✅
- Date normalization (5 Aug 2026) ✅

**Dashboard**
- Dashboard page (16 Jul 2026) ✅
- Month-per-day charts, group-by dedup (5 Aug 2026) ✅

**Search**
- Records search + recent history (16 Jul 2026) ✅
- Recent-invoices SQL LIMIT 10 (5 Aug 2026) ✅

**Excel**
- Month-sheet export on save (23 Jun 2026) ✅
- Export-as-download + cached artifact (5 Aug 2026) ✅
- Option E style-object reuse (5 Aug 2026) ✅

**Synchronization**
- Sync scripts + Settings Sync Now (18–31 Jul 2026) ✅
- Neon replace (3 Aug 2026) ✅

**UI/UX**
- Top-nav buttons, dark/light themes, mobile CSS (16 Jul 2026) ✅
- EMI Notification tab (1 Aug 2026) ✅

**Performance**
- Phase 1: quick-stats & count SQL (5 Aug 2026) ✅
- Phase 2: full-table-load elimination (5 Aug 2026) ✅
- Excel engine investigation + Option E (5 Aug 2026) ✅

**Bug Fixes**
- Excel-file-open-while-saving handling (earliest 17 Jun 2026) ✅
- "table records has no column named remarks" → schema fix (≤16 Jul 2026) ✅
- Linux poppler/dependency fixes (18 Jul 2026) ✅
- Dual-backend restoration (1 Aug 2026) ✅
- April 2024 misfiled rows removed (3 Aug 2026) ✅
- Mixed date format normalization (5 Aug 2026) ✅

**Infrastructure**
- `.bat` launchers (23 Jun – 18 Jul 2026) ✅
- Git init (18 Jul 2026) ✅
- `.env`/`.env.example`/requirements cloud deps (31 Jul 2026) ✅
- Recovery archive discipline (3 Aug 2026) ✅

**Refactoring**
- Monolith → pages/ modular (16 Jul 2026) ✅
- `_apply_alternating_rows` style-object hoist (5 Aug 2026) ✅

---

## Evidence gaps / Unknowns
- ❓ Exact date the very first lines of code were written (before 12 Jun 2026 is unproven; June data depth suggests May/early-June work).
- ❓ The original monolithic `app.py` on the `E:` drive is not in this repo (only referenced in logs) — its full content and evolution are ❓.
- ❓ When exactly `records.remarks` was added (between 11 Jul error and 16 Jul refactor) — estimated 🟡.
- ❓ Whether `Excel/ALL_RECORDS.xlsx`'s first creation (23 Jun) came from the import tool running against "59 records" (the `import_excel.py` docstring) — plausible but unverified sequence 🟡.
- 🟡 Month-sheet data prior to the DB (Apr 2024–May 2026) existed only in Excel before the 3 Aug 2026 import; exact data-entry path before the app is unknown ❓.

---

## Historical Accuracy

This document was reconstructed from **verified evidence**, not from memory or assumption:

- **Git history** — `git log --all` (9 commits, 2026-07-18 → 2026-08-01), including commit dates, authors and messages.
- **Recovery artifacts** — `Archive/Recovery_2026-08-03/` scripts, the recovery dry-run report, and the 03-Aug backup databases.
- **Database inspection** — live SQLite/Neon comparisons, schema (`PRAGMA`), index and row-count audits.
- **Activity logs** — `logs/activity.log` (invoice generation, sync activity).
- **File timestamps** — creation and modification times for every project file (including `logs/errors.log`).
- **Backups** — `Backups/*.json` per-invoice backups and `data/finance_backup_*.db` snapshots.

Each milestone is labeled with a confidence level (✅ Verified / 🟡 Estimated / ❓ Unknown); anything not directly proven by the above is explicitly marked rather than assumed. No dates, versions, or events were invented.

*End of PROJECT_HISTORY.md*

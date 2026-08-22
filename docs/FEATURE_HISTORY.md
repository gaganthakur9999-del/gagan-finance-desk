# Gagan Finance Desk — Feature History

**Reconstructed:** 2026-08-05. Lists **every major feature** added, with its purpose, date (or estimate), current status, and related files. No invented version numbers or dates.

**Confidence legend:** ✅ **Verified** (git commit / file timestamp / log) · 🟡 **Estimated** · ❓ **Unknown**

---

## Core Ledger & Invoicing

| Feature | Purpose | Date | Status | Related files |
|---------|---------|------|--------|---------------|
| Month-based ledger (SR NO per month, `YYMMNNN` invoice IDs) | Mirror the user's pre-existing Excel ledger (Apr 2024–) | Before Jun 2026 🟡 | ✅ Active | `Excel/ALL_RECORDS.xlsx`, `database.py`, `excel_utils.py` |
| SQLite persistence (`finance.db`) | Store all records locally | 23 Jun 2026 ✅ | ✅ Active | `data/finance.db`, `database.py` |
| Linked DOCX invoice generation from `template.docx` | Produce a professional invoice document | 22–23 Jun 2026 ✅ | ✅ Active | `invoice.py`, `template.docx` |
| Automatic JSON backup on every save (`Backups/{invoice_no}.json`) | Per-invoice recovery safety | 22 Jun 2026 ✅ | ✅ Active | `Backups/*.json`, `invoice.py` |
| `remarks` column on records | Free-text notes per record | Between 11 Jul–16 Jul 2026 🟡 | ✅ Active | `database.py`, `pages/records.py` |
| Invoice numbering YYMM + counter (with rollover/future-code handling) | Sequential, month-aware IDs | 1 Aug 2026 ✅ | ✅ Active | `invoice.py` |
| Regenerate invoice from Records page | Re-produce an existing customer's invoice | 16 Jul 2026 ✅ | ✅ Active | `pages/records.py`, `invoice.py` |
| Move-up/move-down SR ordering | Re-order rows within a month | 16 Jul 2026 ✅ | ✅ Active | `pages/records.py`, `database.py.swap_sr_no` |

## PDF Processing

| Feature | Purpose | Date | Status | Related files |
|---------|---------|------|--------|---------------|
| Bajaj DO PDF auto-extraction (name, product, price, mobile, address, BID, date, EMI, DI, scheme) | Avoid manual data entry from delivery orders | 16 Jul 2026 ✅ | ✅ Active | `pdf_extract.py` |
| Missing-field warnings after extraction | Flag unreliable auto-detection | 16 Jul 2026 ✅ | ✅ Active | `pdf_extract.py`, `pages/generate_invoice.py`, `helpers.py` |

## Excel

| Feature | Purpose | Date | Status | Related files |
|---------|---------|------|--------|---------------|
| Multi-sheet Excel export (one sheet per month, styled headers, alternating rows, auto column widths) | Maintain the user's existing workbook format | 23 Jun 2026 ✅ | ✅ Active | `excel_utils.py` |
| Excel file auto-update after every invoice save | Keep `Excel/ALL_RECORDS.xlsx` current | 23 Jun 2026 ✅ | ✅ Active | `excel_utils.update_excel_file` |
| Excel download button (Records page) | Export / share without needing the file on disk | 16 Jul 2026 ✅ | ✅ Active | `pages/records.py` |
| Download served from cached workbook when current | Avoid rebuilding the workbook on every rerun | 5 Aug 2026 ✅ | ✅ Active | `pages/records.py`, `database.get_db_fingerprint` |
| Style-object reuse in alternating rows (Option E) | Remove ~1.8M throwaway style objects per export | 5 Aug 2026 ✅ | ✅ Active | `excel_utils._apply_alternating_rows` |

## Dashboard & Statistics

| Feature | Purpose | Date | Status | Related files |
|---------|---------|------|--------|---------------|
| Dashboard page (records, DP, DI totals; monthly & daily charts) | Overview of business health | 16 Jul 2026 ✅ | ✅ Active | `pages/dashboard.py`, `database.get_dashboard_stats` |
| Month-context charts (records-per-day) | Per-day activity view | 16 Jul 2026 ✅ | ✅ Active | `pages/dashboard.py` |
| Skip redundant monthly GROUP BY on month views | Faster page renders | 5 Aug 2026 ✅ | ✅ Active | `pages/dashboard.py`, `ui_components._show_month_cards` |

## Search & Records

| Feature | Purpose | Date | Status | Related files |
|---------|---------|------|--------|---------------|
| Records page: search across name/phone/invoice/BID/product/serial | Find any customer fast | 16 Jul 2026 ✅ | ✅ Active | `pages/records.py`, `database.search_records` |
| Search-history chips | Repeat recent lookups | 16 Jul 2026 ✅ | ✅ Active | `helpers._get_search_history`, `pages/records.py` |
| Month filter, sorting, pagination | Browse the ledger efficiently | 16 Jul 2026 ✅ | ✅ Active | `pages/records.py`, `database.search_records` |
| Bulk select + delete | Remove many records at once | 16 Jul 2026 ✅ | ✅ Active | `pages/records.py` |
| Edit record form (full field set) | Correct mistakes | 16 Jul 2026 ✅ | ✅ Active | `pages/records.py`, `database.update_record` |
| Recent-invoices hint (LIMIT-10 SQL) | Fast invoice-number suggestions | 5 Aug 2026 ✅ | ✅ Active | `database.get_recent_invoices`, `pages/records.py` |

## EMI Notification

| Feature | Purpose | Date | Status | Related files |
|---------|---------|------|--------|---------------|
| EMI end-date computation (scheme `12/4`, first-EMI rule, last-EMI month) | Know when each customer's EMIs finish | 1 Aug 2026 ✅ | ✅ Active | `pages/emi_notification.py` |
| Two-month EMI-ending tables + prev/current/next navigation | Follow-up planning | 1 Aug 2026 ✅ | ✅ Active | `pages/emi_notification.py` |
| SQL pre-filter of EMI candidates (8 columns) | Faster page load | 5 Aug 2026 ✅ | ✅ Active | `database.load_emi_candidates` |

## Settings & Configuration

| Feature | Purpose | Date | Status | Related files |
|---------|---------|------|--------|---------------|
| Persistent settings file (`config/settings.json`): template path, poppler path, GST/CGST/SGST, invoice prefix, theme | Per-user configuration | 16 Jul 2026 ✅ | ✅ Active | `config.py`, `config/settings.json` |
| GST validation (CGST+SGST = GST) | Prevent wrong tax math | 16 Jul 2026 ✅ | ✅ Active | `pages/settings.py` |
| Database backup download / restore | Full-DB safety | 16 Jul 2026 ✅ | ✅ Active | `pages/settings.py` |
| System status (template/poppler/DB/report count) | Diagnose setup quickly | 16 Jul 2026 ✅ | ✅ Active | `pages/settings.py`, `database.count_records` |

## Synchronization & Cloud

| Feature | Purpose | Date | Status | Related files |
|---------|---------|------|--------|---------------|
| PostgreSQL / Neon support (`DATABASE_URL`) | Run on Render/cloud | 18 Jul 2026 ✅ | ✅ Active | `database.py` |
| Offline→Online sync script + `.bat` | Push local records to Neon | 18–31 Jul 2026 ✅ | ✅ Active | `sync_offline_to_online.py`, `Sync Offline to Online.bat` |
| Online→Offline sync script + `.bat` | Pull Neon records to desktop | 18–31 Jul 2026 ✅ | ✅ Active | `sync_online_to_offline.py`, `Sync Online to Offline.bat` |
| Settings → Sync Now (UI button) | One-click push to cloud | 18–31 Jul 2026 ✅ | ✅ Active | `pages/settings._sync_now` |
| Dedup-by-(invoice, serial) during sync | Prevent duplicates across copies | 18–31 Jul 2026 ✅ | ✅ Active | `sync_*.py`, `settings._sync_now` |

## UI/UX

| Feature | Purpose | Date | Status | Related files |
|---------|---------|------|--------|---------------|
| Top-button navigation (no sidebar) | Full-window layout for desktop + phone | 16 Jul 2026 ✅ | ✅ Active | `ui_components.render_menu`, `app.py` |
| Dark/light themes (CSS engine) | Personal preference; flash-free startup | 16 Jul 2026 ✅ | ✅ Active | `styles.py`, `app.py` |
| Mobile-responsive CSS (768px / 480px) | Android Chrome usability | 16 Jul 2026 ✅ | ✅ Active | `styles.py` |
| Theme toggle button in menu | Instant theme switching | 16 Jul 2026 ✅ | ✅ Active | `ui_components._toggle_theme` |
| `GAGAN FINANCE DESK.bat` double-click launcher | Start the app without a terminal | 23 Jun 2026 ✅ | ✅ Active | `GAGAN FINANCE DESK.bat` |

## Performance (5 Aug 2026 — all ✅ Active)

| Feature | Purpose | Related files |
|---------|---------|---------------|
| Targeted SQL quick-stats on Generate Invoice | Stop loading the whole table for "today" | `database.get_today_stats`, `pages/generate_invoice.py` |
| `SELECT COUNT(*)` on Settings status | Stop loading the whole table for a count | `database.count_records`, `pages/settings.py` |
| Scalar SQL `suggest_next_invoice()` | Next-invoice number without full scan | `database.get_latest_invoice_yy_code` / `get_max_invoice_counter` |
| Recent invoices `LIMIT 10` | Only fetch what's shown | `database.get_recent_invoices` |
| Move target point query | One row instead of full scan | `database.get_record_id_by_month_srno` |
| EMI candidate SQL pre-filter | 8 columns / filtered rows | `database.load_emi_candidates` |
| COUNT-only search caption | No `SELECT *` for a total | `database.count_search_records` |

## Data-Integrity & Migration Features

| Feature | Purpose | Date | Status | Related files |
|---------|---------|------|--------|---------------|
| `import_excel.py` bulk Excel → DB importer | Migrate existing records | 23 Jun 2026 ✅ | ✅ Active (manual CLI) | `import_excel.py` |
| `migrate_dates()` auto date canonicalization | Convert legacy ISO dates to DD-MM-YYYY + fix month column | 16 Jul 2026 🟡 (present by refactor) | 🟡 Opt-in only (never runs automatically since 20 Aug 2026) | `database.migrate_dates` |
| `bid_date` write-path normalization (PDF/manual/edit/import/sync) | Never store non-DD-MM-YYYY again | 5 Aug 2026 ✅ | ✅ Active | `database.add_record`/`update_record`, `sync_*.py`, `settings._sync_now` |
| `migrate_date_format.py` one-time migration | Convert 11 existing DD/MM/YYYY rows | 5 Aug 2026 ✅ | ✅ Retained in repo (idempotent) | `migrate_date_format.py` |

## Performance (Phase 3 - 2026-08-20)

| Feature | Purpose | Date | Status | Related files |
|---------|---------|------|--------|---------------|
| PostgreSQL connection pool (lazy `ThreadedConnectionPool`) | Reuse one Neon connection instead of a fresh TCP connect per operation | 20 Aug 2026 ✅ | ✅ Active (Render/Neon) | `database.py` |
| `st.cache_data` read-result caching (30 s TTL) | Skip repeated read-only queries across reruns | 20 Aug 2026 ✅ | ✅ Active (Streamlit) | `database.py` |
| Centralized `invalidate_cache()` | Clear cached read results immediately after every write | 20 Aug 2026 ✅ | ✅ Active | `database.py` |
| Lazy `init_db()` + opt-in `migrate_dates()` | No schema work / records scan at startup | 20 Aug 2026 ✅ | ✅ Active | `database.py` |
| `get_monthly_card_stats()` grouped query | One round trip for all monthly cards | 20 Aug 2026 ✅ | ✅ Active | `database.py`, `ui_components.py` |
| PostgreSQL Excel-download fingerprint cache | No full workbook rebuild on every rerun (Neon) | 20 Aug 2026 ✅ | ✅ Active | `pages/records.py`, `database.get_db_fingerprint` |
| Reuse `search_records()` total (no duplicate COUNT) | One fewer query per Records rerun | 20 Aug 2026 ✅ | ✅ Active | `pages/records.py` |
| Batched Settings Sync Now (`executemany`) | One connection + transaction instead of one per record | 20 Aug 2026 ✅ | ✅ Active | `pages/settings.py` |
| Lazy page/module imports | Dashboard no longer loads PDF/DOCX/Excel stack | 20 Aug 2026 ✅ | ✅ Active | `app.py`, `ui_components.py`, `pages/records.py` |
| Invoice-number / manual-entry lazy evaluation | No SQL when the value already exists | 20 Aug 2026 ✅ | ✅ Active | `pages/generate_invoice.py`, `ui_components.py` |
| `PERF_DEBUG=1` instrumentation | Optional query/pool timing (off by default) | 20 Aug 2026 ✅ | ✅ Active | `database.py` |
| Case-insensitive search matching | `LOWER(col) LIKE LOWER(?)` on both backends | 20 Aug 2026 ✅ | ✅ Active | `database.search_records` / `count_search_records` |
| Records page month XLSX download | Export the currently displayed Records page as .xlsx (in-memory; `✅` column excluded) | 22 Aug 2026 ✅ | ✅ Active | `pages/records.py`, `excel_utils.export_rows_to_xlsx` |
| EMI table XLSX downloads | Per-table .xlsx export of displayed EMI data | 22 Aug 2026 ✅ | ✅ Active | `pages/emi_notification.py` |
| Dual-backend protection comments | Never regress PostgreSQL support | 1 Aug 2026 ✅ | ✅ Active (enforced by convention) | `database.py` |

---

## Status Legend
- ✅ **Active** — present in the current codebase.
- 🟡 **Estimated date** — inferred from file timestamps/logs, not a direct commit.
- ❓ **Unknown** — cannot be proven.

Some features below are no longer the primary path or are superseded (shown for completeness):
- **HTML invoice preview** (`invoice.py` — `sts.html`/weasyprint paths, commit `957bb58`, 18 Jul 2026): superseded by the final DOCX-based template flow; the codebase currently ships DOCX→PDF→PNG on Windows and DOCX-only download elsewhere. 🟡
- **fpdf2 direct PDF** (`invoice.py` fpdf2 path, commit `8b8d00c`, 18 Jul 2026): listed in requirements but the active path uses `docxtpl` + optional `docx2pdf` (Windows) / `mammoth+weasyprint` (legacy commit). 🟡

---

## Current Feature Summary

| Feature category | Current status |
|------------------|----------------|
| Core ledger & invoicing | ✅ Active |
| PDF processing | ✅ Active |
| Excel | ✅ Active |
| Dashboard & statistics | ✅ Active |
| Search & records | ✅ Active |
| EMI notification | ✅ Active |
| Settings & configuration | ✅ Active |
| Synchronization & cloud | ✅ Active |
| UI/UX | ✅ Active |
| Performance | ✅ Active (5 Aug 2026 optimizations) |
| Data-integrity & migration | ✅ Active |

*End of FEATURE_HISTORY.md*

# Gagan Finance Desk

A desktop-first finance management application for an electronics / mobile financing business. It turns Bajaj Delivery-Order PDFs into professional invoices, keeps every financed record in a local ledger, maintains a month-by-month Excel export, and synchronizes to a Neon/PostgreSQL cloud copy.

Runs fully offline on your PC (SQLite) and in the cloud (Render + Neon) from the same codebase.

![GitHub last commit](https://img.shields.io/github/last-commit/gaganthakur9999-del/gagan-finance-desk)
![GitHub repo size](https://img.shields.io/github/repo-size/gaganthakur9999-del/gagan-finance-desk)
![Top language](https://img.shields.io/github/languages/top/gaganthakur9999-del/gagan-finance-desk)
![GitHub issues](https://img.shields.io/github/issues/gaganthakur9999-del/gagan-finance-desk)

---

## Quick Start

```bash
pip install -r requirements.txt
python -m streamlit run app.py
```

Upload a Bajaj DO PDF on **Generate Invoice**, confirm the extracted details, and click **Generate Invoice + Save** — the invoice is created, the record is stored, a JSON backup is written, and the Excel ledger updates automatically.

---

## Overview

- Upload a Bajaj **DO PDF** → customer, product, price, phone, address, BID, date, EMI, DI and scheme are extracted automatically into an editable form.
- Generate a GST-aware invoice from `template.docx` (taxable value, CGST, SGST, amount in words) and keep a JSON backup with every save.
- Maintain a month-by-month records ledger (SR number per month, `YYMM + counter` invoice numbers).
- Keep `Excel/ALL_RECORDS.xlsx` current (one sheet per month) and export it from the app.
- Track totals (records, DP, DI) and per-month/per-day charts on the Dashboard.
- See which customers' EMIs end in the next two months (EMI Notification tab; candidate query fixed Sep 2026).
- Search, filter, sort, paginate, bulk-delete, edit, regenerate invoices, and re-order rows.
- Sync V2 runs automatically in the background on the desktop app (see [Synchronization](#synchronization)).

---

## Features

- PDF auto-extraction with missing-field warnings
- Invoice generation (DOCX; PDF/PNG preview on Windows)
- Per-invoice JSON backups
- Month-sheet Excel export/update + download
- Dashboard charts
- EMI end-date notifications
- Full Records management (search / edit / delete / regenerate / move)
- Settings (GST rates, paths, theme, DB backup/restore, Sync V2 status)
- **Sync V2** — automatic background synchronization: master-first Offline, Online-created records pull back, tombstone deletes, revision/conflict tracking

> **Screenshots:** coming soon — will show the invoice flow, dashboard, records table, and EMI notification views.

---

## Technology Stack

| Area | Technology |
|------|-----------|
| Language | Python 3.14+ |
| UI | Streamlit |
| Local DB | SQLite (WAL) |
| Cloud DB | Neon / PostgreSQL (`psycopg2-binary`) |
| Excel | `openpyxl` |
| Invoices | `docxtpl`, `num2words` |
| PDF extract | `pypdf` |
| PDF/PNG (Windows) | `docx2pdf`, `pdf2image`, `pywin32` |

---

## Requirements

- **Python 3.14+** with `pip` — dependencies are pinned in `requirements.txt`.
- **Windows (optional, PDF/PNG preview only):** Microsoft Word and Poppler binaries.
- **Cloud (optional):** a Neon PostgreSQL connection string for `DATABASE_URL`.

---

## Architecture Overview

`database.py` is a single dual-backend data layer:

- **SQLite** is used when `DATABASE_URL` is absent (offline desktop mode).
- **PostgreSQL/Neon** is used when `DATABASE_URL` is set (Render/cloud) — SQL placeholders are converted automatically and every query path is PostgreSQL-safe.

Both backends must always be preserved in one module (a past regression removed PostgreSQL support once; guard comments in `database.py` prevent recurrence).

---

## Project Structure

```text
.
├── app.py                  # Streamlit entry point + routing
├── pages/                  # 5 routable pages
├── database.py             # Dual-backend data layer
├── invoice.py              # Invoice generation, numbering, backups
├── pdf_extract.py          # DO PDF extraction
├── excel_utils.py          # Multi-sheet Excel export
├── styles.py / helpers.py / ui_components.py / config.py
├── docs/                   # All long-form documentation
├── scripts/
│   ├── import/             # Excel → DB importer
│   ├── sync/               # Offline↔Online sync
│   └── migrations/         # One-time migrations
├── requirements.txt
└── .env.example
```

Runtime directories (`data/`, `Excel/`, `Backups/`, `logs/`, `temp/`, `config/settings.json`) are git-ignored and never committed.

---

## Repository Policy

- **Runtime & private data is never committed** — local databases, backups, logs, exports, credentials (`.env`) and temp files stay local (see `.gitignore`).
- **History is preserved** — relocated files use `git mv` so change history is retained.
- **Documentation lives in `docs/`** — the README is the single public homepage and links outward.

---

## Installation

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m streamlit run app.py
```

Or double-click `GAGAN FINANCE DESK.bat` on Windows.

---

## Configuration

Settings persist in `config/settings.json` (edit in the Settings page): template path, Poppler path, GST/CGST/SGST, invoice prefix, theme.

Environment variables:

- `DATABASE_URL` — set on Render to point to Neon PostgreSQL (the Online/Render app switches to PostgreSQL; it never runs the desktop background worker).
- `NEON_URL` — used by the Offline Sync V2 background worker to reach Neon.
- `FINANCE_DB_PATH` — optional override of the local SQLite path (defaults to `data/finance.db`).

---

## Running Locally

Start the app, then:

1. Upload a Bajaj DO PDF on **Generate Invoice**.
2. Confirm/re-edit the extracted data.
3. Click **Generate Invoice + Save** (creates invoice file + DB record + JSON backup + Excel update).

---

## Deployment (Render + Neon)

1. Push `main` to GitHub.
2. Create a Render **Web Service** from the repo.
3. Start command:

   ```text
   streamlit run app.py --server.port ${PORT:-8501} --server.address 0.0.0.0
   ```

4. Set the environment variable `DATABASE_URL` to your Neon connection string (`sslmode=require`).
5. Deploy — the app uses PostgreSQL automatically; DOCX download works everywhere; PDF/PNG preview is Windows-only by design.
6. The Online/Render service **never** starts the Offline background worker and **never** shows local database backup/restore or sync-button controls — the desktop-only tools are gated behind the absence of `DATABASE_URL`.

---

## Synchronization

### Sync V2 (current, automatic)

Sync V2 is the production synchronization system. It is active automatically on the **Offline (desktop/SQLite)** application only; the Online/Render app never runs a background worker and exposes no sync controls.

- **Offline is the master.** Offline can create, edit, delete and reorder records. Writes are **local-first**: the SQLite transaction commits and the durable outbox entry is written immediately, then the background worker is notified — normal CRUD never waits for the network.
- **Background worker** (`sync_v2_worker.py`): starts when Offline opens, runs one sync session, then waits for write notifications with periodic retries when connectivity is unavailable. Failures are recorded in `sync_state`; local data and pending outbox operations are preserved.
- **Online may create new records.** Online-created rows receive a Sync V2 `sync_id`, a server revision and base snapshot through the Online write seam, and are pulled back into Offline by the worker. Online is not an independent editing master.

Actual data flow:

```text
Offline local change → SQLite commit → durable outbox → background worker → Neon
Online new record    → Neon (Sync V2 metadata) → background worker → Offline SQLite
```

- **Identity:** `sync_id` is the stable cross-database identity. All legacy NULL-sync production records have been adopted; production now has **0** remaining NULL-sync identities.
- **Deletes:** Sync V2 tombstones (sets `deleted_at`) instead of instantly destroying synchronization history. Tombstoned records disappear from all normal/live reads and cannot be resurrected by a later sync.
- **Conflicts:** changes carry revision/base snapshots and go through a three-way merge. Safe independent field changes merge; same-field/divergent changes become explicit conflicts that are never silently overwritten. SR (serial-order) moves use grouped month-level conflict handling. Conflicts are resolved through the existing Keep Offline / Keep Online / Review-Merge choices.
- **Settings page (Offline):** shows live Sync V2 status (synced / syncing / error / pending / conflicts needing review), last successful sync, and local pending count. No manual sync button is needed.

> Historical/technical detail lives in the canonical docs: architecture in `docs/TECHNICAL_HISTORY.md`, milestones in `docs/PROJECT_HISTORY.md`, and dated changes in `docs/CHANGELOG.md`.

---

## Documentation

- [`docs/PROJECT_HISTORY.md`](docs/PROJECT_HISTORY.md) — chronological development history (evidence + confidence)
- [`docs/FEATURE_HISTORY.md`](docs/FEATURE_HISTORY.md) — feature inventory with dates, status, files
- [`docs/TECHNICAL_HISTORY.md`](docs/TECHNICAL_HISTORY.md) — architecture, database, performance, sync, Excel-engine milestones
- [`docs/CHANGELOG.md`](docs/CHANGELOG.md) — forward-keeping change log from the current production baseline

---

## Current Status

- **GitHub:** `main` = `9ac24f3` (local HEAD == origin/main at last documentation update)
- **Sync V2:** deployed and live — automatic background sync works on Offline; Online-created records automatically reach Offline; legacy NULL-sync identities adopted (0 remain)
- **Render:** deployed; uses Neon via `DATABASE_URL`; no background worker; local sync/backup controls hidden
- **Neon ↔ SQLite:** production live record counts match (1,527 live on each at last verification); no open conflicts; outbox clean
- **EMI Notification:** candidate-query bug fixed (Sep 2026); notifications show correctly again
- **Backups:** per-invoice JSON (`Backups/`), SQLite snapshots (`data/` + timestamped `Backups/` pre-change snapshots), Settings backup/restore (desktop only)

---

## Roadmap (short)

- Incremental Excel-update engine (update only the affected month sheet)
- Full-text search (FTS5) for the Records page
- Index fix for `LOWER()`-based invoice/serial uniqueness checks
- Precomputed monthly summary for dashboard/stats at scale
- Connected conflict-review surface in the Offline Settings page (attach the running engine to the existing Sync V2 review UI)

These are identified, verified opportunities from the technical audit — not commitments. The Sync V2 foundation itself (automatic worker, Online seam, adoption) is **shipped**.

## EMI Notification fix (September 2026)

The EMI Notification page stopped showing any customers because `database.load_emi_candidates()` built its SQL with Python `%`-formatting while the query contained the `LIKE '%/%'` wildcard literal — every call raised a silent format error and the function returned an empty list (on both SQLite and PostgreSQL). The SQL literal was corrected to `LIKE '%%/%%'` (the escaped form for `%`-formatting). EMI candidates are returned again and the notifications show the correct upcoming EMI-ending months.

---

## Acknowledgements

- Built with **Streamlit**, **openpyxl**, **docxtpl**, **pypdf**, and **psycopg2** — the open-source backbone of this tool.
- The ledger model (monthly sheets, SR numbers, `YYMM` invoice IDs) follows the business workflow it serves.
- Thanks to the project's users for real-world use that shaped every version.

---

## Support / License

**Support:** open an issue on the GitHub repository, or contact the repository owner.

**License:** this repository does not include a license file — no license is implied. For any use beyond personal operation, contact the project owner.

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
- See which customers' EMIs end in the next two months (EMI Notification tab).
- Search, filter, sort, paginate, bulk-delete, edit, regenerate invoices, and re-order rows.

---

## Features

- PDF auto-extraction with missing-field warnings
- Invoice generation (DOCX; PDF/PNG preview on Windows)
- Per-invoice JSON backups
- Month-sheet Excel export/update + download
- Dashboard charts
- EMI end-date notifications
- Full Records management (search / edit / delete / regenerate / move)
- Settings (GST rates, paths, theme, DB backup/restore, cloud sync)
- SQLite ↔ Neon synchronization (dedup by invoice + serial)

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

- `DATABASE_URL` — set on Render to point to Neon PostgreSQL (app switches to PostgreSQL).
- `NEON_URL` — used by the sync scripts and Settings → Sync Now.

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

---

## Synchronization

```bash
python scripts/sync/sync_offline_to_online.py   # SQLite → Neon
python scripts/sync/sync_online_to_offline.py   # Neon → SQLite
```

Records are deduplicated by `(invoice_no, serial_no)`; `bid_date` is normalized to `DD-MM-YYYY` on every write path. Windows launchers are included (`Sync *.bat`).

---

## Documentation

- [`docs/PROJECT_HISTORY.md`](docs/PROJECT_HISTORY.md) — chronological development history (evidence + confidence)
- [`docs/FEATURE_HISTORY.md`](docs/FEATURE_HISTORY.md) — feature inventory with dates, status, files
- [`docs/TECHNICAL_HISTORY.md`](docs/TECHNICAL_HISTORY.md) — architecture, database, performance, sync, Excel-engine milestones
- [`docs/CHANGELOG.md`](docs/CHANGELOG.md) — forward-keeping change log from the current production baseline

---

## Current Status

- **GitHub:** `main` = `84aa752` (local HEAD == origin/main)
- **Render:** deployed; uses Neon via `DATABASE_URL`
- **Neon:** synchronized with local dataset; all `bid_date` values `DD-MM-YYYY`
- **SQLite:** 1,395 records, canonical dates, verified month consistency
- **Backups:** per-invoice JSON (`Backups/`), SQLite snapshots (`data/`), Settings backup/restore

---

## Roadmap (short)

- Incremental Excel-update engine (update only the affected month sheet)
- Full-text search (FTS5) for the Records page
- Index fix for `LOWER()`-based invoice/serial uniqueness checks
- Batch Neon synchronization (single connection + `executemany`)
- Precomputed monthly summary for dashboard/stats at scale

These are identified, verified opportunities from the technical audit — not commitments.

---

## Acknowledgements

- Built with **Streamlit**, **openpyxl**, **docxtpl**, **pypdf**, and **psycopg2** — the open-source backbone of this tool.
- The ledger model (monthly sheets, SR numbers, `YYMM` invoice IDs) follows the business workflow it serves.
- Thanks to the project's users for real-world use that shaped every version.

---

## Support / License

**Support:** open an issue on the GitHub repository, or contact the repository owner.

**License:** this repository does not include a license file — no license is implied. For any use beyond personal operation, contact the project owner.

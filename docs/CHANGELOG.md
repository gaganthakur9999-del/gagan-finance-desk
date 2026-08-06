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

Entries for changes landing **after** the production baseline will be added here. Use the template below for each future release section.

### Added
_To be written._

### Changed
_To be written._

### Fixed
_To be written._

### Performance
_To be written._

### Documentation
_To be written._

### Refactoring
_To be written._
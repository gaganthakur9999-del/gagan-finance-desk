"""
bootstrap_reconcile.py — Phase-2 READ-ONLY Initial Synchronization Review runner.

Loads records from the real Offline (SQLite) and Online (Neon) databases using
READ-ONLY connections, runs the reconciliation analysis, and writes the review to:

    data/syncv2/bootstrap_review_<timestamp>.json   (full machine-readable report)
    data/syncv2/bootstrap_review_<timestamp>.md     (human-readable summary)

Safety: it never writes to either production database (no INSERT/UPDATE/DELETE/
ALTER/sync_id/tombstone/outbox/conflict/baseline writes). Reports only.

Usage:
    python scripts/sync/bootstrap_reconcile.py
"""
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

import sync_bootstrap_analysis as ana  # noqa: E402

DB_FILE = os.path.join(PROJECT_ROOT, "data", "finance.db")
ENV_FILE = os.path.join(PROJECT_ROOT, ".env")
OUT_DIR = os.path.join(PROJECT_ROOT, "data", "syncv2")

COLS = ["id"] + ana.BUSINESS_FIELDS + ana.SYSTEM_FIELDS
_SQL_COLS = ",".join(COLS)


def load_neon_url():
    url = os.environ.get("NEON_URL", "")
    if url:
        return url
    if os.path.exists(ENV_FILE):
        for line in open(ENV_FILE, encoding="utf-8"):
            line = line.strip()
            if line.startswith("NEON_URL="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def load_offline():
    conn = sqlite3.connect("file:%s?mode=ro" % DB_FILE, uri=True)
    try:
        rows = conn.execute("SELECT %s FROM records ORDER BY id" % _SQL_COLS).fetchall()
        return [dict(zip(COLS, r)) for r in rows]
    finally:
        conn.close()


def load_online():
    url = load_neon_url()
    if not url:
        print("NEON_URL not available", file=sys.stderr)
        sys.exit(1)
    import psycopg2
    conn = psycopg2.connect(url)
    conn.set_session(readonly=True, autocommit=True)
    try:
        cur = conn.cursor()
        cur.execute("SELECT %s FROM records ORDER BY id" % _SQL_COLS)
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        cur.close()
        return rows
    finally:
        conn.close()


def fmt(v):
    return "" if v is None else str(v)


def _cap(items, limit=400):
    return items[:limit], max(0, len(items) - limit)


def row_line(r):
    return ("invoice=%s | bid=%s | name=%s | date=%s | serial=%s | amount=%s | "
            "product=%s | month=%s | phone=%s | sr=%s" % (
                fmt(r.get("invoice")), fmt(r.get("bid")), fmt(r.get("name")),
                fmt(r.get("bid_date")), fmt(r.get("serial")), fmt(r.get("price")),
                fmt(r.get("product")), fmt(r.get("month")), fmt(r.get("phone")),
                fmt(r.get("sr_no"))))


def render_markdown(review) -> str:
    s = review["summary"]
    L = ["INITIAL SYNC REVIEW", "===================", "",
         "Offline records:              %d" % s["offline_records"],
         "Online records:               %d" % s["online_records"], "",
         "High-confidence matches:      %d" % s["high_confidence_matches"],
         "Medium-confidence matches:    %d" % s["medium_confidence_matches"],
         "Low-confidence matches:       %d" % s["low_confidence_matches"],
         "Suspicious matches (review):  %d" % s["suspicious_matches"],
         "Ambiguous (offline):          %d" % s["ambiguous_offline"],
         "Ambiguous (online):           %d" % s["ambiguous_online"], "",
         "Identical matches:            %d" % s["identical_matches"],
         "Offline differs:              %d" % s["offline_differs"],
         "Online differs:               %d" % s["online_differs"],
         "Both differ:                  %d" % s["both_differ"],
         "Potential conflicts:          %d" % s["potential_conflicts"], "",
         "Offline-only:                 %d" % s["offline_only"],
         "Online-only:                  %d" % s["online_only"], "",
         "Invoice collisions:           %d" % s["invoice_collisions"],
         "SR / order differences:       %d" % s["sr_order_differences"],
         "Serial differences:           %d" % s["serial_differences"],
         "BID differences:              %d" % s["bid_differences"], "",
         "Potential manual reviews:     %d" % s["potential_manual_reviews"], "",
         "PRODUCTION MODIFIED:          %s" % ("NO" if not s["production_modified"] else "YES"),
         "",
         "ONLINE-ONLY RECORDS (%d)" % s["online_only"],
         "Default action: KEEP ONLINE FOR LATER (review each)."]
    items, dropped = _cap(review["online_only"])
    for e in items:
        L.append("  Online-only  %s" % row_line(e["record"]))
    if dropped:
        L.append("  ... and %d more (see JSON)." % dropped)

    L += ["", "OFFLINE-ONLY RECORDS (%d)" % s["offline_only"],
          "Default action: NEW OFFLINE-ONLY RECORD FOR REVIEW (add to sync later)."]
    items, dropped = _cap(review["offline_only"])
    for e in items:
        L.append("  Offline-only  %s" % row_line(e["record"]))
    if dropped:
        L.append("  ... and %d more (see JSON)." % dropped)

    L += ["", "AMBIGUOUS MATCHES (%d)" % (s["ambiguous_offline"] + s["ambiguous_online"])]
    items, dropped = _cap(review["ambiguous"])
    for e in items:
        L.append("  [%s id=%s] %s -> %s" % (e["side"], e["ids"][0], e["reason"],
                                            row_line(e["record"])))
    if dropped:
        L.append("  ... and %d more (see JSON)." % dropped)

    L += ["", "INVOICE COLLISIONS (%d)" % s["invoice_collisions"]]
    for c in review["invoice_collisions"]:
        L.append("  invoice=%s groups=%s" % (c["invoice"], c["groups"]))

    L += ["", "FIELD FINDINGS",
          "  Offline blank (invoice/serial/bid): %s" % json.dumps(review["offline_findings"]["blank"]),
          "  Online  blank (invoice/serial/bid): %s" % json.dumps(review["online_findings"]["blank"]),
          "  Offline dup invoice: %s" % review["offline_findings"]["dup_invoice"],
          "  Online  dup invoice: %s" % review["online_findings"]["dup_invoice"],
          "  Offline dup serial:  %s" % review["offline_findings"]["dup_serial"],
          "  Online  dup serial:  %s" % review["online_findings"]["dup_serial"],
          "  Offline dup bid:     %s" % review["offline_findings"]["dup_bid"],
          "  Online  dup bid:     %s" % review["online_findings"]["dup_bid"],
          "",
          "Full per-match diffs, proposed mapping and baseline preview are in the "
          "matching .json report."]
    return "\n".join(L)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    print("Loading Offline (SQLite, read-only) ...", flush=True)
    off = load_offline()
    print("Loading Online (Neon, read-only) ...", flush=True)
    on = load_online()
    print("Offline %d / Online %d records loaded. Running analysis ..." % (len(off), len(on)), flush=True)
    review = ana.build_review(off, on)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    jp = os.path.join(OUT_DIR, "bootstrap_review_%s.json" % ts)
    mp = os.path.join(OUT_DIR, "bootstrap_review_%s.md" % ts)
    with open(jp, "w", encoding="utf-8") as f:
        json.dump(review, f, indent=1, default=str)
    with open(mp, "w", encoding="utf-8") as f:
        f.write(render_markdown(review))
    print(render_markdown(review))
    print("\nReports written:\n  %s\n  %s" % (jp, mp))


if __name__ == "__main__":
    main()


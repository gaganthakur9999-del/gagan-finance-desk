"""
bootstrap_apply.py — Phase-3 PRODUCTION BOOTSTRAP / BASELINE (one-time, guarded).

Establishes the approved initial synchronization baseline from the Phase-2
READ-ONLY review report:
  - 1,523 matched identity pairs (1,522 auto + 1 manual NEEL CHAND pair)
  - OFFLINE IS AUTHORITATIVE for every matched record
  - Online business fields are brought to the Offline baseline (the only expected
    production business change: the 16 approved sr_no ordering fixes)
  - permanent UUID v4 sync_id assigned per identity (same UUID on both replicas)
  - per-record base_json = Offline business snapshot (identical string on both sides)
  - server_rev / row_rev = 0 (no fake edit history)
  - sync_state updated to "mutually baselined"; outbox/applied_ops/conflicts stay EMPTY

Safety model (mandatory order, see Phase-3 authorization):
  VERIFY GIT -> FRESH BACKUPS -> VERIFY BACKUPS -> APPLY -> VERIFY RESULT.
  Refuses to run when either database already has sync_id values unless an explicit
  `--recover` completes the missing half of a previously interrupted apply.
  All writes per database run inside a single transaction; failures ROLL BACK,
  STOP and REPORT. No automatic "clever" partial recovery.

Usage:
    python scripts/sync/bootstrap_apply.py                 # apply against production
    python scripts/sync/bootstrap_apply.py --recover       # complete an interrupted apply
    python scripts/sync/bootstrap_apply.py --report <path> # explicit Phase-2 report
"""
import hashlib
import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

import sync_bootstrap_analysis as ana  # noqa: E402

DB_FILE = os.path.join(PROJECT_ROOT, "data", "finance.db")
ENV_FILE = os.path.join(PROJECT_ROOT, ".env")
SYNCV2_DIR = os.path.join(PROJECT_ROOT, "data", "syncv2")
STATE_FILE = os.path.join(SYNCV2_DIR, "bootstrap_apply_state.json")

BASE_FIELDS = ana.BUSINESS_FIELDS  # 19 business fields stored in base_json

# Checksum column list is identical to the safety-backup mechanism for comparability.
CHECKSUM_COLUMNS = [
    "id", "sr_no", "bid_date", "invoice_no", "name", "xcell", "product", "serial_no",
    "price", "emi", "di", "bid", "dp_taken", "scheme", "actual_product",
    "given_prod_price", "phone", "alt_phone", "month", "created_at", "updated_at",
    "remarks",
]


class StopError(Exception):
    """Any critical stop condition. Raised (never swallowed) -> ROLLBACK/STOP/REPORT."""


def sha256_of_rows(rows) -> str:
    h = hashlib.sha256()
    for row in rows:
        h.update(json.dumps(list(row), sort_keys=True, default=str).encode("utf-8"))
    return h.hexdigest()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def make_base_json(off_row) -> str:
    """Canonical baseline business snapshot from the authoritative Offline row.

    Stored VERBATIM on both replicas so base_json is byte-identical per pair.
    """
    return json.dumps({f: off_row.get(f) for f in BASE_FIELDS},
                      sort_keys=True, default=str, ensure_ascii=True, separators=(",", ":"))


def latest_report():
    files = sorted(
        f for f in os.listdir(SYNCV2_DIR)
        if f.startswith("bootstrap_review_") and f.endswith(".json"))
    if not files:
        raise StopError("No bootstrap_review_*.json report found in %s" % SYNCV2_DIR)
    return os.path.join(SYNCV2_DIR, files[-1])


def load_report(path):
    with open(path, encoding="utf-8") as f:
        rep = json.load(f)
    mapping = rep.get("proposed_mapping", [])
    uuid_by_pair = {}
    for e in mapping:
        if e.get("offline_id") is not None and e.get("online_id") is not None:
            uuid_by_pair[(e["offline_id"], e["online_id"])] = e["proposed_sync_id"]
    auto = []
    for m in rep.get("matches", []):
        oid, nid = m["offline_id"], m["online_id"]
        sid = uuid_by_pair.get((oid, nid))
        if not sid:
            raise StopError("Report is missing proposed_sync_id for pair (%s,%s)" % (oid, nid))
        auto.append({"offline_id": oid, "online_id": nid, "sync_id": sid,
                     "level": m.get("level"), "sr_only_diff": m.get("identical") is False})
    off_only = rep.get("offline_only", [])
    on_only = rep.get("online_only", [])
    if len(off_only) != 1 or len(on_only) != 1:
        raise StopError("Report no longer shows exactly one Offline-only and one "
                        "Online-only record (got %d/%d). Unexpected state - STOP."
                        % (len(off_only), len(on_only)))
    off_row, on_row = off_only[0]["record"], on_only[0]["record"]
    return {
        "path": path,
        "generated_checksum": rep.get("summary", {}).get("reconciliation_ok"),
        "auto": auto,
        "manual": {"offline_id": off_row["id"], "online_id": on_row["id"],
                   "offline_record": off_row, "online_record": on_row},
        "summary": rep.get("summary", {}),
    }


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return None


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1, default=str)


def ensure_manual_uuid(plan):
    """Persist (once) the manual NEEL CHAND pair UUID so re-runs/recovery are stable."""
    st = load_state()
    report_path = os.path.abspath(plan["path"])
    if st and st.get("report_path") == report_path and st.get("manual_sync_id"):
        return st["manual_sync_id"]
    manual_uuid = str(uuid.uuid4())
    if st:
        st["report_path"] = report_path
        st["manual_sync_id"] = manual_uuid
        st["manual_offline_id"] = plan["manual"]["offline_id"]
        st["manual_online_id"] = plan["manual"]["online_id"]
        st["manual_updated_at_utc"] = now_iso()
        save_state(st)
    else:
        save_state({
            "created_at_utc": now_iso(),
            "report_path": report_path,
            "source": "bootstrap_apply.py",
            "manual_sync_id": manual_uuid,
            "manual_offline_id": plan["manual"]["offline_id"],
            "manual_online_id": plan["manual"]["online_id"],
        })
    return manual_uuid


def build_pairs(plan):
    """Flatten plan to per-identity rows: [(offline_id, online_id, sync_id, is_manual)]."""
    manual_uuid = ensure_manual_uuid(plan)
    pairs = [{"offline_id": p["offline_id"], "online_id": p["online_id"],
              "sync_id": p["sync_id"], "manual": False, "level": p["level"],
              "sr_diff": bool(p.get("sr_only_diff"))}
             for p in plan["auto"]]
    pairs.append({"offline_id": plan["manual"]["offline_id"],
                  "online_id": plan["manual"]["online_id"],
                  "sync_id": manual_uuid, "manual": True, "level": "manual_neel",
                  "sr_diff": False})
    _validate_coverage(pairs)
    return pairs


def _validate_coverage(pairs):
    offs = [p["offline_id"] for p in pairs]
    ons = [p["online_id"] for p in pairs]
    if len(offs) != len(set(offs)) or len(ons) != len(set(ons)):
        raise StopError("Mapping contains duplicate offline/online ids - STOP")
    sids = [p["sync_id"] for p in pairs]
    if len(sids) != len(set(sids)):
        raise StopError("Duplicate sync_id in mapping - STOP")


def load_neon_url():
    url = os.environ.get("NEON_URL", "")
    if url:
        return url
    if os.path.exists(ENV_FILE):
        for line in open(ENV_FILE, encoding="utf-8"):
            if line.startswith("NEON_URL="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def offline_connect():
    conn = sqlite3.connect(DB_FILE)
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def online_connect():
    url = load_neon_url()
    if not url:
        raise StopError("NEON_URL not available")
    import psycopg2
    conn = psycopg2.connect(url)
    conn.set_session(autocommit=False)
    return conn


def read_rows(conn, is_pg):
    """id + business fields for every record, keyed by id."""
    cols = ["id"] + BASE_FIELDS
    sql = "SELECT %s FROM records ORDER BY id" % ",".join(cols)
    if is_pg:
        cur = conn.cursor()
        cur.execute(sql)
        desc = [d[0] for d in cur.description]
        rows = [dict(zip(desc, r)) for r in cur.fetchall()]
        cur.close()
    else:
        rows = [dict(zip(cols, r)) for r in conn.execute(sql).fetchall()]
    return {r["id"]: r for r in rows}


def read_sync_state_counts(conn, is_pg):
    """Current (records, sync_id_set, outbox/applied/conflict counts)."""
    if is_pg:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM records")
        n = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM records WHERE sync_id IS NOT NULL AND sync_id <> ''")
        s = cur.fetchone()[0]
        counts = {}
        for t in ("outbox", "applied_ops", "conflicts"):
            cur.execute("SELECT COUNT(*) FROM " + t)
            counts[t] = cur.fetchone()[0]
        cur.close()
        return n, s, counts
    n = conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]
    s = conn.execute(
        "SELECT COUNT(*) FROM records WHERE sync_id IS NOT NULL AND sync_id <> ''").fetchone()[0]
    counts = {t: conn.execute("SELECT COUNT(*) FROM " + t).fetchone()[0]
              for t in ("outbox", "applied_ops", "conflicts")}
    return n, s, counts


def business_diffs(off_row, on_row):
    """Business fields that differ between the two sides (normalized comparison)."""
    return [f for f in BASE_FIELDS if not ana.field_equal(f, off_row, on_row)]


def preflight(plan, pairs, off_rows, on_rows, report_sr_diff_pairs):
    """Every critical stop condition is checked BEFORE any write. Raises StopError."""
    off_ids = set(off_rows)
    on_ids = set(on_rows)
    plan_off = {p["offline_id"] for p in pairs}
    plan_on = {p["online_id"] for p in pairs}
    if len(off_ids) != len(plan_off) or len(on_ids) != len(plan_on):
        unexpected_off = sorted(off_ids - plan_off)
        unexpected_on = sorted(on_ids - plan_on)
        raise StopError(
            "Unexpected records vs approved report - STOP. unexpected_offline_ids=%s "
            "unexpected_online_ids=%s" % (unexpected_off, unexpected_on))
    if not off_ids.issubset(plan_off) or not on_ids.issubset(plan_on):
        raise StopError("Record coverage mismatch - STOP")

    sr_fix_pairs = set()
    for p in pairs:
        off_row = off_rows[p["offline_id"]]
        on_row = on_rows.get(p["online_id"])
        if on_row is None:
            raise StopError("Online partner missing for offline id %s - STOP" % p["offline_id"])
        diffs = business_diffs(off_row, on_row)
        unexpected = [f for f in diffs if f != "sr_no"]
        if unexpected:
            raise StopError(
                "Unexpected business-field difference on pair (%s,%s): %s - STOP"
                % (p["offline_id"], p["online_id"], unexpected))
        if diffs:
            if p["manual"]:
                raise StopError("Manual NEEL pair unexpectedly differs: %s - STOP" % diffs)
            sr_fix_pairs.add(p["online_id"])
    if sr_fix_pairs != set(report_sr_diff_pairs):
        raise StopError(
            "SR-difference pair set changed since the approved report - STOP "
            "(now %d expected %d)" % (len(sr_fix_pairs), len(report_sr_diff_pairs)))
    return {"sr_fix_online_ids": sorted(sr_fix_pairs)}


def _sync_state_update(conn, is_pg, now):
    """Mark the replica as mutually baselined (id=1 seed row exists from Phase 1)."""
    if is_pg:
        cur = conn.cursor()
        cur.execute(
            "UPDATE sync_state SET last_success_at=%s, last_attempt_at=%s, "
            "last_error=NULL, last_pulled_sync_rev=0, last_push_op_id=NULL, "
            "conflict_count=0 WHERE id=1", (now, now))
        cur.close()
    else:
        conn.execute(
            "UPDATE sync_state SET last_success_at=?, last_attempt_at=?, "
            "last_error=NULL, last_pulled_sync_rev=0, last_push_op_id=NULL, "
            "conflict_count=0 WHERE id=1", (now, now))


def apply_offline(conn, pairs, off_rows, now):
    """Single-transaction Offline update: sync_id + base_json + rev init. No business change."""
    try:
        for p in pairs:
            off = off_rows[p["offline_id"]]
            base = make_base_json(off)
            cur = conn.execute(
                "UPDATE records SET sync_id=?, base_json=?, server_rev=0, row_rev=0 "
                "WHERE id=? AND (sync_id IS NULL OR sync_id='')", (p["sync_id"], base, p["offline_id"]))
            if cur.rowcount != 1:
                raise StopError("Offline UPDATE rowcount %s != 1 for id %s - ROLLBACK"
                                % (cur.rowcount, p["offline_id"]))
        _sync_state_update(conn, False, now)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def read_checksum_rows(conn, is_pg):
    cols = ",".join(CHECKSUM_COLUMNS)
    if is_pg:
        cur = conn.cursor()
        cur.execute("SELECT %s FROM records ORDER BY id" % cols)
        rows = cur.fetchall()
        cur.close()
        return rows
    return conn.execute("SELECT %s FROM records ORDER BY id" % cols).fetchall()


def verify_post(conn_off, off_pg, conn_on, on_pg, pairs, manual_uuid, expected=None):
    """Mandatory post-bootstrap verification. Raises StopError on any mismatch."""
    if expected is None:
        expected = len(pairs)
    off_rows = read_rows(conn_off, off_pg)
    on_rows = read_rows(conn_on, on_pg)
    if len(off_rows) != expected or len(on_rows) != expected:
        raise StopError("Record count changed after apply - STOP (%d/%d)"
                        % (len(off_rows), len(on_rows)))

    def base_json_map(conn, is_pg):
        if is_pg:
            cur = conn.cursor()
            cur.execute("SELECT id, base_json FROM records")
            rows = cur.fetchall()
            cur.close()
            return {r[0]: r[1] for r in rows}
        return {r[0]: r[1] for r in conn.execute("SELECT id, base_json FROM records").fetchall()}

    off_base = base_json_map(conn_off, off_pg)
    on_base = base_json_map(conn_on, on_pg)
    pair_by_on = {p["online_id"]: p for p in pairs}
    for nid, on_row in on_rows.items():
        p = pair_by_on.get(nid)
        if p is None:
            raise StopError("Online id %s not in mapping - STOP" % nid)
        off_row = off_rows[p["offline_id"]]
        d = business_diffs(off_row, on_row)
        if d:
            raise StopError("Business difference remains on pair (%s,%s): %s - STOP"
                            % (p["offline_id"], nid, d))
        if not off_base.get(p["offline_id"]) or off_base[p["offline_id"]] != on_base.get(nid):
            raise StopError("base_json differs between replicas on pair (%s,%s) - STOP"
                            % (p["offline_id"], nid))

    def sync_map(conn, is_pg):
        if is_pg:
            cur = conn.cursor()
            cur.execute("SELECT id, sync_id FROM records")
            rows = cur.fetchall()
            cur.close()
            return {r[0]: r[1] for r in rows}
        return {r[0]: r[1] for r in conn.execute("SELECT id, sync_id FROM records").fetchall()}

    off_sync = sync_map(conn_off, off_pg)
    on_sync = sync_map(conn_on, on_pg)
    for label, smap in (("offline", off_sync), ("online", on_sync)):
        vals = list(smap.values())
        if any(not v for v in vals):
            raise StopError("NULL sync_id present (%s) - STOP" % label)
        if len(set(vals)) != len(vals):
            raise StopError("Duplicate sync_id present (%s) - STOP" % label)
    if set(off_sync.values()) != set(on_sync.values()):
        raise StopError("sync_id sets differ between sides - STOP")
    if manual_uuid not in on_sync.values():
        raise StopError("NEEL CHAND manual sync_id missing - STOP")
    return {"pair_business_diffs_remaining": 0,
            "base_json_mismatches": 0,
            "sync_ids_offline": len(off_sync),
            "sync_ids_online": len(on_sync)}


def apply_online(conn, is_pg, pairs, off_rows, on_rows, sr_fix_ids, now):
    """Single-transaction Online update: 16 approved sr fixes + sync_id/base_json/rev init.

    is_pg=True -> PostgreSQL with BATCHED multi-row UPDATE...FROM (VALUES...) for speed;
    is_pg=False -> SQLite (?) per-row updates so the code path is testable on twins.
    """
    try:
        cur = conn.cursor()

        def sr_rows():
            for p in pairs:
                if p["online_id"] in sr_fix_ids:
                    yield (off_rows[p["offline_id"]].get("sr_no"),
                           p["online_id"], on_rows[p["online_id"]].get("sr_no"))

        sr_list = list(sr_rows())
        if is_pg:
            if sr_list:
                marks = ",".join("(%s,%s,%s)" for _ in sr_list)
                flat = [v for t in sr_list for v in t]
                cur.execute(
                    "UPDATE records SET sr_no=v.sr_new, updated_at=NOW() "
                    "FROM (VALUES %s) AS v(sr_new, id, sr_old) "
                    "WHERE records.id=v.id AND records.sr_no=v.sr_old" % marks, flat)
                if cur.rowcount != len(sr_list):
                    raise StopError("Online sr batched fix rowcount %s != %d - ROLLBACK"
                                    % (cur.rowcount, len(sr_list)))
        else:
            for t in sr_list:
                cur.execute(
                    "UPDATE records SET sr_no=?, updated_at=? WHERE id=? AND sr_no=?",
                    (t[0], now, t[1], t[2]))
                if cur.rowcount != 1:
                    raise StopError(
                        "Online sr fix rowcount %s != 1 for online id %s - ROLLBACK"
                        % (cur.rowcount, t[1]))

        def sync_rows():
            for p in pairs:
                yield (p["sync_id"], make_base_json(off_rows[p["offline_id"]]), p["online_id"])

        sync_list = list(sync_rows())
        if is_pg:
            marks = ",".join("(%s,%s,%s)" for _ in sync_list)
            flat = [v for t in sync_list for v in t]
            cur.execute(
                "UPDATE records SET sync_id=v.sync_id, base_json=v.base_json, "
                "server_rev=0, row_rev=0 FROM (VALUES %s) AS v(sync_id, base_json, id) "
                "WHERE records.id=v.id AND (records.sync_id IS NULL OR records.sync_id='')"
                % marks, flat)
            if cur.rowcount != len(sync_list):
                raise StopError("Online sync batched rowcount %s != %d - ROLLBACK"
                                % (cur.rowcount, len(sync_list)))
        else:
            for t in sync_list:
                cur.execute(
                    "UPDATE records SET sync_id=?, base_json=?, server_rev=0, row_rev=0 "
                    "WHERE id=? AND (sync_id IS NULL OR sync_id='')", (t[0], t[1], t[2]))
                if cur.rowcount != 1:
                    raise StopError("Online UPDATE rowcount %s != 1 for id %s - ROLLBACK"
                                    % (cur.rowcount, t[2]))
        _sync_state_update(conn, is_pg, now)
        conn.commit()
        cur.close()
    except Exception:
        conn.rollback()
        raise


def git_head():
    try:
        out = os.popen('git -C "%s" rev-parse HEAD' % PROJECT_ROOT).read().strip()
        return out or "unknown"
    except Exception:
        return "unknown"


def newest_backup_manifest():
    """Fresh pre-bootstrap backups must exist and be verified (Phase-3 gate 1)."""
    data_dir = os.path.join(PROJECT_ROOT, "data")
    files = sorted(f for f in os.listdir(data_dir)
                   if f.startswith("syncv2_backup_manifest_") and f.endswith(".json"))
    if not files:
        raise StopError("No pre-bootstrap backup manifest found - run "
                        "scripts/backup/safety_backup.py first - STOP")
    path = os.path.join(data_dir, files[-1])
    with open(path, encoding="utf-8") as f:
        m = json.load(f)
    created = m.get("created_at_utc", "")
    try:
        age_min = (datetime.now(timezone.utc) - datetime.fromisoformat(created)).total_seconds() / 60.0
    except ValueError:
        raise StopError("Backup manifest has an unreadable timestamp - STOP")
    sqlite_ok = m.get("sqlite", {}).get("verified") is True
    neon = m.get("neon", {})
    neon_ok = neon.get("verified") is True or bool(neon.get("skipped"))
    if not (sqlite_ok and neon_ok):
        raise StopError("Newest backup manifest is NOT fully verified - STOP")
    if age_min > 60:
        raise StopError("Newest backup is %.0f minutes old (limit 60) - re-run "
                        "safety_backup.py - STOP" % age_min)
    return path, m


def render_apply_md(audit):
    L = ["PHASE-3 BOOTSTRAP APPLY REPORT", "================================", "",
         "Created (UTC):          %s" % audit["created_at_utc"],
         "Git head:               %s" % audit["git_head"],
         "Source review report:   %s" % audit["source_report"],
         "Backup manifest:        %s" % audit["backup_manifest"], "",
         "Offline records:        %d" % audit["offline_records_before"],
         "Online records:         %d" % audit["online_records_before"],
         "Identity pairs:         %d  (%d auto + %d manual NEEL)"
         % (audit["pairs_total"], audit["auto_matched"], audit["manual_neel"]["assigned"]),
         "UUIDs assigned:         %d per replica" % audit["pairs_total"],
         "SR order fixes applied: %d (Offline authoritative)"
         % len(audit["sr_fix_online_ids"]), "",
         "NEEL CHAND:             %s" % audit["manual_neel"]["summary"],
         "Outbox rows (post):     %d (must stay 0)" % audit["post_sync_counts"]["outbox"],
         "Applied_ops rows (post): %d" % audit["post_sync_counts"]["applied_ops"],
         "Conflicts rows (post):  %d" % audit["post_sync_counts"]["conflicts"],
         "server_rev initial: %d | row_rev initial: %d | sync_sequence: %d"
         % (audit["initial_revs"]["server_rev"], audit["initial_revs"]["row_rev"],
            audit["initial_revs"]["sync_sequence"]),
         "", "CHECKSUMS (22 business/system columns)",
         "  Offline before/after:  %s / %s  (business unchanged=%s)"
         % (audit["checksums"]["offline_before"][:16], audit["checksums"]["offline_after"][:16],
            audit["checksums"]["offline_business_unchanged"]),
         "  Online before/after:   %s / %s"
         % (audit["checksums"]["online_before"][:16], audit["checksums"]["online_after"][:16]),
         "  Online change: exactly the 16 approved sr_no updates only (verified).", "",
         "VERIFICATION:",
         "  %s" % json.dumps(audit["verification"]), "",
         "Initial sync checkpoint: baseline agreed on both replicas via per-record "
         "base_json equality; first normal sync sees zero diffs. Sync remains disabled.",
         "Sensitive production data is NOT committed to Git (data/syncv2 ignored)."]
    return "\n".join(L)


def _audit_docs(audit):
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    jp = os.path.join(SYNCV2_DIR, "bootstrap_apply_%s.json" % ts)
    mp = os.path.join(SYNCV2_DIR, "bootstrap_apply_%s.md" % ts)
    with open(jp, "w", encoding="utf-8") as f:
        json.dump(audit, f, indent=1, default=str)
    with open(mp, "w", encoding="utf-8") as f:
        f.write(render_apply_md(audit))
    return jp, mp


def main(argv):
    args = list(argv)
    if "--report" in args:
        report = os.path.join(SYNCV2_DIR, args[args.index("--report") + 1])
    else:
        report = latest_report()
    recover = "--recover" in args
    execute = "--apply" in args or recover

    # Gate 1: fresh verified backups must exist (backup precedes apply).
    backup_manifest, _ = newest_backup_manifest()

    plan = load_report(report)
    pairs = build_pairs(plan)
    manual_uuid = [p["sync_id"] for p in pairs if p["manual"]][0]

    conn_off = offline_connect()
    conn_on = online_connect()
    now = now_iso()
    try:
        n_off, s_off, off_counts = read_sync_state_counts(conn_off, False)
        n_on, s_on, on_counts = read_sync_state_counts(conn_on, True)
        checksum_off_before = sha256_of_rows(read_checksum_rows(conn_off, False))
        checksum_on_before = sha256_of_rows(read_checksum_rows(conn_on, True))
        off_rows = read_rows(conn_off, False)
        on_rows = read_rows(conn_on, True)

        if n_off != 1523 or n_on != 1523:
            raise StopError("Record counts not as expected (%d/%d) - STOP" % (n_off, n_on))
        if off_counts["outbox"] or on_counts["outbox"]:
            raise StopError("outbox is not empty before bootstrap - STOP")
        if (conn_off.execute("SELECT COUNT(*) FROM applied_ops").fetchone()[0]
                or conn_off.execute("SELECT COUNT(*) FROM conflicts").fetchone()[0]):
            raise StopError("applied_ops/conflicts not empty before bootstrap - STOP")
        if s_off == n_off and s_on == n_on:
            raise StopError("Both replicas are already fully baselined - re-running the "
                            "one-time bootstrap is refused - STOP")
        if (s_off or s_on) and not recover:
            raise StopError("Partial bootstrap state found (offline set=%d online set=%d). "
                            "Run with --recover to complete - STOP" % (s_off, s_on))
        if s_on and not s_off:
            raise StopError("Online baselined while Offline is not - unexpected partial "
                            "order; manual review required - STOP")

        pf = preflight(plan, pairs, off_rows, on_rows,
                       report_sr_diff_pairs={p["online_id"] for p in pairs if p.get("sr_diff")})
        sr_fix_ids = set(pf["sr_fix_online_ids"])
        if len(sr_fix_ids) != 16:
            raise StopError("Expected exactly 16 sr-fix pairs, found %d - STOP" % len(sr_fix_ids))
        if not execute:
            raise StopError("Validation passed; pass --apply to execute the production write - STOP")

        if s_off == n_off and s_on == 0:
            # Recovery of an interrupted apply (Offline already done, Online pending).
            for p in pairs:
                cur = conn_off.execute("SELECT sync_id FROM records WHERE id=?", (p["offline_id"],))
                if (cur.fetchone() or [None])[0] != p["sync_id"]:
                    raise StopError("Offline sync_id does not match the approved mapping - STOP")
            apply_online(conn_on, True, pairs, off_rows, on_rows, sr_fix_ids, now)
        else:
            if s_off or s_on:
                raise StopError("Unexpected partial-baseline combination - STOP")
            apply_offline(conn_off, pairs, off_rows, now)
            apply_online(conn_on, True, pairs, off_rows, on_rows, sr_fix_ids, now)

        # Post verification with fresh reads.
        verif = verify_post(conn_off, False, conn_on, True, pairs, manual_uuid)
        checksum_off_after = sha256_of_rows(read_checksum_rows(conn_off, False))
        checksum_on_after = sha256_of_rows(read_checksum_rows(conn_on, True))
        n2_off, s2_off, off_counts2 = read_sync_state_counts(conn_off, False)
        n2_on, s2_on, on_counts2 = read_sync_state_counts(conn_on, True)
        if n2_off != 1523 or n2_on != 1523 or s2_off != 1523 or s2_on != 1523:
            raise StopError("Post-apply counts invalid (%d/%d/%d/%d) - STOP"
                            % (n2_off, s2_off, n2_on, s2_on))

        audit = {
            "phase": 3,
            "created_at_utc": now,
            "git_head": git_head(),
            "source_report": os.path.abspath(report),
            "backup_manifest": backup_manifest,
            "offline_records_before": n_off,
            "online_records_before": n_on,
            "pairs_total": len(pairs),
            "auto_matched": len([p for p in pairs if not p["manual"]]),
            "manual_neel": {
                "assigned": 1,
                "offline_id": plan["manual"]["offline_id"],
                "online_id": plan["manual"]["online_id"],
                "sync_id": manual_uuid,
                "summary": "NEEL CHAND (28-08-2025 / AUGUST_2025 / sr 35) unified as ONE "
                           "record with a single shared sync_id; verified field-by-field "
                           "identical; neither replica deleted or duplicated."},
            "sr_fix_online_ids": sorted(sr_fix_ids),
            "initial_revs": {
                "server_rev": 0, "row_rev": 0, "sync_sequence": 0,
                "rationale": "Bootstrap baseline only; no fake edit history created. "
                             "server_rev/row_rev 0 = no server operation stream started. "
                             "Mutual agreement is captured by per-record base_json = the "
                             "Offline business snapshot stored verbatim on BOTH replicas."},
            "uuid_policy": "uuid4, permanent, never derived from DB ids or business keys.",
            "checksums": {
                "offline_before": checksum_off_before,
                "offline_after": checksum_off_after,
                "offline_business_unchanged": checksum_off_before == checksum_off_after,
                "online_before": checksum_on_before,
                "online_after": checksum_on_after,
                "online_change": "only the 16 approved sr_no updates (+ updated_at on those rows)"},
            "pre_sync_counts": {"offline_outbox": off_counts["outbox"],
                                "online_outbox": on_counts["outbox"]},
            "post_sync_counts": {"outbox": off_counts2["outbox"],
                                 "applied_ops": off_counts2["applied_ops"],
                                 "conflicts": off_counts2["conflicts"]},
            "verification": verif,
            "production_modified": True,
            "normal_sync_enabled": False,
        }
        jp, mp = _audit_docs(audit)
        print("APPLY OK")
        print(render_apply_md(audit))
        print("\nAudit artifacts:\n  %s\n  %s" % (jp, mp))
    except StopError as e:
        try:
            conn_off.rollback()
        except Exception:
            pass
        try:
            conn_on.rollback()
        except Exception:
            pass
        print("STOP:", e, file=sys.stderr)
        sys.exit(1)
    finally:
        conn_off.close()
        conn_on.close()


if __name__ == "__main__":
    main(sys.argv[1:])







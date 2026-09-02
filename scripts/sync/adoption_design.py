"""adoption_design.py - Phase 7C controlled NULL/blank sync_id ADOPTION DESIGN.

DO NOT RUN AGAINST PRODUCTION. Production adoption is NOT authorized until the
Phase-8 cutover is explicitly approved. This module exists so the procedure can be
reviewed and proven against synthetic twin databases.

Semantics (identical to Phase-3 bootstrap rules):
  - identity is established ONLY by an exact deterministic counterpart check
    (full business-field equality + nonblank serial/invoice strong keys; the
    serial literal 'NA' is a NORMAL VALUE and never an identity key);
  - Offline is authoritative for the business snapshot and SR;
  - base_json = the Offline business snapshot, identical on both replicas;
  - server_rev = 0 and row_rev = 0 (no fake edit history), tombstone = false;
  - NO outbox row, NO conflict, NO duplicate row are created by adoption;
  - sync_id is supplied by the caller (uuid4 generated once and persisted in the
    runbook audit state) so reruns are deterministic and rerun-safe.

Enforced guarantees: idempotent, transactional per replica, rollback-safe,
deterministic, rerun-safe, and incapable of silently merging a different record.
"""
import json

from syncv2 import protocol as P
from syncv2 import store as S
from syncv2 import merge as M

_COLS = ["id", "sr_no", "bid_date", "invoice_no", "name", "xcell", "product",
         "serial_no", "price", "emi", "di", "bid", "dp_taken", "scheme",
         "actual_product", "given_prod_price", "phone", "alt_phone", "month",
         "remarks", "sync_id", "server_rev", "row_rev", "base_json",
         "deleted_at", "created_at", "updated_at"]

# Classifier categories.
EXACT_COUNTERPART = 1
LIKELY_REVIEW = 2
INDEPENDENT_NEW = 3
DUPLICATE = 4
AMBIGUOUS = 5


def _ph(is_pg):
    return "%s" if is_pg else "?"


def _norm(v):
    return str(v or "").strip().upper()


def row_by_id(conn, is_pg, record_id):
    rows = S.fetch_all(conn, is_pg,
                       "SELECT %s FROM records WHERE id=%s"
                       % (",".join(_COLS), _ph(is_pg)), (record_id,))
    return dict(zip(_COLS, rows[0])) if rows else None


def _business(row):
    return {f: row.get(f) for f in P.BUSINESS_FIELDS}


def _business_equal(a, b):
    return all(M.values_equal(f, a.get(f), b.get(f))
               for f in P.BUSINESS_FIELDS)

def strong_match_ok(off_row, on_row):
    """Category-1 exact-counterpart test.

    Full business-field equality plus nonblank serial/invoice on both sides.
    serial 'NA' is never an identity key. Returns bool.
    """
    if off_row is None or on_row is None:
        return False
    if off_row.get("deleted_at") or on_row.get("deleted_at"):
        return False
    serial = _norm(off_row.get("serial_no"))
    invoice = _norm(off_row.get("invoice_no"))
    if not serial or serial == "NA":
        return False
    if not invoice:
        return False
    if serial != _norm(on_row.get("serial_no")):
        return False
    if invoice != _norm(on_row.get("invoice_no")):
        return False
    return _business_equal(off_row, on_row)



def classify_null_sync_rows(rows_a, rows_b):
    """Classify every NULL/blank-sync row on side A against side B's live rows.

    Deterministic evidence only. Never uses invoice alone or BID alone as
    identity. Returns list of dicts with id/category/reason/candidates.
    """
    b_live = [r for r in rows_b if not r.get("deleted_at")]
    results = []
    for r in rows_a:
        if r.get("sync_id") and str(r["sync_id"]).strip():
            continue
        serial = _norm(r.get("serial_no"))
        invoice = _norm(r.get("invoice_no"))
        exact = []
        likely = []
        for cand in b_live:
            if strong_match_ok(r, cand):
                exact.append(cand)
            else:
                c_serial = _norm(cand.get("serial_no"))
                c_invoice = _norm(cand.get("invoice_no"))
                key_hit = 0
                if serial and serial != "NA" and serial == c_serial:
                    key_hit += 2
                if invoice and invoice == c_invoice:
                    key_hit += 1
                field_eq = _business_equal(r, cand)
                if key_hit >= 2 or (field_eq and invoice and invoice == c_invoice
                                    and (not serial or serial == "NA")):
                    likely.append(cand)
        same_side_dup = [
            o for o in rows_a
            if o.get("id") != r.get("id") and not o.get("deleted_at")
            and serial and serial != "NA"
            and _norm(o.get("serial_no")) == serial]
        if len(exact) == 1 and not same_side_dup:
            cat, reason = EXACT_COUNTERPART, "exact deterministic counterpart"
        elif same_side_dup:
            cat, reason = DUPLICATE, "same-side duplicate with another live row"
        elif len(exact) > 1:
            cat, reason = AMBIGUOUS, "multiple exact counterparts"
        elif len(likely) == 1:
            cat, reason = LIKELY_REVIEW, "strong-key candidate; requires review"
        elif len(likely) > 1:
            cat, reason = AMBIGUOUS, "multiple strong-key candidates"
        else:
            cat, reason = INDEPENDENT_NEW, "no counterpart found on other side"
        results.append({
            "id": r.get("id"),
            "serial_no": r.get("serial_no"),
            "invoice_no": r.get("invoice_no"),
            "bid": r.get("bid"),
            "name": r.get("name"),
            "category": cat,
            "reason": reason,
            "exact_candidates": [x.get("id") for x in exact],
            "likely_candidates": [x.get("id") for x in likely],
            "same_side_duplicates": [x.get("id") for x in same_side_dup],
        })
    return results

def _apply_side(conn, is_pg, record_id, sync_id, base_json):
    """Single-transaction adoption UPDATE for one replica. Rolls back on error."""
    q = _ph(is_pg)
    sql = ("UPDATE records SET sync_id=%s, base_json=%s, server_rev=0, row_rev=0 "
           "WHERE id=%s AND (sync_id IS NULL OR sync_id='')" % (q, q, q))
    try:
        n = S.execute(conn, is_pg, sql, (sync_id, base_json, record_id))
        conn.commit()
        return n
    except Exception:
        conn.rollback()
        raise


def adopt_pair(off_conn, on_conn, off_id, on_id, sync_id, off_pg=False,
               on_pg=False, base_json=None):
    """Adopt ONE NULL-sync identity across two replicas.

    off_conn/on_conn: open connections (Offline, Online/Neon). off_pg/on_pg:
    backend flags. sync_id: caller-supplied uuid4 (generate once and persist).
    base_json: optional authoritative Offline snapshot; when None it is built
    from the Offline row (Offline-authoritative).

    Returns a summary dict. Raises on any precondition violation. Rollback-safe:
    a failure on either side leaves a recoverable half-state; re-running with the
    SAME sync_id completes the other side. Never silently merges a different
    record (strict strong_match_ok required for the write path).
    """
    import uuid as _uuid
    sync_id = str(sync_id)
    _uuid.UUID(sync_id)  # validate uuid4-shaped input
    off_row = row_by_id(off_conn, off_pg, off_id)
    on_row = row_by_id(on_conn, on_pg, on_id)
    if off_row is None or on_row is None:
        raise ValueError("adoption requires both rows to exist")
    if off_row.get("deleted_at") or on_row.get("deleted_at"):
        raise ValueError("adoption refuses tombstoned rows")
    if not strong_match_ok(off_row, on_row):
        raise ValueError("adoption refuses non-identical pair (strong match "
                         "failed) - will not silently merge a different record")

    def state(row):
        return str(row.get("sync_id") or "").strip()

    off_state, on_state = state(off_row), state(on_row)
    if off_state == sync_id and on_state == sync_id:
        return {"result": "noop", "sync_id": sync_id, "offline_id": off_id,
                "online_id": on_id}
    for label, st in (("offline", off_state), ("online", on_state)):
        if st and st != sync_id:
            other = off_id if label == "offline" else on_id
            raise ValueError("adoption refuses conflicting sync_id on %s "
                             "(id %r already has %s)" % (label, other, st))

    if base_json is None:
        biz = _business(off_row)
        base_json = json.dumps(biz, sort_keys=True, default=str,
                               ensure_ascii=True, separators=(",", ":"))
    summary = {"result": "adopted", "sync_id": sync_id, "offline_id": off_id,
               "online_id": on_id}
    if not off_state:
        n = _apply_side(off_conn, off_pg, off_id, sync_id, base_json)
        if n != 1:
            raise RuntimeError("offline adoption rowcount %s != 1" % n)
        summary["offline_applied"] = True
    if not on_state:
        n = _apply_side(on_conn, on_pg, on_id, sync_id, base_json)
        if n != 1:
            raise RuntimeError("online adoption rowcount %s != 1" % n)
        summary["online_applied"] = True
    return summary


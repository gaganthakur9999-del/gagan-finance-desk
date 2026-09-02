"""
syncv2/merge.py - deterministic three-way merge primitives (pure functions).

Three-way model for every relevant field, given BASE (ancestor from base_json),
OFFLINE (current local), ONLINE (current server/remote):

    offline == base and online != base  -> Online changed        (accept online)
    offline != base and online == base  -> Offline changed       (accept offline)
    both == base                         -> unchanged
    both != base, offline == online      -> safe convergence
    both != base, offline != online      -> CONFLICT per field rules

No use of updated_at as authority. All functions are pure and testable.
"""
import re
from datetime import datetime

from .protocol import (
    BUSINESS_FIELDS, SAFE_MERGE_FIELDS, FINANCIAL_FIELDS, SERIAL_FIELD,
    BID_FIELD, INVOICE_FIELD, SR_FIELD, MONTH_FIELD,
)

_WS = re.compile(r"\s+")
_DATE_FMTS = ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d")
_MONTHS = ["JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE",
           "JULY", "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER"]


# ---------------------------------------------------------------- normalization
def norm_text(v) -> str:
    if v is None:
        return ""
    return _WS.sub(" ", str(v)).strip().upper()


def norm_num(v):
    if v is None:
        return None
    s = str(v).replace(",", "").strip()
    if s == "":
        return None
    try:
        return round(float(s), 2)
    except (TypeError, ValueError):
        return None


def norm_date(v):
    if v is None:
        return ""
    s = str(v).strip()
    for fmt in _DATE_FMTS:
        try:
            return datetime.strptime(s[:10], fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return norm_text(s)


def values_equal(field, a, b) -> bool:
    if field in ("price", "emi", "di", "dp_taken", "given_prod_price"):
        na, nb = norm_num(a), norm_num(b)
        if na is None and nb is None:
            return (a in (None, "") and b in (None, "")) or norm_text(a) == norm_text(b)
        return na == nb
    if field == "bid_date":
        return norm_date(a) == norm_date(b)
    if field == "month":
        return norm_text(a) == norm_text(b)
    return norm_text(a) == norm_text(b)


def month_from_bid_date(bid_date):
    """Month label derived from bid_date (e.g. 15-08-2026 -> AUGUST_2026).

    Used to keep `month` derived; the engine never treats month as independent
    authoritative business data. Returns '' when the date is unparseable.
    """
    d = norm_date(bid_date)
    if not d or len(d) != 10:
        return ""
    year, mth = d[:4], int(d[5:7])
    return "%s_%s" % (_MONTHS[mth - 1], year)


# ---------------------------------------------------------------- field actions
FIELD_ACTION_UNCHANGED = "unchanged"
FIELD_USE_ONLINE = "use_online"
FIELD_USE_OFFLINE = "use_offline"
FIELD_CONVERGED = "converged"
FIELD_CONFLICT = "conflict"


def classify_field(field, base, offline, online):
    """Return (action, offline_changed, online_changed)."""
    b, o, n = base.get(field), offline.get(field), online.get(field)
    off_ch = not values_equal(field, b, o)
    on_ch = not values_equal(field, b, n)
    if not off_ch and not on_ch:
        return FIELD_ACTION_UNCHANGED, False, False
    if off_ch and not on_ch:
        return FIELD_USE_OFFLINE, True, False
    if on_ch and not off_ch:
        return FIELD_USE_ONLINE, False, True
    if values_equal(field, o, n):
        return FIELD_CONVERGED, True, True
    return FIELD_CONFLICT, True, True


def field_is_soft_conflict(field) -> bool:
    """True when a divergent field is a genuine conflict per the approved rules.

    Safe mergeable fields that BOTH sides changed to different values are merged
    by KEEPING BOTH records' differing values? No - a record has one value per
    field. The approved rules say safe fields are *independently mergeable* across
    FIELDS (field A from offline + field B from online), and financial/serial/
    delete/sr are same-field conflicts. For safe fields where both sides changed
    the SAME field differently we still need a deterministic answer: the engine
    treats them as soft conflicts too (never silently discard one side) - the
    resolution engine then chooses deterministically. This is stricter than
    necessary but never loses data.
    """
    return field in FINANCIAL_FIELDS or field == SERIAL_FIELD


def _soft_conflict_fields(base, offline, online):
    """Divergent same-safe-field cases (both sides changed differently)."""
    out = []
    for f in sorted(SAFE_MERGE_FIELDS | {BID_FIELD}):
        action, _, _ = classify_field(f, base, offline, online)
        if action == FIELD_CONFLICT:
            out.append(f)
    return out


def merge_business(base, offline, online, merge_sr_from="offline"):
    """Three-way merge of business fields (sr/invoice/month handled separately).

    Returns:
      resolved    - merged field values (soft-conflict fields EXCLUDED)
      field_actions
      conflicts   - [{kind, field, base, offline, online}]
      month_resolved
    """
    resolved, actions, conflicts = {}, {}, []
    for f in sorted(set(BUSINESS_FIELDS) - {"sr_no", "invoice_no", "month"}):
        action, _, _ = classify_field(f, base, offline, online)
        actions[f] = action
        if action == FIELD_CONFLICT:
            conflicts.append({
                "kind": "financial" if f in FINANCIAL_FIELDS
                        else ("serial" if f == SERIAL_FIELD else "field"),
                "field": f, "base": base.get(f),
                "offline": offline.get(f), "online": online.get(f)})
            continue
        if action in (FIELD_USE_OFFLINE, FIELD_CONVERGED):
            resolved[f] = offline.get(f)
        elif action == FIELD_USE_ONLINE:
            resolved[f] = online.get(f)
        elif action == FIELD_ACTION_UNCHANGED:
            resolved[f] = base.get(f)
    # sr_no ordering handled at month scope by the caller.
    resolved["sr_no"] = (online if merge_sr_from == "online" else offline).get("sr_no")
    month = month_from_bid_date(resolved.get("bid_date"))
    resolved["month"] = (month or offline.get("month") or online.get("month")
                         or base.get("month"))
    return {"resolved": resolved, "field_actions": actions,
            "conflicts": conflicts, "month_resolved": resolved["month"]}


# ---------------------------------------------------------------- invoice engine
def normalized_invoice(v) -> str:
    return norm_text(v)


def detect_invoice_collision(new_invoice, owner_sync_id, existing_owners):
    """existing_owners: normalized invoice -> set(sync_id).

    Returns a review object when a different sync_id already owns the same
    normalized nonblank invoice. Never merges records by invoice number.
    """
    inv = normalized_invoice(new_invoice)
    if not inv:
        return None
    others = (existing_owners.get(inv, set()) or set()) - {owner_sync_id}
    if others:
        return {"kind": "invoice_collision", "invoice": new_invoice,
                "owner_sync_id": owner_sync_id, "other_sync_ids": sorted(others)}
    return None


# ---------------------------------------------------------------- SR ordering
def sr_ordering(sr_rows):
    """sr_rows: (sync_id, sr_no) -> ordered sync_id list by sr_no."""
    return [sid for sid, _ in sorted(sr_rows, key=lambda x: (int(x[1] or 0), x[0]))]


def reconcile_sr(base_seq, offline_seq, online_seq):
    """Ordering reconciliation for ONE month scope (ordered sync_id lists)."""
    if offline_seq == base_seq and online_seq == base_seq:
        return {"action": "unchanged", "base_seq": base_seq,
                "offline_seq": offline_seq, "online_seq": online_seq}
    if offline_seq != base_seq and online_seq == base_seq:
        return {"action": "use_offline", "base_seq": base_seq,
                "offline_seq": offline_seq, "online_seq": online_seq}
    if online_seq != base_seq and offline_seq == base_seq:
        return {"action": "use_online", "base_seq": base_seq,
                "offline_seq": offline_seq, "online_seq": online_seq}
    if offline_seq == online_seq:
        return {"action": "use_offline", "base_seq": base_seq,
                "offline_seq": offline_seq, "online_seq": online_seq}
    return {"action": "conflict", "base_seq": base_seq,
            "offline_seq": offline_seq, "online_seq": online_seq}


# ---------------------------------------------------------------- tombstones
def reconcile_tombstone(base_alive, off_alive, on_alive, off_edit, on_edit):
    """Alive states True/False; *edit = business fields changed vs base."""
    if not base_alive and not off_alive and not on_alive:
        return "unchanged", "both_deleted"
    if not base_alive and (off_alive or on_alive):
        return "conflict", "resurrect"
    if base_alive and off_alive and on_alive:
        return "unchanged", "alive"
    if base_alive and not off_alive and not on_alive:
        return "apply", "both"
    if base_alive and not off_alive:
        return "apply", "offline"
    if base_alive and not on_alive:
        return "apply", "online"
    return "conflict", "unexpected"


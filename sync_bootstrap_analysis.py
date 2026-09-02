"""
sync_bootstrap_analysis.py — Phase-2 READ-ONLY Initial Synchronization Review engine.

PURE ANALYSIS. It never writes to a database. It consumes row dicts loaded from
SQLite (offline) and PostgreSQL/Neon (online) and produces:
  - matched pairs with categorical confidence (HIGH/MEDIUM/LOW)
  - ambiguous matches
  - offline-only / online-only records
  - field-level differences + categories (identical / offline-differs / online-differs /
    both-differ / potential-conflict / invoice-collision / sr-order-difference /
    serial-difference / bid-difference)
  - a PROPOSED sync_id mapping (report artifact only - never written to any DB)
  - a baseline preview (offline-authoritative) for every differing matched pair

Normalization (comparison only - original stored values are never altered):
  - bid / invoice_no / serial_no / name / month / remarks: trimmed, uppercased;
    internal whitespace collapsed for name.
  - numeric fields (price, emi, di, dp_taken, given_prod_price): commas stripped,
    parsed to float.
  - bid_date: parsed to a canonical date where possible.
Created_at/updated_at are treated as system fields (they legitimately differ across
independent databases) and reported separately, NOT as business differences.
"""
import re
import uuid
from datetime import datetime
from typing import Dict, List, Optional

BUSINESS_FIELDS = [
    "sr_no", "bid_date", "invoice_no", "name", "xcell", "product", "serial_no",
    "price", "emi", "di", "bid", "dp_taken", "scheme", "actual_product",
    "given_prod_price", "phone", "alt_phone", "month", "remarks",
]
SYSTEM_FIELDS = ["created_at", "updated_at"]
ALL_FIELDS = BUSINESS_FIELDS + SYSTEM_FIELDS

NUMERIC_FIELDS = {"price", "emi", "di", "dp_taken", "given_prod_price"}
# Fields where a "both sides present but different" difference is a business conflict.
SENSITIVE_FIELDS = {"sr_no", "bid_date", "invoice_no", "price", "emi", "di",
                    "dp_taken", "given_prod_price"}

_WS = re.compile(r"\s+")


def norm_text(v) -> str:
    """Trim + uppercase (no fuzzy matching)."""
    if v is None:
        return ""
    return _WS.sub(" ", str(v)).strip().upper()


def norm_num(v):
    """Comma-stripped float, or None when unparseable."""
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
    """Canonical ISO date string, or the trimmed text when unparseable."""
    if v is None:
        return ""
    s = str(v).strip()
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:10], fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return s.upper()


def field_equal(field: str, off, on) -> bool:
    a, b = off.get(field), on.get(field)
    if field in NUMERIC_FIELDS:
        na, nb = norm_num(a), norm_num(b)
        if na is None and nb is None:
            # Both blank or both unparseable -> compare as text.
            return (a in (None, "") and b in (None, "")) or norm_text(a) == norm_text(b)
        return na == nb
    if field == "bid_date":
        return norm_date(a) == norm_date(b)
    return norm_text(a) == norm_text(b)


def row_key(row: Dict) -> int:
    return int(row.get("id"))


def _is_blank(v) -> bool:
    return v is None or str(v).strip() == ""


def _index(rows: List[Dict], keyfn) -> Dict:
    """key -> ordered list of row ids (rows must have an 'id')."""
    out: Dict[str, list] = {}
    for r in rows:
        k = keyfn(r)
        if k:
            out.setdefault(k, []).append(row_key(r))
    return out


def _norm_bid(r):
    return norm_text(r.get("bid"))


def _norm_inv(r):
    return norm_text(r.get("invoice_no"))


def _norm_serial(r):
    return norm_text(r.get("serial_no"))


def _inv_serial_key(r):
    i, s = _norm_inv(r), _norm_serial(r)
    if i and s:
        return i + "|" + s
    return ""


def _inv_only_key(r):
    i = _norm_inv(r)
    return i if i else ""


def _fallback_key(r):
    name = norm_text(r.get("name"))
    month = norm_text(r.get("month"))
    price = norm_num(r.get("price"))
    if name and month and price is not None:
        return name + "|" + month + "|" + str(price)
    return ""


def _corroboration(off, on) -> Dict:
    keys = ["name", "month", "price", "invoice_no", "serial_no", "bid_date"]
    return {k: field_equal(k, off, on) for k in keys}


def _pair(off_id, on_id, level, off_status, on_status, matches):
    off_status[off_id] = "matched"
    on_status[on_id] = "matched"
    matches.append({"off_id": off_id, "on_id": on_id, "level": level})


def _make_ambiguous(side_ids, level, reason, ambiguous, status, side_label):
    for sid in side_ids:
        if status[sid] == "pending":
            status[sid] = "ambiguous"
            ambiguous.append({"side": side_label, "ids": [sid],
                              "level": level, "reason": reason})


def _run_level(level, keyfn, off_rows, on_rows, off_status, on_status, matches,
               ambiguous, label):
    """Generic unique cross-map level. Marks one-to-many keys as ambiguous."""
    from collections import OrderedDict

    def idx(rows, status):
        m = OrderedDict()
        for r in rows:
            if status[r["id"]] != "pending":
                continue
            k = keyfn(r)
            if k:
                m.setdefault(k, []).append(r["id"])
        return m

    off_m = idx(off_rows, off_status)
    on_m = idx(on_rows, on_status)
    for key, oids in list(off_m.items()):
        nids = on_m.get(key)
        if not nids:
            continue
        if len(oids) == 1 and len(nids) == 1:
            _pair(oids[0], nids[0], level, off_status, on_status, matches)
        else:
            _make_ambiguous(oids, level, "key maps to %d online row(s)" % len(nids),
                            ambiguous, off_status, "offline")
            _make_ambiguous(nids, level, "key maps to %d offline row(s)" % len(oids),
                            ambiguous, on_status, "online")


def reconcile(off_rows, on_rows) -> Dict:
    """Multi-level reconciliation. Pure analysis - never writes to a database."""
    off_by_id = {r["id"]: r for r in off_rows}
    on_by_id = {r["id"]: r for r in on_rows}
    off_status = {r["id"]: "pending" for r in off_rows}
    on_status = {r["id"]: "pending" for r in on_rows}
    matches, ambiguous = [], []

    _run_level("level1_bid", _norm_bid, off_rows, on_rows, off_status, on_status,
               matches, ambiguous, "L1 bid")
    _run_level("level2_invoice_serial", _inv_serial_key, off_rows, on_rows,
               off_status, on_status, matches, ambiguous, "L2 invoice+serial")
    _run_level("level2b_invoice", _inv_only_key, off_rows, on_rows, off_status,
               on_status, matches, ambiguous, "L2b invoice")
    _run_level("level3_fallback", _fallback_key, off_rows, on_rows, off_status,
               on_status, matches, ambiguous, "L3 name+month+price")

    off_only = [r["id"] for r in off_rows if off_status[r["id"]] == "pending"]
    on_only = [r["id"] for r in on_rows if on_status[r["id"]] == "pending"]

    _LEVEL_CONF = {"level1_bid": "HIGH", "level2_invoice_serial": "MEDIUM",
                   "level2b_invoice": "MEDIUM", "level3_fallback": "LOW"}
    for m in matches:
        off, on = off_by_id[m["off_id"]], on_by_id[m["on_id"]]
        diffs = [{"field": f, "offline": off.get(f), "online": on.get(f)}
                 for f in BUSINESS_FIELDS if not field_equal(f, off, on)]
        agree = _corroboration(off, on)
        n_agree = sum(1 for v in agree.values() if v)
        suspicious = n_agree == 0
        conf = "LOW" if (suspicious and _LEVEL_CONF[m["level"]] == "HIGH") else _LEVEL_CONF[m["level"]]
        m.update({
            "confidence": conf, "suspicious": suspicious, "corroboration_agree": n_agree,
            "diffs": diffs, "identical": not diffs,
            "categories": _categories(off, on, diffs),
        })

    return {"off_by_id": off_by_id, "on_by_id": on_by_id, "matches": matches,
            "ambiguous": ambiguous, "off_only": off_only, "on_only": on_only,
            "off_rows": off_rows, "on_rows": on_rows}


def _categories(off, on, diffs):
    """Classify a matched pair into report categories (may be several)."""
    cats = []
    if not diffs:
        cats.append("MATCHED_IDENTICAL")
        return cats
    diff_map = {d["field"]: d for d in diffs}
    if "sr_no" in diff_map:
        cats.append("SR_ORDER_DIFFERENCE")
    if "serial_no" in diff_map:
        cats.append("SERIAL_DIFFERENCE")
    if "bid" in diff_map:
        cats.append("BID_DIFFERENCE")
    if "invoice_no" in diff_map:
        cats.append("INVOICE_DIFFERENCE")
    both, off_only, on_only = [], [], []
    for d in diffs:
        f = d["field"]
        o, n = d["offline"], d["online"]
        o_blank = o is None or str(o).strip() == ""
        n_blank = n is None or str(n).strip() == ""
        if not o_blank and not n_blank:
            both.append(f)
        elif not o_blank and n_blank:
            off_only.append(f)
        else:
            on_only.append(f)
    if both:
        cats.append("BOTH_DIFFER")
        if any(f in SENSITIVE_FIELDS for f in both):
            cats.append("POTENTIAL_CONFLICT")
    if off_only and not both:
        cats.append("OFFLINE_DIFFERS")
    if on_only and not both:
        cats.append("ONLINE_DIFFERS")
    return cats


def invoice_collisions(res) -> List[Dict]:
    """Same normalized invoice attached to records that are NOT the same matched pair.

    NOTE: offline and online integer id spaces overlap, so each side is iterated
    separately (never decided via numeric membership in the other side's map).
    """
    off = {r["id"]: r for r in res["off_rows"]}
    on = {r["id"]: r for r in res["on_rows"]}
    pair_of = {m["off_id"]: m["on_id"] for m in res["matches"]}
    pair_on = {m["on_id"]: m["off_id"] for m in res["matches"]}
    by_inv: Dict[str, list] = {}

    def add(rid, r, side):
        k = norm_text(r.get("invoice_no"))
        if not k:
            return
        if side == "offline" and rid in pair_of:
            grp = ("pair", rid, pair_of[rid])
        elif side == "online" and rid in pair_on:
            grp = ("pair", pair_on[rid], rid)
        else:
            grp = ("row", side, rid)
        by_inv.setdefault(k, []).append(grp)

    for rid, r in off.items():
        add(rid, r, "offline")
    for rid, r in on.items():
        add(rid, r, "online")
    out = []
    for inv, groups in sorted(by_inv.items()):
        distinct = {("%s:%s:%s" % g) for g in groups}
        if len(distinct) > 1:
            out.append({"invoice": inv, "groups": [list(g) for g in groups]})
    return out


def possible_matches(res, limit_signals=6) -> List[Dict]:
    """Weak-signal suggestions for side-only rows (for manual review only)."""
    off = res["off_by_id"]
    on = res["on_by_id"]
    matched_off = {m["off_id"] for m in res["matches"]}
    matched_on = {m["on_id"] for m in res["matches"]}
    out = []
    for oid in res["off_only"]:
        r = off[oid]
        sig = []
        for nid, n in on.items():
            if nid in matched_on:
                continue
            hits = []
            if _norm_bid(r) and _norm_bid(r) == _norm_bid(n):
                hits.append("bid")
            if _norm_inv(r) and _norm_inv(r) == _norm_inv(n):
                hits.append("invoice")
            if norm_text(r.get("phone")) and norm_text(r.get("phone")) == norm_text(n.get("phone")):
                hits.append("phone")
            if (norm_text(r.get("name")) == norm_text(n.get("name"))
                    and norm_text(r.get("month")) == norm_text(n.get("month"))):
                hits.append("name+month")
            if hits:
                sig.append({"online_id": nid, "signals": hits})
        if sig:
            out.append({"side": "offline", "id": oid, "candidates": sig[:limit_signals]})
    for nid in res["on_only"]:
        r = on[nid]
        sig = []
        for oid, o in off.items():
            if oid in matched_off:
                continue
            hits = []
            if _norm_bid(r) and _norm_bid(r) == _norm_bid(o):
                hits.append("bid")
            if _norm_inv(r) and _norm_inv(r) == _norm_inv(o):
                hits.append("invoice")
            if norm_text(r.get("phone")) and norm_text(r.get("phone")) == norm_text(o.get("phone")):
                hits.append("phone")
            if (norm_text(r.get("name")) == norm_text(o.get("name"))
                    and norm_text(r.get("month")) == norm_text(o.get("month"))):
                hits.append("name+month")
            if hits:
                sig.append({"offline_id": oid, "signals": hits})
        if sig:
            out.append({"side": "online", "id": nid, "candidates": sig[:limit_signals]})
    return out


def _describe(r):
    return {
        "id": r.get("id"), "invoice": r.get("invoice_no"), "bid": r.get("bid"),
        "name": r.get("name"), "bid_date": r.get("bid_date"),
        "serial": r.get("serial_no"), "price": r.get("price"),
        "product": r.get("product"), "month": r.get("month"), "phone": r.get("phone"),
        "sr_no": r.get("sr_no"), "scheme": r.get("scheme"),
    }


def side_findings(rows):
    n_inv, n_ser, n_bid = {}, {}, {}
    blank = {"invoice": 0, "serial": 0, "bid": 0}
    sr_month = {}
    for r in rows:
        i = norm_text(r.get("invoice_no"))
        s = norm_text(r.get("serial_no"))
        b = norm_text(r.get("bid"))
        if not i:
            blank["invoice"] += 1
        else:
            n_inv.setdefault(i, []).append(r["id"])
        if not s:
            blank["serial"] += 1
        else:
            n_ser.setdefault(s, []).append(r["id"])
        if not b:
            blank["bid"] += 1
        else:
            n_bid.setdefault(b, []).append(r["id"])
        mth = norm_text(r.get("month"))
        sr_month.setdefault(mth, []).append(int(r.get("sr_no") or 0))
    return {
        "blank": blank,
        "dup_invoice": sorted({k for k, v in n_inv.items() if len(v) > 1}),
        "dup_serial": sorted({k for k, v in n_ser.items() if len(v) > 1}),
        "dup_bid": sorted({k for k, v in n_bid.items() if len(v) > 1}),
        "sr_month": {k: {"records": len(v), "unique": len(set(v)), "min": min(v),
                         "max": max(v), "gaps": sorted(set(range(min(v), max(v) + 1)) - set(v))}
                     for k, v in sorted(sr_month.items()) if v},
    }


def build_review(off_rows, on_rows) -> Dict:
    """Full review document (pure analysis). Raises if counts do not reconcile."""
    res = reconcile(off_rows, on_rows)
    off, on = res["off_by_id"], res["on_by_id"]
    matches = res["matches"]
    off_total, on_total = len(off_rows), len(on_rows)

    conf = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    cat = {}
    for m in matches:
        conf[m["confidence"]] = conf.get(m["confidence"], 0) + 1
        for c in m["categories"]:
            cat[c] = cat.get(c, 0) + 1

    amb_off = sorted({a["ids"][0] for a in res["ambiguous"] if a["side"] == "offline"})
    amb_on = sorted({a["ids"][0] for a in res["ambiguous"] if a["side"] == "online"})

    matched_off = {m["off_id"] for m in matches}
    matched_on = {m["on_id"] for m in matches}
    assert len(matched_off) + len(amb_off) + len(res["off_only"]) == off_total, (
        "offline counts do not reconcile")
    assert len(matched_on) + len(amb_on) + len(res["on_only"]) == on_total, (
        "online counts do not reconcile")

    collisions = invoice_collisions(res)
    poss = possible_matches(res)
    off_find = side_findings(off_rows)
    on_find = side_findings(on_rows)

    # Proposed mapping (report artifact only - never written to any database).
    mapping = []
    for m in matches:
        reason = "%s (%s)" % (m["level"].replace("_", " "), m["confidence"])
        if m.get("suspicious"):
            reason += " - SUSPICIOUS: no corroborating field agrees"
        mapping.append({"offline_id": m["off_id"], "online_id": m["on_id"],
                        "proposed_sync_id": str(uuid.uuid4()),
                        "confidence": m["confidence"], "reason": reason})
    for oid in res["off_only"]:
        mapping.append({"offline_id": oid, "online_id": None,
                        "proposed_sync_id": str(uuid.uuid4()),
                        "confidence": "OFFLINE_ONLY", "reason": "no confident online match"})
    for nid in res["on_only"]:
        mapping.append({"offline_id": None, "online_id": nid,
                        "proposed_sync_id": str(uuid.uuid4()),
                        "confidence": "ONLINE_ONLY", "reason": "no confident offline match"})
    amb_by_id = {a["ids"][0]: a for a in res["ambiguous"]}
    for oid in amb_off:
        mapping.append({"offline_id": oid, "online_id": None,
                        "proposed_sync_id": str(uuid.uuid4()),
                        "confidence": "AMBIGUOUS",
                        "reason": "unresolved - %s" % amb_by_id[oid]["reason"]})
    for nid in amb_on:
        mapping.append({"offline_id": None, "online_id": nid,
                        "proposed_sync_id": str(uuid.uuid4()),
                        "confidence": "AMBIGUOUS",
                        "reason": "unresolved - %s" % amb_by_id[nid]["reason"]})

    # Baseline preview (offline-authoritative) for differing matched pairs.
    baseline_preview = []
    for m in matches:
        if m["identical"]:
            continue
        rows = [{"field": d["field"], "offline": d["offline"], "online": d["online"],
                 "proposed_baseline": d["offline"]} for d in m["diffs"]]
        baseline_preview.append({"offline_id": m["off_id"], "online_id": m["on_id"],
                                 "diffs": rows})

    summary = {
        "offline_records": off_total,
        "online_records": on_total,
        "high_confidence_matches": conf.get("HIGH", 0),
        "medium_confidence_matches": conf.get("MEDIUM", 0),
        "low_confidence_matches": conf.get("LOW", 0),
        "suspicious_matches": sum(1 for m in matches if m.get("suspicious")),
        "ambiguous_offline": len(amb_off),
        "ambiguous_online": len(amb_on),
        "identical_matches": cat.get("MATCHED_IDENTICAL", 0),
        "offline_differs": cat.get("OFFLINE_DIFFERS", 0),
        "online_differs": cat.get("ONLINE_DIFFERS", 0),
        "both_differ": cat.get("BOTH_DIFFER", 0),
        "potential_conflicts": cat.get("POTENTIAL_CONFLICT", 0),
        "offline_only": len(res["off_only"]),
        "online_only": len(res["on_only"]),
        "invoice_collisions": len(collisions),
        "sr_order_differences": cat.get("SR_ORDER_DIFFERENCE", 0),
        "serial_differences": cat.get("SERIAL_DIFFERENCE", 0),
        "bid_differences": cat.get("BID_DIFFERENCE", 0),
        "invoice_differences": cat.get("INVOICE_DIFFERENCE", 0),
        "potential_manual_reviews": (len(amb_off) + len(amb_on) + len(res["off_only"])
                                     + len(res["on_only"]) + cat.get("POTENTIAL_CONFLICT", 0)
                                     + len(collisions)
                                     + sum(1 for m in matches if m.get("suspicious"))),
        "production_modified": False,
        "reconciliation_ok": True,
    }
    return {
        "summary": summary,
        "matches": [{"offline_id": m["off_id"], "online_id": m["on_id"],
                     "level": m["level"], "confidence": m["confidence"],
                     "suspicious": m.get("suspicious", False), "identical": m["identical"],
                     "categories": m["categories"], "diffs": m["diffs"],
                     "offline": _describe(off[m["off_id"]]),
                     "online": _describe(on[m["on_id"]])} for m in matches],
        "ambiguous": [{"side": a["side"], "ids": a["ids"], "level": a["level"],
                       "reason": a["reason"],
                       "record": _describe((off if a["side"] == "offline" else on)[a["ids"][0]])}
                      for a in res["ambiguous"]],
        "offline_only": [{"record": _describe(off[oid])} for oid in res["off_only"]],
        "online_only": [{"record": _describe(on[nid])} for nid in res["on_only"]],
        "possible_matches": poss,
        "invoice_collisions": collisions,
        "offline_findings": off_find,
        "online_findings": on_find,
        "proposed_mapping": mapping,
        "baseline_preview": baseline_preview,
    }







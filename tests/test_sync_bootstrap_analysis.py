"""
Synthetic tests for the Phase-2 read-only bootstrap reconciliation analysis.

Every fixture dataset below is fabricated (synthetic) - no production data is
used. The tests exercise the matching levels, ambiguity handling, categories,
collision detection, possible-match suggestions and reconciliation invariants.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sync_bootstrap_analysis import (  # noqa: E402
    build_review, reconcile, norm_num, norm_text, field_equal, invoice_collisions,
)

AUG = "AUGUST"
SEP = "SEPTEMBER"


def R(rid, **kw):
    """A complete synthetic record row with overridable fields."""
    row = {
        "id": rid, "sr_no": 1, "bid_date": "01-08-2026", "invoice_no": "",
        "name": "", "xcell": "", "product": "", "serial_no": "", "price": 0,
        "emi": 0, "di": 0, "bid": "", "dp_taken": "", "scheme": "",
        "actual_product": "", "given_prod_price": 0, "phone": "", "alt_phone": "",
        "month": AUG, "remarks": "", "created_at": "2026-08-01 10:00:00",
        "updated_at": "2026-08-01 10:00:00",
    }
    row.update(kw)
    return row


def ident_pair(oid, nid, bid="BID101"):
    """Identical record on both sides (the happy path)."""
    off = R(oid, bid=bid, invoice_no="INV-1", serial_no="SN1", name="AMIT KUMAR",
            price=5500, month=AUG, sr_no=1, phone="9811111111")
    on = R(nid, bid=bid, invoice_no="INV-1", serial_no="SN1", name="AMIT KUMAR",
           price=5500, month=AUG, sr_no=1, phone="9811111111")
    return off, on


def test_identical_pair_is_high_confidence():
    off, on = ident_pair(1, 101)
    res = reconcile([off], [on])
    assert len(res["matches"]) == 1
    m = res["matches"][0]
    assert m["confidence"] == "HIGH"
    assert m["identical"] is True
    assert m["categories"] == ["MATCHED_IDENTICAL"]


def test_same_bid_different_details_is_suspicious():
    off = R(1, bid="BID999", bid_date="01-08-2026", invoice_no="INV-A",
            serial_no="S1", name="RAM", price=1000, month=AUG)
    on = R(101, bid="BID999", bid_date="20-09-2026", invoice_no="INV-Z",
           serial_no="S9", name="SHAYAM", price=9999, month=SEP)
    res = reconcile([off], [on])
    assert len(res["matches"]) == 1
    m = res["matches"][0]
    # BID matched, but NO corroborating field agrees -> suspicious, downgraded.
    assert m["corroboration_agree"] == 0
    assert m["suspicious"] is True
    assert m["confidence"] == "LOW"


def test_distinct_records_are_side_only():
    off = R(1, bid="BID-A", name="A", price=1000)
    on = R(101, bid="BID-B", name="B", price=2000)
    res = reconcile([off], [on])
    assert res["matches"] == []
    assert res["off_only"] == [1]
    assert res["on_only"] == [101]
    rev = build_review([off], [on])
    assert rev["summary"]["offline_only"] == 1
    assert rev["summary"]["online_only"] == 1
    assert rev["summary"]["reconciliation_ok"] is True


def test_duplicate_bid_is_ambiguous_not_paired():
    # Two offline rows share one bid; the online row alone would otherwise match.
    off1 = R(1, bid="BIDX", name="A")
    off2 = R(2, bid="BIDX", name="B")
    on1 = R(101, bid="BIDX", name="A")
    res = reconcile([off1, off2], [on1])
    assert res["matches"] == []
    assert res["off_only"] == []
    assert res["on_only"] == []
    sides = {a["side"] for a in res["ambiguous"]}
    assert sides == {"offline", "online"}
    assert len([a for a in res["ambiguous"] if a["side"] == "offline"]) == 2
    assert len([a for a in res["ambiguous"] if a["side"] == "online"]) == 1


def test_invoice_serial_match_when_bid_blank_is_medium():
    off = R(1, bid="", invoice_no="INV-77", serial_no="SN-77", name="RAVI", price=8000)
    on = R(101, bid="", invoice_no="INV-77", serial_no="SN-77", name="RAVI", price=8000)
    res = reconcile([off], [on])
    assert len(res["matches"]) == 1
    assert res["matches"][0]["level"] == "level2_invoice_serial"
    assert res["matches"][0]["confidence"] == "MEDIUM"
    assert res["matches"][0]["identical"] is True


def test_invoice_only_match_is_medium():
    off = R(1, bid="", invoice_no="INV-55", serial_no="", name="NITIN", price=3000)
    on = R(101, bid="", invoice_no="INV-55", serial_no="", name="NITIN", price=3000)
    res = reconcile([off], [on])
    assert len(res["matches"]) == 1
    assert res["matches"][0]["level"] == "level2b_invoice"
    assert res["matches"][0]["confidence"] == "MEDIUM"


def test_fallback_name_month_price_is_low():
    off = R(1, bid="", invoice_no="", serial_no="", name="KARAN", month=AUG, price=4500)
    on = R(101, bid="", invoice_no="", serial_no="", name="KARAN", month=AUG, price=4500)
    res = reconcile([off], [on])
    assert len(res["matches"]) == 1
    assert res["matches"][0]["level"] == "level3_fallback"
    assert res["matches"][0]["confidence"] == "LOW"


def test_sr_order_difference_category():
    off, on = ident_pair(1, 101, bid="BID-SR")
    off["sr_no"] = 3
    on["sr_no"] = 4
    rev = build_review([off], [on])
    assert rev["summary"]["sr_order_differences"] == 1
    m = rev["matches"][0]
    assert "SR_ORDER_DIFFERENCE" in m["categories"]
    assert m["identical"] is False


def test_both_differ_and_potential_conflict():
    off, on = ident_pair(1, 101, bid="BID-C")
    off["price"] = 5000
    on["price"] = 6000  # both present, both differ -> conflict
    rev = build_review([off], [on])
    m = rev["matches"][0]
    assert "BOTH_DIFFER" in m["categories"]
    assert "POTENTIAL_CONFLICT" in m["categories"]
    assert rev["summary"]["potential_conflicts"] == 1
    # Baseline preview proposes the offline value as authoritative.
    price_diff = [d for d in rev["baseline_preview"][0]["diffs"] if d["field"] == "price"][0]
    assert price_diff["offline"] == 5000
    assert price_diff["proposed_baseline"] == 5000


def test_offline_differs_when_offline_has_extra_value():
    off, on = ident_pair(1, 101, bid="BID-OD")
    off["phone"] = "9810000000"
    on["phone"] = ""
    rev = build_review([off], [on])
    m = rev["matches"][0]
    assert "OFFLINE_DIFFERS" in m["categories"]
    assert "ONLINE_DIFFERS" not in m["categories"]
    assert rev["summary"]["offline_differs"] == 1


def test_invoice_collision_detected():
    # Pair P matched identical (invoice INV-9). A second, offline-only row also
    # carries INV-9 -> same invoice on two different identities.
    off, on = ident_pair(1, 101, bid="BID-P")
    off["invoice_no"] = "INV-9"
    on["invoice_no"] = "INV-9"
    extra_off = R(2, bid="BID-Q", invoice_no="INV-9", name="OTHER")
    res = reconcile([off, extra_off], [on])
    cols = invoice_collisions(res)
    assert any(c["invoice"] == "INV-9" for c in cols)


def test_overlapping_id_spaces_no_false_invoice_collisions():
    # Offline and online id spaces overlap numerically (like the live databases).
    # A correct pair share an invoice => NOT a collision, even when an online id
    # (1066) also exists offline as a *different* record matched elsewhere.
    off = [R(1076, bid="B1076", invoice_no="V", name="P"),
           R(1066, bid="B1066", invoice_no="W", name="Q")]
    on = [R(1066, bid="B1076", invoice_no="V", name="P"),
          R(1056, bid="B1066", invoice_no="W", name="Q")]
    res = reconcile(off, on)
    assert len(res["matches"]) == 2
    assert invoice_collisions(res) == []  # V and W each belong to ONE identity.
    # A genuine same-invoice-on-two-identities case must still be caught.
    extra_off = R(2000, invoice_no="V", name="OTHER")
    res2 = reconcile(off + [extra_off], on)
    cols = invoice_collisions(res2)
    assert any(c["invoice"] == "V" for c in cols)


def test_possible_match_suggestion_via_phone():
    off = R(1, bid="BID-A1", name="DEEPAK", phone="9999999999", price=1000, month=AUG)
    on = R(101, bid="BID-A2", name="DEEPAK", phone="9999999999", price=1500, month=SEP)
    rev = build_review([off], [on])
    assert rev["summary"]["offline_only"] == 1
    assert rev["summary"]["online_only"] == 1
    poss = rev["possible_matches"]
    # Each side-only row sees the other side as a weak candidate.
    assert len(poss) == 2
    for p in poss:
        assert p["candidates"]
        assert "phone" in p["candidates"][0]["signals"]


def test_numeric_normalization_ignores_formatting():
    off = R(1, bid="BID-N", price="5,500")
    on = R(101, bid="BID-N", price=5500.0)
    assert field_equal("price", off, on) is True
    assert norm_num(" 5,500 ") == norm_num("5500.00")


def test_name_case_and_space_normalization():
    assert norm_text("  amit   kumar ") == "AMIT KUMAR"


def test_reconciliation_invariants_and_no_prod_writes():
    # Mixed synthetic population: some matched, some ambiguous, some side-only.
    off = [
        R(1, bid="B1", invoice_no="I1", serial_no="S1", name="AA", price=1000),
        R(2, bid="B2", invoice_no="I2", serial_no="S2", name="BB", price=2000),
        R(3, name="CC", price=3000),                         # offline-only
        R(4, bid="B4"), R(5, bid="B4"),                      # dup bid -> ambiguous
    ]
    on = [
        R(101, bid="B1", invoice_no="I1", serial_no="S1", name="AA", price=1000),
        R(102, bid="B2", invoice_no="I2", serial_no="S2", name="BB", price=2000),
        R(103, name="DIFFERENT", price=9999),                # online-only
        R(104, bid="B4"),                                    # ambiguous partner
    ]
    rev = build_review(off, on)
    s = rev["summary"]
    assert s["offline_records"] == 5
    assert s["online_records"] == 4
    assert s["production_modified"] is False
    assert s["reconciliation_ok"] is True
    # Offline: 2 matched + 2 ambiguous + 1 offline-only = 5
    assert s["high_confidence_matches"] == 2
    assert s["ambiguous_offline"] == 2
    assert s["offline_only"] == 1
    # Online: 2 matched + 1 ambiguous + 1 online-only = 4
    assert s["ambiguous_online"] == 1
    assert s["online_only"] == 1
    # 2 matched + 1 offline-only + 1 online-only + 2 ambiguous + 1 ambiguous
    assert len(rev["proposed_mapping"]) == 7
    # Proposed mapping covers exactly the union of ids.
    covered = {e["offline_id"] for e in rev["proposed_mapping"] if e["offline_id"]}
    assert covered == {1, 2, 3, 4, 5}
    covered_on = {e["online_id"] for e in rev["proposed_mapping"] if e["online_id"]}
    assert covered_on == {101, 102, 103, 104}


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print("PASS", fn.__name__)
        except Exception:
            failed += 1
            import traceback
            traceback.print_exc()
    print("\n%s" % ("ALL SYNC BOOTSTRAP ANALYSIS TESTS PASSED" if failed == 0
                    else "%d FAILED" % failed))
    sys.exit(1 if failed else 0)



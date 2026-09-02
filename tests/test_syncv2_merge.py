"""syncv2/merge.py - pure three-way merge tests (no databases)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from syncv2 import merge as M
from syncv2 import protocol as P

AUG = "AUGUST_2026"


def base():
    return {f: None for f in P.BUSINESS_FIELDS}


def fill(**kw):
    b = base()
    b.update({"sr_no": 1, "bid_date": "01-08-2026", "invoice_no": "INV-1",
              "name": "AA", "product": "P1", "serial_no": "SN1", "price": 1000,
              "emi": 100, "di": 50, "bid": "B1", "dp_taken": 0,
              "scheme": "12/1", "actual_product": "P1", "given_prod_price": 0,
              "phone": "9000000000", "alt_phone": "", "month": AUG, "remarks": ""})
    b.update(kw)
    return b


def test_offline_only_change_merges_to_offline_value():
    base_row, off, on = fill(), fill(), fill()
    off["name"] = "OFFLINE NAME"
    res = M.merge_business(base_row, off, on)
    assert res["conflicts"] == []
    assert res["resolved"]["name"] == "OFFLINE NAME"


def test_online_only_change_merges_to_online_value():
    base_row, off, on = fill(), fill(), fill()
    on["phone"] = "9111111111"
    res = M.merge_business(base_row, off, on)
    assert res["conflicts"] == []
    assert res["resolved"]["phone"] == "9111111111"


def test_different_fields_independent_merge():
    base_row, off, on = fill(), fill(), fill()
    off["name"] = "N_OFF"
    on["phone"] = "9-ON"
    res = M.merge_business(base_row, off, on)
    assert res["conflicts"] == []
    assert res["resolved"]["name"] == "N_OFF"
    assert res["resolved"]["phone"] == "9-ON"


def test_same_field_same_value_converges():
    base_row, off, on = fill(), fill(), fill()
    off["price"] = 9999
    on["price"] = 9999
    res = M.merge_business(base_row, off, on)
    assert res["conflicts"] == []
    assert res["resolved"]["price"] == 9999


def test_same_financial_field_different_values_conflicts():
    base_row, off, on = fill(), fill(), fill()
    off["price"] = 5000
    on["price"] = 6000
    res = M.merge_business(base_row, off, on)
    assert len(res["conflicts"]) == 1
    c = res["conflicts"][0]
    assert c["kind"] == "financial" and c["field"] == "price"
    assert "price" not in res["resolved"]


def test_emi_and_di_divergence_conflict_separately():
    base_row, off, on = fill(), fill(), fill()
    off["emi"] = 111
    on["emi"] = 222
    off["di"] = 10
    on["di"] = 20
    res = M.merge_business(base_row, off, on)
    assert {c["field"] for c in res["conflicts"]} == {"emi", "di"}


def test_serial_divergence_is_conflict():
    base_row, off, on = fill(), fill(), fill()
    off["serial_no"] = "SERIAL-OFF"
    on["serial_no"] = "SERIAL-ON"
    res = M.merge_business(base_row, off, on)
    assert {c["field"] for c in res["conflicts"]} == {"serial_no"}


def test_bid_is_ordinary_mergeable_field():
    base_row, off, on = fill(), fill(), fill()
    off["bid"] = "B-OFF"
    res = M.merge_business(base_row, off, on)
    assert res["conflicts"] == []
    assert res["resolved"]["bid"] == "B-OFF"


def test_month_is_derived_from_bid_date():
    base_row, off, on = fill(), fill(), fill()
    off["bid_date"] = "15-09-2026"
    res = M.merge_business(base_row, off, on)
    assert res["resolved"]["month"] == "SEPTEMBER_2026"


def test_number_formatting_ignored():
    assert M.values_equal("price", "5,500", 5500.0) is True
    assert M.normalized_invoice(" inv-1 ") == "INV-1"


def test_invoice_change_retains_identity():
    base_row, off, on = fill(), fill(), fill()
    off["invoice_no"] = "NEW-INV"
    action, _, _ = M.classify_field(P.INVOICE_FIELD, base_row, off, on)
    assert action == M.FIELD_USE_OFFLINE


def test_invoice_collision_detection():
    col = M.detect_invoice_collision("INV-X", "sync-1", {"INV-X": {"sync-2"}})
    assert col is not None
    assert col["other_sync_ids"] == ["sync-2"]
    assert M.detect_invoice_collision("INV-X", "sync-1", {"INV-X": {"sync-1"}}) is None
    assert M.detect_invoice_collision("", "sync-1", {"": {"sync-1"}}) is None


def test_sr_ordering_one_sided_use():
    base_seq = ["A", "B", "C"]
    off = ["C", "A", "B"]
    assert M.reconcile_sr(base_seq, off, base_seq)["action"] == "use_offline"


def test_sr_ordering_online_only():
    base_seq = ["A", "B", "C"]
    on = ["A", "C", "B"]
    assert M.reconcile_sr(base_seq, base_seq, on)["action"] == "use_online"


def test_sr_ordering_both_sides_conflict_grouped():
    base_seq = ["A", "B", "C"]
    off = ["C", "A", "B"]
    on = ["B", "C", "A"]
    assert M.reconcile_sr(base_seq, off, on)["action"] == "conflict"


def test_tombstone_matrix():
    assert M.reconcile_tombstone(True, False, True, False, False) == ("apply", "offline")
    assert M.reconcile_tombstone(True, True, False, False, False) == ("apply", "online")
    assert M.reconcile_tombstone(True, False, False, False, False) == ("apply", "both")
    assert M.reconcile_tombstone(False, False, False, False, False) == ("unchanged", "both_deleted")
    assert M.reconcile_tombstone(False, True, False, True, False) == ("conflict", "resurrect")


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
    print("\n%s" % ("ALL SYNCV2 MERGE TESTS PASSED" if failed == 0 else "%d FAILED" % failed))
    sys.exit(1 if failed else 0)


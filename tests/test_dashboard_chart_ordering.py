# -*- coding: utf-8 -*-
# Regression tests: Dashboard "Records Overview" ALL-MONTHS x-axis ordering.
#
# Root cause (diagnosed and fixed):
# - pages/dashboard.py builds chart_data in chronological order (month_sort_key),
#   but st.bar_chart defaults to sort=True, which lets Vega-Lite apply its default
#   ascending (lexicographic) sort to the categorical x-axis. Month labels like
#   "APRIL_2025" then sort alphabetically instead of chronologically. Single-month
#   labels are zero-padded DD-MM-YYYY strings whose lexicographic order happens to
#   equal chronological order, which is why only ALL MONTHS appeared broken.
# - Fix: st.bar_chart(..., sort=False) on all three chart calls in pages/dashboard.py
#   so Vega-Lite emits "sort": null and renders the pre-sorted data order.
#
# Run standalone:  python tests/test_dashboard_chart_ordering.py
# Run with pytest: pytest tests/test_dashboard_chart_ordering.py

import os
import sqlite3
import sys
import tempfile

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT not in sys.path:
    sys.path.insert(0, PROJECT)

os.environ.pop("DATABASE_URL", None)
os.environ.pop("NEON_URL", None)

_TMP = tempfile.mkdtemp(prefix="gfd_chart_test_")
os.environ["FINANCE_DB_PATH"] = os.path.join(_TMP, "finance.db")

import config  # noqa: E402
config.EXCEL_FILE = os.path.join(_TMP, "ALL_RECORDS.xlsx")

import database as db  # noqa: E402

# One record in each of these months, deliberately NOT in chronological order.
_MONTHS = ["MAY_2024", "AUGUST_2024", "APRIL_2025", "SEPTEMBER_2026"]
_MONTH_DATE = {
    "MAY_2024": "15-05-2024", "AUGUST_2024": "15-08-2024",
    "APRIL_2025": "15-04-2025", "SEPTEMBER_2026": "15-09-2026",
}
_CHRONO = ["MAY_2024", "AUGUST_2024", "APRIL_2025", "SEPTEMBER_2026"]


def _make_db():
    db.init_db()
    conn = sqlite3.connect(db.DB_FILE)
    rows = []
    for i, month in enumerate(_MONTHS):
        rows.append((i + 1, _MONTH_DATE[month], "260%02d01" % (i + 1), "Test %d" % i,
                     "X", "PROD", "SER%06d" % (i + 1), 1000.0 + i, 100.0, 50.0,
                     "BID%04d" % i, 0.0, "12/4", "PROD", 0.0, "900000000%d" % i,
                     "", month, "note"))
    conn.executemany("INSERT INTO records (sr_no,bid_date,invoice_no,name,xcell,product,"
                     "serial_no,price,emi,di,bid,dp_taken,scheme,actual_product,"
                     "given_prod_price,phone,alt_phone,month,remarks) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()


_make_db()


def test_month_sort_key_orders_chronologically():
    got = sorted(list(_MONTHS), key=db.month_sort_key)
    assert got == _CHRONO, "month_sort_key must order MONTH_YYYY chronologically"


def test_chart_data_is_chronological():
    stats = db.get_dashboard_stats(month="", include_monthly_counts=True)
    mc = stats["monthly_counts"]
    assert set(mc) == set(_MONTHS)
    ordered = [m for m in sorted(mc.keys(), key=db.month_sort_key)]
    assert ordered == _CHRONO


def test_bar_chart_spec_preserves_order_with_sort_false():
    from streamlit.elements.lib.built_in_chart_utils import generate_chart, ChartType
    stats = db.get_dashboard_stats(month="", include_monthly_counts=True)
    mc = stats["monthly_counts"]
    chart_data = {m: mc[m] for m in sorted(mc.keys(), key=db.month_sort_key)}
    spec_false = generate_chart(chart_type=ChartType.VERTICAL_BAR, data=chart_data,
                                sort_from_user=False).to_dict()
    spec_true = generate_chart(chart_type=ChartType.VERTICAL_BAR, data=chart_data,
                               sort_from_user=True).to_dict()
    assert spec_false["encoding"]["x"].get("sort") is None
    assert "sort" not in spec_true["encoding"]["x"]


def test_dashboard_sql_identical_for_both_backends():
    # get_dashboard_stats must use ONE shared GROUP BY month query (no backend branch).
    captured = []
    orig = db._execute

    def spy(conn, sql, params=None, return_cursor=False):
        captured.append(sql)
        return orig(conn, sql, params, return_cursor)

    db._execute = spy
    try:
        db.get_dashboard_stats(month="", include_monthly_counts=True)
    finally:
        db._execute = orig
    group_by = [s for s in captured if "GROUP BY month" in s]
    assert len(group_by) == 1, "expected exactly one GROUP BY month query"
    assert "USE_POSTGRES" not in group_by[0]


def test_dashboard_page_renders_all_months_and_single_month():
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(os.path.join(PROJECT, "app.py"), default_timeout=240)
    at.session_state["page"] = "Dashboard"
    at.run()
    assert not at.exception, at.exception
    at.selectbox[0].select("ALL_MONTHS")
    at.run()
    assert not at.exception, at.exception
    at.selectbox[0].select("AUGUST_2024")
    at.run()
    assert not at.exception, at.exception


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print("PASS", fn.__name__)
        except Exception as e:
            failed += 1
            print("FAIL", fn.__name__, "->", type(e).__name__, e)
    print("\n%s" % ("ALL DASHBOARD CHART ORDERING TESTS PASSED" if failed == 0 else "%d FAILED" % failed))
    sys.exit(1 if failed else 0)

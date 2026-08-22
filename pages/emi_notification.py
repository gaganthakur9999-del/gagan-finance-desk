from datetime import datetime
import streamlit as st
from helpers import amount_to_float, _parse_date
from ui_components import app_header
import database as db


def add_months(dt, months):
    """Shift a date by whole months (day = 1)."""
    month_index = dt.month - 1 + months
    year = dt.year + month_index // 12
    month = month_index % 12 + 1
    return datetime(year, month, 1)


def parse_scheme(scheme):
    """Parse '12/4' -> (12, 4) or None."""
    scheme = str(scheme or "").strip()
    if "/" not in scheme:
        return None
    try:
        total, advance = scheme.split("/")
        return int(total.strip()), int(advance.strip())
    except (ValueError, AttributeError):
        return None


def first_emi_month(bid_date):
    """First EMI: next month if day<=23, else skip next month."""
    if bid_date.day <= 23:
        return add_months(bid_date.replace(day=1), 1)
    return add_months(bid_date.replace(day=1), 2)


def compute_emi_info(record):
    bid_date = _parse_date(record.get("bid_date", ""))
    scheme = parse_scheme(record.get("scheme", ""))
    if not bid_date or not scheme:
        return None
    total, advance = scheme
    remaining = total - advance
    if remaining <= 0:
        return None
    first = first_emi_month(bid_date)
    last = add_months(first, remaining - 1)
    return {
        "name": record.get("name", ""),
        "phone": record.get("phone", ""),
        "alt_phone": record.get("alt_phone", ""),
        "purchase_date": bid_date.strftime("%d-%m-%Y"),
        "product": record.get("product", ""),
        "actual_product": record.get("actual_product", ""),
        "paid_emis": remaining,
        "scheme": record.get("scheme", ""),
        "emi_amount": amount_to_float(record.get("emi", 0)),
        "last_emi": last,
        "last_emi_label": last.strftime("%B %Y"),
    }


def _show_table(title, rows):
    st.markdown(f"### {title}")
    if not rows:
        st.info("No customers in this category.")
        return
    display = []
    for r in sorted(rows, key=lambda x: x["last_emi"]):
        display.append({
            "Name": r["name"], "Phone": r["phone"], "Alt Phone": r["alt_phone"],
            "Purchase Date": r["purchase_date"], "Product": r["product"],
            "Actual Product": r["actual_product"], "Paid EMIs": r["paid_emis"],
            "Scheme": r["scheme"], "EMI Amount (Rs)": f"{r['emi_amount']:,.0f}",
        })
    st.dataframe(display, width="stretch", hide_index=True)


def page_emi_notification():
    app_header()
    if "emi_offset" not in st.session_state:
        st.session_state.emi_offset = 0

    now = datetime.now()
    base_month = add_months(now.replace(day=1), st.session_state.emi_offset)
    month1 = base_month
    month2 = add_months(base_month, 1)

    # Build a map: (year, month) -> list of customers ending that month.
    # Optimized: fetch only the columns the EMI logic needs, pre-filtered in SQL.
    month_map = {}
    for record in db.load_emi_candidates():
        info = compute_emi_info(record)
        if not info:
            continue
        key = (info["last_emi"].year, info["last_emi"].month)
        month_map.setdefault(key, []).append(info)

    st.caption(f"Today: {now.strftime('%d-%m-%Y')}")

    # Show the two month tables first (data at top - no scrolling needed)
    t1, t2 = st.tabs([
        month1.strftime("%B %Y"),
        month2.strftime("%B %Y"),
    ])
    key1 = (month1.year, month1.month)
    key2 = (month2.year, month2.month)
    with t1:
        _show_table(f"EMI Ending In {month1.strftime('%B %Y')}", month_map.get(key1, []))
    with t2:
        _show_table(f"EMI Ending In {month2.strftime('%B %Y')}", month_map.get(key2, []))

    # Navigation BELOW the data - month names on own line, then 3 equal buttons
    st.divider()
    st.markdown(f"### {month1.strftime('%B %Y')} – {month2.strftime('%B %Y')}",
                help="Use the buttons below to navigate months")
    n1, n2, n3 = st.columns(3)
    with n1:
        if st.button("◀ Previous", key="emi_prev", width="stretch"):
            st.session_state.emi_offset -= 1
            st.rerun()
    with n2:
        if st.button("📅 Current Month", key="emi_reset", width="stretch"):
            st.session_state.emi_offset = 0
            st.rerun()
    with n3:
        if st.button("Next ▶", key="emi_next", width="stretch"):
            st.session_state.emi_offset += 1
            st.rerun()

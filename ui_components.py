"""
Reusable Streamlit UI components for Gagan's Finance Desk.
"""
import sqlite3
from datetime import datetime

import streamlit as st

from config import APP_NAME, settings
from excel_utils import update_excel_file
from helpers import log_activity
from invoice import suggest_next_invoice
import database as db


def app_header():
    st.markdown(f"### {APP_NAME}")


def _navigate_to(page_name):
    st.session_state.page = page_name


def _toggle_theme():
    current = settings.get("theme", "dark")
    settings["theme"] = "light" if current == "dark" else "dark"
    from config import save_settings
    save_settings(settings)
    st.session_state._theme_changed = True


def render_menu():
    pages = ["Generate Invoice", "Records", "Dashboard", "Settings"]
    if "page" not in st.session_state:
        st.session_state.page = "Generate Invoice"

    cols = st.columns([1, 1, 1, 1, 1, 3])
    for index, page_name in enumerate(pages):
        with cols[index]:
            is_active = st.session_state.page == page_name
            st.button(
                page_name, key=f"nav_{page_name}", width="stretch",
                type="primary" if is_active else "secondary",
                on_click=_navigate_to, args=(page_name,),
            )
    with cols[4]:
        icon = "☀️" if settings.get("theme", "dark") == "dark" else "🌙"
        st.button(icon, key="theme_toggle", help="Toggle theme",
                  on_click=_toggle_theme, width="stretch")
    return st.session_state.page


def editable_customer_form(data):
    st.markdown("**Customer Details**")
    c1, c2, c3 = st.columns([1.7, 1, 1])
    with c1:
        data["name"] = st.text_input("Customer Name", value=data["name"])
    with c2:
        data["mobile"] = st.text_input("Phone", value=data["mobile"])
    with c3:
        data["price"] = st.text_input("Product Price", value=data["price"])
    c4, c5 = st.columns([1, 1.25])
    with c4:
        data["address"] = st.text_area("Address", value=data["address"], height=64)
    with c5:
        data["product"] = st.text_area("Financed Product", value=data["product"], height=64)
    with st.expander("More finance details", expanded=False):
        c6, c7, c8, c9, c10 = st.columns(5)
        with c6:
            data["bid"] = st.text_input("BID / DO ID", value=data["bid"])
        with c7:
            data["bid_date"] = st.text_input("BID Date", value=data["bid_date"])
        with c8:
            data["emi"] = st.text_input("EMI", value=data["emi"])
        with c9:
            data["di"] = st.text_input("DI", value=data["di"])
        with c10:
            data["scheme"] = st.text_input("Scheme", value=data["scheme"])
    return data


def _show_month_cards():
    """Show monthly summary cards when no PDF is uploaded."""
    available = db.get_available_months()
    if not available:
        st.info("No records yet. Upload a PDF or use manual entry below.")
        return
    st.markdown("**📊 Monthly Overview**")
    for i in range(0, len(available), 4):
        batch = available[i:i+4]
        cols = st.columns(len(batch))
        for j, month_key in enumerate(batch):
            with cols[j]:
                stats = db.get_dashboard_stats(month=month_key)
                rec_count = stats.get("total_records", 0)
                total_di = stats.get("total_di", 0)
                # Format month name
                try:
                    month_name, year = month_key.split("_")
                    dt = datetime.strptime(month_name, "%B")
                    label = dt.strftime("%b") + f" {year}"
                except (ValueError, IndexError):
                    label = month_key.replace("_", " ").title()
                total_xcell = stats.get("total_xcell", 0) or 0
                st.metric(label=label, value=f"{rec_count} Files | Xcell {total_xcell:,.0f}",
                          delta=f"DI ₹{total_di:,.0f}")


def _show_manual_entry():
    """Collapsed manual entry form for adding invoices without PDF."""
    with st.expander("➕ Add Manually (No PDF)", expanded=False):
        st.markdown("**Customer Details**")
        mc1, mc2, mc3 = st.columns([1.7, 1, 1])
        with mc1:
            m_name = st.text_input("Customer Name", key="manual_name")
        with mc2:
            m_phone = st.text_input("Phone", key="manual_phone")
        with mc3:
            m_price = st.text_input("Product Price", key="manual_price")
        mc4, mc5 = st.columns(2)
        with mc4:
            m_product = st.text_input("Financed Product", key="manual_product")
        with mc5:
            m_serial = st.text_input("Serial / IMEI", key="manual_serial")
        mc6, mc7, mc8 = st.columns(3)
        with mc6:
            m_invoice = st.text_input("Invoice No", value=suggest_next_invoice(settings), key="manual_invoice")
        with mc7:
            m_date = st.text_input("Invoice Date", value=datetime.now().strftime("%d-%m-%Y"), key="manual_date")
        with mc8:
            m_xcell = st.text_input("Xcell", key="manual_xcell")
        mc9, mc10, mc11 = st.columns(3)
        with mc9:
            m_dp = st.text_input("DP Taken", key="manual_dp")
        with mc10:
            m_di = st.text_input("DI", key="manual_di")
        with mc11:
            m_alt_phone = st.text_input("Alt Phone", key="manual_alt_phone")
        m_remarks = st.text_input("Remarks", key="manual_remarks")
        if st.button("📄 Generate + Save (Manual)", type="primary", use_container_width=True):
            errors = []
            if not m_name.strip():
                errors.append("Customer name required")
            if not m_product.strip():
                errors.append("Product required")
            if not m_price.strip():
                errors.append("Price required")
            if not m_serial.strip():
                errors.append("Serial / IMEI required")
            if not m_invoice.strip():
                errors.append("Invoice number required")
            if db.check_invoice_exists(m_invoice.strip()):
                errors.append(f"Invoice '{m_invoice}' already exists")
            if db.check_serial_exists(m_serial.strip()):
                errors.append(f"Serial '{m_serial}' already exists")
            if errors:
                st.error("❌ " + "; ".join(errors))
                return
            manual_data = {
                "name": m_name.strip(), "product": m_product.strip(),
                "price": m_price.strip(), "mobile": m_phone.strip(),
                "address": "", "bid": "", "bid_date": m_date.strip(),
                "emi": "0", "di": m_di.strip(), "scheme": "",
            }
            try:
                with st.spinner("Saving..."):
                    db.add_record(
                        invoice_no=m_invoice.strip(), data=manual_data,
                        serial_no=m_serial.strip(), xcell=m_xcell.strip(),
                        dp_taken=m_dp.strip(), product_given=m_product.strip(),
                        given_prod_price=m_price.strip(), alt_phone=m_alt_phone.strip(),
                        remarks=m_remarks.strip(),
                    )
                    log_activity("MANUAL_ENTRY", f"Manual invoice {m_invoice} for {m_name}")
                    update_excel_file()
                st.success(f"✅ Invoice {m_invoice} saved!")
                st.rerun()
            except (sqlite3.Error, ValueError) as e:
                st.error(f"❌ Failed: {e}")
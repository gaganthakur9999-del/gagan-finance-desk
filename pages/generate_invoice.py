"""
Generate Invoice page for Gagan's Finance Desk.
"""
import os
from datetime import datetime

import sqlite3
import streamlit as st

from config import settings
from excel_utils import update_excel_file
from helpers import (
    _parse_date, amount_to_float, log_activity, show_error,
    extracted_data_for_display, validate_before_generate
)
from invoice import (
    generate_invoice, save_backup, suggest_next_invoice,
    download_generated_files, reset_generation_state
)
from pdf_extract import extract_data
from ui_components import (
    app_header, editable_customer_form, _show_month_cards, _show_manual_entry
)
import database as db


def page_generate_invoice():
    app_header()

    all_records = db.load_all_records()
    today_dt = datetime.now()
    today_records = [r for r in all_records if _parse_date(r.get("bid_date", "")) and _parse_date(r.get("bid_date", "")).date() == today_dt.date()]
    today_dp = sum(amount_to_float(r.get("dp_taken", 0)) for r in today_records)
    today_di = sum(amount_to_float(r.get("di", 0)) for r in today_records)
    st.markdown(
        f'<div class="quick-stats">'
        f'<span>📅 Today: <strong>{len(today_records)}</strong> invoices</span>'
        f'<span>💵 DP: <strong>₹{today_dp:,.2f}</strong></span>'
        f'<span>💤 DI: <strong>₹{today_di:,.2f}</strong></span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    uploaded_file = st.file_uploader("Upload Bajaj DO PDF", type=["pdf"], label_visibility="collapsed")
    if uploaded_file is not None and uploaded_file.size > 10 * 1024 * 1024:
        st.error("❌ PDF file too large. Maximum size is 10MB.")
        st.stop()
    if not uploaded_file:
        _show_month_cards()
        _show_manual_entry()
        return

    current_file = uploaded_file.name
    if st.session_state.get("last_uploaded_file") != current_file:
        reset_generation_state()
        st.session_state.last_uploaded_file = current_file

    if "extracted_data" not in st.session_state:
        st.session_state.extracted_data = extract_data(uploaded_file)

    with st.expander("Auto Extracted Data", expanded=False):
        st.dataframe(
            extracted_data_for_display(st.session_state.extracted_data),
            width="stretch", hide_index=True,
        )

    data = editable_customer_form(st.session_state.extracted_data.copy())
    st.session_state.extracted_data = data

    st.markdown("**Invoice Details**")
    c1, c2, c3 = st.columns([1, 1, 1.15])
    with c1:
        default_inv = st.session_state.get("generated_invoice_no", suggest_next_invoice(settings))
        invoice_no = st.text_input("Invoice Number", value=default_inv)
    with c2:
        invoice_date = st.text_input("Invoice Date", value=datetime.now().strftime("%d-%m-%Y"))
    with c3:
        serial_no = st.text_input("Serial / IMEI")
    c4, c5, c6 = st.columns(3)
    with c4:
        xcell = st.text_input("Xcell")
    with c5:
        dp_taken = st.text_input("DP Taken")
    with c6:
        alt_phone = st.text_input("Alt Phone")
    remarks = st.text_input("Remarks", placeholder="Any notes or remarks for this record...")
    same_product = st.checkbox("Actual product is same as financed product", value=True)
    if same_product:
        product_given = data["product"]
        given_prod_price = data["price"]
    else:
        cp1, cp2 = st.columns([1.7, 1])
        with cp1:
            product_given = st.text_input("Actual Product")
        with cp2:
            given_prod_price = st.text_input("Given Product Price")

    if st.session_state.get("generated"):
        st.success("✅ Invoice generated and saved!")
        if st.session_state.get("image_file") and os.path.exists(st.session_state.image_file):
            st.image(st.session_state.image_file, caption="Invoice Preview", width=700)
        st.markdown("<div style='margin-top:10px'>", unsafe_allow_html=True)
        download_generated_files()
        st.markdown("</div>", unsafe_allow_html=True)
        if st.button("🆕 Start New Invoice", width="stretch"):
            reset_generation_state()
            st.session_state.pop("last_uploaded_file", None)
            st.rerun()
        return

    if st.button("📄 Generate Invoice + Save", width="stretch", type="primary"):
        errors = validate_before_generate(invoice_no, invoice_date, serial_no, data, settings)
        if errors:
            st.error("❌ Please fix the following errors:")
            for error in errors:
                st.error(error)
            st.stop()
        try:
            with st.spinner("🔄 Generating invoice document..."):
                docx_file, pdf_file, image_file = generate_invoice(data, invoice_no, invoice_date, serial_no)
            with st.spinner("💾 Saving to database..."):
                db.add_record(
                    invoice_no=invoice_no, data=data, serial_no=serial_no,
                    xcell=xcell, dp_taken=dp_taken,
                    product_given=product_given, given_prod_price=given_prod_price,
                    alt_phone=alt_phone, remarks=remarks,
                )
            with st.spinner("🔐 Creating backup..."):
                save_backup(invoice_no, data, serial_no, {
                    "xcell": xcell, "dp_taken": dp_taken,
                    "actual_product": product_given, "given_product_price": given_prod_price,
                    "alt_phone": alt_phone,
                })
            with st.spinner("📊 Updating Excel file..."):
                update_excel_file()
            st.session_state.generated = True
            st.session_state.docx_file = docx_file
            st.session_state.pdf_file = pdf_file
            st.session_state.image_file = image_file
            st.session_state.generated_invoice_no = invoice_no
            st.success(f"✅ Invoice #{invoice_no} generated and saved successfully!")
            log_activity("INVOICE_GENERATED", f"Invoice {invoice_no} for {data['name']}")
            st.rerun()
        except PermissionError as e:
            st.error(f"⚠️ File Access Error: {str(e)}")
        except (OSError, PermissionError, RuntimeError) as exc:
            show_error("❌ Invoice generation failed. Check:\n• Template file exists\n• Microsoft Word is installed\n• Poppler path is correct", exc)
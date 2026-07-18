"""
Records page for Gagan's Finance Desk.
"""
import os
import sqlite3
from datetime import datetime

import streamlit as st

from config import TEMP_DIR, settings
from excel_utils import export_to_excel, update_excel_file
from helpers import (
    _add_search_history, _format_month, _get_search_history, _normalize_date,
    _parse_date, log_activity
)
from invoice import (
    download_generated_files, generate_invoice, reset_generation_state
)
from ui_components import app_header
import database as db


def page_records():
    app_header()

    # Unsaved-changes warning
    if st.session_state.get("_form_dirty", False):
        st.warning("⚠️ You have unsaved changes. Please save or cancel before leaving.", icon="⚠️")

    # Search + Download Excel on same row
    search_col, download_col = st.columns([3, 3])
    with search_col:
        query = st.text_input("🔍 Search all fields", placeholder="Search by name, phone, invoice, BID, product, or serial...",
                              on_change=_add_search_history, args=(st.session_state.get("_search_query", ""),))
    with download_col:
        st.markdown("<div style='margin-top:28px'></div>", unsafe_allow_html=True)
        all_records = db.export_all_records()
        if all_records:
            wb = export_to_excel(all_records)
            excel_path = os.path.join(TEMP_DIR, "exported_records.xlsx")
            wb.save(excel_path)
            with open(excel_path, "rb") as f:
                st.download_button("⬇️ Download Excel File", f, file_name="ALL_RECORDS.xlsx", use_container_width=True)

    # Search history chips
    history = _get_search_history()
    if history and not query:
        st.markdown("**Recent searches:**")
        hcols = st.columns(len(history))
        for i, hq in enumerate(history):
            with hcols[i]:
                if st.button(f"🔍 {hq}", key=f"sh_{i}"):
                    st.session_state._search_query = hq
                    st.rerun()

    # Initialize filter variables (no UI for Advanced Filters)
    name_filter = ""
    phone_filter = ""

    available_months = db.get_available_months()
    rec_month_options = ["ALL_RECORDS"] + available_months
    current_month = datetime.now().strftime("%B_%Y").upper()
    rec_default_idx = 0
    if current_month in available_months:
        rec_default_idx = rec_month_options.index(current_month)
    rec_selected_month = st.selectbox("Month Filter", options=rec_month_options,
        format_func=lambda x: "📅 All Records" if x == "ALL_RECORDS" else x,
        index=rec_default_idx, key="rec_month_filter")

    sc1, sc2, sc3, sc4 = st.columns([1.5, 1.5, 1, 1])
    with sc1:
        sort_by = st.selectbox("Sort by",
            ["id", "bid_date", "invoice_no", "name", "price", "dp_taken", "di", "month", "created_at"],
            format_func=lambda x: {"id": "Default (Newest First)", "bid_date": "BID Date",
                "invoice_no": "Invoice No", "name": "Name", "price": "Price",
                "dp_taken": "DP Taken", "di": "DI", "month": "Month", "created_at": "Date Added"}.get(x, x))
    with sc2:
        sort_desc = st.checkbox("Descending", value=True)
    with sc3:
        page_size = st.selectbox("Per page", [20, 50, 100, 200], index=2)
    with sc4:
        st.markdown("<br>", unsafe_allow_html=True)
        total_count = db.search_records(query=query, page=1, page_size=1)[1]
        st.caption(f"Total: {total_count} records")

    page = st.number_input("Page", min_value=1, value=1, step=1)

    # Initialize date filters
    date_from = ""
    date_to = ""

    if rec_selected_month != "ALL_RECORDS":
        try:
            month_name, year = rec_selected_month.split('_')
            month_dt = datetime.strptime(month_name, "%B")
            date_from = f"01-{month_dt.month:02d}-{year}"
            import calendar
            last_day = calendar.monthrange(int(year), month_dt.month)[1]
            date_to = f"{last_day}-{month_dt.month:02d}-{year}"
        except ValueError:
            st.warning(f"Could not parse month filter: {rec_selected_month}")

    records, total_count = db.search_records(
        query=query, name_filter=name_filter, phone_filter=phone_filter,
        date_from=date_from, date_to=date_to, sort_by=sort_by,
        sort_desc=sort_desc, page=page, page_size=page_size,
    )

    if rec_selected_month != "ALL_RECORDS" and records:
        records = [r for r in records if r.get("month") == rec_selected_month]
        total_count = len(records)
    if not records:
        st.info("No records found matching your criteria.")
        return

    st.caption(f"📊 Showing {len(records)} records (Page {page} of {max(1, (total_count - 1) // page_size + 1)})")

    # Bulk select checkboxes
    bulk_col1, bulk_col2, bulk_col3 = st.columns([1, 1, 5])
    if "selected_ids" not in st.session_state:
        st.session_state.selected_ids = []

    display_cols = ["SR NO", "BID DATE", "INVOICE NO", "NAME", "XCELL", "PRODUCT",
        "SERIAL NO", "PRICE", "EMI", "DI", "BID", "DP TAKEN",
        "SCHEME", "ACTUAL PRODUCT", "GIVEN PROD PRICE", "PHONE", "ALT PHONE",
        "MONTH", "REMARKS"]

    display_data = []
    for record in records:
        bid_date_raw = str(record.get("bid_date", "") or "")
        bid_date_display = bid_date_raw.split(" ")[0] if " " in bid_date_raw else bid_date_raw
        rid = str(record.get("id", ""))
        checked = rid in st.session_state.selected_ids
        display_data.append({
            "✅": checked,
            "SR NO": record.get("sr_no", ""),
            "BID DATE": bid_date_display,
            "INVOICE NO": record.get("invoice_no", ""),
            "NAME": record.get("name", ""),
            "XCELL": record.get("xcell", ""),
            "PRODUCT": record.get("product", ""),
            "SERIAL NO": record.get("serial_no", ""),
            "PRICE": record.get("price", ""),
            "EMI": record.get("emi", ""),
            "DI": record.get("di", ""),
            "BID": record.get("bid", ""),
            "DP TAKEN": record.get("dp_taken", ""),
            "SCHEME": record.get("scheme", ""),
            "ACTUAL PRODUCT": record.get("actual_product", ""),
            "GIVEN PROD PRICE": record.get("given_prod_price", ""),
            "PHONE": record.get("phone", ""),
            "ALT PHONE": record.get("alt_phone", ""),
            "MONTH": record.get("month", ""),
            "REMARKS": record.get("remarks", ""),
        })

    # Handle checkbox toggles via table
    st.dataframe(display_data, width="stretch", hide_index=True,
                 column_order=["✅"] + display_cols,
                 on_select="rerun",
                 selection_mode="multi-row",
                 key="bulk_table")

    # Update selected_ids from interactive table
    if st.session_state.get("bulk_table", {}).get("selection"):
        st.session_state.selected_ids = [
            str(records[idx].get("id", ""))
            for idx in st.session_state.bulk_table["selection"]["rows"]
        ]

    with bulk_col1:
        if st.session_state.selected_ids:
            count = len(st.session_state.selected_ids)
            st.caption(f"**{count} selected**")
    with bulk_col2:
        if st.session_state.selected_ids and st.button("🗑️ Delete Selected", type="primary"):
            for sid in list(st.session_state.selected_ids):
                try:
                    db.delete_record(int(sid))
                except (sqlite3.Error, ValueError) as e:
                    st.error(f"Failed to delete {sid}: {e}")
            st.session_state.selected_ids = []
            st.success("Selected records deleted!")
            st.rerun()

    # ---- Edit, Delete, Regenerate, Move ----
    st.markdown("---")
    st.markdown("**✏️ Edit / 🗑️ Delete / 🔄 Regenerate Invoice / ⬆⬇ Move**")
    st.caption("Select a record below, then click action:")

    action_col1, action_col2, action_col3, action_col4, action_col5, action_col6 = st.columns([1.8, 1, 1, 0.8, 0.8, 1])
    with action_col1:
        rec_options = {}
        rec_list = []
        for r in records:
            sid = r.get("id", "")
            sr_no = r.get("sr_no", "")
            month = r.get("month", "").replace("_", " ").title()[:10]
            name = r.get("name", "")[:18]
            label = f"SR {sr_no} ({month}) - {name}" if sr_no else f"ID {sid} - {name}"
            rec_options[label] = sid
            rec_list.append({"id": sid, "sr_no": sr_no})
        default_label = list(rec_options.keys())[0] if rec_options else "No records"
        selected_label = st.selectbox("Record", options=list(rec_options.keys()), index=0, key="rec_select", label_visibility="collapsed")
        selected_id = rec_options.get(selected_label, records[0].get("id", 1) if records else 1)

    # Invoice number suggestion on edit (show recent invoice numbers)
    recent_invoices = [
        r.get("invoice_no", "") for r in db.load_all_records()[:10]
        if r.get("invoice_no", "")
    ]

    with action_col2:
        st.write("")
        if st.button("✏️ Edit", type="primary", use_container_width=True):
            st.session_state.editing_id = selected_id
            st.rerun()
    with action_col3:
        st.write("")
        if st.button("🔄 Regenerate", use_container_width=True):
            st.session_state.regenerate_id = selected_id
            st.rerun()
    with action_col4:
        st.write("")
        if st.button("⬆ Move Up", use_container_width=True):
            st.session_state.move_id = selected_id
            st.session_state.move_dir = "up"
            st.rerun()
    with action_col5:
        st.write("")
        if st.button("⬇ Move Down", use_container_width=True):
            st.session_state.move_id = selected_id
            st.session_state.move_dir = "down"
            st.rerun()
    with action_col6:
        st.write("")
        if st.button("🗑️ Delete", use_container_width=True):
            st.session_state.deleting_id = selected_id
            st.rerun()

    # ---- Move Up/Down Handler ----
    if "move_id" in st.session_state:
        move_id = st.session_state.move_id
        direction = st.session_state.move_dir
        record = db.get_record_by_id(move_id)
        if record:
            month = record.get("month", "")
            sr_no = record.get("sr_no", 0)
            if direction == "up":
                target_sr = sr_no - 1
            else:
                target_sr = sr_no + 1
            all_recs = db.load_all_records()
            target_rec = None
            for r in all_recs:
                if r.get("month") == month and r.get("sr_no") == target_sr:
                    target_rec = r
                    break
            if target_rec:
                db.swap_sr_no(move_id, target_rec["id"])
                st.success(f"✅ SR NO swapped! Refreshing...")
                del st.session_state.move_id
                del st.session_state.move_dir
                st.rerun()
            else:
                st.warning(f"Cannot move {direction} - boundary reached.")
                del st.session_state.move_id
                del st.session_state.move_dir
                st.rerun()
        else:
            del st.session_state.move_id
            del st.session_state.move_dir
            st.rerun()

    # ---- Regenerate Invoice ----
    if "regenerate_id" in st.session_state:
        regen_id = st.session_state.regenerate_id
        record = db.get_record_by_id(regen_id)
        if record:
            st.markdown(f"### 🔄 Regenerate Invoice for Record #{regen_id}")
            st.caption("You can edit the date below before regenerating.")
            regen_data = {
                "name": record.get("name", ""), "product": record.get("product", ""),
                "price": str(record.get("price", "")), "mobile": record.get("phone", ""),
                "address": record.get("address", ""),
                "bid": record.get("bid", ""), "bid_date": record.get("bid_date", ""),
                "emi": str(record.get("emi", "")), "di": str(record.get("di", "")),
                "scheme": record.get("scheme", ""),
            }
            regen_invoice = record.get("invoice_no", "")
            regen_serial = record.get("serial_no", "")

            inv_suggestions = [inv for inv in recent_invoices if inv != regen_invoice][:5]

            rc1, rc2 = st.columns(2)
            with rc1:
                edit_invoice = st.text_input("Invoice No", value=regen_invoice)
                if inv_suggestions:
                    st.caption(f"Recent: {', '.join(inv_suggestions)}")
            with rc2:
                edit_date = st.text_input("Invoice Date", value=str(record.get("bid_date", datetime.now().strftime("%d-%m-%Y"))))
            rc3, rc4 = st.columns(2)
            with rc3:
                edit_serial = st.text_input("Serial / IMEI", value=regen_serial)
            with rc4:
                edit_price = st.text_input("Price", value=regen_data["price"])
            col_regen1, col_regen2 = st.columns(2)
            with col_regen1:
                if st.button("📄 Generate Invoice Again", type="primary", use_container_width=True):
                    try:
                        regen_data["price"] = edit_price
                        with st.spinner("🔄 Generating..."):
                            d, p, img = generate_invoice(regen_data, edit_invoice, edit_date, edit_serial)
                        st.session_state.docx_file = d
                        st.session_state.pdf_file = p
                        st.session_state.image_file = img
                        st.session_state.generated = True
                        st.session_state.generated_invoice_no = edit_invoice
                        del st.session_state.regenerate_id
                        st.rerun()
                    except (sqlite3.Error, ValueError) as e:
                        st.error(f"❌ Failed: {str(e)}")
            with col_regen2:
                if st.button("Cancel", use_container_width=True):
                    del st.session_state.regenerate_id
                    st.rerun()
        else:
            st.error(f"Record #{regen_id} not found.")
            del st.session_state.regenerate_id
            st.rerun()

    if st.session_state.get("generated") and st.session_state.get("image_file"):
        st.markdown("---")
        st.success("✅ Invoice regenerated successfully!")
        if os.path.exists(st.session_state.image_file):
            st.image(st.session_state.image_file, caption="Regenerated Invoice Preview", width=700)
        rd1, rd2, rd3 = st.columns(3)
        if st.session_state.get("docx_file") and os.path.exists(st.session_state.docx_file):
            with open(st.session_state.docx_file, "rb") as f1:
                with rd1:
                    st.download_button("⬇️ Download Word", f1,
                        file_name=os.path.basename(st.session_state.docx_file), width="stretch")
        if st.session_state.get("pdf_file") and os.path.exists(st.session_state.pdf_file):
            with open(st.session_state.pdf_file, "rb") as f2:
                with rd2:
                    st.download_button("⬇️ Download PDF", f2,
                        file_name=os.path.basename(st.session_state.pdf_file), width="stretch")
        if st.session_state.get("image_file") and os.path.exists(st.session_state.image_file):
            with open(st.session_state.image_file, "rb") as f3:
                with rd3:
                    st.download_button("⬇️ Download PNG", f3,
                        file_name=os.path.basename(st.session_state.image_file), width="stretch")
        if st.button("✅ Done - Back to Records", width="stretch"):
            for key in ["generated", "docx_file", "pdf_file", "image_file", "generated_invoice_no"]:
                st.session_state.pop(key, None)
            st.rerun()

    # ---- Edit Modal ----
    if "editing_id" in st.session_state:
        edit_id = st.session_state.editing_id
        record = db.get_record_by_id(edit_id)
        if record:
            st.markdown(f"### ✏️ Editing Record #{edit_id}")
            edit_data = {
                "name": record.get("name", ""), "product": record.get("product", ""),
                "price": str(record.get("price", "")), "mobile": record.get("phone", ""),
                "address": "", "bid": record.get("bid", ""),
                "bid_date": record.get("bid_date", ""), "emi": str(record.get("emi", "")),
                "di": str(record.get("di", "")), "scheme": record.get("scheme", ""),
            }
            with st.form(key="edit_form"):
                ec1, ec2, ec3 = st.columns(3)
                with ec1:
                    e_invoice = st.text_input("Invoice No", value=record.get("invoice_no", ""))
                    if recent_invoices:
                        st.caption(f"Recent: {', '.join(recent_invoices[:3])}")
                with ec2:
                    e_serial = st.text_input("Serial No", value=record.get("serial_no", ""))
                with ec3:
                    e_bid_date = st.text_input("BID Date", value=record.get("bid_date", ""))
                ec4, ec5, ec6 = st.columns(3)
                with ec4:
                    e_name = st.text_input("Name", value=edit_data["name"])
                with ec5:
                    e_phone = st.text_input("Phone", value=edit_data["mobile"])
                with ec6:
                    e_price = st.text_input("Price", value=edit_data["price"])
                ec7, ec8, ec9 = st.columns(3)
                with ec7:
                    e_product = st.text_input("Product", value=edit_data["product"])
                with ec8:
                    e_xcell = st.text_input("Xcell", value=record.get("xcell", ""))
                with ec9:
                    e_dp = st.text_input("DP Taken", value=str(record.get("dp_taken", "")))
                ec10, ec11, ec12 = st.columns(3)
                with ec10:
                    e_bid = st.text_input("BID", value=edit_data["bid"])
                with ec11:
                    e_emi = st.text_input("EMI", value=edit_data["emi"])
                with ec12:
                    e_di = st.text_input("DI", value=edit_data["di"])
                ec13, ec14, ec15 = st.columns(3)
                with ec13:
                    e_scheme = st.text_input("Scheme", value=edit_data["scheme"])
                with ec14:
                    e_actual_product = st.text_input("Actual Product", value=record.get("actual_product", ""))
                with ec15:
                    e_given_price = st.text_input("Given Prod Price", value=str(record.get("given_prod_price", "")))
                ec16, ec17 = st.columns(2)
                with ec16:
                    e_alt_phone = st.text_input("Alt Phone", value=record.get("alt_phone", ""))
                with ec17:
                    e_remarks = st.text_input("Remarks", value=record.get("remarks", ""))
                submit_col1, submit_col2 = st.columns(2)
                with submit_col1:
                    if st.form_submit_button("💾 Save Changes", type="primary", use_container_width=True):
                        try:
                            edit_dict = {
                                "bid_date": e_bid_date, "name": e_name, "product": e_product,
                                "price": e_price, "emi": e_emi, "di": e_di, "bid": e_bid,
                                "scheme": e_scheme, "mobile": e_phone,
                            }
                            db.update_record(
                                record_id=edit_id, invoice_no=e_invoice, data=edit_dict,
                                serial_no=e_serial, xcell=e_xcell, dp_taken=e_dp,
                                product_given=e_actual_product, given_prod_price=e_given_price,
                                alt_phone=e_alt_phone, remarks=e_remarks,
                            )
                            log_activity("RECORD_UPDATED", f"Record #{edit_id} updated")
                            st.success(f"✅ Record #{edit_id} updated!")
                            del st.session_state.editing_id
                            st.session_state._form_dirty = False
                            st.rerun()
                        except (sqlite3.Error, ValueError) as e:
                            st.error(f"❌ Failed: {str(e)}")
                with submit_col2:
                    if st.form_submit_button("Cancel", use_container_width=True):
                        del st.session_state.editing_id
                        st.session_state._form_dirty = False
                        st.rerun()
        else:
            st.error(f"Record #{edit_id} not found.")
            del st.session_state.editing_id
            st.rerun()

    # ---- Delete Confirmation ----
    if "deleting_id" in st.session_state:
        del_id = st.session_state.deleting_id
        record = db.get_record_by_id(del_id)
        if record:
            sr_no = record.get("sr_no", "")
            month = record.get("month", "").replace("_", " ").title()
            name = record.get("name", "Unknown")
            st.warning(f"⚠️ Delete SR {sr_no} ({month} - {name})?")
            confirm_col1, confirm_col2 = st.columns(2)
            with confirm_col1:
                if st.button("✅ Yes, Delete", use_container_width=True):
                    try:
                        db.delete_record(del_id)
                        log_activity("RECORD_DELETED", f"Record #{del_id} deleted")
                        st.success(f"✅ Record #{del_id} deleted!")
                        del st.session_state.deleting_id
                        st.rerun()
                    except (sqlite3.Error, ValueError) as e:
                        st.error(f"❌ Failed: {str(e)}")
            with confirm_col2:
                if st.button("❌ Cancel", use_container_width=True):
                    del st.session_state.deleting_id
                    st.rerun()
        else:
            st.error(f"Record #{del_id} not found.")
            del st.session_state.deleting_id
            st.rerun()
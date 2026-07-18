"""
Settings page for Gagan's Finance Desk.
"""
import os
import sqlite3

import streamlit as st

from config import save_settings, settings
from helpers import log_activity
from ui_components import app_header
import database as db


def page_settings():
    app_header()
    st.subheader("⚙️ Settings")

    edited = settings.copy()

    st.markdown("### File Paths")
    edited["template_path"] = st.text_input("Template Path (.docx)", value=edited["template_path"])
    edited["poppler_path"] = st.text_input("Poppler Path (for PDF conversion)", value=edited["poppler_path"])

    st.markdown("### Invoice Settings")
    edited["invoice_prefix"] = st.text_input("Invoice Prefix (e.g., INV-, GAG-)", value=edited["invoice_prefix"])

    st.markdown("### Tax Rates")
    c1, c2, c3 = st.columns(3)
    with c1:
        edited["gst_rate"] = st.number_input("GST %", value=float(edited["gst_rate"]), min_value=0.0, max_value=100.0)
    with c2:
        edited["cgst_rate"] = st.number_input("CGST %", value=float(edited["cgst_rate"]), min_value=0.0, max_value=100.0)
    with c3:
        edited["sgst_rate"] = st.number_input("SGST %", value=float(edited["sgst_rate"]), min_value=0.0, max_value=100.0)

    st.markdown("### Appearance")
    edited["theme"] = st.selectbox("Theme", options=["dark", "light"], index=0 if edited.get("theme", "dark") == "dark" else 1)

    if st.button("💾 Save Settings", width="stretch", type="primary"):
        gst_total = float(edited["cgst_rate"]) + float(edited["sgst_rate"])
        if abs(gst_total - float(edited["gst_rate"])) > 0.01:
            st.error(f"❌ GST rate ({edited['gst_rate']}%) must equal CGST ({edited['cgst_rate']}%) + SGST ({edited['sgst_rate']}%) = {gst_total}%")
            st.stop()
        try:
            save_settings(edited)
            log_activity("SETTINGS_UPDATED", "Settings saved")
            st.success("✅ Settings saved! Refreshing...")
            st.rerun()
        except (OSError, PermissionError) as e:
            st.error(f"❌ Failed: {str(e)}")

    st.divider()
    st.markdown("### 💾 Database Backup & Restore")
    st.caption("Download a backup of your database.")
    db_path = os.path.join("data", "finance.db")
    if os.path.exists(db_path):
        with open(db_path, "rb") as f:
            st.download_button("⬇️ Download Database Backup", f, file_name="finance_backup.db", width="stretch")
    uploaded_db = st.file_uploader("Restore Database from Backup", type=["db"], label_visibility="collapsed")
    if uploaded_db is not None:
        if st.button("⚠️ Restore Database (overwrites current data)", type="primary", use_container_width=True):
            try:
                backup_bytes = uploaded_db.read()
                with open(db_path, "wb") as f:
                    f.write(backup_bytes)
                db.invalidate_cache()
                st.success("✅ Database restored! Refreshing...")
                st.rerun()
            except (OSError, IOError) as e:
                st.error(f"❌ Failed: {str(e)}")

    st.divider()
    st.markdown("### ℹ️ System Status")
    template_exists = os.path.exists(edited['template_path'])
    st.write(f"Template: {'✅ Found' if template_exists else '❌ Not Found'}")
    poppler_exists = os.path.exists(edited['poppler_path'])
    st.write(f"Poppler: {'✅ Found' if poppler_exists else '❌ Not Found'}")
    db_exists = os.path.exists(db_path)
    st.write(f"Database: {'✅ Found' if db_exists else '❌ Not Found'}")
    if db_exists:
        try:
            record_count = len(db.load_all_records())
            st.caption(f"Total records: {record_count}")
        except (sqlite3.Error, OSError) as e:
            st.caption(f"Error: {str(e)}")
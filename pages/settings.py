"""
Settings page for Gagan's Finance Desk.
"""
import os
import sqlite3
from datetime import datetime

import streamlit as st

from config import save_settings, settings
from helpers import log_activity
from ui_components import app_header
import database as db


def _get_neon_url():
    """Get Neon PostgreSQL connection string from env or .env file."""
    url = os.environ.get("NEON_URL", "")
    if url:
        return url
    env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")
    if os.path.exists(env_file):
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("NEON_URL="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _sync_now():
    """Push all local SQLite records to Neon cloud."""
    neon_url = _get_neon_url()
    if not neon_url:
        st.error("❌ NEON_URL not configured. Copy .env.example to .env and add your Neon connection string.")
        return
    try:
        import psycopg2
    except ImportError:
        st.error("❌ psycopg2 not installed. Run: pip install psycopg2-binary")
        return
    try:
        conn = psycopg2.connect(neon_url)
        cur = conn.cursor()
        cur.execute("SELECT invoice_no, serial_no FROM records")
        online_keys = {(r[0] or "", r[1] or "") for r in cur.fetchall()}
        cur.close()
        conn.close()
    except Exception as e:
        st.error(f"❌ Failed to connect to Neon: {e}")
        return

    records = db.load_all_records()
    synced = 0
    skipped = 0
    failures = []
    for rec in records:
        key = (str(rec.get("invoice_no") or ""), str(rec.get("serial_no") or ""))
        if key in online_keys:
            skipped += 1
            continue
        try:
            conn = psycopg2.connect(neon_url)
            cur = conn.cursor()
            month = rec.get("month") or ""
            cur.execute("SELECT COALESCE(MAX(sr_no),0)+1 FROM records WHERE month=%s", (month,))
            sr = cur.fetchone()[0]
            cur.execute("""
                INSERT INTO records (sr_no,bid_date,invoice_no,name,xcell,product,serial_no,
                    price,emi,di,bid,dp_taken,scheme,actual_product,given_prod_price,
                    phone,alt_phone,month,remarks)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                sr, rec.get("bid_date", ""), rec.get("invoice_no", ""), rec.get("name", ""),
                rec.get("xcell", ""), rec.get("product", ""), rec.get("serial_no", ""),
                float(rec.get("price", 0) or 0), float(rec.get("emi", 0) or 0), float(rec.get("di", 0) or 0),
                rec.get("bid", ""), float(rec.get("dp_taken", 0) or 0), rec.get("scheme", ""),
                rec.get("actual_product", ""), float(rec.get("given_prod_price", 0) or 0),
                rec.get("phone", ""), rec.get("alt_phone", ""), month, rec.get("remarks", ""),
            ))
            conn.commit()
            cur.close()
            conn.close()
            synced += 1
        except Exception as e:
            failures.append(f"{rec.get('invoice_no', '?')} - {e}")

    log_activity("SYNC", f"Cloud sync: {synced} synced, {skipped} skipped, {len(failures)} failed")
    st.success(f"✅ Sync complete! {synced} synced, {skipped} skipped, {len(failures)} failed.")
    if failures:
        st.warning("⚠️ Some records failed:")
        for failure in failures[:5]:
            st.error(f"❌ {failure}")


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
    st.markdown("### ☁️ Cloud Sync")
    st.caption("Push your local records to the Neon cloud database.")
    if st.button("🔄 Sync Now", width="stretch", type="primary"):
        with st.spinner("🔄 Syncing records to cloud..."):
            _sync_now()

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
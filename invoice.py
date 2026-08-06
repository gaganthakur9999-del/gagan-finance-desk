"""
Invoice generation module for Gagan's Finance Desk.
Handles DOCX generation and invoice numbering.
Works on both Windows (local) and Linux (Render cloud).
"""
import json
import logging
import os
import re
import sys
from datetime import datetime

import streamlit as st
from docxtpl import DocxTemplate
from num2words import num2words

from config import BACKUP_DIR, TEMP_DIR, settings
from helpers import amount_to_float, clean_amount, format_amount, log_activity
import database as db

# Conditional imports - pywin32 and docx2pdf only work on Windows
IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    try:
        import pythoncom
        from docx2pdf import convert
        from pdf2image import convert_from_path
        HAS_PDF_SUPPORT = True
    except ImportError:
        HAS_PDF_SUPPORT = False
else:
    HAS_PDF_SUPPORT = False


def invoice_sort_key(invoice_no):
    """Sort key for invoice numbers. Extracts the trailing counter
    (the digits after YYMM), so `2607100` sorts after `260799`."""
    value = str(invoice_no or "")
    match = re.search(r"^\d{4}(\d+)$", value)
    return int(match.group(1)) if match else 0


def _current_ym_code():
    """Return the YYMM code for today (e.g. '2608' for Aug 2026)."""
    return datetime.now().strftime("%y%m")


def _latest_ym_code_from_records(records):
    """Return the highest YYMM code found in existing records, or ''."""
    codes = []
    for r in records:
        m = re.match(r"^(\d{4})\d+$", str(r.get("invoice_no") or ""))
        if m:
            codes.append(m.group(1))
    return max(codes) if codes else ""


def suggest_next_invoice():
    """Suggest the next invoice number in the format YYMM + counter.

    The YYMM code follows the calendar month/year automatically,
    BUT if the user has already jumped ahead (e.g. using 2608 codes
    while the calendar is still 2607), the existing higher code is
    preserved so the sequence continues seamlessly.

    The counter has a minimum of 2 digits but no upper limit, so
    260799 -> 2607100 (month code preserved).

    Optimized: uses two scalar SQL queries instead of loading the
    whole records table, producing the exact same number.
    """
    today_code = _current_ym_code()
    # Highest YYMM prefix across all-digit invoice numbers (scalar query).
    latest_code = db.get_latest_invoice_yy_code()
    # Use the later of (today's calendar code, latest code in records)
    ym_code = latest_code if latest_code > today_code else today_code
    # Max trailing counter for ym_code (scalar query). 0 when none exist.
    highest_counter = db.get_max_invoice_counter(ym_code)
    counter = highest_counter + 1
    if counter < 10:
        return f"{ym_code}0{counter}"
    return f"{ym_code}{counter}"


def generate_invoice(data, invoice_no, invoice_date, serial_no):
    """
    Generate invoice as DOCX. On Windows, also generates PDF preview.
    On Linux/cloud, only generates DOCX (user can download).
    """
    template = DocxTemplate(settings["template_path"])
    price_value = float(clean_amount(data["price"]))
    gst_rate = float(settings.get("gst_rate", 18.0))
    cgst_rate = float(settings.get("cgst_rate", 9.0))
    sgst_rate = float(settings.get("sgst_rate", 9.0))
    taxable_value = round(price_value / (1 + gst_rate / 100), 2)
    cgst = round(taxable_value * (cgst_rate / 100), 2)
    sgst = round(taxable_value * (sgst_rate / 100), 2)
    total_tax = round(cgst + sgst, 2)
    context = {
        "invoice_no": invoice_no, "date": invoice_date,
        "name": data["name"], "mobile": data["mobile"], "address": data.get("address", ""),
        "product": data["product"], "price": format_amount(data["price"]),
        "serial_no": serial_no, "price_in_words": num2words(price_value).title(),
        "taxable_value": taxable_value, "cgst": cgst, "sgst": sgst,
        "total_tax": total_tax, "tax_words": num2words(total_tax).title(),
    }
    safe_invoice_no = re.sub(r"[^A-Za-z0-9_-]+", "_", invoice_no)
    docx_file = os.path.join(TEMP_DIR, f"Invoice_{safe_invoice_no}.docx")

    template.render(context)
    template.save(docx_file)

    # PDF support only on Windows (requires Word + poppler)
    pdf_file = ""
    image_file = ""

    if IS_WINDOWS and HAS_PDF_SUPPORT:
        try:
            pdf_file = os.path.join(TEMP_DIR, f"Invoice_{safe_invoice_no}.pdf")
            image_file = os.path.join(TEMP_DIR, f"Invoice_{safe_invoice_no}.png")

            pythoncom.CoInitialize()
            try:
                convert(docx_file, pdf_file)
            finally:
                pythoncom.CoUninitialize()

            poppler_path = settings.get("poppler_path") or None
            images = convert_from_path(pdf_file, poppler_path=poppler_path)
            if images:
                images[0].save(image_file, "PNG")
        except Exception as e:
            logging.warning(f"PDF preview generation skipped: {e}")
            pdf_file = ""
            image_file = ""

    return docx_file, pdf_file, image_file


def save_backup(invoice_no, data, serial_no, extra):
    backup_data = {
        "invoice_no": invoice_no,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "customer": data, "serial_no": serial_no, "extra": extra,
    }
    with open(os.path.join(BACKUP_DIR, f"{invoice_no}.json"), "w", encoding="utf-8") as f:
        json.dump(backup_data, f, indent=4)


def download_generated_files():
    d1, d2, d3 = st.columns(3)
    if st.session_state.get("docx_file") and os.path.exists(st.session_state.docx_file):
        with open(st.session_state.docx_file, "rb") as f1:
            with d1:
                st.download_button("⬇️ Download Word", f1,
                    file_name=os.path.basename(st.session_state.docx_file), width="stretch")

    if st.session_state.get("pdf_file") and os.path.exists(st.session_state.pdf_file):
        with open(st.session_state.pdf_file, "rb") as f2:
            with d2:
                st.download_button("⬇️ Download PDF", f2,
                    file_name=os.path.basename(st.session_state.pdf_file), width="stretch")

    if st.session_state.get("image_file") and os.path.exists(st.session_state.image_file):
        with open(st.session_state.image_file, "rb") as f3:
            with d3:
                st.download_button("⬇️ Download PNG", f3,
                    file_name=os.path.basename(st.session_state.image_file), width="stretch")


def reset_generation_state():
    for file_key in ["docx_file", "pdf_file", "image_file"]:
        old_path = st.session_state.get(file_key)
        if old_path and os.path.exists(old_path):
            try:
                os.remove(old_path)
            except OSError:
                pass
    for key in ["generated", "docx_file", "pdf_file", "image_file", "extracted_data", "generated_invoice_no"]:
        st.session_state.pop(key, None)
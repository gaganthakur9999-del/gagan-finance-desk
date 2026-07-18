"""
Invoice generation module for Gagan's Finance Desk.
Handles DOCX generation from template and DOCX-to-PDF conversion.
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

# Conditional imports
IS_WINDOWS = sys.platform == "win32"

# On Windows: we can use Word + poppler for full PDF/PNG
if IS_WINDOWS:
    try:
        import pythoncom
        from docx2pdf import convert
        from pdf2image import convert_from_path
        HAS_WIN_PDF = True
    except ImportError:
        HAS_WIN_PDF = False
else:
    HAS_WIN_PDF = False

# On ALL platforms: use mammoth + weasyprint to convert DOCX → HTML → PDF
try:
    import mammoth
    from weasyprint import HTML as WeasyHTML
    HAS_DOCX2PDF = True
except ImportError:
    HAS_DOCX2PDF = False


def invoice_sort_key(invoice_no):
    match = re.search(r"(\d+)$", str(invoice_no or ""))
    return int(match.group(1)) if match else 0


def suggest_next_invoice(settings):
    current_month = datetime.now().strftime("%B_%Y").upper()
    records = db.load_all_records()
    month_records = [r for r in records if r.get("month") == current_month]
    if not month_records:
        return settings.get("invoice_prefix", "")
    last_invoice = max(month_records, key=lambda item: invoice_sort_key(item.get("invoice_no")))
    last_value = str(last_invoice.get("invoice_no") or "")
    match = re.search(r"^(.*?)(\d+)$", last_value)
    if not match:
        return settings.get("invoice_prefix", "")
    prefix, number = match.groups()
    return f"{prefix}{int(number) + 1:0{len(number)}d}"


def generate_invoice(data, invoice_no, invoice_date, serial_no):
    """
    Generate invoice from template.docx.
    Returns (docx_path, pdf_path, image_path) for compatibility.
    On Windows: full DOCX + PDF + PNG
    On Linux: DOCX from template + PDF from DOCX via mammoth+weasyprint
    """
    # === Generate DOCX from template ===
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
        "taxable_value": format_amount(taxable_value), "cgst": format_amount(cgst),
        "sgst": format_amount(sgst), "total_tax": format_amount(total_tax),
    }
    safe_invoice_no = re.sub(r"[^A-Za-z0-9_-]+", "_", invoice_no)
    docx_file = os.path.join(TEMP_DIR, f"Invoice_{safe_invoice_no}.docx")

    template.render(context)
    template.save(docx_file)

    # === Generate PDF from the template-based DOCX ===
    pdf_file = ""
    image_file = ""

    # Method 1: Windows Word conversion (full fidelity)
    if IS_WINDOWS and HAS_WIN_PDF:
        try:
            win_pdf = os.path.join(TEMP_DIR, f"Invoice_{safe_invoice_no}_win.pdf")
            win_img = os.path.join(TEMP_DIR, f"Invoice_{safe_invoice_no}.png")
            pythoncom.CoInitialize()
            try:
                convert(docx_file, win_pdf)
            finally:
                pythoncom.CoUninitialize()
            poppler_path = settings.get("poppler_path") or None
            images = convert_from_path(win_pdf, poppler_path=poppler_path)
            if images:
                images[0].save(win_img, "PNG")
            pdf_file = win_pdf
            image_file = win_img
        except Exception as e:
            logging.warning(f"Windows PDF conversion failed: {e}")

    # Method 2: Cross-platform DOCX → HTML → PDF (works on Linux/Render)
    if not pdf_file and HAS_DOCX2PDF:
        try:
            with open(docx_file, "rb") as f:
                result = mammoth.convert_to_html(f, 
                    style_map = "p[style-name='Title'] => h1:fresh")
                html = result.value

            # Wrap in clean A5-ready HTML
            full_html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
@page {{ size: A4; margin: 15mm; }}
body {{ font-family: 'Arial', sans-serif; font-size: 11pt; color: #222; }}
h1 {{ text-align: center; font-size: 18pt; color: #1a3a5c; }}
table {{ width: 100%; border-collapse: collapse; margin: 10px 0; }}
td, th {{ padding: 6px 8px; border: 1px solid #ccc; text-align: left; }}
th {{ background: #1a3a5c; color: white; font-weight: bold; }}
</style></head><body>
{html}
</body></html>"""
            pdf_name = os.path.join(TEMP_DIR, f"Invoice_{safe_invoice_no}.pdf")
            WeasyHTML(string=full_html).write_pdf(pdf_name)
            pdf_file = pdf_name
        except Exception as e:
            logging.warning(f"Cross-platform PDF failed: {e}")

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
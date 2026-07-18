"""
Invoice generation module for Gagan's Finance Desk.
Handles DOCX generation, HTML preview, and invoice numbering.
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


def generate_html_preview(data, invoice_no, invoice_date, serial_no) -> str:
    """
    Generate an HTML invoice string that can be displayed inline.
    This works on ALL platforms - no Word, no Poppler needed.
    Returns HTML as a string, mobile-responsive with dark/light support.
    """
    price_value = float(clean_amount(data.get("price", 0)))
    gst_rate = float(settings.get("gst_rate", 18.0))
    cgst_rate = float(settings.get("cgst_rate", 9.0))
    sgst_rate = float(settings.get("sgst_rate", 9.0))
    taxable_value = round(price_value / (1 + gst_rate / 100), 2)
    cgst = round(taxable_value * (cgst_rate / 100), 2)
    sgst = round(taxable_value * (sgst_rate / 100), 2)
    total_tax = round(cgst + sgst, 2)
    total_amount = round(taxable_value + total_tax, 2)
    
    price_in_words = num2words(price_value).title()
    tax_words = num2words(total_tax).title()
    
    address = data.get("address", "")
    mobile = data.get("mobile", "")
    product = data.get("product", "")
    name = data.get("name", "")
    bid = data.get("bid", "")
    emi = data.get("emi", "")
    di = data.get("di", "")
    scheme = data.get("scheme", "")
    
    # Determine if dark or light theme
    theme = settings.get("theme", "dark")
    is_dark = theme == "dark"
    
    bg = "#1a1f2e" if is_dark else "#ffffff"
    text = "#e0e4ea" if is_dark else "#1a1d23"
    border = "#2a3344" if is_dark else "#dde1e6"
    header_bg = "#232a3a" if is_dark else "#f0f2f6"
    accent = "#5B8DB8" if is_dark else "#2E5A7C"
    
    html = f"""<div style="background:{bg}; color:{text}; border:1px solid {border}; border-radius:10px; padding:20px; max-width:800px; margin:10px auto; font-family:Arial,sans-serif;">
    <div style="text-align:center; border-bottom:2px solid {accent}; padding-bottom:12px; margin-bottom:16px;">
        <h2 style="margin:0; color:{accent}; font-size:22px;">GAGAN'S FINANCE DESK</h2>
        <p style="margin:4px 0 0 0; font-size:12px; color:{text if is_dark else '#666'};">
            Invoice No: <strong>{invoice_no}</strong> &nbsp;|&nbsp; Date: <strong>{invoice_date}</strong>
        </p>
    </div>
    <table style="width:100%; border-collapse:collapse; font-size:14px;">
        <tr><td style="padding:4px 8px; width:30%; font-weight:bold; color:{accent};">Customer:</td>
            <td style="padding:4px 8px;">{name}</td></tr>
        <tr><td style="padding:4px 8px; font-weight:bold; color:{accent};">Phone:</td>
            <td style="padding:4px 8px;">{mobile}</td></tr>
        <tr><td style="padding:4px 8px; font-weight:bold; color:{accent};">Address:</td>
            <td style="padding:4px 8px;">{address}</td></tr>
        <tr><td style="padding:4px 8px; font-weight:bold; color:{accent};">Product:</td>
            <td style="padding:4px 8px;">{product}</td></tr>
        <tr><td style="padding:4px 8px; font-weight:bold; color:{accent};">Serial / IMEI:</td>
            <td style="padding:4px 8px;">{serial_no}</td></tr>
    </table>
    <hr style="border:none; border-top:1px solid {border}; margin:12px 0;">
    <table style="width:100%; border-collapse:collapse; font-size:14px;">
        <tr style="background:{header_bg};">
            <th style="padding:8px; text-align:left; border:1px solid {border};">Description</th>
            <th style="padding:8px; text-align:right; border:1px solid {border};">Amount</th>
        </tr>
        <tr><td style="padding:8px; border:1px solid {border};">Product Price</td>
            <td style="padding:8px; text-align:right; border:1px solid {border};">{format_amount(price_value)}</td></tr>
        <tr><td style="padding:8px; border:1px solid {border};">Taxable Value</td>
            <td style="padding:8px; text-align:right; border:1px solid {border};">{format_amount(taxable_value)}</td></tr>
        <tr><td style="padding:8px; border:1px solid {border};">CGST @ {cgst_rate}%</td>
            <td style="padding:8px; text-align:right; border:1px solid {border};">{format_amount(cgst)}</td></tr>
        <tr><td style="padding:8px; border:1px solid {border};">SGST @ {sgst_rate}%</td>
            <td style="padding:8px; text-align:right; border:1px solid {border};">{format_amount(sgst)}</td></tr>
        <tr style="font-weight:bold; background:{header_bg};">
            <td style="padding:8px; border:1px solid {border};">Total</td>
            <td style="padding:8px; text-align:right; border:1px solid {border}; color:{accent}; font-size:16px;">{format_amount(total_amount)}</td></tr>
    </table>
    <hr style="border:none; border-top:1px solid {border}; margin:12px 0;">
    <table style="width:100%; border-collapse:collapse; font-size:14px;">
        <tr><td style="padding:4px 8px; width:30%; font-weight:bold; color:{accent};">BID / DO ID:</td>
            <td style="padding:4px 8px;">{bid}</td></tr>
        <tr><td style="padding:4px 8px; font-weight:bold; color:{accent};">EMI / DI:</td>
            <td style="padding:4px 8px;">{emi} / {di}</td></tr>
        <tr><td style="padding:4px 8px; font-weight:bold; color:{accent};">Scheme:</td>
            <td style="padding:4px 8px;">{scheme}</td></tr>
    </table>
    <p style="font-size:12px; margin-top:12px; font-style:italic; color:{'#9aa3b2' if is_dark else '#888'};">
        Amount in words: <strong>{price_in_words}</strong><br>
        Tax in words: <strong>{tax_words}</strong>
    </p>
</div>"""
    return html


def generate_invoice(data, invoice_no, invoice_date, serial_no):
    """
    Generate invoice as DOCX + HTML preview (works everywhere).
    On Windows, also generates PDF/PNG preview.
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
    
    # Generate HTML preview (works everywhere)
    html_preview = generate_html_preview(data, invoice_no, invoice_date, serial_no)
    html_file = os.path.join(TEMP_DIR, f"Invoice_{safe_invoice_no}.html")
    with open(html_file, "w", encoding="utf-8") as f:
        f.write(html_preview)
    
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
    
    return docx_file, pdf_file, image_file, html_file


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
    
    # PNG download button - show if it exists (Windows only)
    if st.session_state.get("image_file") and os.path.exists(st.session_state.image_file):
        with open(st.session_state.image_file, "rb") as f3:
            with d3:
                st.download_button("⬇️ Download PNG", f3,
                    file_name=os.path.basename(st.session_state.image_file), width="stretch")


def reset_generation_state():
    for file_key in ["docx_file", "pdf_file", "image_file", "html_file"]:
        old_path = st.session_state.get(file_key)
        if old_path and os.path.exists(old_path):
            try:
                os.remove(old_path)
            except OSError:
                pass
    for key in ["generated", "docx_file", "pdf_file", "image_file", "html_file",
                "extracted_data", "generated_invoice_no"]:
        st.session_state.pop(key, None)
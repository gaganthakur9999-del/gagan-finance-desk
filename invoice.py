"""
Invoice generation module for Gagan's Finance Desk.
Handles DOCX generation, HTML preview, and PDF generation.
Works on both Windows (local) and Linux (Render cloud).
"""
import json
import logging
import os
import re
import sys
from datetime import datetime
from io import BytesIO

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

# fpdf2 works on all platforms (Windows, Linux, Mac)
try:
    from fpdf import FPDF
    HAS_FPDF = True
except ImportError:
    HAS_FPDF = False


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


def generate_pdf(data, invoice_no, invoice_date, serial_no, safe_name) -> str:
    """
    Generate a clean PDF invoice using fpdf2.
    Works on ALL platforms (Windows, Linux, Mac).
    Returns path to the generated PDF file.
    """
    price_value = float(clean_amount(data.get("price", 0)))
    gst_rate = float(settings.get("gst_rate", 18.0))
    cgst_rate = float(settings.get("cgst_rate", 9.0))
    sgst_rate = float(settings.get("sgst_rate", 9.0))
    taxable_value = round(price_value / (1 + gst_rate / 100), 2)
    cgst = round(taxable_value * (cgst_rate / 100), 2)
    sgst = round(taxable_value * (sgst_rate / 100), 2)
    total_tax = round(cgst + sgst, 2)

    price_in_words = num2words(price_value).title()
    tax_words = num2words(total_tax).title()

    name = data.get("name", "")
    mobile = data.get("mobile", "")
    address = data.get("address", "")
    product = data.get("product", "")
    bid = data.get("bid", "")
    emi = data.get("emi", "")
    di = data.get("di", "")
    scheme = data.get("scheme", "")

    pdf = FPDF(orientation='P', unit='mm', format='A4')
    pdf.add_page()

    # Colors
    accent = (46, 90, 124)
    dark = (30, 40, 60)
    gray = (100, 110, 120)
    light_gray = (245, 245, 245)
    white = (255, 255, 255)
    border_c = (200, 200, 200)

    # Header
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(*accent)
    pdf.cell(0, 10, "GAGAN'S FINANCE DESK", 0, 1, "C")
    pdf.set_draw_color(*accent)
    pdf.line(10, 18, 200, 18)

    pdf.ln(4)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*gray)
    pdf.cell(0, 5, f"Invoice No: {invoice_no}    |    Date: {invoice_date}", 0, 1, "C")
    pdf.ln(6)

    # Customer section
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(*dark)
    pdf.cell(0, 6, "Customer Details", 0, 1, "L")
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*dark)

    data_rows = [
        ("Customer:", name),
        ("Phone:", mobile),
        ("Address:", address),
        ("Product:", product),
        ("Serial / IMEI:", serial_no),
    ]
    for label, value in data_rows:
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(30, 5, label, 0, 0, "L")
        pdf.set_font("Helvetica", "", 9)
        pdf.cell(0, 5, value, 0, 1, "L")

    pdf.ln(4)

    # Price table
    col1_w = 120
    col2_w = 60
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(*accent)
    pdf.set_text_color(*white)
    pdf.cell(col1_w, 7, "  Description", 1, 0, "L", True)
    pdf.cell(col2_w, 7, "Amount", 1, 1, "C", True)

    pdf.set_text_color(*dark)
    price_rows = [
        ("Product Price", format_amount(price_value)),
        ("Taxable Value", format_amount(taxable_value)),
        (f"CGST @ {cgst_rate}%", format_amount(cgst)),
        (f"SGST @ {sgst_rate}%", format_amount(sgst)),
    ]

    fill = False
    for label, val in price_rows:
        bg = light_gray if fill else white
        pdf.set_fill_color(*bg)
        pdf.set_font("Helvetica", "", 9)
        pdf.cell(col1_w, 6, f"  {label}", 1, 0, "L", True)
        pdf.cell(col2_w, 6, f"  {val}", 1, 1, "R", True)
        fill = not fill

    # Total row
    total_amount = round(taxable_value + total_tax, 2)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_fill_color(*accent)
    pdf.set_text_color(*white)
    pdf.cell(col1_w, 8, "  Total", 1, 0, "L", True)
    pdf.cell(col2_w, 8, f"  {format_amount(total_amount)}", 1, 1, "R", True)

    pdf.ln(4)
    pdf.set_text_color(*dark)

    # Finance info
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 6, "Finance Details", 0, 1, "L")
    pdf.set_font("Helvetica", "", 9)
    fin_rows = [
        ("BID / DO ID:", bid),
        ("EMI / DI:", f"{emi} / {di}"),
        ("Scheme:", scheme),
    ]
    for label, value in fin_rows:
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(30, 5, label, 0, 0, "L")
        pdf.set_font("Helvetica", "", 9)
        pdf.cell(0, 5, value, 0, 1, "L")

    pdf.ln(6)

    # Amount in words
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(*gray)
    pdf.cell(0, 4, f"Amount in words: {price_in_words}", 0, 1, "L")
    pdf.cell(0, 4, f"Tax in words: {tax_words}", 0, 1, "L")

    # Footer
    pdf.ln(10)
    pdf.set_draw_color(*border_c)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(2)
    pdf.set_font("Helvetica", "", 7)
    pdf.set_text_color(*gray)
    pdf.cell(0, 4, "Gagan's Finance Desk - Generated automatically", 0, 1, "C")

    pdf_file = os.path.join(TEMP_DIR, f"Invoice_{safe_name}.pdf")
    pdf.output(pdf_file)
    return pdf_file


def generate_html_preview(data, invoice_no, invoice_date, serial_no) -> str:
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

    html = f"""<div style="background:white; color:#1a1d23; border:1px solid #dde1e6; border-radius:10px; padding:20px; max-width:800px; margin:10px auto; font-family:Arial,sans-serif;">
    <div style="text-align:center; border-bottom:2px solid #2E5A7C; padding-bottom:12px; margin-bottom:16px;">
        <h2 style="margin:0; color:#2E5A7C; font-size:22px;">GAGAN'S FINANCE DESK</h2>
        <p style="margin:4px 0 0 0; font-size:12px; color:#666;">
            Invoice No: <strong>{invoice_no}</strong> &nbsp;|&nbsp; Date: <strong>{invoice_date}</strong>
        </p>
    </div>
    <table style="width:100%; border-collapse:collapse; font-size:14px;">
        <tr><td style="padding:4px 8px; width:30%; font-weight:bold; color:#2E5A7C;">Customer:</td>
            <td style="padding:4px 8px;">{name}</td></tr>
        <tr><td style="padding:4px 8px; font-weight:bold; color:#2E5A7C;">Phone:</td>
            <td style="padding:4px 8px;">{mobile}</td></tr>
        <tr><td style="padding:4px 8px; font-weight:bold; color:#2E5A7C;">Address:</td>
            <td style="padding:4px 8px;">{address}</td></tr>
        <tr><td style="padding:4px 8px; font-weight:bold; color:#2E5A7C;">Product:</td>
            <td style="padding:4px 8px;">{product}</td></tr>
        <tr><td style="padding:4px 8px; font-weight:bold; color:#2E5A7C;">Serial / IMEI:</td>
            <td style="padding:4px 8px;">{serial_no}</td></tr>
    </table>
    <hr style="border:none; border-top:1px solid #dde1e6; margin:12px 0;">
    <table style="width:100%; border-collapse:collapse; font-size:14px;">
        <tr style="background:#f0f2f6;">
            <th style="padding:8px; text-align:left; border:1px solid #dde1e6;">Description</th>
            <th style="padding:8px; text-align:right; border:1px solid #dde1e6;">Amount</th>
        </tr>
        <tr><td style="padding:8px; border:1px solid #dde1e6;">Product Price</td>
            <td style="padding:8px; text-align:right; border:1px solid #dde1e6;">{format_amount(price_value)}</td></tr>
        <tr><td style="padding:8px; border:1px solid #dde1e6;">Taxable Value</td>
            <td style="padding:8px; text-align:right; border:1px solid #dde1e6;">{format_amount(taxable_value)}</td></tr>
        <tr><td style="padding:8px; border:1px solid #dde1e6;">CGST @ {cgst_rate}%</td>
            <td style="padding:8px; text-align:right; border:1px solid #dde1e6;">{format_amount(cgst)}</td></tr>
        <tr><td style="padding:8px; border:1px solid #dde1e6;">SGST @ {sgst_rate}%</td>
            <td style="padding:8px; text-align:right; border:1px solid #dde1e6;">{format_amount(sgst)}</td></tr>
        <tr style="font-weight:bold; background:#f0f2f6;">
            <td style="padding:8px; border:1px solid #dde1e6;">Total</td>
            <td style="padding:8px; text-align:right; border:1px solid #dde1e6; color:#2E5A7C; font-size:16px;">{format_amount(total_amount)}</td></tr>
    </table>
    <hr style="border:none; border-top:1px solid #dde1e6; margin:12px 0;">
    <table style="width:100%; border-collapse:collapse; font-size:14px;">
        <tr><td style="padding:4px 8px; width:30%; font-weight:bold; color:#2E5A7C;">BID / DO ID:</td>
            <td style="padding:4px 8px;">{bid}</td></tr>
        <tr><td style="padding:4px 8px; font-weight:bold; color:#2E5A7C;">EMI / DI:</td>
            <td style="padding:4px 8px;">{emi} / {di}</td></tr>
        <tr><td style="padding:4px 8px; font-weight:bold; color:#2E5A7C;">Scheme:</td>
            <td style="padding:4px 8px;">{scheme}</td></tr>
    </table>
    <p style="font-size:12px; margin-top:12px; font-style:italic; color:#888;">
        Amount in words: <strong>{price_in_words}</strong><br>
        Tax in words: <strong>{tax_words}</strong>
    </p>
</div>"""
    return html


def generate_invoice(data, invoice_no, invoice_date, serial_no):
    """
    Generate invoice as DOCX + HTML preview + PDF.
    DOCX from template - PDF from fpdf2 (works on all platforms).
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

    # Generate PDF using fpdf2 (works on ALL platforms - Windows, Linux, Mac)
    pdf_file = ""
    image_file = ""
    try:
        if HAS_FPDF:
            pdf_file = generate_pdf(data, invoice_no, invoice_date, serial_no, safe_invoice_no)
    except Exception as e:
        logging.warning(f"PDF generation via fpdf2 failed: {e}")
        pdf_file = ""

    # Windows-only: PDF/PNG from Word (extra, not needed for cloud)
    if IS_WINDOWS and HAS_PDF_SUPPORT:
        try:
            win_pdf = os.path.join(TEMP_DIR, f"Invoice_{safe_invoice_no}_win.pdf")
            win_img = os.path.join(TEMP_DIR, f"Invoice_{safe_invoice_no}_win.png")

            pythoncom.CoInitialize()
            try:
                convert(docx_file, win_pdf)
            finally:
                pythoncom.CoUninitialize()

            poppler_path = settings.get("poppler_path") or None
            images = convert_from_path(win_pdf, poppler_path=poppler_path)
            if images:
                images[0].save(win_img, "PNG")

            # Prefer Word-generated PDF if available
            if not pdf_file or not os.path.exists(pdf_file):
                pdf_file = win_pdf
            image_file = win_img
        except Exception as e:
            logging.warning(f"Windows PDF preview skipped: {e}")

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
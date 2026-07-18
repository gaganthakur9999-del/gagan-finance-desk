"""
Invoice generation module for Gagan's Finance Desk.
Handles DOCX generation, Word-to-PDF conversion, and invoice numbering.
"""
import json
import logging
import os
import re
from datetime import datetime

import pythoncom
import streamlit as st
from docx2pdf import convert
from docxtpl import DocxTemplate
from num2words import num2words
from pdf2image import convert_from_path

from config import BACKUP_DIR, TEMP_DIR, settings
from helpers import amount_to_float, clean_amount, format_amount, log_activity
import database as db


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
        "name": data["name"], "mobile": data["mobile"], "address": data["address"],
        "product": data["product"], "price": format_amount(data["price"]),
        "serial_no": serial_no, "price_in_words": num2words(price_value).title(),
        "taxable_value": taxable_value, "cgst": cgst, "sgst": sgst,
        "total_tax": total_tax, "tax_words": num2words(total_tax).title(),
    }
    safe_invoice_no = re.sub(r"[^A-Za-z0-9_-]+", "_", invoice_no)
    docx_file = os.path.join(TEMP_DIR, f"Invoice_{safe_invoice_no}.docx")
    pdf_file = os.path.join(TEMP_DIR, f"Invoice_{safe_invoice_no}.pdf")
    image_file = os.path.join(TEMP_DIR, f"Invoice_{safe_invoice_no}.png")
    template.render(context)
    template.save(docx_file)
    pythoncom.CoInitialize()
    try:
        convert(docx_file, pdf_file)
    finally:
        pythoncom.CoUninitialize()
    poppler_path = settings.get("poppler_path") or None
    images = convert_from_path(pdf_file, poppler_path=poppler_path)
    if not images:
        raise RuntimeError("PDF preview image could not be created.")
    images[0].save(image_file, "PNG")
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
    if st.session_state.get("docx_file"):
        with open(st.session_state.docx_file, "rb") as f1:
            with d1:
                st.download_button("⬇️ Download Word", f1,
                    file_name=os.path.basename(st.session_state.docx_file), width="stretch")
    if st.session_state.get("pdf_file"):
        with open(st.session_state.pdf_file, "rb") as f2:
            with d2:
                st.download_button("⬇️ Download PDF", f2,
                    file_name=os.path.basename(st.session_state.pdf_file), width="stretch")
    if st.session_state.get("image_file"):
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
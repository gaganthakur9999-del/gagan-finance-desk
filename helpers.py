"""
Generic helper functions for Gagan's Finance Desk.
Formatting, parsing, validation, and reusable utilities.
"""
import logging
import os
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

import streamlit as st

import database as db


# Detect if we're on Windows (local) or Linux (Render cloud)
IS_WINDOWS = sys.platform == "win32"


def show_error(message, exc=None):
    if exc:
        logging.exception(message)
    st.error(message)


def log_activity(activity_type, details):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_message = f"[{activity_type}] {timestamp}: {details}"
    logging.info(log_message)
    activity_log = os.path.join("logs", "activity.log")
    try:
        with open(activity_log, "a", encoding="utf-8") as f:
            f.write(log_message + "\n")
    except (OSError, PermissionError) as e:
        logging.error(f"Failed to write activity log: {e}")


def clean_amount(value):
    return str(value or "").replace(",", "").strip()


def amount_to_float(value):
    try:
        return float(clean_amount(value))
    except ValueError:
        return 0.0


def format_amount(value):
    try:
        return f"{float(clean_amount(value)):,.2f}"
    except ValueError:
        return str(value or "")


def _parse_date(date_str):
    date_str = str(date_str or "").strip()
    date_str = date_str.split(" ")[0]
    if not date_str:
        return None
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    return None


def _normalize_date(date_str):
    dt = _parse_date(date_str)
    return dt.strftime("%d-%m-%Y") if dt else (date_str or "")


def _get_search_history():
    return st.session_state.get("_search_history", [])


def _add_search_history(query):
    if not query:
        return
    history = st.session_state.get("_search_history", [])
    if query in history:
        history.remove(query)
    history.insert(0, query)
    st.session_state["_search_history"] = history[:5]


def _format_month(month_key):
    try:
        month_name, year = month_key.split("_")
        dt = datetime.strptime(month_name, "%B")
        return dt.strftime("%b") + f" {year}"
    except (ValueError, IndexError):
        return month_key.replace("_", " ").title()


def validate_before_generate(invoice_no, invoice_date, serial_no, data, settings):
    errors = []
    invoice_no = str(invoice_no or "").strip()
    if not invoice_no:
        errors.append("❌ Invoice number is required.")
    if not invoice_date.strip():
        errors.append("❌ Invoice date is required.")
    else:
        try:
            datetime.strptime(invoice_date, "%d-%m-%Y")
        except ValueError:
            errors.append("❌ Invoice date format must be DD-MM-YYYY.")
    serial_no = str(serial_no or "").strip()
    if not serial_no:
        errors.append("❌ Serial / IMEI is required.")
    name = str(data.get("name") or "").strip()
    if not name:
        errors.append("❌ Customer name is missing.")
    elif len(name) < 2:
        errors.append("❌ Customer name must be at least 2 characters.")
    product = str(data.get("product") or "").strip()
    if not product:
        errors.append("❌ Product is missing.")
    price_str = clean_amount(data.get("price") or "")
    if not price_str:
        errors.append("❌ Product price is missing.")
    else:
        try:
            price_val = float(price_str)
            if price_val <= 0:
                errors.append("❌ Product price must be greater than 0.")
        except ValueError:
            errors.append("❌ Product price must be a valid number.")
    # Template check - on Linux/Render, template is in current directory
    tmpl_path = settings.get("template_path", "template.docx")
    if not os.path.exists(tmpl_path):
        # On Render, fallback to current directory
        if not IS_WINDOWS:
            tmpl_path = "template.docx"
        if not os.path.exists(tmpl_path):
            errors.append(f"❌ Template file not found: {tmpl_path}")

    # Poppler check - only on Windows (unavailable on Linux/Render)
    if IS_WINDOWS and settings.get("poppler_path"):
        if not os.path.exists(settings["poppler_path"]):
            errors.append(f"❌ Poppler folder not found: {settings['poppler_path']}")
    if db.check_invoice_exists(invoice_no):
        errors.append(f"❌ Invoice number '{invoice_no}' already exists.")
    if db.check_serial_exists(serial_no):
        errors.append(f"❌ Serial number '{serial_no}' already exists.")
    return errors


def records_for_display(records):
    display_records = []
    for record in records:
        display_record = {}
        for key, value in record.items():
            if value is None:
                display_record[key] = ""
            elif isinstance(value, datetime):
                display_record[key] = value.strftime("%d-%m-%Y")
            else:
                display_record[key] = str(value)
        display_records.append(display_record)
    return display_records


def extracted_data_for_display(data):
    labels = {
        "name": "Customer Name", "product": "Product", "price": "Price",
        "mobile": "Phone", "address": "Address", "bid": "BID / DO ID",
        "bid_date": "BID Date", "emi": "EMI", "di": "DI", "scheme": "Scheme",
    }
    return [
        {"Field": labels.get(key, key), "Extracted Value": str(value or "")}
        for key, value in data.items()
    ]
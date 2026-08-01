"""
PDF data extraction module for Gagan's Finance Desk.
Extracts customer/finance data from Bajaj DO PDF files.
"""
import re
from pypdf import PdfReader

from helpers import show_error


def extract_data(pdf_file):
    data = {
        "name": "", "product": "", "price": "", "mobile": "",
        "address": "", "bid": "", "bid_date": "", "emi": "", "di": "", "scheme": "",
    }
    try:
        text = ""
        reader = PdfReader(pdf_file)
        for page in reader.pages:
            extracted = page.extract_text()
            if extracted:
                text += extracted + "\n"
        name_match = re.search(
            r"loan\s*application\s*of\s*Mr/Miss/Mrs\.\s*(.*?)\s*(has|been)",
            text, re.IGNORECASE | re.DOTALL,
        )
        if name_match:
            data["name"] = name_match.group(1).strip()
        if "Model" in text and "EAN Number" in text:
            start_p = text.find("Model") + len("Model")
            end_p = text.find("EAN Number")
            raw_chunk = text[start_p:end_p]
            clean_chunk = raw_chunk.replace('"', "").replace(",", "")
            lines = [line.strip() for line in clean_chunk.split("\n") if line.strip()]
            data["product"] = " - ".join(lines).strip().upper()
        price_match = re.search(
            r"Product\s*Price:?\s*([\d,]+(?:\.\d+)?)",
            text, re.IGNORECASE,
        )
        if price_match:
            data["price"] = price_match.group(1).strip()
        mobile_match = re.search(r"Mobile\s*Number\s*:\s*(\d+)", text)
        if mobile_match:
            data["mobile"] = mobile_match.group(1).strip()
        # Address: find the LAST address label (the real delivery address is at
        # the bottom of the DO). Require a colon so the "EMI Anywhere ... Delivery
        # address mentioned" banner at the top is NOT matched.
        address_match = None
        for m in re.finditer(
            r"(?:Address of the customer for delivery|Delivery Address)\s*:",
            text, re.IGNORECASE,
        ):
            address_match = m
        if address_match:
            start = address_match.end()
            end = text.find("On the delivery of the product", start)
            if end == -1:
                end = len(text)
            # If "Mobile Number:" appears AFTER the label, stop there,
            # but only if it is within a reasonable distance below the label.
            mob = re.search(r"Mobile Number:", text[start:end], re.IGNORECASE)
            if mob and mob.start() < 500:
                end = start + mob.start()
            data["address"] = re.sub(r"\s+", " ", text[start:end]).strip()
        bid_match = re.search(r"DO\s*ID\s*:\s*([A-Z0-9]+)", text)
        if bid_match:
            data["bid"] = bid_match.group(1).strip()
        date_match = re.search(r"Date\s*:\s*(\d{2}/\d{2}/\d{4})", text)
        if date_match:
            data["bid_date"] = date_match.group(1).strip()
        emi_match = re.search(r"Total\s*EMI.*?([\d,]+)", text)
        if emi_match:
            data["emi"] = emi_match.group(1).replace(",", "").strip()
        di_match = re.search(r"Net\s*Disbursement.*?([\d,]+)", text)
        if di_match:
            data["di"] = di_match.group(1).replace(",", "").strip()
        scheme_match = re.search(r"\((\d+/\d+)\)", text)
        if scheme_match:
            data["scheme"] = scheme_match.group(1).strip()
    except (OSError, ValueError) as exc:
        show_error("PDF extraction failed. Please check if the uploaded PDF is readable.", exc)
    return data


def get_missing_fields(data):
    """Return a list of missing critical fields for confidence warnings."""
    missing = []
    if not str(data.get("name") or "").strip():
        missing.append("Name")
    if not str(data.get("price") or "").strip():
        missing.append("Price")
    if not str(data.get("mobile") or "").strip():
        missing.append("Mobile Number")
    return missing
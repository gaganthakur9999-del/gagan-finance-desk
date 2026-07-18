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
        price_match = re.search(r"Product\s*Price.*?([\d,]+\.\d+)", text)
        if price_match:
            data["price"] = price_match.group(1).strip()
        mobile_match = re.search(r"Mobile\s*Number\s*:\s*(\d+)", text)
        if mobile_match:
            data["mobile"] = mobile_match.group(1).strip()
        address_match = re.search(
            r"Address of the customer for delivery:\s*(.*?)\s*Mobile Number:", text, re.S,
        )
        if address_match:
            data["address"] = re.sub(r"\s+", " ", address_match.group(1)).strip()
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
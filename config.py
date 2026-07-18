"""
Configuration module for Gagan's Finance Desk.
Handles settings, constants, and default paths.
"""
import json
import logging
import os
from typing import Any, Dict

APP_NAME = "Gagan's Finance Desk"
EXCEL_DIR = "Excel"
BACKUP_DIR = "Backups"
TEMP_DIR = "temp"
LOG_DIR = "logs"
CONFIG_DIR = "config"
SETTINGS_FILE = os.path.join(CONFIG_DIR, "settings.json")
EXCEL_FILE = os.path.join(EXCEL_DIR, "ALL_RECORDS.xlsx")

HEADERS = [
    "SR NO", "BID DATE", "INVOICE NO", "NAME", "XCELL", "PRODUCT",
    "SERIAL NO / IMEI", "PRICE", "EMI", "DI", "BID", "DP TAKEN",
    "SCHEME", "ACTUAL PRODUCT", "GIVEN PROD PRICE", "PHONE", "ALT PHONE",
    "REMARKS",
]

DEFAULT_SETTINGS = {
    "template_path": "template.docx",
    "poppler_path": r"C:\poppler\Library\bin",
    "gst_rate": 18.0,
    "cgst_rate": 9.0,
    "sgst_rate": 9.0,
    "invoice_prefix": "",
    "theme": "dark",
}


def read_settings() -> Dict[str, Any]:
    if not os.path.exists(SETTINGS_FILE):
        return DEFAULT_SETTINGS.copy()
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)
        return {**DEFAULT_SETTINGS, **saved}
    except (json.JSONDecodeError, OSError) as exc:
        logging.exception("Failed to read settings")
        return DEFAULT_SETTINGS.copy()


def save_settings(settings: Dict[str, Any]) -> None:
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=4)


# Module-level settings (set once at app startup, used by other modules)
settings = read_settings()

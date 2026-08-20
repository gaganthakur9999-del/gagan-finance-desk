"""
Gagan's Finance Desk - V1 (Offline Edition)
Desktop Streamlit app with template-based invoice generation.
"""
# =========================
# CRITICAL: Prevent default Streamlit UI flash
# The default Streamlit HTML shell (header, toolbar, decoration, sidebar) is
# rendered BEFORE Python code runs. To hide these elements instantly:
# 1. st.set_page_config() - FIRST Streamlit call
# 2. Inject CSS via st.markdown IMMEDIATELY - no imports, no I/O delays
# 3. Then everything else
# =========================
import streamlit as st

# Step 1: Set page config - MUST be first Streamlit call
st.set_page_config(page_title="Gagan's Finance Desk", layout="wide", initial_sidebar_state="collapsed")

# Step 2: Inject critical CSS IMMEDIATELY - before any imports that do I/O
# This hides Streamlit's default UI shell elements before they render
st.markdown("""
<style>
[data-testid="stHeader"], [data-testid="stSidebar"], [data-testid="collapsedControl"],
[data-testid="stToolbar"], [data-testid="stDecoration"] { display: none !important; }
.stApp { background: #131720; }
.block-container { padding-top: 1rem; max-width: 94%; }
</style>
""", unsafe_allow_html=True)

# Step 3: Now do I/O - import config (reads settings.json from disk)
from config import APP_NAME, LOG_DIR, CONFIG_DIR, TEMP_DIR, EXCEL_DIR, BACKUP_DIR, settings
from styles import apply_style

# Apply full CSS theme (overrides the inline dark defaults)
apply_style(settings)

# Step 4: Everything else
import logging
import os
from logging.handlers import RotatingFileHandler

for folder in [EXCEL_DIR, BACKUP_DIR, TEMP_DIR, LOG_DIR, CONFIG_DIR]:
    os.makedirs(folder, exist_ok=True)

_handler = RotatingFileHandler(
    os.path.join(LOG_DIR, "errors.log"), maxBytes=5*1024*1024, backupCount=3
)
_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
logging.getLogger().addHandler(_handler)
logging.getLogger().setLevel(logging.ERROR)

# =========================
# PAGE ROUTING
# =========================
# Pages are imported LAZILY inside their routing branch so the Dashboard (or any
# other page) never initializes PDF/DOCX/Excel machinery it does not need.
# Navigation and behavior are unchanged.
from ui_components import render_menu

page = render_menu()

if st.session_state.pop("_theme_changed", False):
    st.rerun()

if page == "Generate Invoice":
    from pages.generate_invoice import page_generate_invoice
    page_generate_invoice()
elif page == "Records":
    from pages.records import page_records
    page_records()
elif page == "Dashboard":
    from pages.dashboard import page_dashboard
    page_dashboard()
elif page == "EMI Notification":
    from pages.emi_notification import page_emi_notification
    page_emi_notification()
else:
    from pages.settings import page_settings
    page_settings()

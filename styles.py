"""
CSS styling module for Gagan's Finance Desk.
Contains apply_style() CSS rendering.
"""
import streamlit as st


def apply_style(settings):
    """Apply CSS based on current theme settings."""
    is_dark = settings.get("theme", "dark") == "dark"
    if is_dark:
        bg_main = "#131720"
        bg_surface = "#232a3a"
        bg_elevated = "#2d3547"
        bg_input = "#222938"
        text_primary = "#e0e4ea"
        text_secondary = "#9aa3b2"
        text_muted = "#8a94a8"
        border = "#2a3344"
        border_soft = "#252d3d"
        accent = "#5B8DB8"
        accent_hover = "#6B9DC4"
    else:
        bg_main = "#eef0f4"
        bg_surface = "#ffffff"
        bg_elevated = "#e2e6ec"
        bg_input = "#ffffff"
        text_primary = "#1a1d23"
        text_secondary = "#5a6270"
        text_muted = "#8a929e"
        border = "#c8cdd4"
        border_soft = "#dde1e6"
        accent = "#2E5A7C"
        accent_hover = "#3A6B8F"

    light_theme_fixes = """
/* === LIGHT THEME FIXES === */
[data-testid="stSelectbox"] > div[data-baseweb="select"] > div,
[data-testid="stSelectbox"] [role="listbox"],
div[data-baseweb="select"] > div,
div[data-baseweb="popover"] > div,
[data-testid="stSelectbox"] input {
    background-color: #ffffff !important;
    color: #1a1d23 !important;
    -webkit-text-fill-color: #1a1d23 !important;
    border-color: #c8cdd4 !important;
}
div[data-baseweb="menu"], ul[data-baseweb="menu"], [role="listbox"] {
    background-color: #ffffff !important;
    color: #1a1d23 !important;
    border: 1px solid #c8cdd4 !important;
}
div[data-baseweb="select"] [role="option"], div[data-baseweb="menu"] div {
    background-color: #ffffff !important; color: #1a1d23 !important;
}
div[data-baseweb="select"] [role="option"]:hover, div[data-baseweb="menu"] div:hover {
    background-color: #eef0f4 !important; color: #1a1d23 !important;
}
[data-testid="stTextInput"] input {
    background-color: #ffffff !important; color: #1a1d23 !important;
    -webkit-text-fill-color: #1a1d23 !important; border-color: #c8cdd4 !important;
}
.stButton button[kind="primary"] {
    background: #2E5A7C !important; border-color: #2E5A7C !important; color: #ffffff !important;
}
.stButton button[kind="secondary"] {
    background: #e2e6ec !important; border-color: #c8cdd4 !important; color: #1a1d23 !important;
}
[data-testid="stCheckbox"] label span { color: #1a1d23 !important; }
.stNumberInput input {
    background: #ffffff !important; color: #1a1d23 !important;
    -webkit-text-fill-color: #1a1d23 !important; border-color: #c8cdd4 !important;
}
.stNumberInput button { background: #e2e6ec !important; color: #1a1d23 !important; border-color: #c8cdd4 !important; }
[data-testid="stFileUploader"] section { background: #ffffff !important; border-color: #c8cdd4 !important; }
[data-testid="stDataFrame"] { background: #ffffff !important; border-color: #dde1e6 !important; }
[data-testid="stMetric"] { background: #ffffff !important; border-color: #dde1e6 !important; }
div[data-testid="stExpander"] details { background: #ffffff !important; border-color: #c8cdd4 !important; }
div[data-testid="stExpander"] summary, div[data-testid="stExpander"] summary p,
div[data-testid="stExpander"] summary span { color: #1a1d23 !important; }
.stAlert { background: rgba(46, 90, 124, 0.08) !important; border-left-color: #2E5A7C !important; }
"""

    st.markdown(
        f"""
        <style>
        .stApp {{ background: {bg_main}; color: {text_primary}; }}
        .block-container {{ padding-top: 1rem; padding-bottom: 1.2rem; max-width: 94%; }}
        [data-testid="stAppViewContainer"] > .main {{ background: {bg_main}; }}
        [data-testid="stHeader"], [data-testid="stSidebar"], [data-testid="collapsedControl"],
        [data-testid="stToolbar"], [data-testid="stDecoration"] {{ display: none !important; }}
        h1, h2, h3, h4, p, label, span, div, li {{ color: {text_primary}; }}
        h1 {{ font-size: 1.55rem !important; margin-bottom: 0.1rem !important; color: {text_primary} !important; }}
        h2, h3 {{ font-size: 1rem !important; margin-top: 0.35rem !important; margin-bottom: 0.2rem !important; color: {text_primary} !important; }}
        strong, b {{ color: {text_primary} !important; }}
        .stCaption, [data-testid="stCaptionContainer"] {{ color: {text_secondary} !important; }}
        hr {{ border-color: {border_soft} !important; }}
        [data-testid="stVerticalBlock"] {{ gap: 0.65rem; }}
        [data-testid="stHorizontalBlock"] {{ gap: 0.9rem; }}
        [data-testid="stWidgetLabel"] {{ min-height: 0.85rem; margin-bottom: 0.1rem; }}
        [data-testid="stWidgetLabel"] p {{ font-size: 0.82rem; color: {text_secondary} !important; }}
        .stTextInput input, .stNumberInput input {{
            min-height: 2.25rem; padding-top: 0.28rem; padding-bottom: 0.28rem;
            background: {bg_input} !important; color: {text_primary} !important;
            -webkit-text-fill-color: {text_primary} !important; border: 1px solid {border} !important; border-radius: 0.55rem;
        }}
        .stTextArea textarea {{
            min-height: 4.1rem !important; padding-top: 0.25rem; padding-bottom: 0.25rem;
            background: {bg_input} !important; color: {text_primary} !important;
            -webkit-text-fill-color: {text_primary} !important; border: 1px solid {border} !important; border-radius: 0.55rem;
        }}
        .stTextInput input::placeholder, .stTextArea textarea::placeholder {{
            color: {text_muted} !important; -webkit-text-fill-color: {text_muted} !important; opacity: 1 !important;
        }}
        .stNumberInput button {{ background: {bg_elevated} !important; color: {text_primary} !important; border-color: {border} !important; }}
        [data-testid="stCheckbox"] label span {{ color: {text_primary} !important; }}
        [data-testid="stFileUploader"] section {{ padding: 0.65rem; background: {bg_surface}; border: 1px solid {border}; border-radius: 0.6rem; }}
        .stButton button, .stDownloadButton button {{
            min-height: 2.35rem; padding-top: 0.35rem; padding-bottom: 0.35rem; border-radius: 0.55rem;
            background: {bg_elevated}; color: {text_primary}; border: 1px solid {border}; font-weight: 600;
        }}
        .stButton button:hover, .stDownloadButton button:hover {{ background: {bg_elevated}; color: {text_primary}; border-color: {accent}; }}
        .stButton button[kind="primary"] {{ background: {accent}; border-color: {accent}; color: #eef4f8; }}
        .stButton button[kind="primary"]:hover {{ background: {accent_hover}; border-color: {accent_hover}; color: #eef4f8; }}
        .stAlert {{ padding-top: 0.35rem; padding-bottom: 0.35rem; border-radius: 0.55rem; }}
        div[data-testid="stExpander"] details {{ padding-top: 0; background: {bg_surface}; border: 1px solid {border}; border-radius: 0.6rem; }}
        div[data-testid="stExpander"] summary, div[data-testid="stExpander"] summary p,
        div[data-testid="stExpander"] summary span {{ color: {text_primary} !important; font-weight: 600; }}
        [data-testid="stFileUploader"] p, [data-testid="stFileUploader"] span,
        [data-testid="stFileUploader"] small {{ color: {text_secondary} !important; }}
        [data-testid="stFileUploader"] button {{ background: {bg_elevated} !important; color: {text_primary} !important; border: 1px solid {border} !important; }}
        [data-testid="stDataFrame"] {{ background: {bg_surface}; border: 1px solid {border_soft}; border-radius: 0.6rem; }}
        [data-testid="stDataFrame"] thead th {{ position: sticky !important; top: 0 !important; z-index: 5 !important; background: {bg_surface} !important; }}
        [data-testid="stMetric"] {{ background: {bg_surface}; border: 1px solid {border_soft}; border-radius: 0.6rem; padding: 0.65rem 0.85rem; }}
        [data-testid="stMetricLabel"] {{ color: {text_secondary} !important; }}
        [data-testid="stMetricValue"] {{ color: {text_primary} !important; }}
        .invoice-centered {{ display: flex; justify-content: center; width: 100%; margin: 1rem 0; }}
        .invoice-centered img {{ max-width: 100%; height: auto; border: 1px solid {border_soft}; border-radius: 6px; }}
        .quick-stats {{ background: {bg_surface}; border: 1px solid {border_soft}; border-radius: 0.6rem; padding: 0.5rem 1rem; margin-bottom: 0.8rem; }}
        .quick-stats span {{ color: {text_secondary}; font-size: 0.85rem; margin-right: 1.5rem; }}
        .quick-stats strong {{ color: {accent}; }}
        .inv-modal-overlay {{ display: none; position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.88); z-index: 99999; justify-content: center; align-items: center; }}
        .inv-modal-overlay.active {{ display: flex !important; }}
        .inv-modal-content {{ background: white; padding: 12px; border-radius: 8px; max-width: 94vw; max-height: 92vh; overflow: hidden; position: relative; }}
        .inv-modal-content img {{ display: block; max-width: 92vw; max-height: 88vh; object-fit: contain; }}
        .inv-modal-close {{ position: absolute; top: 4px; right: 8px; background: #e74c3c; color: white; border: none; border-radius: 50%; width: 28px; height: 28px; font-size: 16px; cursor: pointer; display: flex; align-items: center; justify-content: center; z-index: 100000; }}
        .inv-modal-close:hover {{ background: #c0392b; }}
        .stTextInput input:focus, .stTextArea textarea:focus {{ box-shadow: 0 0 0 3px rgba(91, 141, 184, 0.15) !important; }}
        .stButton button, .stDownloadButton button {{ transition: all 0.15s ease; }}
        .stButton button:hover, .stDownloadButton button:hover {{ transform: translateY(-1px); box-shadow: 0 2px 8px rgba(0,0,0,0.15); }}
        .quick-stats {{ border-left: 3px solid #5B8DB8; }}
        .invoice-centered img {{ box-shadow: 0 4px 20px rgba(0,0,0,0.25); }}
        [data-testid="stDataFrame"] tr:hover td {{ background: rgba(91, 141, 184, 0.06) !important; }}
        ::selection {{ background: rgba(91, 141, 184, 0.25); color: #ffffff; }}
        div[data-testid="stExpander"] summary svg {{ color: #5B8DB8 !important; }}
        {light_theme_fixes if not is_dark else ''}

        /* ==========================================
           MOBILE RESPONSIVE — works on Android Chrome
           ========================================== */
        @media only screen and (max-width: 768px) {{
            .block-container {{ max-width: 100% !important; padding-left: 0.5rem !important; padding-right: 0.5rem !important; }}
            [data-testid="stHorizontalBlock"] {{ flex-wrap: wrap !important; gap: 0.4rem !important; }}
            [data-testid="stHorizontalBlock"] > div {{ flex: 1 1 100% !important; min-width: 0 !important; }}
            [data-testid="stHorizontalBlock"] > div:has(> .stButton) {{ flex: 1 1 auto !important; }}
            h1 {{ font-size: 1.2rem !important; }}
            h2, h3 {{ font-size: 0.95rem !important; }}
            .stButton button, .stDownloadButton button {{ min-height: 2.6rem !important; font-size: 0.9rem !important; }}
            .stTextInput input, .stNumberInput input {{ min-height: 2.5rem !important; font-size: 1rem !important; }}
            .stTextArea textarea {{ min-height: 3.5rem !important; font-size: 1rem !important; }}
            [data-testid="stMetric"] {{ padding: 0.4rem 0.6rem !important; }}
            [data-testid="stMetricValue"] {{ font-size: 1.1rem !important; }}
            [data-testid="stMetricLabel"] {{ font-size: 0.75rem !important; }}
            div[data-testid="stExpander"] {{ margin: 0.3rem 0 !important; }}
            div[data-testid="stExpander"] details {{ padding: 0.3rem !important; }}
            [data-testid="stDataFrame"] {{ font-size: 0.75rem !important; }}
            [data-testid="stDataFrame"] td, [data-testid="stDataFrame"] th {{ padding: 0.25rem 0.3rem !important; white-space: nowrap !important; }}
            [data-testid="stSelectbox"] > div {{ min-height: 2.5rem !important; }}
            [data-testid="stSelectbox"] input {{ font-size: 0.9rem !important; }}
            .stNumberInput button {{ min-height: 2.2rem !important; min-width: 2.2rem !important; }}
            [data-testid="stCheckbox"] label {{ font-size: 0.85rem !important; }}
            .stAlert {{ font-size: 0.85rem !important; padding: 0.3rem 0.5rem !important; }}
            .quick-stats {{ display: flex !important; flex-wrap: wrap !important; gap: 0.3rem !important; }}
            .quick-stats span {{ font-size: 0.78rem !important; margin-right: 0.5rem !important; }}
            .invoice-centered img {{ max-width: 100% !important; }}
            [data-testid="stFileUploader"] section {{ padding: 0.4rem !important; }}
            [data-testid="stVerticalBlock"] {{ gap: 0.4rem !important; }}
            .st-b7 {{ padding: 0.3rem 0 !important; }}
        }}
        @media only screen and (max-width: 480px) {{
            h1 {{ font-size: 1rem !important; }}
            [data-testid="stMetricValue"] {{ font-size: 0.95rem !important; }}
            .stButton button, .stDownloadButton button {{ font-size: 0.82rem !important; min-height: 2.4rem !important; }}
            [data-testid="stDataFrame"] {{ font-size: 0.65rem !important; }}
            [data-testid="stDataFrame"] td, [data-testid="stDataFrame"] th {{ padding: 0.15rem 0.2rem !important; }}
            .quick-stats span {{ font-size: 0.72rem !important; }}
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )

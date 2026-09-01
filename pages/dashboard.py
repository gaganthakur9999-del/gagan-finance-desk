"""
Dashboard page for Gagan's Finance Desk.
"""
from datetime import datetime

import streamlit as st

import calendar
from config import settings
from database import month_sort_key
from helpers import _normalize_date, _parse_date
from ui_components import app_header
import database as db


def page_dashboard():
    app_header()

    # Month selector
    available_months = db.get_available_months()
    month_options = ["ALL_MONTHS"] + available_months
    month_labels = ["📅 All Months"] + available_months
    current_month = datetime.now().strftime("%B_%Y").upper()
    default_idx = 0
    if current_month in available_months:
        default_idx = month_options.index(current_month)
    selected_month = st.selectbox(
        "Select Month", options=month_options,
        format_func=lambda x: month_labels[month_options.index(x)] if x in month_options else x,
        index=default_idx,
    )
    month_param = selected_month if selected_month != "ALL_MONTHS" else ""
    # When a specific month is selected, the monthly GROUP BY isn't displayed,
    # so avoid computing it (saves a full group-by scan on every rerun).
    stats = db.get_dashboard_stats(
        month=month_param,
        include_monthly_counts=(selected_month == "ALL_MONTHS"),
    )
    c1, c2, c3 = st.columns(3)
    c1.metric("📋 Total Records", f"{stats.get('total_records', 0):,}")
    c2.metric("💵 Total DP", f"₹{stats.get('total_dp', 0):,.2f}")
    c3.metric("💤 Total DI", f"₹{stats.get('total_di', 0):,.2f}")

    st.subheader("📊 Records Overview")
    if selected_month == "ALL_MONTHS":
        monthly_counts = stats.get("monthly_counts", {})
        if monthly_counts:
            sorted_months = sorted(monthly_counts.keys(), key=month_sort_key)
            chart_data = {m: monthly_counts[m] for m in sorted_months}
            st.bar_chart(chart_data, color="#4d8fad", sort=False)
            st.caption("Records per month")
        else:
            st.info("No data available yet.")
    else:
        daily_counts = stats.get("daily_counts", {})
        daily_counts_norm = {}
        for k, v in daily_counts.items():
            norm = _normalize_date(k)
            if norm:
                daily_counts_norm[norm] = daily_counts_norm.get(norm, 0) + v
        daily_counts = daily_counts_norm
        if daily_counts:
            try:
                month_dt = datetime.strptime(month_param, "%B_%Y")
                _, last_day = calendar.monthrange(month_dt.year, month_dt.month)
                sorted_dates = [f"{d:02d}-{month_dt.month:02d}-{month_dt.year}" for d in range(1, last_day + 1)]
                chart_data = {d: daily_counts.get(d, 0) for d in sorted_dates}
                st.bar_chart(chart_data, color="#4d8fad", sort=False)
                st.caption(f"Records per day - {month_param.replace('_', ' ').title()}")
            except (ValueError, TypeError):
                sorted_d = sorted(daily_counts.keys(), key=lambda x: _parse_date(x) or datetime.min)
                chart_data = {d: daily_counts[d] for d in sorted_d}
                st.bar_chart(chart_data, color="#4d8fad", sort=False)
                st.caption("Records per day")
        else:
            st.info("No daily data.")
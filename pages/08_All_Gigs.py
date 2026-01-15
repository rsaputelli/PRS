# pages/08_All_Gigs.py
from __future__ import annotations

import streamlit as st
import pandas as pd
from datetime import date

from auth_helper import require_login, sb
from lib.ui_header import render_header

# ===============================
# Auth (any logged-in user)
# ===============================
user, session, user_id = require_login()

render_header("All Gigs")

st.caption(
    "This page shows the full gig calendar. "
    "Assignments and compensation details are not shown."
)

# ===============================
# Load gigs (SAFE columns only)
# ===============================

cols = (
    "id, event_date, title, contract_status, "
    "start_time, end_time, "
    "venues(name)"
)

res = (
    sb.table("gigs")
    .select(cols)
    .order("event_date", desc=False)
    .execute()
)

rows = res.data or []
df = pd.DataFrame(rows)

if df.empty:
    st.info("No gigs found.")
    st.stop()

# ===============================
# Normalize / clean for display
# ===============================

df["event_date"] = pd.to_datetime(df["event_date"]).dt.date

df["Venue"] = df["venues"].apply(
    lambda v: v.get("name") if isinstance(v, dict) else ""
)

df = df.drop(columns=["venues"])

df = df.rename(columns={
    "event_date": "Date",
    "title": "Gig",
    "start_time": "Start",
    "end_time": "End",
    "contract_status": "Status",
})

# ===============================
# Optional filters (lightweight)
# ===============================

with st.expander("Filters", expanded=False):
    col1, col2 = st.columns(2)

    with col1:
        status_filter = st.multiselect(
            "Status",
            sorted(df["Status"].dropna().unique().tolist()),
            default=sorted(df["Status"].dropna().unique().tolist()),
        )

    with col2:
        venue_search = st.text_input("Search venue", "")

filtered = df.copy()

if status_filter:
    filtered = filtered[filtered["Status"].isin(status_filter)]

if venue_search:
    filtered = filtered[
        filtered["Venue"]
        .str.contains(venue_search, case=False, na=False)
    ]

# ===============================
# Display
# ===============================

st.dataframe(
    filtered.sort_values("Date"),
    use_container_width=True,
    hide_index=True,
)

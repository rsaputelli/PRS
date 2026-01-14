##--- pages/08_Sound_Tech_Schedule.py

from __future__ import annotations

import streamlit as st
import pandas as pd
from datetime import date

from auth_helper import require_login, sb
from lib.ui_header import render_header


# ===============================
# Auth
# ===============================
user, session, user_id = require_login()
auth_email = (user.email or "").lower().strip()


# ===============================
# Resolve sound tech
# ===============================
res = (
    sb.table("sound_techs")
    .select("id, display_name, email")
    .eq("email", auth_email)
    .limit(1)
    .execute()
)

if not res.data:
    st.error(
        "Your account is not linked to a sound tech record. "
        "Please contact the administrator."
    )
    st.stop()

sound_tech = res.data[0]


# ===============================
# Header
# ===============================
render_header("Sound Tech Schedule")

st.markdown(
    f"<div style='font-size:20px; font-weight:600; margin-top:-6px;'>"
    f"{sound_tech.get('display_name') or auth_email}"
    f"</div>",
    unsafe_allow_html=True,
)

st.markdown("---")


# ===============================
# Load gigs
# ===============================
res = (
    sb.table("gigs")
    .select(
        """
        id,
        title,
        event_date,
        start_time,
        end_time,
        sound_fee,
        contract_status,
        venues(name)
        """
    )
    .eq("sound_tech_id", sound_tech["id"])
    .execute()
)

gigs = pd.DataFrame(res.data or [])

if gigs.empty:
    st.info("No gigs assigned.")
    st.stop()

# Extract venue name from join
gigs["venue"] = gigs["venues"].apply(
    lambda v: v.get("name") if isinstance(v, dict) else ""
)


# ===============================
# Normalize
# ===============================
gigs["event_date"] = pd.to_datetime(gigs["event_date"]).dt.date

def fmt_time(val):
    if not val:
        return ""
    try:
        return pd.to_datetime(val).strftime("%I:%M %p").lstrip("0")
    except Exception:
        return str(val)

gigs["start_time"] = gigs["start_time"].apply(fmt_time)
gigs["end_time"] = gigs["end_time"].apply(fmt_time)


# ===============================
# Date filter
# ===============================
today = date.today()
scope = st.radio(
    "Show:",
    ["Future gigs only", "All gigs"],
    horizontal=True,
)

if scope == "Future gigs only":
    gigs = gigs[gigs["event_date"] >= today]

if gigs.empty:
    st.info("No gigs in this date range.")
    st.stop()


# ===============================
# Display
# ===============================
display = gigs[
    [
        "event_date",
        "title",
        "venue",
        "start_time",
        "end_time",
        "sound_fee",
        "contract_status",
    ]
].copy()

display = display.sort_values(["event_date", "start_time"])

# Format fee
display["sound_fee"] = display["sound_fee"].apply(
    lambda x: f"${x:,.2f}" if x is not None else ""
)

# Rename for UI
display.columns = ["Date", "Title", "Venue", "Start", "End", "Fee", "Status"]

st.subheader("Assigned Gigs")
st.dataframe(display, use_container_width=True, hide_index=True)


##--- pages/08_Sound_Tech_Schedule.py

from __future__ import annotations

import streamlit as st
import pandas as pd
from datetime import date

from auth_helper import require_login, sb
from lib.ui_header import render_header
from lib.calendar_utils import make_ics_bytes
import datetime as dt
from zoneinfo import ZoneInfo

# ===============================
# Auth
# ===============================
user, session, user_id = require_login()
auth_email = (user.email or "").lower().strip()


# ===============================
# AUTO-LINK AUTH USER → SOUND TECH
# (one-time, email-based)
# ===============================

sound_tech = (
    sb.table("sound_techs")
    .select("*")
    .eq("user_id", user_id)
    .maybe_single()
    .execute()
    .data
)

# If not yet linked, try email-based match
if not sound_tech and auth_email:
    sound_tech = (
        sb.table("sound_techs")
        .select("*")
        .eq("email", auth_email)
        .maybe_single()
        .execute()
        .data
    )

    if sound_tech:
        sb.table("sound_techs").update(
            {"user_id": user_id}
        ).eq("id", sound_tech["id"]).execute()

# Final gate
if not sound_tech:
    st.warning(
        "Your account is not linked to a sound technician record. "
        "Please contact the administrator."
    )
    st.stop()


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
# ICS helper (Sound Tech Schedule)
# ===============================
from lib.calendar_utils import make_ics_bytes
import datetime as dt
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo("America/New_York")

def build_sound_tech_ics(row: dict) -> bytes:
    starts_at = dt.datetime.combine(
        row["event_date"], row["_start_time_raw"], tzinfo=LOCAL_TZ
    )
    ends_at = dt.datetime.combine(
        row["event_date"], row["_end_time_raw"], tzinfo=LOCAL_TZ
    )

    fee_str = f"${row['sound_fee']:,.2f}" if row.get("sound_fee") is not None else ""

    desc = (
        f"PRS Gig Assignment\n\n"
        f"Role: Sound Tech\n"
        f"Gig: {row.get('title','')}\n"
        f"Venue: {row.get('venue','')}\n"
        f"Start: {row.get('start_time','')}\n"
        f"End: {row.get('end_time','')}\n"
        f"Fee: {fee_str}\n"
        f"Status: {row.get('contract_status','')}\n\n"
        f"Imported from PRS"
    )

    return make_ics_bytes(
        uid=f"prs-soundtech-{row['id']}",
        title=f"{row.get('title','')} (Sound)",
        starts_at=starts_at,
        ends_at=ends_at,
        location=row.get("venue", ""),
        description=desc,
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

# ---- STEP C: preserve raw times for ICS ----
gigs["_start_time_raw"] = pd.to_datetime(gigs["start_time"]).dt.time
gigs["_end_time_raw"] = pd.to_datetime(gigs["end_time"]).dt.time

# ---- Display formatting (UI only) ----
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
# Status filter
# ===============================
st.subheader("Filters")

status_filter = st.multiselect(
    "Contract status",
    ["Pending", "Hold", "Confirmed"],
    default=["Pending", "Hold", "Confirmed"],
)

if status_filter and "contract_status" in gigs.columns:
    gigs = gigs[gigs["contract_status"].isin(status_filter)]

st.caption(
    "**Contract Status definitions:**\n\n"
    "**Confirmed** = fully executed and locked in.\n"
    "**Hold** = solid inquiry; date is being held pending confirmation.\n"
    "**Pending** = verbally confirmed, awaiting final contract (common for private events)."
)

if gigs.empty:
    st.info("No gigs match the selected filters.")
    st.stop()

# ===============================
# RENDER TABLE + ICS
# ===============================
st.subheader("Assigned Gigs")

# Sort for display
gigs = gigs.sort_values(
    ["event_date", "_start_time_raw"],
    ascending=[True, True],
)

# Column headers
hcols = st.columns([2.5, 3, 3, 2, 2, 2, 3, 3])
hcols[0].markdown("**Date**")
hcols[1].markdown("**Title**")
hcols[2].markdown("**Venue**")
hcols[3].markdown("**Start**")
hcols[4].markdown("**End**")
hcols[5].markdown("**Fee**")
hcols[6].markdown("**Status**")
hcols[7].markdown("**Calendar**")

st.markdown("---")

for _, row in gigs.iterrows():
    cols = st.columns([2.5, 3, 3, 2, 2, 2, 3, 3])

    d = row.get("event_date")
    cols[0].write(d.strftime("%m-%d-%Y") if hasattr(d, "strftime") else "")
    cols[1].write(row.get("title", ""))
    cols[2].write(row.get("venue", ""))
    cols[3].write(row.get("start_time", ""))
    cols[4].write(row.get("end_time", ""))

    fee = row.get("sound_fee")
    cols[5].write(f"${fee:,.2f}" if fee is not None else "")
    cols[6].write(row.get("contract_status", ""))

    ics_bytes = build_sound_tech_ics(row)
    cols[7].download_button(
        label="📅 ICS",
        data=ics_bytes,
        file_name=f"{row.get('title','gig')}-sound.ics",
        mime="text/calendar",
        key=f"ics-sound-{row.get('id')}",
    )




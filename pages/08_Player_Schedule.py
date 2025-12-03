from __future__ import annotations
import streamlit as st
import pandas as pd
from datetime import datetime, time
from typing import List, Dict, Optional
from supabase import Client, create_client  # (currently unused, but harmless)

from lib.ui_header import render_header
from lib.calendar_utils import make_ics_download
from lib.email_utils import _fetch_musicians_map
from Master_Gig_App import _select_df, _IS_ADMIN, _get_logged_in_user


def fmt_time_range(start, end):
    """Safe time range formatter (HH:MM–HH:MM)."""
    if not start and not end:
        return ""
    try:
        s = str(start)[:5] if start else ""
        e = str(end)[:5] if end else ""
        if s == e:
            return s
        return f"{s}–{e}"
    except Exception:
        return ""


def fmt_date(d):
    """Mirror Schedule View's human-readable date format."""
    if d is None or (isinstance(d, float) and pd.isna(d)):
        return ""
    try:
        return pd.to_datetime(d).strftime("%a %b %-d, %Y")
    except Exception:
        return str(d)


# ======================================================
# Page Header
# ======================================================
render_header("Schedule (Fee-Free View)")

user = _get_logged_in_user()
if not user:
    st.error("You must be logged in to view this page.")
    st.stop()

user_email = user.get("email")
user_name  = user.get("full_name", user_email)
user_id    = str(user.get("id"))

st.markdown(f"**Logged in as:** {user_name}")


# ======================================================
# View selector
# ======================================================
mode = st.radio(
    "View:",
    ["My Gigs Only", "All Gigs"],
    horizontal=True,
)


# ======================================================
# Contract Status Filter
# ======================================================
status_filter = st.multiselect(
    "Show gigs with status:",
    ["Pending", "Hold", "Confirmed"],
    default=["Pending", "Hold", "Confirmed"],
)


# ======================================================
# Load gigs
# ======================================================
gigs_df = _select_df("gigs", "*")
if gigs_df.empty:
    st.info("No gigs available.")
    st.stop()

# Convert date
if "event_date" in gigs_df.columns:
    gigs_df["event_date"] = pd.to_datetime(
        gigs_df["event_date"], errors="coerce"
    ).dt.date

# Apply status filter
if "contract_status" in gigs_df.columns:
    gigs_df = gigs_df[gigs_df["contract_status"].isin(status_filter)]


# ======================================================
# Upcoming Only Filter
# ======================================================
upcoming_only = st.checkbox("Show upcoming gigs only", value=True)
today = datetime.today().date()

if upcoming_only:
    gigs_df = gigs_df[gigs_df["event_date"] >= today]


# ======================================================
# Search Bar
# ======================================================
search_text = st.text_input("Search gigs (title, venue, players)", "").strip().lower()


# ======================================================
# My Gigs filter
# ======================================================
gm = _select_df("gig_musicians", "*")
gm["musician_id"] = gm["musician_id"].astype(str)

if mode == "My Gigs Only":
    my_gig_ids = gm.loc[gm["musician_id"] == user_id, "gig_id"].astype(str).unique()
    gigs_df = gigs_df[gigs_df["id"].astype(str).isin(my_gig_ids)]


# ======================================================
# Render gigs
# ======================================================
if gigs_df.empty:
    st.info("No gigs match your filters.")
    st.stop()

# Sort chronologically
gigs_df = gigs_df.sort_values("event_date")


for _, g in gigs_df.iterrows():
    gid = str(g["id"])
    title = g.get("title", "(untitled)")
    date  = g.get("event_date")
    start = g.get("start_time")
    end   = g.get("end_time")
    status = g.get("contract_status")

    # Venue
    venue = _select_df("venues", "*", where_eq={"id": g.get("venue_id")}, limit=1)
    venue_name = venue.iloc[0]["name"] if not venue.empty else "(venue)"
    venue_addr = venue.iloc[0]["address"] if not venue.empty else ""

    # Lineup
    gm_rows = gm[gm["gig_id"].astype(str) == gid]
    lineup_ids = gm_rows["musician_id"].astype(str).tolist()
    mus_map = _fetch_musicians_map(lineup_ids)
    lineup_text = ", ".join([
        f"{mus_map[mid]['name']} ({gm_rows.loc[gm_rows['musician_id']==mid,'role'].iloc[0]})"
        for mid in lineup_ids if mid in mus_map
    ])

    # Additional lineup-based search filtering
    if search_text:
        full_text = " ".join([
            title.lower(),
            venue_name.lower(),
            lineup_text.lower()
        ])
        if search_text not in full_text:
            continue

    # Notes
    notes_public = g.get("notes") or ""

    # Card UI
    st.markdown("### " + title)
    st.write(f"**Status:** {status}")
    st.write(f"**When:** {fmt_date(date)} — {fmt_time_range(start, end)}")
    st.write(f"**Venue:** {venue_name}<br>{venue_addr}", unsafe_allow_html=True)
    st.write(f"**Lineup:** {lineup_text}")
    if notes_public.strip():
        st.write(f"**Notes:** {notes_public}")

    # ICS download button
    ics_bytes = make_ics_download(gid)
    if ics_bytes:
        st.download_button(
            "Download ICS",
            data=ics_bytes,
            file_name=f"{title}.ics",
            mime="text/calendar"
        )

    st.markdown("---")

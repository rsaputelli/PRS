# pages/08_Player_Schedule.py

from __future__ import annotations
import streamlit as st
import pandas as pd
from datetime import datetime, time
from typing import List, Dict, Optional

from lib.ui_header import render_header
from lib.email_utils import _fetch_musicians_map
from Master_Gig_App import _select_df, _get_logged_in_user


def fmt_date(d) -> str:
    """Simple human-readable date formatter, similar to Schedule View."""
    if d is None or (isinstance(d, float) and pd.isna(d)):
        return ""
    try:
        return pd.to_datetime(d).strftime("%a %b %-d, %Y")
    except Exception:
        return str(d)


def fmt_time_range(start, end) -> str:
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


# ======================================================
# Page Header / Auth
# ======================================================
render_header("Schedule (Fee-Free View)")

user = _get_logged_in_user()
if not user:
    st.error("You must be logged in to view this page.")
    st.stop()

user_email = user.get("email")
user_name = user.get("full_name", user_email)
user_id = str(user.get("id"))

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
if not gm.empty:
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
    date = g.get("event_date")
    start = g.get("start_time")
    end = g.get("end_time")
    status = g.get("contract_status")

    # Venue
    venue = _select_df("venues", "*", where_eq={"id": g.get("venue_id")}, limit=1)
    venue_name = venue.iloc[0]["name"] if not venue.empty else "(venue)"
    venue_addr = venue.iloc[0]["address"] if not venue.empty else ""

    # Lineup
    gm_rows = gm[gm["gig_id"].astype(str) == gid] if not gm.empty else pd.DataFrame()
    lineup_ids = gm_rows["musician_id"].astype(str).tolist() if not gm_rows.empty else []
    mus_map = _fetch_musicians_map(lineup_ids) if lineup_ids else {}
    lineup_parts = []
    for mid in lineup_ids:
        if mid in mus_map:
            name = mus_map[mid].get("name") or mus_map[mid].get("display_name") or "(unknown)"
            role_series = gm_rows.loc[gm_rows["musician_id"] == mid, "role"]
            role = role_series.iloc[0] if not role_series.empty else ""
            if role:
                lineup_parts.append(f"{name} ({role})")
            else:
                lineup_parts.append(name)
    lineup_text = ", ".join(lineup_parts)

    # Additional lineup-based search filtering
    if search_text:
        full_text = " ".join([
            str(title).lower(),
            str(venue_name).lower(),
            lineup_text.lower(),
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
    st.write(f"**Lineup:** {lineup_text or '(none)'}")
    if notes_public.strip():
        st.write(f"**Notes:** {notes_public}")

    st.markdown("---")

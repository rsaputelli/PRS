# pages/08_Player_Schedule.py

from __future__ import annotations
import streamlit as st
import pandas as pd
from datetime import datetime, date, time
from typing import Optional, Dict, Any

import os
from supabase import create_client, Client

# ==========================================
# Supabase Auth — identical to Edit Gig / Schedule View
# ==========================================

def _get_secret(name: str, required=False) -> Optional[str]:
    val = st.secrets.get(name) or os.environ.get(name)
    if required and not val:
        st.error(f"Missing required secret: {name}")
        st.stop()
    return val

SUPABASE_URL = _get_secret("SUPABASE_URL", required=True)
SUPABASE_ANON_KEY = _get_secret("SUPABASE_ANON_KEY", required=True)
sb: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)

# Restore session if tokens present
if (
    "sb_access_token" in st.session_state
    and st.session_state["sb_access_token"]
    and "sb_refresh_token" in st.session_state
    and st.session_state["sb_refresh_token"]
):
    try:
        sb.auth.set_session(
            access_token=st.session_state["sb_access_token"],
            refresh_token=st.session_state["sb_refresh_token"],
        )
    except Exception:
        st.error("Your session has expired. Please log in again.")
        st.stop()

# Auth gate
if "user" not in st.session_state or not st.session_state["user"]:
    st.error("Please log in from the Login page.")
    st.stop()

USER = st.session_state["user"]

# ==========================================
# Local _select_df — same as Schedule View
# ==========================================
def _select_df(table: str, *, where_eq: dict | None = None, limit: int | None = None):
    q = sb.table(table).select("*")
    if where_eq:
        for col, val in where_eq.items():
            q = q.eq(col, val)
    if limit:
        q = q.limit(limit)
    resp = q.execute()
    return pd.DataFrame(resp.data or [])

# ==========================================
# Header
# ==========================================
from lib.ui_header import render_header
render_header("My Schedule")

# ==========================================
# Helpers for formatting (same as Schedule View)
# ==========================================

def fmt_date(val):
    if not val or pd.isna(val):
        return ""
    try:
        return pd.to_datetime(val).strftime("%a %b %-d, %Y")
    except Exception:
        return str(val)

def fmt_time_range(start_raw, end_raw):
    def to_t(t):
        if not t or pd.isna(t):
            return None
        try:
            return datetime.strptime(t, "%H:%M:%S").strftime("%-I:%M %p")
        except:
            return str(t)

    st = to_t(start_raw)
    en = to_t(end_raw)
    if st and en:
        return f"{st}–{en}"
    return st or ""

# ==========================================
# Load gigs the logged-in player is assigned to
# ==========================================

email = USER.get("email", "").strip().lower()

if not email:
    st.error("Could not determine your email address.")
    st.stop()

# Step 1: find musician ID
mus_df = sb.table("musicians").select("*").eq("email", email).execute()
musician_rows = mus_df.data or []

if not musician_rows:
    st.warning("You are logged in, but no musician record is associated with your email.")
    st.stop()

musician = musician_rows[0]
musician_id = musician["id"]

# Step 2: find gigs assigned to this musician
assign_df = sb.table("gig_musicians").select("*").eq("musician_id", musician_id).execute()
assign_rows = assign_df.data or []

if not assign_rows:
    st.info("You have no scheduled gigs.")
    st.stop()

gig_ids = [row["gig_id"] for row in assign_rows]

# Step 3: load gigs
gigs = _select_df("gigs").query("id in @gig_ids")

if gigs.empty:
    st.info("You have no scheduled gigs.")
    st.stop()

# Sort by date/time
gigs["_sdt"] = pd.to_datetime(gigs["event_date"], errors="coerce")
gigs = gigs.sort_values("_sdt")

# ==========================================
# Display
# ==========================================

st.markdown("### Your Upcoming Gigs")

for _, r in gigs.iterrows():
    dt = fmt_date(r.get("event_date"))
    time_range = fmt_time_range(r.get("start_time"), r.get("end_time"))
    title = r.get("title") or "(untitled)"
    venue = r.get("venue_name") or ""  # If you want venue lookup, we can add it

    st.markdown(
        f"""
        **{dt}**  
        **{title}**  
        {time_range}  
        {venue}  
        ---
        """
    )

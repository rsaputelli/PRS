# pages/08_Player_Schedule.py
from __future__ import annotations

import streamlit as st
import pandas as pd
from datetime import datetime, date
from supabase import create_client, Client

from lib.auth import is_logged_in, current_user, IS_ADMIN
from lib.ui_header import render_header


# ===============================
# Supabase Init
# ===============================
def _get_secret(name, required: bool = False):
    val = st.secrets.get(name)
    if required and not val:
        st.error(f"Missing secret: {name}")
        st.stop()
    return val


SUPABASE_URL = _get_secret("SUPABASE_URL", required=True)
SUPABASE_ANON_KEY = _get_secret("SUPABASE_ANON_KEY", required=True)

sb: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)

# Attach session if available
if (
    st.session_state.get("sb_access_token")
    and st.session_state.get("sb_refresh_token")
):
    try:
        sb.auth.set_session(
            access_token=st.session_state["sb_access_token"],
            refresh_token=st.session_state["sb_refresh_token"],
        )
    except Exception:
        pass


# ===============================
# AUTH — Unified PRS Login Model
# ===============================
if not is_logged_in():
    st.error("Please log in to view your schedule.")
    st.stop()

USER = current_user()
email = (USER.get("email") or "").lower().strip()


# ===============================
# Helper: generic select to DataFrame
# (mirrors 02_Schedule_View)
# ===============================
def _select_df(table: str, select: str = "*", where_eq: dict | None = None) -> pd.DataFrame:
    try:
        q = sb.table(table).select(select)
        if where_eq:
            for k, v in where_eq.items():
                q = q.eq(k, v)
        data = q.execute().data or []
        return pd.DataFrame(data)
    except Exception as e:
        st.warning(f"{table} query failed: {e}")
        return pd.DataFrame()


# ===============================
# ROLE DETECTION (Option C)
# ===============================
def get_profile_record():
    """Try to pull the user's profile row. If none exists, return None."""
    try:
        res = sb.table("profiles").select("*").eq("email", email).limit(1).execute()
        rows = res.data or []
        return rows[0] if rows else None
    except Exception:
        return None


profile = get_profile_record()

if IS_ADMIN():
    role = "admin"
elif profile and profile.get("role"):
    role = profile["role"]
else:
    # BEFORE profiles exist, musicians get blocked from "My Gigs" but can still see "All Gigs"
    role = "guest"   # means logged-in but unmapped user


# ===============================
# HEADER
# ===============================
render_header("My Schedule", emoji="🎸")
st.markdown("---")


# ===============================
# MODE SELECTOR: My Gigs vs All Gigs
# ===============================
if role == "admin":
    mode = st.radio(
        "Schedule View:",
        ["My Gigs", "All Gigs"],
        index=1,
        horizontal=True,
    )
elif role in ("musician", "sound_tech"):
    mode = st.radio(
        "Schedule View:",
        ["My Gigs", "All Gigs"],
        index=0,
        horizontal=True,
    )
else:
    # Guest user (not yet mapped to musician)
    st.info(
        "You are logged in, but your musician profile isn't linked yet. "
        "You may view the full gig schedule below."
    )
    mode = "All Gigs"


# ===============================
# LOAD GIGS
# ===============================
def load_gigs_for_user(player_email: str) -> pd.DataFrame:
    """
    Lookup gigs where this user is booked.
    Uses the same RPC you already had wired: get_player_gigs(player_email).
    """
    try:
        res = sb.rpc("get_player_gigs", {"player_email": player_email}).execute()
        return pd.DataFrame(res.data or [])
    except Exception:
        return pd.DataFrame()


def load_all_gigs() -> pd.DataFrame:
    return _select_df("gigs", "*")


# === Load venue lookup table (id -> name), same idea as 02_Schedule_View ===
def load_venue_lookup() -> dict[int, str]:
    venues_df = _select_df("venues", "id,name")
    if venues_df.empty:
        return {}
    return {row["id"]: row["name"] for _, row in venues_df.iterrows()}


venue_lookup = load_venue_lookup()

# --- Base gigs set ---
if mode == "My Gigs":
    gigs_df = load_gigs_for_user(email)
else:
    gigs_df = load_all_gigs()

if gigs_df.empty:
    st.info("No gigs found.")
    st.stop()


# ===============================
# NORMALIZE / ENRICH DATA
# ===============================
gigs = gigs_df.copy()

# Dates
if "event_date" in gigs.columns:
    gigs["event_date"] = pd.to_datetime(gigs["event_date"]).dt.date

# Times -> AM/PM
def _fmt_time(val):
    if not val:
        return ""
    try:
        return pd.to_datetime(val, format="%H:%M:%S").strftime("%I:%M %p")
    except Exception:
        try:
            return pd.to_datetime(val).strftime("%I:%M %p")
        except Exception:
            return str(val)

if "start_time" in gigs.columns:
    gigs["start_time"] = gigs["start_time"].apply(_fmt_time)
if "end_time" in gigs.columns:
    gigs["end_time"] = gigs["end_time"].apply(_fmt_time)

# Venue name from venue_id
if "venue_id" in gigs.columns:
    gigs["venue_name"] = gigs["venue_id"].map(venue_lookup).fillna("")


# ===============================
# DATE FILTER (Future vs All)
# ===============================
st.subheader("Date Filter")
date_scope = st.radio(
    "Show:",
    ["Future gigs only", "All gigs"],
    index=0,
    horizontal=True,
)

today = date.today()

if date_scope == "Future gigs only" and "event_date" in gigs.columns:
    gigs = gigs[gigs["event_date"] >= today]

if gigs.empty:
    st.info("No gigs found for the selected date filter.")
    st.stop()


# ===============================
# STATUS + VENUE FILTERS
# ===============================
st.subheader("Filters")

col_f1, col_f2 = st.columns([1, 1.5])

with col_f1:
    status_filter = st.multiselect(
        "Contract status",
        ["Pending", "Hold", "Confirmed"],
        default=["Pending", "Hold", "Confirmed"],
    )

with col_f2:
    venue_search = st.text_input("Search venue", "")

filtered = gigs.copy()

# Status filter
if "contract_status" in filtered.columns and status_filter:
    filtered = filtered[filtered["contract_status"].isin(status_filter)]

# Venue search filter
if venue_search:
    if "venue_name" in filtered.columns:
        vs = venue_search.strip().lower()
        filtered = filtered[
            filtered["venue_name"].fillna("").str.lower().str.contains(vs)
        ]

if filtered.empty:
    st.info("No gigs match the selected filters.")
    st.stop()


# ===============================
# BUILD FINAL DISPLAY TABLE
# ===============================
rows = []
for _, row in filtered.iterrows():
    rows.append(
        {
            "Date": row.get("event_date"),
            "Title": row.get("title", ""),
            "Venue": row.get("venue_name", ""),
            "Start": row.get("start_time", ""),
            "End": row.get("end_time", ""),
            "Status": row.get("contract_status", ""),
        }
    )

clean_df = pd.DataFrame(rows)

# Sort
clean_df = clean_df.sort_values(
    ["Date", "Start"], ascending=[True, True], ignore_index=True
)

# ===============================
# RENDER TABLE
# ===============================
st.subheader("Gigs")
st.dataframe(clean_df, use_container_width=True, hide_index=True)

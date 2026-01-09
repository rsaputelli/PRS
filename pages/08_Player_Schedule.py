# pages/08_Player_Schedule.py
from __future__ import annotations

import streamlit as st
import pandas as pd
from datetime import datetime, date
from supabase import create_client, Client

# from lib.auth import is_logged_in, current_user, IS_ADMIN
from lib.ui_header import render_header
from auth_helper import require_login

user, session, user_id = require_login()
auth_email = (user.email or "").lower().strip()

from auth_helper import sb

# ===============================
# Supabase Init
# ===============================
# def _get_secret(name, required: bool = False):
    # val = st.secrets.get(name)
    # if required and not val:
        # st.error(f"Missing secret: {name}")
        # st.stop()
    # return val


# SUPABASE_URL = _get_secret("SUPABASE_URL", required=True)
# SUPABASE_ANON_KEY = _get_secret("SUPABASE_ANON_KEY", required=True)

# sb: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)

# Attach session if available
# if (
    # st.session_state.get("sb_access_token")
    # and st.session_state.get("sb_refresh_token")
# ):
    # try:
        # sb.auth.set_session(
            # access_token=st.session_state["sb_access_token"],
            # refresh_token=st.session_state["sb_refresh_token"],
        # )
    # except Exception:
        # pass


# ===============================
# AUTH — Unified PRS Login Model
# ===============================
# if not is_logged_in():
    # st.error("Please log in to view your schedule.")
    # st.stop()

# USER = current_user()
# email = (USER.get("email") or "").lower().strip()


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
# ROLE DETECTION
# ===============================
res = (
    sb.table("profiles")
    .select("role")
    .eq("id", user_id)
    .execute()
)

role = res.data[0]["role"] if res.data else "standard"
is_admin = role == "admin"

player_email = auth_email

musician = None

if not is_admin:
    m_res = (
        sb.table("musicians")
        .select("id, display_name")
        .ilike("email", f"{auth_email.strip()}%")
        .limit(1)
        .execute()
    )

    if not m_res.data:
        st.error(
            "Your account is not linked to a musician record. "
            "Please contact the administrator."
        )
        st.stop()

    musician = m_res.data[0]

# ===============================
# HEADER
# ===============================
render_header("My Schedule", emoji="🎸")
st.markdown("---")


# ===============================
# ROLE-SCOPED VIEW MODE LOGIC
# ===============================

# is_admin = IS_ADMIN()
# player_email = email

# Guests = logged-in but not mapped to musician record
# role = (
    # "admin" if is_admin
    # else (profile.get("role") if profile and profile.get("role") else "guest")
# )

# Default = musicians see only their gigs
# view_mode = "my"


# if role == "admin":
    # Admins may choose
    # view_mode = st.radio(
        # "Schedule View:",
        # ["my", "all"],
        # index=1,
        # format_func=lambda x: "My Gigs" if x == "my" else "All Gigs",
        # horizontal=True,
    # )

# elif role in ("musician", "sound_tech"):
    # Musicians / Sound Techs → My gigs only
    # st.info("You are viewing your assigned gigs only.", icon="🎸")
    # view_mode = "my"

# else:
    # Guest / unmapped users → All gigs only
    # st.warning(
        # "You are viewing the public band schedule. "
        # "If you are a band member and should see your personal gig schedule, "
        # "please contact the site administrator.",
        # icon="👤",
    # )
    # view_mode = "all"

# ===============================
# ROLE-SCOPED VIEW MODE LOGIC
# ===============================

view_mode = "my"

if is_admin:
    view_mode = st.radio(
        "Schedule View:",
        ["my", "all"],
        index=1,
        format_func=lambda x: "My Gigs" if x == "my" else "All Gigs",
        horizontal=True,
    )
else:
    st.info("You are viewing your assigned gigs only.", icon="🎸")


# ===============================
# LOAD GIGS
# ===============================

def load_gigs_for_user(player_email: str) -> pd.DataFrame:
    try:
        res = sb.rpc("get_player_gigs", {"player_email": player_email}).execute()
        return pd.DataFrame(res.data or [])
    except Exception:
        return pd.DataFrame()


def load_all_gigs() -> pd.DataFrame:
    return _select_df("gigs", "*")


if view_mode == "my":
    gigs_df = load_gigs_for_user(player_email)
else:
    gigs_df = load_all_gigs()

if gigs_df.empty:
    st.info("No gigs found for this view.", icon="ℹ️")
    st.stop()

# ===============================
# VENUE LOOKUP (works for all roles)
# ===============================
def load_venue_lookup() -> dict[str, str]:
    venues_df = _select_df("venues", "id,name")
    if venues_df.empty:
        return {}
    return {str(row["id"]): row["name"] for _, row in venues_df.iterrows()}

venue_lookup = load_venue_lookup()

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
    gigs["venue_name"] = (
        gigs["venue_id"].astype(str).map(venue_lookup).fillna("")
)

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

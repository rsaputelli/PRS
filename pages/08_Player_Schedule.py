# pages/08_Player_Schedule.py
from __future__ import annotations

import streamlit as st
import pandas as pd
from datetime import datetime
from supabase import create_client, Client

from lib.auth import is_logged_in, current_user, IS_ADMIN
from lib.ui_header import render_header


# ===============================
# Supabase Init
# ===============================
def _get_secret(name, required=False):
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
    role = "guest"   # logged-in but unmapped musician


# ===============================
# HEADER
# ===============================
render_header("My Schedule", emoji="🎸")
st.markdown("---")


# ===============================
# MODE SELECTOR
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
    st.info(
        "You are logged in, but your musician profile isn't linked yet. "
        "You may view the full gig schedule below."
    )
    mode = "All Gigs"


# ===============================
# QUERY GIGS
# ===============================
def load_gigs_for_user(email: str):
    """
    Lookup gigs where this user is booked.
    Ensures venue fields exist.
    """
    try:
        res = sb.rpc("get_player_gigs", {"player_email": email}).execute()
        df = pd.DataFrame(res.data or [])

        # Guarantee venue-related columns exist
        for col in ["venue_name", "venue_text", "location"]:
            if col not in df.columns:
                df[col] = ""

        return df

    except Exception:
        return pd.DataFrame()


def load_all_gigs():
    """
    Full gig list WITH venue fields (exact parity with Schedule View).
    """
    try:
        res = (
            sb.table("gigs")
            .select(
                """
                id,
                title,
                event_date,
                start_time,
                end_time,
                contract_status,
                venue_name,
                venue_text,
                location
                """
            )
            .order("event_date", desc=False)
            .execute()
        )
        return pd.DataFrame(res.data or [])
    except Exception:
        return pd.DataFrame()

# Load the appropriate dataset
if mode == "My Gigs":
    df = load_gigs_for_user(email)
    if df.empty:
        st.warning("You are not listed on any upcoming gigs.")
else:
    df = load_all_gigs()


# ===============================
# DISPLAY PREP
# ===============================
if df.empty:
    st.info("No gigs found.")
    st.stop()

# ---- Normalize date ----
if "event_date" in df.columns:
    df["event_date"] = pd.to_datetime(df["event_date"]).dt.date

# ---- AM/PM formatting ----
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

if "start_time" in df.columns:
    df["start_time"] = df["start_time"].apply(_fmt_time)
if "end_time" in df.columns:
    df["end_time"] = df["end_time"].apply(_fmt_time)


# ===============================
# VENUE RESOLUTION (Exact match to Schedule View)
# ===============================
def _resolve_venue(row):
    """
    Exact venue resolution from 02_Schedule_View:
    Priority:
    1. venue_name
    2. venue_text
    3. location
    """
    for field in ["venue_name", "venue_text", "location"]:
        v = row.get(field)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""

df["venue_display"] = df.apply(_resolve_venue, axis=1)


# ===============================
# BUILD DISPLAY ROWS
# ===============================
display_rows = []
for _, row in df.iterrows():
    display_rows.append({
        "Date": row.get("event_date"),
        "Title": row.get("title", ""),
        "Venue": row.get("venue_display", ""),    # ← NEW
        "Start": row.get("start_time", ""),
        "End": row.get("end_time", ""),
        "Status": row.get("contract_status", ""),
    })

clean_df = pd.DataFrame(display_rows)

# ---- Sort ----
clean_df = clean_df.sort_values(["Date", "Start"], ascending=[True, True], ignore_index=True)


# ===============================
# FILTERS (Status + Future toggle + Search)
# ===============================

colA, colB, colC = st.columns([1, 1, 2])

with colA:
    status_filter = st.multiselect(
        "Status filter",
        ["Confirmed", "Pending", "Hold"],
        default=["Confirmed", "Pending", "Hold"],
    )

with colB:
    date_scope = st.radio(
        "Show:",
        ["Future gigs only", "All gigs"],
        index=0,
        horizontal=True,
    )

with colC:
    search_txt = st.text_input("Search venue:", "")


# Apply filters
filtered_df = clean_df.copy()

# Status
filtered_df = filtered_df[filtered_df["Status"].isin(status_filter)]

# Future-only
if date_scope == "Future gigs only":
    today = datetime.now().date()
    filtered_df = filtered_df[filtered_df["Date"] >= today]

# Venue Search
if search_txt.strip():
    s = search_txt.lower().strip()
    filtered_df = filtered_df[
        filtered_df["Venue"].str.lower().str.contains(s, na=False)
    ]


# ===============================
# RENDER TABLE
# ===============================
st.subheader("Gigs")
st.dataframe(filtered_df, use_container_width=True, hide_index=True)

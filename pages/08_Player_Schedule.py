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
    """RPC to load gigs for a musician."""
    try:
        res = sb.rpc("get_player_gigs", {"player_email": email}).execute()
        return pd.DataFrame(res.data or [])
    except Exception:
        return pd.DataFrame()


def load_all_gigs():
    try:
        res = sb.table("gigs").select("*").order("event_date", desc=False).execute()
        return pd.DataFrame(res.data or [])
    except Exception:
        return pd.DataFrame()


# === Load venue lookup table (IDENTICAL to Schedule View) ===
def load_venue_lookup():
    try:
        res = sb.table("venues").select("id,name").execute()
        rows = res.data or []
        return {r["id"]: r["name"] for r in rows}
    except Exception:
        return {}

venue_lookup = load_venue_lookup()


# Load gigs
if mode == "My Gigs":
    df = load_gigs_for_user(email)
    if df.empty:
        st.warning("You are not listed on any upcoming gigs.")
else:
    df = load_all_gigs()


# ===============================
# DISPLAY
# ===============================
if df.empty:
    st.info("No gigs found.")
    st.stop()

# ---- Normalize date ----
if "event_date" in df.columns:
    df["event_date"] = pd.to_datetime(df["event_date"]).dt.date

# ---- Time formatting ----
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
# VENUE RESOLUTION (FULL BLOCK — matches Schedule View)
# ===============================

# Normalize venue_id
if "venue_id" in df.columns:
    df["venue_id"] = df["venue_id"].astype(str)

# Pull venues table
try:
    vres = sb.table("venues").select(
        "id, name, address, city, state, zip"
    ).execute()
    venues_df = pd.DataFrame(vres.data or [])
except Exception:
    venues_df = pd.DataFrame()

# Normalize venue_id there also
if not venues_df.empty:
    venues_df["id"] = venues_df["id"].astype(str)
    venues_df = venues_df.rename(columns={"id": "venue_id"})

    # Merge into gigs
    df = df.merge(venues_df, how="left", on="venue_id")

# Fallback inline venue text fields
inline_fields = ["venue", "venue_name", "location", "venue_text"]
inline_fields = [f for f in inline_fields if f in df.columns]

def _resolve_venue_name(row):
    # 1: From venues table
    name = row.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()

    # 2: Inline fields on gig record
    for f in inline_fields:
        v = row.get(f)
        if isinstance(v, str) and v.strip():
            return v.strip()

    return ""

def _resolve_venue_address(row):
    # If venues table has address data
    addr = row.get("address")
    city = row.get("city")
    state = row.get("state")
    zipc = row.get("zip")

    parts = []
    if addr:
        parts.append(str(addr).strip())
    if city:
        parts.append(str(city).strip())
    if state:
        parts.append(str(state).strip())
    if zipc:
        parts.append(str(zipc).strip())

    if parts:
        return ", ".join(parts)

    # No fallback inline address support needed yet
    return ""

df["venue_name_final"] = df.apply(_resolve_venue_name, axis=1)
df["venue_address_final"] = df.apply(_resolve_venue_address, axis=1)

# Single combined display column (Schedule View style)
def _venue_display(row):
    vn = row.get("venue_name_final", "")
    ad = row.get("venue_address_final", "")
    if vn and ad:
        return f"{vn} – {ad}"
    return vn or ad or ""

df["venue_display"] = df.apply(_venue_display, axis=1)


# ===============================
# FILTER BAR
# ===============================
st.subheader("Filters")

col1, col2, col3 = st.columns([1, 1, 2])

with col1:
    status_filter = st.multiselect(
        "Contract status",
        ["Pending", "Hold", "Confirmed"],
        default=["Pending", "Hold", "Confirmed"],
    )

with col2:
    upcoming_only = st.toggle("Upcoming only", value=True)

with col3:
    search_venue = st.text_input("Search venue", "").strip().lower()


# ===============================
# APPLY FILTERS
# ===============================
filtered_df = df.copy()

# Upcoming filter
if upcoming_only and "event_date" in filtered_df.columns:
    today = date.today()
    filtered_df = filtered_df[filtered_df["event_date"] >= today]

# Contract status filter
if "contract_status" in filtered_df.columns:
    filtered_df = filtered_df[filtered_df["contract_status"].isin(status_filter)]

# Venue search filter
if search_venue:
    filtered_df = filtered_df[
        filtered_df["venue_name"].str.lower().str.contains(search_venue, na=False)
    ]


# ===============================
# Final Display Table
# ===============================
if filtered_df.empty:
    st.info("No gigs match the selected filters.")
    st.stop()


# Build simplified rows (INCLUDING VENUE FIX)
display_rows = []
for _, row in filtered_df.iterrows():
    display_rows.append({
        "Date": row.get("event_date"),
        "Title": row.get("title", ""),
        "Venue": row.get("venue_name", ""),
        "Start": row.get("start_time", ""),
        "End": row.get("end_time", ""),
        "Status": row.get("contract_status", ""),
    })

clean_df = pd.DataFrame(display_rows)

# Sort
clean_df = clean_df.sort_values(["Date", "Start"], ascending=[True, True], ignore_index=True)

# Render
st.subheader("Gigs")
st.dataframe(clean_df, use_container_width=True, hide_index=True)

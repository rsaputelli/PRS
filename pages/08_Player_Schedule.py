# pages/08_Player_Schedule.py
from __future__ import annotations

import streamlit as st
import pandas as pd
from datetime import datetime
from supabase import create_client, Client
from lib.ui_header import render_header
from lib.ui_format import format_currency
from lib.auth import is_logged_in, current_user

# ---------------------------------------------------------
# Page config
# ---------------------------------------------------------
st.set_page_config(page_title="Player Schedule", page_icon="🎸", layout="wide")

# ---------------------------------------------------------
# LOGIN REQUIRED
# ---------------------------------------------------------
if not is_logged_in():
    st.error("Please sign in from the Login page.")
    st.stop()

USER = current_user()
user_email = USER.get("email", "").strip().lower()

if not user_email:
    st.error("No authenticated user email found.")
    st.stop()

# ---------------------------------------------------------
# Supabase client
# ---------------------------------------------------------
SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_ANON_KEY = st.secrets["SUPABASE_ANON_KEY"]
sb: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)

# ---------------------------------------------------------
# Load musician profile (to find their ID)
# ---------------------------------------------------------
try:
    mres = sb.table("musicians").select("*").eq("email", user_email).limit(1).execute()
    musician_rows = mres.data or []
except Exception as e:
    st.error(f"Could not load musician record: {e}")
    st.stop()

if not musician_rows:
    st.error("You do not appear to be listed as a musician in the system.")
    st.stop()

musician = musician_rows[0]
musician_id = musician.get("id")

# ---------------------------------------------------------
# Header
# ---------------------------------------------------------
render_header(title="Player Schedule", emoji="🎸")
st.markdown("---")

# ---------------------------------------------------------
# UI Toggle
# ---------------------------------------------------------
view_mode = st.radio(
    "View:",
    ["My Booked Gigs", "All Gigs"],
    horizontal=True
)

# ---------------------------------------------------------
# Helper: Fetch gigs safely
# ---------------------------------------------------------
def fetch_all_gigs() -> pd.DataFrame:
    """Load all gigs but strip private/sensitive fields."""
    res = sb.table("gigs").select("*").order("event_date").execute()
    rows = res.data or []
    df = pd.DataFrame(rows)
    if df.empty:
        return df

    # Hide sensitive columns if present
    sensitive = {
        "fee", "private_flag", "notes", "agent_id",
        "sound_tech_id", "sound_fee", "contract_status",
        "created_at", "updated_at"
    }
    for col in sensitive:
        if col in df.columns:
            df.drop(columns=[col], inplace=True)

    return df


def fetch_my_gigs(musician_id: str) -> pd.DataFrame:
    """Load gigs the musician is booked on."""
    try:
        res = sb.rpc("gigs_for_musician", {"musician_id": musician_id}).execute()
        rows = res.data or []
    except Exception:
        # fallback if RPC missing
        join = sb.table("gig_musicians").select("*").eq("musician_id", musician_id).execute()
        join_rows = join.data or []
        gig_ids = [j["gig_id"] for j in join_rows]

        if not gig_ids:
            return pd.DataFrame()

        res2 = sb.table("gigs").select("*").in_("id", gig_ids).order("event_date").execute()
        rows = res2.data or []

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    # Same sensitive column hiding
    sensitive = {
        "fee", "private_flag", "notes", "agent_id",
        "sound_tech_id", "sound_fee", "contract_status",
        "created_at", "updated_at"
    }
    for col in sensitive:
        if col in df.columns:
            df.drop(columns=[col], inplace=True)

    return df


# ---------------------------------------------------------
# Render Selected View
# ---------------------------------------------------------

if view_mode == "My Booked Gigs":
    st.subheader("🎵 Gigs You're Booked On")
    df = fetch_my_gigs(musician_id)

    if df.empty:
        st.info("You are not currently booked on any gigs.")
    else:
        df_display = df[["event_date", "title", "venue_id", "start_time", "end_time"]]
        st.dataframe(df_display, use_container_width=True)

else:
    st.subheader("📅 All Scheduled Gigs")
    df = fetch_all_gigs()

    if df.empty:
        st.info("No gigs are currently scheduled.")
    else:
        df_display = df[["event_date", "title", "venue_id", "start_time", "end_time"]]
        st.dataframe(df_display, use_container_width=True)

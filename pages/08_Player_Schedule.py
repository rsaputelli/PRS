# pages/08_Player_Schedule.py
from __future__ import annotations

import streamlit as st
import pandas as pd
from datetime import datetime
from supabase import create_client, Client

from lib.auth import is_logged_in, current_user, IS_ADMIN
from lib.ui_header import render_header
from lib.ui_format import format_currency


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
    # BEFORE profiles exist, musicians get blocked from "My Gigs" but can still see "All Gigs"
    role = "guest"   # means logged-in but unmapped user


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
    # Guest user (not yet mapped to musician)
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
    Uses musicians.email match temporarily until profile mapping is added.
    """
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


# Load the appropriate dataset
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

# ---- AM/PM time formatting ----
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

# ---- Build simplified display table ----
display_rows = []
for _, row in df.iterrows():
    display_rows.append({
        "Date": row.get("event_date"),
        "Title": row.get("title", ""),
        "Start": row.get("start_time", ""),
        "End": row.get("end_time", ""),
        "Status": row.get("contract_status", ""),
    })

clean_df = pd.DataFrame(display_rows)

# ---- Sort ----
clean_df = clean_df.sort_values(["Date", "Start"], ascending=[True, True], ignore_index=True)

# ===============================
# RENDER TABLE
# ===============================
st.subheader("Gigs")

# ===============================
# OPTIONAL STATUS SELECTOR (admin only)
# ===============================
if role == "admin":
    st.write("Update gig status:")

    for i, row in df.iterrows():   # USE FULL df BECAUSE IT CONTAINS gig_id
        gig_id = row.get("id")
        gig_date = row.get("event_date")
        gig_title = row.get("title")
        current_status = (row.get("contract_status") or "").capitalize()

        if current_status not in ["Confirmed", "Pending", "Hold"]:
            current_status = "Pending"

        new_status = st.selectbox(
            f"{gig_date} – {gig_title}",
            ["Confirmed", "Pending", "Hold"],
            index=["Confirmed", "Pending", "Hold"].index(current_status),
            key=f"status_{gig_id}",
        )

        # Save status change
        if new_status != current_status:
            try:
                sb.table("gigs").update({"contract_status": new_status}) \
                    .eq("id", gig_id) \
                    .execute()
                st.success(f"Updated status for {gig_title} to {new_status}")
            except Exception as e:
                st.error(f"Failed to update status: {e}")

    st.markdown("---")

# Display the simplified table
st.dataframe(clean_df, use_container_width=True, hide_index=True)

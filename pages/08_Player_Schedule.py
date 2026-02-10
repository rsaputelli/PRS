# pages/08_Player_Schedule.py
from __future__ import annotations

import streamlit as st
import pandas as pd
from datetime import date
import datetime as dt
from zoneinfo import ZoneInfo

from lib.ui_header import render_header
from auth_helper import require_login, sb
from lib.calendar_utils import make_ics_bytes

# ------------------------------------------------------------
# Auth
# ------------------------------------------------------------
user, session, user_id = require_login()
auth_email = (user.email or "").strip().lower()

LOCAL_TZ = ZoneInfo("America/New_York")


# ------------------------------------------------------------
# Supabase helpers (defensive)
# ------------------------------------------------------------
def _resp_data(resp):
    """Safely return resp.data, handling None responses."""
    if resp is None:
        return None
    return getattr(resp, "data", None)


def _maybe_single_dict(resp_data):
    """
    Normalize Supabase response data to either dict or None.
    - maybe_single() typically returns dict or None
    - some client paths can yield [] (empty list) or [dict]
    """
    if resp_data is None:
        return None
    if isinstance(resp_data, dict):
        return resp_data
    if isinstance(resp_data, list):
        return resp_data[0] if resp_data else None
    return None


def _select_df(table: str, select: str = "*", where_eq: dict | None = None) -> pd.DataFrame:
    """Generic table select → DataFrame, defensive on response shapes."""
    try:
        q = sb.table(table).select(select)
        if where_eq:
            for k, v in where_eq.items():
                q = q.eq(k, v)
        resp = q.execute()
        data = _resp_data(resp) or []
        if isinstance(data, dict):
            data = [data]
        return pd.DataFrame(data)
    except Exception as e:
        st.warning(f"{table} query failed: {e}")
        return pd.DataFrame()


# ------------------------------------------------------------
# Resolve role (admin vs standard)
# ------------------------------------------------------------
role_resp = (
    sb.table("profiles")
    .select("role")
    .eq("id", user_id)
    .limit(1)
    .execute()
)
role_data = _resp_data(role_resp) or []
if isinstance(role_data, dict):
    role_data = [role_data]

role = role_data[0]["role"] if role_data else "standard"
is_admin = (role == "admin")


# ------------------------------------------------------------
# Resolve musician (players must be linked; admins may be unlinked)
# ------------------------------------------------------------
def resolve_musician_for_user(user_id: str, auth_email: str) -> dict | None:
    """
    Strategy:
      1) Try musicians.user_id == auth user_id
      2) If not found and auth_email present, try musicians.email == auth_email
         and then attempt one-time link: set musicians.user_id = user_id
    Returns musician dict or None.
    """
    # 1) user_id match
    try:
        r1 = (
            sb.table("musicians")
            .select("id, display_name, instrument, email, user_id")
            .eq("user_id", user_id)
            .maybe_single()
            .execute()
        )
        musician = _maybe_single_dict(_resp_data(r1))
    except Exception:
        musician = None

    if musician:
        return musician

    # 2) email fallback + one-time link
    email_norm = (auth_email or "").strip().lower()
    if not email_norm:
        return None

    try:
        r2 = (
            sb.table("musicians")
            .select("id, display_name, instrument, email, user_id")
            .eq("email", email_norm)
            .maybe_single()
            .execute()
        )
        musician = _maybe_single_dict(_resp_data(r2))
    except Exception:
        musician = None

    if not musician:
        return None

    # Try to link (best effort). If RLS blocks it, we still return the musician record.
    try:
        sb.table("musicians").update({"user_id": user_id}).eq("id", musician["id"]).execute()
        musician["user_id"] = user_id
    except Exception:
        pass

    return musician


musician = resolve_musician_for_user(user_id=user_id, auth_email=auth_email)

# Non-admins MUST be linked
if not is_admin and not musician:
    st.error(
        "Your account is not linked to a musician record. "
        "Please contact the administrator."
    )
    st.stop()

# Display name for header
if is_admin:
    display_name = user.email
else:
    display_name = (musician.get("display_name") if musician else None) or user.email


# ------------------------------------------------------------
# ICS helper (Player Schedule)
# ------------------------------------------------------------
def build_player_ics(row: dict) -> bytes:
    event_date = row["event_date"]
    start_time = row["_start_time_raw"]
    end_time = row["_end_time_raw"]

    starts_at = dt.datetime.combine(event_date, start_time, tzinfo=LOCAL_TZ)
    ends_at = dt.datetime.combine(event_date, end_time, tzinfo=LOCAL_TZ)

    desc = (
        f"PRS Gig Assignment\n\n"
        f"Instrument: {row.get('instrument','')}\n"
        f"Gig: {row.get('title','')}\n"
        f"Venue: {row.get('venue_name','')}\n"
        f"Start: {row.get('start_time','')}\n"
        f"End: {row.get('end_time','')}\n"
        f"Status: {row.get('contract_status','')}\n\n"
        f"Imported from PRS"
    )

    return make_ics_bytes(
        uid=f"prs-player-{row['id']}",
        title=row.get("title", "Gig"),
        starts_at=starts_at,
        ends_at=ends_at,
        location=row.get("venue_name", ""),
        description=desc,
    )


# ------------------------------------------------------------
# Header
# ------------------------------------------------------------
render_header("My Schedule")

if display_name:
    st.markdown(
        f"<div style='font-size:20px; font-weight:600; color:#2c2c2c; margin-top:-6px;'>"
        f"{display_name}"
        f"</div>",
        unsafe_allow_html=True,
    )

st.markdown("---")


# ------------------------------------------------------------
# Role-scoped view mode
# ------------------------------------------------------------
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


# ------------------------------------------------------------
# Load gigs
# ------------------------------------------------------------
def load_gigs_for_musician(musician_id: str) -> pd.DataFrame:
    """Returns gigs assigned to a specific musician via gig_musicians."""
    try:
        res = (
            sb.table("gig_musicians")
            .select(
                """
                gig_id,
                gigs (
                    id,
                    title,
                    event_date,
                    start_time,
                    end_time,
                    venue_id,
                    contract_status
                )
                """
            )
            .eq("musician_id", musician_id)
            .execute()
        )

        rows = []
        for r in (_resp_data(res) or []):
            gig = r.get("gigs") if isinstance(r, dict) else None
            if gig:
                rows.append(gig)

        return pd.DataFrame(rows)

    except Exception as e:
        st.warning(f"Failed to load musician gigs: {e}")
        return pd.DataFrame()


def load_all_gigs() -> pd.DataFrame:
    return _select_df("gigs", "*")


if view_mode == "my":
    if not musician:
        # Admin chose "my" but isn't a musician
        st.info("No musician link found for your account. Switch to **All Gigs** to view the full schedule.", icon="ℹ️")
        st.stop()
    gigs_df = load_gigs_for_musician(musician["id"])
else:
    gigs_df = load_all_gigs()

if gigs_df.empty:
    st.info("No gigs found for this view.", icon="ℹ️")
    st.stop()


# ------------------------------------------------------------
# Venue lookup (works for all roles)
# ------------------------------------------------------------
def load_venue_lookup() -> dict[str, str]:
    venues_df = _select_df("venues", "id,name")
    if venues_df.empty:
        return {}
    return {str(row["id"]): row["name"] for _, row in venues_df.iterrows()}


venue_lookup = load_venue_lookup()


# ------------------------------------------------------------
# Normalize / enrich data
# ------------------------------------------------------------
gigs = gigs_df.copy()

gigs["instrument"] = musician.get("instrument") if musician else ""

# Dates
if "event_date" in gigs.columns:
    gigs["event_date"] = pd.to_datetime(gigs["event_date"]).dt.date

# Preserve raw times for ICS
if "start_time" in gigs.columns:
    gigs["_start_time_raw"] = pd.to_datetime(gigs["start_time"]).dt.time

if "end_time" in gigs.columns:
    gigs["_end_time_raw"] = pd.to_datetime(gigs["end_time"]).dt.time


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
    gigs["venue_name"] = gigs["venue_id"].astype(str).map(venue_lookup).fillna("")


# ------------------------------------------------------------
# Date filter (Future vs All)
# ------------------------------------------------------------
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


# ------------------------------------------------------------
# Status + venue filters
# ------------------------------------------------------------
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

if "contract_status" in filtered.columns and status_filter:
    filtered = filtered[filtered["contract_status"].isin(status_filter)]

if venue_search and "venue_name" in filtered.columns:
    vs = venue_search.strip().lower()
    filtered = filtered[filtered["venue_name"].fillna("").str.lower().str.contains(vs)]

if filtered.empty:
    st.info("No gigs match the selected filters.")
    st.stop()

st.caption(
    "**Contract Status definitions:**\n\n"
    "**Confirmed** = fully executed and locked in.\n"
    "**Hold** = solid inquiry; date is being held pending confirmation.\n"
    "**Pending** = verbally confirmed, awaiting final contract (common for private events)."
)


# ------------------------------------------------------------
# Render gigs + ICS
# ------------------------------------------------------------
st.subheader("Gigs")

filtered = filtered.sort_values(["event_date", "_start_time_raw"], ascending=[True, True])

hcols = st.columns([2.5, 3, 3, 2, 2, 3, 3])
hcols[0].markdown("**Date**")
hcols[1].markdown("**Title**")
hcols[2].markdown("**Venue**")
hcols[3].markdown("**Start**")
hcols[4].markdown("**End**")
hcols[5].markdown("**Status**")
hcols[6].markdown("**Calendar**")

st.markdown("---")

for _, row in filtered.iterrows():
    cols = st.columns([2.5, 3, 3, 2, 2, 3, 3])

    d = row.get("event_date")
    cols[0].write(d.strftime("%m-%d-%Y") if hasattr(d, "strftime") else "")
    cols[1].write(row.get("title", ""))
    cols[2].write(row.get("venue_name", ""))
    cols[3].write(row.get("start_time", ""))
    cols[4].write(row.get("end_time", ""))
    cols[5].write(row.get("contract_status", ""))

    ics_bytes = build_player_ics(row)
    cols[6].download_button(
        label="📅 ICS",
        data=ics_bytes,
        file_name=f"{row.get('title','gig')}.ics",
        mime="text/calendar",
        key=f"ics-player-{row.get('id')}",
    )

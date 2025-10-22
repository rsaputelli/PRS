# pages/04_Enter_Gig.py
"""
Enter Gig — safe-syntax build for Streamlit Cloud (Py 3.10+), with:
- Auth gating before any widgets
- Status + Fee in Event Basics
- Single time inputs with cross‑midnight handling
- Exact dropdown options (no substring bleed)
- Robust Supabase session attach + guarded dropdown fetch
"""

import re
from datetime import date, time, datetime, timedelta

import pandas as pd
import streamlit as st
from supabase import create_client, Client
from postgrest import APIError as PostgrestAPIError

# -----------------------------
# Page config + Auth gate
# -----------------------------
st.set_page_config(page_title="Enter Gig", page_icon="📝", layout="wide")

if not st.session_state.get("user"):
    st.error("Please sign in from the Login page.")
    st.stop()

# -----------------------------
# Secrets / Supabase client
# -----------------------------
SUPABASE_URL = st.secrets.get("SUPABASE_URL")
SUPABASE_ANON_KEY = st.secrets.get("SUPABASE_ANON_KEY")
if not SUPABASE_URL or not SUPABASE_ANON_KEY:
    st.error("Missing SUPABASE_URL or SUPABASE_ANON_KEY in secrets.")
    st.stop()

sb: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
# Attach the authenticated session so RLS policies for "authenticated" apply
if st.session_state.get("sb_access_token") and st.session_state.get("sb_refresh_token"):
    try:
        sb.auth.set_session(
            access_token=st.session_state["sb_access_token"],
            refresh_token=st.session_state["sb_refresh_token"],
        )
    except Exception as _e:
        st.warning(f"Could not attach Supabase session: {_e}")

# -----------------------------
# Helpers
# -----------------------------
STATUS_CHOICES = ["Tentative", "Confirmed", "Contract Sent", "Canceled"]

@st.cache_data(ttl=300)
def _fetch_table(tbl, cols="*"):
    """Basic fetch helper with caching for dropdowns."""
    try:
        res = sb.table(tbl).select(cols).order("id").execute()
        rows = res.data or []
        return pd.DataFrame(rows)
    except PostgrestAPIError as e:
        st.error("Failed to load '" + str(tbl) + "'. If you're signed in, try refreshing.\nDetails: " + str(getattr(e, 'message', e)))
        return pd.DataFrame()
    except Exception as e:
        st.error("Failed to load '" + str(tbl) + "': " + str(e))
        return pd.DataFrame()

@st.cache_data(ttl=300)
def load_dropdowns():
    agents = _fetch_table("agents", ["id", "name"]).rename(columns={"name": "agent_name"})
    venues = _fetch_table("venues", ["id", "name", "city", "state"]).rename(columns={"name": "venue_name"})
    sounds = _fetch_table("sound_techs", ["id", "first_name", "last_name", "phone"]) \
        .assign(display_name=lambda d: (d["first_name"].fillna("").str.strip() + " " + d["last_name"].fillna("").str.strip()).str.strip())
    return {"agents": agents, "venues": venues, "sounds": sounds}

def _fmt_name(first, last):
    f = (first or "").strip()
    l = (last or "").strip()
    s = (f + " " + l).strip()
    return s if s else "Unnamed"

def _clean_phone(s):
    if not s:
        return None
    digits = re.sub(r"\D", "", str(s))
    return digits if digits else None

def _format_12h(t):
    try:
        return datetime.combine(date.today(), t).strftime("%I:%M %p").lstrip("0")
    except Exception:
        return ""

def _compose_datetimes(event_dt, start_t, end_t):
    start_dt = datetime.combine(event_dt, start_t)
    end_dt = datetime.combine(event_dt, end_t)
    if end_dt <= start_dt:
        end_dt += timedelta(days=1)
    return start_dt, end_dt

def _insert_row(table, payload):
    res = sb.table(table).insert(payload).select("*").single().execute()
    if res.data is None:
        raise RuntimeError(str(res.error or {"message": "Insert failed"}))
    return res.data

# -----------------------------
# Page Layout
# -----------------------------
st.title("Enter Gig")

lists = load_dropdowns()
agents_df = lists.get("agents", pd.DataFrame())
venues_df = lists.get("venues", pd.DataFrame())
sounds_df = lists.get("sounds", pd.DataFrame())

# Build labels for dropdowns (exact options — avoids substring bleed)
agent_options = ["— None —"] + ([] if agents_df.empty else [str(r.agent_name) for _, r in agents_df.iterrows()])
venue_options = ["— Select a venue —"] + ([] if venues_df.empty else [
    (str(r.venue_name) + " (" + (str(r.city or "").strip()) + ", " + (str(r.state or "").strip()) + ")").strip().rstrip("() ")
    for _, r in venues_df.iterrows()
])
sound_options = ["— None —"] + ([] if sounds_df.empty else [
    _fmt_name(r.first_name if "first_name" in sounds_df.columns else None,
              r.last_name if "last_name" in sounds_df.columns else None)
    for _, r in sounds_df.iterrows()
])

# Reverse lookup from label -> id
agent_lookup = {}
if len(agent_options) > 1 and not agents_df.empty:
    for i, lbl in enumerate(agent_options[1:]):
        agent_lookup[lbl] = str(agents_df.iloc[i].id)

venue_lookup = {}
if len(venue_options) > 1 and not venues_df.empty:
    for i, lbl in enumerate(venue_options[1:]):
        venue_lookup[lbl] = str(venues_df.iloc[i].id)

sound_lookup = {}
if len(sound_options) > 1 and not sounds_df.empty:
    for i, lbl in enumerate(sound_options[1:]):
        sound_lookup[lbl] = str(sounds_df.iloc[i].id)

with st.form("enter_gig_form", clear_on_submit=False):
    colA, colB = st.columns([1, 1])

    # --------------------- Event Basics ---------------------
    with colA:
        st.subheader("Event Basics")
        title = st.text_input("Title", placeholder="e.g., PRS @ The Buck", max_chars=120)
        event_date = st.date_input("Event Date", value=date.today())
        start_time_in = st.time_input("Start Time", value=time(21, 0), step=300)
        end_time_in = st.time_input("End Time", value=time(23, 0), step=300)

        status = st.selectbox("Status", STATUS_CHOICES, index=1)
        fee_val = st.number_input("Guaranteed Fee ($)", min_value=0.0, step=50.0, value=0.0, format="%0.2f")

    # --------------------- Venue & Sound ---------------------
    with colB:
        st.subheader("Venue & Sound")
        venue_label = st.selectbox("Venue", venue_options, index=0)
        agent_label = st.selectbox("Booking Agent", agent_options, index=0)
        sound_label = st.selectbox("Sound Tech", sound_options, index=0)

        st.caption("If venue provides sound and you don’t have a specific tech, you can note contact details below.")
        vbv_name = st.text_input("Sound by Venue — Contact Name", placeholder="Name (optional)")
        vbv_phone = st.text_input("Sound by Venue — Phone", placeholder="Phone (optional)")

    # --------------------- Privacy & Notes ---------------------
    is_private = st.checkbox("Private Event (hide from public views)", value=False)
    notes = st.text_area("Notes", height=100, placeholder="Any special notes…")

    submit = st.form_submit_button("💾 Save Gig", type="primary")

# On submit — build, validate, and insert
if submit:
    if not title or not str(title).strip():
        st.error("Please provide a Title.")
        st.stop()

    start_dt, end_dt = _compose_datetimes(event_date, start_time_in, end_time_in)
    if end_dt.date() > start_dt.date():
        st.info("This gig ends next day (" + end_dt.strftime('%Y-%m-%d %I:%M %p') + "). We will save Event Date as the start date and keep the end time as entered.")

    agent_id = agent_lookup.get(agent_label)
    venue_id = venue_lookup.get(venue_label)
    sound_id = sound_lookup.get(sound_label)

    payload = {
        "title": str(title).strip(),
        "event_date": start_dt.date().isoformat(),
        "start_time": start_time_in.strftime("%H:%M:%S"),
        "end_time": end_time_in.strftime("%H:%M:%S"),
        "contract_status": status,
        "fee": float(fee_val) if fee_val is not None else None,
        "agent_id": agent_id if agent_label != "— None —" else None,
        "venue_id": venue_id if venue_label != "— Select a venue —" else None,
        "sound_tech_id": sound_id if sound_label != "— None —" else None,
        "is_private": bool(is_private),
        "notes": (notes or "").strip() or None,
        "sound_by_venue_name": (vbv_name or "").strip() or None,
        "sound_by_venue_phone": _clean_phone(vbv_phone),
    }

    try:
        new_gig = _insert_row("gigs", payload)
        gig_id = new_gig.get("id")

        st.success("Gig saved successfully ✅")
        st.json({
            "id": gig_id,
            "title": new_gig.get("title"),
            "event_date": new_gig.get("event_date"),
            "start_time (12-hr)": _format_12h(start_time_in),
            "end_time (12-hr)": _format_12h(end_time_in),
            "status": new_gig.get("contract_status"),
            "fee": new_gig.get("fee"),
        })

        st.info("Open the Schedule View to verify the new gig appears with Venue / Location / Sound.")
    except Exception as e:
        st.error("Insert failed: " + str(e))
        st.stop()

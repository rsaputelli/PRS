# pages/04_Enter_Gig.py
"""
Enter Gig — cleaned layout, proper auth gating, overnight time handling,
and tighter dropdown behavior. Designed as a drop‑in replacement.

Key changes vs prior version:
- Auth gate happens before ANY form fields render (so Status/Fee/etc. never appear pre‑login)
- Event Basics now includes Status and Fee (grouped with Title/Date/Times)
- Start/End time use single inputs each (st.time_input), and we detect cross‑midnight
- Insert payload uses only known columns; optional fields normalized to None
- Safer phone/notes cleaning; fee coerced to Decimal-compatible float
- Role dropdown uses exact labels (no substring bleed) via a fixed options list

Assumptions:
- Supabase URL and anon key in secrets: SUPABASE_URL, SUPABASE_ANON_KEY
- st.session_state["user"] holds an authenticated user dict (as in your app shell)
- RLS policies allow select/insert/update for the "authenticated" role
- public.gigs has columns created earlier: title, event_date, start_time, end_time,
  contract_status, fee, agent_id, venue_id, sound_tech_id, is_private, notes,
  sound_by_venue_name, sound_by_venue_phone

If you maintain different table/column names, adjust the payload below accordingly.
"""
from __future__ import annotations

import re
from datetime import date, time, datetime, timedelta
from typing import Optional, Dict, List

import pandas as pd
import streamlit as st
from supabase import create_client, Client

# -----------------------------
# Page config + Auth gate
# -----------------------------
st.set_page_config(page_title="Enter Gig", page_icon="📝", layout="wide")

# Surface a friendly message and stop if not logged in —
# this ensures NO form widgets render pre‑login.
if not st.session_state.get("user"):
    st.error("Please sign in from the Login page.")
    st.stop()

# -----------------------------
# Secrets / Supabase client
# -----------------------------
def _get_secret(name: str, required: bool = True) -> Optional[str]:
    val = st.secrets.get(name)
    if required and not val:
        st.error(f"Missing required secret: {name}")
        st.stop()
    return val

SUPABASE_URL = _get_secret("SUPABASE_URL", required=True)
SUPABASE_ANON_KEY = _get_secret("SUPABASE_ANON_KEY", required=True)
sb: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)

# -----------------------------
# Helpers
# -----------------------------
ROLE_CHOICES: List[str] = [
    "Male Vocals", "Female Vocals",
    "Keyboard", "Drums", "Guitar", "Bass",
    "Trumpet", "Saxophone", "Trombone",
]

STATUS_CHOICES = ["Tentative", "Confirmed", "Contract Sent", "Canceled"]

@st.cache_data(ttl=300)
def _fetch_table(tbl: str, cols: List[str] | str = "*") -> pd.DataFrame:
    """Basic fetch helper with small caching for dropdowns."""
    res = (
        sb.table(tbl)
        .select(cols)
        .order("id")
        .execute()
    )
    rows = res.data or []
    return pd.DataFrame(rows)

@st.cache_data(ttl=300)
def load_dropdowns() -> Dict[str, pd.DataFrame]:
    agents = _fetch_table("agents", ["id", "name"]).rename(columns={"name": "agent_name"})
    venues = _fetch_table("venues", ["id", "name", "city", "state"]).rename(columns={"name": "venue_name"})
    sounds = _fetch_table("sound_techs", ["id", "first_name", "last_name", "phone"])
    return {"agents": agents, "venues": venues, "sounds": sounds}


def _fmt_name(first: Optional[str], last: Optional[str]) -> str:
    first = (first or "").strip()
    last = (last or "").strip()
    if first or last:
        return f"{first} {last}".strip()
    return "Unnamed"


def _clean_phone(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    digits = re.sub(r"\D", "", str(s))
    if not digits:
        return None
    return digits


def _format_12h(t: time) -> str:
    return datetime.combine(date.today(), t).strftime("%I:%M %p").lstrip("0")


def _compose_datetimes(event_dt: date, start_t: time, end_t: time):
    """Return (start_dt, end_dt) handling cross‑midnight end."""
    start_dt = datetime.combine(event_dt, start_t)
    end_dt = datetime.combine(event_dt, end_t)
    if end_dt <= start_dt:
        end_dt += timedelta(days=1)
    return start_dt, end_dt


def _insert_row(table: str, payload: Dict) -> Dict:
    res = sb.table(table).insert(payload).select("*").single().execute()
    if res.data is None:
        raise RuntimeError(res.error or {"message": "Insert failed"})
    return res.data

# -----------------------------
# Page Layout
# -----------------------------
st.title("Enter Gig")

# Load dropdown data
lists = load_dropdowns()
agents_df = lists["agents"]
venues_df = lists["venues"]
sounds_df = lists["sounds"]

# Build labels for dropdowns (exact match options — avoids substring bleed)
agent_options = ["— None —"] + [f"{r.agent_name}" for _, r in agents_df.iterrows()]
venue_options = ["— Select a venue —"] + [
    f"{r.venue_name} ({(r.city or '').strip()}, {(r.state or '').strip()})".strip().rstrip("() ")
    for _, r in venues_df.iterrows()
]
sound_options = ["— None —"] + [
    f"{_fmt_name(r.first_name, r.last_name)}" for _, r in sounds_df.iterrows()
]

# Reverse lookup from label -> id
agent_lookup = {lbl: str(agents_df.iloc[i].id) for i, lbl in enumerate(agent_options[1:])}
venue_lookup = {lbl: str(venues_df.iloc[i].id) for i, lbl in enumerate(venue_options[1:])}
sound_lookup = {lbl: str(sounds_df.iloc[i].id) for i, lbl in enumerate(sound_options[1:])}

with st.form("enter_gig_form", clear_on_submit=False, border=True):
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
    # Basic validation
    if not title.strip():
        st.error("Please provide a Title.")
        st.stop()

    start_dt, end_dt = _compose_datetimes(event_date, start_time_in, end_time_in)
    if end_dt.date() > start_dt.date():
        st.info(
            f"This gig ends next day ({end_dt.strftime('%Y-%m-%d %I:%M %p')}). "
            "We will save Event Date as the start date and keep the end time as entered."
        )

    agent_id = agent_lookup.get(agent_label)
    venue_id = venue_lookup.get(venue_label)
    sound_id = sound_lookup.get(sound_label)

    payload = {
        "title": title.strip(),
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
        st.error(f"Insert failed: {e}")
        st.stop()

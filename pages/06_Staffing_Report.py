# =============================
# File: pages/06_Staffing_Report.py
# =============================
import os
from datetime import datetime, date, time, timedelta
from typing import Optional, Dict, List

import pandas as pd
import streamlit as st
from supabase import create_client, Client
from lib.ui_header import render_header
from lib.ui_format import format_currency
from auth_helper import require_admin

# -----------------------------
# Supabase helpers
# -----------------------------

def _get_secret(name, default=None, required=False):
    if hasattr(st, "secrets") and name in st.secrets:
        val = st.secrets[name]
    else:
        val = os.environ.get(name, default)
    if required and (val is None or str(val).strip() == ""):
        st.stop()
    return val

SUPABASE_URL = _get_secret("SUPABASE_URL", required=True)
SUPABASE_ANON_KEY = _get_secret("SUPABASE_ANON_KEY", required=True)
sb: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)

if st.session_state.get("sb_access_token") and st.session_state.get("sb_refresh_token"):
    try:
        sb.auth.set_session(
            access_token=st.session_state["sb_access_token"],
            refresh_token=st.session_state["sb_refresh_token"],
        )
    except Exception as e:
        st.warning(f"Could not attach session; showing public data only. ({e})")

# -----------------------------
# Admin gate (canonical)
# -----------------------------
user, session, user_id = require_admin()
if not user:
    st.stop()


# -----------------------------
# Page config + Header
# -----------------------------
st.set_page_config(page_title="Staffing Report", page_icon="📋", layout="wide")
render_header(title="Staffing Report", emoji="📋")

# -----------------------------
# Load data
# -----------------------------
@st.cache_data(ttl=60)
def _select_df(table: str, select: str = "*", where_eq: Optional[Dict] = None) -> pd.DataFrame:
    try:
        q = sb.table(table).select(select)
        if where_eq:
            for k, v in where_eq.items():
                q = q.eq(k, v)
        data = q.execute().data or []
        return pd.DataFrame(data)
    except Exception:
        return pd.DataFrame()

ROLE_CHOICES = [
    "Male Vocals", "Female Vocals",
    "Keyboard", "Drums", "Guitar", "Bass",
    "Trumpet", "Saxophone", "Trombone",
]

# Fetch gigs & supporting tables
gigs = _select_df("gigs", "*")
if gigs.empty:
    st.info("No gigs found.")
    st.stop()

# Normalize types
if "event_date" in gigs.columns:
    gigs["event_date"] = pd.to_datetime(gigs["event_date"], errors="coerce").dt.date

# Compose _start_dt/_end_dt to help with sorting and time text
from datetime import timedelta as _td

def _to_time_obj(x) -> Optional[time]:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return None
    try:
        if isinstance(x, time):
            return x
        ts = pd.to_datetime(x, errors="coerce")
        if pd.isna(ts):
            return None
        return ts.time()
    except Exception:
        return None


def _compose_span(row):
    d = row.get("event_date")
    st_raw = row.get("start_time")
    en_raw = row.get("end_time")
    if pd.isna(d):
        return (pd.NaT, pd.NaT)
    d: date = d
    st_t = _to_time_obj(st_raw) or time(0, 0)
    en_t = _to_time_obj(en_raw) or st_t
    start_dt = datetime.combine(d, st_t)
    end_dt = datetime.combine(d, en_t)
    if end_dt <= start_dt:
        end_dt += _td(days=1)
    return (pd.Timestamp(start_dt), pd.Timestamp(end_dt))

spans = gigs.apply(_compose_span, axis=1, result_type="expand")
gigs["_start_dt"] = pd.to_datetime(spans[0], errors="coerce")
gigs["_end_dt"]   = pd.to_datetime(spans[1], errors="coerce")

assign = _select_df("gig_musicians", "gig_id, role, musician_id")

# -----------------------------
# Filters
# -----------------------------
colf1, colf2 = st.columns([1, 1])
with colf1:
    upcoming_only = st.toggle("Upcoming only", value=True)
with colf2:
    status_filter = st.multiselect("Contract status", ["Pending", "Hold", "Confirmed"], default=["Pending", "Hold", "Confirmed"])

if upcoming_only and "_start_dt" in gigs.columns:
    gigs = gigs[gigs["_start_dt"].dt.date >= pd.Timestamp.today().date()]
if status_filter and "contract_status" in gigs.columns:
    gigs = gigs[gigs["contract_status"].isin(status_filter)]

if gigs.empty:
    st.info("No gigs match your filters.")
    st.stop()

# -----------------------------
# Fully-staffed definition & evaluation
# -----------------------------

def _fmt_time12(ts: pd.Timestamp) -> str:
    if pd.isna(ts):
        return ""
    try:
        return ts.strftime("%-I:%M %p")
    except Exception:
        try:
            return pd.to_datetime(ts).strftime("%-I:%M %p")
        except Exception:
            return ""


def _is_sound_covered(r: pd.Series) -> bool:
    # Covered if a sound tech is assigned OR venue-provided sound contact/phone exists
    has_tech = pd.notna(r.get("sound_tech_id")) and str(r.get("sound_tech_id")).strip() != ""
    by_venue = (
        (pd.notna(r.get("sound_by_venue_name")) and str(r.get("sound_by_venue_name")).strip() != "") or
        (pd.notna(r.get("sound_by_venue_phone")) and str(r.get("sound_by_venue_phone")).strip() != "")
    )
    return bool(has_tech or by_venue)


def _missing_roles(gig_id) -> List[str]:
    if assign.empty:
        return ROLE_CHOICES[:]  # none assigned at all
    sub = assign[assign["gig_id"] == gig_id]
    have = set(sub["role"].dropna().astype(str).tolist())
    return [r for r in ROLE_CHOICES if r not in have]


def _details_ok(r: pd.Series) -> (bool, List[str]):
    missing = []
    if not (pd.notna(r.get("venue_id")) and str(r.get("venue_id")).strip() != ""):
        missing.append("venue")
    if not pd.notna(r.get("start_time")):
        missing.append("start time")
    if not pd.notna(r.get("end_time")):
        missing.append("end time")
    return (len(missing) == 0, missing)

# Build report rows
rows: List[Dict] = []
for _, r in gigs.iterrows():
    gig_id = r.get("id")
    miss_roles = _missing_roles(gig_id)
    sound_ok = _is_sound_covered(r)
    details_ok, miss_details = _details_ok(r)
    fully_staffed = (len(miss_roles) == 0) and sound_ok and details_ok

    if not fully_staffed:
        rows.append({
            "Date": pd.to_datetime(r.get("event_date")).strftime("%a %b %-d, %Y") if pd.notna(r.get("event_date")) else "",
            "Time": _fmt_time12(r.get("_start_dt")) + (" – " + _fmt_time12(r.get("_end_dt")) if not pd.isna(r.get("_end_dt")) else ""),
            "Title": r.get("title"),
            "Venue ID": r.get("venue_id"),
            "Fee": format_currency(r.get("fee")),
            "Missing Roles": ", ".join(miss_roles) if miss_roles else "",
            "Sound Covered?": "Yes" if sound_ok else "No",
            "Missing Details": ", ".join(miss_details) if miss_details else "",
            "Gig ID": gig_id,
            "Status": r.get("contract_status"),
        })

st.markdown("---")
st.subheader("Gigs Not Fully Staffed")

if not rows:
    st.success("All filtered gigs are fully staffed. ✅")
else:
    out = pd.DataFrame(rows)
    # Prefer stable ordering
    order = [c for c in ["Date", "Time", "Title", "Venue ID", "Fee", "Missing Roles", "Sound Covered?", "Missing Details", "Status", "Gig ID"] if c in out.columns]
    out = out[order]
    # Show in a grid
    st.dataframe(out, use_container_width=True, hide_index=True)

# Definition box
st.markdown("---")
st.markdown(
    """
**Fully staffed** means:
- All nine musician roles assigned: Male Vocals, Female Vocals, Guitar, Bass, Keyboards, Drums, Saxophone, Trombone, Trumpet.
- Sound is covered: either a sound tech is assigned **or** venue-provided sound contact/phone is recorded.
- Core details are set: a venue is selected and both **start** and **end** times are present.
    """
)

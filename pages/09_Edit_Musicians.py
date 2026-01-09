# pages/09_Edit_Musicians.py

from __future__ import annotations
import streamlit as st
import pandas as pd
from datetime import datetime
from typing import Optional, Dict, Any
import os

from supabase import create_client, Client
from auth_helper import require_admin

# ==========================================
# Supabase Client (canonical PRS pattern)
# ==========================================
def _get_secret(name: str, default=None, required: bool = False) -> Optional[str]:
    if hasattr(st, "secrets") and name in st.secrets:
        val = st.secrets[name]
    else:
        val = os.environ.get(name, default)
    if required and (val is None or str(val).strip() == ""):
        st.error(f"Missing required secret: {name}")
        st.stop()
    return val

SUPABASE_URL = _get_secret("SUPABASE_URL", required=True)
SUPABASE_ANON_KEY = _get_secret("SUPABASE_ANON_KEY", required=True)
sb: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)

# Attach Supabase session for RLS BEFORE any queries
if st.session_state.get("sb_access_token") and st.session_state.get("sb_refresh_token"):
    try:
        sb.auth.set_session(
            access_token=st.session_state["sb_access_token"],
            refresh_token=st.session_state["sb_refresh_token"],
        )
    except Exception as e:
        st.warning(f"Could not attach Supabase session. ({e})")

# ==========================================
# ADMIN GATE (single source of truth)
# ==========================================
user, session, user_id = require_admin()
if not user:
    st.stop()

# ==========================================
# Local utilities (match Schedule View)
# ==========================================
def _select_df(table: str, *, where_eq: dict | None = None, limit: int | None = None):
    q = sb.table(table).select("*")
    if where_eq:
        for col, val in where_eq.items():
            q = q.eq(col, val)
    if limit:
        q = q.limit(limit)
    resp = q.execute()
    return pd.DataFrame(resp.data or [])

@st.cache_data(ttl=300)
def _fetch_instruments() -> list[str]:
    """
    Fetch canonical instrument list from vw_people_dropdown.
    Cached to prevent repeated reads; clear cache if instruments are edited.
    """
    resp = (
        sb.table("vw_people_dropdown")
        .select("role")
        .order("role")
        .execute()
    )
    return [r["role"] for r in (resp.data or [])]


# ==========================================
# Page Header
# ==========================================
from lib.ui_header import render_header
render_header("Edit Musicians")

#from lib.email_utils import _fetch_musicians_map  # optional for future auto-fill

# ==========================================
# Load canonical instrument list
# ==========================================
instruments = _fetch_instruments()

if not instruments:
    instruments = ["— No instruments configured —"]

# ==========================================
# SAVE LOGIC
# ==========================================
def _save_musician(payload: Dict[str, Any], musician_id: Optional[str] = None):
    """Insert or update musician using supabase-py v2, with debug + row-count check."""
    if musician_id:
        resp = sb.table("musicians").update(payload).eq("id", musician_id).execute()
    else:
        resp = sb.table("musicians").insert(payload).execute()

    raw = resp.model_dump()

    # DEBUG: show what Supabase actually returned
    # st.write("DEBUG_SAVE_RESPONSE", raw)

    # Hard error if Supabase reports an error
    if raw.get("error"):
        raise Exception(str(raw["error"]))

    data = raw.get("data") or []

    # If we expected an update but got no data back, treat that as a failure for now
    if musician_id and not data:
        raise Exception(
            f"Supabase update affected 0 rows for id={musician_id}. "
            f"Payload={payload}"
        )

    return data


# ==========================================
# LOAD ALL MUSICIANS
# ==========================================
df = _select_df("musicians")

st.markdown("### Select a Musician to Edit")

musicians_list = (
    df.sort_values("display_name")["display_name"].tolist()
    if not df.empty else []
)

action = st.radio(
    "Action",
    ["Edit Existing", "Add New"],
    horizontal=True,
)

musician_id = None
row = {}

if action == "Edit Existing":
    if not musicians_list:
        st.info("No musicians found.")
        st.stop()

    sel_name = st.selectbox("Choose Musician", musicians_list)
    row = df[df["display_name"] == sel_name].iloc[0].to_dict()
    musician_id = row["id"]

# ==========================================
# FORM
# ==========================================
st.markdown("### Musician Details")
with st.form("musician_form"):
    first = st.text_input("First Name", row.get("first_name", ""))
    middle = st.text_input("Middle Name", row.get("middle_name", ""))
    last = st.text_input("Last Name", row.get("last_name", ""))
    stage = st.text_input("Stage Name", row.get("stage_name", ""))
    current_instrument = row.get("instrument")
    instrument = st.selectbox(
        "Instrument",
        instruments,
        index=instruments.index(current_instrument)
        if current_instrument in instruments
        else 0,
    )
    phone = st.text_input("Phone", row.get("phone", ""))
    email = st.text_input("Email", row.get("email", ""))
    address = st.text_area("Address", row.get("address", ""))
    active = st.checkbox("Active", value=row.get("active", True))

    submitted = st.form_submit_button("Save Musician")

if submitted:
    payload = {
        "first_name": first or None,
        "middle_name": middle or None,
        "last_name": last or None,
        "stage_name": stage or None,
        "instrument": instrument or None,
        "phone": phone or None,
        "email": email or None,
        "address": address or None,
        "active": active,
        "updated_at": datetime.utcnow().isoformat(),
    }

    try:
        _save_musician(payload, musician_id)
        st.success("Musician saved successfully.")
        st.balloons()
    except Exception as e:
        st.error(f"Error saving musician: {e}")

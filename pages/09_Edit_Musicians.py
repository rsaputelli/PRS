# pages/09_Edit_Musicians.py

from __future__ import annotations
import streamlit as st
import pandas as pd
from datetime import datetime
from typing import Optional, Dict, Any
import os

# ==========================================
# Supabase: identical to Edit Gig / Schedule View
# ==========================================
from supabase import create_client, Client

def _get_secret(name: str, required=False) -> Optional[str]:
    """Match the secret-fetch logic used across PRS."""
    val = st.secrets.get(name) or os.environ.get(name)
    if required and not val:
        st.error(f"Missing required secret: {name}")
        st.stop()
    return val

SUPABASE_URL = _get_secret("SUPABASE_URL", required=True)
SUPABASE_ANON_KEY = _get_secret("SUPABASE_ANON_KEY", required=True)
sb: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)

# Attach authenticated session
if (
    "sb_access_token" in st.session_state
    and st.session_state["sb_access_token"]
    and "sb_refresh_token" in st.session_state
    and st.session_state["sb_refresh_token"]
):
    try:
        sb.auth.set_session(
            access_token=st.session_state["sb_access_token"],
            refresh_token=st.session_state["sb_refresh_token"],
        )
    except Exception:
        st.error("Your session has expired. Please log in again.")
        st.stop()

# Auth gate
if "user" not in st.session_state or not st.session_state["user"]:
    st.error("Please sign in from the Login page.")
    st.stop()

USER = st.session_state["user"]

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

# ==========================================
# TEMPORARY ADMIN LIST (for testing only)
# ==========================================
PRS_ADMINS = {
    "ray@lutinemanagement.com",
    "ray.saputelli@lutinemanagement.com",
    "prsbandinfo@gmail.com",
    "rjs2119@gmail.com",
}

# ==========================================
# Admin Gate (only using temporary list)
# ==========================================
def _IS_ADMIN():
    return USER.get("email") in PRS_ADMINS

if not _IS_ADMIN():
    st.error("You do not have permission to edit musician records.")
    st.stop()

# ==========================================
# Page Header
# ==========================================
from lib.ui_header import render_header
render_header("Edit Musicians")

from lib.email_utils import _fetch_musicians_map  # optional for future auto-fill

# ==========================================
# SAVE LOGIC
# ==========================================
def _save_musician(payload: Dict[str, Any], musician_id: Optional[str] = None):
    if musician_id:
        resp = sb.table("musicians").update(payload).eq("id", musician_id).execute()
    else:
        resp = sb.table("musicians").insert(payload).execute()

    if resp.get("error"):
        raise Exception(resp["error"])
    return resp

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
    instrument = st.text_input("Instrument", row.get("instrument", ""))
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

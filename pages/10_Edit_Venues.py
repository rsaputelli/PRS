# pages/10_Edit_Venues.py

from __future__ import annotations
import streamlit as st
import pandas as pd
from datetime import datetime
from typing import Optional, Dict, Any
import os

from auth_helper import require_admin
from supabase import create_client, Client

# ==========================================
# Supabase Client (matches Musicians / Edit Gig)
# ==========================================

def _get_secret(name: str, required=False) -> Optional[str]:
    val = st.secrets.get(name) or os.environ.get(name)
    if required and not val:
        st.error(f"Missing required secret: {name}")
        st.stop()
    return val

# ==========================================
# Supabase Client (MUST be first)
# ==========================================

SUPABASE_URL = _get_secret("SUPABASE_URL", required=True)
SUPABASE_ANON_KEY = _get_secret("SUPABASE_ANON_KEY", required=True)
sb: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)

# Attach authenticated session for RLS
if st.session_state.get("sb_access_token") and st.session_state.get("sb_refresh_token"):
    try:
        sb.auth.set_session(
            access_token=st.session_state["sb_access_token"],
            refresh_token=st.session_state["sb_refresh_token"],
        )
    except Exception:
        st.error("Your session has expired. Please log in again.")
        st.stop()

# ==========================================
# Admin gate (HARD STOP, BEFORE HEADER/UI)
# ==========================================
user, session, user_id = require_admin()
if not user:
    st.stop()

# ==========================================
# Page header (AFTER gate)
# ==========================================
from lib.ui_header import render_header
render_header("Edit Venues")
st.markdown("---")


# ==========================================
# Local df helper
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
# Save venue
# ==========================================
def _save_venue(payload: Dict[str, Any], venue_id: Optional[str] = None):
    """Insert or update a venue."""
    if venue_id:
        resp = sb.table("venues").update(payload).eq("id", venue_id).execute()
    else:
        resp = sb.table("venues").insert(payload).execute()

    raw = resp.model_dump()

    # Fail if supabase reports an error
    if raw.get("error"):
        raise Exception(str(raw["error"]))

    data = raw.get("data") or []

    # If updating and no rows were updated, treat as failure
    if venue_id and not data:
        raise Exception(f"Update failed — no rows affected for id={venue_id}")

    return data

# ==========================================
# Load venues
# ==========================================
df = _select_df("venues")

st.markdown("### Select a Venue to Edit")

venue_list = (
    df.sort_values("name")["name"].tolist()
    if not df.empty else []
)

action = st.radio(
    "Action",
    ["Edit Existing", "Add New"],
    horizontal=True,
)

venue_id = None
row = {}

if action == "Edit Existing":
    if not venue_list:
        st.info("No venues found.")
        st.stop()

    sel_name = st.selectbox("Choose Venue", venue_list)
    row = df[df["name"] == sel_name].iloc[0].to_dict()
    venue_id = row["id"]

# ==========================================
# FORM
# ==========================================
st.markdown("### Venue Details")
with st.form("venue_form"):

    name = st.text_input("Venue Name", row.get("name", ""))

    # Address fields (schema accurate)
    address_line1 = st.text_input("Address Line 1", row.get("address_line1", ""))
    address_line2 = st.text_input("Address Line 2", row.get("address_line2", ""))
    city = st.text_input("City", row.get("city", ""))
    state = st.text_input("State", row.get("state", ""))
    postal_code = st.text_input("Postal Code", row.get("postal_code", ""))
    country = st.text_input("Country", row.get("country", "USA"))

    # Contact fields
    contact_name = st.text_input("Contact Name", row.get("contact_name", ""))
    contact_phone = st.text_input("Contact Phone", row.get("contact_phone", ""))
    contact_email = st.text_input("Contact Email", row.get("contact_email", ""))

    notes = st.text_area("Notes", row.get("notes", ""))

    submitted = st.form_submit_button("Save Venue")
    
if submitted:
    payload = {
        "name": name or None,
        "address_line1": address_line1 or None,
        "address_line2": address_line2 or None,
        "city": city or None,
        "state": state or None,
        "postal_code": postal_code or None,
        "country": country or "USA",
        "contact_name": contact_name or None,
        "contact_phone": contact_phone or None,
        "contact_email": contact_email or None,
        "notes": notes or None,
        "updated_at": datetime.utcnow().isoformat(),
    }

    try:
        _save_venue(payload, venue_id)
        st.success("Venue saved successfully.")
        st.balloons()
    except Exception as e:
        st.error(f"Error saving venue: {e}")


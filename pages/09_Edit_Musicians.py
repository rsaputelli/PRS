# pages/09_Edit_Musicians.py

from __future__ import annotations
import streamlit as st
import pandas as pd
from datetime import datetime
from typing import List, Dict, Optional

# --- FIX: Ensure root directory is in module path ---
import sys, os
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from Master_Gig_App import _select_df, _IS_ADMIN, _get_logged_in_user


# ======================================================
# Page Access
# ======================================================
render_header("Musicians (Admin)")
user = _get_logged_in_user()

if not user:
    st.error("You must be logged in.")
    st.stop()

if not _IS_ADMIN():
    st.error("Admin access required.")
    st.stop()

# ======================================================
# Supabase
# ======================================================
SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_ANON_KEY = st.secrets["SUPABASE_SERVICE_ROLE_KEY"]  # full perms required
sb: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)


# ======================================================
# Helpers
# ======================================================
def _save_musician(payload: Dict[str, Any], musician_id: Optional[str] = None):
    """Insert or update musician."""
    if musician_id:
        resp = sb.table("musicians").update(payload).eq("id", musician_id).execute()
    else:
        resp = sb.table("musicians").insert(payload).execute()
    if resp.get("error"):
        raise Exception(resp["error"])
    return resp


def _delete_musician(mid: str):
    """Soft-delete: just set active = false."""
    resp = sb.table("musicians").update({"active": False}).eq("id", mid).execute()
    if resp.get("error"):
        raise Exception(resp["error"])


# ======================================================
# Load Data
# ======================================================
mus_df = _select_df("musicians", "*")
mus_df["id"] = mus_df["id"].astype(str)

# Display name fallbacks
mus_df["display_fallback"] = mus_df.apply(
    lambda r: r["display_name"]
    or r["stage_name"]
    or f"{r['first_name']} {r['last_name']}".strip()
    or "(no name)",
    axis=1
)

# ======================================================
# Search Bar
# ======================================================
search = st.text_input("Search (name, instrument, email)").strip().lower()

if search:
    mus_df = mus_df[
        mus_df["display_fallback"].str.lower().str.contains(search)
        | mus_df["instrument"].fillna("").str.lower().str.contains(search)
        | mus_df["email"].fillna("").str.lower().str.contains(search)
    ]


# ======================================================
# Add New Musician
# ======================================================
st.markdown("### Add New Musician")

with st.expander("➕ Add Musician"):
    with st.form("add_musician_form"):
        first = st.text_input("First Name")
        middle = st.text_input("Middle Name", "")
        last = st.text_input("Last Name")
        stage = st.text_input("Stage Name")
        instr = st.text_input("Instrument")
        phone = st.text_input("Phone")
        email = st.text_input("Email")
        address = st.text_area("Address")

        submitted = st.form_submit_button("Add Musician")

        if submitted:
            try:
                payload = {
                    "first_name": first or None,
                    "middle_name": middle or None,
                    "last_name": last or None,
                    "stage_name": stage or None,
                    "instrument": instr or None,
                    "phone": phone or None,
                    "email": email or None,
                    "address": address or None,
                    "active": True,
                }
                _save_musician(payload)
                st.success("Musician added.")
                st.experimental_rerun()
            except Exception as e:
                st.error(f"Error: {e}")


# ======================================================
# Current Musicians List
# ======================================================
st.markdown("## All Musicians")

mus_df = mus_df.sort_values("display_fallback")

for _, row in mus_df.iterrows():
    mid = row["id"]
    display = row["display_fallback"]
    inst = row.get("instrument", "")
    email = row.get("email", "")
    phone = row.get("phone", "")
    active = row.get("active", True)

    with st.expander(f"{display}  —  {inst or '(no instrument)'}"):
        st.markdown(f"**Email:** {email or '(none)'}")
        st.markdown(f"**Phone:** {phone or '(none)'}")
        st.markdown(f"**Instrument:** {inst or '(none)'}")
        st.markdown(f"**Stage Name:** {row.get('stage_name') or '(none)'}")
        st.markdown(f"**Active:** {'Yes' if active else 'No'}")

        # -----------------------------
        # Edit Form
        # -----------------------------
        with st.form(f"edit_form_{mid}"):
            first = st.text_input("First Name", row.get("first_name") or "")
            middle = st.text_input("Middle Name", row.get("middle_name") or "")
            last = st.text_input("Last Name", row.get("last_name") or "")
            stage = st.text_input("Stage Name", row.get("stage_name") or "")
            instr = st.text_input("Instrument", row.get("instrument") or "")
            phone2 = st.text_input("Phone", row.get("phone") or "")
            email2 = st.text_input("Email", row.get("email") or "")
            address2 = st.text_area("Address", row.get("address") or "")
            active2 = st.checkbox("Active", value=active)

            save = st.form_submit_button("Save Changes")

            if save:
                try:
                    payload = {
                        "first_name": first or None,
                        "middle_name": middle or None,
                        "last_name": last or None,
                        "stage_name": stage or None,
                        "instrument": instr or None,
                        "phone": phone2 or None,
                        "email": email2 or None,
                        "address": address2 or None,
                        "active": active2,
                    }
                    _save_musician(payload, musician_id=mid)
                    st.success("Updated successfully.")
                    st.experimental_rerun()
                except Exception as e:
                    st.error(f"Error: {e}")

        # -----------------------------
        # Soft Delete (Inactivate)
        # -----------------------------
        if active:
            if st.button("Deactivate Musician", key=f"del_{mid}"):
                try:
                    _delete_musician(mid)
                    st.success("Musician deactivated.")
                    st.experimental_rerun()
                except Exception as e:
                    st.error(f"Error: {e}")

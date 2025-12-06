# lib/auth.py

import streamlit as st

def is_logged_in() -> bool:
    """True if user is authenticated via Supabase."""
    return "user" in st.session_state and bool(st.session_state["user"])

def current_user():
    """Returns the Supabase user dict or None."""
    return st.session_state.get("user")

def IS_ADMIN() -> bool:
    """
    Unified PRS admin gate.
    All admin-only pages should rely on this.
    """
    user = current_user()
    if not user:
        return False

    email = (user.get("email") or "").lower().strip()
    admin_list = st.secrets.get("PRS_ADMINS", [])

    # Ensure secrets are normalized to lowercase
    admin_list = [a.lower().strip() for a in admin_list]

    return email in admin_list

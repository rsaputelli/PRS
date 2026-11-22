# -----auth_helper.py

import streamlit as st
from supabase import create_client, Client
from supabase_auth.errors import AuthApiError

# --- Supabase client configuration (the anon key only) ---
SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_ANON_KEY = st.secrets["SUPABASE_ANON_KEY"]


def _new_client() -> Client:
    """Return a fresh client so auth state is never shared between Streamlit users."""
    return create_client(SUPABASE_URL, SUPABASE_ANON_KEY)


def _clear_auth_state() -> None:
    for key in ("supabase_user", "user_id", "sb_access_token", "sb_refresh_token"):
        st.session_state.pop(key, None)


def attach_session(client: Client):
    """Attach this Streamlit session's Supabase tokens and persist any rotated pair."""
    if st.session_state.get("force_logged_out"):
        _clear_auth_state()
        return None

    access = st.session_state.get("sb_access_token")
    refresh = st.session_state.get("sb_refresh_token")
    if not access or not refresh:
        return None

    try:
        response = client.auth.set_session(access, refresh)
        session = getattr(response, "session", None) or client.auth.get_session()
    except AuthApiError:
        # Refresh tokens can only be exchanged once. A failed refresh requires a new login.
        _clear_auth_state()
        return None

    if session:
        # Supabase rotates refresh tokens. Save the current pair immediately so another
        # page/client in this Streamlit session never replays the previous refresh token.
        st.session_state["sb_access_token"] = session.access_token
        st.session_state["sb_refresh_token"] = session.refresh_token
    return session


def get_authenticated_client() -> Client:
    """Create a client bound only to the current Streamlit user's saved session."""
    client = _new_client()
    attach_session(client)
    return client


def restore_session():
    # HARD LOGOUT SENTINEL
    if st.session_state.get("force_logged_out"):
        _clear_auth_state()
        return None, None

    if not st.session_state.get("sb_access_token") or not st.session_state.get("sb_refresh_token"):
        _clear_auth_state()
        return None, None

    client = _new_client()
    session = attach_session(client)
    user = session.user if session else None

    # Hydrate app-level identity from the authenticated Supabase session.
    if user:
        st.session_state["supabase_user"] = user
        st.session_state["user_id"] = user.id
    else:
        st.session_state.pop("supabase_user", None)
        st.session_state.pop("user_id", None)

    return user, session


def require_login():
    user, session = restore_session()
    user_id = st.session_state.get("user_id")

    if not user_id:
        st.error("Please sign in.")
        st.stop()

    return user, session, user_id


def require_admin():
    user, session, user_id = require_login()

    # Use a request-scoped client; never retain a mutable authenticated client globally.
    res = (
        get_authenticated_client()
        .table("profiles")
        .select("role")
        .eq("id", user_id)
        .execute()
    )

    role = res.data[0]["role"] if res.data else None
    if role != "admin":
        st.error("Admins only.")
        st.stop()

    return user, session, user_id

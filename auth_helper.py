# auth_helper.py
import streamlit as st
from supabase import create_client, Client


def get_client() -> Client:
    return create_client(
        st.secrets["SUPABASE_URL"],
        st.secrets["SUPABASE_ANON_KEY"],
    )


def restore_session():
    """
    Restores a Supabase session from st.session_state
    (if one exists), and returns (user, session).
    """

    sb = get_client()

    access = st.session_state.get("sb_access_token")
    refresh = st.session_state.get("sb_refresh_token")

    if access and refresh:
        try:
            sb.auth.set_session(access, refresh)
        except Exception as e:
            st.warning(f"Auth restore failed: {e}")

    session = sb.auth.get_session()
    user = session.user if session else None

    # keep values in state for visibility / debugging
    if session:
        st.session_state["sb_access_token"] = session.access_token
        st.session_state["sb_refresh_token"] = session.refresh_token

    return user, session

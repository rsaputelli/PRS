import streamlit as st
from supabase import create_client, Client


# Create a cached client so every page shares it
@st.cache_resource
def get_supabase() -> Client:
    return create_client(
        st.secrets["SUPABASE_URL"],
        st.secrets["SUPABASE_ANON_KEY"],
    )


def restore_session():
    """
    Returns (user, session)

    - Reads stored access/refresh tokens
    - Restores Supabase session if possible
    - Ensures pages can consistently see who is logged in
    """

    sb = get_supabase()

    access = st.session_state.get("sb_access_token")
    refresh = st.session_state.get("sb_refresh_token")

    # Try to restore session from stored tokens
    if access:
        try:
            sb.auth.set_session(access, refresh or "")
        except Exception:
            pass

    session = sb.auth.get_session()
    user = getattr(session, "user", None) if session else None

    return user, session

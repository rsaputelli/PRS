import streamlit as st
from supabase import create_client, Client

sb: Client = create_client(
    st.secrets["SUPABASE_URL"],
    st.secrets["SUPABASE_ANON_KEY"]
)

def restore_session():
    access = st.session_state.get("sb_access_token")
    refresh = st.session_state.get("sb_refresh_token")

    if access and refresh:
        # 🔹 Re-attach session for this request/page
        sb.auth.set_session(access, refresh)

    session = sb.auth.get_session()
    user = session.user if session else None
    return user, session



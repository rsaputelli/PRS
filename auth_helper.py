import streamlit as st
from supabase import create_client, Client

sb: Client = create_client(
    st.secrets["SUPABASE_URL"],
    st.secrets["SUPABASE_ANON_KEY"]
)

def restore_session():
    # Already restored?
    if st.session_state.get("sb_access_token") and st.session_state.get("sb_refresh_token"):
        return sb.auth.get_user(), sb.auth.get_session()

    # Try restoring from Supabase cookie
    session = sb.auth.get_session()
    if session and session.access_token:
        st.session_state["sb_access_token"]  = session.access_token
        st.session_state["sb_refresh_token"] = session.refresh_token
        sb.auth.set_session(session.access_token, session.refresh_token)
        return sb.auth.get_user(), session

    # Nothing yet — treat as anonymous
    return None, None


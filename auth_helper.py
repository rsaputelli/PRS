# -----auth_helper.py

import streamlit as st
from supabase import create_client, Client

# --- Supabase client (ANON ONLY: session/user hydration) ---
SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_ANON_KEY = st.secrets["SUPABASE_ANON_KEY"]

sb: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)


def restore_session():
    access = st.session_state.get("sb_access_token")
    refresh = st.session_state.get("sb_refresh_token")

    if access and refresh:
        # Re-attach session for this request/page
        sb.auth.set_session(access, refresh)

    session = sb.auth.get_session()
    user = session.user if session else None

    # 🔹 Hydrate app-level identity (what your pages actually check)
    if user:
        st.session_state["supabase_user"] = user
        st.session_state["user_id"] = user.id
    else:
        st.session_state.pop("supabase_user", None)
        st.session_state.pop("user_id", None)

    return user, session

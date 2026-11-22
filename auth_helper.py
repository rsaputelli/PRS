# -----auth_helper.py

import streamlit as st
from supabase import create_client, Client
from supabase_auth.errors import AuthApiError

# --- Supabase client (ANON ONLY: session/user hydration) ---
SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_ANON_KEY = st.secrets["SUPABASE_ANON_KEY"]

sb: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)

def restore_session():
    # 🚫 HARD LOGOUT SENTINEL
    if st.session_state.get("force_logged_out"):
        st.session_state.pop("supabase_user", None)
        st.session_state.pop("user_id", None)
        st.session_state.pop("sb_access_token", None)
        st.session_state.pop("sb_refresh_token", None)
        return None, None

    access = st.session_state.get("sb_access_token")
    refresh = st.session_state.get("sb_refresh_token")

    if access and refresh:
        try:
            sb.auth.set_session(access, refresh)
            session = sb.auth.get_session()
            user = session.user if session else None

        except AuthApiError:
            # 🔥 Refresh token invalid / already used
            st.session_state.pop("supabase_user", None)
            st.session_state.pop("user_id", None)
            st.session_state.pop("sb_access_token", None)
            st.session_state.pop("sb_refresh_token", None)
            return None, None
    else:
        session = None
        user = None

    # 🔹 Hydrate app-level identity (authoritative)
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

    res = (
        sb.table("profiles")
        .select("role")
        .eq("id", user_id)
        .execute()
    )

    role = res.data[0]["role"] if res.data else None
    if role != "admin":
        st.error("Admins only.")
        st.stop()

    return user, session, user_id

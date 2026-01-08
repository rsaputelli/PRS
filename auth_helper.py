##---auth_helper.py

def restore_session():
    access = st.session_state.get("sb_access_token")
    refresh = st.session_state.get("sb_refresh_token")

    if access and refresh:
        # Re-attach session for this request/page
        sb.auth.set_session(access, refresh)

    session = sb.auth.get_session()
    user = session.user if session else None

    # 🔹 Ensure app-level identity is populated
    if user:
        st.session_state["supabase_user"] = user
        st.session_state["user_id"] = user.id
    else:
        # Clear stale state
        st.session_state.pop("supabase_user", None)
        st.session_state.pop("user_id", None)

    return user, session

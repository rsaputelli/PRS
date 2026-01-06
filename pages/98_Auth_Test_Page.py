import streamlit as st
from auth_helper import restore_session

st.title("🔍 Auth Test Page")

user, session = restore_session()

st.write("### Session Diagnostic")
st.json({
    "user_id": getattr(user, "id", None),
    "email": getattr(user, "email", None),
    "has_access_token": bool(st.session_state.get("sb_access_token")),
    "has_refresh_token": bool(st.session_state.get("sb_refresh_token")),
})

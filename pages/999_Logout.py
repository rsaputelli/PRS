##-----  pages/999_Logout.py

import streamlit as st
from supabase import create_client

st.set_page_config(page_title="Logout", page_icon="🚪")

# --- HARD LOGOUT ---
try:
    sb = create_client(
        st.secrets["SUPABASE_URL"],
        st.secrets["SUPABASE_ANON_KEY"],
    )
    sb.auth.sign_out()
except Exception:
    pass  # logout must never block

# 🔒 Set sentinel FIRST
st.session_state["force_logged_out"] = True

# 🧹 Clear everything else safely
for k in list(st.session_state.keys()):
    if k != "force_logged_out":
        st.session_state.pop(k, None)


st.info("You have been logged out.")

st.markdown(
    '<meta http-equiv="refresh" content="0; url=/Login">',
    unsafe_allow_html=True,
)

st.stop()

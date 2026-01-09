##-----  pages/999_Logout.py

import streamlit as st
from supabase import create_client

st.set_page_config(page_title="Logout", page_icon="🚪")

try:
    sb = create_client(
        st.secrets["SUPABASE_URL"],
        st.secrets["SUPABASE_ANON_KEY"],
    )
    sb.auth.sign_out()
except Exception:
    pass  # logout must never block

st.session_state.clear()

st.info("Signing you out…")

st.markdown(
    '<meta http-equiv="refresh" content="0; url=/Login">',
    unsafe_allow_html=True,
)

st.stop()

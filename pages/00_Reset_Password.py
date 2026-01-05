# --------------------------------------------------
# 00_Reset_Password.py  — resilient recovery handler
# --------------------------------------------------

import streamlit as st
from supabase import create_client, Client

st.set_page_config(page_title="Reset Password", layout="centered")

sb: Client = create_client(
    st.secrets["SUPABASE_URL"],
    st.secrets["SUPABASE_ANON_KEY"]
)

raw_params = st.query_params


# --------------------------------------------------
# Normalize Streamlit query params (lists → strings)
# --------------------------------------------------
def qp(key):
    v = raw_params.get(key)
    if isinstance(v, list):
        return v[0]
    return v

params = {k: qp(k) for k in raw_params}

st.subheader("DEBUG — do not worry, this is temporary")

st.write("🔹 RAW PARAMS:", raw_params)
st.write("🔹 NORMALIZED PARAMS:", params)

from datetime import datetime
st.write("🔹 Timestamp:", datetime.utcnow().isoformat())

# Try to show Supabase session state (if any)
try:
    current = sb.auth.get_session()
    st.write("🔹 sb.auth.get_session():", current)
except Exception as e:
    st.write("🔸 get_session() error:", e)

st.write("🔹 reset_session_ready:", st.session_state.get("reset_session_ready"))

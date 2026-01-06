import streamlit as st
from supabase import create_client

st.set_page_config(page_title="Reset Password — DEBUG")

sb = create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_ANON_KEY"])

st.subheader("🔍 RESET DEBUG — LIVE URL INSPECTION")

# --- Dump the browser URL + hash BEFORE anything else ---
st.components.v1.html("""
<div id="dbg" style="padding:10px;border:1px solid #ddd;"></div>
<script>
  const out = document.getElementById("dbg");
  out.innerText =
    "href:  " + window.location.href + "\\n" +
    "path:  " + window.location.pathname + "\\n" +
    "search:" + window.location.search + "\\n" +
    "hash:  " + window.location.hash;
</script>
""", height=120)

st.write("🔹 query_params:", st.query_params)

try:
    st.write("🔹 get_session():", sb.auth.get_session())
except Exception as e:
    st.write("🔸 get_session() error:", e)

# 00_Reset_Password.py
import streamlit as st
from supabase import create_client

st.set_page_config(page_title="Reset Password", layout="centered")

sb = create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_ANON_KEY"])
params = st.query_params

# 🔹 Handle BOTH hash tokens and code-exchange
st.components.v1.html("""
<script>
const h = window.location.hash;
if (h && h.includes("access_token")) {
  const q = new URLSearchParams(h.substring(1));
  const url =
    window.location.pathname +
    "?type=recovery" +
    "&access_token=" + encodeURIComponent(q.get("access_token")) +
    "&refresh_token=" + encodeURIComponent(q.get("refresh_token") || "");
  window.location.replace(url);
}
</script>
""", height=0)

is_recovery = params.get("type") == "recovery"

# 🔹 Establish session if tokens are present
if is_recovery and params.get("access_token"):
    try:
        sb.auth.set_session(
            params["access_token"],
            params.get("refresh_token") or ""
        )
        st.session_state["has_reset_session"] = True
    except Exception as e:
        st.error(f"Could not restore session: {e}")

# 🔹 If no session, tell user to reopen link
if not st.session_state.get("has_reset_session"):
    st.warning("This reset link is not active. Please reopen the link from your email.")
    st.stop()

# 🔹 Password form
st.subheader("Reset Your Password")
pw1 = st.text_input("New Password", type="password")
pw2 = st.text_input("Confirm Password", type="password")

if st.button("Update Password"):
    if pw1 != pw2:
        st.error("Passwords do not match.")
    elif len(pw1) < 6:
        st.error("Password must be at least 6 characters.")
    else:
        try:
            sb.auth.update_user({"password": pw1})
            st.success("Password updated! You may now sign in.")
        except Exception as e:
            st.error(f"Password reset failed: {e}")

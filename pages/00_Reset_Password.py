#---00_Reset_Password.py

import streamlit as st
from supabase import create_client, Client

st.set_page_config(page_title="Reset Password", layout="centered")

sb: Client = create_client(
    st.secrets["SUPABASE_URL"],
    st.secrets["SUPABASE_ANON_KEY"]
)

# --- EARLY: Convert hash → query params BEFORE Streamlit loads UI ---
st.components.v1.html("""
<script>
(function () {
  const h = window.location.hash;
  if (!h || !h.includes("access_token")) return;

  const q = new URLSearchParams(h.substring(1));

  const url =
    window.location.pathname
    + "?type=recovery"
    + "&access_token=" + encodeURIComponent(q.get("access_token"))
    + "&refresh_token=" + encodeURIComponent(q.get("refresh_token") || "");

  // Replace URL WITHOUT reloading the hash again
  window.location.replace(url);
})();
</script>
""", height=0)

params = st.query_params
is_recovery = params.get("type") == "recovery"

# Track session status
st.session_state.setdefault("reset_ready", False)

# --- Token session flow ---
if is_recovery and params.get("access_token"):
    try:
        sb.auth.set_session(
            params["access_token"],
            params.get("refresh_token") or ""
        )
        st.session_state["reset_ready"] = True
    except Exception as e:
        st.error(f"Session attach failed: {e}")

# --- Require active recovery session ---
if not st.session_state["reset_ready"]:
    st.warning("This reset link is not active yet — please reopen the link from your email.")
    st.stop()

# --- Reset Form ---
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
            st.success("Password updated — you may now sign in.")
        except Exception as e:
            st.error(f"Password reset failed: {e}")
